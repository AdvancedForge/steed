"""Application-owned task authority; clients request but never approve grants."""

import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer, pyqtSignal

from services.browser_backend import BrowserBackend, QtBrowserBackend, navigation_url
from services.command_broker import CommandBroker, require_owner_thread
from services.task_client import TaskClient
from services.task_policy import LiveGrant, TaskPermissionError, TaskScope
from services.task_store import ACTIVE_STATUSES, TERMINAL_STATUSES, TaskStore


class TaskService(QObject):
    """Trusted UI approves immutable scopes; broker mutations check live grants."""

    failed = pyqtSignal(str)
    changed = pyqtSignal()

    def __init__(
        self,
        path: Path,
        parent=None,
        backend: BrowserBackend | None = None,
        *,
        clock=time.monotonic,
        profiles=None,
    ):
        super().__init__(parent)
        require_owner_thread(self)
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Task services require the Qt application thread")
        self.store = TaskStore(path)
        self.profiles = profiles
        self.backend = (
            backend
            if backend is not None
            else QtBrowserBackend(
                self, profile_provider=profiles.task_profile if profiles else None
            )
        )
        self._clock = clock
        self._sessions = {}
        self._grants = {}
        self._closed = False
        self.failure = ""
        if hasattr(self.backend, "changed"):
            self.backend.changed.connect(self._session_changed)
        if hasattr(self.backend, "blocked"):
            self.backend.blocked.connect(
                self._network_blocked, Qt.ConnectionType.QueuedConnection
            )
        self.broker = CommandBroker(
            {
                "task.create": self._create,
                "task.status": self.store.get,
                "task.list": self.store.list_tasks,
                "task.events": self.store.events,
                "task.finish": self._finish_task,
                "grant.request": self._request_grant,
                "grant.list": self.store.grants,
                "client.denied": self._client_denied,
                "session.open": self._open_session,
                "session.navigate": self._navigate,
                "session.inspect": self._inspect,
                "session.stop": self._stop,
                "session.attach": self._attach,
                "session.close": self._close_session,
            },
            self,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._expire_grants)
        self._timer.start()

    def _create(self, objective):
        task = self.store.create(objective)
        self.changed.emit()
        return task

    def client(self, task_id):
        """Trusted controller issues a handle bound to one task, on the Qt thread."""
        require_owner_thread(self)
        self._usable()
        self._active(task_id)
        return TaskClient(task_id, self.broker)

    def _client_denied(self, task_id, reason):
        self._deny(task_id, reason)

    def _active(self, task_id):
        if self.store.get(task_id).status not in ACTIVE_STATUSES:
            raise TaskPermissionError("task_inactive")

    def _owned(self, task_id, session_id):
        self._active(task_id)
        if self._sessions.get(session_id) != task_id:
            self._deny(task_id, "session_not_owned")

    def _deny(self, task_id, code):
        self.store.record_event(task_id, "permission.denied", {"reason": code})
        raise TaskPermissionError(code)

    def _request_grant(self, task_id, scope):
        self._active(task_id)
        scope = TaskScope.parse(scope)
        if scope.profile != "private":
            if self.profiles is None:
                raise ValueError("Existing profiles require the application registry")
            self.profiles.validate_task_profile(scope.profile)
        result = self.store.request_grant(task_id, scope.payload())
        self.changed.emit()
        return result

    def approve_grant(self, task_id, grant_id):
        """Trusted Qt UI method, deliberately absent from the command broker."""
        require_owner_thread(self)
        self._usable()
        self._active(task_id)
        record = self.store.grant(task_id, grant_id)
        scope = TaskScope.parse(record.scope)
        if record.status != "requested":
            raise ValueError("Only a pending request can be approved")
        grant = LiveGrant(scope, clock=self._clock)
        expires = (
            datetime.now(timezone.utc) + timedelta(seconds=scope.duration_seconds)
        ).isoformat()
        existing = scope.profile != "private"
        if existing:
            if self.profiles is None:
                raise ValueError("Existing profiles require the application registry")
            self.profiles.reserve_task_profile(task_id, scope.profile)
        try:
            record = self.store.resolve_grant(
                task_id, grant_id, "approved", expires_at=expires
            )
            self._grants[task_id] = (grant_id, grant)
            if existing:
                self.profiles.activate_task_profile(
                    task_id, grant, self.backend.blocked.emit
                )
        except Exception:
            grant.end("approval_failed")
            if task_id in self._grants:
                self._fatal(
                    "Task profile access could not be initialized; "
                    "task commands are disabled."
                )
            elif existing:
                self.profiles.release_task_profiles(task_id)
            raise
        self.changed.emit()
        return record

    def deny_grant(self, task_id, grant_id):
        require_owner_thread(self)
        self._usable()
        result = self.store.resolve_grant(task_id, grant_id, "denied", reason="user")
        self.changed.emit()
        return result

    def revoke_grant(self, task_id, grant_id):
        require_owner_thread(self)
        self._usable()
        record = self.store.grant(task_id, grant_id)
        live = self._grants.get(task_id)
        if record.status != "approved" or live is None or live[0] != grant_id:
            raise ValueError("Choose this task's active grant")
        live[1].end("revoked")
        self._end_authority(task_id, "revoked")
        return self.store.grant(task_id, grant_id)

    def cancel_task(self, task_id):
        require_owner_thread(self)
        self._usable()
        return self._finish_task(task_id, "cancelled")

    def _usable(self):
        if self._closed or self.failure:
            raise RuntimeError("Task services are unavailable")

    def _authorize(self, task_id, action, **arguments):
        self._usable()
        self._active(task_id)
        live = self._grants.get(task_id)
        if live is None:
            self._deny(task_id, "approval_required")
        _, grant = live
        try:
            grant.check(action, **arguments)
            grant.consume_operation()
        except TaskPermissionError as error:
            self.store.record_event(
                task_id, "permission.denied", {"reason": error.code}
            )
            if grant.reason():
                self._end_authority(task_id, grant.reason())
            raise
        return grant

    def _open_session(self, task_id, profile="private"):
        if not isinstance(profile, str):
            self._deny(task_id, "profile_out_of_scope")
        grant = self._authorize(task_id, "open", profile=profile)
        if (
            sum(owner == task_id for owner in self._sessions.values())
            >= grant.scope.max_sessions
        ):
            self._deny(task_id, "session_budget_exceeded")
        self.store.record_event(task_id, "session.open_requested", {"profile": profile})
        snapshot = self.backend.open_session(task_id, grant=grant, profile=profile)
        try:
            self.store.transition(task_id, "running")
            self.store.record_event(task_id, "session.opened", asdict(snapshot))
        except Exception:
            self.backend.close_session(snapshot.session_id)
            raise
        self._sessions[snapshot.session_id] = task_id
        self.changed.emit()
        return snapshot

    def _session_changed(self, snapshot):
        if self._closed or self.failure or snapshot.session_id not in self._sessions:
            return
        try:
            self.store.record_event(
                snapshot.task_id, "session.updated", asdict(snapshot)
            )
        except Exception:
            self._fatal()

    def _network_blocked(self, task_id, reason):
        if self._closed or self.failure or task_id not in self._grants:
            return
        try:
            self.store.record_event(
                task_id, "permission.denied", {"reason": reason, "source": "engine"}
            )
            self._expire_grants()
        except Exception:
            self._fatal()

    def _navigate(self, task_id, session_id, url):
        self._owned(task_id, session_id)
        navigation_url(url)
        self._authorize(task_id, "navigate", url=url)
        self.store.record_event(
            task_id,
            "session.navigation_requested",
            {"session_id": session_id, "url": url},
        )
        return self.backend.navigate(session_id, url)

    def _inspect(self, task_id, session_id):
        self._owned(task_id, session_id)
        self._authorize(task_id, "inspect")
        return self.backend.inspect(session_id)

    def _stop(self, task_id, session_id):
        # Cleanup always remains available, even without an active grant.
        self._owned(task_id, session_id)
        snapshot = self.backend.stop(session_id)
        self.store.record_event(task_id, "session.stopped", asdict(snapshot))
        return snapshot

    def _attach(self, task_id, session_id):
        self._owned(task_id, session_id)
        self._authorize(task_id, "attach")
        return self.backend.attach_view(session_id)

    def _close_session(self, task_id, session_id):
        self._owned(task_id, session_id)
        self._sessions.pop(session_id)
        self.backend.close_session(session_id)
        self.store.record_event(task_id, "session.closed", {"session_id": session_id})
        self.changed.emit()

    def _finish_task(self, task_id, status="completed"):
        self._active(task_id)
        if status not in TERMINAL_STATUSES:
            raise ValueError("Choose a terminal task status")
        live = self._grants.pop(task_id, None)
        if live:
            live[1].end(status)
        try:
            for session_id, owner in list(self._sessions.items()):
                if owner == task_id:
                    self._close_session(task_id, session_id)
            result = self.store.transition(task_id, status)
        finally:
            self._discard_sessions(task_id)
        self.changed.emit()
        return result

    def _discard_sessions(self, task_id=None):
        for session_id, owner in list(self._sessions.items()):
            if task_id is None or owner == task_id:
                self._sessions.pop(session_id)
                self.backend.close_session(session_id)
        if self.profiles is not None:
            self.profiles.release_task_profiles(task_id)

    def _end_authority(self, task_id, reason):
        live = self._grants.pop(task_id, None)
        if not live:
            return
        grant_id, grant = live
        grant.end(reason)
        status = (
            "expired"
            if reason == "expired"
            else "revoked"
            if reason == "revoked"
            else "exhausted"
        )
        try:
            self.store.resolve_grant(task_id, grant_id, status, reason=reason)
            self._finish_task(task_id, "cancelled")
        except Exception:
            self._fatal()
            raise
        finally:
            self._discard_sessions(task_id)
        self.changed.emit()

    def _expire_grants(self):
        if self._closed or self.failure:
            return
        try:
            for task_id, (_, grant) in list(self._grants.items()):
                reason = grant.reason()
                if reason:
                    self._end_authority(task_id, reason)
        except Exception:
            self._fatal()

    def _fatal(self, message=None):
        if self.failure:
            return
        self.failure = (
            message or "Task journal is unavailable; task commands are disabled."
        )
        for _, grant in self._grants.values():
            grant.end("journal_failure")
        self._timer.stop()
        self.broker.close()
        # Delete native pages after the engine's current signal callback returns.
        QTimer.singleShot(0, lambda: None if self._closed else self._discard_sessions())
        self.failed.emit(self.failure)
        self.changed.emit()

    def close(self):
        require_owner_thread(self)
        if self._closed:
            return
        self.broker.close()
        self._timer.stop()
        self._closed = True
        for _, grant in self._grants.values():
            grant.end("shutdown")
        try:
            for task in self.store.list_tasks():
                if task.status in ACTIVE_STATUSES:
                    self._finish_task(task.task_id, "interrupted")
        finally:
            self._grants.clear()
            self._sessions.clear()
            try:
                self.backend.close()
            finally:
                if self.profiles is not None:
                    self.profiles.release_task_profiles()
                self.store.close()
