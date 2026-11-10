"""Profile-local start-page data and a maintained, script-free HTML scheme."""

import base64
import re
from copy import deepcopy
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import urlencode
from uuid import uuid4

from PyQt6.QtCore import QBuffer, QIODevice, QObject, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import (
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)

from bookmark_format import (
    MAX_BOOKMARKS,
    bookmark_name,
    export_bookmarks,
    parse_bookmarks,
)
from services.command_broker import require_owner_thread
from services.page_api import PageApi, api_endpoint
from services.page_style import ROOT_CSS
from utils import load_json, save_json

NEW_TAB = "steed:newtab"
WELCOME = "steed:welcome"
MODULES = ("search", "image", "bookmarks", "recent", "history", "news")
DEFAULT_SETTINGS = {
    "modules": ["search", "bookmarks", "recent"],
    "image": "",
    "feed": "",
    "minimal": False,
    # "simple": the name and a search box over a background; "classic": the
    # sections above.
    "layout": "simple",
    # "photo" (changes now and then), "color" or "image" (your own).
    "background": "photo",
    "color": "",
    "photo_every": "6h",
}
LAYOUTS = ("simple", "classic")
BACKGROUNDS = ("photo", "color", "image")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_HTML_BYTES = 256 * 1024
WORKSPACES = (
    "settings",
    "agents",
    "style",
    "memory",
    "features",
    "userscripts",
    "access",
    "apps",
    "cookies",
    "downloads",
    "bookmarks",
    "history",
    "library",
    "connections",
    "tasks",
    "panels",
    "passwords",
    "layout",
    "newtab-settings",
    "tab-groups",
    "permissions",
    "agent-setup",
    "adblock",
    "services",
)


def workspace_name(text):
    """Resolve maintained workspaces separately from authored start pages."""
    if not isinstance(text, str):
        return None
    for name in WORKSPACES:
        if text in ("steed:" + name, "steed://" + name, "steed://" + name + "/"):
            return name
    return None


