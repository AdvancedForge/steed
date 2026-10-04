"""Optional, bounded RSS/Atom reader with no browser cookies or credentials."""

import time
import xml.etree.ElementTree as ET

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

MAX_FEED_BYTES = 2 * 1024 * 1024


def feed_items(body):
    from services.start_pages import web_url

    if (
        len(body) > MAX_FEED_BYTES
        or b"<!DOCTYPE" in body.upper()
        or b"<!ENTITY" in body.upper()
    ):
        raise ValueError("Unsupported feed")
    root = ET.fromstring(body)
    if root.tag.rsplit("}", 1)[-1] not in ("rss", "feed", "RDF"):
        raise ValueError("Use an RSS or Atom feed")
    result = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        title, link = "", ""
        for child in entry:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "title":
                title = " ".join("".join(child.itertext()).split())[:200]
            elif tag == "link" and child.get("rel", "alternate") == "alternate":
                link = child.get("href") or child.text or ""
        try:
            link = web_url(link)
        except ValueError:
            continue
        if title and (title, link) not in result:
            result.append((title, link))
        if len(result) == 20:
            break
    return result


class NewsFeeds(QObject):
    changed = pyqtSignal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.cache = {}
        self.requests = {}
        self.scheduled = {}

    def snapshot(self, profile, url):
        if not url:
            return [], "Add an RSS or Atom feed in Customize to turn on news."
        cached = self.cache.get(profile)
        if not cached or cached[0] != url or time.monotonic() - cached[1] >= 600:
            if profile not in self.requests and self.scheduled.get(profile) != url:
                self.scheduled[profile] = url

                def start():
                    if self.scheduled.get(profile) == url:
                        self.scheduled.pop(profile, None)
                        self.fetch(profile, url)

                QTimer.singleShot(0, start)
        if cached and cached[0] == url:
            return cached[2], cached[3]
        return [], "Loading your feed…"

    def fetch(self, profile, url):
        if profile in self.requests:
            return
        request = QNetworkRequest(QUrl(url))
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader, "Steed feed reader"
        )
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.LoadControl.Manual,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
            QNetworkRequest.LoadControl.Manual,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.AuthenticationReuseAttribute,
            QNetworkRequest.LoadControl.Manual,
        )
        request.setTransferTimeout(10000)
        reply = self.network.get(request)
        body = bytearray()
        self.requests[profile] = reply

        def receive():
            body.extend(bytes(reply.read(max(1, MAX_FEED_BYTES + 1 - len(body)))))
            if len(body) > MAX_FEED_BYTES or reply.bytesAvailable():
                reply.abort()

        def finish():
            receive()
            if self.requests.get(profile) is reply:
                del self.requests[profile]
                try:
                    status = reply.attribute(
                        QNetworkRequest.Attribute.HttpStatusCodeAttribute
                    )
                    if (
                        reply.error() != QNetworkReply.NetworkError.NoError
                        or status != 200
                    ):
                        raise ValueError("Feed unavailable")
                    items = feed_items(bytes(body))
                    message = "No articles in this feed." if not items else ""
                except (ValueError, ET.ParseError):
                    items, message = (
                        [],
                        "Feed unavailable. Check the address or try Refresh.",
                    )
                self.cache[profile] = (url, time.monotonic(), items, message)
                self.changed.emit(profile)
            reply.deleteLater()

        reply.readyRead.connect(receive)
        reply.finished.connect(finish)

    def forget(self, profile):
        self.scheduled.pop(profile, None)
        reply = self.requests.pop(profile, None)
        self.cache.pop(profile, None)
        if reply is not None:
            reply.abort()
