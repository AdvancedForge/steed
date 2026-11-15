"""Adding extensions: what they ask for, the store download and its page bar."""

from PyQt6.QtCore import QObject, Qt, QUrl
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton

from extensions import icon_from, inspect, store_id


def confirm(window, details) -> bool:
    """Ask before installing, listing what the extension asks for."""
    box = QMessageBox(window)
    box.setWindowTitle("Add Extension")
    box.setText(f"Add {details['name']} {details['version']}?".replace("  ", " "))
    lines = details["permissions"] or ["Nothing beyond its own pages"]
    box.setInformativeText(
        (details["description"] + "\n\n" if details["description"] else "")
        + "It can:\n"
        + "\n".join(f"• {line}" for line in lines)
        + (
            "\n\nSteed's engine lacks Chrome features it uses ("
            + ", ".join(details["unsupported"])
            + "), so it may not work."
            if details["unsupported"]
            else ""
        )
        + "\n\nAdd only extensions you trust."
    )
    icon = icon_from(details["icon"])
    if not icon.isNull():
        box.setIconPixmap(icon.pixmap(48, 48))
    add = box.addButton("Add Extension", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return box.clickedButton() is add


class ExtensionInstaller(QObject):
    """One per window: checks, asks, then installs into the window's profile."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.extensions = window.profile_manager.registry.extensions
        self.extensions.downloaded.connect(self._downloaded)
        self.extensions.failed.connect(self._failed)
        self.waiting = set()  # (profile, store id) this window asked for

    @property
    def profile(self):
        return self.window.profile_manager.current_profile_name

    def say(self, text):
        self.window.status_bar.showMessage(text, 8000)

    def add_path(self, path, *, store=None) -> bool:
        """Install a folder, .zip or .crx after asking; False when not added."""
        if not self.extensions.available(self.profile):
            self.say("Incognito windows have no extensions.")
            return False
        try:
            details = inspect(path)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return False
        if not confirm(self.window, details):
            self.say("Not added.")
            return False
        try:
            self.extensions.install(self.profile, path, store=store)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return False
        self.say(f"Adding {details['name']}…")
        return True

    def add_store(self, text) -> bool:
        """Download from a Chrome Web Store address or id, then add_path."""
        extension_id = store_id(text)
        if extension_id is None:
            self.say("That is not a Chrome Web Store extension address.")
            return False
        if not self.extensions.available(self.profile):
            self.say("Incognito windows have no extensions.")
            return False
        if self.extensions.from_store(self.profile, extension_id) is not None:
            self.say("That extension is already added.")
            return False
        self.waiting.add((self.profile, extension_id))
        self.extensions.download(self.profile, extension_id)
        self.say("Downloading from the Chrome Web Store…")
        return True

    def _downloaded(self, profile, extension_id, path, error):
        if (profile, extension_id) not in self.waiting:
            return
        self.waiting.discard((profile, extension_id))
        if error:
            self.say(error)
        elif profile == self.profile:
            self.add_path(path, store=extension_id)
        # Installed or not, the download is no longer needed.
        if path:
            self.extensions.discard_download(path)

    def _failed(self, profile, message):
        if profile == self.profile:
            self.say(f"Extension not added: {message}")


class StoreBar(QFrame):
    """On a Chrome Web Store extension page: "Add to Steed"."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setObjectName("PasswordBar")  # Same look as the password bar.
        self.extension_id = None
        self.url = QUrl()
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.add = QPushButton("Add to Steed", self)
        self.add.setAutoDefault(False)
        self.add.clicked.connect(self._add)
        row.addWidget(self.add)
        close = QPushButton("✕", self)
        close.setAccessibleName("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.hide)
        row.addWidget(close)
        self.hide()

    def update_for(self, url: QUrl):
        self.url = QUrl(url)
        extension_id = store_id(url.toString())
        extensions = self.window.profile_manager.registry.extensions
        profile = self.window.profile_manager.current_profile_name
        if extension_id is None or not extensions.available(profile):
            self.extension_id = None
            self.hide()
            return
        if extension_id == self.extension_id and self.isHidden():
            return  # Closed for this extension.
        self.extension_id = extension_id
        added = extensions.from_store(profile, extension_id) is not None
        self.label.setText(
            "This extension is added to Steed."
            if added
            else "Chrome's own button does not work here: add this extension to "
            "Steed instead. Some extensions need Chrome features Steed lacks."
        )
        self.add.setVisible(not added)
        self.show()

    def _add(self):
        if self.extension_id and self.window.extension_installer.add_store(
            self.extension_id
        ):
            self.add.setEnabled(False)
            self.label.setText("Downloading…")
            self.window.profile_manager.registry.extensions.changed.connect(
                self._refresh, Qt.ConnectionType.SingleShotConnection
            )

    def _refresh(self, *_):
        self.add.setEnabled(True)
        self.extension_id = None
        self.update_for(self.url)
