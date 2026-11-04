"""Non-executing review of an agent's proposed user script or panel app."""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.task_policy import TaskPermissionError
from ui.html_dialog import HtmlSettingsDialog


def _label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class CustomizationReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, proposal, parent=None):
        super().__init__(parent)
        self.service, self.proposal = service, proposal
        self.task_id = proposal.task_id
        self.request_key = proposal.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.definition = service.customizations.definition(proposal)
        document = self.definition.document
        script = self.definition.kind == "user_script"
        self.setWindowTitle("Review user script" if script else "Review panel app")
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        title = _label(self.definition.name)
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(title)
        if script:
            everywhere = "*" in document["sites"]
            sites = (
                "every website you visit"
                if everywhere
                else ", ".join(document["sites"])
            )
            parts = []
            if document["css"].strip():
                parts.append("change how pages look (CSS)")
            if document["js"].strip():
                parts.append(
                    "run JavaScript in those pages, with each page's own permissions: "
                    "it can read and change what the page shows and send it to that site"
                )
            description = (
                f"An agent proposes a user script for {sites}. It would "
                + " and ".join(parts)
                + ". It gets none of Steed's data (tabs, history, passwords). It is "
                "added as a new entry you can switch off or delete in "
                "steed:userscripts."
            )
            if everywhere and document["js"].strip():
                description = "⚠ Runs on every website. " + description
        else:
            description = (
                "An agent proposes a panel app: one HTML page for the App Panel. It "
                "runs at its own steed://app-… address with its own storage, can use "
                "Steed data only through the app API under Local pages access "
                "settings, and is added as a new app you can delete in steed:apps."
            )
        layout.addWidget(_label(description))
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
        layout.addWidget(_label("Add to this profile:"))
        layout.addWidget(self.profile)
        layout.addWidget(_label("Exact code (shown, not run):"))
        if script:
            code = ""
            if document["css"].strip():
                code += "/* CSS */\n" + document["css"] + "\n\n"
            if document["js"].strip():
                code += f"// JavaScript, runs when the page is {document['when']}\n"
                code += document["js"]
        else:
            code = document["html"]
        self.source = QPlainTextEdit(code)
        self.source.setReadOnly(True)
        layout.addWidget(self.source, 1)
        self.status = _label("")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.apply_button = QPushButton("Add script" if script else "Add app")
        self.apply_button.setAutoDefault(False)
        self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.apply_button)
        decline = QPushButton("Decline")
        decline.setDefault(True)
        decline.clicked.connect(self.decline)
        row.addWidget(decline)
        later = QPushButton("Later")
        later.setAutoDefault(False)
        later.clicked.connect(self.reject)
        row.addWidget(later)
        layout.addLayout(row)

    def apply(self):
        registry = self.service.profiles
        profile = self.profile.currentText()
        try:
            key = registry.start_pages.profile_key(profile, create=True)
            self.service.customizations.resolve(
                self.proposal.task_id,
                self.proposal.request_id,
                approve=True,
                profile=profile,
                profile_key=key,
            )
        except (ValueError, KeyError, TaskPermissionError) as error:
            self.status.setText(f"It could not be added: {error}")
            self.apply_button.setEnabled(False)
            return
        except (OSError, sqlite3.Error):
            self.service._fatal()
            self.status.setText("It could not be saved. Task commands have stopped.")
            self.apply_button.setEnabled(False)
            return
        self.accept()

    def decline(self):
        try:
            self.service.customizations.resolve(
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
