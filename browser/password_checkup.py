"""Password Checkup: reused, weak and breached passwords in the vault.

Reused and weak are found on this computer. Breaches use Have I Been Pwned's
range search: only the first 5 characters of each password's SHA-1 hash are
sent (with padding, so the answer's size says nothing either), and the
matching is done here. Nothing else leaves the computer, and only when asked.
"""

import hashlib
import re
from collections import defaultdict

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"
COMMON = {
    "password", "123456", "12345678", "123456789", "qwerty", "abc123", "111111",
    "letmein", "iloveyou", "admin", "welcome", "monkey", "dragon", "football",
    "passw0rd", "qwerty123", "1q2w3e4r", "sunshine", "princess", "000000",
}  # fmt: skip


def weak(password: str) -> bool:
    """Short, common, or made of one kind of character or a run."""
    lowered = password.lower()
    if len(password) < 8 or lowered in COMMON:
        return True
    if len(set(password)) <= 2:
        return True
    if re.fullmatch(r"\d+", password) and len(password) < 12:
        return True
    runs = "0123456789abcdefghijklmnopqrstuvwxyz"
    return len(password) < 12 and (lowered in runs or lowered in runs[::-1])


def reused(logins) -> dict[str, int]:
    """Login id -> how many saved logins share its password."""
    groups = defaultdict(list)
    for login in logins:
        groups[login.password].append(login.id)
    return {i: len(ids) for ids in groups.values() if len(ids) > 1 for i in ids}


def local_findings(logins) -> dict:
    return {
        "weak": [login.id for login in logins if weak(login.password)],
        "reused": reused(logins),
    }


def sha1(password: str) -> str:
    return hashlib.sha1(password.encode("utf-8")).hexdigest().upper()


def counts_in(body: str) -> dict[str, int]:
    """suffix -> times seen, from a range answer ("SUFFIX:COUNT" lines)."""
    found = {}
    for line in body.splitlines():
        suffix, _, count = line.strip().partition(":")
        if count.isdigit() and int(count) > 0:
            found[suffix.upper()] = int(count)
    return found


class BreachCheck(QObject):
    """Ask the range search about each password; finished(results, error)."""

    finished = pyqtSignal(dict, str)  # login id -> times seen; error text

    def __init__(self, logins, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.hashes = defaultdict(list)  # prefix -> [(suffix, login id)]
        for login in logins:
            digest = sha1(login.password)
            self.hashes[digest[:5]].append((digest[5:], login.id))
        self.waiting = set(self.hashes)
        self.results = {}
        self.error = ""

    def start(self):
        if not self.waiting:
            self.finished.emit({}, "")
            return
        for prefix in list(self.hashes):
            request = QNetworkRequest(QUrl(RANGE_URL.format(prefix=prefix)))
            request.setRawHeader(b"Add-Padding", b"true")
            request.setRawHeader(b"User-Agent", b"Steed-Password-Checkup")
            reply = self.network.get(request)
            reply.finished.connect(lambda r=reply, p=prefix: self._answered(p, r))

    def _answered(self, prefix, reply):
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.error = "Could not reach Have I Been Pwned: " + reply.errorString()
        else:
            # A failed reply can return None (see search_suggestions).
            counts = counts_in(bytes(reply.readAll() or b"").decode("ascii", "replace"))
            for suffix, login_id in self.hashes[prefix]:
                if suffix in counts:
                    self.results[login_id] = counts[suffix]
        reply.deleteLater()
        self.waiting.discard(prefix)
        if not self.waiting:
            self.finished.emit(self.results, self.error)
