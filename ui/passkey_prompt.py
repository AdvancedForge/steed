"""Asking before a site gets a passkey made or used."""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)


def _account(user_name, display):
    if display and user_name and display != user_name:
        return f"{display} ({user_name})"
    return display or user_name or "this account"


def confirm_new_passkey(window, rp_id, user) -> str:
    """ "steed" to save one in Steed, "key" for a security key, or "" for no."""
    dialog = QDialog(window)
    dialog.setWindowTitle("Create a passkey")
    layout = QVBoxLayout(dialog)
    text = QLabel(
        f"Create a passkey for {rp_id}, for "
        f"{_account(user.get('name'), user.get('displayName'))}?\n\n"
        "Steed keeps it in this profile's encrypted password vault and asks "
        "before each sign-in."
    )
    text.setWordWrap(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox()
    create = buttons.addButton("Create Passkey", QDialogButtonBox.ButtonRole.AcceptRole)
    key = QPushButton("Use a Security Key")
    buttons.addButton(key, QDialogButtonBox.ButtonRole.ActionRole)
    buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
    choice = {"value": ""}
    create.clicked.connect(lambda: choice.update(value="steed"))
    key.clicked.connect(lambda: (choice.update(value="key"), dialog.accept()))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
    return choice["value"]


def choose_passkey(window, rp_id, passkeys):
    """The passkey to sign in with, "key" for a security key, or None."""
    dialog = QDialog(window)
    dialog.setWindowTitle("Sign in with a passkey")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"Sign in to {rp_id} with:"))
    accounts = QListWidget()
    for passkey in passkeys:
        accounts.addItem(_account(passkey["user_name"], passkey["user_display"]))
    accounts.setCurrentRow(0)
    accounts.itemActivated.connect(lambda _: dialog.accept())
    layout.addWidget(accounts)
    buttons = QDialogButtonBox()
    buttons.addButton("Sign In", QDialogButtonBox.ButtonRole.AcceptRole)
    key = QPushButton("Use a Security Key")
    buttons.addButton(key, QDialogButtonBox.ButtonRole.ActionRole)
    buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
    choice = {"value": None}
    key.clicked.connect(lambda: (choice.update(value="key"), dialog.reject()))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted and accounts.currentRow() >= 0:
        return passkeys[accounts.currentRow()]
    return choice["value"]


class PasskeyBar(QFrame):
    """A site asked quietly for a passkey (on its sign-in page): offer them."""

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("PasswordBar")  # Same look as the password bar.
        self.tab = self.ask = self.chosen = None
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.choose = QPushButton("Choose…", self)
        self.choose.setAutoDefault(False)
        self.choose.clicked.connect(self._choose)
        row.addWidget(self.choose)
        later = QPushButton("Not now", self)
        later.setAutoDefault(False)
        later.clicked.connect(self.clear)
        row.addWidget(later)
        self.hide()

    def offer(self, tab, ask, rp_id, chosen):
        self.tab, self.ask, self.chosen = tab, ask, chosen
        self.label.setText(f"Sign in to {rp_id} with a saved passkey?")
        self.show()

    def _choose(self):
        chosen = self.chosen
        self.clear()
        if chosen is not None:
            chosen()

    def withdraw(self, tab, ask):
        """The site cancelled its request."""
        if tab is self.tab and ask == self.ask:
            self.clear()

    def tab_changed(self, tab):
        """Another tab, or a new page in this one: the offer was for that page."""
        if self.tab is not None and tab is not self.tab:
            self.clear()

    def page_changed(self, tab):
        if tab is self.tab:
            self.clear()

    def clear(self):
        self.tab = self.ask = self.chosen = None
        self.hide()
