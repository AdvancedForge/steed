"""General → Sync…: a folder shared between computers, and the passphrase."""

from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from browser.password_vault import VaultError
from ui.html_dialog import HtmlSettingsDialog


class SyncDialog(HtmlSettingsDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Sync")
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Pick a folder your other computers can reach (Syncthing, Nextcloud, Dropbox, a USB stick). Bookmarks, saved groups, the reading list and search keywords stay in step through it. Nothing goes to a Steed server."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText("No sync folder")
        row.addWidget(self.folder, 1)
        choose = QPushButton("Choose…")
        choose.setAutoDefault(False)
        choose.clicked.connect(self.choose)
        row.addWidget(choose)
        self.stop = QPushButton("Stop Syncing")
        self.stop.setAutoDefault(False)
        self.stop.clicked.connect(lambda: self.set_folder(""))
        row.addWidget(self.stop)
        layout.addLayout(row)
        passwords = QLabel(
            "Passwords sync too with a sync passphrase, the same on every computer. "
            "They are sealed with it in the folder, and a profile's passwords sync "
            "while its vault is unlocked."
        )
        passwords.setWordWrap(True)
        layout.addWidget(passwords)
        row = QHBoxLayout()
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase.setPlaceholderText("Sync passphrase (8 characters or more)")
        self.passphrase.returnPressed.connect(self.sync_passwords)
        row.addWidget(self.passphrase, 1)
        self.start_passwords = QPushButton("Sync Passwords")
        self.start_passwords.setAutoDefault(False)
        self.start_passwords.clicked.connect(self.sync_passwords)
        row.addWidget(self.start_passwords)
        self.stop_passwords = QPushButton("Stop Syncing Passwords")
        self.stop_passwords.setAutoDefault(False)
        self.stop_passwords.clicked.connect(self.no_passwords)
        row.addWidget(self.stop_passwords)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status, 1)
        row = QHBoxLayout()
        self.now = QPushButton("Sync Now")
        self.now.setAutoDefault(False)
        self.now.clicked.connect(self.sync_now)
        row.addWidget(self.now)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        service.synced.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        folder = self.service.folder
        self.folder.setText(str(folder) if folder else "")
        self.stop.setEnabled(folder is not None)
        self.now.setEnabled(folder is not None)
        on = bool(self.service.config.get("sync_passwords", False))
        self.passphrase.setVisible(not on)
        self.passphrase.setEnabled(folder is not None)
        self.start_passwords.setVisible(not on)
        self.start_passwords.setEnabled(folder is not None)
        self.stop_passwords.setVisible(on)
        self.status.setText(self.service.status)

    def choose(self):
        folder = QFileDialog.getExistingDirectory(self, "Sync Folder")
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder):
        self.service.set_folder(folder)
        if folder:
            self.service.run()
        self.refresh()

    def sync_passwords(self):
        try:
            self.service.set_passphrase(self.passphrase.text())
        except VaultError as error:
            self.status.setText(str(error))
            return
        self.passphrase.clear()
        self.service.run()
        self.refresh()

    def no_passwords(self):
        self.service.stop_passwords()
        self.refresh()

    def sync_now(self):
        self.service.run()
        self.refresh()
