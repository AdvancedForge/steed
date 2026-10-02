"""Queue named internal commands onto the Qt application thread."""

import threading
from concurrent.futures import Future, InvalidStateError
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
        return self._enqueue(command, arguments, None)

    def submit_guarded(self, command: str, guard, **arguments) -> Future:
        """Trusted callback checked at dispatch; never exposed across external IPC."""
        if not callable(guard):
            raise ValueError("Provide a trusted dispatch guard")
        return self._enqueue(command, arguments, guard)

    def _enqueue(self, command, arguments, guard):
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
            self._submitted.emit((command, arguments, future, guard))
        return future

    @pyqtSlot(object)
    def _dispatch(self, request):
        require_owner_thread(self)
        command, arguments, future, guard = request
        with self._lock:
            closed = self._closed
        if closed:
            future.cancel()
            return
        if not future.set_running_or_notify_cancel():
            with self._lock:
                self._pending.discard(future)
            return
        try:
            if guard is not None:
                guard()
            if command not in self._handlers:
                raise ValueError("Unsupported internal command")
            result = self._handlers[command](**arguments)
        except Exception as error:
            self._complete(future, error=error)
        else:
            if isinstance(result, Future):
                result.add_done_callback(lambda source: self._resolved(future, source))
            else:
                self._complete(future, result=result)

    def _resolved(self, future, source):
        try:
            result = source.result()
        except Exception as error:
            self._complete(future, error=error)
        else:
            self._complete(future, result=result)

    def _complete(self, future, *, result=None, error=None):
        with self._lock:
            self._pending.discard(future)
        if future.done():
            return
        try:
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)
        except InvalidStateError:
            pass  # A concurrent completion or shutdown already settled it.

    def close(self):
        require_owner_thread(self)
        with self._lock:
            self._closed = True
            pending = list(self._pending)
            self._pending.clear()
        for future in pending:
            if not future.cancel() and not future.done():
                self._complete(future, error=RuntimeError("Command broker is closed"))
