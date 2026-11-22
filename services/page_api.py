"""steed://newtab/api/… — data for custom New Tab and Welcome pages.

A custom start page (its own HTML and JavaScript) can ask Steed for web data,
open tabs, history and bookmarks with fetch("steed://newtab/api/<name>").
Each capability follows the user's local-page access setting: never, ask
(a prompt the first time in a session) or always. Only the start page itself
may call these addresses; websites cannot reach the steed: scheme.
"""

import json
import re
from urllib.parse import parse_qs

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

from browser import access_levels

ENDPOINTS = {
    "fetch": "web",
    "tabs": "tabs",
    "history": "history",
    "bookmarks": "bookmarks",
    # Your own pages also see and run what you made in Steed.
    "watches": "steed",
    "routines": "steed",
    "skills": "steed",
    "run-skill": "automation",
    "run-routine": "automation",
    "automations": "steed",
    "run-automation": "automation",
    "open": "automation",
    "notify": "automation",
}
# What only your own steed:<name> pages may ask (not New Tab or panel apps).
YOURS_ONLY = frozenset(
    {
        "watches",
        "routines",
        "skills",
        "automations",
        "run-skill",
        "run-routine",
        "run-automation",
        "open",
        "notify",
    }
)
FETCH_LIMIT = 2 * 1024 * 1024
FETCH_TIMEOUT_MS = 15000
HISTORY_LIMIT = 200


APP_HOST = re.compile(r"app-[a-f0-9]{12}")


def api_endpoint(url):
    """The endpoint name for steed://<page>/api/<name>, or None.

    Pages are the custom New Tab and Welcome pages and side-panel apps.
    """
    from services.start_pages import RESERVED_NAMES, YOUR_PAGE_NAME

    host = url.host() or ""
    yours = (
        YOUR_PAGE_NAME.fullmatch(host)
        and host not in RESERVED_NAMES
        and not host.startswith("app-")
    )
    if (
        url.scheme() == "steed"
        and (host in ("newtab", "welcome") or APP_HOST.fullmatch(host) or yours)
        and url.path().startswith("/api/")
        and not url.userInfo()
        and url.port() == -1
    ):
        name = url.path().removeprefix("/api/")
        if name in YOURS_ONLY and not yours:
            return None
        return name if name in ENDPOINTS else None
    return None


