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
from ui.task_approval import WrappedLabel


class AppConnectionsDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("App Connections")
        self.resize(720, 400)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Connect accounts for delegated tasks. Each task still asks for the exact "
            "tools and destinations it needs. Credentials stay in your desktop keyring."
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
        self.provider = QComboBox()
        for provider in manager.providers.values():
            self.provider.addItem(provider.title, provider.name)
        layout.addWidget(self.provider)
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
            check.deleteLater()
        self.capabilities = {}
        provider = self.manager.providers.get(self.provider.currentData())
        if provider is not None:
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
        self.connect_button.setEnabled(available)
        self.disconnect_button.setEnabled(available and self.accounts.currentRow() >= 0)
        self.unlock_button.setEnabled(available)
        self.cancel_button.setEnabled(self.manager.busy)
        for widget in (
            self.provider,
            self.client,
            self.browse,
            self.setup_button,
            self.forget_setup_button,
            *self.capabilities.values(),
        ):
            widget.setEnabled(available)

    def refresh(self):
        selected = None
        row = self.accounts.currentRow()
        if row >= 0:
            selected = self.accounts.item(row, 0).data(Qt.ItemDataRole.UserRole)
        accounts = self.manager.store.list() if self.manager.store is not None else ()
        self.accounts.setVisible(bool(accounts))
        self.provider.setVisible(len(self.manager.providers) > 1)
        self.accounts.setRowCount(len(accounts))
        for row, account in enumerate(accounts):
            for col, value in enumerate(
                (
                    account.provider,
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
        self.status.setText(self.manager.message)
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
