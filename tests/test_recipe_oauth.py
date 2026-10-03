"""Reviewed native OAuth setup, refresh rotation, account binding and collections."""

import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from app_connector_support import MemorySecrets
from recipe_support import TOKEN, SyntheticRecipeHttp, recipe_data

from services.app_accounts import AccountError, AccountStore, ClientSetupRequired
from services.app_oauth import desktop_authorization
from services.connector_recipes import RecipeStore, parse_recipe
from services.connectors import ConnectorRequest
from services.recipe_connector import RecipeConnector, connect_recipe
from services.recipe_oauth import connect_recipe_oauth, token_response

AUTH = {
    "type": "oauth_pkce",
    "authorization_endpoint": "https://auth.fixture.example/authorize",
    "token_endpoint": "https://auth.fixture.example/token",
    "scopes": ["profile", "notes.read"],
}
REFRESH = "SYNTHETIC_REFRESH_TOKEN"
ROTATED = "SYNTHETIC_ROTATED_REFRESH"
SECRET = "SYNTHETIC_CLIENT_SECRET"


def oauth_recipe():
    data = recipe_data()
    data["authentication"] = dict(AUTH)
    data["tools"].append(
        {
            "name": "list_notes",
            "description": "Read this account's notes",
            "method": "GET",
            "path": "/notes",
            "response": [{"label": "notes", "pointer": "/items"}],
        }
    )
    return parse_recipe(json.dumps(data).encode())


class OAuthHttp(SyntheticRecipeHttp):
    def __init__(self):
        super().__init__()
        self.posts, self.access, self.rotated = [], TOKEN, TOKEN + "_ROTATED"
        self.token_change, self.no_refresh, self.credential_echo = {}, False, False
        self.on_post = None

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        if method == "POST":
            assert url == AUTH["token_endpoint"]
            assert headers == {"Content-Type": "application/x-www-form-urlencoded"}
            params = parse_qs(body.decode())
            self.posts.append(params)
            if self.on_post:
                self.on_post()
            refresh = params["grant_type"] == ["refresh_token"]
            self.access = self.rotated if refresh else TOKEN
            result = {
                "token_type": "Bearer",
                "access_token": self.access,
                "expires_in": 3600,
                "scope": " ".join(AUTH["scopes"]),
            }
            if not self.no_refresh:
                result["refresh_token"] = ROTATED if refresh else REFRESH
            return {**result, **self.token_change}
        assert headers == {"Authorization": "Bearer " + self.access}
        self.calls.append((method, url, headers, body))
        if url.endswith("/me"):
            return {"id": self.subject, "email": self.label}
        if url.endswith("/notes"):
            return {"items": [{"id": "note-1", "title": "Synthetic note"}]}
        return (
            {**self.body, "body": SECRET + " " + ROTATED}
            if self.credential_echo
            else self.body
        )


class RecipeOAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.recipe = oauth_recipe()
        self.catalog = RecipeStore(self.root / "recipes.json")
        self.catalog.install(self.recipe)
        self.secrets = MemorySecrets()
        self.store = AccountStore(self.root / "accounts.json", self.secrets)
        self.client_path = self.root / "client.json"
        self.client_path.write_text(
            json.dumps({"client_id": "SYNTHETIC_CLIENT", "client_secret": SECRET})
        )
        self.http, self.cancelled = OAuthHttp(), threading.Event()

    def authorize(self, endpoint, client_id, scopes, cancelled):
        self.assertEqual(endpoint, AUTH["authorization_endpoint"])
        self.assertEqual(client_id, "SYNTHETIC_CLIENT")
        self.assertEqual(scopes, tuple(AUTH["scopes"]))
        return "SYNTHETIC_CODE", "SYNTHETIC_VERIFIER", "http://127.0.0.1:12345/callback"

    def connect(self, path="default"):
        return connect_recipe_oauth(
            self.recipe,
            self.catalog,
            self.store,
            self.client_path if path == "default" else path,
            ["read_note", "list_notes"],
            self.cancelled,
            http=self.http,
            authorize=self.authorize,
        )

    def connector(self, account):
        return RecipeConnector(
            self.recipe, self.catalog, account, self.store, http=self.http
        )

    def read(self, connector, name="read_note", destination="note-1"):
        definition = next(t for t in connector.tools() if t.tool == name)
        return definition.execute(
            ConnectorRequest(
                "request",
                connector.account.connector,
                name,
                "read",
                destination,
                None,
                operation_id="operation",
            ),
            self.cancelled,
        )

    def test_setup_and_repeat_connections_reuse_only_keyring_registration(self):
        account = self.connect()
        self.client_path.unlink()
        second = self.connect(None)
        self.assertNotEqual(account.account_id, second.account_id)
        self.assertEqual(self.http.posts[0]["grant_type"], ["authorization_code"])
        self.assertEqual(self.http.posts[0]["code_verifier"], ["SYNTHETIC_VERIFIER"])
        for path in (self.store.path, self.catalog.path):
            raw = path.read_text()
            for value in (
                TOKEN,
                REFRESH,
                SECRET,
                "SYNTHETIC_CODE",
                "SYNTHETIC_VERIFIER",
            ):
                self.assertNotIn(value, raw)
        self.store.forget_client_configuration(self.recipe.provider)
        with self.assertRaises(ClientSetupRequired):
            self.connect(None)
        self.assertEqual(len(self.http.posts), 2)

    def test_first_use_refreshes_verifies_rotates_and_subsequent_reads_reuse_access(
        self,
    ):
        account = self.connect()
        connector = self.connector(account)
        self.assertIn("Shared task data", self.read(connector).text)
        self.assertEqual(self.http.posts[1]["refresh_token"], [REFRESH])
        self.assertEqual(
            self.secrets.load(account.account_id)["refresh_token"], ROTATED
        )
        self.read(connector)
        self.assertEqual(len(self.http.posts), 2)
        self.assertEqual(sum(call[1].endswith("/me") for call in self.http.calls), 4)

    def test_expired_cached_access_rotates_again_with_the_new_refresh_token(self):
        account = self.connect()
        connector = self.connector(account)
        self.read(connector)
        connector.oauth.cached["expires_at"] = time.time() - 1
        self.read(connector)
        self.assertEqual(self.http.posts[-1]["refresh_token"], [ROTATED])

    def test_changed_identity_cannot_publish_or_save_a_refreshed_token(self):
        account = self.connect()
        self.http.subject = "different-account"
        with self.assertRaises(AccountError):
            self.read(self.connector(account))
        self.assertEqual(
            self.secrets.load(account.account_id)["refresh_token"], REFRESH
        )
        self.assertTrue(all(call[1].endswith("/me") for call in self.http.calls))

    def test_refresh_save_failure_returns_no_data(self):
        account = self.connect()
        with patch.object(
            self.store,
            "save_credential",
            side_effect=AccountError("Keyring unavailable"),
        ):
            with self.assertRaises(AccountError):
                self.read(self.connector(account))
        self.assertTrue(all(call[1].endswith("/me") for call in self.http.calls))

    def test_disabled_account_removed_recipe_and_cancellation_block_late_refresh(self):
        for mutate in (
            lambda account: self.store.disable(account.account_id),
            lambda account: self.catalog.disable(account.provider),
            lambda account: self.cancelled.set(),
        ):
            with self.subTest(mutate=mutate):
                self.catalog.install(self.recipe)
                self.cancelled.clear()
                self.http.on_post = None
                account = self.connect()
                self.http.on_post = lambda mutate=mutate, account=account: mutate(
                    account
                )
                with self.assertRaises((AccountError, ValueError)):
                    self.read(self.connector(account))
                self.assertEqual(
                    self.secrets.load(account.account_id)["refresh_token"], REFRESH
                )

    def test_no_refresh_credential_works_until_expiry_then_requires_reconnect(self):
        self.http.no_refresh = True
        account = self.connect()
        connector = self.connector(account)
        self.read(connector)
        self.assertEqual(len(self.http.posts), 1)
        connector.oauth.cached["expires_at"] = time.time() - 1
        with self.assertRaises(AccountError):
            self.read(connector)
        self.assertEqual(len(self.http.posts), 1)

    def test_collection_reads_use_only_the_reviewed_path_and_exact_account_destination(
        self,
    ):
        account = self.connect()
        connector = self.connector(account)
        result = self.read(connector, "list_notes", "account")
        self.assertEqual(
            json.loads(result.text),
            {"notes": [{"id": "note-1", "title": "Synthetic note"}]},
        )
        self.assertEqual(self.http.calls[-1][1], "https://api.fixture.example/notes")
        for destination in ("note-1", "*", "account/other", "https://evil.example"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.read(connector, "list_notes", destination)

    def test_credential_echoes_are_redacted_from_retained_results(self):
        account = self.connect()
        self.http.credential_echo = True
        result = self.read(self.connector(account))
        for value in (SECRET, ROTATED, self.http.access):
            self.assertNotIn(value, result.text)
        self.assertIn("[credential redacted]", result.text)

    def test_client_files_cannot_change_endpoints_or_escape_registration_validation(
        self,
    ):
        for value in (
            {"client_id": "x", "token_endpoint": "https://evil.example/token"},
            {"client_id": "space here"},
            {"client_id": True},
            {"client_id": "ok", "client_secret": ""},
        ):
            self.client_path.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(AccountError):
                self.connect()
        self.assertFalse(self.http.posts)
        self.assertFalse(self.store.list())
        with self.assertRaises(AccountError):
            connect_recipe(
                self.recipe,
                self.catalog,
                self.store,
                TOKEN,
                ["read_note"],
                self.cancelled,
                http=self.http,
            )

    def test_json_escaping_and_object_keys_cannot_hide_reflected_credentials(self):
        secret, refresh = 'SYNTHETIC_"SECRET\\', 'SYNTHETIC_"REFRESH\\'
        self.client_path.write_text(
            json.dumps({"client_id": "SYNTHETIC_CLIENT", "client_secret": secret})
        )
        self.http.token_change = {"refresh_token": refresh}
        self.http.body = {
            "title": secret,
            "body": {refresh: [refresh, self.http.rotated]},
        }
        account = self.connect()
        result = json.loads(self.read(self.connector(account)).text)
        self.assertEqual(
            result,
            {
                "title": "[credential redacted]",
                "body": {
                    "[credential redacted]": [
                        "[credential redacted]",
                        "[credential redacted]",
                    ]
                },
            },
        )

    def test_token_responses_have_reviewed_scopes_types_lifetimes_and_tokens(self):
        base = {
            "access_token": TOKEN,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "profile notes.read",
        }
        for changes in (
            {"scope": "profile"},
            {"scope": "profile notes.read other.write"},
            {"expires_in": True},
            {"expires_in": 0},
            {"expires_in": 86401},
            {"token_type": 1},
            {"access_token": "bad token"},
            {"error": "denied"},
        ):
            with self.subTest(changes=changes), self.assertRaises(AccountError):
                token_response({**base, **changes}, AUTH["scopes"])
        token_response({k: v for k, v in base.items() if k != "scope"}, AUTH["scopes"])

    def test_authentication_schema_rejects_unreviewed_origins_scopes_and_credentials(
        self,
    ):
        for changes in (
            {"authorization_endpoint": "http://auth.fixture.example/authorize"},
            {"token_endpoint": "https://127.0.0.1/token"},
            {"token_endpoint": "https://auth.fixture.example/token?x=1"},
            {"token_endpoint": "https://auth.fixture.example:443/token"},
            {"scopes": []},
            {"scopes": ["a", "a"]},
            {"scopes": ["bad scope"]},
            {"scopes": ["x"] * 21},
            {"client_secret": SECRET},
            {"redirect_uri": "https://evil.example"},
        ):
            data = recipe_data()
            data["authentication"] = {**AUTH, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_recipe(json.dumps(data).encode())

    def test_generic_native_authorization_uses_pkce_state_and_no_google_parameters(
        self,
    ):
        observed = {}

        def browser(url):
            params = parse_qs(urlsplit(url).query)
            observed.update(params)
            redirect = params["redirect_uri"][0]
            with self.assertRaises(HTTPError):
                urlopen(
                    redirect
                    + "?"
                    + urlencode({"code": "WRONG_CODE", "state": "wrong"}),
                    timeout=3,
                )
            urlopen(
                redirect
                + "?"
                + urlencode({"code": "SYNTHETIC_CODE", "state": params["state"][0]}),
                timeout=3,
            ).close()

        threads = []

        def open_browser(url):
            thread = threading.Thread(target=browser, args=(url,))
            threads.append(thread)
            thread.start()
            return True

        code, verifier, _ = desktop_authorization(
            AUTH["authorization_endpoint"],
            "SYNTHETIC_CLIENT",
            tuple(AUTH["scopes"]),
            self.cancelled,
            open_browser=open_browser,
            timeout=5,
            google_options=False,
        )
        for thread in threads:
            thread.join()
        self.assertEqual(code, "SYNTHETIC_CODE")
        self.assertEqual(observed["code_challenge_method"], ["S256"])
        self.assertEqual(
            observed["code_challenge"],
            [
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            ],
        )
        self.assertNotIn("access_type", observed)
        self.assertNotIn("prompt", observed)


if __name__ == "__main__":
    unittest.main()
