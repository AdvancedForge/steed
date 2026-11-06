"""Shared download lifetime and bounded persistent history."""

import logging
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


RETRYABLE = ("DownloadInterrupted", "Interrupted", "DownloadCancelled")


class DownloadManager(QObject):
    changed = pyqtSignal()
    finished = pyqtSignal(str)  # download id, when one completes

    def __init__(self, config, parent=None, *, destination_picker=None):
        super().__init__(parent)
        self.destination_picker = destination_picker
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
        retry = self.retries.pop((profile, request.url().toString()), None)
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
            key, Path(path).name, path, profile, url=request.url().toString()
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
