"""Trusted user approval and cancellation, outside the client command surface."""

import json
import sqlite3

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
    QVBoxLayout,
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
            "Task scripts and file actions are unavailable. Revoking access cancels the task."
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
        layout.addWidget(self.details)
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
                    "Control changes: text entry, selection by index, and checkbox/radio activation. "
                    "Active document scripts are unavailable.\n"
                    if "edit" in scope["actions"]
                    else ""
                )
                + (
                    "Submission: native GET/POST forms can change account data and send "
                    "current fields, including hidden site tokens. Password/file forms, "
                    "child frames, new windows and multipart forms are unavailable.\n"
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
