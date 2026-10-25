"""Simple, non-executing user review of an external agent's page proposal."""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.task_policy import TaskPermissionError
from start_page_format import state_digest
from ui.html_dialog import HtmlSettingsDialog


class StartPageReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, proposal, parent=None):
        super().__init__(parent)
        self.service, self.proposal = service, proposal
        self.task_id = proposal.task_id
        self.request_key = proposal.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Review start page")
        self.resize(680, 530)
        self.definition = service.start_pages.definition(proposal)
        layout = QVBoxLayout(self)
        title = QLabel(self.definition.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)
        document = self.definition.document
        page = "New Tab" if self.definition.page == "newtab" else "Welcome"
        description = (
            f"Replace {page} with a self-contained HTML page. Inline JavaScript runs after you apply it. It cannot read browser data or call native Steed actions."
            if document["mode"] == "custom"
            else f"Restore the built-in {page} page."
        )
        if self.definition.page == "style":
            self.setWindowTitle("Review theme and CSS")
            description = (
                "Change theme colors, root CSS or browser bars for the profile you select. CSS changes presentation only; no browser, account or file access is granted. View → Reset Page Style recovers from unusable CSS.\nChanged fields: "
                + ", ".join(document["style"])
            )
        if "settings" in document:
            description += "\nSections: " + ", ".join(document["settings"]["modules"])
            if document["settings"]["feed"]:
                description += "\nNews feed: " + document["settings"]["feed"]
        notice = QLabel(description)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.profile = QComboBox()
        names = {"global"}
        names.update(
            p.name
            for p in service.profiles.config.profiles_dir.iterdir()
            if p.is_dir() and p.name not in ("search", "global_profile")
        )
        self.profile.addItems(sorted(names))
        manager = getattr(parent, "profile_manager", None)
        if manager and manager.current_profile_name in names:
            self.profile.setCurrentText(manager.current_profile_name)
        layout.addWidget(QLabel("Apply to this profile:"))
        layout.addWidget(self.profile)
        self.all_profiles = QCheckBox(
            "Apply styling to all profiles and use for new profiles (replaces appearance overrides)"
        )
        self.all_profiles.setVisible(self.definition.page == "style")
        layout.addWidget(self.all_profiles)
        self.source = QPlainTextEdit(self.definition.source)
        self.source.setReadOnly(True)
        layout.addWidget(
            QLabel("Exact page declaration (preview does not run scripts):")
        )
        layout.addWidget(self.source, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.apply_button = QPushButton("Apply page")
        self.apply_button.setAutoDefault(False)
        self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.apply_button)
        decline = QPushButton("Decline")
        decline.setDefault(True)
        decline.clicked.connect(self.decline)
        row.addWidget(decline)
        close = QPushButton("Later")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.profile.currentTextChanged.connect(self.capture_base)
        self.capture_base()

    def capture_base(self):
        registry = self.service.profiles
        self.profile_key = registry.start_pages.profile_key(
            self.profile.currentText(), create=True
        )
        self.base = state_digest(
            registry.page_style.get(self.profile.currentText())
            if self.definition.page == "style"
            else registry.start_pages.state(self.profile.currentText())
        )

    def apply(self):
        try:
            self.service.start_pages.resolve(
                self.proposal.task_id,
                self.proposal.request_id,
                approve=True,
                profile=self.profile.currentText(),
                profile_key=self.profile_key,
                base=self.base,
                all_profiles=self.all_profiles.isChecked()
                if self.definition.page == "style"
                else False,
            )
        except (ValueError, KeyError, TaskPermissionError):
            self.status.setText(
                "The request or page changed. Close and review it again."
            )
            self.apply_button.setEnabled(False)
            return
        except (OSError, sqlite3.Error):
            self.service._fatal()
            self.status.setText(
                "The page could not be saved. Task commands have stopped."
            )
            self.apply_button.setEnabled(False)
            return
        self.accept()

    def decline(self):
        try:
            self.service.start_pages.resolve(
                self.proposal.task_id, self.proposal.request_id, approve=False
            )
        except (ValueError, KeyError, TaskPermissionError):
            self.status.setText("This request is no longer available.")
            return
        except (OSError, sqlite3.Error):
            self.service._fatal()
            self.status.setText("The decision could not be saved.")
            return
        self.accept()
