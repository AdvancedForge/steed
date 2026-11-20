"""Your agent asks to sign in with a saved password: allow (once or always)
or deny. Steed fills and submits; the agent never sees the login."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from services.task_policy import TaskPermissionError
from ui.html_dialog import HtmlSettingsDialog


class SigninReviewDialog(HtmlSettingsDialog):
    def __init__(self, service, request, parent=None):
        super().__init__(parent)
        self.service, self.request = service, request
        self.task_id = request.task_id
        self.request_key = request.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"Your agent wants to sign in to {request.site}")
        layout = QVBoxLayout(self)
        note = QLabel(
            f"Steed will fill in your saved login for {request.site} and submit "
            "it, with its one-time code if it has one. Your agent never sees the "
            "password, the code or the username; it is only told whether the "
            "sign-in worked."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
        self.account = QComboBox()
        for login_id, username in request.accounts:
            self.account.addItem(username or "(no username)", login_id)
        self.account.setVisible(len(request.accounts) > 1)
        if len(request.accounts) > 1:
            layout.addWidget(QLabel("Account"))
        layout.addWidget(self.account)
        self.always = QCheckBox(
            "Always allow sign-ins with saved passwords (Access && Permissions)"
        )
        layout.addWidget(self.always)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        allow = QPushButton("Sign in")
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
            self.service.signin.resolve(
                self.task_id,
                self.request.request_id,
                approve=approve,
                login_id=self.account.currentData() if approve else None,
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
