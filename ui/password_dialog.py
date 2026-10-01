"""In-browser KeePassXC login selection, filling, and saving."""

from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from password_forms import (
    PASSWORD_WORLD,
    fill_form_script,
    inspect_form_script,
    login_url,
)


class SaveLoginDialog(QDialog):
    def __init__(self, site, login, password, parent):
        super().__init__(parent)
        self.setWindowTitle("Save Login to KeePassXC")
        layout = QFormLayout(self)
        label = QLabel(site)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addRow("Website", label)
        self.login = QLineEdit(login)
        self.password = QLineEdit(password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Username", self.login)
        layout.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class PasswordDialog(QDialog):
    def __init__(self, manager, profile, page, parent, auto_fill=False):
        super().__init__(parent)
        self.manager = manager
        self.profile = profile
        self.page = page
        self.auto_fill = auto_fill
        self.entries = []
        self.context = None
        self._closed = False
        self._busy = False
        self.setWindowTitle("Passwords — KeePassXC")
        self.resize(460, 340)
        layout = QVBoxLayout(self)
        self.site = QLabel(page.url().host() or "Open a login page to fill passwords")
        self.site.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.site)
        self.status = QLabel(
            "Keep KeePassXC running with your vault open. Enable Browser Integration "
            "in its settings, then connect once for this profile."
        )
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.logins = QListWidget()
        self.logins.currentRowChanged.connect(self._selection_changed)
        self.logins.itemDoubleClicked.connect(lambda _: self.fill())
        layout.addWidget(self.logins, 1)
        row = QHBoxLayout()
        self.connect_button = QPushButton("Connect KeePassXC")
        self.connect_button.clicked.connect(self.connect_vault)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.connect_button)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.save_button = QPushButton("Save This Login...")
        self.save_button.clicked.connect(self.save_login)
        self.fill_button = QPushButton("Fill Login")
        self.fill_button.setDefault(True)
        self.fill_button.clicked.connect(self.fill)
        self.fill_button.setEnabled(False)
        row.addWidget(self.save_button)
        row.addWidget(self.fill_button)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def _alive(self):
        return not self._closed and not sip.isdeleted(self.page)

    def _selection_changed(self, *_):
        self.fill_button.setEnabled(not self._busy and self.logins.currentRow() >= 0)

    def _set_busy(self, value):
        self._busy = value
        for button in (self.connect_button, self.refresh_button, self.save_button):
            button.setEnabled(not value)
        self._selection_changed()

    def _submit(self, operation, data, callback):
        self._set_busy(True)
        self.status.setText(
            "Waiting for KeePassXC. Unlock or approve access there if prompted."
        )

        def finished(result, error):
            if not self._alive():
                return
            self._set_busy(False)
            if error:
                self.status.setText(error)
            else:
                callback(result)

        self.manager.submit(self, self.profile, operation, data, finished)

    def connect_vault(self):
        self._submit("connect", {}, lambda _: self.refresh())

    def _inspect(self, callback, read_values=False):
        if not self._alive() or login_url(self.page.url()) is None:
            self.status.setText("Open an HTTPS login page to fill or save passwords.")
            return
        token = uuid4().hex
        expected = bytes(self.page.url().toEncoded()).decode("ascii")

        def inspected(context):
            if not self._alive():
                return
            if bytes(self.page.url().toEncoded()).decode("ascii") != expected:
                self.status.setText("The page changed. Retry the password action.")
                return
            if not isinstance(context, dict) or context.get("token") != token:
                self.status.setText(
                    context.get("error", "Could not inspect this login form.")
                    if isinstance(context, dict)
                    else "Could not inspect this login form."
                )
                return
            if context.get("url") != expected:
                self.status.setText("The page changed. Retry the password action.")
                return
            callback(context)

        self.page.runJavaScript(
            inspect_form_script(token, read_values), PASSWORD_WORLD, inspected
        )

    def refresh(self):
        if self._busy:
            return
        self.entries.clear()
        self.logins.clear()
        self.context = None

        def inspected(context):
            self.context = context
            self.site.setText(self.page.url().host())
            self._submit(
                "get-logins",
                {
                    "url": login_url(QUrl(context["url"])),
                    "submitUrl": login_url(QUrl(context["submitUrl"])),
                },
                self._loaded,
            )

        self._inspect(inspected)

    def _loaded(self, entries):
        # Keep only selection metadata while the dialog is open. Fetch again on
        # Fill so a vault locked after listing cannot release cached passwords.
        self.entries = [
            {key: entry.get(key, "") for key in ("name", "login", "uuid")}
            for entry in entries
        ]
        for entry in entries:
            name = entry.get("name", "Login")
            self.logins.addItem(f"{name} — {entry['login']}")
        self.status.setText(
            "Choose a login to fill."
            if entries
            else "No matching logins. Save one below."
        )
        if entries:
            self.logins.setCurrentRow(0)
            if self.auto_fill and len(entries) == 1:
                self.fill()
        self.auto_fill = False

    def fill(self):
        if self._busy or not self._alive() or self.context is None:
            return
        index = self.logins.currentRow()
        if not 0 <= index < len(self.entries):
            return
        context = self.context
        if bytes(self.page.url().toEncoded()).decode("ascii") != context["url"]:
            self.status.setText("The page changed. Refresh the login list.")
            return
        selected = self.entries[index]

        def fetched(entries):
            matches = [
                entry
                for entry in entries
                if (
                    entry.get("uuid") == selected["uuid"]
                    if selected["uuid"]
                    else entry.get("login") == selected["login"]
                    and entry.get("name", "") == selected["name"]
                )
            ]
            if len(matches) != 1:
                self.status.setText(
                    "This login changed or is unavailable. Refresh the list."
                )
                return
            if bytes(self.page.url().toEncoded()).decode("ascii") != context["url"]:
                self.status.setText("The page changed. Retry filling.")
                return
            entry = matches[0]
            self._set_busy(True)
            self.page.runJavaScript(
                fill_form_script(
                    context["token"], context["url"], entry["login"], entry["password"]
                ),
                PASSWORD_WORLD,
                filled,
            )

        def filled(result):
            if not self._alive():
                return
            self._set_busy(False)
            if isinstance(result, dict) and result.get("filled"):
                self.parent().status_bar.showMessage(
                    "Login filled from KeePassXC", 4000
                )
                self.accept()
            else:
                self.status.setText(
                    result.get("error", "Could not fill this login form.")
                    if isinstance(result, dict)
                    else "Could not fill this login form."
                )

        self._submit(
            "get-logins",
            {
                "url": login_url(QUrl(context["url"])),
                "submitUrl": login_url(QUrl(context["submitUrl"])),
            },
            fetched,
        )

    def save_login(self):
        if self._busy:
            return

        def inspected(context):
            editor = SaveLoginDialog(
                login_url(QUrl(context["url"])),
                context["login"],
                context["password"],
                self,
            )
            try:
                if editor.exec() != QDialog.DialogCode.Accepted or not self._alive():
                    return
                if bytes(self.page.url().toEncoded()).decode("ascii") != context["url"]:
                    self.status.setText("The page changed. Retry saving the login.")
                    return
                password = editor.password.text()
                if not password:
                    self.status.setText("Enter a password before saving.")
                    return
                self._submit(
                    "set-login",
                    {
                        "url": login_url(QUrl(context["url"])),
                        "submitUrl": login_url(QUrl(context["submitUrl"])),
                        "login": editor.login.text(),
                        "password": password,
                    },
                    lambda _: self.status.setText(
                        "Login saved to KeePassXC. Refresh to fill it."
                    ),
                )
            finally:
                context["password"] = ""
                editor.password.clear()
                editor.deleteLater()

        self._inspect(inspected, read_values=True)

    def done(self, result):
        self._closed = True
        self.manager.cancel(self)
        self.entries.clear()
        self.context = None
        super().done(result)
