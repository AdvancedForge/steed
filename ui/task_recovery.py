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
    QTabWidget,
    QVBoxLayout,
    QWidget,
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
            + "\nChoose exactly what to share. This creates independent draft/file copies. The agent will still request fresh permissions; saved approvals will not bypass that request. Pages, old grants, code approvals and writes are never resumed."
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
        selections = QTabWidget()
        self.materials = selections
        layout.addWidget(selections)
        drafts = QWidget()
        draft_layout = QVBoxLayout(drafts)
        draft_layout.addWidget(
            QLabel("Choose up to ten drafts (none selected by default):")
        )
        self.artifacts = QListWidget()
        self.artifacts.currentItemChanged.connect(self.preview_artifact)
        draft_layout.addWidget(self.artifacts)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        draft_layout.addWidget(self.preview)
        selections.addTab(drafts, "Drafts")
        files = QWidget()
        file_layout = QVBoxLayout(files)
        file_layout.addWidget(
            QLabel("Choose up to ten sealed files, up to 32 MiB total (none selected):")
        )
        self.files = QListWidget()
        self.files.currentItemChanged.connect(self.preview_file)
        file_layout.addWidget(self.files)
        self.file_preview = QPlainTextEdit()
        self.file_preview.setReadOnly(True)
        file_layout.addWidget(self.file_preview)
        selections.addTab(files, "Files")
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
            "Sharing can disclose selected material to the agent's model provider. Draft bodies need artifact permission; files need workspace permission. Files are copied as data: packages must be staged and approved again to run. Matching completed or uncertain app writes stay blocked. Deleting an original does not delete a shared copy."
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
        self.files.clear()
        self.file_preview.clear()
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
        for file in self.service.workspace.list(source):
            if file.status != "sealed":
                continue
            item = QListWidgetItem(f"{file.name} ({file.byte_count:,} bytes)")
            item.setData(Qt.ItemDataRole.UserRole, file.file_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.files.addItem(item)
        if self.files.count():
            self.files.setCurrentRow(0)
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

    def preview_file(self, current, previous):
        self.file_preview.clear()
        if current is None:
            return
        try:
            item = self.service.workspace.get(
                self.source.currentData(), current.data(Qt.ItemDataRole.UserRole)
            )
            self.file_preview.setPlainText(
                f"Name: {item.name}\nType: {item.media_type}\n"
                f"Size: {item.byte_count:,} bytes\nSource: {item.source}\nSHA-256: {item.sha256}\n"
                "The file is copied without rendering or executing its contents."
            )
        except (KeyError, OSError, sqlite3.Error):
            self.file_preview.setPlainText("This file is no longer available.")

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
                    file_ids=[
                        self.files.item(index).data(Qt.ItemDataRole.UserRole)
                        for index in range(self.files.count())
                        if self.files.item(index).checkState() == Qt.CheckState.Checked
                    ],
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
                "Could not share this material. Choose a finished task, up to ten existing drafts and ten sealed files (32 MiB total); large evidence may need to be omitted."
            )
            return
        self.done(QDialog.DialogCode.Accepted)
