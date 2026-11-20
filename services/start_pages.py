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

from browser import webengine
from browser.bookmark_format import (
    MAX_BOOKMARKS,
    bookmark_name,
    export_bookmarks,
    parse_bookmarks,
)
from browser.utils import load_json, save_json
from services.command_broker import require_owner_thread
from services.page_api import PageApi, api_endpoint
from services.page_style import ROOT_CSS

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
    # Where photos come from (newtab_photo.SOURCES), the folder for "folder",
    # and "color" or "grayscale".
    "photo_source": "online",
    "photo_folder": "",
    "photo_style": "color",
    # The browser's toolbar and tabs take their colour from the background.
    "match_theme": True,
    # Customize inside the ☰ menu instead of its own gear button.
    "gear_in_menu": False,
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
    "watches",
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
    """Register before QApplication; host syntax keeps distinct page origins.
    Custom start pages call their own steed://newtab/api/… with fetch()."""
    webengine.register_scheme(b"steed")


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


def action_page(url):
    """The start page a link was clicked on. Welcome moves between its steps
    with #look, #bring and #agent, so the #fragment does not matter here."""
    return page_name(url.adjusted(QUrl.UrlFormattingOption.RemoveFragment))


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
    match_theme = value.get("match_theme", True)
    if type(match_theme) is not bool:
        raise ValueError("Choose whether the browser matches the background")
    from services.newtab_photo import SOURCES, STYLES

    source = value.get("photo_source", "online")
    folder = value.get("photo_folder", "")
    style = value.get("photo_style", "color")
    if source not in SOURCES or style not in STYLES:
        raise ValueError("Choose where photos come from and how they look")
    if (
        not isinstance(folder, str)
        or len(folder) > 4096
        or "\x00" in folder
        or (folder and not Path(folder).is_absolute())
    ):
        raise ValueError("Choose a folder of photos")
    gear_in_menu = value.get("gear_in_menu", False)
    if type(gear_in_menu) is not bool:
        raise ValueError("Choose where New Tab's settings button goes")
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
        "match_theme": match_theme,
        "gear_in_menu": gear_in_menu,
        "photo_source": source,
        "photo_folder": folder,
        "photo_style": style,
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
        # Profiles whose customize panel stays open on the next render.
        self.keep_open = set()
        self.keep_more = set()  # …with More options open too.
        # Where New Tab pages sit in the window, and its size (x, y, width,
        # height): with glass the picture fills the window (render_simple).
        self.canvas = None
        # Profiles that asked for a new photo now (their new tabs show it).
        self.photo_wanted = set()
        self._image_tints = {}

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
            body = welcome_steps(
                state["settings"]["background"], styles.get(profile)["glass"]
            )
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
        css += root_css + PAGES_CSS + (ONBOARD_CSS if name == "welcome" else "")
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
        from ui.lumen import level

        return template.substitute(
            theme=theme,
            effects=level(self.registry.config),
            title=title,
            css=css,
            body=body,
            menu=newtab_menu() if styles.get(profile)["newtab_menu"] else "",
            profile=escape(profile_label + private_note),
            body_class=(
                "minimal"
                if name == "newtab" and state["settings"]["minimal"]
                else "onboarding"
                if name == "welcome"
                else ""
            )
            + background,
        ).encode()

    def render_simple(self, profile, settings, root_css):
        """The name, an empty search box, and a background: a photo that
        changes now and then, a colour, or your own image."""
        styles = self.registry.page_style
        dark = styles.dark(profile)
        menu = newtab_menu() if styles.get(profile)["newtab_menu"] else ""
        private = profile == "search"
        glass = styles.get(profile)["glass"]
        from ui.lumen import level

        css = SIMPLE_CSS + root_css + (GLASS_CSS if glass else "") + LUMEN_CSS
        live, credit, scrim = self.live_parts(profile, settings)
        has_menu = bool(menu)
        in_menu = has_menu and settings["gear_in_menu"]
        opened = profile in self.keep_open
        panel = customize_panel(
            settings,
            open_=opened,
            glass=glass,
            in_menu=in_menu,
            menu_available=has_menu,
            photo=self.photos.current(),
            more_open=profile in self.keep_more,
        )
        if in_menu:
            menu, panel = newtab_menu(panel, open_=opened), ""
        self.keep_open.discard(profile)
        self.keep_more.discard(profile)
        return (
            Template(SIMPLE_PAGE)
            .substitute(
                panel=panel,
                theme="dark" if dark else "light",
                effects=level(self.registry.config),
                css=css,
                live=live,
                scrim=" ".join(
                    name for name, on in (("scrim", scrim), ("glass", glass)) if on
                ),
                svg=REFRACT_SVG if glass else "",
                menu=menu,
                note='<span class="note">Incognito · nothing is saved</span>'
                if private
                else "",
                credit=credit,
            )
            .encode()
        )

    def live_parts(self, profile, settings=None):
        """What changes on an open New Tab without reloading it: the picture,
        its credit, and the colours taken from it. (css, credit HTML, scrim)"""
        if settings is None:
            settings = self.state(profile)["settings"]
        styles = self.registry.page_style
        dark = styles.dark(profile)
        glass = styles.get(profile)["glass"]
        css, credit, scrim = self._background(settings, dark)
        if glass and self.canvas and settings["background"] != "color":
            # The picture fills the whole window: the bars show their part of
            # it (frosted), so the page shows it from where it sits.
            x, y, width, height = (int(value) for value in self.canvas)
            css += (
                f"body::before {{ left: -{x}px; top: -{y}px; width: {width}px;"
                f" height: {height}px; right: auto; bottom: auto; }}"
            )
        tint = self.theme_tint(profile)
        if tint:  # The page's highlights match the browser's.
            from ui.chrome_style import tint_colors

            toolbar, accent = tint_colors(tint, dark)
            # Browser colours the user fixed in Style win here too.
            chosen = styles.get(profile)["chrome"]
            toolbar = chosen.get("toolbar", toolbar)
            accent = chosen.get("accent", accent)
            # html:root[data-theme] outranks the theme's own dark palette.
            css += f"html:root[data-theme] {{ --accent: {accent}; }}"
            if dark:  # The search box and panel in the browser's colour too.
                css += (
                    f"html:root[data-theme] {{ --surface: {toolbar};"
                    f" --border: {accent}66;"
                    f" --soft: color-mix(in srgb, {accent} 28%, {toolbar}); }}"
                )
        return css, credit, scrim

    def theme_tint(self, profile) -> str | None:
        """The colour the browser takes from this profile's new tab background
        (a photo or your own image), or None."""
        try:
            settings = self.state(profile)["settings"]
        except (ValueError, OSError):
            return None
        if settings["layout"] != "simple" or not settings["match_theme"]:
            return None
        from PyQt6.QtGui import QImage

        from services.newtab_photo import theme_color

        if settings["background"] == "photo":
            photo = self.photos.current()
            if photo is None:
                return None
            return photo["color"] or theme_color(QImage(str(photo["path"])))
        if settings["background"] == "image" and settings["image"]:
            image = settings["image"]
            if image not in self._image_tints:
                import base64

                data = base64.b64decode(image.split(",", 1)[1])
                self._image_tints = {image: theme_color(QImage.fromData(data))}
            return self._image_tints[image]
        return None

    def background_image(self, profile):
        """(key, QImage) of the picture behind this profile's new tab (the
        photo or your own image) when the browser follows it, or None."""
        try:
            settings = self.state(profile)["settings"]
        except (ValueError, OSError):
            return None
        if settings["layout"] != "simple" or not settings["match_theme"]:
            return None
        from PyQt6.QtGui import QImage

        if settings["background"] == "photo":
            photo = self.photos.current()
            if photo is None:
                return None
            image = QImage(str(photo["path"]))
            key = f"photo:{photo['fetched']}"
        elif settings["background"] == "image" and settings["image"]:
            import base64
            import hashlib

            data = base64.b64decode(settings["image"].split(",", 1)[1])
            image, key = QImage.fromData(data), hashlib.sha1(data).hexdigest()
        else:
            return None
        return None if image.isNull() else (key, image)

    def set_from_page(self, profile, params):
        """A choice made in the new tab's customize panel (checked here)."""
        from services.newtab_photo import PERIODS, SOURCES, STYLES

        allowed = {
            "background": BACKGROUNDS,
            "layout": LAYOUTS,
            "photo_every": tuple(PERIODS),
            "match_theme": ("1", "0"),
            "gear_in_menu": ("1", "0"),
            "photo_source": SOURCES,
            "photo_style": STYLES,
        }
        changes = {}
        for key, value in params:
            if key == "color" and (
                not value or re.fullmatch(r"#[0-9a-fA-F]{6}", value)
            ):
                changes["color"] = value
            elif key in allowed and value in allowed[key]:
                changes[key] = (
                    value == "1" if key in ("match_theme", "gear_in_menu") else value
                )
            else:
                raise ValueError("Not a choice the panel offers")
        if not changes:
            raise ValueError("Nothing chosen")
        current = self.state(profile)["settings"]
        if changes.get("photo_source") == "folder" and not current["photo_folder"]:
            raise ValueError("Choose a folder of photos first")
        settings = {**self.state(profile)["settings"], **changes}
        self.keep_open.add(profile)
        if set(changes) & MORE_OPTIONS:
            self.keep_more.add(profile)
        self.configure(profile, settings)

    def _background(self, settings, dark):
        """(CSS, photo credit HTML, whether light text needs a scrim)."""
        import base64

        choice = settings["background"]
        if choice == "image" and settings["image"]:
            return (f'body {{ --newtab-image: url("{settings["image"]}"); }}', "", True)
        if choice == "photo":
            self.refresh_photo(settings)
            photo = self.photos.current()
            if photo is not None:
                try:
                    data = base64.b64encode(photo["path"].read_bytes()).decode("ascii")
                except OSError:
                    data = ""
                if data:
                    author = escape(photo["author"] or "Unsplash")
                    credit = (
                        ""  # Your own photos need no credit.
                        if photo["source"] != "online"
                        else f'<a class="credit" href="{escape(photo["page"])}">'
                        f"Photo: {author} · {escape(photo.get('provider') or 'Unsplash')}</a>"
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

    def refresh_photo(self, settings, now=False):
        """A new photo when one is due, from the source these settings choose."""
        self.photos.refresh(
            settings["photo_every"],
            now=now,
            source=settings["photo_source"],
            folder=settings["photo_folder"],
            style=settings["photo_style"],
        )

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


SWATCHES = (
    "#1f3a5f", "#2f5d50", "#5b3a6b", "#7a2e2e", "#8a6d1f",
    "#dfe8f5", "#e6f2e8", "#f4e6ef", "#f6ead8", "#ececec",
)  # fmt: skip


# Choices kept under "More options" (it stays open after one of them).
MORE_OPTIONS = {
    "photo_source",
    "photo_style",
    "photo_every",
    "match_theme",
    "layout",
    "gear_in_menu",
}


def welcome_steps(background, glass):
    """Getting started in three calm steps, one idea each: the look, your
    things from another browser, your agent. No script: the address's
    #fragment picks the step (and survives the page being shown again)."""

    def tile(label, href, kind, on):
        state = " on" if on else ""
        chosen = '<span class="chosen">Chosen</span>' if on else ""
        return (
            f'<a class="tile {kind}{state}" aria-pressed="{str(bool(on)).lower()}"'
            f' href="{href}"><span class="tile-art"></span>'
            f'<span class="tile-label">{label}{chosen}</span></a>'
        )

    return (
        '<div class="onboard">'
        '<ol class="steps" aria-label="Getting started">'
        '<li><a href="#look">Look</a></li><li><a href="#bring">Your things</a></li>'
        '<li><a href="#agent">Agent</a></li></ol>'
        '<section id="look" class="step"><h1>Welcome to Steed.</h1>'
        "<p>First, how should it look? You can change it any time.</p>"
        '<h2 class="choice">Background</h2>'
        '<div class="tiles">'
        + tile(
            "Photo",
            "steed://action/newtab-set?background=photo",
            "photo",
            background == "photo",
        )
        + tile(
            "Colour",
            "steed://action/newtab-set?background=color",
            "colour",
            background == "color",
        )
        + "</div>"
        # Glass is on or off with either background, so it is a switch.
        + f'<a class="switch{" on" if glass else ""}" role="switch"'
        f' aria-checked="{str(bool(glass)).lower()}"'
        f' href="steed://action/newtab-set?glass={"0" if glass else "1"}">'
        '<span class="switch-text"><strong>Liquid glass</strong>'
        "<span>See-through bars and panels over the page</span></span>"
        '<span class="track"><span class="knob"></span></span>'
        f'<span class="state">{"On" if glass else "Off"}</span></a>'
        + '<div class="actions"><a class="button" href="#bring">Next</a></div></section>'
        '<section id="bring" class="step"><h1>Bring your things.</h1>'
        "<p>Bookmarks and history from another browser, and your passwords "
        "from a file you export there.</p>"
        '<div class="actions"><a class="button" href="steed://action/import">Import…</a>'
        '<a class="button secondary" href="#agent">Not now</a></div></section>'
        '<section id="agent" class="step"><h1>Meet your agent.</h1>'
        "<p>Codex, Claude Code or another agent works beside you, only with what you allow.</p>"
        '<div class="actions"><a class="button" href="steed://action/agents">Open the agent panel</a>'
        '<a class="button secondary" href="steed://action/start">Start browsing</a></div></section>'
        '<a class="skip" href="steed://action/start">Skip the tour</a></div>'
    )


def customize_panel(
    settings,
    open_=False,
    glass=False,
    in_menu=False,
    menu_available=True,
    photo=None,
    more_open=False,
):
    """The gear's panel: links to Steed actions (the page runs no script).
    Calm first: the background as three tiles, what goes with the one
    chosen, and glass; everything else under More options.
    in_menu: a row inside the ☰ menu rather than its own button."""
    from urllib.parse import quote

    def choice(label, key, value, current):
        on = " on" if current else ""
        return (
            f'<a class="pill{on}" aria-pressed="{str(bool(current)).lower()}"'
            f' href="steed://action/newtab-set?{key}={quote(value, safe="")}">'
            f"{label}</a>"
        )

    def tile(label, href, kind, current):
        on = " on" if current else ""
        return (
            f'<a class="tile {kind}{on}" aria-pressed="{str(bool(current)).lower()}"'
            f' href="{href}"><span class="tile-art"></span><span>{label}</span></a>'
        )

    background = settings["background"]
    parts = [
        '<div class="tiles">',
        tile(
            "Photo",
            "steed://action/newtab-set?background=photo",
            "photo",
            background == "photo",
        ),
        tile(
            "Colour",
            "steed://action/newtab-set?background=color",
            "colour",
            background == "color",
        ),
        tile(
            "Your image", "steed://action/newtab-image", "image", background == "image"
        ),
        "</div>",
    ]
    if background == "photo":
        parts.append(
            '<div class="choices quick">'
            '<a class="pill primary" href="steed://action/newtab-photo">New photo</a>'
        )
        if photo is not None:
            saved = photo["favourite"] or photo["source"] == "favourites"
            parts.append(
                '<span class="pill on">♥ Saved</span>'
                if saved
                else '<a class="pill" href="steed://action/newtab-favourite">♡ Save</a>'
            )
            if photo["source"] == "online":
                parts.append(
                    '<a class="pill" href="steed://action/newtab-block">Skip this one</a>'
                )
        parts.append("</div>")
    if background == "color":
        parts.append('<div class="swatches">')
        parts.append(
            f'<a class="swatch theme{" on" if not settings["color"] else ""}"'
            ' href="steed://action/newtab-set?color=" title="Follow the theme"'
            ' aria-label="Follow the theme"></a>'
        )
        for color in SWATCHES:
            on = " on" if settings["color"] == color else ""
            parts.append(
                f'<a class="swatch{on}" style="background:{color}" title="{color}"'
                f' aria-label="{color}"'
                f' href="steed://action/newtab-set?color={quote(color, safe="")}"></a>'
            )
        parts.append("</div>")
    parts.append('<div class="choices look">')
    parts.append(choice("Liquid glass", "glass", "0" if glass else "1", glass))
    parts.append("</div>")

    more = []
    if background == "photo":
        source = settings["photo_source"]
        more.append('<h3>Photos from</h3><div class="choices">')
        for value, label in (
            ("online", "Online"),
            ("mine", "Your photos"),
            ("folder", "A folder"),
            ("favourites", "Favourites"),
        ):
            if value == "folder" and not settings["photo_folder"]:
                # No folder yet: choosing one comes first.
                more.append(
                    '<a class="pill" href="steed://action/newtab-folder">A folder…</a>'
                )
                continue
            more.append(choice(label, "photo_source", value, source == value))
        more.append(
            '<a class="pill" href="steed://action/newtab-add-photos">Add photos…</a>'
        )
        if settings["photo_folder"]:
            more.append(
                '<a class="pill" href="steed://action/newtab-folder">Change folder…</a>'
            )
        more.append("</div>")
        if settings["photo_folder"] and source == "folder":
            more.append(
                f'<p class="folder">{escape(Path(settings["photo_folder"]).name)}</p>'
            )
        style = settings["photo_style"]
        more.append('<h3>Photo</h3><div class="choices">')
        more.append(choice("Colour", "photo_style", "color", style == "color"))
        more.append(
            choice("Black & white", "photo_style", "grayscale", style == "grayscale")
        )
        more.append('</div><h3>A new one</h3><div class="choices">')
        for value, label in (
            ("new_tab", "Every tab"),
            ("hourly", "Hourly"),
            ("6h", "Every 6 hours"),
            ("daily", "Daily"),
        ):
            more.append(
                choice(label, "photo_every", value, settings["photo_every"] == value)
            )
        more.append("</div>")
    match = settings["match_theme"]
    more.append('<h3>Browser colours</h3><div class="choices">')
    more.append(choice("From the background", "match_theme", "1", match))
    more.append(choice("Steed's own", "match_theme", "0", not match))
    more.append('</div><h3>Layout</h3><div class="choices">')
    more.append(choice("Simple", "layout", "simple", settings["layout"] == "simple"))
    more.append(choice("Classic", "layout", "classic", settings["layout"] == "classic"))
    more.append("</div>")
    if menu_available:
        more.append('<h3>This button</h3><div class="choices">')
        more.append(choice("On its own", "gear_in_menu", "0", not in_menu))
        more.append(choice("In the ☰ menu", "gear_in_menu", "1", in_menu))
        more.append("</div>")
    more.append(
        '<a class="more" href="steed://action/customize">All New Tab settings…</a>'
    )
    parts.append(
        f'<details class="more-options"{" open" if more_open else ""}>'
        "<summary>More options</summary>" + "".join(more) + "</details>"
    )
    summary = (
        '<summary class="row">' + GEAR + "Customize New Tab</summary>"
        if in_menu
        else '<summary class="icon" title="Customize New Tab"'
        ' aria-label="Customize New Tab">' + GEAR + "</summary>"
    )
    return (
        f'<details class="customize"{" open" if open_ else ""}>'
        + summary
        + '<div class="panel">'
        + "".join(parts)
        + "</div></details>"
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
.browser-menu > summary { list-style: none; cursor: pointer; font-size: 0; display: grid; place-items: center;
  width: 38px; height: 38px; border-radius: 50%; background: color-mix(in srgb, var(--surface) 35%, transparent);
  backdrop-filter: blur(6px); }
.browser-menu > summary::-webkit-details-marker { display: none; }
.browser-menu > summary::before { content: "☰"; font-size: 18px; }
.browser-menu > div { position: absolute; right: 0; top: 100%; margin-top: 6px; min-width: 190px; padding: 7px;
  border: 1px solid var(--border); border-radius: 12px; background: var(--surface); box-shadow: 0 8px 24px #0002; }
.browser-menu a { display: block; padding: 8px 12px; text-decoration: none; color: var(--ink); border-radius: 6px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
.customize { position: relative; }
.browser-menu .customize summary.row { display: flex; align-items: center; gap: 9px; padding: 8px 12px; white-space: nowrap;
  border-radius: 6px; color: var(--ink); font-size: 14px; }
.browser-menu .customize summary.row svg { width: 16px; height: 16px; flex: none; }
.browser-menu .customize summary.row:hover, .browser-menu .customize[open] summary.row { background: var(--soft); }
.browser-menu .customize .panel { position: static; width: 290px; margin: 4px 0 0; padding: 8px 6px 4px;
  border: 0; border-top: 1px solid var(--border); border-radius: 0; box-shadow: none; background: none; }
.browser-menu .customize[open] ~ a, .browser-menu .customize[open] ~ hr { display: none; }
.browser-menu .panel .pill { display: inline-block; padding: 5px 11px; border-radius: 999px; }
.browser-menu .panel .swatch { display: block; padding: 0; border-radius: 50%; }
.browser-menu .panel .more { padding: 0; }
.browser-menu > div { max-height: calc(100vh - 80px); overflow-y: auto; }
body.glass .browser-menu .customize .panel { background: none; backdrop-filter: none; box-shadow: none; border-top-color: var(--glass-edge); }
body.glass .browser-menu .customize summary.row { color: inherit; }
.customize summary { list-style: none; cursor: pointer; }
.customize summary::-webkit-details-marker { display: none; }
.customize[open] > summary.icon { background: var(--surface); color: var(--ink); }
.panel { position: absolute; right: 0; top: calc(100% + 8px); width: 300px; padding: 14px 16px 16px;
  border: 1px solid var(--border); border-radius: 16px; background: var(--surface); color: var(--ink);
  box-shadow: 0 12px 40px #0003; text-align: left; font-size: 14px; }
.panel h2 { font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  color: var(--muted); margin: 14px 0 8px; } .panel h2:first-child { margin-top: 0; }
.panel h3 { font-size: 13px; font-weight: 600; margin: 12px 0 6px; color: var(--muted); }
.choices { display: flex; flex-wrap: wrap; gap: 6px; }
.pill { display: inline-block; padding: 5px 11px; border-radius: 999px; border: 1px solid var(--border);
  color: var(--ink); text-decoration: none; font-size: 13px; }
.pill:hover { background: var(--soft); }
.pill.on { background: var(--accent); border-color: var(--accent); color: var(--bg); }
.swatches { display: grid; grid-template-columns: repeat(6, 28px); gap: 8px; }
.swatch { width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--border); }
.swatch.on { outline: 2px solid var(--accent); outline-offset: 2px; }
.swatch.theme { background: linear-gradient(135deg, #dfe9ff, #f3e3f7); }
.more { display: block; margin-top: 14px; font-size: 13px; color: var(--accent); }
.photo-tools { margin-top: 8px; } .corner > .customize > .panel { max-height: calc(100vh - 90px); overflow-y: auto; } .panel .folder { width: 100%; margin: 2px 0 0; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
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
GLASS_CSS = """
/* ---- Liquid glass ---- */
body.glass { --glass: #ffffff2e; --glass-low: #ffffff0f; --glass-edge: #ffffff8c; --glass-shine: #ffffffd9;
  --glass-ink: inherit; --glass-panel: #ffffff38; }
body.glass:not(.scrim) { --glass: #ffffff8c; --glass-low: #ffffff40; --glass-edge: #ffffffe6;
  --glass-panel: #ffffffa6; }
:root[data-theme="dark"] body.glass:not(.scrim) { --glass: #ffffff1f; --glass-low: #ffffff0a;
  --glass-edge: #ffffff40; --glass-shine: #ffffff66; --glass-panel: #1e273599; }
body.glass .search, body.glass .panel, body.glass .corner .icon, body.glass .browser-menu > summary,
body.glass .browser-menu > div {
  color: var(--glass-ink); border: 1px solid var(--glass-edge);
  background: linear-gradient(160deg, var(--glass), var(--glass-low));
  -webkit-backdrop-filter: blur(16px) saturate(190%); backdrop-filter: url(#steed-refract) blur(16px) saturate(190%);
  box-shadow: inset 0 1px 0 var(--glass-shine), inset 0 -1px 1px #ffffff26, inset 0 0 22px #ffffff14,
    0 12px 36px #00000033; }
body.glass .panel, body.glass .browser-menu > div { background: var(--glass-panel); color: inherit;
  backdrop-filter: blur(30px) saturate(200%); }
body.glass.scrim .panel, body.glass.scrim .browser-menu > div { background: #14182466; }
body.glass .panel h2, body.glass .panel h3 { color: inherit; opacity: .75; }
body.glass .browser-menu a { color: inherit; }
body.glass .browser-menu hr { border: 0; border-top: 1px solid var(--glass-edge); opacity: .45; }
body.glass .search { position: relative; height: 58px; border-radius: 29px; overflow: hidden;
  transition: transform .45s cubic-bezier(.25, 1.6, .45, 1), box-shadow .3s; }
body.glass .search:focus-within { transform: scale(1.015); border-color: var(--glass-shine);
  box-shadow: inset 0 1px 0 var(--glass-shine), inset 0 0 26px #ffffff1f, 0 0 0 4px #ffffff1f, 0 18px 50px #00000040; }
body.glass .search::before { content: ""; position: absolute; inset: 0; pointer-events: none; border-radius: inherit;
  background: radial-gradient(120% 90% at 18% -30%, #ffffff73, transparent 55%),
    linear-gradient(105deg, transparent 38%, #ffffff2e 48%, transparent 58%);
  background-size: 100% 100%, 260% 100%; animation: steed-sheen 9s ease-in-out infinite; }
@keyframes steed-sheen { 0%, 100% { background-position: 0 0, 120% 0; } 50% { background-position: 0 0, -20% 0; } }
body.glass .search input { color: inherit; position: relative; }
body.glass .wordmark { text-shadow: 0 1px 0 #ffffff59, 0 8px 32px #00000040; }
body.glass .corner .icon:hover, body.glass .browser-menu > summary:hover { transform: scale(1.06); }
body.glass .corner .icon, body.glass .browser-menu > summary { transition: transform .35s cubic-bezier(.25, 1.6, .45, 1); }
body.glass .pill { border-color: var(--glass-edge); color: inherit; background: var(--glass-low); }
body.glass .pill:hover { background: var(--glass); }
body.glass .pill.on { background: var(--glass-shine); border-color: var(--glass-shine); color: #141824;
  box-shadow: inset 0 1px 0 #fff, 0 2px 8px #0000002e; }
body.glass .more { color: inherit; }
body.glass .customize[open] > summary.icon { background: var(--glass-shine); color: #141824; }
@media (prefers-reduced-motion: reduce) { body.glass .search::before { animation: none; }
  body.glass .search, body.glass .corner .icon { transition: none; } }
"""
# Bends what shows through the glass near its edges, like thick glass.
REFRACT_SVG = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
    '<filter id="steed-refract" x="0" y="0" width="100%" height="100%">'
    '<feTurbulence type="fractalNoise" baseFrequency="0.004 0.009" numOctaves="2"'
    ' seed="11" result="noise"/><feGaussianBlur in="noise" stdDeviation="3"'
    ' result="soft"/><feDisplacementMap in="SourceGraphic" in2="soft" scale="34"'
    ' xChannelSelector="R" yChannelSelector="G"/></filter></svg>'
)

# Lumen for the New Tab: the name set wide and heavy, a lit capsule to type
# in, one entrance as the page opens, and (Full effects) a slow drift of the
# photo. Pure CSS: the page runs no script.
LUMEN_CSS = """
body { font-family: var(--steed-font, "Mona Sans", system-ui, sans-serif); font-weight: 450; -webkit-font-smoothing: antialiased; }
.wordmark { font-family: var(--steed-display-font, "Mona Sans Expanded", "Mona Sans", system-ui, sans-serif); font-weight: 800; letter-spacing: -.035em; font-size: clamp(64px, 10vw, 112px); }
.search { height: 58px; border-radius: 29px; border: 1px solid color-mix(in srgb, #fff 16%, transparent);
  background: linear-gradient(180deg, color-mix(in srgb, var(--surface) 78%, transparent), color-mix(in srgb, var(--surface) 62%, transparent));
  backdrop-filter: blur(22px) saturate(1.4);
  box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 22%, transparent), 0 18px 50px -18px #000a;
  transition: box-shadow .4s cubic-bezier(.22, 1, .36, 1), border-color .3s, transform .5s cubic-bezier(.22, 1, .36, 1); }
.search:focus-within { border-color: color-mix(in srgb, var(--accent) 70%, transparent);
  box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 26%, transparent), 0 0 0 5px color-mix(in srgb, var(--accent) 16%, transparent),
    0 0 60px -10px var(--accent), 0 18px 50px -18px #000a; transform: translateY(-1px) scale(1.01); }
.search input { font-weight: 500; }
.corner .icon, .browser-menu > summary { transition: background-color .2s, transform .35s cubic-bezier(.34, 1.45, .64, 1); }
.corner .icon:hover, .browser-menu > summary:hover { transform: scale(1.08); }
html:not([data-effects=minimal]) body::before { transition: background-image .9s ease; }
/* Customize: calm first, the rest under More options. */
.panel { width: 320px; }
.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.panel a.tile { display: flex; flex-direction: column; gap: 6px; align-items: stretch; text-align: center;
  text-decoration: none; color: inherit; font-size: 13px; font-weight: 500; padding: 0; background: none; }
.panel a.tile:hover { background: none; }
.tile-art { display: block; width: 100%; aspect-ratio: 4 / 3; border-radius: 12px; border: 2px solid transparent;
  background: color-mix(in srgb, var(--surface) 70%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, #fff 12%, transparent);
  transition: border-color .2s, transform .35s cubic-bezier(.34, 1.45, .64, 1); }
.tile:hover .tile-art { transform: translateY(-2px); }
.tile.on .tile-art { border-color: var(--accent); }
.tile.photo .tile-art { background: var(--newtab-image, linear-gradient(135deg, #577590, #f3722c)) center / cover; }
.tile.colour .tile-art { background: conic-gradient(from 200deg, #1f3a5f, #2f5d50, #8a6d1f, #7a2e2e, #5b3a6b, #1f3a5f); }
.tile.image .tile-art { background: repeating-linear-gradient(135deg, color-mix(in srgb, var(--ink) 10%, transparent) 0 6px, transparent 6px 12px); }
.quick, .look, .swatches { margin-top: 12px; }
.panel .pill.primary, .browser-menu .panel .pill.primary { background: var(--accent); color: var(--bg); border-color: transparent; font-weight: 600; }
.more-options { margin-top: 14px; border-top: 1px solid color-mix(in srgb, var(--ink) 12%, transparent); padding-top: 10px; }
.more-options > summary { cursor: pointer; list-style: none; color: var(--muted); font-size: 13px; font-weight: 500; }
.more-options > summary::-webkit-details-marker { display: none; }
.more-options > summary::after { content: "›"; margin-left: 6px; display: inline-block; transition: transform .2s; }
.more-options[open] > summary::after { transform: rotate(90deg); }
.more-options h3 { margin: 12px 0 6px; }
@keyframes mark-in { from { opacity: 0; transform: translateY(18px) scale(.97); letter-spacing: .02em; filter: blur(8px); } }
@keyframes field-in { from { opacity: 0; transform: translateY(14px) scaleX(.9); } }
@keyframes fade-in { from { opacity: 0; } }
@keyframes drift { from { transform: scale(1.0) translate3d(0, 0, 0); } to { transform: scale(1.07) translate3d(-1.2%, -.8%, 0); } }
html[data-effects=full] .wordmark { animation: mark-in .9s cubic-bezier(.22, 1, .36, 1) both; }
html[data-effects=full] .search { animation: field-in .8s .12s cubic-bezier(.22, 1, .36, 1) both; }
html[data-effects=full] .corner, html[data-effects=full] .credit { animation: fade-in .8s .3s both; }
html[data-effects=full] body::before { animation: fade-in .7s both, drift 60s 0.7s ease-in-out infinite alternate; will-change: transform; }
html[data-effects=balanced] .wordmark, html[data-effects=balanced] .search { animation: fade-in .35s both; }
.panel h3, .panel h2 { text-transform: none; letter-spacing: 0; font-weight: 600; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
"""

# Welcome: one step at a time (the #fragment chooses; #look first).
ONBOARD_CSS = """
body.onboarding header nav, body.onboarding .browser-menu, body.onboarding footer { display: none; }
.onboard { max-width: 620px; margin: 3vh auto 0; padding: 0 24px 40px; }
.steps { list-style: none; display: flex; gap: 8px; padding: 0; margin: 0 0 26px; }
.steps a { display: block; padding: 6px 14px; border-radius: 999px; color: var(--muted); text-decoration: none;
  font-size: 13px; font-weight: 600; border: 1px solid color-mix(in srgb, var(--border) 70%, transparent); }
.onboard:not(:has(.step:target)) .steps li:nth-child(1) a,
.onboard:has(#look:target) .steps li:nth-child(1) a, .onboard:has(#bring:target) .steps li:nth-child(2) a,
.onboard:has(#agent:target) .steps li:nth-child(3) a { color: var(--ink); background: var(--soft); border-color: transparent; }
.step { display: none; }
.step:target, .onboard:not(:has(.step:target)) #look { display: block; }
.step h1 { margin: 0 0 10px; }
.step p { font-size: 17px; max-width: 46ch; }
.step h2.choice { margin: 20px 0 0; font-size: 14px; color: var(--muted); font-weight: 600; }
.step .tiles { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; margin: 12px 0 8px; max-width: 360px; }
.step .tile { display: flex; flex-direction: column; gap: 8px; text-decoration: none; color: var(--ink); font-weight: 600; font-size: 14px; }
.step .tile-label { display: flex; align-items: center; gap: 8px; }
.step .chosen { font-size: 12px; font-weight: 600; padding: 2px 9px; border-radius: 999px;
  background: var(--accent); color: var(--bg); }
.step .tile-art { display: block; aspect-ratio: 16 / 9; border-radius: 16px; border: 2px solid transparent;
  background: var(--surface); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ink) 10%, transparent);
  transition: border-color .2s, transform .35s cubic-bezier(.34, 1.45, .64, 1); }
.step .tile:hover .tile-art { transform: translateY(-3px); }
/* The chosen background: a ring in the accent colour and a check. */
.step .tile .tile-art { position: relative; }
.step .tile.on .tile-art { border-color: var(--bg);
  box-shadow: 0 0 0 3px var(--accent), 0 14px 30px -18px var(--accent); }
.step .tile.on .tile-art::after { content: "✓"; position: absolute; top: 10px; right: 10px; display: grid;
  place-items: center; width: 26px; height: 26px; border-radius: 50%; background: var(--accent); color: var(--bg);
  font-size: 15px; font-weight: 800; }
.step .tile:not(.on) .tile-art { opacity: .78; }
.step .tile:not(.on):hover .tile-art { opacity: 1; }
.step .switch { display: flex; align-items: center; gap: 14px; max-width: 360px; margin-top: 18px; padding: 12px 16px;
  border-radius: 16px; text-decoration: none; color: var(--ink); background: var(--surface);
  border: 1px solid color-mix(in srgb, var(--border) 70%, transparent); }
.step .switch-text { display: flex; flex-direction: column; gap: 2px; flex: 1; }
.step .switch-text span { font-size: 13px; color: var(--muted); }
.step .track { position: relative; width: 44px; height: 26px; border-radius: 999px; flex: none;
  background: color-mix(in srgb, var(--ink) 18%, transparent); transition: background-color .2s; }
.step .knob { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%;
  background: var(--surface); box-shadow: 0 1px 3px #0005; transition: left .3s cubic-bezier(.34, 1.45, .64, 1); }
.step .switch.on .track { background: var(--accent); }
.step .switch.on .knob { left: 21px; }
.step .switch .state { width: 2.2em; font-size: 13px; font-weight: 600; color: var(--muted); }
.step .switch.on .state { color: var(--ink); }
.step .tile.photo .tile-art { background: linear-gradient(160deg, #f3a66b, #6a4c93 60%, #1d3557); }
.step .tile.colour .tile-art { background: conic-gradient(from 200deg, #1f3a5f, #2f5d50, #8a6d1f, #7a2e2e, #5b3a6b, #1f3a5f); }
.step .tile.glass .tile-art { background: linear-gradient(135deg, color-mix(in srgb, #fff 35%, transparent), color-mix(in srgb, #fff 8%, transparent)),
  linear-gradient(160deg, #577590, #f3722c); }
.step .actions { display: flex; gap: 10px; margin-top: 26px; flex-wrap: wrap; }
.onboard .skip { display: inline-block; margin-top: 40px; color: var(--muted); font-size: 13px; }
@keyframes step-in { from { opacity: 0; transform: translateY(10px); } }
html:not([data-effects=minimal]) .step:target, html:not([data-effects=minimal]) .onboard:not(:has(.step:target)) #look {
  animation: step-in .45s cubic-bezier(.22, 1, .36, 1); }
"""

# Lumen for Welcome and the classic start pages: the same type, lit cards
# that answer the pointer with a lift, and capsule buttons.
PAGES_CSS = """
body { font-family: var(--steed-font, "Mona Sans", system-ui, sans-serif); font-weight: 450; -webkit-font-smoothing: antialiased; }
h1, h2, .brand { font-family: var(--steed-title-font, "Mona Sans SemiExpanded", "Mona Sans", system-ui, sans-serif); font-weight: 700; letter-spacing: -.01em; }
.hero h1 { font-family: var(--steed-display-font, "Mona Sans Expanded", "Mona Sans", system-ui, sans-serif); font-weight: 800; letter-spacing: -.03em;
  font-size: clamp(40px, 6.4vw, 72px); line-height: 1.02; }
.eyebrow { text-transform: none; letter-spacing: 0; }
.card { border-radius: 20px; border-color: color-mix(in srgb, var(--border) 70%, transparent);
  box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 7%, transparent);
  transition: transform .45s cubic-bezier(.22, 1, .36, 1), box-shadow .45s cubic-bezier(.22, 1, .36, 1), border-color .3s; }
.card:hover { transform: translateY(-3px); border-color: color-mix(in srgb, var(--accent) 35%, var(--border));
  box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 10%, transparent), 0 22px 44px -24px color-mix(in srgb, var(--accent) 60%, #000); }
button, .button { border-radius: 999px; transition: transform .3s cubic-bezier(.34, 1.45, .64, 1), background-color .2s; }
button:active, .button:active { transform: scale(.965); }
.search { border-radius: 22px; }
@keyframes rise { from { opacity: 0; transform: translateY(16px); } }
html[data-effects=full] .hero { animation: rise .7s cubic-bezier(.22, 1, .36, 1) both; }
html[data-effects=full] .cards > * { animation: rise .7s cubic-bezier(.22, 1, .36, 1) both; }
html[data-effects=full] .cards > :nth-child(2) { animation-delay: .06s; }
html[data-effects=full] .cards > :nth-child(3) { animation-delay: .12s; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
"""

SIMPLE_PAGE = (
    '<!doctype html><html lang="en" data-theme="$theme" data-effects="$effects">'
    '<head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\';'
    " style-src 'unsafe-inline'; img-src data:; form-action steed:; base-uri 'none';"
    " object-src 'none'\">"
    '<title>New Tab</title><style>$css</style><style id="steed-live">$live</style>'
    '</head><body class="$scrim">$svg'
    '<div class="corner">$panel$menu</div>'
    '<main class="simple"><h1 class="wordmark">Steed</h1>'
    '<form class="search" action="steed://action/search" method="get">'
    '<label class="sr-only" for="query">Search or enter a web address</label>'
    '<input id="query" name="q" autocomplete="off" maxlength="4096" autofocus required>'
    '</form></main><span id="steed-credit">$credit</span>$note</body></html>'
)


def newtab_menu(extra="", open_=False):
    """The ☰ menu; extra goes first in its dropdown (the Customize row)."""
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
        f'<details class="browser-menu"{" open" if open_ else ""}>'
        "<summary>☰ Browser menu</summary><div>"
        + extra
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


class StartPageHandler(webengine.SchemeHandler):
    def __init__(self, pages, profile_name, parent):
        super().__init__(parent)
        self.pages, self.profile_name = pages, profile_name
        self.api = PageApi(pages, profile_name, self)

    def handle(self, request):
        endpoint = api_endpoint(request.url)
        if endpoint is not None:
            self.api.handle(request, endpoint)
            return
        app = app_id(request.url)
        if app is not None:
            # Panel apps load only directly (never from another page).
            if request.method != b"GET" or not request.initiator.isEmpty():
                request.fail("denied")
                return
            try:
                data = self.pages.render_app(self.profile_name, app)
            except (OSError, ValueError):
                request.fail("failed")
                return
            request.reply(b"text/html", data)
            return
        name = page_name(request.url)
        initiator = request.initiator
        if (
            name is None
            or request.method != b"GET"
            or (not initiator.isEmpty() and page_name(initiator) is None)
        ):
            request.fail("denied")
            return
        try:
            data = self.pages.render(self.profile_name, name)
        except (OSError, ValueError):
            request.fail("failed")
            return
        request.reply(b"text/html", data)
