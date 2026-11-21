"""Unlock Bitwarden for this run, so Steed can sign in with its logins."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from services.bitwarden import Bitwarden, BitwardenError
from ui.html_dialog import HtmlSettingsDialog


class BitwardenDialog(HtmlSettingsDialog):
    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        if getattr(registry, "bitwarden", None) is None:
            registry.bitwarden = Bitwarden()
        self.bitwarden = registry.bitwarden
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Bitwarden")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Keep your passwords in Bitwarden? Unlock it here and Steed signs in "
            "with its logins the same way as with its own: filling the page "
            "itself, so no agent ever sees a password, and only when you allow "
            "it. Steed uses the Bitwarden command line (bw); sign in to it once "
            "in a terminal with bw login. The unlock lasts until you lock it or "
            "Steed closes, and nothing from Bitwarden is saved by Steed."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Bitwarden master password")
        self.password.returnPressed.connect(self.unlock)
        layout.addWidget(self.password)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "quote")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.unlock_button = QPushButton("Unlock")
        self.unlock_button.setProperty("primary", True)
        self.unlock_button.clicked.connect(self.unlock)
        self.lock_button = QPushButton("Lock")
        self.lock_button.clicked.connect(self.lock)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (self.unlock_button, self.lock_button, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self, message=""):
        if not self.bitwarden.available:
            text = (
                "The Bitwarden command line (bw) is not installed. Install it, "
                "sign in once with bw login, then open this again."
            )
        elif self.bitwarden.unlocked:
            text = "Unlocked: Steed can sign in with your Bitwarden logins."
        else:
            text = "Locked."
        self.status.setText(message or text)
        self.status.setProperty(
            "role", "quote" if self.bitwarden.unlocked else "warning"
        )
        self.unlock_button.setEnabled(
            self.bitwarden.available and not self.bitwarden.unlocked
        )
        self.password.setEnabled(self.unlock_button.isEnabled())
        self.lock_button.setEnabled(self.bitwarden.unlocked)

    def unlock(self):
        try:
            self.bitwarden.unlock(self.password.text())
        except BitwardenError as error:
            self.password.clear()
            self.refresh(str(error))
            return
        self.password.clear()
        self.refresh()

    def lock(self):
        try:
            self.bitwarden.lock()
        except BitwardenError:
            pass
        self.refresh()
