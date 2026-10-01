"""Automatic, nonmodal user approval for queued task permission requests."""

import sqlite3

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

ACTION_LABELS = {
    "navigate": "Open links on these sites",
    "inspect": "See page addresses and titles",
    "read": "Read page text and control labels",
    "attach": "Show task pages in a browser view",
}


class TaskApprovalDialog(QDialog):
    def __init__(self, service, task, grant, parent=None):
        super().__init__(parent)
        self.service, self.task_id, self.grant_id = (
            service,
            task.task_id,
            grant.grant_id,
        )
        self.setWindowTitle("Allow task access?")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(480, 420)
        layout = QVBoxLayout(self)
        objective = QLabel("Task: " + task.objective[:256])
        objective.setTextFormat(Qt.TextFormat.PlainText)
        objective.setWordWrap(True)
        layout.addWidget(objective)
        scope = grant.scope
        existing = scope["profile"] != "private"
        seconds = scope["duration_seconds"]
        duration = (
            f"{seconds // 60} minutes" if seconds % 60 == 0 else f"{seconds} seconds"
        )
        summary = QLabel(
            "Profile: "
            + (scope["profile"].removeprefix("existing:") if existing else "Private")
            + "\n"
            + "\n".join(ACTION_LABELS[action] for action in scope["actions"])
            + f"\nAccess lasts {duration}."
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addWidget(QLabel("Allowed sites:"))
        self.sites = QPlainTextEdit("\n".join(scope["origins"]) or "about:blank only")
        self.sites.setReadOnly(True)
        self.sites.setMaximumHeight(100)
        layout.addWidget(self.sites)
        notice = QLabel(
            (
                "Uses your existing sign-in and site storage. You can keep browsing. "
                "Site changes persist and may affect your signed-in session. "
                if existing
                else "Uses a separate private session. "
            )
            + (
                "These sites may reach private network addresses. "
                if scope["allow_private_network"]
                else ""
            )
            + "Task scripts, uploads and downloads are unavailable. "
            "You can revoke access in File → Task Permissions."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        more = QPushButton("Show limits")
        self.limits = QLabel(
            f"Up to {scope['max_operations']} operations, {scope['max_requests']} "
            f"network requests and {scope['max_sessions']} sessions.\n"
            "GET/HEAD requests can have website effects. DNS routes are not pinned.\n"
            "Form values and editable text are omitted from page reads."
        )
        self.limits.setWordWrap(True)
        self.limits.hide()
        more.clicked.connect(
            lambda: self.limits.setVisible(not self.limits.isVisible())
        )
        layout.addWidget(more)
        layout.addWidget(self.limits)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        self.deny_button = QPushButton("Deny")
        self.approve_button = QPushButton("Approve")
        # Enter should never silently approve an unexpected popup.
        self.deny_button.setDefault(True)
        self.approve_button.setAutoDefault(False)
        self.deny_button.clicked.connect(lambda: self.decide(False))
        self.approve_button.clicked.connect(lambda: self.decide(True))
        buttons.addWidget(self.deny_button)
        buttons.addWidget(self.approve_button)
        layout.addLayout(buttons)

    def decide(self, approve):
        try:
            method = self.service.approve_grant if approve else self.service.deny_grant
            method(self.task_id, self.grant_id)
        except (
            ValueError,
            PermissionError,
            KeyError,
            RuntimeError,
            OSError,
            sqlite3.Error,
        ):
            self.error.setText(
                "Access could not be saved. Review the request and try again."
            )
            return
        self.done(QDialog.DialogCode.Accepted)


class TaskApprovalQueue(QObject):
    def __init__(self, registry, service):
        super().__init__(registry)
        self.registry, self.service = registry, service
        self.dialog = None
        self.deferred = set()
        self.scheduled = False
        service.changed.connect(self.schedule)
        self.schedule()

    def schedule(self):
        if not self.scheduled:
            self.scheduled = True
            QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self.scheduled = False
        pending = []
        if not self.service._closed and not self.service.failure:
            try:
                pending = [
                    (task, grant)
                    for task in self.service.store.list_tasks()
                    for grant in self.service.store.grants(task.task_id)
                    if grant.status == "requested"
                ]
            except (sqlite3.Error, OSError, ValueError):
                self.service._fatal()
        if self.dialog is not None and not sip.isdeleted(self.dialog):
            if any(grant.grant_id == self.dialog.grant_id for _, grant in pending):
                return
            self.dialog.done(QDialog.DialogCode.Accepted)
            return
        windows = [
            owner.parent()
            for owner in self.registry.owners
            if owner.parent() is not None
            and not getattr(owner.parent(), "_closing", False)
        ]
        if not windows:
            return
        parent = QApplication.activeWindow()
        if parent not in windows:
            parent = windows[0]
        for task, grant in reversed(pending):
            if grant.grant_id in self.deferred:
                continue
            dialog = TaskApprovalDialog(self.service, task, grant, parent)
            self.dialog = dialog
            dialog.finished.connect(
                lambda result, gid=grant.grant_id: self._finished(result, gid)
            )
            dialog.show()
            return

    def _finished(self, result, grant_id):
        # Closing/Escape leaves a request pending in the management dialog.
        if result == QDialog.DialogCode.Rejected:
            self.deferred.add(grant_id)
        self.dialog = None
        self.schedule()
