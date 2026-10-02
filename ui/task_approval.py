"""Automatic, nonmodal user approval for queued task permission requests."""

import sqlite3

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

ACTION_LABELS = {
    "navigate": "Open links on these sites",
    "inspect": "See page addresses and titles",
    "read": "Read page text and control labels",
    "attach": "Show task pages in a browser view",
    "edit": "Change text, choices, checkboxes and radio buttons",
    "scroll": "Scroll task pages",
    "submit": "Submit forms on these sites (can change account data)",
    "download": "Save downloads to the exact files listed below (no overwriting)",
    "upload": "Send the listed local files to the listed upload sites",
    "artifact": "Save and read task notes/drafts here (kept after the task ends)",
    "app": "Use the exact app tools/destinations below (results kept here)",
}


def app_description(service, permission):
    try:
        return service.connectors.get(
            permission["connector"], permission["tool"]
        ).description
    except ValueError:
        return "App connection is no longer available."


class WrappedLabel(QLabel):
    """Keep complete wrapped text visible inside the scrollable scope."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(self.heightForWidth(event.size().width()))


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
        self.resize(500, 560)
        outer = QVBoxLayout(self)
        objective = WrappedLabel("Task: " + task.objective[:256])
        objective.setTextFormat(Qt.TextFormat.PlainText)
        objective.setWordWrap(True)
        objective.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        outer.addWidget(objective)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        scope = grant.scope
        existing = scope["profile"] != "private"
        seconds = scope["duration_seconds"]
        duration = (
            f"{seconds // 60} minutes" if seconds % 60 == 0 else f"{seconds} seconds"
        )
        summary = WrappedLabel(
            "Profile: "
            + (scope["profile"].removeprefix("existing:") if existing else "Private")
            + "\n"
            + "\n".join(ACTION_LABELS[action] for action in scope["actions"])
            + f"\nAccess lasts {duration}."
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        summary.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(summary)
        layout.addWidget(QLabel("Allowed sites:"))
        self.sites = QPlainTextEdit("\n".join(scope["origins"]) or "about:blank only")
        self.sites.setReadOnly(True)
        self.sites.setMaximumHeight(100)
        layout.addWidget(self.sites)
        if scope.get("download_destinations"):
            destinations = QPlainTextEdit("\n".join(scope["download_destinations"]))
            destinations.setReadOnly(True)
            destinations.setMaximumHeight(100)
            layout.addWidget(QLabel("Allowed download files:"))
            layout.addWidget(destinations)
            size = scope["max_download_bytes"]
            layout.addWidget(QLabel(f"Each download is limited to {size:,} bytes."))
        if scope.get("upload_sources"):
            layout.addWidget(QLabel("Local files allowed for upload:"))
            files = QPlainTextEdit("\n".join(scope["upload_sources"]))
            files.setReadOnly(True)
            files.setMaximumHeight(80)
            layout.addWidget(files)
            sites = QPlainTextEdit("\n".join(scope["upload_origins"]))
            sites.setReadOnly(True)
            sites.setMaximumHeight(70)
            layout.addWidget(QLabel("Allowed upload sites:"))
            layout.addWidget(sites)
            layout.addWidget(
                QLabel(f"Up to {scope['max_upload_bytes']:,} staged bytes per page.")
            )
        if scope.get("connector_permissions"):
            layout.addWidget(QLabel("Allowed app operations:"))
            apps = QPlainTextEdit(
                "\n\n".join(
                    f"{p['effect'].upper()}: {app_description(service, p)}"
                    + f"\nDestination: {p['destination']}"
                    for p in scope["connector_permissions"]
                )
            )
            apps.setReadOnly(True)
            apps.setMaximumHeight(180)
            layout.addWidget(apps)
            app_notice = WrappedLabel(
                "App tools may send drafts or change external data. Only listed "
                "destinations are allowed. Dispatched writes can still happen after cancellation."
            )
            app_notice.setWordWrap(True)
            layout.addWidget(app_notice)
        notice = WrappedLabel(
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
            + "Task scripts are unavailable. "
            + (
                "Forms send their current fields, including hidden site tokens. "
                + (
                    "Password, multi-file and folder forms are unavailable. "
                    if "upload" in scope["actions"]
                    else "Password and file forms are unavailable. "
                )
                if "submit" in scope["actions"]
                else ""
            )
            + "You can revoke access in File → Task Permissions."
        )
        notice.setWordWrap(True)
        notice.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(notice)
        more = QPushButton("Show limits")
        self.limits = WrappedLabel(
            f"Up to {scope['max_operations']} operations, {scope['max_requests']} "
            f"network requests and {scope['max_sessions']} sessions.\n"
            "GET/HEAD requests can have website effects. DNS routes are not pinned.\n"
            "Form values and editable text are omitted from page reads."
            + (
                "\nApp tool identifiers:\n"
                + "\n".join(
                    f"{p['connector']}.{p['tool']}"
                    for p in scope["connector_permissions"]
                )
                if scope.get("connector_permissions")
                else ""
            )
        )
        self.limits.setWordWrap(True)
        self.limits.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self.limits.hide()
        more.clicked.connect(
            lambda: self.limits.setVisible(not self.limits.isVisible())
        )
        layout.addWidget(more)
        layout.addWidget(self.limits)
        self.error = QLabel()
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
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
        outer.addLayout(buttons)

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
