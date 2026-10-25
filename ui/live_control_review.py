"""One clear approval for an agent-requested live-tab takeover."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from services.live_control import resolve
from ui.html_dialog import HtmlSettingsDialog


class LiveControlDialog(HtmlSettingsDialog):
    def __init__(self, service, record, parent=None):
        super().__init__(parent)
        self.service, self.record = service, record
        self.task_id = record.task_id
        self.request_key = record.request_key
        self.setWindowTitle("Give this agent control of your tabs?")
        self.resize(510, 310)
        layout = QVBoxLayout(self)
        task = service.store.get(record.task_id)
        titles = [
            v().title()[:160] or "Untitled website"
            for v in record.views
            if v() is not None
        ]
        text = QLabel(
            "Task: "
            + task.objective[:256]
            + "\nProfile: "
            + record.profile
            + "\nTabs: "
            + ", ".join(titles)
            + "\nSites: "
            + ", ".join(dict.fromkeys(record.origins))
            + "\n\nThe agent can see screenshots, click, type and submit data using these signed-in tabs. Normal website scripts and background traffic continue.\n\nTake Back above a tab ends agent control. Your other tabs remain available to you."
        )
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        layout.addWidget(text)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        deny = QPushButton("Decline")
        deny.setDefault(True)
        self.allow_button = QPushButton("Give control")
        self.allow_button.setAutoDefault(False)
        deny.clicked.connect(lambda: self.decide(False))
        self.allow_button.clicked.connect(lambda: self.decide(True))
        row.addWidget(deny)
        row.addWidget(self.allow_button)
        layout.addLayout(row)
        self.finished.connect(
            lambda result: (
                resolve(service, record, approve=False)
                if result == QDialog.DialogCode.Rejected and record.state == "requested"
                else None
            )
        )

    def decide(self, approve):
        try:
            result = resolve(self.service, self.record, approve=approve)
        except (ValueError, RuntimeError, OSError):
            self.error.setText(
                "Control could not be given; the tabs, permissions or task changed. Select a browsing tab and request control again."
            )
            return
        if result["state"] == "stale":
            self.error.setText(
                "The selected window or tabs changed. Request control again."
            )
            self.allow_button.setEnabled(False)
            return
        self.accept()