class PageApi(QObject):
    """Answers one profile's start page; decisions last for the session."""

    def __init__(self, pages, profile_name, parent=None):
        super().__init__(parent)
        self.pages = pages
        self.profile_name = profile_name
        self.session = {}
        self.prompts = {}
        self.network = None

    @property
    def config(self):
        return self.pages.registry.config

    def handle(self, job, name):
        url, initiator = job.url, job.initiator
        # Only the start page on the same steed: host may ask.
        if (
            job.method != b"GET"
            or initiator.scheme() != "steed"
            or initiator.host() != url.host()
            or not self._custom_page(url.host())
        ):
            job.fail("denied")
            return
        capability = ENDPOINTS[name]
        policy = access_levels.values(self.config, "pages")[capability]
        # Session answers belong to one page: allowing an app is not allowing another.
        key = (url.host(), capability)
        if policy == "always" or self.session.get(key) is True:
            self._serve(job, name, url)
        elif policy == "never" or self.session.get(key) is False:
            self._json(job, {"error": "denied", "capability": capability}, 403)
        else:
            self._ask(job, name, url, capability, key)

    def _custom_page(self, host):
        if APP_HOST.fullmatch(host):
            return self.pages.app(self.profile_name, host[4:]) is not None
        if host not in ("newtab", "welcome"):
            return self.pages.your_page(self.profile_name, host) is not None
        state = self.pages.state(self.profile_name)
        return bool(state.get("html" if host == "newtab" else "welcome_html"))

    def _describe(self, host):
        if APP_HOST.fullmatch(host):
            app = self.pages.app(self.profile_name, host[4:])
            return f"Your panel app “{app['name'] if app else 'app'}”"
        if host not in ("newtab", "welcome"):
            return f"Your page steed:{host}"
        return (
            "Your custom New Tab page"
            if host == "newtab"
            else "Your custom Welcome page"
        )

    def _ask(self, job, name, url, capability, key):
        waiting = self.prompts.setdefault(key, [])
        waiting.append((job, name, url))
        if len(waiting) > 1:
            return  # One question per capability; later requests share it.
        from PyQt6.QtWidgets import QApplication, QMessageBox

        spec = access_levels.PAGE_CAPABILITIES[capability]
        box = QMessageBox(QApplication.activeWindow())
        who = self._describe(url.host())
        box.setWindowTitle("Allow this page?")
        box.setText(f"{who} asks to: {spec['label'].lower()}.\n\n{spec['help']}")
        once = box.addButton("Allow this session", QMessageBox.ButtonRole.AcceptRole)
        always = box.addButton("Always allow", QMessageBox.ButtonRole.YesRole)
        box.addButton("Don't allow", QMessageBox.ButtonRole.RejectRole)

        def answered():
            clicked = box.clickedButton()
            allowed = clicked in (once, always)
            if clicked is always:
                access_levels.set_policy(self.config, "pages", capability, "always")
            self.session[key] = allowed
            for pending, pending_name, pending_url in self.prompts.pop(key, []):
                if not pending.alive:
                    continue
                if allowed:
                    self._serve(pending, pending_name, pending_url)
                else:
                    self._json(
                        pending, {"error": "denied", "capability": capability}, 403
                    )

        box.finished.connect(lambda _: answered())
        box.setModal(False)
        box.show()

    def _serve(self, job, name, url):
        if name == "fetch":
            self._fetch(job, url)
            return
        registry = self.pages.registry
        if name in YOURS_ONLY:
            try:
                self._json(job, self._yours(name, parse_qs(url.query())))
            except ValueError as error:
                self._json(job, {"error": str(error)}, 400)
            return
        if name == "bookmarks":
            data = [
                {"title": b.get("title", ""), "url": b.get("url", "")}
                for b in self.pages.state(self.profile_name).get("bookmarks", [])
            ]
        elif name == "history":
            data = [
                {"title": entry.title, "url": entry.url}
                for entry in registry.get_history(self.profile_name).entries()[
                    :HISTORY_LIMIT
                ]
            ]
        else:
            data = self._tabs()
        self._json(job, data)

    def _window(self):
        """A window of this profile, to run things in."""
        from PyQt6.QtWidgets import QApplication

        windows = [
            w
            for w in QApplication.topLevelWidgets()
            if getattr(w, "profile_manager", None) is not None
            and not getattr(w, "_closing", True)
            and w.profile_manager.registry is self.pages.registry
            and w.profile_manager.current_profile_name == self.profile_name
        ]
        active = QApplication.activeWindow()
        return active if active in windows else (windows[0] if windows else None)

    def _yours(self, name, query):
        """Dashboards and automations for your own pages."""
        registry = self.pages.registry
        one = lambda key: (query.get(key) or [""])[0]  # noqa: E731
        if name == "watches":
            return [
                {
                    "title": w.get("title", ""),
                    "url": w.get("url", ""),
                    "last_change": w.get("last_change", 0),
                    "last_summary": w.get("last_summary", ""),
                    "paused": bool(w.get("paused")),
                }
                for w in registry.watches.list()
            ]
        if name == "routines":  # Routines are automations now.
            from services.automations import describe_trigger

            return [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "when": describe_trigger(a["trigger"]),
                    "last_run": a["last_run"],
                    "paused": a["paused"],
                }
                for a in registry.automations.list()
                if a["trigger"]["kind"] == "schedule"
            ]
        if name == "automations":
            from services.automations import describe_trigger

            return [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "runs": describe_trigger(a["trigger"]),
                    "last_run": a["last_run"],
                    "last_outcome": a["last_outcome"],
                    "paused": a["paused"],
                }
                for a in registry.automations.list()
            ]
        if name == "run-automation":
            if not registry.automations.run(one("id")):
                raise ValueError("No such automation.")
            return {"started": True}
        if name == "skills":
            return [
                {"id": s["id"], "name": s["name"], "ask": s["ask"]}
                for s in registry.skills.list()
            ]
        window = self._window()
        if name == "run-skill":
            skill = registry.skills.get(one("id")) or registry.skills.find(one("name"))
            if skill is None or window is None:
                raise ValueError("No such skill, or no window to run it in.")
            window.run_skill(skill["id"])
            return {"ran": skill["name"]}
        if name == "run-routine":
            found = registry.automations.get(one("id"))
            if found is None or not registry.automations.run(found["id"]):
                raise ValueError("No such routine.")
            return {"ran": found["name"], "started": True}
        if name == "open":
            target = QUrl(one("url"))
            if target.scheme() not in ("http", "https") or not target.host():
                raise ValueError("Open an http or https address.")
            if window is None:
                raise ValueError("No window to open it in.")
            window._new_tab(url=target.toString())
            return {"opened": target.toString()}
        title, text = one("title")[:120], one("text")[:400]
        if not title:
            raise ValueError("Give the notification a title.")
        if getattr(self, "notifier", None) is None:
            from ui.desktop_notify import DesktopNotifier

            self.notifier = DesktopNotifier(self)
        self.notifier.notify(title, text)
        return {"notified": True}

    def _tabs(self):
        from PyQt6.QtWidgets import QApplication

        rows = []
        for widget in QApplication.topLevelWidgets():
            manager = getattr(widget, "profile_manager", None)
            if (
                manager is None
                or getattr(widget, "_closing", True)
                or manager.registry is not self.pages.registry
                or manager.current_profile_name != self.profile_name
            ):
                continue
            for tab in widget.tabs:
                if getattr(tab, "agent_panel", False):
                    continue
                rows.append({"title": tab.title(), "url": widget._tab_url(tab)})
        return rows

    def _fetch(self, job, url):
        target = QUrl(parse_qs(url.query()).get("url", [""])[0])
        if (
            not target.isValid()
            or target.scheme() not in ("http", "https")
            or target.userInfo()
        ):
            self._json(job, {"error": "Use an http or https address"}, 400)
            return
        if self.network is None:
            # Its own empty cookie jar: page fetches never carry sign-ins.
            self.network = QNetworkAccessManager(self)
        request = QNetworkRequest(target)
        request.setTransferTimeout(FETCH_TIMEOUT_MS)
        for attribute in (
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
        ):
            request.setAttribute(attribute, QNetworkRequest.LoadControl.Manual)
        reply = self.network.get(request)

        def finished():
            body = bytes(reply.read(FETCH_LIMIT + 1) or b"")
            error = reply.error() != reply.NetworkError.NoError
            kind = reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader)
            reply.deleteLater()
            if not job.alive:
                return
            if error or len(body) > FETCH_LIMIT:
                self._json(
                    job,
                    {"error": "The address could not be loaded (2 MB limit)"},
                    502,
                )
                return
            self._reply(job, (kind or "application/octet-stream").encode(), body)

        reply.finished.connect(finished)

    def _json(self, job, data, status=200):
        if status != 200:
            data = {**data, "status": status}
        self._reply(job, b"application/json", json.dumps(data).encode())

    @staticmethod
    def _reply(job, content_type, body):
        job.reply(content_type, body)
