"""Application-owned task authority; clients request but never approve grants."""

import hashlib
import os
import re
import sqlite3
import stat
import time
from concurrent.futures import Future
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer, pyqtSignal

from services.agent_errors import AgentRequestError
from services.app_plans import AppPlans
from services.browser_backend import BrowserBackend, QtBrowserBackend, navigation_url
from services.command_broker import CommandBroker, require_owner_thread
from services.connector_access import ConnectorAccess
from services.connector_policy import destination_name
from services.connector_reviews import ConnectorReviews
from services.connector_store import ConnectorStore, request_fingerprint
from services.connectors import (
    ConnectorJob,
    ConnectorProbe,
    ConnectorRegistry,
    ConnectorRequest,
    ConnectorReview,
)
from services.data_panels import DataPanels
from services.form_submission import journal_origin
from services.layout_proposals import LayoutProposals
from services.page_actions import action_arguments, scroll_arguments, wait_arguments
from services.page_inspector import PageInspectionError
from services.recipe_proposals import RecipeProposals
from services.start_page_proposals import StartPageProposals
from services.task_artifacts import artifact_input
from services.task_client import TaskClient
from services.task_downloads import DownloadDestination
from services.task_messages import TaskMessages
from services.task_policy import LiveGrant, TaskPermissionError, TaskScope
from services.task_recovery import (
    MAX_RECOVERY_ARTIFACTS,
    MAX_RECOVERY_FILES,
    recovery_snapshot,
)
from services.task_store import ACTIVE_STATUSES, TERMINAL_STATUSES, TaskStore
from services.task_workspace import MAX_FILE_BYTES, TaskWorkspace
from services.worker_manager import WorkerManager

# A waiting call must answer within the adapter's 35 s reply limit.
MESSAGE_WAIT_MS = 25000


