"""Application-owned provider setup; workers never access Qt or task SQLite."""

import threading
from concurrent.futures import Future
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.app_accounts import AccountError, AccountStore
from services.bundled_recipes import BundledRecipes
from services.command_broker import require_owner_thread
from services.connector_policy import connector_name
from services.connector_recipes import RecipeError, RecipeStore
from services.connectors import ConnectorRegistry
from services.google_connector import GoogleConnector, connect_google
from services.recipe_connector import RecipeConnector, connect_recipe
from services.recipe_oauth import connect_recipe_oauth


@dataclass(frozen=True)
class AppProvider:
    name: str
    title: str
    capabilities: tuple[tuple[str, str], ...]
    connect: object
    tools: object
    setup_hint: str = "Choose the provider's desktop client configuration file."
    client_placeholder: str = "Desktop client configuration"
    setup_url: str = ""
    authentication: str = "oauth"
    recipe_sha256: str = ""


class AppConnections(QObject):
    """Trusted provider factories are replaceable without changing task authority."""

    changed = pyqtSignal()

    def __init__(
        self,
        path,
        parent=None,
        *,
        store=None,
        providers=None,
        recipe_http_factory=None,
        bundled_directory=None,
    ):
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
                        ("documents", "Google Docs and folders"),
                        ("email", "Send Gmail (no mailbox reading)"),
                    ),
                    connect_google,
                    lambda account, store: GoogleConnector(account, store).tools(),
                    "1. Open Google Setup and choose your Cloud project.\n"
                    "2. Enable Drive and Docs APIs for documents, and/or Gmail API for sends; configure consent and add your test account.\n"
                    "3. Create a Desktop app OAuth client, download its JSON, and choose it below.\n"
                    "Then click Connect Google. Consent opens your system browser.",
                    "Google Desktop OAuth client JSON (from Google Cloud)",
                    "https://console.cloud.google.com/auth/clients",
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
        self.recipe_http_factory = recipe_http_factory
        self.bundled_recipes = BundledRecipes(bundled_directory)
        self.recipes = None
        self.recipe_failure = ""
        try:
            self.recipes = RecipeStore(path.with_name("connector_recipes.json"))
            for recipe, enabled in self.recipes.list():
                if enabled:
                    self.providers[recipe.provider] = self._recipe_provider(recipe)
        except (RecipeError, OSError):
            self.recipe_failure = "Connector recipe catalog is unavailable; recipe providers are disabled."
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

    def _recipe_http(self, recipe):
        return self.recipe_http_factory(recipe) if self.recipe_http_factory else None

    def _recipe_provider(self, recipe):
        return AppProvider(
            recipe.provider,
            f"{recipe.name} ({recipe.origin.removeprefix('https://')})",
            tuple(
                (
                    t.name,
                    f"{t.effect.upper()}: {t.description}"
                    + (f" ({len(t.steps)} reviewed steps)" if t.steps else ""),
                )
                for t in recipe.tools
            ),
            lambda store, path, caps, cancelled: (
                connect_recipe_oauth(
                    recipe,
                    self.recipes,
                    store,
                    path,
                    caps,
                    cancelled,
                    http=self._recipe_http(recipe),
                )
                if recipe.authentication.kind == "oauth_pkce"
                else (_ for _ in ()).throw(
                    AccountError("Use trusted bearer-token connection UI")
                )
            ),
            lambda account, store: RecipeConnector(
                recipe, self.recipes, account, store, http=self._recipe_http(recipe)
            ).tools(),
            (
                "Connect this reviewed service with its own API token. It stays in your desktop keyring."
                if recipe.authentication.kind == "bearer"
                else "Import a native OAuth client JSON with client_id and optionally client_secret once. It must support PKCE S256 and an http://127.0.0.1:<port>/callback redirect. Sign-in opens your system browser; all reviewed scopes are requested. Client setup and tokens stay in your keyring."
            ),
            client_placeholder="Native OAuth client JSON (client_id, optional client_secret)",
            authentication=recipe.authentication.kind,
            recipe_sha256=recipe.sha256,
        )

    def install_recipe(self, recipe):
        require_owner_thread(self)
        if self._closed or self.failure or self.busy or self.recipes is None:
            raise AccountError("Wait for account setup before installing a recipe")
        existing = self.providers.get(recipe.provider)
        if existing is not None and existing.recipe_sha256 != recipe.sha256:
            raise ValueError("Recipe identity collides with an existing provider")
        if any(
            r.provider == recipe.provider and not enabled
            for r, enabled in self.recipes.list()
        ):
            # Removal persists catalog denial before account cleanup. A failed
            # cleanup must never resurrect an old account or its task grants.
            for account in self.store.list():
                if account.provider == recipe.provider:
                    self.store.disable(account.account_id)
                    if self.task_service is not None:
                        self.task_service.disconnect_connector(account.connector)
                    self.connectors.unregister(account.connector)
        self.recipes.install(recipe)
        self.providers[recipe.provider] = self._recipe_provider(recipe)
        self.message = f"Installed {recipe.name}. Connect its account, then choose allowed actions."
        self.changed.emit()

    def connect_recipe_account(self, provider_name, token, capabilities):
        require_owner_thread(self)
        recipe = next(
            r
            for r, enabled in self.recipes.list()
            if r.provider == provider_name and enabled
        )
        self.message = "Verifying this service's account identity…"
        self._worker(
            lambda cancelled: connect_recipe(
                recipe,
                self.recipes,
                self.store,
                token,
                tuple(capabilities),
                cancelled,
                http=self._recipe_http(recipe),
            )
        )

    def remove_recipe(self, provider):
        require_owner_thread(self)
        if (
            self._closed
            or self.failure
            or self.busy
            or self.recipes is None
            or provider not in self.providers
        ):
            raise AccountError("Recipe removal is unavailable during account setup")
        self.recipes.disable(provider)  # Deny interpreter entry before grant cleanup.
        forget_setup = self.providers[provider].authentication == "oauth_pkce"
        for account in self.store.list():
            if account.provider == provider and account.enabled:
                connector = self.store.disable(account.account_id)
                if self.task_service is not None:
                    self.task_service.disconnect_connector(connector)
                self.connectors.unregister(connector)
        self.providers.pop(provider, None)
        accounts = [a.account_id for a in self.store.list() if a.provider == provider]
        self.message = "Recipe disabled. Removing its local account credentials…"

        def forget(cancelled):
            for account_id in accounts:
                self.store.forget_secret(account_id)
            if forget_setup:
                self.store.forget_client_configuration(provider)

        self._worker(
            forget, success="Connector recipe removed; existing task receipts remain."
        )

    def access_service(self):
        require_owner_thread(self)
        if self.task_service is None and self.parent() is not None:
            self.task_service = self.parent().tasks
        return self.task_service

    def set_access(self, account_id, group, enabled):
        require_owner_thread(self)
        if self._closed or self.failure or self.busy:
            raise AccountError("Account access is unavailable.")
        account = next(a for a in self.store.list() if a.account_id == account_id)
        self.store.active(account)
        service = self.access_service()
        if service is None:
            raise AccountError("Task permission service is unavailable.")
        service.set_connector_access(account.connector, group, enabled)
        self.message = "Account access saved. Turning access off stops matching tasks."
        self.changed.emit()

    def _worker(self, operation, *, success="Account operation completed."):
        require_owner_thread(self)
        if self._closed or self.failure or self.busy:
            raise AccountError(
                "App connections are unavailable or another setup is running."
            )
        self._cancelled = threading.Event()
        self._future = Future()
        future, cancelled = self._future, self._cancelled
        self.busy = True
        self._success_message = success

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
        self._worker(
            lambda _: self.store.forget_secret(account_id),
            success="Account disconnected. Saved app setup can be reused for future connections.",
        )

    def unlock(self):
        self.message = "Unlock your desktop keyring when prompted."
        self._worker(
            lambda _: self.store.secrets.check(unlock=True),
            success="Desktop keyring is unlocked.",
        )

    def forget_client_configuration(self, provider):
        """User can remove reusable registration without disconnecting accounts."""
        connector_name(provider)
        self.message = "Removing saved app setup…"
        self._worker(
            lambda _: self.store.forget_client_configuration(provider),
            success="Saved app setup forgotten. Existing accounts remain connected.",
        )

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
                self.message = self._success_message
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
