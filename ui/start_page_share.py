"""Explicit source sharing reuses external agents' existing task conversations."""

import json
import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.task_messages import MESSAGE_BYTES
from services.task_policy import TaskPermissionError


class PageSourceShareDialog(QDialog):
    def __init__(self, service, profile, page, html, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Share page source with an agent")
        self.resize(620, 500)
        self.payload = json.dumps(
            {
                "type": "steed_page_source",
                "profile": profile,
                "page": page,
                "html": html,
            },
            ensure_ascii=False,
        )
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Send only the HTML shown below. It may contain text or links you added. The agent can propose changes through Steed's start-page tools; sending this message grants no browsing or file access."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.tasks = QComboBox()
        for task in service.store.list_tasks():
            if task.task_id in service.external_tasks and task.status in (
                "created",
                "running",
            ):
                self.tasks.addItem(
                    task.objective[:100] + " · " + task.task_id[:8], task.task_id
                )
        layout.addWidget(self.tasks)
        self.source = QPlainTextEdit(self.payload)
        self.source.setReadOnly(True)
        layout.addWidget(self.source, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.send = QPushButton("Send source")
        self.send.setAutoDefault(False)
        self.send.clicked.connect(self.submit)
        row.addWidget(self.send)
        cancel = QPushButton("Cancel")
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)
        if len(self.payload.encode()) > MESSAGE_BYTES:
            self.status.setText(
                "This source exceeds the conversation's 8 KB limit. Export HTML to an enabled shared folder, or copy it into your website chat."
            )
            self.send.setEnabled(False)
        elif not self.tasks.count():
            self.status.setText("No active connected agent is available.")
            self.send.setEnabled(False)

    def submit(self):
        try:
            self.service.send_message(self.tasks.currentData(), self.payload)
        except (ValueError, KeyError, TaskPermissionError):
            self.status.setText(
                "This agent is no longer available; reconnect and try again."
            )
            return
        except (OSError, sqlite3.Error):
            self.service._fatal()
            self.status.setText("The source could not be sent.")
            self.send.setEnabled(False)
            return
        self.send.setEnabled(False)
        self.accept()
