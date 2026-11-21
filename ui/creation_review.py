"""Your agent proposes a page of your own or an automation: the HTML or
every step shown, saved only when you press Add."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.task_policy import TaskPermissionError
from ui.html_dialog import HtmlSettingsDialog


def _profile(parent):
    manager = getattr(parent, "profile_manager", None)
    name = getattr(manager, "current_profile_name", None)
    return name if isinstance(name, str) and name else "global"


class CreationReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, proposal, parent=None):
        super().__init__(parent)
        self.service, self.proposal = service, proposal
        self.task_id = proposal.task_id
        self.request_key = proposal.request_key
        self.profile = _profile(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        document = proposal.document
        layout = QVBoxLayout(self)
        if proposal.kind == "page":
            self.setWindowTitle("Your agent made you a page")
            self.resize(720, 560)
            heading = QLabel(f"steed:{document['name']}  ·  {document['title']}")
            heading.setTextFormat(Qt.TextFormat.PlainText)
            heading.setProperty("role", "quote")
            layout.addWidget(heading)
            layout.addWidget(QLabel("Its HTML and JavaScript"))
            self.body = QPlainTextEdit(document["html"])
            self.body.setReadOnly(True)
            layout.addWidget(self.body, 1)
            note = (
                "Nothing is saved until you press Add. What its scripts may do "
                "follows Access → Local pages; you can edit or remove it in "
                "View → Your Pages."
            )
        else:
            from services.automations import describe_step, describe_trigger

            self.setWindowTitle("Your agent suggests an automation")
            self.resize(600, 460)
            heading = QLabel(
                f"{document['name']}: {describe_trigger(document['trigger'])}"
            )
            heading.setTextFormat(Qt.TextFormat.PlainText)
            heading.setWordWrap(True)
            heading.setProperty("role", "quote")
            layout.addWidget(heading)
            layout.addWidget(QLabel("Steps, in order"))
            self.body = QListWidget()
            for number, step in enumerate(document["steps"], 1):
                self.body.addItem(f"{number}. {describe_step(step)}")
            layout.addWidget(self.body, 1)
            note = (
                "Nothing is saved or run until you press Add. Steps that ask an "
                "assistant or act on pages follow your agents' permissions; "
                "pause, edit or remove it in Agents → Automations."
            )
        hint = QLabel(note)
        hint.setWordWrap(True)
        hint.setProperty("role", "note")
        layout.addWidget(hint)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.setProperty("primary", True)
        add.clicked.connect(self.apply)
        deny = QPushButton("Deny")
        deny.clicked.connect(self.decline)
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        for button in (add, deny, later):
            row.addWidget(button)
        layout.addLayout(row)

    def _decide(self, approve):
        try:
            self.service.creations.resolve(
                self.task_id,
                self.proposal.request_id,
                approve=approve,
                profile=self.profile,
            )
        except (ValueError, KeyError, OSError, TaskPermissionError) as error:
            self.status.setText(str(error))
            return
        self.accept()

    def apply(self):
        self._decide(True)

    def decline(self):
        self._decide(False)
