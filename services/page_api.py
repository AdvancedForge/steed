"""steed://newtab/api/… — data for custom New Tab and Welcome pages.

A custom start page (its own HTML and JavaScript) can ask Steed for web data,
open tabs, history and bookmarks with fetch("steed://newtab/api/<name>").
Each capability follows the user's local-page access setting: never, ask
(a prompt the first time in a session) or always. Only the start page itself
may call these addresses; websites cannot reach the steed: scheme.
"""

import json
from urllib.parse import parse_qs

from PyQt6 import sip
from PyQt6.QtCore import QBuffer, QIODevice, QObject, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestJob

import access_levels

ENDPOINTS = {
    "fetch": "web",
    "tabs": "tabs",
    "history": "history",
    "bookmarks": "bookmarks",
}
FETCH_LIMIT = 2 * 1024 * 1024
FETCH_TIMEOUT_MS = 15000
HISTORY_LIMIT = 200


def api_endpoint(url):
    """The endpoint name for steed://newtab/api/<name>, or None."""
    if (
        url.scheme() == "steed"
        and url.host() in ("newtab", "welcome")
        and url.path().startswith("/api/")
        and not url.userInfo()
        and url.port() == -1
    ):
        name = url.path().removeprefix("/api/")
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
        url, initiator = job.requestUrl(), job.initiator()
        # Only the start page on the same steed: host may ask.
        if (
            bytes(job.requestMethod()) != b"GET"
            or initiator.scheme() != "steed"
            or initiator.host() != url.host()
            or not self._custom_page(url.host())
        ):
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        capability = ENDPOINTS[name]
        policy = access_levels.values(self.config, "pages")[capability]
        if policy == "always" or self.session.get(capability) is True:
            self._serve(job, name, url)
        elif policy == "never" or self.session.get(capability) is False:
            self._json(job, {"error": "denied", "capability": capability}, 403)
        else:
            self._ask(job, name, url, capability)

    def _custom_page(self, host):
        state = self.pages.state(self.profile_name)
        return bool(state.get("html" if host == "newtab" else "welcome_html"))

    def _ask(self, job, name, url, capability):
        waiting = self.prompts.setdefault(capability, [])
        waiting.append((job, name, url))
        if len(waiting) > 1:
            return  # One question per capability; later requests share it.
        from PyQt6.QtWidgets import QApplication, QMessageBox

        spec = access_levels.PAGE_CAPABILITIES[capability]
        box = QMessageBox(QApplication.activeWindow())
        box.setWindowTitle("Allow your New Tab page?")
        box.setText(
            f"Your custom New Tab page asks to: {spec['label'].lower()}.\n\n"
            f"{spec['help']}"
        )
        once = box.addButton("Allow this session", QMessageBox.ButtonRole.AcceptRole)
        always = box.addButton("Always allow", QMessageBox.ButtonRole.YesRole)
        box.addButton("Don't allow", QMessageBox.ButtonRole.RejectRole)

        def answered():
            clicked = box.clickedButton()
            allowed = clicked in (once, always)
            if clicked is always:
                access_levels.set_policy(self.config, "pages", capability, "always")
            self.session[capability] = allowed
            for pending, pending_name, pending_url in self.prompts.pop(capability, []):
                if sip.isdeleted(pending):
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
            body = bytes(reply.read(FETCH_LIMIT + 1))
            error = reply.error() != reply.NetworkError.NoError
            kind = reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader)
            reply.deleteLater()
            if sip.isdeleted(job):
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
        buffer = QBuffer(job)
        buffer.setData(body)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(content_type, buffer)
