"""Explicit native form messages; previews never execute agent-provided code."""

import json
import sqlite3
from uuid import uuid4

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from data_panel_format import MAX_FIELD_CHARS, FormInputError
from ui.html_dialog import HtmlSettingsDialog


def plain_label(text):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class PanelForm(QWidget):
    def __init__(self, definition, *, context=None, parent=None):
        super().__init__(parent)
        self.definition, self.context = definition, context
        self.inputs = {}
        self.preview = None
        self.preview_send = None
        self._attempt = None
        self._sent_values = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        for field in definition["fields"]:
            title = field["title"] + (" (required)" if field["required"] else "")
            caption = plain_label(title)
            body.addWidget(caption)
            if field["kind"] == "text":
                editor = QLineEdit()
                editor.setMaxLength(MAX_FIELD_CHARS)
            else:
                editor = QComboBox()
                editor.addItem("Choose…", "")
                for option in field["options"]:
                    editor.addItem(option, option)
            editor.setAccessibleName(title)
            caption.setBuddy(editor)
            self.inputs[field["id"]] = editor
            body.addWidget(editor)
        self.availability = plain_label("")
        body.addWidget(self.availability)
        self.notice = plain_label("")
        body.addWidget(self.notice)
        self.send = QPushButton("Review and send to agent…")
        # Reparenting a preview into QDialog must not steal its Decline default.
        self.send.setAutoDefault(False)
        self.send.setDefault(False)
        self.send.clicked.connect(self.review)
        body.addWidget(self.send)
        self.refresh_available()

    def values(self):
        return {
            key: editor.text()
            if isinstance(editor, QLineEdit)
            else editor.currentData()
            for key, editor in self.inputs.items()
        }

    def refresh_available(self):
        try:
            if self.context is None:
                raise ValueError("Preview only")
            service, task_id, request_id, profile_key, profile = self.context()
            service.data_panels.form_available(task_id, request_id, profile_key)
        except (ValueError, KeyError, OSError, sqlite3.Error):
            available = False
            text = (
                "Preview only • sends nothing."
                if self.context is None
                else "Sending unavailable • the owning task/agent or display profile is unavailable."
            )
        else:
            available = True
            text = f"Send only to this panel's owning agent • profile {profile}"
        self.send.setEnabled(available and self.preview is None)
        if self.preview_send is not None:
            self.preview_send.setEnabled(available)
        for editor in self.inputs.values():
            editor.setEnabled(available and self.preview is None)
        self.availability.setText(text)

    def clear_private(self):
        if self.preview is not None:
            self.preview.reject()
        for editor in self.inputs.values():
            if isinstance(editor, QLineEdit):
                editor.clear()
            else:
                editor.setCurrentIndex(0)
        self._attempt = self._sent_values = None

    def review(self):
        values = self.values()
        if values == self._sent_values:
            self.notice.setText(
                "Already queued for the agent. Edit a field to send another message."
            )
            return
        try:
            service, task_id, request_id, profile_key, profile = self.context()
            service.data_panels.form_available(task_id, request_id, profile_key)
            payload = service.data_panels.form_payload(
                task_id, request_id, self.definition["id"], values
            )
        except FormInputError as error:
            self.notice.setText(str(error))
            self.inputs[error.field_id].setFocus()
            return
        except (ValueError, KeyError, OSError, sqlite3.Error):
            self.notice.setText(
                "Check required fields, text limits and the agent connection. Nothing was sent."
            )
            return
        if self._attempt is None or self._attempt[0] != values:
            self._attempt = (values, uuid4().hex)
        submission_id = self._attempt[1]
        dialog = HtmlSettingsDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("Send form to agent")
        dialog.resize(600, 480)
        body = QVBoxLayout(dialog)
        objective = service.store.get(task_id).objective
        objective = "".join(
            c if c.isprintable() or c in "\n\t" else f"\\u{ord(c):04x}"
            for c in objective
        )
        if len(objective) > 200:
            objective = objective[:200] + "…"
        body.addWidget(
            plain_label(
                f"Recipient: {objective}\nTask ID: {task_id}\nDisplay profile: {profile}"
            )
        )
        body.addWidget(
            plain_label(
                "This sends the values below as a task message. The agent can act on your instructions using its own tools; Steed access still uses existing permissions and effect reviews."
            )
        )
        content = QPlainTextEdit()
        content.setReadOnly(True)
        message = json.loads(payload)
        readable = "\n\n".join(
            field["label"] + "\n" + field["value"] for field in message["fields"]
        )
        content.setPlainText(readable)
        content.setAccessibleName("Form values to send")
        body.addWidget(content, 1)
        details = QPushButton("Show message details")
        details.setCheckable(True)
        details.setAutoDefault(False)
        details.toggled.connect(
            lambda expanded: content.setPlainText(payload if expanded else readable)
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
            try:
                current = self.context()
                if current[:4] != (service, task_id, request_id, profile_key):
                    raise ValueError("The form recipient changed")
                service.data_panels.submit_form(
                    task_id,
                    request_id,
                    self.definition["id"],
                    submission_id,
                    values,
                    profile_key=profile_key,
                )
            except (ValueError, KeyError, OSError, sqlite3.Error):
                error.setText(
                    "Message was not queued. Reconnect the owning agent or check task limits, then retry. Your values are kept."
                )
                return
            self._sent_values = dict(values)
            self.notice.setText(
                "Queued for the agent • this is not confirmation that the agent has read or acted on it."
            )
            dialog.accept()

        def finished(*_):
            self.preview = None
            self.preview_send = None
            self.refresh_available()

        send.clicked.connect(deliver)
        dialog.finished.connect(finished)
        self.preview = dialog
        self.preview_send = send
        self.refresh_available()
        dialog.show()