CUSTOM_EXAMPLE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>My New Tab</title>
<style>body { font: 18px system-ui; max-width: 680px; margin: 15vh auto;
padding: 24px; background: #f5f4f0; color: #24332f; }
input, button { font: inherit; padding: 12px; border-radius: 8px; }
input { width: 65%; } </style></head><body><h1>My space.</h1>
<form action="https://duckduckgo.com/" method="get">
<label for="q">Search</label><p><input id="q" name="q" autofocus>
<button>Go</button></p></form></body></html>"""


def register_scheme():
    """Register before QApplication; host syntax keeps distinct page origins."""
    if QWebEngineUrlScheme.schemeByName(b"steed").name():
        return
    scheme = QWebEngineUrlScheme(b"steed")
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalScheme
        # Custom start pages call their own steed://newtab/api/… with fetch().
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


register_scheme()


def internal_url(text):
    aliases = {
        NEW_TAB: "steed://newtab/",
        WELCOME: "steed://welcome/",
        "steed::newtab": "steed://newtab/",
        "steed::welcome": "steed://welcome/",
    }
    aliases.update({"steed:" + name: "steed://" + name + "/" for name in WORKSPACES})
    if text in aliases:
        return QUrl(aliases[text])
    url = QUrl(text)
    return url if page_name(url) is not None or workspace_name(url.toString()) else None


MAX_APPS = 12
APP_HOST = re.compile(r"app-([a-f0-9]{12})")


def app_id(url):
    """The panel app id for steed://app-<id>/, or None."""
    match = APP_HOST.fullmatch(url.host()) if url.scheme() == "steed" else None
    if (
        match
        and url.path() in ("", "/")
        and not url.userInfo()
        and url.port() == -1
        and not url.hasQuery()
        and not url.hasFragment()
    ):
        return match.group(1)
    return None


def clean_app(item):
    """A stored panel app, or ValueError."""
    if not isinstance(item, dict):
        raise ValueError("Invalid app")
    identity, name = item.get("id"), " ".join(str(item.get("name", "")).split())
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{12}", identity):
        raise ValueError("Invalid app")
    if not 1 <= len(name) <= 60 or not name.isprintable():
        raise ValueError("Give the app a name of 1 to 60 characters.")
    return {"id": identity, "name": name, "html": clean_html(item.get("html", ""))}


def page_name(url):
    if (
        url.scheme() == "steed"
        and url.host() in ("newtab", "welcome")
        and url.path() in ("", "/")
        and not url.userInfo()
        and url.port() == -1
        and not url.hasQuery()
        and not url.hasFragment()
    ):
        return url.host()
    return None


def display_url(url):
    name = page_name(url)
    return "steed:" + name if name is not None else url.toString()


def web_url(text):
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError("Use a web address of at most 4096 characters")
    url = QUrl(text.strip())
    if (
        not url.isValid()
        or url.scheme() not in ("https", "http")
        or not url.host()
        or url.userInfo()
    ):
        raise ValueError("Use an HTTP or HTTPS address without embedded credentials")
    return url.toString()


def clean_settings(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid new-tab settings")
    modules = value.get("modules", DEFAULT_SETTINGS["modules"])
    if (
        not isinstance(modules, list)
        or len(modules) > len(MODULES)
        or any(m not in MODULES for m in modules)
        or len(set(modules)) != len(modules)
    ):
        raise ValueError("Choose each supported section at most once")
    image = value.get("image", "")
    if not isinstance(image, str) or len(image) > (MAX_IMAGE_BYTES * 4 // 3 + 100):
        raise ValueError("Choose an image of at most 2 MB after resizing")
    if image and not re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/]+=*", image):
        raise ValueError("Choose a local image using the image picker")
    feed = value.get("feed", "")
    feed = web_url(feed) if feed else ""
    minimal = value.get("minimal", False)
    if type(minimal) is not bool:
        raise ValueError("Choose an explicit minimal-page preference")
    from services.newtab_photo import PERIODS

    layout = value.get("layout", DEFAULT_SETTINGS["layout"])
    background = value.get("background", DEFAULT_SETTINGS["background"])
    color = value.get("color", "")
    every = value.get("photo_every", DEFAULT_SETTINGS["photo_every"])
    if layout not in LAYOUTS or background not in BACKGROUNDS or every not in PERIODS:
        raise ValueError("Choose a layout, background and photo schedule Steed offers")
    if not isinstance(color, str) or (
        color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
    ):
        raise ValueError("Choose a colour like #336699")
    return {
        "modules": list(modules),
        "image": image,
        "feed": feed,
        "minimal": minimal,
        "layout": layout,
        "background": background,
        "color": color.lower(),
        "photo_every": every,
    }


def image_data(path):
    """Decode a bounded local image and copy a resized PNG, never a file URL."""
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QImageReader

    path = Path(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Choose an image file of at most 8 MB")
    reader = QImageReader(str(path))
    size = reader.size()
    if size.isEmpty() or size.width() * size.height() > 32_000_000:
        raise ValueError("Choose an image of at most 32 megapixels")
    reader.setAutoTransform(True)
    if size.width() > 1600 or size.height() > 900:
        reader.setScaledSize(
            size.scaled(QSize(1600, 900), Qt.AspectRatioMode.KeepAspectRatio)
        )
    image = reader.read()
    if image.isNull():
        raise ValueError("This image could not be opened")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG") or buffer.size() > MAX_IMAGE_BYTES:
        raise ValueError("Choose a smaller image")
    return "data:image/png;base64," + base64.b64encode(bytes(buffer.data())).decode(
        "ascii"
    )


class StartPages(QObject):
    changed = pyqtSignal(str)
    news_changed = pyqtSignal(str)

    def __init__(self, registry):
        super().__init__(registry)
        self.registry = registry
        self._states = {}
        from services.news_feed import NewsFeeds

        self.news = NewsFeeds(self)
        self.news.changed.connect(self.news_changed.emit)
        from services.newtab_photo import NewTabPhotos

        self.photos = NewTabPhotos(registry.config.config_dir / "newtab", self)

    def path(self, profile):
        if profile == "search":
            return None
        directory = (
            self.registry.config.config_dir / "global_profile"
            if profile == "global"
            else self.registry.config.profiles_dir / profile
        )
        return directory / "start_page.json"

    def profile_key(self, profile, *, create=False):
        if profile == "global":
            return self.registry._permission_identity(
                self.registry.config.config_dir / "global_profile", create=create
            )
        if profile == "search":
            raise ValueError("Choose a normal display profile")
        return self.registry.approval_profile_key("existing:" + profile, create=create)

    def state(self, profile):
        require_owner_thread(self)
        if profile not in self._states:
            path = self.path(profile)
            raw = load_json(path, {}) if path is not None else {}
            raw = raw if isinstance(raw, dict) else {}
            try:
                settings = clean_settings(raw.get("settings", DEFAULT_SETTINGS))
            except ValueError:
                settings = deepcopy(DEFAULT_SETTINGS)
            bookmarks, seen = [], set()
            items = raw.get("bookmarks", [])
            for item in items[:MAX_BOOKMARKS] if isinstance(items, list) else []:
                try:
                    url = web_url(item["url"])
                    title = bookmark_name(" ".join(item["title"].split())[:200])
                    identity = item["id"]
                    if (
                        not title
                        or not isinstance(identity, str)
                        or not re.fullmatch(r"[a-f0-9]{32}", identity)
                        or identity in seen
                    ):
                        continue
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                try:
                    folder = bookmark_name(item.get("folder", ""), optional=True)
                except ValueError:
                    folder = ""
                bookmarks.append(
                    {"id": identity, "url": url, "title": title, "folder": folder}
                )
                seen.add(identity)
            html = raw.get("html", "")
            try:
                html = clean_html(html)
            except ValueError:
                html = ""
            welcome_html = raw.get("welcome_html", "")
            try:
                welcome_html = clean_html(welcome_html)
            except ValueError:
                welcome_html = ""
            apps = []
            for item in (
                raw.get("apps", [])[:MAX_APPS]
                if isinstance(raw.get("apps"), list)
                else []
            ):
                try:
                    app = clean_app(item)
                except ValueError:
                    continue
                if all(other["id"] != app["id"] for other in apps):
                    apps.append(app)
            self._states[profile] = {
                "version": 1,
                "settings": settings,
                "bookmarks": bookmarks,
                "html": html,
                "welcome_html": welcome_html,
                "apps": apps,
            }
        return deepcopy(self._states[profile])

    def _commit(self, profile, state):
        path = self.path(profile)
        if path is not None:
            save_json(path, state)
        self._states[profile] = deepcopy(state)
        self.changed.emit(profile)

    def configure(
        self, profile, settings, html=None, *, page="newtab", welcome_html=None
    ):
        state = self.state(profile)
        state["settings"] = clean_settings(settings)
        if html is not None:
            if page not in ("newtab", "welcome"):
                raise ValueError("Choose New Tab or Welcome")
            state["html" if page == "newtab" else "welcome_html"] = clean_html(html)
        if welcome_html is not None:
            state["welcome_html"] = clean_html(welcome_html)
        self.news.forget(profile)
        self._commit(profile, state)

    def save_app(self, profile, name, html, identity=None):
        """Add a panel app, or replace one by id; returns its id."""
        if profile == "search":
            raise ValueError("Incognito windows have no panel apps.")
        state = self.state(profile)
        apps = state["apps"]
        if identity is None:
            if len(apps) >= MAX_APPS:
                raise ValueError(f"Keep at most {MAX_APPS} panel apps.")
            identity = uuid4().hex[:12]
            apps.append(clean_app({"id": identity, "name": name, "html": html}))
        else:
            index = next((i for i, a in enumerate(apps) if a["id"] == identity), None)
            if index is None:
                raise ValueError("That app no longer exists.")
            apps[index] = clean_app({"id": identity, "name": name, "html": html})
        self._commit(profile, state)
        return identity

    def remove_app(self, profile, identity):
        state = self.state(profile)
        state["apps"] = [a for a in state["apps"] if a["id"] != identity]
        self._commit(profile, state)

    def app(self, profile, identity):
        return next(
            (a for a in self.state(profile).get("apps", []) if a["id"] == identity),
            None,
        )

    def render_app(self, profile, identity):
        app = self.app(profile, identity)
        if app is None:
            raise ValueError("Unknown app")
        styles = self.registry.page_style
        root_css = ROOT_CSS.read_text(encoding="utf-8") + styles.css(profile)
        return (
            '<!doctype html><html data-theme="'
            + ("dark" if styles.dark(profile) else "light")
            + '"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; style-src &apos;unsafe-inline&apos;; script-src &apos;unsafe-inline&apos;; connect-src &apos;self&apos;; img-src data: blob:; form-action http: https:; base-uri &apos;none&apos;; object-src &apos;none&apos;">'
            + "<style>"
            + root_css
            + "</style></head>"
            + app["html"]
        ).encode()

    def add_bookmark(self, profile, url, title, *, folder=None):
        state = self.state(profile)
        url, title = web_url(url), bookmark_name(title)
        folder = bookmark_name(folder, optional=True) if folder is not None else None
        existing = next((b for b in state["bookmarks"] if b["url"] == url), None)
        if existing:
            existing["title"] = title
            if folder is not None:
                existing["folder"] = folder
        else:
            if len(state["bookmarks"]) >= MAX_BOOKMARKS:
                raise ValueError("Remove a bookmark before adding more (limit 2,000)")
            state["bookmarks"].append(
                {"id": uuid4().hex, "url": url, "title": title, "folder": folder or ""}
            )
        self._commit(profile, state)

    def update_bookmark(self, profile, identity, url, title, folder):
        state = self.state(profile)
        url, title = web_url(url), bookmark_name(title)
        folder = bookmark_name(folder, optional=True)
        existing = next((b for b in state["bookmarks"] if b["id"] == identity), None)
        if existing is None:
            raise ValueError("This bookmark no longer exists")
        if any(b["url"] == url and b["id"] != identity for b in state["bookmarks"]):
            raise ValueError(
                "That address is already bookmarked; edit its existing entry"
            )
        existing.update(url=url, title=title, folder=folder)
        self._commit(profile, state)

    def replace_bookmarks(self, profile, bookmarks):
        """Used by sync: the whole list, keeping ids and order by address."""
        state = self.state(profile)
        wanted = {}
        for item in bookmarks:
            try:
                url = web_url(item["url"])
                title = bookmark_name(" ".join(str(item["title"]).split())[:200])
                folder = bookmark_name(item.get("folder", ""), optional=True)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if title:
                wanted.setdefault(url, {"url": url, "title": title, "folder": folder})
        kept = [
            {**bookmark, **wanted.pop(bookmark["url"])}
            for bookmark in state["bookmarks"]
            if bookmark["url"] in wanted
        ]
        kept += [{"id": uuid4().hex, **item} for item in wanted.values()]
        state["bookmarks"] = kept[:MAX_BOOKMARKS]
        self._commit(profile, state)

    def import_bookmarks(self, profile, content):
        entries, skipped = parse_bookmarks(content, web_url)
        result = self.import_entries(profile, entries)
        return {**result, "skipped": result["skipped"] + skipped}

    def import_entries(self, profile, entries):
        """Add {url, title, folder} bookmarks; existing addresses are skipped."""
        skipped = 0
        valid = []
        for entry in entries:
            try:
                url = web_url(entry["url"])
                title = bookmark_name(
                    " ".join(str(entry.get("title") or url).split())[:200]
                )
                folder = bookmark_name(
                    " ".join(str(entry.get("folder") or "").split())[:200],
                    optional=True,
                )
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue
            valid.append({"url": url, "title": title, "folder": folder or ""})
        entries = valid
        state = self.state(profile)
        seen = {b["url"] for b in state["bookmarks"]}
        added, duplicates = 0, 0
        for entry in entries:
            if entry["url"] in seen:
                duplicates += 1
                continue
            if len(state["bookmarks"]) >= MAX_BOOKMARKS:
                raise ValueError("Import would exceed the 2,000-bookmark profile limit")
            state["bookmarks"].append(dict(entry, id=uuid4().hex))
            seen.add(entry["url"])
            added += 1
        if added:
            self._commit(profile, state)
        return {"added": added, "duplicates": duplicates, "skipped": skipped}

    def export_bookmarks(self, profile):
        return export_bookmarks(self.state(profile)["bookmarks"])

    def remove_bookmark(self, profile, identity):
        state = self.state(profile)
        state["bookmarks"] = [b for b in state["bookmarks"] if b["id"] != identity]
        self._commit(profile, state)

    def forget(self, profile):
        self._states.pop(profile, None)
        self.news.forget(profile)

    def render(self, profile, name):
        state = self.state(profile)
        styles = self.registry.page_style
        root_css = ROOT_CSS.read_text(encoding="utf-8") + styles.css(profile)
        custom_html = state["html" if name == "newtab" else "welcome_html"]
        if custom_html:
            # A first policy cannot be loosened by policies in user-authored HTML.
            return (
                '<!doctype html><html data-theme="'
                + ("dark" if styles.dark(profile) else "light")
                + '"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; style-src &apos;unsafe-inline&apos;; script-src &apos;unsafe-inline&apos;; connect-src &apos;self&apos;; img-src data: blob:; form-action http: https:; base-uri &apos;none&apos;; object-src &apos;none&apos;">'
                + "<style>"
                + root_css
                + "</style></head>"
                + custom_html
            ).encode()
        private = profile == "search"
        profile_label = "Incognito" if private else profile.title()
        if name == "newtab" and state["settings"]["layout"] == "simple":
            return self.render_simple(profile, state["settings"], root_css)
        if name == "welcome":
            body = '<section class="hero"><span class="eyebrow">YOUR BROWSER, YOUR WORKSPACE</span><h1>Welcome to Steed.</h1><p>A quiet place to browse, organize your work, and collaborate with agents.</p></section><div class="cards">'
            for heading, text, action, label in (
                (
                    "Make it yours",
                    "Start with search, or add bookmarks, an image and a news feed. Each profile keeps its own space.",
                    "customize",
                    "Customize New Tab",
                ),
                (
                    "Bring your agent",
                    "Chat with an external agent in the sidebar or a tab. Connect when you are ready; browsing works on its own.",
                    "agents",
                    "Open agent setup",
                ),
                (
                    "Keep files close",
                    "Enable folders once so agents can work with your files without uploading them again. You choose which folders to share.",
                    "library",
                    "Choose folders",
                ),
            ):
                body += f'<section class="card"><h2>{heading}</h2><p>{text}</p><a class="button secondary" href="steed://action/{action}">{label}</a></section>'
            body += '</div><section class="finish"><a class="button" href="steed://action/start">Start browsing</a><p>Ctrl+T · New tab &nbsp; Ctrl+F · Find &nbsp; Ctrl+D · Bookmark</p></section>'
        else:
            settings = state["settings"]
            body = '<section class="hero"><h1>Your space.</h1><p>Pick up where you left off, or start somewhere new.</p></section>'
            for module in settings["modules"]:
                if module == "search":
                    body += '<form class="search" action="steed://action/search" method="get"><label class="sr-only" for="query">Search or enter a web address</label><input id="query" name="q" placeholder="Search or enter a web address" autocomplete="off" maxlength="4096" autofocus required><button type="submit">Go</button></form>'
                elif module == "image":
                    # The selected image fills the viewport behind the content.
                    pass
                elif module == "bookmarks":
                    body += section(
                        "Bookmarks",
                        [(b["title"], b["url"]) for b in state["bookmarks"][:24]],
                        "Save a page with Ctrl+D.",
                        ' <a href="steed://action/bookmark">Add</a> · <a href="steed://action/bookmarks">Manage all</a>',
                    )
                elif module == "recent":
                    entries = self.registry.closed_tabs.entries(profile)[:10]
                    body += section(
                        "Recently closed",
                        [
                            (
                                c.state.title or c.state.url,
                                "steed://action/reopen?"
                                + urlencode({"id": c.state.tab_id}),
                            )
                            for c in entries
                        ],
                        "Tabs you close will appear here during this session.",
                    )
                elif module == "history":
                    entries = self.registry.get_history(profile).entries()[:10]
                    body += section(
                        "Recent visits",
                        [(e.title, e.url) for e in entries],
                        "Your recent visits will appear here.",
                    )
                elif module == "news":
                    body += self.news_section(profile)
        css = (
            Path(__file__).resolve().parents[1] / "ui" / "assets" / "start_page.css"
        ).read_text(encoding="utf-8")
        css += root_css
        background = ""
        if name == "newtab" and "image" in state["settings"]["modules"]:
            image = state["settings"]["image"]
            if image:
                css += 'body { --newtab-image: url("' + image + '"); }'
                background = " has-background"
        theme = "dark" if styles.dark(profile) else "light"
        title = "Welcome to Steed" if name == "welcome" else "New Tab"
        private_note = " · Temporary; nothing is saved" if private else ""
        template = Template(
            (
                Path(__file__).resolve().parents[1]
                / "ui"
                / "assets"
                / "start_page.html"
            ).read_text(encoding="utf-8")
        )
        return template.substitute(
            theme=theme,
            title=title,
            css=css,
            body=body,
            menu=newtab_menu() if styles.get(profile)["newtab_menu"] else "",
            profile=escape(profile_label + private_note),
            body_class=(
                "minimal" if name == "newtab" and state["settings"]["minimal"] else ""
            )
            + background,
        ).encode()

    def render_simple(self, profile, settings, root_css):
        """The name, an empty search box, and a background: a photo that
        changes now and then, a colour, or your own image."""
        styles = self.registry.page_style
        dark = styles.dark(profile)
        background, credit, scrim = self._background(settings, dark)
        menu = newtab_menu() if styles.get(profile)["newtab_menu"] else ""
        private = profile == "search"
        css = SIMPLE_CSS + root_css + background
        return (
            Template(SIMPLE_PAGE)
            .substitute(
                theme="dark" if dark else "light",
                css=css,
                scrim="scrim" if scrim else "",
                menu=menu,
                note='<span class="note">Incognito · nothing is saved</span>'
                if private
                else "",
                credit=credit,
            )
            .encode()
        )

    def _background(self, settings, dark):
        """(CSS, photo credit HTML, whether light text needs a scrim)."""
        import base64

        choice = settings["background"]
        if choice == "image" and settings["image"]:
            return (f'body {{ --newtab-image: url("{settings["image"]}"); }}', "", True)
        if choice == "photo":
            photo = self.photos.current()
            self.photos.refresh(settings["photo_every"])
            if photo is not None:
                try:
                    data = base64.b64encode(photo["path"].read_bytes()).decode("ascii")
                except OSError:
                    data = ""
                if data:
                    author = escape(photo["author"] or "Unsplash")
                    credit = (
                        f'<a class="credit" href="{escape(photo["page"])}">'
                        f"Photo: {author} · Unsplash</a>"
                        if photo["page"]
                        else f'<span class="credit">Photo: {author}</span>'
                    )
                    return (
                        f'body {{ --newtab-image: url("data:image/jpeg;base64,{data}"); }}',
                        credit,
                        True,
                    )
            # Until a photo arrives (or offline): a soft gradient.
            return (FALLBACK_GRADIENT_DARK if dark else FALLBACK_GRADIENT, "", False)
        color = settings["color"]
        if color:
            return (f"body {{ --newtab-color: {color}; }}", "", _is_dark(color))
        return (FALLBACK_GRADIENT_DARK if dark else FALLBACK_GRADIENT, "", False)

    def news_section(self, profile):
        feed = self.state(profile)["settings"]["feed"]
        items, status = self.news.snapshot(profile, feed)
        return section(
            "News",
            items,
            status,
            ' <a href="steed://action/refresh-news">Refresh</a>',
            identifier="news",
        )


def _is_dark(color):
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.299 * red + 0.587 * green + 0.114 * blue < 140


FALLBACK_GRADIENT = (
    "body { --newtab-color: #eef2f8; --newtab-gradient: radial-gradient(circle at"
    " 20% 15%, #dfe9ff 0, transparent 55%), radial-gradient(circle at 85% 80%,"
    " #f3e3f7 0, transparent 50%); }"
)
FALLBACK_GRADIENT_DARK = (
    "body { --newtab-color: #12151c; --newtab-gradient: radial-gradient(circle at"
    " 20% 15%, #1f2d4d 0, transparent 55%), radial-gradient(circle at 85% 80%,"
    " #3a2342 0, transparent 50%); }"
)
SIMPLE_CSS = """
* { box-sizing: border-box; }
html, body { height: 100%; }
body { margin: 0; color: var(--ink); font: 16px/1.5 system-ui, sans-serif; background:
  var(--newtab-gradient, none), var(--newtab-color, var(--bg)); background-size: cover;
  display: flex; flex-direction: column; isolation: isolate; }
body::before { content: ""; position: fixed; inset: 0; z-index: -2;
  background-image: var(--newtab-image, none); background-size: cover; background-position: center; }
body.scrim::after { content: ""; position: fixed; inset: 0; z-index: -1;
  background: linear-gradient(#0000004d, #00000026 40%, #00000066); }
body.scrim { color: #fff; }
.corner { position: fixed; top: 14px; right: 18px; display: flex; gap: 8px; align-items: center; z-index: 5; }
.corner .icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 50%;
  color: inherit; background: color-mix(in srgb, var(--surface) 35%, transparent); backdrop-filter: blur(6px); }
.corner .icon:hover { background: color-mix(in srgb, var(--surface) 70%, transparent); }
.corner .icon svg { width: 20px; height: 20px; }
main.simple { margin: auto; width: min(640px, 100% - 48px); text-align: center; padding-bottom: 12vh; }
.wordmark { font-size: clamp(56px, 9vw, 88px); font-weight: 750; letter-spacing: -3px; margin: 0 0 22px;
  line-height: 1; }
body.scrim .wordmark { text-shadow: 0 2px 24px #0006; }
.search { display: flex; align-items: center; height: 54px; padding: 0 22px; border-radius: 27px;
  background: var(--surface); border: 1px solid var(--border); box-shadow: 0 6px 28px #0000002a; }
.search:focus-within { border-color: var(--accent); }
.search input { flex: 1; min-width: 0; font: inherit; font-size: 17px; color: var(--ink); background: transparent; border: 0; outline: none; }
.credit, .note { position: fixed; left: 16px; bottom: 12px; font-size: 12px; color: inherit; opacity: .8; text-decoration: none; }
.credit:hover { opacity: 1; text-decoration: underline; }
.browser-menu { position: relative; font-size: 14px; }
.browser-menu summary { list-style: none; cursor: pointer; font-size: 0; display: grid; place-items: center;
  width: 38px; height: 38px; border-radius: 50%; background: color-mix(in srgb, var(--surface) 35%, transparent);
  backdrop-filter: blur(6px); }
.browser-menu summary::-webkit-details-marker { display: none; }
.browser-menu summary::before { content: "☰"; font-size: 18px; }
.browser-menu > div { position: absolute; right: 0; top: 100%; margin-top: 6px; min-width: 190px; padding: 7px;
  border: 1px solid var(--border); border-radius: 12px; background: var(--surface); box-shadow: 0 8px 24px #0002; }
.browser-menu a { display: block; padding: 8px 12px; text-decoration: none; color: var(--ink); border-radius: 6px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
"""
GEAR = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"'
    ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8'
    " 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0"
    " 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7"
    " 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2"
    " 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7"
    " 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3"
    ' 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>'
)
SIMPLE_PAGE = (
    '<!doctype html><html lang="en" data-theme="$theme"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\';'
    " style-src 'unsafe-inline'; img-src data:; form-action steed:; base-uri 'none';"
    " object-src 'none'\">"
    '<title>New Tab</title><style>$css</style></head><body class="$scrim">'
    '<div class="corner">'
    '<a class="icon" href="steed://action/customize" title="Customize New Tab"'
    ' aria-label="Customize New Tab">' + GEAR + "</a>$menu</div>"
    '<main class="simple"><h1 class="wordmark">Steed</h1>'
    '<form class="search" action="steed://action/search" method="get">'
    '<label class="sr-only" for="query">Search or enter a web address</label>'
    '<input id="query" name="q" autocomplete="off" maxlength="4096" autofocus required>'
    "</form></main>$credit$note</body></html>"
)


def newtab_menu():
    links = "".join(
        f'<a href="steed://action/menu?name={name}">{label}</a>'
        for name, label in (
            ("general", "General"),
            ("profile", "Profile"),
            ("tabs", "Tabs"),
            ("view", "View"),
            ("cookies", "Cookies"),
            ("agents", "Agents"),
        )
    )
    return (
        '<details class="browser-menu"><summary>☰ Browser menu</summary><div>'
        + links
        + '<a href="steed://style/">Style</a><hr>'
        + '<a href="steed://settings/">Open in Settings ↗</a></div></details>'
    )


def section(title, items, empty, action="", *, identifier=""):
    links = "".join(
        f'<li><a href="{escape(url, quote=True)}"><span>{escape(title)}</span><span class="arrow" aria-hidden="true">↗</span></a></li>'
        for title, url in items
    )
    content = f'<ul class="links">{links}</ul>' if links else f"<p>{escape(empty)}</p>"
    return f'<section class="card" id="{identifier}"><div class="section-head"><h2>{title}</h2><span class="section-actions">{action}</span></div>{content}</section>'


def clean_html(value):
    if (
        not isinstance(value, str)
        or "\x00" in value
        or len(value.encode("utf-8")) > MAX_HTML_BYTES
    ):
        raise ValueError("Use an HTML page of at most 256 KB without null bytes")
    return value


class StartPageHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, pages, profile_name, parent):
        super().__init__(parent)
        self.pages, self.profile_name = pages, profile_name
        self.api = PageApi(pages, profile_name, self)

    def requestStarted(self, job):
        endpoint = api_endpoint(job.requestUrl())
        if endpoint is not None:
            self.api.handle(job, endpoint)
            return
        app = app_id(job.requestUrl())
        if app is not None:
            # Panel apps load only directly (never from another page).
            if bytes(job.requestMethod()) != b"GET" or not job.initiator().isEmpty():
                job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
                return
            try:
                data = self.pages.render_app(self.profile_name, app)
            except (OSError, ValueError):
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                return
            buffer = QBuffer(job)
            buffer.setData(data)
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(b"text/html", buffer)
            return
        name = page_name(job.requestUrl())
        initiator = job.initiator()
        if (
            name is None
            or bytes(job.requestMethod()) != b"GET"
            or (not initiator.isEmpty() and page_name(initiator) is None)
        ):
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        try:
            data = self.pages.render(self.profile_name, name)
        except (OSError, ValueError):
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        buffer = QBuffer(job)
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(b"text/html", buffer)
