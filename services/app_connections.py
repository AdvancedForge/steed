"""Application-owned provider setup; workers never access Qt or task SQLite."""

import threading
from concurrent.futures import Future
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.app_accounts import AccountError, AccountStore
from services.command_broker import require_owner_thread
from services.connector_policy import connector_name
from services.connectors import ConnectorRegistry
from services.google_connector import GoogleConnector, connect_google


@dataclass(frozen=True)
class AppProvider:
    name: str
    title: str
    capabilities: tuple[tuple[str, str], ...]
    connect: object
    tools: object
    setup_hint: str = "Choose the provider's desktop client configuration file."
    client_placeholder: str = "Desktop client configuration"


class AppConnections(QObject):
    """Trusted provider factories are replaceable without changing task authority."""

    changed = pyqtSignal()

    def __init__(self, path, parent=None, *, store=None, providers=None):
        super().__init__(parent)
        require_owner_thread(self)
        definitions = (
            providers
            if providers is not None
            else (
                AppProvider(
                    "google",
                    "Google",
                    (
                        ("documents", "Create Google Docs"),
                        ("email", "Send Gmail (no mailbox reading)"),
                    ),
                    connect_google,
                    lambda account, store: GoogleConnector(account, store).tools(),
                    "Enable the selected APIs in your Google Cloud project first. "
                    "Authorization opens your system browser. A send with a lost "
                    "acknowledgement needs checking in Gmail; Steed does not request mailbox reading access.",
                    "Google Desktop OAuth client JSON (from Google Cloud)",
                ),
            )
        )
        self.providers = {}
        for provider in definitions:
            connector_name(provider.name)
            if (
                provider.name in self.providers
                or not callable(provider.connect)
                or not callable(provider.tools)
            ):
                raise ValueError("Invalid trusted app provider")
            self.providers[provider.name] = provider
        self.connectors = ConnectorRegistry()
        self.store = None
        self.task_service = None
        self.busy = False
        self.message = (
            "Connect an account to make its tools available to approved tasks."
        )
        self.failure = ""
        self._future = None
        self._cancelled = None
        self._closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        try:
            self.store = store if store is not None else AccountStore(path)
            for account in self.store.list():
                if account.enabled and account.provider in self.providers:
                    self._register(account)
        except (AccountError, OSError, ValueError):
            self.connectors = ConnectorRegistry()
            self.failure = "App connection metadata could not be loaded. Check app_accounts.json; no tools are enabled."
            self.message = self.failure

    def _register(self, account):
        definitions = self.providers[account.provider].tools(account, self.store)
        if any(t.connector != account.connector for t in definitions):
            raise ValueError(
                "Provider tools must use their immutable account identifier"
            )
        for definition in definitions:
            self.connectors.register(definition)

    def _worker(self, operation):
        require_owner_thread(self)
        if self._closed or self.failure or self.busy:
            raise AccountError(
                "App connections are unavailable or another setup is running."
            )
        self._cancelled = threading.Event()
        self._future = Future()
        future, cancelled = self._future, self._cancelled
        self.busy = True

        def run():
            try:
                future.set_result(operation(cancelled))
            except Exception as error:
                future.set_exception(error)

        threading.Thread(target=run, daemon=True).start()
        self.timer.start()
        self.changed.emit()

    def connect_account(self, provider_name, client_file, capabilities):
        provider = self.providers[provider_name]
        self.message = (
            "Finish account authorization in your system browser, then return here."
        )
        self._worker(
            lambda cancelled: provider.connect(
                self.store, client_file, tuple(capabilities), cancelled
            )
        )

    def disconnect(self, account_id):
        require_owner_thread(self)
        if self._closed or self.failure or self.busy:
            raise AccountError("Wait for the current account setup to finish.")
        # Disk metadata denies adapter entry even if grant cleanup subsequently fails.
        connector = self.store.disable(account_id)
        try:
            if self.task_service is not None:
                self.task_service.disconnect_connector(connector)
        finally:
            self.connectors.unregister(connector)
        self.message = "Connection disabled. Removing its local credentials…"
        self._worker(lambda _: self.store.forget_secret(account_id))

    def unlock(self):
        self.message = "Unlock your desktop keyring when prompted."
        self._worker(lambda _: self.store.secrets.check(unlock=True))

    def cancel(self):
        require_owner_thread(self)
        if self.busy:
            self._cancelled.set()
            self.message = "Cancelling account setup…"
            self.changed.emit()

    def _poll(self):
        if not self._future.done():
            return
        self.timer.stop()
        self.busy = False
        try:
            account = self._future.result()
            if account is not None:
                self._register(account)
                self.message = (
                    f"Connected {account.label}. Tasks still need your approval."
                )
            else:
                self.message = "Account operation completed."
        except AccountError as error:
            self.message = str(error)
        except Exception:
            self.message = "Account operation failed. Check your OAuth setup, network and desktop keyring. Disabled connections stay disabled."
        self._future = None
        self.changed.emit()

    def close(self):
        require_owner_thread(self)
        self.cancel()
        self._closed = True
        self.timer.stop()
