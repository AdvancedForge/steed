"""Immutable, bounded source snapshots; filesystem work never blocks Qt."""

import hashlib
import os
import stat
import tempfile
import threading
from concurrent.futures import Future
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from services.task_downloads import open_directory
from services.task_policy import TaskPermissionError


class UploadCopy(QObject):
    prepared = pyqtSignal(object)

    def __init__(self, source, grant, origin, parent=None, *, remaining_bytes=None):
        super().__init__(parent)
        grant.check("upload", source=source, url=origin)
        self.source, self.grant, self.origin = source, grant, origin
        self.byte_limit = grant.scope.max_upload_bytes
        if remaining_bytes is not None:
            self.byte_limit = min(self.byte_limit, max(0, remaining_bytes))
        self.ready = Future()
        self.cancelled = threading.Event()
        self.directory = tempfile.TemporaryDirectory(prefix="steed-upload-")
        self.path = Path(self.directory.name) / Path(source).name
        self.size = 0
        self.digest = ""
        self.prepared.connect(self.received, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=self.copy, daemon=True).start()

    def authority(self):
        if self.cancelled.is_set():
            raise TaskPermissionError("upload_cancelled")
        self.grant.check("upload", source=self.source, url=self.origin)

    def copy(self):
        descriptor = None
        try:
            self.authority()
            folder = open_directory(Path(self.source).parent)
            try:
                descriptor = os.open(
                    Path(self.source).name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                    dir_fd=folder,
                )
            finally:
                os.close(folder)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise TaskPermissionError("upload_source_not_regular")
            if before.st_size > self.byte_limit:
                raise TaskPermissionError("upload_too_large")
            digest = hashlib.sha256()
            size = 0
            with self.path.open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                while True:
                    self.authority()
                    data = os.read(descriptor, 65536)
                    if not data:
                        break
                    size += len(data)
                    if size > self.byte_limit:
                        raise TaskPermissionError("upload_too_large")
                    output.write(data)
                    digest.update(data)
                output.flush()
                os.fchmod(output.fileno(), 0o400)
            after = os.fstat(descriptor)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or size != before.st_size:
                raise TaskPermissionError("upload_source_changed")
            self.authority()
            reply = (size, digest.hexdigest())
        except Exception as error:
            reply = error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            self.prepared.emit(reply)
        except RuntimeError:
            pass  # The owning Qt session already disposed this copy.

    def received(self, reply):
        if self.ready.done():
            return
        if isinstance(reply, Exception):
            self.ready.set_exception(reply)
        else:
            try:
                self.authority()
            except Exception as error:
                self.ready.set_exception(error)
            else:
                self.size, self.digest = reply
                self.ready.set_result(self)

    def close(self):
        self.cancelled.set()
        if not self.ready.done():
            self.ready.set_exception(TaskPermissionError("upload_cancelled"))
        self.directory.cleanup()
