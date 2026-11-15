"""Spell check in text fields, with Chromium's dictionaries.

Off until you choose a language (Settings → General → Spell check); then
Steed downloads that language's dictionary once from Chromium's dictionary
repository (chromium.googlesource.com, about 0.5–2 MB) into
~/.custom_browser/dictionaries/<language>.bdic, where Qt WebEngine reads it
(QTWEBENGINE_DICTIONARIES_PATH, set before the engine starts). Misspelled
words are underlined; right-click one for suggestions. Nothing you type
leaves the computer.
"""

import base64
import json
import re
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

REPOSITORY = (
    "https://chromium.googlesource.com/chromium/deps/hunspell_dictionaries/"
    "+/refs/heads/main/"
)
MAX_BYTES = 16 * 1024 * 1024
# Languages offered, by Chromium's dictionary name.
LANGUAGES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "en-CA": "English (Canada)",
    "en-AU": "English (Australia)",
    "fr-FR": "Français",
    "de-DE": "Deutsch",
    "es-ES": "Español",
    "it-IT": "Italiano",
    "pt-BR": "Português (Brasil)",
    "pt-PT": "Português (Portugal)",
    "nl-NL": "Nederlands",
    "sv-SE": "Svenska",
    "da-DK": "Dansk",
    "nb-NO": "Norsk bokmål",
    "pl-PL": "Polski",
    "cs-CZ": "Čeština",
    "ru-RU": "Русский",
    "uk-UA": "Українська",
    "tr-TR": "Türkçe",
    "el-GR": "Ελληνικά",
    "hu-HU": "Magyar",
    "ro-RO": "Română",
    "vi-VN": "Tiếng Việt",
    "id-ID": "Bahasa Indonesia",
}


def folder() -> Path:
    return Path.home() / ".custom_browser" / "dictionaries"


def installed(language: str, where: Path | None = None) -> bool:
    return ((where or folder()) / f"{language}.bdic").is_file()


def newest(names, language):
    """Chromium's file for a language, newest version: en-US-10-2.bdic."""
    pattern = re.compile(rf"^{re.escape(language)}-(\d+)-(\d+)\.bdic$")
    found = [
        (tuple(int(n) for n in match.groups()), name)
        for name in names
        if (match := pattern.match(name))
    ]
    return max(found)[1] if found else None


class Spellcheck(QObject):
    """Downloads dictionaries and switches spell check on for every profile."""

    ready = pyqtSignal(str)  # language, now usable
    failed = pyqtSignal(str)  # what went wrong, in words

    def __init__(self, parent=None, where: Path | None = None):
        super().__init__(parent)
        self.where = where or folder()
        self.network = None
        self.reply = None
        self.wanted = ""

    def apply(self, profiles, language):
        """Spell check in these profiles: on in language, or off ("")."""
        usable = bool(language) and installed(language, self.where)
        for profile in profiles:
            profile.setSpellCheckEnabled(usable)
            profile.setSpellCheckLanguages([language] if usable else [])
        return usable

    def fetch(self, language):
        """Download a language's dictionary (once); ready or failed follows."""
        if language not in LANGUAGES:
            self.failed.emit("Steed has no dictionary for that language.")
            return
        if installed(language, self.where):
            self.ready.emit(language)
            return
        self.wanted = language
        self._get(REPOSITORY + "?format=JSON", self._listed)

    def _get(self, address, then):
        if self.network is None:
            self.network = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(address))
        request.setTransferTimeout(30000)
        self.reply = reply = self.network.get(request)
        reply.finished.connect(lambda: then(reply))

    def _body(self, reply):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit("The dictionary could not be downloaded. Try again later.")
            return None
        data = bytes(reply.readAll())
        if len(data) > MAX_BYTES:
            self.failed.emit("The dictionary was larger than expected.")
            return None
        return data

    def _listed(self, reply):
        data = self._body(reply)
        if data is None:
            return
        try:
            # googlesource prefixes JSON with )]}' to stop it being run.
            listing = json.loads(data.split(b"\n", 1)[1])
            names = [entry["name"] for entry in listing["entries"]]
        except (ValueError, KeyError, IndexError, TypeError):
            self.failed.emit("The dictionary list could not be read.")
            return
        name = newest(names, self.wanted)
        if name is None:
            self.failed.emit("Steed has no dictionary for that language.")
            return
        self._get(REPOSITORY + name + "?format=TEXT", self._downloaded)

    def _downloaded(self, reply):
        data = self._body(reply)
        if data is None:
            return
        try:
            dictionary = base64.b64decode(data, validate=False)
        except ValueError:
            dictionary = b""
        if not dictionary.startswith(b"BDic"):
            self.failed.emit("The downloaded dictionary was not valid.")
            return
        self.where.mkdir(parents=True, exist_ok=True)
        target = self.where / f"{self.wanted}.bdic"
        temporary = target.with_suffix(".part")
        temporary.write_bytes(dictionary)
        temporary.replace(target)
        self.ready.emit(self.wanted)

    def close(self):
        if self.reply is not None:
            try:
                self.reply.abort()
            except RuntimeError:
                pass
