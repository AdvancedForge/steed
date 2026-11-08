"""Asking before a site gets a passkey made or used."""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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
