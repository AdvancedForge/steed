"""Trusted source selection for an agent's request to continue previous work."""

import json
import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.task_store import TERMINAL_STATUSES


class TaskRecoveryDialog(QDialog):
    def __init__(self, service, task, parent=None):
        super().__init__(parent)
        self.service, self.task_id = service, task.task_id
        self.request_key = "recovery:" + task.task_id
        self.setWindowTitle("Continue previous work?")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(650, 580)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "New task: "
            + task.objective[:256]
            + "\nChoose exactly what to share. This creates independent draft copies. The agent will still request fresh permissions for its work; saved approvals will not bypass that request. Pages, old grants and writes are never resumed."
        )
        intro.setTextFormat(Qt.TextFormat.PlainText)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.source = QComboBox()
        for archived in reversed(service.store.list_tasks()):
            if archived.status in TERMINAL_STATUSES:
                self.source.addItem(
                    archived.objective[:120] + " (" + archived.status + ")",
                    archived.task_id,
                )
        layout.addWidget(self.source)
        self.scope = QLabel()
        self.scope.setTextFormat(Qt.TextFormat.PlainText)
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        layout.addWidget(QLabel("Choose up to ten drafts (none selected by default):"))
        self.artifacts = QListWidget()
        self.artifacts.currentItemChanged.connect(self.preview_artifact)
        layout.addWidget(self.artifacts)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.evidence = QCheckBox(
            "Share prior permission scope, activity and app receipt summaries"
        )
        self.evidence.setChecked(True)
        layout.addWidget(self.evidence)
        self.evidence_preview = QPlainTextEdit()
        self.evidence_preview.setReadOnly(True)
        self.evidence_preview.setMaximumHeight(100)
        layout.addWidget(self.evidence_preview)
        notice = QLabel(
            "Drafts and evidence are untrusted historical text. Imported draft bodies require artifact permission. Matching completed or uncertain app writes remain blocked, even if receipt sharing is unchecked. Deleting an original draft does not delete a shared copy."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        deny = QPushButton("Deny")
        deny.setDefault(True)
        deny.clicked.connect(lambda: self.decide(False))
        self.share = QPushButton("Share Selected Material")
        self.share.setAutoDefault(False)
        self.share.clicked.connect(lambda: self.decide(True))
        buttons.addWidget(deny)
        buttons.addWidget(self.share)
        layout.addLayout(buttons)
        self.source.currentIndexChanged.connect(self.refresh_source)
        self.refresh_source()
        service.changed.connect(self.check_request, Qt.ConnectionType.QueuedConnection)

    def check_request(self):
        try:
            ended = (
                self.service._closed
                or self.service.failure
                or self.service.store.recovery(self.task_id)["status"] != "requested"
            )
        except (OSError, sqlite3.Error, ValueError):
            self.service._fatal()
            ended = True
        if ended:
            self.done(QDialog.DialogCode.Accepted)

    def refresh_source(self):
        self.artifacts.clear()
        self.preview.clear()
        source = self.source.currentData()
        self.share.setEnabled(source is not None)
        if source is None:
            self.scope.setText("No finished or interrupted tasks are available yet.")
            self.evidence_preview.clear()
            return
        grants = self.service.store.grants(source)
        profile = (
            grants[-1].scope.get("profile", "private")
            if grants
            else "No previous grant"
        )
        self.scope.setText(
            "Previous profile: "
            + profile
            + "\nPrevious permissions are historical context only."
        )
        for artifact in self.service.store.artifacts(source):
            item = QListWidgetItem(artifact.title)
            item.setData(Qt.ItemDataRole.UserRole, artifact.artifact_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.artifacts.addItem(item)
        if self.artifacts.count():
            self.artifacts.setCurrentRow(0)
        receipts = self.service.connector_store.list(source)
        self.evidence_preview.setPlainText(
            "App receipts:\n"
            + (
                "\n".join(
                    f"{r.effect.upper()}: {r.connector}.{r.tool} → {r.destination}\n{r.state}: {r.reference}\n{r.text[:512]}"
                    for r in receipts
                )
                or "None"
            )
            + "\nAlso shares prior scope and up to 50 activity entries."
            + (
                "\nPrevious permission scope:\n"
                + json.dumps(grants[-1].scope, ensure_ascii=False, indent=2)
                if grants
                else ""
            )
        )

    def preview_artifact(self, current, previous):
        if current is None:
            self.preview.clear()
            return
        try:
            artifact = self.service.store.artifact(
                self.source.currentData(), current.data(Qt.ItemDataRole.UserRole)
            )
            self.preview.setPlainText(artifact.content)
        except (KeyError, OSError, sqlite3.Error):
            self.preview.setPlainText("This draft is no longer available.")

    def decide(self, allow):
        try:
            if allow:
                selected = [
                    self.artifacts.item(index).data(Qt.ItemDataRole.UserRole)
                    for index in range(self.artifacts.count())
                    if self.artifacts.item(index).checkState() == Qt.CheckState.Checked
                ]
                self.service.approve_recovery(
                    self.task_id,
                    self.source.currentData(),
                    selected,
                    evidence=self.evidence.isChecked(),
                )
            else:
                self.service.deny_recovery(self.task_id)
        except (
            ValueError,
            KeyError,
            PermissionError,
            RuntimeError,
            OSError,
            sqlite3.Error,
        ):
            self.error.setText(
                "Could not share this material. Choose a finished task and at most ten existing drafts; large evidence may need to be omitted."
            )
            return
        self.done(QDialog.DialogCode.Accepted)
