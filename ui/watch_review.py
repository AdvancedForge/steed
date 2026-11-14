"""Review an agent's proposed watch: the person can adjust it, then add it."""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from services.task_policy import TaskPermissionError
from services.watches import INTERVAL_LABELS
from ui.html_dialog import HtmlSettingsDialog


class WatchReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, proposal, parent=None):
        super().__init__(parent)
        self.service, self.proposal = service, proposal
        self.task_id = proposal.task_id
        self.request_key = proposal.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Your agent suggests a watch")
        layout = QVBoxLayout(self)
        page = QLabel(f"{proposal.name}\n{proposal.url}")
        page.setTextFormat(Qt.TextFormat.PlainText)
        page.setWordWrap(True)
        page.setProperty("role", "quote")
        layout.addWidget(page)
        layout.addWidget(QLabel("Tell me"))
        self.instruction = QLineEdit(proposal.instruction)
        self.instruction.setPlaceholderText("Any change")
        self.instruction.setMaxLength(400)
        layout.addWidget(self.instruction)
        layout.addWidget(QLabel("How often to look"))
        self.every = QComboBox()
        for key, label in INTERVAL_LABELS.items():
            self.every.addItem(label, key)
        self.every.setCurrentIndex(list(INTERVAL_LABELS).index(proposal.every))
        layout.addWidget(self.every)
        layout.addWidget(QLabel("Then (optional)"))
        self.then = QLineEdit(proposal.then_action)
        self.then.setPlaceholderText("Nothing: just tell me")
        self.then.setMaxLength(400)
        layout.addWidget(self.then)
        self.then_now = QCheckBox("Start without asking me first")
        self.then_now.setEnabled(bool(proposal.then_action))
        self.then.textChanged.connect(
            lambda text: self.then_now.setEnabled(bool(text.strip()))
        )
        layout.addWidget(self.then_now)
        self.profile = QComboBox()
        names = {"global"}
        profiles_dir = service.profiles.config.profiles_dir
        if profiles_dir.is_dir():
            names.update(
                p.name
                for p in profiles_dir.iterdir()
                if p.is_dir() and p.name not in ("search", "global_profile")
            )
        self.profile.addItems(sorted(names))
        manager = getattr(parent, "profile_manager", None)
        if manager and manager.current_profile_name in names:
            self.profile.setCurrentText(manager.current_profile_name)
        self.profile_label = QLabel("Look with the sign-ins of")
        self.profile_label.setVisible(len(names) > 1)
        self.profile.setVisible(len(names) > 1)
        layout.addWidget(self.profile_label)
        layout.addWidget(self.profile)
        note = QLabel(
            "Steed reads this page in the background and tells you when it "
            "changes the way you asked. Your agent still asks before it acts "
            "on a site."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.apply_button = QPushButton("Add watch")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.apply_button)
        decline = QPushButton("Decline")
        decline.setAutoDefault(False)
        decline.clicked.connect(self.decline)
        row.addWidget(decline)
        later = QPushButton("Later")
        later.setAutoDefault(False)
        later.clicked.connect(self.reject)
        row.addWidget(later)
        layout.addLayout(row)

    def apply(self):
        try:
            self.service.watches.resolve(
                self.task_id,
                self.proposal.request_id,
                approve=True,
                profile=self.profile.currentText(),
                instruction=self.instruction.text(),
                every=self.every.currentData(),
                then=self.then.text(),
                then_mode="now"
                if self.then_now.isChecked() and self.then.text().strip()
                else "ask",
            )
        except (ValueError, KeyError, TaskPermissionError) as error:
            self.status.setText(str(error) or "This suggestion is no longer available.")
            self.apply_button.setEnabled(False)
            return
        except (OSError, sqlite3.Error):
            self.service._fatal()
            self.status.setText("The watch could not be saved.")
            self.apply_button.setEnabled(False)
            return
        self.accept()

    def decline(self):
        try:
            self.service.watches.resolve(
                self.task_id, self.proposal.request_id, approve=False
            )
        except (ValueError, KeyError, TaskPermissionError):
            self.status.setText("This suggestion is no longer available.")
            return
        except (OSError, sqlite3.Error):
            self.service._fatal()
            self.status.setText("The decision could not be saved.")
            return
        self.accept()
