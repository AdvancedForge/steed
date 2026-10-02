"""Trusted user approval and cancellation, outside the client command surface."""

import json
import sqlite3
from dataclasses import asdict

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from services.task_store import ACTIVE_STATUSES


class TaskDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Task Permissions")
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Requests open automatically for approval. Existing profiles support "
            "shared access; you can keep browsing while a task uses your sign-in. "
            "File transfers require exact file and site permission. Task scripts are unavailable. Revoking access cancels the task."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Task", "Task status", "Permission"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self.show_details)
        layout.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.panels = QTabWidget()
        self.panels.addTab(self.details, "Permissions")
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.panels.addTab(self.activity, "Activity")
        artifacts = QWidget()
        artifact_layout = QVBoxLayout(artifacts)
        artifact_notice = QLabel(
            "Agent-generated notes and drafts. Sources refer to recorded page "
            "inspections; their claims are not verified. Content is shown as plain text."
        )
        artifact_notice.setWordWrap(True)
        artifact_layout.addWidget(artifact_notice)
        self.artifacts = QComboBox()
        self.artifacts.currentIndexChanged.connect(self.show_artifact)
        artifact_layout.addWidget(self.artifacts)
        self.artifact_preview = QPlainTextEdit()
        self.artifact_preview.setReadOnly(True)
        artifact_layout.addWidget(self.artifact_preview)
        self.delete_artifact_button = QPushButton("Delete Selected Draft")
        self.delete_artifact_button.clicked.connect(self.delete_artifact)
        artifact_layout.addWidget(self.delete_artifact_button)
        self.panels.addTab(artifacts, "Artifacts")
        self.downloads = QTableWidget(0, 4)
        self.downloads.setHorizontalHeaderLabels(["File", "State", "Bytes", "Result"])
        self.downloads.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.downloads.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.downloads.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.downloads.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.panels.addTab(self.downloads, "Downloads")
        apps = QWidget()
        app_layout = QVBoxLayout(apps)
        app_notice = QLabel(
            "App receipts show observed outcomes. An uncertain write may have happened; "
            "reconcile it before considering a new request. Cancellation does not undo writes."
        )
        app_notice.setWordWrap(True)
        app_layout.addWidget(app_notice)
        self.apps = QTableWidget(0, 4)
        self.apps.setHorizontalHeaderLabels(
            ["Operation", "Destination", "State", "Request"]
        )
        self.apps.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.apps.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.apps.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.apps.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for column in (0, 2, 3):
            self.apps.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.apps.itemSelectionChanged.connect(self.show_app)
        app_layout.addWidget(self.apps)
        self.app_preview = QPlainTextEdit()
        self.app_preview.setReadOnly(True)
        app_layout.addWidget(self.app_preview)
        app_buttons = QHBoxLayout()
        self.cancel_app_button = QPushButton("Cancel App Operation")
        self.cancel_app_button.clicked.connect(lambda: self.perform_app("cancel"))
        self.reconcile_app_button = QPushButton("Reconcile Uncertain Result")
        self.reconcile_app_button.clicked.connect(lambda: self.perform_app("reconcile"))
        app_buttons.addWidget(self.cancel_app_button)
        app_buttons.addWidget(self.reconcile_app_button)
        app_layout.addLayout(app_buttons)
        self.panels.addTab(apps, "Apps")
        layout.addWidget(self.panels)
        self.cancel_download_button = QPushButton("Cancel Selected Download")
        self.cancel_download_button.clicked.connect(self.cancel_download)
        self.downloads.itemSelectionChanged.connect(self.update_download_action)
        layout.addWidget(self.cancel_download_button)
        watching = QHBoxLayout()
        self.sessions = QComboBox()
        self.watch_button = QPushButton("Watch Page")
        self.watch_button.clicked.connect(self.watch_page)
        watching.addWidget(self.sessions)
        watching.addWidget(self.watch_button)
        layout.addLayout(watching)
        buttons = QHBoxLayout()
        self.approve_button = QPushButton("Approve Request")
        self.deny_button = QPushButton("Deny Request")
        self.revoke_button = QPushButton("Revoke Access")
        self.cancel_button = QPushButton("Cancel Task")
        for button, operation in (
            (self.approve_button, "approve"),
            (self.deny_button, "deny"),
            (self.revoke_button, "revoke"),
            (self.cancel_button, "cancel"),
        ):
            button.clicked.connect(lambda checked=False, op=operation: self.perform(op))
            buttons.addWidget(button)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        service.changed.connect(self.refresh)
        self.finished.connect(lambda: service.changed.disconnect(self.refresh))
        self._rows = []
        self._download_rows = []
        self._app_rows = []
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh_activity)
        self.finished.connect(self.timer.stop)
        self.timer.start()
        self.refresh()

    def selection(self):
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def refresh(self):
        selected = self.selection()
        selected_id = selected[0].task_id if selected else None
        self.table.blockSignals(True)
        try:
            self._rows = []
            if not self.service._closed and not self.service.failure:
                for task in self.service.store.list_tasks():
                    grants = self.service.store.grants(task.task_id)
                    self._rows.append((task, grants[-1] if grants else None))
            self.table.setRowCount(len(self._rows))
            selected_row = -1
            for row, (task, grant) in enumerate(self._rows):
                for column, text in enumerate(
                    (
                        " ".join(task.objective.split())[:120],
                        task.status,
                        grant.status if grant else "Not requested",
                    )
                ):
                    self.table.setItem(row, column, QTableWidgetItem(text))
                if task.task_id == selected_id:
                    selected_row = row
            if selected_row >= 0:
                self.table.selectRow(selected_row)
            elif self._rows:
                self.table.selectRow(0)
            self.table.resizeColumnsToContents()
        except (sqlite3.Error, OSError, ValueError):
            self._rows = []
            self.table.setRowCount(0)
            self.service._fatal()
        finally:
            self.table.blockSignals(False)
        self.show_details()

    def show_details(self):
        selected = self.selection()
        available = not self.service._closed and not self.service.failure
        task, grant = selected if selected else (None, None)
        active = available and task is not None and task.status in ACTIVE_STATUSES
        pending = active and grant is not None and grant.status == "requested"
        self.approve_button.setEnabled(bool(pending))
        self.deny_button.setEnabled(bool(pending))
        self.revoke_button.setEnabled(
            bool(active and grant and grant.status == "approved")
        )
        self.cancel_button.setEnabled(bool(active))
        selected_session = self.sessions.currentData()
        self.sessions.clear()
        if active:
            for session_id, owner in self.service._sessions.items():
                if owner == task.task_id:
                    state = self.service.backend.inspect(session_id)
                    self.sessions.addItem(state.url, session_id)
            index = self.sessions.findData(selected_session)
            if index >= 0:
                self.sessions.setCurrentIndex(index)
        self.watch_button.setEnabled(
            bool(
                active
                and grant
                and grant.status == "approved"
                and "attach" in grant.scope["actions"]
                and self.sessions.count()
            )
        )
        if self.service.failure:
            self.details.setPlainText(self.service.failure)
        elif grant:
            scope = grant.scope
            network = (
                "Private addresses and DNS hostnames are allowed at the listed origins. "
                "DNS routes are not pinned; a hostname may resolve to a private network."
                if scope["allow_private_network"]
                else "Public literal IP addresses only."
            )
            self.details.setPlainText(
                f"Task: {task.task_id}\n"
                f"Profile: {scope['profile']}\n"
                + "Origins:\n"
                + ("\n".join(scope["origins"]) or "about:blank only")
                + "\n"
                + (
                    "Storage: existing cookies, cached responses, and site storage are shared. "
                    "Site changes persist and may change your signed-in session. "
                    "The network scope and budget apply to task pages only. "
                    "Task pages bypass background service workers and keep document scripts disabled. "
                    "Your tabs and other approved tasks can use this profile at the same time.\n"
                    if scope["profile"] != "private"
                    else "Storage: a separate private profile for each session.\n"
                )
                + f"Actions: {', '.join(scope['actions']) or 'None'}\n"
                + (
                    "Upload files:\n"
                    + "\n".join(scope["upload_sources"])
                    + "\nUpload sites:\n"
                    + "\n".join(scope["upload_origins"])
                    + f"\nStaging per page: up to {scope['max_upload_bytes']:,} bytes.\n"
                    if scope.get("upload_sources")
                    else ""
                )
                + (
                    "Download files (no overwriting):\n"
                    + "\n".join(scope["download_destinations"])
                    + f"\nEach file: up to {scope['max_download_bytes']:,} bytes.\n"
                    if scope.get("download_destinations")
                    else ""
                )
                + (
                    "Control changes: text entry, selection by index, and checkbox/radio activation. "
                    "Active document scripts are unavailable.\n"
                    if "edit" in scope["actions"]
                    else ""
                )
                + (
                    "Submission: native GET/POST forms can change account data and send "
                    "current fields, including hidden site tokens. Password, multi-file and folder forms, "
                    "child frames and new windows are unavailable. Multipart needs approved single-file upload.\n"
                    if "submit" in scope["actions"]
                    else "Form submission is unavailable.\n"
                )
                + (
                    "Content access: readable page text and control labels are shared with "
                    "the task client. Form values and editable text are omitted.\n"
                    if "read" in scope["actions"]
                    else "Content access: URL/title metadata only; page text is not shared.\n"
                )
                + f"Network permission: {network}\n"
                + (
                    "Network methods: GET/HEAD, plus one bound POST per approved form action. "
                    if "submit" in scope["actions"]
                    else "Network methods: GET/HEAD. "
                )
                + "Requests can have effects on a website.\n"
                + (
                    "App operations (exact effect and destination):\n"
                    + "\n".join(
                        f"{p['effect'].upper()}: {p['connector']}.{p['tool']} → {p['destination']}"
                        for p in scope.get("connector_permissions", [])
                    )
                    + "\nApp results are retained; cancellation does not undo writes.\n"
                    if scope.get("connector_permissions")
                    else ""
                )
                + f"Duration: {scope['duration_seconds']} seconds after approval\n"
                + f"Operation limit: {scope['max_operations']}\n"
                + f"Network request limit: {scope['max_requests']}\n"
                + f"Session limit: {scope['max_sessions']}\n"
                + f"Expires: {grant.expires_at or 'Not approved'}\n"
                + "\nClient objective (preview): "
                + json.dumps(task.objective[:256], ensure_ascii=False)
            )
        else:
            self.details.setPlainText(
                "Task permission requests from internal clients appear here."
            )
        self.refresh_activity()

    def refresh_activity(self):
        selected = self.selection()
        if self.service._closed or self.service.failure or not selected:
            self.activity.clear()
            self.downloads.setRowCount(0)
            self._download_rows = []
            self.update_download_action()
            self.artifacts.clear()
            self.artifact_preview.clear()
            self.apps.setRowCount(0)
            self._app_rows = []
            self.show_app()
            return
        task = selected[0]
        try:
            events = self.service.store.recent_events(task.task_id)
            lines = []
            for event in events:
                label = event.kind.replace(".", " ").replace("_", " ").capitalize()
                reason = event.payload.get("reason") or event.payload.get("error")
                if reason:
                    label += ": " + str(reason)[:100].replace("_", " ")
                if event.kind == "download.finished":
                    label = "Download " + event.payload.get("state", "finished")
                lines.append(event.created_at[11:19] + "  " + label)
            content = "\n".join(lines) or "No recorded activity."
            if self.activity.toPlainText() != content:
                scroll = self.activity.verticalScrollBar()
                follow = scroll.value() == scroll.maximum()
                previous = scroll.value()
                self.activity.setPlainText(content)
                scroll.setValue(scroll.maximum() if follow else previous)
            receipts = self.service.store.recent_events(
                task.task_id,
                limit=100,
                kinds=("download.requested", "download.finished", "download.failed"),
            )
            records = {}
            for event in receipts:
                data = dict(event.payload)
                data.setdefault("state", "requested")
                records[data["download_id"]] = data
            for session, owner in self.service._sessions.items():
                if owner != task.task_id:
                    continue
                listing = getattr(self.service.backend, "list_downloads", None)
                if callable(listing):
                    for receipt in listing(session):
                        records[receipt.download_id] = asdict(receipt)
            previous = self.downloads.currentRow()
            selected_id = (
                self._download_rows[previous]["download_id"]
                if 0 <= previous < len(self._download_rows)
                else None
            )
            self._download_rows = list(records.values())[-100:]
            self.downloads.blockSignals(True)
            self.downloads.setRowCount(len(self._download_rows))
            for row, record in enumerate(self._download_rows):
                size = record.get("received_bytes", 0)
                total = record.get("total_bytes", 0)
                text = f"{size:,}" + (f" / {total:,}" if total > 0 else "")
                for column, value in enumerate(
                    (
                        record["destination"],
                        record["state"].replace("_", " "),
                        text,
                        (record.get("error") or record.get("reason", "")).replace(
                            "_", " "
                        ),
                    )
                ):
                    self.downloads.setItem(row, column, QTableWidgetItem(value))
                if record["download_id"] == selected_id:
                    self.downloads.selectRow(row)
            self.downloads.blockSignals(False)
            self.update_download_action()
            previous = self.artifacts.currentData()
            self.artifacts.blockSignals(True)
            self.artifacts.clear()
            for artifact in self.service.store.artifacts(task.task_id):
                self.artifacts.addItem(artifact.title, artifact.artifact_id)
            index = self.artifacts.findData(previous)
            if index >= 0:
                self.artifacts.setCurrentIndex(index)
            self.artifacts.blockSignals(False)
            self.show_artifact()
            self.refresh_apps(task.task_id)
        except (sqlite3.Error, OSError, ValueError, KeyError):
            self.service._fatal()

    def show_artifact(self):
        selected = self.selection()
        artifact_id = self.artifacts.currentData()
        self.delete_artifact_button.setEnabled(
            bool(
                selected
                and artifact_id
                and not self.service._closed
                and not self.service.failure
            )
        )
        if (
            not selected
            or not artifact_id
            or self.service._closed
            or self.service.failure
        ):
            self.artifact_preview.clear()
            return
        try:
            artifact = self.service.store.artifact(selected[0].task_id, artifact_id)
            sources = (
                ", ".join(f"#{s}" for s in artifact.source_events) or "None supplied"
            )
            content = (
                f"Agent-generated {artifact.format}; {artifact.byte_count:,} bytes\n"
                f"SHA-256: {artifact.sha256}\nPage inspection evidence: {sources}\n\n"
                + artifact.content
            )
            if self.artifact_preview.toPlainText() != content:
                self.artifact_preview.setPlainText(content)
        except (sqlite3.Error, OSError, ValueError, KeyError):
            self.service._fatal()

    def delete_artifact(self):
        selected = self.selection()
        artifact_id = self.artifacts.currentData()
        if (
            not selected
            or not artifact_id
            or self.service._closed
            or self.service.failure
        ):
            return
        if (
            QMessageBox.question(
                self,
                "Delete draft?",
                "Delete this retained draft? This cannot be undone.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.service.store.delete_artifact(selected[0].task_id, artifact_id)
        except (sqlite3.Error, OSError, ValueError, KeyError):
            self.service._fatal()
        self.refresh_activity()

    def refresh_apps(self, task_id):
        index = self.apps.currentRow()
        previous = (
            self._app_rows[index].request_id
            if 0 <= index < len(self._app_rows)
            else None
        )
        self._app_rows = self.service.connector_store.list(task_id)
        self.apps.blockSignals(True)
        self.apps.setRowCount(len(self._app_rows))
        for row, receipt in enumerate(self._app_rows):
            for column, value in enumerate(
                (
                    f"{receipt.effect.upper()}: {receipt.connector}.{receipt.tool}",
                    receipt.destination,
                    receipt.state,
                    receipt.request_id,
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.apps.setItem(row, column, item)
            if receipt.request_id == previous:
                self.apps.selectRow(row)
        if previous is None and self._app_rows:
            self.apps.selectRow(0)
        self.apps.blockSignals(False)
        self.show_app()

    def show_app(self):
        index = self.apps.currentRow()
        receipt = self._app_rows[index] if 0 <= index < len(self._app_rows) else None
        active = not self.service._closed and not self.service.failure
        job = (
            self.service._connector_jobs.get((receipt.task_id, receipt.request_id))
            if receipt
            else None
        )
        self.cancel_app_button.setEnabled(bool(active and job))
        can_reconcile = False
        if (
            active
            and receipt
            and receipt.state == "uncertain"
            and not any(
                owner == receipt.task_id for owner, _ in self.service._connector_jobs
            )
        ):
            live = self.service._grants.get(receipt.task_id)
            try:
                if live:
                    live[1].check(
                        "app",
                        connector=receipt.connector,
                        tool=receipt.tool,
                        effect=receipt.effect,
                        destination=receipt.destination,
                    )
                    definition = self.service.connectors.get(
                        receipt.connector, receipt.tool
                    )
                    can_reconcile = definition.reconcile is not None
            except (ValueError, PermissionError):
                pass
        self.reconcile_app_button.setEnabled(can_reconcile)
        text = (
            f"Operation: {receipt.effect.upper()} {receipt.connector}.{receipt.tool}\n"
            f"Destination: {receipt.destination}\n"
            f"State: {receipt.state}\nResult: {receipt.reference or 'No confirmed reference'}\n"
            f"Reason: {receipt.reason or 'None'}\nOperation ID: {receipt.operation_id}\n"
            f"Draft ID: {receipt.artifact_id or 'Not used'}\n\n{receipt.text}"
            if receipt
            else ""
        )
        if self.app_preview.toPlainText() != text:
            self.app_preview.setPlainText(text)

    def perform_app(self, operation):
        index = self.apps.currentRow()
        if not 0 <= index < len(self._app_rows):
            return
        receipt = self._app_rows[index]
        try:
            if operation == "cancel":
                self.service._cancel_app_job(receipt.task_id, receipt.request_id)
            else:
                self.service._app_reconcile(receipt.task_id, receipt.request_id)
        except Exception:
            QMessageBox.warning(
                self,
                "App Operation",
                "The operation or permission changed. Review its receipt before continuing.",
            )
        self.refresh_activity()

    def update_download_action(self):
        row = self.downloads.currentRow()
        record = (
            self._download_rows[row] if 0 <= row < len(self._download_rows) else None
        )
        selected = self.selection()
        self.cancel_download_button.setEnabled(
            bool(
                selected
                and not self.service._closed
                and not self.service.failure
                and record
                and self.service._sessions.get(record["session_id"])
                == selected[0].task_id
                and record["state"] in ("requested", "in_progress")
            )
        )

    def cancel_download(self):
        row = self.downloads.currentRow()
        selected = self.selection()
        if not selected or not 0 <= row < len(self._download_rows):
            return
        record = self._download_rows[row]
        try:
            self.service._download_cancel(
                selected[0].task_id, record["session_id"], record["download_id"]
            )
        except Exception:
            QMessageBox.warning(
                self, "Download", "The download changed. Review its current state."
            )
        self.refresh_activity()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)

    def perform(self, operation):
        selected = self.selection()
        if not selected:
            return
        task, grant = selected
        try:
            if operation == "cancel":
                self.service.cancel_task(task.task_id)
            elif grant:
                methods = {
                    "approve": self.service.approve_grant,
                    "deny": self.service.deny_grant,
                    "revoke": self.service.revoke_grant,
                }
                methods[operation](task.task_id, grant.grant_id)
        except ValueError as error:
            QMessageBox.warning(self, "Task Permissions", str(error))
        except (
            PermissionError,
            KeyError,
            RuntimeError,
            OSError,
            sqlite3.Error,
        ):
            QMessageBox.warning(
                self,
                "Task Permissions",
                "The task or permission changed, or its journal could not be saved. Review the current state before trying again.",
            )
        self.refresh()

    def watch_page(self):
        selected = self.selection()
        session_id = self.sessions.currentData()
        if not selected or not session_id:
            return
        try:
            self.service._watch(selected[0].task_id, session_id)
        except Exception:
            QMessageBox.warning(
                self,
                "Task Page",
                "The page could not be opened. Check its permission and current task state.",
            )
        else:
            self.accept()
