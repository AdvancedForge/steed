"""User-owned app setup; account connection never approves an agent task."""

import sqlite3
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.app_accounts import AccountError
from services.connector_access import GROUP_LABELS
from services.connector_recipes import MAX_RECIPE_BYTES, parse_recipe
from ui.recipe_review import RecipeReviewDialog
from ui.task_approval import WrappedLabel


class AppConnectionsDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("App Connections")
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Sign in once, then choose what agents may do with each account. Allowed "
            "app-only tasks do not repeat permission prompts. Browsing, file access and "
            "code execution have separate permissions. Credentials stay in your desktop keyring."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.accounts = QTableWidget(0, 4)
        self.accounts.setHorizontalHeaderLabels(
            ["Provider", "Account", "Tools", "State"]
        )
        self.accounts.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.accounts.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.accounts.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.accounts.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.accounts.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.accounts)
        self.access_panel = QWidget()
        self.access_layout = QVBoxLayout(self.access_panel)
        self.access_notice = QLabel(
            "Allow these actions without asking for each task. Applies to all connected "
            "agents, across profiles. Each app-only task keeps exact destinations, a "
            "10-minute limit and at most 1,000 operations. Turning access off stops matching "
            "tasks and clears matching saved approvals."
        )
        self.access_notice.setWordWrap(True)
        self.access_layout.addWidget(self.access_notice)
        self.access_choices = {}
        layout.addWidget(self.access_panel)
        self.provider = QComboBox()
        for provider in manager.providers.values():
            self.provider.addItem(provider.title, provider.name)
        layout.addWidget(self.provider)
        recipes = QHBoxLayout()
        self.import_recipe_button = QPushButton("Add Connector Recipe…")
        self.remove_recipe_button = QPushButton("Remove Selected Recipe")
        self.import_recipe_button.clicked.connect(self.import_recipe)
        self.remove_recipe_button.clicked.connect(self.remove_recipe)
        recipes.addWidget(self.import_recipe_button)
        recipes.addWidget(self.remove_recipe_button)
        layout.addLayout(recipes)
        self.capability_layout = QHBoxLayout()
        layout.addLayout(self.capability_layout)
        self.capabilities = {}
        self.provider.currentIndexChanged.connect(self._capabilities)
        self.setup_button = QPushButton("Set Up App Access…")
        self.setup_button.setCheckable(True)
        layout.addWidget(self.setup_button)
        self.setup_panel = QWidget()
        setup_layout = QVBoxLayout(self.setup_panel)
        setup_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setup_button.toggled.connect(self._toggle_setup)
        self.setup_panel.hide()
        layout.addWidget(self.setup_panel)
        setup_help = QLabel(
            "Import the provider's desktop app configuration once. Steed remembers it in your keyring for future account connections."
        )
        setup_help.setWordWrap(True)
        setup_layout.addWidget(setup_help)
        self.setup_link_button = QPushButton()
        self.setup_link_button.clicked.connect(self.open_setup)
        setup_layout.addWidget(self.setup_link_button)
        client_row = QHBoxLayout()
        self.client = QLineEdit()
        self.browse = QPushButton("Choose Client JSON…")
        self.browse.clicked.connect(self._browse)
        client_row.addWidget(self.client)
        client_row.addWidget(self.browse)
        setup_layout.addLayout(client_row)
        self.notice = WrappedLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        setup_layout.addWidget(self.notice)
        self.forget_setup_button = QPushButton("Forget Saved App Setup")
        self.forget_setup_button.clicked.connect(
            lambda: self._run(
                lambda: manager.forget_client_configuration(self.provider.currentData())
            )
        )
        setup_layout.addWidget(self.forget_setup_button)
        self._capabilities()
        row = QHBoxLayout()
        self.connect_button = QPushButton("Connect Account")
        self.disconnect_button = QPushButton("Disconnect Account")
        self.unlock_button = QPushButton("Unlock Keyring")
        self.cancel_button = QPushButton("Cancel Setup")
        for button in (
            self.connect_button,
            self.disconnect_button,
            self.unlock_button,
            self.cancel_button,
        ):
            row.addWidget(button)
        layout.addLayout(row)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.unlock_button.clicked.connect(lambda: self._run(manager.unlock))
        self.cancel_button.clicked.connect(manager.cancel)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        manager.changed.connect(self.refresh)
        self.accounts.itemSelectionChanged.connect(self._buttons)
        self.refresh()

    def _capabilities(self):
        for check in self.capabilities.values():
            self.capability_layout.removeWidget(check)
            check.hide()
            check.deleteLater()
        self.capabilities = {}
        provider = self.manager.providers.get(self.provider.currentData())
        if provider is not None:
            if getattr(self, "_client_provider", None) != provider.name:
                self.client.clear()
                self._client_provider = provider.name
            self.setup_button.setVisible(provider.authentication != "bearer")
            if provider.authentication == "bearer":
                self.setup_button.setChecked(False)
            if hasattr(self, "connect_button"):
                self.connect_button.setText("Connect " + provider.title)
            self.setup_link_button.setText("Open " + provider.title + " Setup")
            self.setup_link_button.setVisible(bool(provider.setup_url))
            self.client.setPlaceholderText(provider.client_placeholder)
            self.notice.setText(provider.setup_hint)
            for name, label in provider.capabilities:
                check = QCheckBox(label)
                check.setChecked(True)
                self.capabilities[name] = check
                self.capability_layout.addWidget(check)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "setup_panel") and not self.setup_panel.isHidden():
            self.setup_panel.setMinimumHeight(
                self.setup_panel.heightForWidth(max(100, event.size().width() - 22))
            )

    def _toggle_setup(self, enabled):
        self.setup_panel.setVisible(enabled)
        if enabled:
            self.setup_panel.setMinimumHeight(
                self.setup_panel.heightForWidth(max(100, self.width() - 22))
            )
            self.resize(
                self.width(),
                max(self.height(), self.layout().totalHeightForWidth(self.width())),
            )

    def open_setup(self):
        provider = self.manager.providers.get(self.provider.currentData())
        if provider is not None and QUrl(provider.setup_url).scheme() == "https":
            QDesktopServices.openUrl(QUrl(provider.setup_url))

    def _browse(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose Desktop OAuth Client", "", "JSON files (*.json)"
        )
        if filename:
            self.client.setText(filename)

    def _run(self, operation):
        try:
            operation()
        except (AccountError, OSError, ValueError, RuntimeError, sqlite3.Error):
            self.status.setText(
                "Account operation could not start. Check setup and the app connection journal."
            )

    def _connect(self):
        capabilities = tuple(n for n, c in self.capabilities.items() if c.isChecked())
        if not capabilities:
            self.status.setText("Choose at least one capability.")
            return
        provider = self.manager.providers[self.provider.currentData()]
        if provider.authentication == "bearer":
            token, accepted = QInputDialog.getText(
                self,
                "Connect " + provider.title,
                "Enter this service's API bearer token. It will be verified with the reviewed identity endpoint and stored only in your desktop keyring.",
                QLineEdit.EchoMode.Password,
            )
            if accepted:
                self._run(
                    lambda: self.manager.connect_recipe_account(
                        provider.name, token, capabilities
                    )
                )
            return
        self._run(
            lambda: self.manager.connect_account(
                self.provider.currentData(),
                Path(self.client.text().strip())
                if self.client.text().strip()
                else None,
                capabilities,
            )
        )

    def _disconnect(self):
        row = self.accounts.currentRow()
        if row >= 0:
            account_id = self.accounts.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self._run(lambda: self.manager.disconnect(account_id))

    def _buttons(self):
        available = not self.manager.busy and not self.manager.failure
        self.connect_button.setEnabled(
            available and self.provider.currentData() in self.manager.providers
        )
        self.disconnect_button.setEnabled(available and self.accounts.currentRow() >= 0)
        self.unlock_button.setEnabled(available)
        self.cancel_button.setEnabled(self.manager.busy)
        self.import_recipe_button.setEnabled(
            available and self.manager.recipes is not None
        )
        selected = self.manager.providers.get(self.provider.currentData())
        self.remove_recipe_button.setEnabled(
            bool(available and selected and selected.recipe_sha256)
        )
        for widget in (
            self.provider,
            self.client,
            self.browse,
            self.setup_button,
            self.forget_setup_button,
            *self.capabilities.values(),
        ):
            widget.setEnabled(available)
        self._access_choices(available)

    def _access_choices(self, available):
        for check in self.access_choices.values():
            self.access_layout.removeWidget(check)
            check.deleteLater()
        self.access_choices = {}
        row = self.accounts.currentRow()
        account = None
        if row >= 0 and self.manager.store is not None:
            account_id = self.accounts.item(row, 0).data(Qt.ItemDataRole.UserRole)
            account = next(
                (a for a in self.manager.store.list() if a.account_id == account_id),
                None,
            )
        self.access_panel.setVisible(bool(account and account.enabled))
        self.access_panel.setEnabled(available)
        if not account or not account.enabled:
            return
        try:
            service = self.manager.access_service()
            if service is None:
                return
            groups = service.account_access.groups(account.connector)
            for group in GROUP_LABELS:
                if group not in groups:
                    continue
                check = QCheckBox(GROUP_LABELS[group])
                state = service.account_access.state(account.connector, group)
                check.setTristate(state == "some")
                check.setCheckState(
                    Qt.CheckState.Checked
                    if state == "all"
                    else Qt.CheckState.PartiallyChecked
                    if state == "some"
                    else Qt.CheckState.Unchecked
                )
                check.setEnabled(available and not service.failure)
                check.setToolTip(
                    "Supported tools: "
                    + ", ".join(sorted({t for t, _ in groups[group]}))
                )
                check.clicked.connect(
                    lambda enabled, selected=account.account_id, mode=group: self._run(
                        lambda: self.manager.set_access(selected, mode, enabled)
                    )
                )
                self.access_choices[group] = check
                self.access_layout.addWidget(check)
        except (OSError, sqlite3.Error, ValueError, RuntimeError):
            self.status.setText("Account access settings are unavailable.")
            self.access_panel.setEnabled(False)

    def refresh(self):
        selected_provider = self.provider.currentData()
        current = [self.provider.itemData(i) for i in range(self.provider.count())]
        if current != list(self.manager.providers):
            self.provider.blockSignals(True)
            self.provider.clear()
            for provider in self.manager.providers.values():
                self.provider.addItem(provider.title, provider.name)
            index = self.provider.findData(selected_provider)
            self.provider.setCurrentIndex(
                index if index >= 0 else max(0, self.provider.count() - 1)
            )
            self.provider.blockSignals(False)
            self._capabilities()
        selected = None
        row = self.accounts.currentRow()
        if row >= 0:
            selected = self.accounts.item(row, 0).data(Qt.ItemDataRole.UserRole)
        accounts = self.manager.store.list() if self.manager.store is not None else ()
        self.accounts.setVisible(bool(accounts))
        self.provider.setVisible(len(self.manager.providers) > 1)
        self.accounts.blockSignals(True)
        self.accounts.setRowCount(len(accounts))
        for row, account in enumerate(accounts):
            for col, value in enumerate(
                (
                    self.manager.providers[account.provider].title
                    if account.provider in self.manager.providers
                    else account.provider,
                    account.label,
                    ", ".join(account.capabilities),
                    "Connected" if account.enabled else "Disabled",
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(account.connector if col == 0 else value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, account.account_id)
                self.accounts.setItem(row, col, item)
            if selected == account.account_id:
                self.accounts.selectRow(row)
        if selected is None and accounts:
            self.accounts.selectRow(len(accounts) - 1)
        self.accounts.blockSignals(False)
        self.status.setText(
            self.manager.message
            + (
                "\n" + self.manager.recipe_failure
                if self.manager.recipe_failure
                else ""
            )
        )
        if self.manager.message.startswith("Connected ") and not self.manager.busy:
            self.client.clear()
            self.setup_button.setChecked(False)
        if self.manager.message.startswith("Set up Google access once"):
            self.setup_button.setChecked(True)
        self.connect_button.setText("Connect " + self.provider.currentText())
        self._buttons()

    def done(self, result):
        self.manager.cancel()
        super().done(result)

    def import_recipe(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Review Connector Recipe", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            with Path(path).open("rb") as stream:
                recipe = parse_recipe(stream.read(MAX_RECIPE_BYTES + 1))
            dialog = RecipeReviewDialog(self.manager, recipe, self)
            dialog.show()
        except (OSError, ValueError):
            self.status.setText(
                "Choose a valid connector recipe JSON of at most 16 KiB."
            )

    def remove_recipe(self):
        from PyQt6.QtWidgets import QMessageBox

        provider = self.manager.providers.get(self.provider.currentData())
        if (
            provider
            and provider.recipe_sha256
            and QMessageBox.question(
                self,
                "Remove Connection Recipe",
                f"Remove {provider.title} and disconnect all of its accounts? Existing task receipts will remain.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self._run(lambda: self.manager.remove_recipe(provider.name))
