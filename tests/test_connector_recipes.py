"""Strict recipe grammar, immutable installation and bounded account interpretation."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app_connector_support import MemorySecrets
from recipe_support import TOKEN, SyntheticRecipeHttp, recipe, recipe_data

from services.app_accounts import AccountError, AccountStore
from services.connector_recipes import (
    RecipeError,
    RecipeStore,
    parse_recipe,
    resource_id,
    select,
)
from services.connectors import ConnectorRequest
from services.recipe_connector import RecipeConnector, connect_recipe


class RecipeValidationTests(unittest.TestCase):
    def test_public_https_origins_have_no_aliases_credentials_or_local_hosts(self):
        for origin in (
            "http://api.example.com",
            "https://127.0.0.1",
            "https://api.example.com:443",
            "https://API.example.com",
            "https://api.example.com/",
            "https://user@api.example.com",
            "https://api.local",
            "https://api.example.com?x=1",
            "https://api.example.com#x",
            "https://api.example.com.",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                recipe(origin=origin)

    def test_unknown_executable_effect_auth_and_schema_fields_are_rejected(self):
        cases = []
        for key, value in (
            ("code", "import os"),
            ("headers", {"Authorization": "secret"}),
            ("version", True),
            ("authentication", {"type": "bearer", "token": TOKEN}),
            ("authentication", {"type": "oauth"}),
        ):
            item = recipe_data()
            item[key] = value
            cases.append(item)
        for key, value in (
            ("method", "POST"),
            ("effect", "read"),
            ("python", "evil"),
            ("path", "/notes/../{destination}"),
            ("path", "//evil/{destination}"),
            ("path", "/notes/{destination}?token=secret"),
            ("path", "/notes/%2e%2e/{destination}"),
        ):
            item = recipe_data()
            item["tools"][0][key] = value
            cases.append(item)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                parse_recipe(json.dumps(item).encode())

    def test_duplicate_keys_nonfinite_values_and_oversized_source_are_rejected(self):
        for raw in (
            b'{"version":1,"version":1}',
            b'{"version":NaN}',
            b'"' + b"x" * 16384 + b'"',
            b"\xff",
            b"[" * 1200 + b"]" * 1200,
        ):
            with self.subTest(raw=raw[:32]), self.assertRaises(RecipeError):
                parse_recipe(raw)

    def test_response_fields_and_tool_counts_are_bounded_and_unambiguous(self):
        cases = []
        for tools in ([], recipe_data()["tools"] * 2, recipe_data()["tools"] * 11):
            item = recipe_data()
            item["tools"] = tools
            cases.append(item)
        for response in (
            [],
            [{"label": "title", "pointer": "bad"}],
            [{"label": "title", "pointer": "/a~2b"}],
            [{"label": "title", "pointer": "/title"}] * 2,
        ):
            item = recipe_data()
            item["tools"][0]["response"] = response
            cases.append(item)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(RecipeError):
                parse_recipe(json.dumps(item).encode())

    def test_resource_ids_cannot_escape_reviewed_paths(self):
        for value in (
            "https://evil.example",
            "../secret",
            "a/b",
            "a?b",
            "a#b",
            "a%2fb",
            "..",
            "foo..bar",
            "x\\y",
            "",
        ):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                resource_id(value)
        self.assertEqual(resource_id("note_42-a.b"), "note_42-a.b")

    def test_json_pointers_support_arrays_and_escaped_keys_without_evaluation(self):
        self.assertEqual(
            select({"a/b": [{"~field": "value"}]}, "/a~1b/0/~0field"), "value"
        )
        for value in ("/a~1b/-1", "/a~1b/01", "/missing"):
            with self.assertRaises((RecipeError, KeyError, IndexError)):
                select({"a/b": [1]}, value)


class RecipeStoreAndInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.catalog = RecipeStore(self.root / "recipes.json")
        self.recipe = recipe()
        self.catalog.install(self.recipe)
        self.secrets = MemorySecrets()
        self.accounts = AccountStore(self.root / "accounts.json", self.secrets)
        self.http = SyntheticRecipeHttp()

    def connect(self):
        return connect_recipe(
            self.recipe,
            self.catalog,
            self.accounts,
            TOKEN,
            ("read_note",),
            threading.Event(),
            http=self.http,
        )

    def test_installs_are_immutable_idempotent_and_restart_hash_checked(self):
        self.catalog.install(self.recipe)
        changed = recipe(name="Changed recipe")
        self.catalog.install(changed)
        restored = RecipeStore(self.catalog.path)
        self.assertEqual(len(restored.list()), 2)
        self.assertNotEqual(self.recipe.provider, changed.provider)
        self.assertEqual(self.catalog.path.stat().st_mode & 0o777, 0o600)
        data = json.loads(self.catalog.path.read_text())
        data["recipes"][0]["recipe"] += " "
        self.catalog.path.write_text(json.dumps(data))
        with self.assertRaises(RecipeError):
            RecipeStore(self.catalog.path)

    def test_failed_catalog_save_keeps_previous_authority(self):
        changed = recipe(name="Changed")
        with patch(
            "services.connector_recipes.save_json", side_effect=OSError("disk failure")
        ):
            with self.assertRaises(OSError):
                self.catalog.install(changed)
        with self.assertRaises(RecipeError):
            self.catalog.active(changed)
        self.catalog.active(self.recipe)

    def test_identity_verified_token_only_in_keyring_and_tools_select_output_fields(
        self,
    ):
        account = self.connect()
        self.assertNotIn(TOKEN, self.accounts.path.read_text())
        self.assertEqual(account.subject, self.http.subject)
        tool = RecipeConnector(
            self.recipe, self.catalog, account, self.accounts, http=self.http
        ).tools()[0]
        outcome = tool.execute(
            ConnectorRequest(
                "read", account.connector, "read_note", "read", "note-1", None
            ),
            threading.Event(),
        )
        self.assertEqual(
            json.loads(outcome.text),
            {"title": "Fixture note", "body": "Shared task data"},
        )
        self.assertNotIn("UNSHARED_FIELD", outcome.text)
        self.assertEqual(self.http.calls[-1][1], self.recipe.origin + "/notes/note-1")

    def test_changed_account_identity_or_recipe_removal_denies_requests(self):
        account = self.connect()
        tool = RecipeConnector(
            self.recipe, self.catalog, account, self.accounts, http=self.http
        ).tools()[0]
        request = ConnectorRequest(
            "read", account.connector, "read_note", "read", "note-1", None
        )
        self.http.subject = "different-user"
        with self.assertRaises(AccountError):
            tool.execute(request, threading.Event())
        self.catalog.disable(self.recipe.provider)
        count = len(self.http.calls)
        with self.assertRaises(RecipeError):
            tool.execute(request, threading.Event())
        self.assertEqual(len(self.http.calls), count)

    def test_changed_credentials_forged_requests_and_cancelled_calls_are_denied(self):
        account = self.connect()
        tool = RecipeConnector(
            self.recipe, self.catalog, account, self.accounts, http=self.http
        ).tools()[0]
        good = ConnectorRequest(
            "read", account.connector, "read_note", "read", "note-1", None
        )
        for request in (
            ConnectorRequest("read", "foreign", "read_note", "read", "note-1", None),
            ConnectorRequest(
                "read", account.connector, "read_note", "write", "note-1", None
            ),
            ConnectorRequest(
                "read", account.connector, "read_note", "read", "../evil", None
            ),
        ):
            with self.assertRaises(ValueError):
                tool.execute(request, threading.Event())
        stopped = threading.Event()
        stopped.set()
        with self.assertRaises(AccountError):
            tool.execute(good, stopped)
        self.secrets.values[account.account_id]["recipe_sha256"] = "changed"
        with self.assertRaises(AccountError):
            tool.execute(good, threading.Event())

    def test_response_token_reflection_is_redacted_and_oversized_result_fails(self):
        account = self.connect()
        tool = RecipeConnector(
            self.recipe, self.catalog, account, self.accounts, http=self.http
        ).tools()[0]
        request = ConnectorRequest(
            "read", account.connector, "read_note", "read", "note-1", None
        )
        self.http.body["body"] = "reflected " + TOKEN
        self.assertNotIn(TOKEN, tool.execute(request, threading.Event()).text)
        self.http.body["body"] = "x" * 20000
        with self.assertRaises(ValueError):
            tool.execute(request, threading.Event())

    def test_bad_identity_locked_keyring_and_cancelled_setup_install_no_account(self):
        self.http.subject = "invalid/subject"
        with self.assertRaises(AccountError):
            self.connect()
        self.assertEqual(self.accounts.list(), ())
        self.secrets.available = False
        with self.assertRaises(AccountError):
            self.connect()
        self.assertEqual(self.accounts.list(), ())
