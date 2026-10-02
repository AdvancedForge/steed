"""Application-owned task authority; clients request but never approve grants."""

import re
import sqlite3
import time
from concurrent.futures import Future
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer, pyqtSignal

from services.browser_backend import BrowserBackend, QtBrowserBackend, navigation_url
from services.command_broker import CommandBroker, require_owner_thread
from services.connector_policy import destination_name
from services.connector_store import ConnectorStore, request_fingerprint
from services.connectors import ConnectorJob, ConnectorRegistry, ConnectorRequest
from services.form_submission import journal_origin
from services.page_actions import action_arguments, scroll_arguments, wait_arguments
from services.page_inspector import PageInspectionError
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
        connectors=None,
        connector_timeout_ms=30000,
    ):
        super().__init__(parent)
        require_owner_thread(self)
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Task services require the Qt application thread")
        self.store = TaskStore(path)
        self.connectors = connectors if connectors is not None else ConnectorRegistry()
        self.connector_store = ConnectorStore(self.store)
        self._connector_jobs = {}
        self.connector_timeout_ms = connector_timeout_ms
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
        self._controls = {}
        self._watchers = {}
        self._actor = ContextVar("task_user_actor", default="agent")
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
                "task.capabilities": self._capabilities,
                "browser.context": self._browser_context,
                "task.list": self.store.list_tasks,
                "task.events": self.store.events,
                "task.finish": self._finish_task,
                "artifact.store": self._store_artifact,
                "artifact.list": self._list_artifacts,
                "artifact.read": self._read_artifact,
                "app.list_tools": self._app_tools,
                "app.call_tool": self._app_call,
                "app.status": self._app_status,
                "app.cancel": self._app_cancel,
                "app.reconcile": self._app_reconcile,
                "grant.request": self._request_grant,
                "grant.list": self.store.grants,
                "client.denied": self._client_denied,
                "session.open": self._open_session,
                "session.navigate": self._navigate,
                "session.inspect": self._inspect,
                "page.inspect": self._inspect_page,
                "page.target": self._inspect_target,
                "page.navigate": self._navigate_target,
                "page.act": self._act,
                "page.submit": self._submit,
                "page.scroll": self._scroll,
                "page.wait": self._wait,
                "page.download": self._download,
                "page.upload": self._upload,
                "download.status": self._download_status,
                "download.cancel": self._download_cancel,
                "session.stop": self._stop,
                "session.attach": self._attach,
                "session.watch": self._watch,
                "user.command": self._user_command,
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

    def _store_artifact(
        self, task_id, title, content, format="markdown", source_events=()
    ):
        self._authorize(task_id, "artifact")
        try:
            result = self.store.store_artifact(
                task_id, title, content, format, source_events
            )
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        self.changed.emit()
        return result

    def _list_artifacts(self, task_id):
        self._authorize(task_id, "artifact")
        return self.store.artifacts(task_id)

    def _read_artifact(self, task_id, artifact_id):
        self._authorize(task_id, "artifact")
        result = self.store.artifact(task_id, artifact_id)
        try:
            self.store.record_event(
                task_id, "artifact.read", {"artifact_id": artifact_id}
            )
        except Exception:
            self._fatal(defer_broker_close=True)
            raise
        return result

    def _app_tools(self, task_id):
        self._usable()
        self._active(task_id)
        return self.connectors.describe()

    def _app_grant(self, task_id, record, *, consume=False):
        arguments = {
            "connector": record.connector,
            "tool": record.tool,
            "effect": record.effect,
            "destination": record.destination,
        }
        if consume:
            return self._authorize(task_id, "app", **arguments)
        self._usable()
        self._active(task_id)
        live = self._grants.get(task_id)
        if live is None:
            self._deny(task_id, "approval_required")
        try:
            live[1].check("app", **arguments)
        except TaskPermissionError as error:
            self._deny(task_id, error.code)
        return live[1]

    def _app_call(
        self, task_id, connector, tool, request_id, destination, artifact_id=None
    ):
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", request_id
        ):
            raise ValueError(
                "Provide a stable request ID of at most 64 letters/digits/_/-"
            )
        definition = self.connectors.get(connector, tool)
        destination_name(destination)
        if definition.validate_destination is not None:
            definition.validate_destination(destination)
        request = ConnectorRequest(
            request_id, connector, tool, definition.effect, destination, None
        )
        grant = self._app_grant(task_id, request)
        artifact = None
        if definition.requires_artifact:
            grant.check("artifact")
            if not artifact_id:
                raise ValueError("This app tool requires an owned artifact")
            artifact = self.store.artifact(task_id, artifact_id)
        elif artifact_id is not None:
            raise ValueError("This app tool does not accept an artifact")
        request = ConnectorRequest(
            request_id,
            connector,
            tool,
            definition.effect,
            destination,
            artifact,
            uuid4().hex,
        )
        fingerprint = request_fingerprint(
            connector, tool, definition.effect, destination, artifact
        )
        try:
            existing = self.connector_store.get(task_id, request_id)
        except KeyError:
            existing = None
        if existing:
            if existing.fingerprint != fingerprint:
                raise ValueError("Request ID is already bound to different inputs")
            return existing  # Includes uncertain writes: never dispatch a duplicate.
        self._app_capacity(task_id)
        self._app_grant(task_id, request, consume=True)
        try:
            receipt = self.connector_store.create(task_id, request, fingerprint)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        self._queue_app_job(task_id, definition, request, grant)
        self.changed.emit()
        return receipt

    def _app_capacity(self, task_id):
        if len(self._connector_jobs) >= 4 or any(
            owner == task_id for owner, _ in self._connector_jobs
        ):
            raise ValueError("An app operation is still running; wait for its result")

    def _queue_app_job(self, task_id, definition, request, grant, *, probe=False):
        job = ConnectorJob(
            definition,
            request,
            grant,
            lambda reply: self._app_finished(task_id, request.request_id, job, reply),
            self,
            probe=probe,
        )
        self._connector_jobs[task_id, request.request_id] = job
        job.timer.timeout.connect(
            lambda: self._app_timeout(task_id, request.request_id, job)
        )
        QTimer.singleShot(
            0, lambda: self._start_app_job(task_id, request.request_id, job)
        )

    def _start_app_job(self, task_id, request_id, job):
        if self._closed or self.failure:
            self._app_finished(task_id, request_id, job, (None, True))
            return
        try:
            self._app_grant(task_id, job.request)
            if job.cancelled.is_set():
                raise TaskPermissionError("app_cancelled")
            if not job.probe and job.request.artifact is not None:
                current = self.store.artifact(task_id, job.request.artifact.artifact_id)
                if current.sha256 != job.request.artifact.sha256:
                    raise ValueError("Draft changed before app dispatch")
        except Exception:
            self._app_finished(task_id, request_id, job, (None, True))
            return
        try:
            self.connector_store.update(
                task_id, request_id, "reconciling" if job.probe else "dispatched"
            )
            self.store.transition(task_id, "running")
            job.dispatched = True
            job.timer.start(self.connector_timeout_ms)
            job.start()
        except Exception:
            self._fatal()
            self._app_finished(task_id, request_id, job, (None, False))
        self.changed.emit()

    def _app_finished(self, task_id, request_id, job, reply):
        self._connector_jobs.pop((task_id, request_id), None)
        job.timer.stop()
        if not self._closed and not self.failure:
            outcome, not_dispatched = reply
            if outcome is not None:
                state, reason = outcome.state, ""
            elif job.probe:
                state, reason = "uncertain", "reconciliation_failed"
            elif not_dispatched:
                state, reason = "cancelled", "cancelled_before_adapter"
            else:
                state = "failed" if job.request.effect == "read" else "uncertain"
                reason = (
                    "connector_failed"
                    if state == "failed"
                    else "connector_outcome_unknown"
                )
            try:
                self.connector_store.update(
                    task_id,
                    request_id,
                    state,
                    reference=outcome.reference if outcome else "",
                    text=outcome.text if outcome else "",
                    reason=reason,
                )
            except Exception:
                self._fatal()
            self.changed.emit()
        job.deleteLater()

    def _app_status(self, task_id, request_id):
        self._usable()
        receipt = self.connector_store.get(task_id, request_id)
        self._app_grant(task_id, receipt, consume=True)
        return receipt

    def _app_timeout(self, task_id, request_id, job):
        job.cancelled.set()
        if self._closed or self.failure:
            return
        try:
            state = (
                "uncertain" if job.probe or job.request.effect != "read" else "failed"
            )
            self.connector_store.update(
                task_id, request_id, state, reason="connector_timeout"
            )
        except Exception:
            self._fatal()
        self.changed.emit()

    def _app_cancel(self, task_id, request_id):
        self._usable()
        self._active(task_id)
        return self._cancel_app_job(task_id, request_id)

    def _cancel_app_job(self, task_id, request_id):
        receipt = self.connector_store.get(task_id, request_id)
        job = self._connector_jobs.get((task_id, request_id))
        if job:
            job.cancelled.set()
            job.timer.stop()
            state = (
                "uncertain"
                if job.probe or (job.dispatched and receipt.effect != "read")
                else "failed"
                if job.dispatched
                else "cancelled"
            )
            try:
                receipt = self.connector_store.update(
                    task_id,
                    request_id,
                    state,
                    reason="cancelled_after_dispatch"
                    if job.dispatched
                    else "cancelled_before_dispatch",
                )
            except Exception:
                self._fatal(defer_broker_close=True)
                raise
            self.changed.emit()
        return receipt

    def _cancel_app_jobs(self, task_id):
        for owner, request_id in tuple(self._connector_jobs):
            if owner == task_id:
                self._cancel_app_job(task_id, request_id)

    def _app_reconcile(self, task_id, request_id):
        self._usable()
        receipt = self.connector_store.get(task_id, request_id)
        grant = self._app_grant(task_id, receipt)
        if receipt.state != "uncertain":
            raise ValueError("Only an uncertain app operation needs reconciliation")
        self._app_capacity(task_id)
        definition = self.connectors.get(receipt.connector, receipt.tool)
        if definition.reconcile is None:
            raise ValueError("This connector has no read-only reconciliation method")
        self._app_grant(task_id, receipt, consume=True)
        request = ConnectorRequest(
            request_id,
            receipt.connector,
            receipt.tool,
            receipt.effect,
            receipt.destination,
            None,
            receipt.operation_id,
        )
        try:
            receipt = self.connector_store.update(task_id, request_id, "reconciling")
        except Exception:
            self._fatal(defer_broker_close=True)
            raise
        self._queue_app_job(task_id, definition, request, grant, probe=True)
        return receipt

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

    def _browser_context(self, task_id):
        """Read selected-window/profile metadata without granting site access."""
        self._usable()
        self._active(task_id)
        if self.profiles is None:
            return {
                "window_id": None,
                "profile": None,
                "profile_selector": None,
                "shared_profile_available": False,
                "source": "no_browser_window",
            }
        return self.profiles.agent_context()

    def _capabilities(self, task_id):
        """Discover implemented authority without opening pages or issuing grants."""
        self._usable()
        self._active(task_id)
        report = getattr(self.backend, "capabilities", None)
        if not callable(report):
            raise RuntimeError("Browser backend capability report unavailable")
        return report()

    def _owned(self, task_id, session_id, *, allow_user=False, actor=None):
        self._active(task_id)
        if self._sessions.get(session_id) != task_id:
            self._deny(task_id, "session_not_owned")
        if not allow_user:
            actor = self._actor.get() if actor is None else actor
            owner = self._controls.get(session_id, "agent")
            if actor != owner:
                self._deny(
                    task_id,
                    "user_control_active" if owner != "agent" else "control_changed",
                )

    def _deny(self, task_id, code):
        self.store.record_event(task_id, "permission.denied", {"reason": code})
        raise TaskPermissionError(code)

    def _request_grant(self, task_id, scope):
        self._active(task_id)
        scope = TaskScope.parse(scope)
        self.connectors.check_scope(scope)
        if scope.profile != "private":
            if self.profiles is None:
                raise ValueError("Existing profiles require the application registry")
            self.profiles.validate_task_profile(scope.profile)
        result = self.store.request_grant(task_id, scope.payload())
        try:
            rules = self.store.approval_rules()
            profile_key = (
                self._approval_profile_key(scope)
                if any(r.mode == "profile" for r in rules)
                else ""
            )
            rule = next((r for r in rules if r.covers(scope, profile_key)), None)
            if rule is not None:
                result = self.approve_grant(task_id, result.grant_id, _rule=rule)
        except (OSError, sqlite3.Error, ValueError):
            self._fatal()
            raise
        self.changed.emit()
        return result

    def _approval_profile_key(self, scope, *, create=False):
        if scope.profile == "private" or self.profiles is None:
            return ""
        return self.profiles.approval_profile_key(scope.profile, create=create)

    def approve_grant(self, task_id, grant_id, *, remember="once", _rule=None):
        """Trusted Qt UI method, deliberately absent from the command broker."""
        require_owner_thread(self)
        self._usable()
        self._active(task_id)
        record = self.store.grant(task_id, grant_id)
        scope = TaskScope.parse(record.scope)
        if record.status != "requested":
            raise ValueError("Only a pending request can be approved")
        self.connectors.check_scope(scope)
        profile_key = (
            self._approval_profile_key(scope, create=remember == "profile")
            if remember == "profile" or _rule is not None and _rule.mode == "profile"
            else ""
        )
        if remember == "profile" and not profile_key:
            raise ValueError("Profile approvals require an existing named profile")
        if _rule is not None and not _rule.covers(scope, profile_key):
            raise ValueError("Saved approval does not cover this request")
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
            record = self.store.approve_with_rule(
                task_id,
                grant_id,
                expires,
                remember=remember,
                profile_key=profile_key,
                rule=_rule,
            )
            self._grants[task_id] = (grant_id, grant)
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

    def forget_approval_rule(self, rule_id):
        """Trusted settings: future requests prompt and linked live tasks lose access."""
        require_owner_thread(self)
        self._usable()
        try:
            links = self.store.remove_approval_rule(rule_id)
            for task_id, grant_id in links:
                live = self._grants.get(task_id)
                if live and live[0] == grant_id:
                    live[1].end("revoked")
            for task_id, grant_id in links:
                if task_id in self._grants:
                    self.revoke_grant(task_id, grant_id)
        except (OSError, sqlite3.Error):
            self._fatal()
            raise
        self.changed.emit()

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

    def disconnect_connector(self, connector):
        """Trusted account settings end matching authority before removing tools."""
        require_owner_thread(self)
        self._usable()
        for rule in self.store.approval_rules():
            if any(
                p["connector"] == connector
                for p in rule.scope.get("connector_permissions", [])
            ):
                self.forget_approval_rule(rule.rule_id)
        for task in self.store.list_tasks():
            for record in self.store.grants(task.task_id):
                if record.status == "requested" and any(
                    p["connector"] == connector
                    for p in record.scope.get("connector_permissions", [])
                ):
                    self.deny_grant(task.task_id, record.grant_id)
        for task_id, (grant_id, live) in list(self._grants.items()):
            if any(p.connector == connector for p in live.scope.connector_permissions):
                self.revoke_grant(task_id, grant_id)
        self.connectors.unregister(connector)
        self.changed.emit()

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
                snapshot.task_id, "session.updated", self._metadata_evidence(snapshot)
            )
        except Exception:
            self._fatal()

    @staticmethod
    def _metadata_evidence(snapshot):
        data = asdict(snapshot)
        if "url" in data:
            data["url"] = journal_origin(data["url"])
        if "title" in data:
            data["title"] = ""
        return data

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
            {"session_id": session_id, "url": journal_origin(url)},
        )
        return self.backend.navigate(session_id, url)

    def _inspect(self, task_id, session_id):
        self._owned(task_id, session_id, allow_user=True)
        self._authorize(task_id, "inspect")
        return self.backend.inspect(session_id)

    def _inspect_page(self, task_id, session_id):
        self._owned(task_id, session_id)
        grant = self._authorize(task_id, "read")
        self.store.record_event(
            task_id, "page.inspect_requested", {"session_id": session_id}
        )
        source = self.backend.inspect_page(session_id)

        def evidence(snapshot):
            return {
                "session_id": session_id,
                "snapshot_id": snapshot.snapshot_id,
                "revision": snapshot.revision,
                "frame_count": len(snapshot.frames),
                "target_count": sum(len(frame.targets) for frame in snapshot.frames),
                "text_characters": sum(len(frame.text) for frame in snapshot.frames),
                "limitations": snapshot.limitations,
            }

        return self._read_result(
            task_id, session_id, grant, source, "page.inspected", evidence
        )

    def _inspect_target(self, task_id, session_id, snapshot_id, target_id):
        self._owned(task_id, session_id)
        grant = self._authorize(task_id, "read")
        source = self.backend.inspect_target(session_id, snapshot_id, target_id)
        return self._read_result(
            task_id,
            session_id,
            grant,
            source,
            "page.target_checked",
            lambda target: {
                "session_id": session_id,
                "snapshot_id": snapshot_id,
                "target_id": target.target_id,
            },
        )

    def _read_result(
        self,
        task_id,
        session_id,
        grant,
        source,
        kind,
        evidence,
        *,
        failure_kind="page.read_failed",
        failure_details=None,
        actor=None,
    ):
        result = Future()
        actor = self._actor.get() if actor is None else actor

        def completed(source):
            if result.done():
                return
            try:
                value = source.result()
                self._usable()
                self._owned(task_id, session_id, actor=actor)
                grant.check("read")
            except Exception as error:
                result.set_exception(error)
                if not self._closed and not self.failure:
                    try:
                        self.store.record_event(
                            task_id,
                            failure_kind,
                            {
                                "session_id": session_id,
                                "reason": getattr(error, "code", "read_failed"),
                                **(failure_details or {}),
                            },
                        )
                    except Exception:
                        self._fatal()
            else:
                try:
                    self.store.record_event(task_id, kind, evidence(value))
                except Exception as error:
                    result.set_exception(error)
                    self._fatal()
                else:
                    result.set_result(value)

        source.add_done_callback(completed)
        return result

    def _navigate_target(self, task_id, session_id, snapshot_id, target_id):
        self._owned(task_id, session_id)
        grant = self._authorize(task_id, "read")
        try:
            grant.check("navigate")
        except TaskPermissionError as error:
            self._deny(task_id, error.code)
        checked = self.backend.inspect_target(session_id, snapshot_id, target_id)
        result = Future()
        actor = self._actor.get()

        def validated(source):
            try:
                target = source.result()
                self._usable()
                self._owned(task_id, session_id, actor=actor)
                grant.check("read")
                if (
                    target.kind != "link"
                    or not target.enabled
                    or target.read_only
                    or "navigate" not in target.operations
                    or target.href is None
                ):
                    raise PageInspectionError("target_not_navigable")
                grant.check("navigate", url=target.href)
                try:
                    self.store.record_event(
                        task_id,
                        "page.navigation_requested",
                        {
                            "session_id": session_id,
                            "snapshot_id": snapshot_id,
                            "target_id": target_id,
                        },
                    )
                except Exception as error:
                    result.set_exception(error)
                    self._fatal()
                    return
                navigated = self.backend.navigate_observed(session_id, target.href)
                receipt = self._read_result(
                    task_id,
                    session_id,
                    grant,
                    navigated,
                    "page.navigation_finished",
                    lambda outcome: {
                        **self._metadata_evidence(outcome),
                        "target_id": target_id,
                    },
                    actor=actor,
                )
                receipt.add_done_callback(completed)
            except Exception as error:
                if not self._closed and not self.failure:
                    try:
                        self.store.record_event(
                            task_id,
                            "page.navigation_failed",
                            {
                                "session_id": session_id,
                                "reason": getattr(error, "code", "navigation_failed"),
                            },
                        )
                    except Exception as write_error:
                        result.set_exception(write_error)
                        self._fatal()
                        return
                result.set_exception(error)

        def completed(source):
            if result.done():
                return
            try:
                result.set_result(source.result())
            except Exception as error:
                result.set_exception(error)

        checked.add_done_callback(validated)
        return result

    def _stop(self, task_id, session_id):
        # The current controller can stop loading without an active grant.
        self._owned(task_id, session_id)
        snapshot = self.backend.stop(session_id)
        self.store.record_event(
            task_id, "session.stopped", self._metadata_evidence(snapshot)
        )
        return snapshot

    def _action_authority(self, task_id, session_id, action):
        self._owned(task_id, session_id)
        grant = self._authorize(task_id, action)
        try:
            grant.check("read")
        except TaskPermissionError as error:
            self._deny(task_id, error.code)
        return grant

    def _act(
        self,
        task_id,
        session_id,
        snapshot_id,
        target_id,
        operation,
        text=None,
        index=None,
    ):
        arguments = action_arguments(operation, text=text, index=index)
        grant = self._action_authority(task_id, session_id, "edit")
        action_id = uuid4().hex
        # Record identifiers/operation only: entered text and option values are
        # never persisted as arguments, success receipts, or error messages.
        return self._start_action(
            task_id,
            session_id,
            grant,
            action_id,
            {
                "action_id": action_id,
                "session_id": session_id,
                "snapshot_id": snapshot_id,
                "target_id": target_id,
                "operation": operation,
            },
            lambda: self.backend.act(
                session_id, action_id, snapshot_id, target_id, arguments
            ),
        )

    def _scroll(self, task_id, session_id, snapshot_id, frame_id=None, dx=0, dy=0):
        arguments = scroll_arguments(dx, dy)
        grant = self._action_authority(task_id, session_id, "scroll")
        action_id = uuid4().hex
        return self._start_action(
            task_id,
            session_id,
            grant,
            action_id,
            {
                "action_id": action_id,
                "session_id": session_id,
                "snapshot_id": snapshot_id,
                "frame_id": frame_id,
                "operation": "scroll",
            },
            lambda: self.backend.scroll(
                session_id, action_id, snapshot_id, frame_id, arguments
            ),
        )

    def _submit(self, task_id, session_id, snapshot_id, target_id):
        grant = self._action_authority(task_id, session_id, "submit")
        try:
            grant.check("navigate")
        except TaskPermissionError as error:
            self._deny(task_id, error.code)
        action_id = uuid4().hex
        return self._start_action(
            task_id,
            session_id,
            grant,
            action_id,
            {
                "action_id": action_id,
                "session_id": session_id,
                "snapshot_id": snapshot_id,
                "target_id": target_id,
                "operation": "submit",
            },
            lambda: self.backend.submit_form(
                session_id, action_id, snapshot_id, target_id
            ),
        )

    def _start_action(self, task_id, session_id, grant, action_id, evidence, dispatch):
        try:
            self.store.record_event(task_id, "page.action_requested", evidence)
        except Exception:
            self._fatal(defer_broker_close=True)
            raise
        try:
            source = dispatch()
        except Exception as error:
            try:
                self.store.record_event(
                    task_id,
                    "page.action_failed",
                    {
                        "session_id": session_id,
                        "action_id": action_id,
                        "reason": getattr(error, "code", "action_failed"),
                    },
                )
            except Exception:
                self._fatal()
                raise
            raise
        return self._read_result(
            task_id,
            session_id,
            grant,
            source,
            "page.action_finished",
            asdict,
            failure_kind="page.action_failed",
            failure_details={"action_id": action_id},
        )

    def _wait(self, task_id, session_id, condition="ready", text=None, timeout_ms=5000):
        wait_arguments(condition, text, timeout_ms)
        self._owned(task_id, session_id)
        grant = self._authorize(task_id, "read")
        self.store.record_event(
            task_id,
            "page.wait_requested",
            {
                "session_id": session_id,
                "condition": condition,
                "timeout_ms": timeout_ms,
            },
        )
        source = self.backend.wait(session_id, condition, text, timeout_ms)
        return self._read_result(
            task_id,
            session_id,
            grant,
            source,
            "page.wait_finished",
            self._metadata_evidence,
        )

    def _attach(self, task_id, session_id):
        self._owned(task_id, session_id, allow_user=True)
        self._authorize(task_id, "attach")
        return self.backend.attach_view(session_id)

    def _download(self, task_id, session_id, snapshot_id, target_id, destination):
        self._owned(task_id, session_id)
        grant = self._authorize(task_id, "download", destination=destination)
        grant.check("read")
        grant.check("navigate")
        checked = self.backend.inspect_target(session_id, snapshot_id, target_id)
        result = Future()
        actor = self._actor.get()
        download_id = uuid4().hex

        def validated(source):
            try:
                target = source.result()
                self._usable()
                self._owned(task_id, session_id, actor=actor)
                if (
                    target.kind != "link"
                    or not target.enabled
                    or target.read_only
                    or "navigate" not in target.operations
                    or target.href is None
                ):
                    raise PageInspectionError("target_not_navigable")
                grant.check("download", url=target.href, destination=destination)
                grant.check("read")
                grant.check("navigate", url=target.href)
                details = {
                    "download_id": download_id,
                    "session_id": session_id,
                    "destination": destination,
                    "url": journal_origin(target.href),
                }

                def publish():
                    self._usable()
                    self._owned(task_id, session_id, actor=actor)
                    grant.check("download", url=target.href, destination=destination)
                    try:
                        self.store.record_event(
                            task_id, "download.publish_requested", details
                        )
                    except Exception:
                        self._fatal()
                        raise

                try:
                    self.store.record_event(task_id, "download.requested", details)
                except Exception:
                    self._fatal()
                    raise
                transfer = self.backend.start_download(
                    session_id, download_id, target.href, destination, publish
                )

                def finished(source):
                    if self._closed or self.failure:
                        return
                    try:
                        outcome = source.result()
                        self.store.record_event(
                            task_id, "download.finished", asdict(outcome)
                        )
                        self.changed.emit()
                    except Exception:
                        self._fatal()

                transfer.result.add_done_callback(finished)
                result.set_result(transfer.snapshot())
            except Exception as error:
                result.set_exception(error)
                if not self._closed and not self.failure:
                    try:
                        self.store.record_event(
                            task_id,
                            "download.failed",
                            {
                                "session_id": session_id,
                                "download_id": download_id,
                                "destination": destination,
                                "state": "failed",
                                "reason": getattr(error, "code", "download_failed"),
                            },
                        )
                    except Exception:
                        self._fatal()

        checked.add_done_callback(validated)
        return result

    def _download_status(self, task_id, session_id, download_id):
        self._owned(task_id, session_id, allow_user=True)
        self._authorize(task_id, "download")
        return self.backend.inspect_download(session_id, download_id)

    def _upload(self, task_id, session_id, snapshot_id, target_id, source):
        self._owned(task_id, session_id)
        grant = self._authorize(
            task_id, "upload", source=source, url=self.backend.inspect(session_id).url
        )
        grant.check("read")
        action_id = uuid4().hex
        return self._start_action(
            task_id,
            session_id,
            grant,
            action_id,
            {
                "action_id": action_id,
                "session_id": session_id,
                "snapshot_id": snapshot_id,
                "target_id": target_id,
                "operation": "upload",
                "source": source,
            },
            lambda: self.backend.upload(
                session_id, action_id, snapshot_id, target_id, source
            ),
        )

    def _download_cancel(self, task_id, session_id, download_id):
        self._owned(task_id, session_id, allow_user=True)
        return self.backend.cancel_download(session_id, download_id)

    def _watch(self, task_id, session_id):
        view = self._attach(task_id, session_id)
        if session_id not in self._watchers:
            from ui.task_watch import TaskWatchDialog

            parent = None
            if self.profiles is not None:
                context = self.profiles.agent_context()
                parent = next(
                    (
                        owner.parent()
                        for owner in self.profiles.owners
                        if getattr(owner.parent(), "browser_window_id", None)
                        == context["window_id"]
                    ),
                    None,
                )
            window = TaskWatchDialog(self, task_id, session_id, view, parent)
            self._watchers[session_id] = window
        window = self._watchers[session_id]
        window.show()
        window.raise_()
        window.activateWindow()
        self._control_event(task_id, "session.watched", session_id)
        return {
            "session_id": session_id,
            "watching": True,
            "control_owner": self.backend.inspect(session_id).control_owner,
        }

    def take_control(self, task_id, session_id, key):
        require_owner_thread(self)
        self._owned(task_id, session_id, allow_user=True)
        if session_id in self._controls:
            raise TaskPermissionError("user_control_active")
        if (
            self._watchers.get(session_id) is None
            or self._watchers[session_id].control_key != key
        ):
            raise TaskPermissionError("control_not_owned")
        self.backend.check_control_change(session_id)
        grant = self._authorize(task_id, "attach")
        grant.check("read")
        self._control_event(task_id, "session.control_taken", session_id)
        self.backend.change_control(session_id, "user")
        self._controls[session_id] = key
        self.changed.emit()

    def return_control(self, task_id, session_id, key):
        require_owner_thread(self)
        self._usable()
        self._owned(task_id, session_id, allow_user=True)
        if self._controls.get(session_id) != key:
            raise TaskPermissionError("control_not_owned")
        self.backend.check_control_change(session_id)
        self._control_event(task_id, "session.control_returned", session_id)
        self.backend.change_control(session_id, "agent")
        self._controls.pop(session_id)
        self.changed.emit()

    def _control_event(self, task_id, kind, session_id):
        try:
            self.store.record_event(task_id, kind, {"session_id": session_id})
        except Exception:
            self._fatal()
            raise

    def _user_command(self, task_id, session_id, key, operation_name, arguments):
        if self._controls.get(session_id) != key:
            raise TaskPermissionError("control_not_owned")
        if operation_name not in {
            "page.inspect",
            "page.navigate",
            "page.act",
            "page.submit",
            "page.scroll",
            "page.wait",
            "page.upload",
        }:
            raise ValueError("Unsupported user action")
        if {"task_id", "session_id"} & arguments.keys():
            raise ValueError("User action identity is fixed")
        token = self._actor.set(key)
        try:
            return self.broker._handlers[operation_name](
                task_id=task_id, session_id=session_id, **arguments
            )
        finally:
            self._actor.reset(token)

    def close_watch(self, task_id, session_id, key):
        require_owner_thread(self)
        watcher = self._watchers.get(session_id)
        if watcher is not None and watcher.control_key == key:
            self._watchers.pop(session_id)
        if self._controls.get(session_id) == key and session_id in self._sessions:
            try:
                self.return_control(task_id, session_id, key)
            except Exception:
                if not self._closed and not self.failure:
                    try:
                        self.cancel_task(task_id)
                    except Exception:
                        self._fatal()

    def _close_session(self, task_id, session_id):
        self._owned(task_id, session_id, allow_user=True)
        self._sessions.pop(session_id)
        self._controls.pop(session_id, None)
        watcher = self._watchers.pop(session_id, None)
        if watcher is not None:
            watcher.session_ended()
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
        self._cancel_app_jobs(task_id)
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
                self._controls.pop(session_id, None)
                watcher = self._watchers.pop(session_id, None)
                if watcher is not None:
                    watcher.session_ended()
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

    def _fatal(self, message=None, *, defer_broker_close=False):
        if self.failure:
            return
        self.failure = (
            message or "Task journal is unavailable; task commands are disabled."
        )
        for _, grant in self._grants.values():
            grant.end("journal_failure")
        for job in self._connector_jobs.values():
            job.cancelled.set()
            job.timer.stop()
        self._timer.stop()
        if defer_broker_close:
            # Let the currently dispatching handler publish its original write
            # error. Authority is already frozen and _usable() denies mutations.
            QTimer.singleShot(0, self.broker.close)
        else:
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