class TaskService(QObject):
    """Trusted UI approves immutable scopes; broker mutations check live grants."""

    failed = pyqtSignal(str)
    changed = pyqtSignal()
    user_message_sent = pyqtSignal(str, object)
    conversation_cleared = pyqtSignal(str)

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
        connections=None,
    ):
        super().__init__(parent)
        require_owner_thread(self)
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Task services require the Qt application thread")
        self.store = TaskStore(path)
        self.messages = TaskMessages(self.store)
        self.external_tasks = set()
        # Disconnected tasks the agent can still resume: task_id -> epoch seconds.
        self.resumable = {}
        self._message_waits = set()
        self.connectors = connectors if connectors is not None else ConnectorRegistry()
        self.connector_store = ConnectorStore(self.store)
        self.account_access = ConnectorAccess(self.store, self.connectors)
        self.app_reviews = ConnectorReviews(self.store)
        self.app_plans = AppPlans(self)
        self.workspace = TaskWorkspace(self.store)
        self._library = None
        self._library_path = path.with_name("agent_library.sqlite")
        self._connector_jobs = {}
        self.connector_timeout_ms = connector_timeout_ms
        self.profiles = profiles
        self.backend = (
            backend
            if backend is not None
            else QtBrowserBackend(
                self,
                profile_provider=profiles.task_profile if profiles else None,
                browser_config=getattr(profiles, "config", None),
            )
        )
        self._clock = clock
        self._sessions = {}
        self._grants = {}
        self._controls = {}
        self._watchers = {}
        from services.live_tabs import LiveTabs

        self.live_tabs = LiveTabs(self)
        self._actor = ContextVar("task_user_actor", default="agent")
        self._closed = False
        self.failure = ""
        self.auto_approve_requests = False
        self.control_requests = {}
        self.workers = WorkerManager(self)
        self.recipes = RecipeProposals(self, connections)
        self.layouts = LayoutProposals(self)
        self.data_panels = DataPanels(self)
        self.start_pages = StartPageProposals(self)
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
                "browser.request_control": self._request_control,
                "browser.control_status": self._control_status,
                "start_page.capabilities": self._start_page_capabilities,
                "start_page.propose": lambda task_id, source, request_id: (
                    self._layout_action(
                        self.start_pages.propose, task_id, source, request_id
                    )
                ),
                "start_page.status": lambda task_id, request_id: self._layout_action(
                    self.start_pages.get, task_id, request_id
                ),
                "panel.capabilities": self._panel_capabilities,
                "panel.propose": lambda task_id, source, request_id: (
                    self._layout_action(
                        self.data_panels.propose, task_id, source, request_id
                    )
                ),
                "panel.status": lambda task_id, request_id: self._layout_action(
                    self.data_panels.get, task_id, request_id
                ),
                "panel.publish": lambda task_id, request_id, update_id, expected_revision, bindings: (
                    self._layout_action(
                        self.data_panels.publish,
                        task_id,
                        request_id,
                        update_id,
                        expected_revision,
                        bindings,
                    )
                ),
                "layout.capabilities": self._layout_capabilities,
                "layout.propose": lambda task_id, source, request_id: (
                    self._layout_action(
                        self.layouts.propose, task_id, source, request_id
                    )
                ),
                "layout.status": lambda task_id, request_id: self._layout_action(
                    self.layouts.get, task_id, request_id
                ),
                "task.list": self.store.list_tasks,
                "task.events": self.store.events,
                "conversation.read": self._read_messages,
                "conversation.wait": self._wait_messages,
                "conversation.reply": self._reply_message,
                "task.finish": self._finish_task,
                "task.recover": self._request_recovery,
                "task.recovery": self._recovery_info,
                "artifact.store": self._store_artifact,
                "artifact.list": self._list_artifacts,
                "artifact.read": self._read_artifact,
                "workspace.begin": self._workspace_begin,
                "workspace.write": self._workspace_write,
                "workspace.seal": self._workspace_seal,
                "workspace.list": self._workspace_list,
                "workspace.read": self._workspace_read,
                "workspace.export": self._workspace_export,
                "workspace.stage_extension": self._workspace_stage_extension,
                "library.folders": lambda task_id: self._library_run(
                    task_id, "folders", enabled_only=True
                ),
                "library.list": lambda task_id, folder_id, **args: self._library_run(
                    task_id, "list", folder_id, **args
                ),
                "library.search": lambda task_id, **args: self._library_run(
                    task_id, "search", **args
                ),
                "library.read": lambda task_id, folder_id, **args: self._library_run(
                    task_id, "read", folder_id, **args
                ),
                "library.mkdir": lambda task_id, folder_id, path: self._library_run(
                    task_id, "mkdir", folder_id, path
                ),
                "library.write_begin": lambda task_id, folder_id, **args: (
                    self._library_run(task_id, "begin", folder_id, task=task_id, **args)
                ),
                "library.write_chunk": lambda task_id, write_id, **args: (
                    self._library_write(task_id, "chunk", write_id, **args)
                ),
                "library.write_commit": lambda task_id, write_id: self._library_write(
                    task_id, "commit", write_id
                ),
                "library.remove": lambda task_id, folder_id, **args: self._library_run(
                    task_id, "remove", folder_id, task=task_id, **args
                ),
                "library.restore": lambda task_id, folder_id, path: self._library_run(
                    task_id, "restore", folder_id, path
                ),
                "library.import_workspace": self._library_import_workspace,
                "library.save_workspace": self._library_save_workspace,
                "worker.run": lambda task_id, extension_id, request_id, runtime="stdlib": (
                    self._worker_action(
                        self.workers.run, task_id, extension_id, request_id, runtime
                    )
                ),
                "worker.capabilities": self._worker_capabilities,
                "worker.status": lambda task_id, request_id: self._worker_action(
                    self.workers.status, task_id, request_id
                ),
                "worker.list": lambda task_id: self._worker_action(
                    self.workers.list, task_id
                ),
                "worker.cancel": self._worker_cancel,
                "recipe.propose": lambda task_id, file_id, request_id: (
                    self._worker_action(
                        self.recipes.propose, task_id, file_id, request_id
                    )
                ),
                "recipe.status": self._recipe_status,
                "recipe.list": self._recipe_list,
                "recipe.capabilities": self._recipe_capabilities,
                "recipe.validate": lambda task_id, file_id: self._worker_action(
                    self.recipes.validate, task_id, file_id
                ),
                "app.list_tools": self._app_tools,
                "app.call_tool": self._app_call,
                "app.status": self._app_status,
                "app.cancel": self._app_cancel,
                "app.reconcile": self._app_reconcile,
                "app.reconcile_history": self._app_reconcile_history,
                "grant.request": self._request_grant,
                "grant.list": self.store.grants,
                "client.denied": self._client_denied,
                "browser.tabs": self.live_tabs.list,
                "session.adopt": self.live_tabs.adopt,
                "session.open": self._open_session,
                "session.navigate": self._navigate,
                "session.inspect": self._inspect,
                "page.inspect": self._inspect_page,
                "page.capture": self._capture_page,
                "page.click": lambda task_id, session_id, capture_id, x, y: (
                    self._computer_page(
                        task_id, session_id, capture_id, "click", {"x": x, "y": y}
                    )
                ),
                "page.type": lambda task_id, session_id, capture_id, text: (
                    self._computer_page(
                        task_id, session_id, capture_id, "type", {"text": text}
                    )
                ),
                "page.key": lambda task_id, session_id, capture_id, key: (
                    self._computer_page(
                        task_id, session_id, capture_id, "key", {"key": key}
                    )
                ),
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

    def _request_recovery(self, task_id):
        """Ask the user to choose historical material; clients cannot select a source."""
        self._usable()
        self._active(task_id)
        try:
            result = self.store.request_recovery(task_id)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        self.changed.emit()
        return result

    def _recovery_info(self, task_id):
        self._usable()
        self._active(task_id)
        return self.store.recovery(task_id)

    def approve_recovery(
        self, task_id, source_task_id, artifact_ids, *, evidence=True, file_ids=()
    ):
        """Trusted selection discloses only chosen drafts, files and bounded evidence."""
        require_owner_thread(self)
        self._usable()
        if (
            not isinstance(artifact_ids, (list, tuple))
            or len(artifact_ids) > MAX_RECOVERY_ARTIFACTS
            or any(not isinstance(value, str) for value in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)
            or type(evidence) is not bool
            or not isinstance(file_ids, (list, tuple))
            or len(file_ids) > MAX_RECOVERY_FILES
            or any(not isinstance(value, str) for value in file_ids)
            or len(set(file_ids)) != len(file_ids)
            or (not artifact_ids and not file_ids and not evidence)
        ):
            raise ValueError(
                "Select at most ten drafts, ten files and/or retained evidence"
            )
        if self.store.get(source_task_id).status not in TERMINAL_STATUSES:
            raise ValueError("Choose a finished or interrupted task")
        try:
            artifacts = [
                self.store.artifact(source_task_id, value) for value in artifact_ids
            ]
            for artifact in artifacts:
                _, size, digest, _ = artifact_input(
                    artifact.title,
                    artifact.content,
                    artifact.format,
                    artifact.source_events,
                )
                if size != artifact.byte_count or digest != artifact.sha256:
                    raise ValueError("Retained draft integrity check failed")
            snapshot = recovery_snapshot(
                self.store, self.connector_store, source_task_id, artifacts, evidence
            )
            effects = [
                (
                    source,
                    receipt,
                    self.store.artifact_digest(source, receipt.artifact_id),
                )
                for source in self.store.recovery_ancestors(source_task_id)
                for receipt in self.connector_store.list(source)
                if receipt.effect != "read"
            ]
            result = self.store.approve_recovery(
                task_id, source_task_id, artifacts, snapshot, effects, file_ids
            )
        except (sqlite3.Error, OSError):
            self._fatal()
            raise
        self.changed.emit()
        return result

    def deny_recovery(self, task_id):
        require_owner_thread(self)
        self._usable()
        try:
            self.store.deny_recovery(task_id)
        except (sqlite3.Error, OSError):
            self._fatal()
            raise
        self.changed.emit()

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

    def _workspace_run(self, task_id, method, *args):
        self._authorize(task_id, "workspace")
        try:
            result = getattr(self.workspace, method)(task_id, *args)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        self.changed.emit()
        return result

    @property
    def library(self):
        require_owner_thread(self)
        self._usable()
        if self._library is None:
            from services.file_library import FileLibrary

            self._library = FileLibrary(
                self._library_path, access_changed=self.changed.emit
            )
        return self._library

    def _library_run(self, task_id, method, folder_id=None, *args, **kwargs):
        self._usable()
        self._active(task_id)
        try:
            library = self.library
            if folder_id is not None:
                library.folder(folder_id, agent=True)
            if method == "folders":
                result = library.folders(**kwargs)
            elif method == "search":
                result = library.search(folder_id=folder_id, **kwargs)
            elif method == "begin":
                kwargs.pop("task", None)
                result = library.begin(task_id, folder_id, **kwargs)
            else:
                result = getattr(library, method)(folder_id, *args, **kwargs)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        if method not in ("folders", "list", "read", "begin", "search"):
            self.changed.emit()
        return result

    def _library_write(self, task_id, method, write_id, **args):
        self._usable()
        self._active(task_id)
        try:
            library = self.library
            item = library.write_info(task_id, write_id)
            library.folder(item["folder"], agent=True)
            result = getattr(library, method)(task_id, write_id, **args)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        if method == "commit":
            self.changed.emit()
        return result

    def _library_import_workspace(self, task_id, folder_id, path, revision):
        self._authorize(task_id, "workspace")
        library = self.library
        library.folder(folder_id, agent=True)
        # Validate the current revision before taking an immutable worker input.
        library.read(folder_id, path, revision, limit=1)
        row = library.row(folder_id, path)
        data = row["data"]
        if (
            len(data) > MAX_FILE_BYTES
            or hashlib.sha256(data).hexdigest() != row["sha256"]
        ):
            raise ValueError(
                "Import an intact library file of at most 8 MiB into a task"
            )
        name = f"library-{folder_id[:8]}-{hashlib.sha256(path.encode()).hexdigest()[:16]}-r{revision}-{Path(path).name[:64]}"
        try:
            return self._store_shared_file(task_id, name, data)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise

    def _library_save_workspace(
        self, task_id, folder_id, path, file_id, expected_revision=0
    ):
        self._authorize(task_id, "workspace")
        library = self.library
        library.folder(folder_id, agent=True)
        item = self.workspace.get(task_id, file_id)
        if item.status != "sealed":
            raise ValueError("Save a sealed workspace file")
        data = self.workspace.bytes(task_id, file_id)
        request_id = hashlib.sha256(
            f"{folder_id}:{path}:{file_id}:{expected_revision}".encode()
        ).hexdigest()
        import base64

        try:
            write = library.begin(
                task_id,
                folder_id,
                path,
                request_id,
                len(data),
                item.sha256,
                expected_revision,
            )
            if write["state"] == "building":
                for offset in range(0, len(data), 32768):
                    library.chunk(
                        task_id,
                        write["id"],
                        offset,
                        base64.b64encode(data[offset : offset + 32768]).decode(),
                    )
            result = library.commit(task_id, write["id"])
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        self.changed.emit()
        return result

    def _workspace_event(self, task_id, kind, payload):
        try:
            self.store.record_event(task_id, kind, payload)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise

    def _workspace_begin(self, task_id, name, media_type, byte_count, sha256):
        return self._workspace_run(
            task_id, "begin", name, media_type, byte_count, sha256
        )

    def _workspace_write(self, task_id, file_id, offset, data_base64):
        return self._workspace_run(task_id, "write", file_id, offset, data_base64)

    def _workspace_seal(self, task_id, file_id):
        return self._workspace_run(task_id, "seal", file_id)

    def _workspace_list(self, task_id):
        return self._workspace_run(task_id, "list")

    def _workspace_read(self, task_id, file_id, offset=0, limit=32768):
        return self._workspace_run(task_id, "read", file_id, offset, limit)

    def _workspace_stage_extension(self, task_id, manifest_id):
        return self._workspace_run(task_id, "stage_extension", manifest_id)

    def _workspace_export(self, task_id, file_id, destination):
        grant = self._authorize(task_id, "workspace")
        grant.check("download", destination=destination)
        item = self.workspace.get(task_id, file_id)
        if (
            item.status != "sealed"
            or item.expected_bytes > grant.scope.max_download_bytes
        ):
            raise ValueError("Export a sealed file within the approved download size")
        data = self.workspace.bytes(task_id, file_id)
        self._publish_workspace(
            task_id,
            item,
            data,
            destination,
            before_publish=lambda: grant.check("download", destination=destination),
        )
        return {
            "file_id": file_id,
            "destination": destination,
            "byte_count": item.expected_bytes,
            "sha256": item.sha256,
        }

    def _publish_workspace(
        self, task_id, item, data, destination, *, before_publish=lambda: None
    ):
        target = DownloadDestination(destination)
        try:
            fd = os.open(
                "payload",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target.stage_fd,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            self._workspace_event(
                task_id,
                "workspace.export_requested",
                {"file_id": item.file_id, "sha256": item.sha256},
            )
            before_publish()
            target.publish(item.expected_bytes, MAX_FILE_BYTES)
        finally:
            target.close()
        self._workspace_event(
            task_id,
            "workspace.exported",
            {"file_id": item.file_id, "sha256": item.sha256},
        )
        self.changed.emit()

    def export_workspace_file(self, task_id, file_id, destination):
        """Trusted user picker only; no task authority is needed for retained files."""
        require_owner_thread(self)
        self._usable()
        item = self.workspace.get(task_id, file_id)
        if item.status != "sealed":
            raise ValueError("Seal the file before exporting")
        self._publish_workspace(
            task_id, item, self.workspace.bytes(task_id, file_id), destination
        )

    def import_workspace_file(self, task_id, path):
        """Trusted user file picker only; no caller-selected path broker endpoint."""
        require_owner_thread(self)
        grant = self._authorize(task_id, "workspace")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ValueError("Share a regular file of at most 8 MiB")
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) != info.st_size:
            raise ValueError("The file changed while being shared")
        grant.check("workspace")
        try:
            return self._store_shared_file(task_id, path, data)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise

    def _store_shared_file(self, task_id, path, data):
        item = self.workspace.begin(
            task_id,
            Path(path).name,
            "application/octet-stream",
            len(data),
            hashlib.sha256(data).hexdigest(),
        )
        import base64

        if item.status == "building":
            for offset in range(0, len(data), 32768):
                self.workspace.write(
                    task_id,
                    item.file_id,
                    offset,
                    base64.b64encode(data[offset : offset + 32768]).decode("ascii"),
                )
        self.workspace.seal(task_id, item.file_id)
        with self.store._db:
            self.store._db.execute(
                "UPDATE workspace_files SET source='user_shared' WHERE file_id=?",
                (item.file_id,),
            )
            self.store._append(
                task_id,
                "workspace.file_shared",
                {"file_id": item.file_id, "byte_count": len(data)},
            )
        self.changed.emit()
        return self.workspace.get(task_id, item.file_id)

    def delete_workspace_file(self, task_id, file_id):
        require_owner_thread(self)
        self._usable()
        try:
            self.workers.deleting(task_id, file_id)
            self.recipes.cancel_task(task_id, file_id)
            self.workspace.delete(task_id, file_id)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        self.changed.emit()

    def _worker_cancel(self, task_id, request_id):
        self._usable()
        self._active(task_id)
        return self._worker_action(self.workers.cancel, task_id, request_id)

    def _recipe_status(self, task_id, request_id):
        self._authorize(task_id, "connector")
        return self._worker_action(self.recipes.get, task_id, request_id)

    def _layout_action(self, method, *args):
        self._usable()
        try:
            return method(*args)
        except TaskPermissionError:
            raise
        except (OSError, sqlite3.Error):
            self._fatal(defer_broker_close=True)
            raise

    def _panel_capabilities(self, task_id):
        from data_panel_format import panel_capabilities

        self._usable()
        self._active(task_id)
        return {"available": self.data_panels.available, **panel_capabilities()}

    def _start_page_capabilities(self, task_id):
        from start_page_format import capabilities

        self._usable()
        self._active(task_id)
        return {"available": self.start_pages.available, **capabilities()}

    def _layout_capabilities(self, task_id):
        from layout_format import layout_capabilities

        self._usable()
        self._active(task_id)
        return {"available": self.layouts.available, **layout_capabilities()}

    def _recipe_capabilities(self, task_id):
        import copy

        from services.connector_recipes import (
            RECIPE_CONTINUATION_EXAMPLE,
            RECIPE_EXAMPLE,
            RECIPE_HEADERS_EXAMPLE,
            RECIPE_IDENTITY_EXAMPLE,
            RECIPE_NESTED_EXAMPLE,
            RECIPE_OAUTH_EXAMPLE,
            RECIPE_PAGINATED_EXAMPLE,
            RECIPE_PLAN_EXAMPLE,
            RECIPE_QUERY_EXAMPLE,
            RECIPE_WRITE_EXAMPLE,
            STATIC_HEADER_FORMATS,
        )

        self._usable()
        self._active(task_id)
        return {
            "available": self.recipes.connections is not None
            and self.recipes.connections.recipes is not None,
            "version": 8,
            "supported_versions": [1, 2, 3, 4, 5, 6, 7, 8],
            "continuation_reads": "version 8 may pin a separate POST continuation path/body with cursor-only bindings and a boolean has_more stop signal",
            "success_flags": "version 8 can review an exact boolean success flag for read responses and identity, including whether missing flags are accepted",
            "typed_reads": "version 7 read tools post a bounded body of typed string/integer/boolean inputs to one exact reviewed path; effect stays read, so no payload approval or operation header exists",
            "graphql_reads": "one fixed reviewed query document per tool; agents supply no query text and mutations, subscriptions and fragments are rejected",
            "application_errors": "declare an errors pointer/code/message so an HTTP-200 service failure is reported instead of empty fields; GraphQL errors are reported, never silently returned as partial data",
            "identity_method": "identity may use one reviewed POST with a literal bounded body",
            "identity_label_alternatives": "ordered reviewed pointers; missing/null/empty only; explicit subject pointer may label an account",
            "request_headers": dict(STATIC_HEADER_FORMATS),
            "authentication": ["bearer", "oauth_pkce"],
            "destinations": {
                "resource": "one exact resource ID",
                "collection": "account",
                "email": "email:recipient@example.com (one ASCII mailbox)",
            },
            "effects": ["read", "write", "send"],
            "write_approval": "exact_payload_every_action",
            "installation_approval": "exact_recipe",
            "limits": {
                "recipe_bytes": 16384,
                "tools": 10,
                "response_fields": 10,
                "proposals_per_task": 20,
                "pending_per_task": 3,
                "oauth_scopes": 20,
                "pagination_pages": 5,
                "pagination_cursor_bytes": 512,
                "request_fields": 10,
                "payload_bytes": 16384,
                "template_depth": 8,
                "template_nodes": 100,
                "template_items": 20,
                "plan_steps": 5,
                "request_headers": 4,
                "identity_label_alternatives": 3,
                "read_inputs": 8,
                "input_string_length": 512,
                "graphql_document_bytes": 4096,
                "graphql_variables": 8,
                "graphql_tokens": 1024,
                "graphql_depth": 32,
            },
            "example": copy.deepcopy(RECIPE_EXAMPLE),
            "oauth_example": copy.deepcopy(RECIPE_OAUTH_EXAMPLE),
            "pagination_example": copy.deepcopy(RECIPE_PAGINATED_EXAMPLE),
            "write_example": copy.deepcopy(RECIPE_WRITE_EXAMPLE),
            "nested_example": copy.deepcopy(RECIPE_NESTED_EXAMPLE),
            "plan_example": copy.deepcopy(RECIPE_PLAN_EXAMPLE),
            "headers_example": copy.deepcopy(RECIPE_HEADERS_EXAMPLE),
            "identity_example": copy.deepcopy(RECIPE_IDENTITY_EXAMPLE),
            "query_example": copy.deepcopy(RECIPE_QUERY_EXAMPLE),
            "continuation_example": copy.deepcopy(RECIPE_CONTINUATION_EXAMPLE),
        }

    def _recipe_list(self, task_id):
        self._authorize(task_id, "connector")
        return self._worker_action(self.recipes.list, task_id)

    def resolve_recipe(self, task_id, request_id, *, approve=False):
        """Trusted popup/management UI only; agents cannot install recipes."""
        return self._worker_action(
            self.recipes.resolve, task_id, request_id, approve=approve
        )

    def _worker_capabilities(self, task_id):
        self._usable()
        self._active(task_id)
        return self._worker_action(self.workers.capabilities)

    def check_worker_setup(self):
        """Trusted UI retries a fixed probe; agents only read its cached outcome."""
        return self._worker_action(self.workers.capabilities, recheck=True)

    def cancel_worker(self, task_id, request_id):
        """Trusted UI may stop a retained task's running worker."""
        require_owner_thread(self)
        self._usable()
        return self._worker_action(self.workers.cancel, task_id, request_id)

    def _worker_action(self, operation, *args, **kwargs):
        require_owner_thread(self)
        self._usable()
        try:
            return operation(*args, **kwargs)
        except TaskPermissionError:
            raise
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise

    def resolve_worker(self, task_id, request_id, *, approve=False, remember=False):
        """Trusted UI package decision; deliberately absent from the broker."""
        return self._worker_action(
            self.workers.resolve,
            task_id,
            request_id,
            approve=approve,
            remember=remember,
        )

    def clear_app_results(self, task_id, request_ids=None):
        """Only trusted browser UI clears retained results; clients cannot erase evidence."""
        require_owner_thread(self)
        self._usable()
        try:
            receipts = {r.request_id: r for r in self.connector_store.list(task_id)}
            selected = tuple(receipts) if request_ids is None else request_ids
            parents = set()
            if (
                isinstance(selected, (list, tuple))
                and len(selected) <= 50
                and all(isinstance(item, str) and item in receipts for item in selected)
                and len(set(selected)) == len(selected)
            ):
                expanded = set(selected)
                for request_id in selected:
                    if self.app_plans.parent(task_id, request_id):
                        parents.add(request_id)
                        expanded.update(
                            row["child_request_id"]
                            for row in self.app_plans.children(task_id, request_id)
                        )
                    link = self.app_plans.link(task_id, request_id)
                    if link is not None:
                        parents.add(link["parent_request_id"])
                request_ids = tuple(expanded)
            count = self.connector_store.clear_results(task_id, request_ids)
            for parent_id in parents:
                self.app_plans.cancel(task_id, parent_id)
        except (sqlite3.Error, OSError, RuntimeError):
            self._fatal()
            raise
        self.changed.emit()
        return count

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
        self,
        task_id,
        connector,
        tool,
        request_id,
        destination,
        artifact_id=None,
        inputs=None,
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
        # Typed read inputs are validated by the trusted tool definition before
        # any receipt exists; they never widen the reviewed effect or destination.
        resolved_inputs = (
            definition.validate_inputs(inputs)
            if definition.validate_inputs is not None
            else None
        )
        if inputs is not None and resolved_inputs is None:
            raise ValueError("This app tool does not accept typed inputs")
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
            inputs=resolved_inputs,
        )
        fingerprint = request_fingerprint(
            connector, tool, definition.effect, destination, artifact, resolved_inputs
        )
        try:
            existing = self.connector_store.get(task_id, request_id)
        except KeyError:
            existing = None
        if existing:
            if existing.fingerprint != fingerprint:
                raise ValueError("Request ID is already bound to different inputs")
            return existing  # Includes uncertain writes: never dispatch a duplicate.
        if request.effect != "read":
            try:
                for historical in self.store.recovery_effects(task_id, request):
                    previous = self.connector_store.get(
                        historical["source_task_id"], historical["request_id"]
                    )
                    if (
                        previous.state not in {"cancelled", "not_applied"}
                        or (historical["source_task_id"], historical["request_id"])
                        in self._connector_jobs
                    ):
                        self._deny(task_id, "recovery_write_already_recorded")
            except TaskPermissionError:
                raise
            except (sqlite3.Error, OSError):
                self._fatal(defer_broker_close=True)
                raise
        self._app_capacity(task_id)
        needed = len(definition.plan.steps) + 1 if definition.plan is not None else 1
        if (
            len(self.connector_store.list(task_id))
            + self.app_plans.reserved(task_id)
            + needed
            > 50
        ):
            raise ValueError("Reserve enough app receipts for every plan step")
        self._app_grant(task_id, request, consume=True)
        try:
            receipt = self.connector_store.create(task_id, request, fingerprint)
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        if definition.plan is not None:
            try:
                self.app_plans.start(task_id, receipt, definition)
            except (sqlite3.Error, OSError):
                self._fatal(defer_broker_close=True)
                raise
        else:
            self._queue_app_job(
                task_id,
                definition,
                request,
                grant,
                preparing=definition.prepare_review is not None,
            )
        self.changed.emit()
        return receipt

    def _app_capacity(self, task_id):
        if len(self._connector_jobs) >= 4 or any(
            owner == task_id for owner, _ in self._connector_jobs
        ):
            raise ValueError("An app operation is still running; wait for its result")

    def _queue_app_job(
        self,
        task_id,
        definition,
        request,
        grant,
        *,
        probe=False,
        probe_request=None,
        preparing=False,
    ):
        job = ConnectorJob(
            definition,
            request,
            grant,
            lambda reply: self._app_finished(task_id, request.request_id, job, reply),
            self,
            probe=probe,
            probe_request=probe_request,
            preparing=preparing,
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
            if not job.preparing and not job.probe:
                self.app_plans.check_review(task_id, job.request)
        except Exception:
            self._app_finished(task_id, request_id, job, (None, True))
            return
        try:
            self.connector_store.update(
                task_id,
                request_id,
                "requested"
                if job.preparing
                else "reconciling"
                if job.probe
                else "dispatched",
                reason="preparing_review" if job.preparing else "",
            )
            self.store.transition(task_id, "running")
            self.app_plans.changed(task_id, request_id, advance=True)
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
            if job.preparing:
                try:
                    if (
                        isinstance(outcome, ConnectorReview)
                        and not job.cancelled.is_set()
                        and outcome.after == job.request.artifact.content
                    ):
                        self._app_grant(task_id, job.request)
                        self.app_reviews.create(task_id, request_id, outcome)
                        self.connector_store.update(
                            task_id, request_id, "requested", reason="awaiting_review"
                        )
                    else:
                        self.connector_store.update(
                            task_id,
                            request_id,
                            "cancelled",
                            reason="review_preparation_failed",
                        )
                except TaskPermissionError:
                    try:
                        self.connector_store.update(
                            task_id,
                            request_id,
                            "cancelled",
                            reason="review_authority_ended",
                        )
                    except Exception:
                        self._fatal()
                except Exception:
                    self._fatal()
                if not self.failure:
                    try:
                        self.app_plans.changed(task_id, request_id, advance=True)
                    except Exception:
                        self._fatal()
                self.changed.emit()
                job.deleteLater()
                return
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
                if job.probe_request is not None and state == "not_applied":
                    source = self.connector_store.probe_source(task_id, request_id)
                    if source in self._connector_jobs:
                        state, reason = "uncertain", "original_operation_running"
                update = (
                    self.connector_store.finish_probe
                    if job.probe_request is not None
                    else self.connector_store.update
                )
                update(
                    task_id,
                    request_id,
                    state,
                    reference=outcome.reference if outcome else "",
                    text=outcome.text if outcome else "",
                    reason=reason,
                )
                self.app_plans.changed(task_id, request_id, advance=not job.probe)
            except Exception:
                self._fatal()
            self.changed.emit()
        job.deleteLater()

    def _app_status(self, task_id, request_id):
        self._usable()
        receipt = self.connector_store.get(task_id, request_id)
        self._app_grant(task_id, receipt, consume=True)
        return receipt

    def resolve_app_review(self, task_id, request_id, *, approve=False):
        """Trusted desktop UI only; no broker command can authorize a proposal."""
        require_owner_thread(self)
        self._usable()
        try:
            return self._resolve_app_review(task_id, request_id, approve=approve)
        except TaskPermissionError:
            raise
        except (sqlite3.Error, OSError):
            self._fatal()
            raise

    def _resolve_app_review(self, task_id, request_id, *, approve):
        receipt = self.connector_store.get(task_id, request_id)
        review = self.app_reviews.get(task_id, request_id)
        if review.status != "pending" or receipt.state != "requested":
            raise ValueError("This review is no longer pending")
        if not approve:
            self.app_reviews.resolve(task_id, request_id, "denied")
            self.connector_store.update(
                task_id, request_id, "cancelled", reason="review_denied"
            )
            self.app_plans.changed(task_id, request_id)
            self.changed.emit()
            return
        definition = self.connectors.get(receipt.connector, receipt.tool)
        definition = self.app_plans.definition(task_id, request_id, definition)
        if definition.prepare_review is None:
            raise ValueError("This tool does not accept reviewed execution")
        artifact = self.store.artifact(task_id, receipt.artifact_id)
        request = ConnectorRequest(
            request_id,
            receipt.connector,
            receipt.tool,
            receipt.effect,
            receipt.destination,
            artifact,
            receipt.operation_id,
            review.payload(),
        )
        if (
            artifact.content != review.after_text
            or request_fingerprint(
                request.connector,
                request.tool,
                request.effect,
                request.destination,
                artifact,
            )
            != receipt.fingerprint
        ):
            raise ValueError("The proposed draft no longer matches the request")
        grant = self._app_grant(task_id, request)
        self.app_plans.check_review(task_id, request)
        grant.check("artifact")
        self._app_capacity(task_id)
        self._app_grant(task_id, request, consume=True)
        self.app_reviews.resolve(task_id, request_id, "approved")
        self._queue_app_job(task_id, definition, request, grant)
        self.changed.emit()

    def _app_timeout(self, task_id, request_id, job):
        job.cancelled.set()
        if self._closed or self.failure:
            return
        try:
            state = (
                "cancelled"
                if job.preparing
                else "uncertain"
                if job.probe or job.request.effect != "read"
                else "failed"
            )
            self.connector_store.update(
                task_id, request_id, state, reason="connector_timeout"
            )
            self.app_plans.changed(task_id, request_id)
        except Exception:
            self._fatal()
        self.changed.emit()

    def _app_cancel(self, task_id, request_id):
        self._usable()
        self._active(task_id)
        return self._cancel_app_job(task_id, request_id)

    def _cancel_app_job(self, task_id, request_id):
        receipt = self.connector_store.get(task_id, request_id)
        if self.app_plans.parent(task_id, request_id):
            self.app_plans.cancel(task_id, request_id)
            return self.connector_store.get(task_id, request_id)
        job = self._connector_jobs.get((task_id, request_id))
        if job:
            job.cancelled.set()
            job.timer.stop()
            state = (
                "cancelled"
                if job.preparing
                else "uncertain"
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
        else:
            try:
                review = self.app_reviews.get(task_id, request_id)
            except KeyError:
                review = None
            if review is not None and review.status == "pending":
                self.app_reviews.resolve(task_id, request_id, "cancelled")
                receipt = self.connector_store.update(
                    task_id, request_id, "cancelled", reason="review_cancelled"
                )
                self.changed.emit()
        self.app_plans.changed(task_id, request_id)
        return receipt

    def _cancel_app_jobs(self, task_id):
        for owner, request_id in tuple(self._connector_jobs):
            if owner == task_id:
                self._cancel_app_job(task_id, request_id)
        for review in self.app_reviews.pending(task_id):
            self._cancel_app_job(task_id, review.request_id)

    def _app_reconcile(self, task_id, request_id):
        self._usable()
        receipt = self.connector_store.get(task_id, request_id)
        source = self.connector_store.probe_source(task_id, request_id)
        if source is not None:
            return self._app_reconcile_history(
                task_id, source[1], request_id, retry=True
            )
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

    def _history_receipt(self, task_id, source_request_id):
        """Only receipts explicitly disclosed by the user are probe targets."""
        recovery = self.store.recovery(task_id)
        context = recovery.get("context", {})
        if recovery["status"] != "approved" or not context.get("evidence_included"):
            self._deny(task_id, "recovery_receipt_not_shared")
        source = recovery["source_task_id"]
        if not any(
            r["task_id"] == source and r["request_id"] == source_request_id
            for r in context.get("app_receipts", [])
        ):
            self._deny(task_id, "recovery_receipt_not_shared")
        return self.connector_store.get(source, source_request_id)

    def _app_reconcile_history(
        self, task_id, source_request_id, request_id, *, retry=False
    ):
        """Probe one shared archived receipt under fresh read-only app authority."""
        self._usable()
        self._active(task_id)
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", request_id
        ):
            raise ValueError(
                "Provide a stable request ID of at most 64 letters/digits/_/-"
            )
        try:
            original = self._history_receipt(task_id, source_request_id)
            if self.connector_store.probe_source(original.task_id, original.request_id):
                raise ValueError(
                    "Choose the original task's receipt to check its outcome"
                )
            definition = self.connectors.get(original.connector, original.tool)
            if definition.reconcile is None:
                raise ValueError(
                    "This connector has no read-only reconciliation method"
                )
            request = ConnectorRequest(
                request_id,
                original.connector,
                original.tool,
                "read",
                original.destination,
                None,
                uuid4().hex,
            )
            grant = self._app_grant(task_id, request)
            source = (original.task_id, original.request_id)
            fingerprint = request_fingerprint(
                request.connector, request.tool, "read", request.destination, None
            )
            try:
                receipt = self.connector_store.get(task_id, request_id)
            except KeyError:
                receipt = None
            if receipt is not None:
                if (
                    receipt.fingerprint != fingerprint
                    or self.connector_store.probe_source(task_id, request_id) != source
                ):
                    raise ValueError("Request ID is already bound to different inputs")
                if not retry:
                    return receipt
                if receipt.state != "uncertain":
                    raise ValueError("Only an uncertain probe needs reconciliation")
            if original.state != "uncertain":
                raise ValueError(
                    "Only an uncertain archived operation needs reconciliation"
                )
            self._app_capacity(task_id)
            self._app_grant(task_id, request, consume=True)
            receipt = (
                self.connector_store.update(task_id, request_id, "reconciling")
                if retry
                else self.connector_store.create(
                    task_id, request, fingerprint, source=source
                )
            )
        except TaskPermissionError:
            raise
        except (sqlite3.Error, OSError):
            self._fatal(defer_broker_close=True)
            raise
        self._queue_app_job(
            task_id,
            definition,
            request,
            grant,
            probe=True,
            probe_request=ConnectorProbe(
                original.request_id, original.destination, original.operation_id
            ),
        )
        self.changed.emit()
        return receipt

    def client(self, task_id):
        """Trusted controller issues a handle bound to one task, on the Qt thread."""
        require_owner_thread(self)
        self._usable()
        self._active(task_id)
        return TaskClient(task_id, self.broker)

    def external_connection(self, task_id, connected):
        """Trusted transport reports connection ownership; never an agent command."""
        require_owner_thread(self)
        if connected:
            self.external_tasks.add(task_id)
        else:
            self.external_tasks.discard(task_id)
        self.changed.emit()

    def _read_messages(self, task_id, after=0, limit=16):
        self._usable()
        page = self.messages.read(task_id, after, limit)
        if self.messages.acknowledge(task_id, page):
            self.changed.emit()
        return page

    def _wait_messages(self, task_id, after=0, timeout_ms=25000):
        """Long-poll for the user's next message; replaces busy polling."""
        self._usable()
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= MESSAGE_WAIT_MS:
            raise ValueError(f"Wait from 1 to {MESSAGE_WAIT_MS} milliseconds")
        if task_id in self._message_waits:
            raise ValueError("This task is already waiting for a message")

        def pending():
            page = self.messages.read(task_id, after, 16)
            return any(message["role"] == "user" for message in page["messages"])

        if pending():
            return {**self._read_messages(task_id, after, 16), "waited": "message"}
        result = Future()
        timer = QTimer(self)
        timer.setSingleShot(True)

        def finish(reason):
            if result.done():
                return
            try:
                page = self._read_messages(task_id, after, 16)
                result.set_result({**page, "waited": reason})
            except Exception as error:
                result.set_exception(error)

        def sent(sent_task, _message):
            if sent_task == task_id:
                finish("message")

        def changed():
            if self._closed or self.failure:
                finish("unavailable")
            elif self.store.get(task_id).status not in ACTIVE_STATUSES:
                finish("task_ended")

        def cleanup(_):
            self._message_waits.discard(task_id)
            timer.stop()
            timer.deleteLater()
            self.user_message_sent.disconnect(sent)
            self.changed.disconnect(changed)

        self._message_waits.add(task_id)
        self.user_message_sent.connect(sent)
        self.changed.connect(changed)
        timer.timeout.connect(lambda: finish("timed_out"))
        result.add_done_callback(cleanup)
        timer.start(timeout_ms)
        return result

    def _reply_message(self, task_id, message_id, text, revision=0, state="complete"):
        self._usable()
        result = self.messages.post_agent(task_id, message_id, text, revision, state)
        self.changed.emit()
        return result

    def send_message(self, task_id, text):
        """Trusted UI shares only explicitly entered text with this task's agent."""
        require_owner_thread(self)
        self._usable()
        if task_id not in self.external_tasks:
            raise ValueError("External agent is not connected")
        result = self.messages.post_user(task_id, text)
        self.changed.emit()
        self.user_message_sent.emit(task_id, result)
        return result

    def clear_messages(self, task_id):
        require_owner_thread(self)
        self._usable()
        self.messages.clear(task_id)
        self.conversation_cleared.emit(task_id)
        self.changed.emit()

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
        result = report()
        if is_dataclass(result) and hasattr(result, "permission_mode"):
            result = replace(
                result,
                permission_mode="auto_approve"
                if self.auto_approve_requests
                else "review",
            )
        return result

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

    def _request_control(self, task_id, request_id, selection="current_tab"):
        from services.live_control import request

        return request(self, task_id, request_id, selection)

    def _control_status(self, task_id, request_id):
        self._active(task_id)
        record = self.control_requests.get((task_id, request_id))
        if record is None:
            raise AgentRequestError(
                "control_request_unknown",
                "Request control before polling this request_id.",
            )
        if record.state == "approved" and not any(
            owner == task_id and grant == record.grant_id
            for owner, grant, _ in self.live_tabs.offers.values()
        ):
            record.state = "returned"
        return record.payload()

    def set_auto_approval(self, enabled):
        """Trusted desktop opt-in for this browser run; never an agent command."""
        require_owner_thread(self)
        self._usable()
        if type(enabled) is not bool:
            raise ValueError("Provide a boolean auto-approval choice")
        try:
            for task in self.store.list_tasks():
                if task.status in ACTIVE_STATUSES:
                    self.store.record_event(
                        task.task_id,
                        "permission.mode_changed",
                        {"auto_approve": enabled},
                    )
        except (sqlite3.Error, OSError):
            self.auto_approve_requests = False
            self._fatal()
            raise
        self.auto_approve_requests = enabled
        self.changed.emit()

    def _request_grant(self, task_id, scope):
        self._active(task_id)
        scope = TaskScope.parse(scope)
        try:
            self.connectors.check_scope(scope)
        except ValueError:
            raise AgentRequestError(
                "invalid_app_scope",
                "Use configured tools from app.list_tools with their exact effects/destinations. Tools requiring a draft also need the artifact action.",
                "connector_permissions",
            ) from None
        recovery = self.store.recovery(task_id)
        if recovery["status"] == "requested":
            raise AgentRequestError(
                "recovery_pending",
                "Wait for task.recovery to finish before requesting access.",
            )
        if scope.profile != "private":
            if self.profiles is None:
                raise AgentRequestError(
                    "profile_unavailable",
                    "Named profiles need the desktop browser. Use profile='private' for an isolated session.",
                    "profile",
                )
            try:
                self.profiles.validate_task_profile(scope.profile)
            except ValueError:
                raise AgentRequestError(
                    "profile_unavailable",
                    "The selected profile is unavailable. Read browser.context for its exact selector, or use profile='private'.",
                    "profile",
                ) from None
        current = self.store.grants(task_id)
        for existing in current:
            if (
                existing.status in ("requested", "approved")
                and TaskScope.parse(existing.scope) == scope
            ):
                return existing
        for existing in current:
            if existing.status == "requested":
                raise AgentRequestError(
                    "grant_request_pending",
                    "A different permission request is pending. Poll grant.list for its decision before requesting another scope.",
                )
        result = self.store.request_grant(task_id, scope.payload())
        try:
            # Sharing archived data and approving current access are separate decisions.
            # Even a remembered permission must not silently authorize a follow-up.
            rules = (
                [] if recovery["status"] == "approved" else self.store.approval_rules()
            )
            profile_key = (
                self._approval_profile_key(scope)
                if any(r.mode == "profile" for r in rules)
                else ""
            )
            rule = next((r for r in rules if r.covers(scope, profile_key)), None)
            if self.auto_approve_requests:
                automatic = {}
            elif rule is not None:
                automatic = {"_rule": rule}
            elif recovery["status"] != "approved" and self.account_access.covers(scope):
                automatic = {"_account_access": True}
            else:
                automatic = None
            if automatic is not None:
                try:
                    approved = self.approve_grant(task_id, result.grant_id, **automatic)
                except ValueError:
                    # An unavailable profile is not journal damage. The request
                    # stays pending for review; approve_grant itself freezes
                    # authority when a failure leaves unsafe state behind.
                    if self.failure:
                        raise
                    self.store.record_event(
                        task_id,
                        "permission.auto_approval_failed",
                        {"grant_id": result.grant_id},
                    )
                else:
                    result = approved
                    if self.auto_approve_requests:
                        self.store.record_event(
                            task_id,
                            "permission.auto_approved",
                            {"grant_id": result.grant_id, "mode": "yolo"},
                        )
        except (OSError, sqlite3.Error, ValueError):
            self._fatal()
            raise
        self.changed.emit()
        return result

    def _approval_profile_key(self, scope, *, create=False):
        if scope.profile == "private" or self.profiles is None:
            return ""
        return self.profiles.approval_profile_key(scope.profile, create=create)

    def approve_grant(
        self, task_id, grant_id, *, remember="once", _rule=None, _account_access=False
    ):
        """Trusted Qt UI method, deliberately absent from the command broker."""
        require_owner_thread(self)
        self._usable()
        self._active(task_id)
        record = self.store.grant(task_id, grant_id)
        scope = TaskScope.parse(record.scope)
        if record.status != "requested":
            raise ValueError("Only a pending request can be approved")
        self.connectors.check_scope(scope)
        if _account_access and (
            remember != "once"
            or _rule is not None
            or not self.account_access.covers(scope)
        ):
            raise ValueError("Account settings do not cover this request")
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
                account_access=_account_access,
            )
            previous = self._grants.get(task_id)
            if previous is not None:
                previous[1].end("replaced")
                self._discard_sessions(task_id)
                if existing:
                    self.profiles.reserve_task_profile(task_id, scope.profile)
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
        self.live_tabs.activate(task_id)
        self.changed.emit()
        return record

    def set_connector_access(self, connector, group, enabled):
        """Trusted settings switch; unavailable to agents and connector code."""
        require_owner_thread(self)
        self._usable()
        try:
            pairs = self.account_access.set(connector, group, enabled)
            if not enabled:

                def affected(permissions):
                    return any(
                        p["connector"] == connector
                        and (p["tool"], p["effect"]) in pairs
                        for p in permissions
                    )

                for rule in self.store.approval_rules():
                    if affected(rule.scope["connector_permissions"]):
                        self.forget_approval_rule(rule.rule_id)
                for task_id, (grant_id, live) in list(self._grants.items()):
                    if any(
                        p.connector == connector and (p.tool, p.effect) in pairs
                        for p in live.scope.connector_permissions
                    ):
                        self.revoke_grant(task_id, grant_id)
            else:
                for task in self.store.list_tasks():
                    if (
                        task.status not in ACTIVE_STATUSES
                        or self.store.recovery(task.task_id)["status"] == "approved"
                    ):
                        continue
                    for grant in self.store.grants(task.task_id):
                        if grant.status == "requested" and self.account_access.covers(
                            TaskScope.parse(grant.scope)
                        ):
                            self.approve_grant(
                                task.task_id, grant.grant_id, _account_access=True
                            )
        except (sqlite3.Error, OSError):
            self._fatal()
            raise
        self.changed.emit()

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
        self.live_tabs.discard(task_id)
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
        try:
            self.account_access.forget(connector)
        except (OSError, sqlite3.Error):
            self._fatal()
            raise
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

    def _open_session(self, task_id, profile="private", mode=None):
        if not isinstance(profile, str):
            self._deny(task_id, "profile_out_of_scope")
        if mode is None:
            live = self._grants.get(task_id)
            mode = "live" if live and "live" in live[1].scope.actions else "restricted"
        if mode not in ("restricted", "live"):
            raise AgentRequestError(
                "invalid_browsing_mode",
                "Use mode=restricted for static retrieval or mode=live for normal websites.",
                "mode",
            )
        grant = self._authorize(task_id, "open", profile=profile)
        if mode == "live":
            grant.check("live", profile=profile)
            grant.check("read")
        if (
            sum(owner == task_id for owner in self._sessions.values())
            >= grant.scope.max_sessions
        ):
            self._deny(task_id, "session_budget_exceeded")
        self.store.record_event(task_id, "session.open_requested", {"profile": profile})
        snapshot = (
            self.backend.open_session(task_id, grant=grant, profile=profile, mode=mode)
            if mode == "live"
            else self.backend.open_session(task_id, grant=grant, profile=profile)
        )
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
            if getattr(snapshot, "mode", "restricted") == "live":
                grant = self._grants.get(snapshot.task_id)
                try:
                    if grant is None:
                        raise TaskPermissionError("approval_required")
                    grant[1].check("read", url=snapshot.url)
                except TaskPermissionError:
                    view = self.backend.live_view(snapshot.session_id)
                    if view is not None:
                        self.live_tabs.release_id(view.agent_tab_id)
                    else:
                        QTimer.singleShot(
                            0,
                            lambda: (
                                self._close_session(
                                    snapshot.task_id, snapshot.session_id
                                )
                                if snapshot.session_id in self._sessions
                                else None
                            ),
                        )
                    return
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
        grant = self._authorize(task_id, "inspect")
        snapshot = self.backend.inspect(session_id)
        grant.check("inspect", url=snapshot.url or "about:blank")
        return snapshot

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

    def _capture_page(self, task_id, session_id):
        self._owned(task_id, session_id)
        grant = self._authorize(task_id, "visual")
        grant.check("read")
        self.store.record_event(
            task_id, "page.capture_requested", {"session_id": session_id}
        )
        source = self.backend.capture_page(session_id)
        return self._read_result(
            task_id,
            session_id,
            grant,
            source,
            "page.captured",
            lambda evidence: {
                key: evidence[key]
                for key in (
                    "session_id",
                    "revision",
                    "width",
                    "height",
                    "viewport_clipped",
                    "source",
                    "limitations",
                )
            },
        )

    def _computer_page(self, task_id, session_id, capture_id, operation, values):
        from services.computer_input import arguments

        values = arguments(operation, values)
        grant = self._action_authority(task_id, session_id, "computer")
        grant.check("visual")
        grant.check("live")
        action_id = uuid4().hex
        return self._start_action(
            task_id,
            session_id,
            grant,
            action_id,
            {
                "action_id": action_id,
                "session_id": session_id,
                "capture_id": capture_id,
                "operation": operation,
            },
            lambda: self.backend.computer(
                session_id, action_id, capture_id, operation, values
            ),
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
        steps=None,
    ):
        arguments = action_arguments(operation, text=text, index=index, steps=steps)
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
            lambda value: dict(value) if isinstance(value, dict) else asdict(value),
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
        if getattr(view, "browser_window", None) is not None:
            window = view.browser_window
            window._switch_tab(window.tabs.index(view))
            window.show()
            window.raise_()
            window.activateWindow()
            return {
                "session_id": session_id,
                "watching": True,
                "control_owner": "agent",
                "mode": "live",
            }
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
        if getattr(self.backend.inspect(session_id), "mode", "restricted") == "live":
            view = self.backend.live_view(session_id)
            if view is not None:
                self.live_tabs.offers.pop(view.agent_tab_id, None)
        self.backend.close_session(session_id)
        self.store.record_event(task_id, "session.closed", {"session_id": session_id})
        self.changed.emit()

    def _finish_task(self, task_id, status="completed"):
        self._active(task_id)
        for (owner, _), request in self.control_requests.items():
            if owner == task_id and request.state == "requested":
                request.state = "cancelled"
        if status not in TERMINAL_STATUSES:
            raise ValueError("Choose a terminal task status")
        if self._library is not None:
            try:
                self._library.cancel_task(task_id)
            except (sqlite3.Error, OSError):
                self._fatal(defer_broker_close=True)
                raise
        live = self._grants.pop(task_id, None)
        if live:
            live[1].end(status)
        self.workers.cancel_task(task_id)
        self.recipes.cancel_task(task_id)
        self.layouts.cancel_task(task_id)
        self.data_panels.cancel_task(task_id, interrupted=status == "interrupted")
        self.start_pages.cancel_task(task_id, interrupted=status == "interrupted")
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
        self.live_tabs.discard(task_id)
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
            if reason in ("expired", "disconnected")
            else "revoked"
            if reason == "revoked"
            else "exhausted"
        )
        try:
            self.store.resolve_grant(task_id, grant_id, status, reason=reason)
            if status == "revoked":
                self._finish_task(task_id, "cancelled")
            else:
                # Expiry and spent budgets end only this grant; the task (and a
                # bound conversation) may request fresh access.
                self.workers.cancel_task(task_id, reason="grant_" + status)
                self._cancel_app_jobs(task_id)
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
        self.workers.stop_all()
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
        self.data_panels.account_reads.close()
        self.changed.emit()  # Clear native linked copies even when every task ended.
        for _, grant in self._grants.values():
            grant.end("shutdown")
        try:
            for task in self.store.list_tasks():
                if task.status in ACTIVE_STATUSES:
                    self._finish_task(task.task_id, "interrupted")
        finally:
            self.workers.close()
            if self._library is not None:
                self._library.close()
            self._grants.clear()
            self._sessions.clear()
            try:
                self.backend.close()
            finally:
                if self.profiles is not None:
                    self.profiles.release_task_profiles()
                self.store.close()
