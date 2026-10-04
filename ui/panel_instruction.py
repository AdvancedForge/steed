"""Reusable native instruction buttons with explicit conversation/payload review."""

import json
import sqlite3
from uuid import uuid4

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.data_panel_form import plain_label


class PanelInstruction(QWidget):
    def __init__(self, definition, *, context=None, parent=None):
        super().__init__(parent)
        self.definition, self.context = definition, context
        self.preview = self.preview_send = self.recipient = self.recipient_label = None
        self._attempt = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        self.availability = plain_label("")
        body.addWidget(self.availability)
        self.notice = plain_label("")
        body.addWidget(self.notice)
        self.send = QPushButton("Choose agent and review…")
        self.send.setAutoDefault(False)
        self.send.clicked.connect(self.review)
        body.addWidget(self.send)
        self.refresh_available()

    def _context(self):
        if self.context is None:
            raise ValueError("Preview only")
        current = self.context()
        current[0].data_panels.instruction_available(*current[1:4])
        return current

    def refresh_available(self):
        try:
            service, *_ = self._context()
            recipients = service.data_panels.instruction_recipients()
        except (ValueError, KeyError, OSError, sqlite3.Error):
            recipients = []
        self.send.setEnabled(bool(recipients) and self.preview is None)
        self.availability.setText(
            "Preview only • sends nothing."
            if self.context is None
            else "Choose a connected conversation; review before sending."
            if recipients
            else "Connect an agent conversation to use this button."
        )
        if self.recipient is not None:
            selected = self.recipient.currentData()
            self.recipient.blockSignals(True)
            self.recipient.clear()
            self.recipient.addItem("Choose a connected conversation…", None)
            for task in recipients:
                objective = " ".join(task.objective.split())[:100]
                objective = "".join(
                    c if c.isprintable() else f"\\u{ord(c):04x}" for c in objective
                )
                self.recipient.addItem(
                    f"{objective} • {task.task_id}",
                    task.task_id,
                )
            self.recipient.setCurrentIndex(max(0, self.recipient.findData(selected)))
            self.recipient.blockSignals(False)
            self._selection_changed()

    def _selection_changed(self):
        selected = self.recipient.currentData()
        self.preview_send.setEnabled(selected is not None)
        self.recipient_label.setText(
            "Recipient task: " + selected if selected else "No recipient selected."
        )

    def clear_private(self):
        if self.preview is not None:
            self.preview.reject()
        self._attempt = None
        self.notice.clear()

    def review(self):
        if self.preview is not None:
            return
        try:
            service, task_id, request_id, profile_key, profile = self._context()
            payload = service.data_panels.instruction_payload(
                task_id, request_id, self.definition["id"]
            )
        except (ValueError, KeyError, OSError, sqlite3.Error):
            self.notice.setText("Instruction unavailable; check the panel and profile.")
            return
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("Send instruction to agent")
        dialog.resize(620, 450)
        body = QVBoxLayout(dialog)
        body.addWidget(
            plain_label(
                f"Display profile: {profile}\nChoose the receiving conversation:"
            )
        )
        recipient = QComboBox()
        recipient.setAccessibleName("Instruction recipient")
        body.addWidget(recipient)
        recipient_label = plain_label("No recipient selected.")
        body.addWidget(recipient_label)
        body.addWidget(
            plain_label(
                "Only this instruction is sent. Panel contents are not attached. The agent can act using its own tools; Steed access keeps its existing permissions and effect reviews."
            )
        )
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(json.loads(payload)["instruction"])
        content.setAccessibleName("Instruction to send")
        body.addWidget(content, 1)
        details = QPushButton("Show message details")
        details.setCheckable(True)
        details.setAutoDefault(False)
        details.toggled.connect(
            lambda full: content.setPlainText(
                payload if full else json.loads(payload)["instruction"]
            )
        )
        body.addWidget(details)
        error = plain_label("")
        body.addWidget(error)
        buttons = QDialogButtonBox()
        send = buttons.addButton(
            "Send to agent", QDialogButtonBox.ButtonRole.AcceptRole
        )
        send.setAutoDefault(False)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setDefault(True)
        cancel.clicked.connect(dialog.reject)
        body.addWidget(buttons)

        def deliver():
            if self.preview is not dialog:
                return
            target = recipient.currentData()
            try:
                if self._context()[:4] != (service, task_id, request_id, profile_key):
                    raise ValueError("The display profile changed")
                if self._attempt is None or self._attempt[0] != target:
                    self._attempt = target, uuid4().hex
                service.data_panels.submit_instruction(
                    task_id,
                    request_id,
                    self.definition["id"],
                    self._attempt[1],
                    target,
                    profile_key=profile_key,
                )
            except (ValueError, KeyError, OSError, sqlite3.Error):
                error.setText(
                    "Message was not queued. Check the selected conversation and retry."
                )
                return
            self._attempt = None
            self.notice.setText(
                "Queued for the selected agent • delivery is not confirmation of action."
            )
            dialog.accept()

        def finished(*_):
            self.preview = self.preview_send = self.recipient = self.recipient_label = (
                None
            )
            self.refresh_available()

        send.clicked.connect(deliver)
        recipient.currentIndexChanged.connect(self._selection_changed)
        dialog.finished.connect(finished)
        self.preview, self.preview_send, self.recipient = dialog, send, recipient
        self.recipient_label = recipient_label
        self.refresh_available()
        dialog.show()
