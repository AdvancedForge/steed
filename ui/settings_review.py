"""Your agent proposes changes to Steed's settings: every change as
now → after, applied only when you press Apply."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.task_policy import TaskPermissionError
from ui.html_dialog import HtmlSettingsDialog


class SettingsReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, proposal, parent=None):
        super().__init__(parent)
        self.service, self.proposal = service, proposal
        self.task_id = proposal.task_id
        self.request_key = proposal.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Your agent suggests changing Steed's settings")
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        document = proposal.document
        if document.get("summary"):
            summary = QLabel(document["summary"])
            summary.setTextFormat(Qt.TextFormat.PlainText)
            summary.setWordWrap(True)
            summary.setProperty("role", "quote")
            layout.addWidget(summary)
        rows = service.settings_proposals.changes(document)
        self.table = QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(["", "What", "Now", "After"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table, 1)
        self.profile = QComboBox()
        if document.get("sites"):
            names = []
            registry = service.profiles
            for owner in getattr(registry, "owners", ()) or ():
                name = getattr(owner, "current_profile_name", None)
                if name and name != "search" and name not in names:
                    names.append(name)
            for name in names or ["global"]:
                self.profile.addItem(name, name)
            layout.addWidget(QLabel("Site settings for profile"))
            layout.addWidget(self.profile)
        else:
            self.profile.hide()
        note = QLabel(
            "Nothing changes until you press Apply. Change any of it back later "
            "in Settings, Shortcuts, Search Keywords, Ad Blocking or a site's menu."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        apply = QPushButton("Apply")
        apply.setProperty("primary", True)
        apply.clicked.connect(self.apply)
        deny = QPushButton("Deny")
        deny.clicked.connect(self.decline)
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        for button in (apply, deny, later):
            row.addWidget(button)
        layout.addLayout(row)

    def _decide(self, approve):
        try:
            self.service.settings_proposals.resolve(
                self.task_id,
                self.proposal.request_id,
                approve=approve,
                profile=self.profile.currentData() if approve else None,
            )
        except (ValueError, KeyError, TaskPermissionError) as error:
            self.status.setText(str(error))
            return
        self.accept()

    def apply(self):
        self._decide(True)

    def decline(self):
        self._decide(False)
