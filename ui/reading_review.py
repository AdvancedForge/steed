"""Your agent asks to search what you've read: allow (once or always) or deny."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from services.task_policy import TaskPermissionError
from ui.html_dialog import HtmlSettingsDialog


class ReadingReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, request, parent=None):
        super().__init__(parent)
        self.service, self.request = service, request
        self.task_id = request.task_id
        self.request_key = request.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Your agent wants to search what you've read")
        layout = QVBoxLayout(self)
        words = QLabel(f"“{request.query}”")
        words.setTextFormat(Qt.TextFormat.PlainText)
        words.setWordWrap(True)
        words.setProperty("role", "quote")
        layout.addWidget(words)
        note = QLabel(
            "If you allow it, your agent gets the pages you read that match these "
            "words: their titles, addresses, when you read them and some of their "
            "text."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
        self.always = QCheckBox("Always allow searches (Access && Permissions)")
        layout.addWidget(self.always)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        allow = QPushButton("Allow")
        allow.setProperty("primary", True)
        allow.clicked.connect(self.apply)
        deny = QPushButton("Deny")
        deny.clicked.connect(self.decline)
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        for button in (allow, deny, later):
            row.addWidget(button)
        layout.addLayout(row)

    def _decide(self, approve):
        try:
            self.service.reading.resolve(
                self.task_id,
                self.request.request_id,
                approve=approve,
                always=approve and self.always.isChecked(),
            )
        except (ValueError, KeyError, TaskPermissionError) as error:
            self.status.setText(str(error))
            return
        self.accept()

    def apply(self):
        self._decide(True)

    def decline(self):
        self._decide(False)
