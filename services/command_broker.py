"""Queue named internal commands onto the Qt application thread."""

import threading
from concurrent.futures import Future
from copy import deepcopy

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot


def require_owner_thread(owner: QObject):
    if QThread.currentThread() != owner.thread():
        raise RuntimeError("Browser services must run on the owning Qt thread")


class CommandBroker(QObject):
    """Only registered commands can run; cancelled/closed queued work is discarded."""

    _submitted = pyqtSignal(object)

    def __init__(self, handlers: dict, parent: QObject):
        super().__init__(parent)
        self._handlers = dict(handlers)
        self._lock = threading.Lock()
        self._closed = False
        self._pending = set()
        self._submitted.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def submit(self, command: str, **arguments) -> Future:
        """Thread-safe entry point. Never wait on its Future from the Qt thread."""
        future = Future()
        # Callers cannot mutate a queued command's arguments after submission.
        try:
            arguments = deepcopy(arguments)
        except Exception as error:
            future.set_exception(error)
            return future
        with self._lock:
            if self._closed:
                future.set_exception(RuntimeError("Command broker is closed"))
                return future
            self._pending.add(future)
            self._submitted.emit((command, arguments, future))
        return future

    @pyqtSlot(object)
    def _dispatch(self, request):
        require_owner_thread(self)
        command, arguments, future = request
        with self._lock:
            self._pending.discard(future)
            closed = self._closed
        if closed:
            future.cancel()
            return
        if not future.set_running_or_notify_cancel():
            return
        try:
            if command not in self._handlers:
                raise ValueError("Unsupported internal command")
            result = self._handlers[command](**arguments)
        except Exception as error:
            future.set_exception(error)
        else:
            future.set_result(result)

    def close(self):
        require_owner_thread(self)
        with self._lock:
            self._closed = True
            pending = list(self._pending)
            self._pending.clear()
        for future in pending:
            future.cancel()
