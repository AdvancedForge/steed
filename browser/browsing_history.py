"""Bounded, profile-local browsing history for address suggestions."""

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from browser.utils import load_json, save_json

logger = logging.getLogger(__name__)
HISTORY_LIMIT = 10000  # Months of browsing; one entry per page.


@dataclass(frozen=True)
class HistoryEntry:
    url: str
    title: str
    # When it was last visited (0: before Steed kept times).
    time: float = 0.0


class BrowsingHistory(QObject):
    """Shared by windows using the same profile; private history has no path."""

    changed = pyqtSignal()

    def __init__(self, path: Path | None, parent=None, limit=HISTORY_LIMIT):
        super().__init__(parent)
        self.path = path
        self.limit = limit
        self._entries: dict[str, HistoryEntry] = {}
        self._dirty = False
        self._closed = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(750)
        self._save_timer.timeout.connect(self.flush)
        from services.page_memory import PageMemory

        # What pages said (services.page_memory), forgotten with the history.
        self.memory = PageMemory(
            path.with_name("read_pages.sqlite") if path is not None else None
        )
        raw = load_json(path, []) if path is not None else []
        if isinstance(raw, list):
            for item in reversed(raw[:limit]):
                if not isinstance(item, dict):
                    continue
                entry = self._entry(
                    item.get("url"), item.get("title", ""), item.get("time", 0.0)
                )
                if entry is not None:
                    self._entries[entry.url] = entry

    @staticmethod
    def _entry(url_text, title, when=0.0) -> HistoryEntry | None:
        if not isinstance(url_text, str):
            return None
        url = QUrl(url_text)
        if (
            not url.isValid()
            or url.scheme() not in ("http", "https")
            or not url.host()
            or url.userName()
            or url.password()
        ):
            return None
        title = " ".join(title.split())[:200] if isinstance(title, str) else ""
        when = float(when) if isinstance(when, (int, float)) else 0.0
        return HistoryEntry(url.toString(), title or url.host(), when)

    def entries(self) -> list[HistoryEntry]:
        return list(reversed(self._entries.values()))

    def record(self, url: str, title: str = "") -> None:
        if self._closed:
            return
        entry = self._entry(url, title, time.time())
        if entry is None:
            return
        if self._entries and next(reversed(self._entries)) == entry.url:
            last = self._entries[entry.url]
            if (last.url, last.title) == (entry.url, entry.title):
                return
        self._entries.pop(entry.url, None)
        self._entries[entry.url] = entry
        while len(self._entries) > self.limit:
            del self._entries[next(iter(self._entries))]
        self._changed()

    def clear(self) -> None:
        if self._closed:
            return
        self._entries.clear()
        self.memory.forget(0)
        self._changed()
        self.flush()

    def remove(self, url: str) -> bool:
        """Forget one page (and any text remembered from it)."""
        if self._closed or url not in self._entries:
            return False
        del self._entries[url]
        forget = getattr(self.memory, "forget_url", None)
        if forget is not None:
            forget(url)
        self._changed()
        self.flush()
        return True

    def remove_since(self, since: float) -> list[str]:
        """Forget pages visited at or after since (0: everything); their URLs."""
        if self._closed:
            return []
        self.memory.forget(since)
        gone = [u for u, e in self._entries.items() if since <= 0 or e.time >= since]
        for url in gone:
            del self._entries[url]
        if gone:
            self._changed()
            self.flush()
        return gone

    def _changed(self) -> None:
        if self.path is not None:
            self._dirty = True
            self._save_timer.start()
        self.changed.emit()

    def flush(self) -> None:
        self._save_timer.stop()
        if self.path is None or not self._dirty:
            return
        try:
            save_json(self.path, [asdict(entry) for entry in self.entries()])
        except OSError:
            logger.warning("Could not save browsing history to %s", self.path)
        else:
            self._dirty = False

    def close(self) -> None:
        self.flush()
        self.memory.close()
        self._closed = True
