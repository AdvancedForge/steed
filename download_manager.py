"""Shared download lifetime and bounded persistent history."""

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QUrl, pyqtSignal

import webengine
from utils import load_json, save_json

logger = logging.getLogger(__name__)


@dataclass
class DownloadRecord:
    download_id: str
    name: str
    path: str
    profile: str
    state: str = "DownloadInProgress"
    received: int = 0
    total: int = 0
    error: str = ""
    paused: bool = False
    # Where it came from, so a failed download can be tried again.
    url: str = ""
    # When it started (0: before Steed kept times).
    started: float = 0.0


RETRYABLE = ("DownloadInterrupted", "Interrupted", "DownloadCancelled")


# Files that run programs (or install them) when opened.
PROGRAM_TYPES = {
    ".appimage",
    ".run",
    ".sh",
    ".bash",
    ".bin",
    ".deb",
    ".rpm",
    ".flatpakref",
    ".snap",
    ".desktop",
    ".jar",
    ".py",
    ".pl",
    ".exe",
    ".msi",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".scr",
    ".apk",
    ".dmg",
    ".pkg",
    ".command",
}


def runs_programs(name: str) -> bool:
    return Path(name).suffix.lower() in PROGRAM_TYPES


def confirm_program(name, parent) -> bool:
    from PyQt6.QtWidgets import QMessageBox

    answer = QMessageBox.warning(
        parent,
        "Download a program?",
        f"“{name}” can run programs on your computer. Only keep it if you "
        "trust where it came from.",
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    return answer == QMessageBox.StandardButton.Save


class DownloadManager(QObject):
    changed = pyqtSignal()
    finished = pyqtSignal(str)  # download id, when one completes
    blocked = pyqtSignal(str, str)  # file name, why (a listed dangerous site)

    def __init__(self, config, parent=None, *, destination_picker=None):
        super().__init__(parent)
        self.destination_picker = destination_picker
        # url -> "phishing"/"malware"/None (services.safe_browsing), and
        # (file name, parent) -> True to keep a file that can run programs.
        self.checker = None
        self.confirm_program = None
        self.history_file = config.config_dir / "downloads.json"
        self.records = {}
        self.requests = {}
        self.private = set()
        # Download address -> destination for a retry, so it is not asked again.
        self.retries = {}
        raw = load_json(self.history_file, [])
        for item in raw[-200:] if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                record = DownloadRecord(**item)
            except TypeError:
                continue
            if record.state in ("DownloadRequested", "DownloadInProgress"):
                record.state = "Interrupted"
                record.error = "Browser closed before this download completed."
                record.paused = False
            self.records[record.download_id] = record

    def attach(self, profile, name):
        profile.downloadRequested.connect(
            lambda request, n=name, p=profile: self._profile_download(request, n, p)
        )

    def _profile_download(self, request, name, profile):
        # Gate before the ordinary handler can open a destination dialog.
        if getattr(request.page(), "is_task_page", False) is True:
            handler = getattr(request.page(), "task_download_handler", None)
            if handler is None:
                request.cancel()
            else:
                handler(request)
            return
        self.handle(request, name)

    def handle(self, request, profile):
        view = webengine.view_for(request.page())
        parent = view.window() if view else None
        suggested = Path(request.suggestedFileName()).name or "download"
        danger = self.checker(request.url()) if self.checker is not None else None
        program = runs_programs(suggested) and request.isSavePageDownload() is not True
        if danger or (
            program
            and self.confirm_program is not None
            and not self.confirm_program(suggested, parent)
        ):
            request.cancel()
            request.deleteLater()
            if danger:
                self.blocked.emit(suggested, danger)
            self.changed.emit()
            return
        retry = self.retries.pop((profile, request.url().toString()), None)
        if request.isSavePageDownload() is True:
            # View → Save Page As… already chose where.
            retry = str(Path(request.downloadDirectory()) / request.downloadFileName())
        try:
            path = (
                retry
                if retry is not None
                else self.destination_picker(suggested, parent)
                if self.destination_picker is not None
                else ""
            )
        except Exception:
            logger.exception("Could not ask where to save %s", suggested)
            path = ""
        if not path:
            # An abandoned prompt must cancel the request explicitly, otherwise
            # the engine keeps the connection open with nowhere to write.
            request.cancel()
            request.deleteLater()
            self.changed.emit()
            return
        path = str(Path(path).absolute())
        request.setDownloadDirectory(str(Path(path).parent))
        request.setDownloadFileName(Path(path).name)
        key = uuid4().hex
        record = DownloadRecord(
            key,
            Path(path).name,
            path,
            profile,
            url=request.url().toString(),
            started=time.time(),
        )
        self.records[key] = record
        self.requests[key] = request
        if profile == "search":
            self.private.add(key)
        for signal in (
            request.receivedBytesChanged,
            request.totalBytesChanged,
            request.stateChanged,
            request.isPausedChanged,
        ):
            signal.connect(lambda *args, k=key: self.refresh(k))
        # Drop the record before accept(): the engine may destroy the request
        # synchronously on accept, and that teardown must not race refresh().
        request.destroyed.connect(lambda: self.request_destroyed(key))
        request.accept()
        if key in self.requests:
            self.refresh(key)
        self.save()

    def refresh(self, key):
        request = self.requests.get(key)
        record = self.records.get(key)
        if request is None or record is None:
            return
        previous = record.state
        record.received = request.receivedBytes()
        record.total = request.totalBytes()
        record.state = request.state().name
        record.paused = request.isPaused()
        record.error = (
            request.interruptReasonString()
            if record.state == "DownloadInterrupted"
            else ""
        )
        self.changed.emit()
        if previous != record.state:
            self.save()
            if record.state == "DownloadCompleted":
                self.finished.emit(key)

    def request_destroyed(self, key):
        self.requests.pop(key, None)
        if key in self.private:
            self.private.discard(key)
            self.records.pop(key, None)
        elif key in self.records and self.records[key].state in (
            "DownloadRequested",
            "DownloadInProgress",
        ):
            self.records[key].state = "Interrupted"
            self.records[
                key
            ].error = "Download request was destroyed before completion."
            self.records[key].paused = False
            self.save()
        self.changed.emit()

    def active(self):
        return [
            key
            for key, record in self.records.items()
            if key in self.requests
            and record.state in ("DownloadRequested", "DownloadInProgress")
        ]

    def control(self, key, action):
        request = self.requests.get(key)
        if request is None or key not in self.active():
            return
        if action == "cancel":
            request.cancel()
        elif action == "pause":
            request.pause()
        elif action == "resume":
            request.resume()

    def can_retry(self, key):
        record = self.records.get(key)
        return (
            record is not None
            and key not in self.active()
            and record.state in RETRYABLE
            and QUrl(record.url).scheme() in ("http", "https")
        )

    def retry(self, key, page):
        """Download it again from the start, to the same file, without asking.

        Qt cannot continue a failed download where it stopped; it starts over.
        page must belong to the record's profile.
        """
        if not self.can_retry(key):
            raise ValueError("This download cannot be tried again.")
        record = self.records.pop(key)
        # Chromium keeps a failed download for its address and file; release it
        # first, or the new download collides with it and fails at once.
        old = self.requests.pop(key, None)
        if old is not None:
            old.cancel()
        self.save()
        self.retries[(record.profile, record.url)] = record.path
        page.download(QUrl(record.url), record.name)
        self.changed.emit()

    def progress(self):
        """(active downloads, overall fraction or None when sizes are unknown)."""
        active = [self.records[key] for key in self.active()]
        totals = [r.total for r in active]
        if not active or not all(total > 0 for total in totals):
            return len(active), None
        return len(active), sum(r.received for r in active) / sum(totals)

    def cancel_active(self):
        for key in self.active():
            self.control(key, "cancel")

    def forget_since(self, since: float, profile=None) -> int:
        """Drop finished downloads (not their files) started since; how many."""
        gone = [
            key
            for key, record in self.records.items()
            if key not in self.requests
            and (profile is None or record.profile == profile)
            and (since <= 0 or record.started >= since)
        ]
        for key in gone:
            self.records.pop(key)
        if gone:
            self.save()
            self.changed.emit()
        return len(gone)

    def save(self):
        records = [
            asdict(record)
            for key, record in self.records.items()
            if key not in self.private
        ]
        save_json(self.history_file, records[-200:])

    @staticmethod
    def file_url(record, reveal=False):
        path = Path(record.path)
        return QUrl.fromLocalFile(str(path.parent if reveal else path))
