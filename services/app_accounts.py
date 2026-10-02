"""Provider-neutral account identities; OAuth material lives in Secret Service."""

import hashlib
import json
import re
import threading
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.connector_policy import connector_name
from utils import save_json


class AccountError(RuntimeError):
    """Safe user-facing setup error without provider responses or credentials."""


class ClientSetupRequired(AccountError):
    """An intentionally forgotten app registration must be imported again."""


@dataclass(frozen=True)
class AppAccount:
    account_id: str
    provider: str
    subject: str
    label: str
    capabilities: tuple[str, ...]
    created_at: str
    enabled: bool = True

    @property
    def connector(self):
        return f"{self.provider}_{self.account_id}"

    def validate(self):
        if not re.fullmatch(r"[a-f0-9]{32}", self.account_id):
            raise ValueError("Invalid account identity")
        connector_name(self.provider)
        connector_name(self.connector)
        if (
            not isinstance(self.subject, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", self.subject)
            or not isinstance(self.label, str)
            or not 1 <= len(self.label) <= 254
            or not all(c.isprintable() for c in self.label)
            or type(self.enabled) is not bool
            or not isinstance(self.created_at, str)
            or len(self.created_at) > 64
            or not isinstance(self.capabilities, tuple)
            or not 1 <= len(self.capabilities) <= 20
            or len(set(self.capabilities)) != len(self.capabilities)
        ):
            raise ValueError("Invalid account metadata")
        for capability in self.capabilities:
            connector_name(capability)
        return self


class SecretServiceStore:
    """Use only the Linux desktop keyring, with no file or plaintext fallback."""

    def _collection(self, connection, *, unlock=False):
        import secretstorage

        collection = secretstorage.get_default_collection(connection)
        if unlock and collection.is_locked():
            collection.unlock(timeout=60)
        collection.ensure_not_locked()
        return collection

    def check(self, *, unlock=False):
        try:
            import secretstorage

            with closing(secretstorage.dbus_init()) as connection:
                self._collection(connection, unlock=unlock)
        except Exception:
            raise AccountError(
                "An unlocked Linux Secret Service keyring is required. Install "
                "requirements-apps.txt and unlock your desktop keyring."
            ) from None

    def _operation(self, account_id, operation, value=None):
        if not re.fullmatch(r"[a-f0-9]{32}", account_id):
            raise ValueError("Invalid account identity")
        try:
            import secretstorage

            with closing(secretstorage.dbus_init()) as connection:
                collection = self._collection(connection)
                attributes = {"application": "steed-apps-v1", "account": account_id}
                if operation == "save":
                    collection.create_item(
                        "Steed app connection",
                        attributes,
                        json.dumps(value).encode("utf-8"),
                        replace=True,
                        content_type="application/json",
                    )
                    return
                items = list(collection.search_items(attributes))
                if operation == "delete":
                    for item in items:
                        item.delete()
                    return
                if len(items) != 1:
                    raise ValueError("Missing or ambiguous credential")
                secret = items[0].get_secret()
                if len(secret) > 65536:
                    raise ValueError("Credential is too large")
                value = json.loads(secret)
                if not isinstance(value, dict):
                    raise ValueError("Invalid credential")
                return value
        except Exception:
            raise AccountError(
                "App credentials are unavailable. Unlock the desktop keyring "
                "or reconnect the account."
            ) from None

    def load(self, account_id):
        return self._operation(account_id, "load")

    def save(self, account_id, value):
        self._operation(account_id, "save", value)

    def delete(self, account_id):
        self._operation(account_id, "delete")


class AccountStore:
    """Immutable connections prevent existing grants being reassigned to accounts."""

    def __init__(self, path: Path, secrets=None):
        self.path = path
        self.secrets = secrets if secrets is not None else SecretServiceStore()
        self._lock = threading.RLock()
        self._credential_locks = {}
        self._accounts = {}
        try:
            encoded = path.read_bytes()
        except FileNotFoundError:
            return
        try:
            if len(encoded) > 65536:
                raise ValueError("Account metadata is too large")
            data = json.loads(encoded)
            if set(data) != {"version", "accounts"} or data["version"] != 1:
                raise ValueError("Unsupported account metadata")
            if not isinstance(data["accounts"], list) or len(data["accounts"]) > 100:
                raise ValueError("Invalid account list")
            for item in data["accounts"]:
                item = dict(item)
                item["capabilities"] = tuple(item["capabilities"])
                account = AppAccount(**item).validate()
                if account.account_id in self._accounts:
                    raise ValueError("Duplicate account identity")
                self._accounts[account.account_id] = account
        except (TypeError, KeyError, ValueError):
            raise AccountError(
                "App connection metadata is invalid; connections are disabled."
            ) from None

    def _save(self, accounts):
        payload = {"version": 1, "accounts": [asdict(a) for a in accounts.values()]}
        if len(json.dumps(payload, indent=2).encode("utf-8")) > 65536:
            raise AccountError("App account metadata is full. No change was saved.")
        save_json(self.path, payload)
        self._accounts = accounts

    def list(self):
        with self._lock:
            return tuple(self._accounts.values())

    def active(self, account):
        with self._lock:
            if not account.enabled or self._accounts.get(account.account_id) != account:
                raise AccountError("This app connection is disabled. Reconnect it.")

    def client_configuration(self, provider, value=None):
        """Keep reusable app registration separate from individual account tokens."""
        connector_name(provider)
        key = self._client_key(provider)
        if value is not None:
            self.secrets.save(
                key, {"kind": "desktop-client", "provider": provider, "client": value}
            )
            return value
        stored = self.secrets.load(key)
        if stored.get("forgotten") is True:
            raise ClientSetupRequired(
                f"Set up {provider.title()} access once: choose an app client file to configure access again."
            )
        if (
            stored.get("kind") != "desktop-client"
            or stored.get("provider") != provider
            or not isinstance(stored.get("client"), dict)
        ):
            raise AccountError(
                "Saved app configuration is unavailable. Choose a client file once."
            )
        return stored["client"]

    def _client_key(self, provider):
        connector_name(provider)
        return hashlib.sha256(
            ("steed-desktop-client:" + provider).encode()
        ).hexdigest()[:32]

    def forget_client_configuration(self, provider):
        self.secrets.save(
            self._client_key(provider),
            {"kind": "desktop-client", "provider": provider, "forgotten": True},
        )

    def install(self, provider, subject, label, capabilities, credential, cancelled):
        """Write secrets first; only committed metadata enables an account."""
        account = AppAccount(
            uuid4().hex,
            provider,
            subject,
            label,
            tuple(sorted(capabilities)),
            datetime.now(timezone.utc).isoformat(),
        ).validate()
        if cancelled.is_set():
            raise AccountError("Connection cancelled.")
        self.secrets.save(account.account_id, credential)
        try:
            with self._lock:
                if len(self._accounts) >= 100:
                    raise AccountError(
                        "At most 100 retained app connections are supported."
                    )
                if cancelled.is_set():
                    raise AccountError("Connection cancelled.")
                self._save({**self._accounts, account.account_id: account})
        except Exception:
            self.secrets.delete(account.account_id)
            raise
        return account

    def _credential_lock(self, account_id):
        with self._lock:
            return self._credential_locks.setdefault(account_id, threading.Lock())

    def save_credential(self, account, credential):
        """Refresh cannot resurrect a credential after a concurrent disconnect."""
        with self._credential_lock(account.account_id):
            self.active(account)
            self.secrets.save(account.account_id, credential)
            try:
                self.active(account)
            except AccountError:
                self.secrets.delete(account.account_id)
                raise

    def disable(self, account_id):
        """Persist denial before deleting credentials; deletion can be retried."""
        with self._lock:
            account = self._accounts[account_id]
            self._save({**self._accounts, account_id: replace(account, enabled=False)})
        return account.connector

    def forget_secret(self, account_id):
        with self._lock:
            if self._accounts[account_id].enabled:
                raise ValueError("Disable the account before removing its credentials")
        with self._credential_lock(account_id):
            self.secrets.delete(account_id)
