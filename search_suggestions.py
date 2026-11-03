"""Search-engine suggestions for the address bar, behind a user setting.

Typed text (never addresses) is sent to the chosen search engine's public
suggestion endpoint. Requests carry no browser cookies or profile identity:
they use a plain network manager, not a web profile.
"""

import json
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

# host prefix -> suggestion endpoint answering in OpenSearch JSON:
# ["query", ["suggestion", ...]]
ENDPOINTS = {
    "duckduckgo": "https://duckduckgo.com/ac/?type=list&q={}",
    "google": "https://suggestqueries.google.com/complete/search?client=firefox&q={}",
    "bing": "https://api.bing.com/osjson.aspx?query={}",
    "ecosia": "https://ac.ecosia.org/autocomplete?type=list&q={}",
}
DEBOUNCE_MS = 150
TIMEOUT_MS = 2000
MAX_BYTES = 64 * 1024
MAX_RESULTS = 4
MAX_LENGTH = 200


def endpoint(search_url):
    """The suggestion URL template for a search engine, or None."""
    host = QUrl(search_url).host().lower().removeprefix("www.")
    for key, template in ENDPOINTS.items():
        if host == key + ".com" or host.startswith(key + "."):
            return template
    return None


def parse(raw, query):
    """Bounded, printable suggestions from an OpenSearch reply."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return []
    if not (isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list)):
        return []
    found = []
    for item in data[1]:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())[:MAX_LENGTH]
        if text and text.isprintable() and text.lower() != query.lower():
            if text not in found:
                found.append(text)
        if len(found) >= MAX_RESULTS:
            break
    return found


class SearchSuggestions(QObject):
    """Fetch suggestions for the latest text only; older replies are dropped."""

    ready = pyqtSignal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Its own empty cookie jar, separate from every browser profile; the
        # requests below also neither send nor store cookies.
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.query = ""
        self.template = None
        self.cache = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(DEBOUNCE_MS)
        self.timer.timeout.connect(self._fetch)

    def request(self, query, search_url):
        """Ask for suggestions for query soon; cached answers return at once."""
        query = query.strip()
        template = endpoint(search_url)
        self.timer.stop()
        self._abort()
        if not query or len(query) > MAX_LENGTH or template is None:
            self.query = ""
            return []
        self.query, self.template = query, template
        if (template, query) in self.cache:
            return self.cache[(template, query)]
        self.timer.start()
        return []

    def cancel(self):
        self.timer.stop()
        self._abort()
        self.query = ""

    def _abort(self):
        if self.reply is not None:
            reply, self.reply = self.reply, None
            reply.abort()
            reply.deleteLater()

    def _fetch(self):
        query, template = self.query, self.template
        if not query:
            return
        request = QNetworkRequest(QUrl(template.format(quote(query, safe=""))))
        request.setTransferTimeout(TIMEOUT_MS)
        request.setAttribute(
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
            QNetworkRequest.LoadControl.Manual,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.LoadControl.Manual,
        )
        reply = self.network.get(request)
        self.reply = reply
        reply.finished.connect(lambda: self._finished(reply, query, template))

    def _finished(self, reply, query, template):
        if reply is not self.reply:
            reply.deleteLater()
            return
        self.reply = None
        raw = bytes(reply.read(MAX_BYTES + 1))
        ok = reply.error() == reply.NetworkError.NoError and len(raw) <= MAX_BYTES
        reply.deleteLater()
        if not ok:
            return
        found = parse(raw, query)
        if len(self.cache) > 200:
            self.cache.clear()
        self.cache[(template, query)] = found
        if query == self.query:
            self.ready.emit(query, found)
