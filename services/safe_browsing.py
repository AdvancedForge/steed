"""Protection against phishing and malware sites.

Steed keeps two public lists of dangerous sites from the malware-filter
project (https://gitlab.com/malware-filter): phishing pages (from OpenPhish,
PhishTank and others) and sites serving malware (abuse.ch URLhaus, online
only). They are downloaded whole, about twice a day, so checking a page sends
nothing about your browsing anywhere. A page on a list is stopped with a
warning (you can still go on, for this run); a download from such a site is
refused. Settings → General → "Warn about dangerous sites" turns it off.
"""

import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

BASE = "https://malware-filter.gitlab.io/malware-filter/"
LISTS = {
    "phishing": BASE + "phishing-filter-domains.txt",
    "malware": BASE + "urlhaus-filter-domains-online.txt",
}
REFRESH_SECONDS = 12 * 3600
FIRST_UPDATE_MS = 20000
MAX_BYTES = 16 * 1024 * 1024
REASONS = {
    "phishing": "a phishing site: it imitates another site to take passwords, "
    "payment details or other personal information",
    "malware": "a site that has recently served harmful software",
}


def folder() -> Path:
    from browser.config import data_dir

    return data_dir() / "safe_browsing"


def parse(text: str) -> set[str]:
    hosts = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if line and not line.startswith(("#", "!")) and " " not in line:
            hosts.add(line.removeprefix("www."))
    return hosts


class SafeBrowsing(QObject):
    changed = pyqtSignal()

    def __init__(self, config, parent=None, where: Path | None = None):
        super().__init__(parent)
        self.config = config
        self.where = where or folder()
        self.hosts = {kind: set() for kind in LISTS}
        self.allowed = set()  # Hosts you chose to visit anyway, this run.
        self.network = None
        self.replies = []
        for kind in LISTS:
            try:
                self.hosts[kind] = parse((self.where / f"{kind}.txt").read_text())
            except OSError:
                pass
        self.timer = QTimer(self)
        self.timer.setInterval(3600 * 1000)
        self.timer.timeout.connect(self.update_if_due)
        self.timer.start()
        QTimer.singleShot(FIRST_UPDATE_MS, self.update_if_due)

    @property
    def enabled(self):
        return bool(self.config.get("safe_browsing", True))

    def check(self, url: QUrl):
        """ "phishing", "malware" or None for an address."""
        if not self.enabled or url.scheme() not in ("http", "https", "ws", "wss"):
            return None
        host = url.host().lower().removeprefix("www.")
        if not host or host in self.allowed:
            return None
        labels = host.split(".")
        parts = {".".join(labels[i:]) for i in range(len(labels) - 1)}
        for kind, listed in self.hosts.items():
            if parts & listed:
                return kind
        return None

    def allow(self, url: QUrl):
        self.allowed.add(url.host().lower().removeprefix("www."))

    # -- the lists

    def age(self):
        """Seconds since the lists were last downloaded (None: never)."""
        try:
            return time.time() - min(
                (self.where / f"{kind}.txt").stat().st_mtime for kind in LISTS
            )
        except OSError:
            return None

    def update_if_due(self):
        age = self.age()
        if self.enabled and (age is None or age > REFRESH_SECONDS):
            self.update()

    def update(self):
        if self.network is None:
            self.network = QNetworkAccessManager(self)
        for kind, address in LISTS.items():
            request = QNetworkRequest(QUrl(address))
            request.setTransferTimeout(60000)
            reply = self.network.get(request)
            self.replies.append(reply)
            reply.finished.connect(lambda r=reply, k=kind: self._got(k, r))

    def _got(self, kind, reply):
        if reply in self.replies:
            self.replies.remove(reply)
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            return
        data = bytes(reply.readAll())
        if len(data) > MAX_BYTES:
            return
        text = data.decode("utf-8", "replace")
        hosts = parse(text)
        if len(hosts) < 10:  # An error page, not a list.
            return
        self.hosts[kind] = hosts
        self.where.mkdir(parents=True, exist_ok=True)
        target = self.where / f"{kind}.txt"
        temporary = target.with_suffix(".part")
        temporary.write_text(text)
        temporary.replace(target)
        self.changed.emit()

    def close(self):
        self.timer.stop()
        for reply in list(self.replies):
            try:
                reply.abort()
            except RuntimeError:
                pass


def warning_page(url: QUrl, kind: str, token: str) -> str:
    """The page shown instead of a listed site."""
    from html import escape

    host = escape(url.host())
    target = escape(url.toString(QUrl.ComponentFormattingOption.FullyEncoded))
    proceed = escape(
        "steed://safety/proceed?"
        + "token="
        + token
        + "&url="
        + QUrl.toPercentEncoding(url.toString()).data().decode()
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Dangerous site</title><style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
  font: 16px/1.55 "Mona Sans", system-ui, sans-serif; background: #3a0d12; color: #fff; }}
main {{ width: min(620px, 100% - 48px); }}
h1 {{ font-size: 32px; line-height: 1.15; margin: 0 0 14px; letter-spacing: -.01em; }}
p {{ margin: 0 0 14px; color: #ffe1e3; }}
code {{ word-break: break-all; color: #fff; }}
.actions {{ display: flex; gap: 14px; align-items: center; margin-top: 26px; flex-wrap: wrap; }}
a.back {{ background: #fff; color: #3a0d12; padding: 11px 22px; border-radius: 999px;
  text-decoration: none; font-weight: 650; }}
a.anyway {{ color: #ffc7cb; font-size: 14px; }}
</style></head><body><main>
<h1>Steed stopped this page</h1>
<p><strong>{host}</strong> is listed as {escape(REASONS[kind])}.</p>
<p>Nothing from it has loaded. If you got here from an email or message,
it was probably not from who it says.</p>
<p><code>{target}</code></p>
<div class="actions"><a class="back" href="steed://safety/back">Go back</a>
<a class="anyway" href="{proceed}">Visit this site anyway</a></div>
</main></body></html>"""
