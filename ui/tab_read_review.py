"""Your agent asks to read the tab you have open: allow once or deny."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from services.task_policy import TaskPermissionError
from ui.html_dialog import HtmlSettingsDialog


class TabReadReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, request, parent=None):
        super().__init__(parent)
        self.service, self.request = service, request
        self.task_id = request.task_id
        self.request_key = request.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Let your agent read this page?")
        layout = QVBoxLayout(self)
        page = QLabel(f"{request.title}\n{request.url}")
        page.setTextFormat(Qt.TextFormat.PlainText)
        page.setWordWrap(True)
        page.setProperty("role", "quote")
        layout.addWidget(page)
        note = QLabel(
            "It gets this page's text once, to answer you. It can't click, type, "
            "go anywhere or see your other tabs."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        allow = QPushButton("Allow")
        allow.setProperty("primary", True)
        allow.clicked.connect(self.apply)
        deny = QPushButton("Deny")
        deny.clicked.connect(self.decline)
        for button in (allow, deny):
            row.addWidget(button)
        layout.addLayout(row)

    def _decide(self, approve):
        try:
            self.service.tab_reading.resolve(
                self.task_id, self.request.request_id, approve=approve
            )
        except (ValueError, KeyError, TaskPermissionError) as error:
            self.status.setText(str(error))
            return
        self.accept()

    def apply(self):
        self._decide(True)

    def decline(self):
        self._decide(False)
