"""Per-site settings: zoom, JavaScript, autoplay and pop-ups, remembered per host.

Each profile keeps its own <profile>/site_settings.json; the incognito profile
keeps them in memory only. A setting equal to the default is not stored, so the
file lists only what you changed.
"""

import json
import math
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal

from utils import save_json

CHOICES = {
    "javascript": ("allow", "block"),
    "autoplay": ("allow", "block"),
    "popups": ("allow", "block"),
}
DEFAULTS = {"zoom": 1.0, "javascript": "allow", "autoplay": "allow", "popups": "allow"}
LABELS = {
    "javascript": "JavaScript",
    "autoplay": "Autoplay with sound",
    "popups": "Pop-ups and new windows",
}
MAX_SITES = 2000
MAX_BYTES = 1 << 20


def site_key(url) -> str | None:
    """The host per-site settings belong to; None for non-web pages."""
    url = QUrl(url) if isinstance(url, str) else url
    if url.scheme() not in ("http", "https") or not url.host():
        return None
    return url.host().lower()


def _valid(key, value) -> bool:
    if key == "zoom":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0.25 <= value <= 5.0
        )
    return key in CHOICES and value in CHOICES[key]


class SiteSettings(QObject):
    changed = pyqtSignal(str)  # host

    def __init__(self, path: Path | None, parent=None):
        super().__init__(parent)
        self.path = Path(path) if path is not None else None
        self.sites: dict[str, dict] = {}
        if self.path is not None:
            self.sites = self._load()

    def _load(self) -> dict:
        try:
            if self.path.stat().st_size > MAX_BYTES:
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        sites = data.get("sites") if isinstance(data, dict) else None
        if data.get("version") != 1 or not isinstance(sites, dict):
            return {}
        result = {}
        for host, values in list(sites.items())[:MAX_SITES]:
            if not isinstance(host, str) or not isinstance(values, dict):
                continue
            kept = {k: v for k, v in values.items() if _valid(k, v)}
            if kept:
                result[host] = kept
        return result

    def get(self, host: str | None) -> dict:
        return {**DEFAULTS, **self.sites.get(host or "", {})}

    def changed_keys(self, host: str | None) -> dict:
        return dict(self.sites.get(host or "", {}))

    def set(self, host: str | None, key: str, value) -> None:
        if not host:
            raise ValueError("Site settings apply to websites only.")
        if not _valid(key, value):
            raise ValueError(f"Not a valid value for {key}.")
        values = dict(self.sites.get(host, {}))
        if key == "zoom":
            value = round(float(value), 3)
        if value == DEFAULTS[key]:
            values.pop(key, None)
        else:
            values[key] = value
        if values == self.sites.get(host, {}):
            return
        if values:
            if host not in self.sites and len(self.sites) >= MAX_SITES:
                raise ValueError("Too many sites with their own settings.")
            self.sites[host] = values
        else:
            self.sites.pop(host, None)
        self._save()
        self.changed.emit(host)

    def clear(self, host: str | None) -> None:
        if host and self.sites.pop(host, None) is not None:
            self._save()
            self.changed.emit(host)

    def _save(self):
        if self.path is not None:
            save_json(self.path, {"version": 1, "sites": self.sites})
