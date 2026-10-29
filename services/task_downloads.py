"""Bounded native transfers and atomic, non-overwriting destination publication."""

import os
import stat
import tempfile
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl

from services.task_policy import TaskPermissionError, download_destination


def open_directory(path):
    """Walk using directory descriptors; never follow a substituted symlink."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in Path(path).parts[1:]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


class DownloadDestination:
    """Keep Chromium writes away from the approved final name until completion."""

    def __init__(self, destination):
        self.path = Path(download_destination(destination))
        self.parent_fd = open_directory(self.path.parent)
        self.stage = None
        self.stage_fd = None
        try:
            try:
                os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise TaskPermissionError("destination_exists")
            self.stage = tempfile.TemporaryDirectory(
                prefix=".steed-download-", dir=f"/proc/self/fd/{self.parent_fd}"
            )
            self.stage_fd = os.open(
                self.stage.name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
        except BaseException:
            self.close()
            raise

    def validate(self):
        fd = open_directory(self.path.parent)
        try:
            a, b = os.fstat(fd), os.fstat(self.parent_fd)
            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                raise TaskPermissionError("destination_changed")
        finally:
            os.close(fd)

    @property
    def staging_directory(self):
        # Anchor writes even if a parent path is renamed or replaced mid-transfer.
        # Use the owning PID because Chromium may resolve this in another process.
        self.validate()
        return f"/proc/{os.getpid()}/fd/{self.stage_fd}"

    def publish(self, expected_bytes, maximum):
        self.validate()
        source = os.open(
            "payload", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.stage_fd
        )
        try:
            info = os.fstat(source)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size != expected_bytes
                or info.st_size > maximum
            ):
                raise TaskPermissionError("download_size_mismatch")
            os.fchmod(source, 0o600)
            os.fsync(source)
            # link() atomically fails if the final name appeared during transfer.
            # Never replace an existing file or follow the final-name symlink.
            os.link(
                "payload",
                self.path.name,
                src_dir_fd=self.stage_fd,
                dst_dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        finally:
            os.close(source)

    def close(self):
        stage, self.stage = self.stage, None
        self._release(stage, 5)

    def _release(self, stage, attempts):
        """Remove the staging folder, then its descriptors; never raise.

        A cancelled download can still be writing its payload, so removal may
        briefly fail with a non-empty folder. Retry while the descriptors that
        the folder's path depends on stay open.
        """
        if stage is not None:
            try:
                stage.cleanup()
            except OSError:
                if attempts > 0:
                    QTimer.singleShot(200, lambda: self._release(stage, attempts - 1))
                    return
        if self.stage_fd is not None:
            os.close(self.stage_fd)
            self.stage_fd = None
        if self.parent_fd is not None:
            os.close(self.parent_fd)
            self.parent_fd = None


@dataclass(frozen=True)
class DownloadSnapshot:
    download_id: str
    session_id: str
    destination: str
    state: str
    received_bytes: int
    total_bytes: int
    error: str = ""


class TaskDownload(QObject):
    """One explicit main-document attachment, owned by its engine session."""

    def __init__(self, session_id, download_id, page, grant, url, destination, publish):
        super().__init__(page)
        self.session_id, self.download_id = session_id, download_id
        self.page, self.grant, self.url = page, grant, url
        self.destination = destination
        self.before_publish = publish
        self.result = Future()
        self.request = None
        self._cancelling = False
        self.output = DownloadDestination(destination)
        self.state, self.error = "requested", ""
        self.received = self.total = 0
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.check)
        self.deadline_timer = QTimer(self)
        self.deadline_timer.setSingleShot(True)
        self.deadline_timer.timeout.connect(lambda: self.cancel("download_timeout"))
        self.timer.start()
        self.deadline_timer.start(30000)

    def snapshot(self):
        return DownloadSnapshot(
            self.download_id,
            self.session_id,
            self.destination,
            self.state,
            self.received,
            self.total,
            self.error,
        )

    def accept(self, request):
        if self.result.done() or self.request is not None:
            request.cancel()
            return
        try:
            self.check_authority()
            if (
                request.page() is not self.page
                or request.isSavePageDownload()
                or request.url() != QUrl(self.url)
            ):
                raise TaskPermissionError("download_request_mismatch")
            if request.totalBytes() > self.grant.scope.max_download_bytes:
                raise TaskPermissionError("download_too_large")
            request.setDownloadDirectory(self.output.staging_directory)
            request.setDownloadFileName("payload")
            self.request = request
            request.receivedBytesChanged.connect(self.check)
            request.totalBytesChanged.connect(self.check)
            request.stateChanged.connect(self.check)
            request.destroyed.connect(self.destroyed_request)
            self.state = "in_progress"
            request.accept()
            self.check()
        except Exception as error:
            request.cancel()
            self.cancel(getattr(error, "code", "download_failed"))

    def check_authority(self):
        self.grant.check("download", url=self.url, destination=self.destination)
        self.grant.check("navigate", url=self.url)
        self.output.validate()

    def check(self, *_):
        if self.result.done() or self._cancelling:
            return
        try:
            self.check_authority()
            if self.request is None:
                return
            self.received = max(0, self.request.receivedBytes())
            self.total = self.request.totalBytes()
            if max(self.received, self.total) > self.grant.scope.max_download_bytes:
                raise TaskPermissionError("download_too_large")
            state = self.request.state().name
            if state == "DownloadCompleted":
                self.before_publish()
                self.check_authority()
                self.output.publish(self.received, self.grant.scope.max_download_bytes)
                self.finish("completed")
            elif state in ("DownloadInterrupted", "DownloadCancelled"):
                self.finish("failed", "download_interrupted")
        except Exception as error:
            self.cancel(getattr(error, "code", "download_failed"))

    def cancel(self, reason="cancelled"):
        if self.result.done() or self._cancelling:
            return
        self._cancelling = True
        try:
            if self.request is not None and not sip.isdeleted(self.request):
                self.request.cancel()
            self.finish("cancelled", reason)
        finally:
            self._cancelling = False

    def finish(self, state, error=""):
        if self.result.done():
            return
        self.state, self.error = state, error
        self.timer.stop()
        self.deadline_timer.stop()
        self.output.close()
        self.result.set_result(self.snapshot())

    def destroyed_request(self):
        self.request = None
        if not self.result.done():
            self.cancel("download_request_destroyed")

    def close(self):
        self.cancel("session_closed")
        if self.request is not None and not sip.isdeleted(self.request):
            self.request.deleteLater()
