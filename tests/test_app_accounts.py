"""Account identity, provider isolation and fail-closed OS credential storage."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app_connector_support import MemorySecrets, install_account

from services.app_accounts import AccountError, AccountStore, SecretServiceStore


class AppAccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "accounts.json"
        self.secrets = MemorySecrets()
        self.store = AccountStore(self.path, self.secrets)

    def test_metadata_contains_no_credentials_and_reconnect_has_new_identity(self):
        first, second = install_account(self.store), install_account(self.store)
        self.assertNotEqual(first.connector, second.connector)
        text = self.path.read_text()
        self.assertNotIn("SYNTHETIC_REFRESH", text)
        self.assertNotIn("SYNTHETIC_CLIENT_SECRET", text)
        self.assertNotIn("access_token", text)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            AccountStore(self.path, self.secrets).list(), self.store.list()
        )

    def test_disable_persists_before_credential_deletion_and_survives_restart(self):
        account = install_account(self.store)
        self.secrets.available = False
        self.store.disable(account.account_id)
        with self.assertRaises(AccountError):
            self.store.forget_secret(account.account_id)
        reopened = AccountStore(self.path, self.secrets)
        with self.assertRaises(AccountError):
            reopened.active(account)
        self.secrets.available = True
        reopened.forget_secret(account.account_id)
        self.assertNotIn(account.account_id, self.secrets.values)

    def test_secrets_cannot_be_removed_while_account_is_enabled(self):
        account = install_account(self.store)
        with self.assertRaises(ValueError):
            self.store.forget_secret(account.account_id)

    def test_provider_identities_cannot_be_swapped(self):
        google = install_account(self.store)
        other = install_account(self.store, provider="other")
        self.assertTrue(other.connector.startswith("other_"))
        self.assertNotEqual(google.connector, other.connector)
        self.store.disable(google.account_id)
        self.store.active(other)

    def test_metadata_write_failure_rolls_back_new_secret(self):
        with patch("services.app_accounts.save_json", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                install_account(self.store)
        self.assertEqual(self.store.list(), ())
        self.assertFalse(self.secrets.values)

    def test_cancel_during_secret_save_never_enables_account(self):
        cancelled = threading.Event()
        save = self.secrets.save

        def cancel_on_save(account_id, value):
            save(account_id, value)
            cancelled.set()

        with patch.object(self.secrets, "save", cancel_on_save):
            with self.assertRaises(AccountError):
                self.store.install(
                    "google",
                    "subject",
                    "fixture@example.test",
                    ("documents",),
                    {},
                    cancelled,
                )
        self.assertFalse(self.store.list())
        self.assertFalse(self.secrets.values)

    def test_disconnect_during_refresh_cannot_resurrect_secret(self):
        account = install_account(self.store)
        save = self.secrets.save

        def disable_on_save(account_id, value):
            save(account_id, value)
            self.store.disable(account_id)

        with patch.object(self.secrets, "save", disable_on_save):
            with self.assertRaises(AccountError):
                self.store.save_credential(account, {"refresh_token": "ROTATED"})
        self.assertNotIn(account.account_id, self.secrets.values)

    def test_malformed_or_unknown_metadata_fails_without_overwriting(self):
        for raw in (
            b"{",
            b"[]",
            b'{"version":2,"accounts":[]}',
            b'{"version":1,"accounts":[{}]}',
        ):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(AccountError):
                    AccountStore(self.path, self.secrets)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_metadata_never_accepts_credential_fields(self):
        install_account(self.store)
        data = json.loads(self.path.read_text())
        data["accounts"][0]["refresh_token"] = "FORBIDDEN"
        self.path.write_text(json.dumps(data))
        with self.assertRaises(AccountError):
            AccountStore(self.path, self.secrets)

    def test_secret_service_unavailable_has_no_fallback_or_secret_error_detail(self):
        with patch.dict("sys.modules", {"secretstorage": None}):
            backend = SecretServiceStore()
            with self.assertRaises(AccountError) as raised:
                backend.check(unlock=True)
            self.assertIn("Secret Service", str(raised.exception))
            with self.assertRaises(AccountError):
                backend.save("a" * 32, {"refresh_token": "NEVER_WRITE_TO_DISK"})
        self.assertFalse(self.path.exists())
