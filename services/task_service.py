"""Internal task/session commands; there is no external agent endpoint yet."""

from dataclasses import asdict
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QObject, QThread, pyqtSignal

from services.browser_backend import BrowserBackend, QtBrowserBackend, navigation_url
from services.command_broker import CommandBroker, require_owner_thread
from services.task_store import ACTIVE_STATUSES, TERMINAL_STATUSES, TaskStore


class TaskService(QObject):
    """Application-owned coordination, independent of visible browser tabs."""

    failed = pyqtSignal(str)

    def __init__(self, path: Path, parent=None, backend: BrowserBackend | None = None):
        super().__init__(parent)
        require_owner_thread(self)
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Task services require the Qt application thread")
        self.store = TaskStore(path)
        self.backend = backend if backend is not None else QtBrowserBackend(self)
        self._sessions = {}
        self._closed = False
        self.failure = ""
        if hasattr(self.backend, "changed"):
            self.backend.changed.connect(self._session_changed)
        self.broker = CommandBroker(
            {
                "task.create": self.store.create,
                "task.status": self.store.get,
                "task.list": self.store.list_tasks,
                "task.events": self.store.events,
                "task.finish": self._finish_task,
                "session.open": self._open_session,
                "session.navigate": self._navigate,
                "session.inspect": self._inspect,
                "session.stop": self._stop,
                "session.attach": self._attach,
                "session.close": self._close_session,
            },
            self,
        )

    def _active(self, task_id):
        if self.store.get(task_id).status not in ACTIVE_STATUSES:
            raise ValueError("Task is no longer active")

    def _owned(self, task_id, session_id):
        self._active(task_id)
        if self._sessions.get(session_id) != task_id:
            raise ValueError("Session does not belong to this task")

    def _open_session(self, task_id):
        self._active(task_id)
        snapshot = self.backend.open_session(task_id)
        try:
            self.store.transition(task_id, "running")
            self.store.record_event(task_id, "session.opened", asdict(snapshot))
        except Exception:
            self.backend.close_session(snapshot.session_id)
            raise
        self._sessions[snapshot.session_id] = task_id
        return snapshot

    def _session_changed(self, snapshot):
        if self._closed or self.failure or snapshot.session_id not in self._sessions:
            return
        try:
            self.store.record_event(
                snapshot.task_id, "session.updated", asdict(snapshot)
            )
        except Exception:
            # Exceptions must not escape Qt callbacks or leave unjournaled commands enabled.
            self.failure = "Task journal write failed; task commands are disabled."
            self.broker.close()
            self.failed.emit(self.failure)

    def _navigate(self, task_id, session_id, url):
        self._owned(task_id, session_id)
        navigation_url(url)
        self.store.record_event(
            task_id,
            "session.navigation_requested",
            {
                "session_id": session_id,
                "url": url,
            },
        )
        return self.backend.navigate(session_id, url)

    def _inspect(self, task_id, session_id):
        self._owned(task_id, session_id)
        return self.backend.inspect(session_id)

    def _stop(self, task_id, session_id):
        self._owned(task_id, session_id)
        snapshot = self.backend.stop(session_id)
        self.store.record_event(task_id, "session.stopped", asdict(snapshot))
        return snapshot

    def _attach(self, task_id, session_id):
        self._owned(task_id, session_id)
        return self.backend.attach_view(session_id)

    def _close_session(self, task_id, session_id):
        self._owned(task_id, session_id)
        self._sessions.pop(session_id)
        self.backend.close_session(session_id)
        self.store.record_event(task_id, "session.closed", {"session_id": session_id})

    def _finish_task(self, task_id, status="completed"):
        self._active(task_id)
        if status not in TERMINAL_STATUSES:
            raise ValueError("Choose a terminal task status")
        for session_id, owner in list(self._sessions.items()):
            if owner == task_id:
                self._close_session(task_id, session_id)
        return self.store.transition(task_id, status)

    def close(self):
        require_owner_thread(self)
        if self._closed:
            return
        self.broker.close()
        self._closed = True
        try:
            for task in self.store.list_tasks():
                if task.status in ACTIVE_STATUSES:
                    self._finish_task(task.task_id, "interrupted")
        finally:
            self._sessions.clear()
            try:
                self.backend.close()
            finally:
                self.store.close()
