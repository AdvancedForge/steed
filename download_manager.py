"""Shared download lifetime and bounded persistent history."""

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFileDialog

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


class DownloadManager(QObject):
    changed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.history_file = config.config_dir / "downloads.json"
        self.records = {}
        self.requests = {}
        self.private = set()
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
            lambda request, n=name: self.handle(request, n)
        )

    def handle(self, request, profile):
        view = QWebEngineView.forPage(request.page()) if request.page() else None
        parent = view.window() if view else None
        suggested = Path(request.suggestedFileName()).name or "download"
        try:
            path, _ = QFileDialog.getSaveFileName(parent, "Save Download", suggested)
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
        record = DownloadRecord(key, Path(path).name, path, profile)
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
        if request is None:
            return
        record = self.records[key]
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
