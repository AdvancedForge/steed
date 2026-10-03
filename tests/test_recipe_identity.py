"""Reviewed nullable identity labels never weaken subject or credential binding."""

import copy
import json
import threading
import unittest
from unittest.mock import patch

import test_connector_recipes as recipes
import test_recipe_oauth as oauth
from recipe_support import TOKEN, recipe_data

from services.app_accounts import AccountError
from services.connector_recipes import (
    RECIPE_IDENTITY_EXAMPLE,
    RecipeError,
    parse_recipe,
)
from services.recipe_connector import RecipeConnector, identity


def identity_recipe():
    source = recipe_data()
    source.update(version=6)
    source["identity"].update(
        label="/name", label_alternatives=["/bot/workspace_name", "/id"]
    )
    return parse_recipe(json.dumps(source).encode())


class RecipeIdentityGrammarTests(unittest.TestCase):
    def test_explicit_alternatives_are_immutable_and_old_schemas_remain_strict(self):
        source = json.loads(identity_recipe().raw)
        recipe = parse_recipe(json.dumps(source).encode())
        metadata = recipe.metadata()
        self.assertEqual(
            metadata["identity"]["label_alternatives"], ["/bot/workspace_name", "/id"]
        )
        metadata["identity"]["label_alternatives"].clear()
        self.assertEqual(len(recipe.label_alternatives), 2)
        source["identity"]["label_alternatives"].reverse()
        changed = parse_recipe(json.dumps(source).encode())
        self.assertNotEqual(changed.provider, recipe.provider)
        for version in range(1, 6):
            source["version"] = version
            if version == 5:
                source["request_headers"] = {"Notion-Version": "2025-09-03"}
            with self.subTest(version=version), self.assertRaises(RecipeError):
                parse_recipe(json.dumps(source).encode())

    def test_invalid_duplicate_excess_or_implicit_fallbacks_are_rejected(self):
        source = json.loads(identity_recipe().raw)
        for values in (
            None,
            [],
            "/id",
            ["/id"] * 2,
            ["/name"],
            ["/a", "/b", "/c", "/d"],
            [True],
            ["/a~2"],
            ["x"],
            ["/" + "x" * 128],
        ):
            bad = copy.deepcopy(source)
            bad["identity"]["label_alternatives"] = values
            with self.subTest(values=values), self.assertRaises(RecipeError):
                parse_recipe(json.dumps(bad).encode())
        for field in ("label_alternatives", "subject", "label"):
            bad = copy.deepcopy(source)
            del bad["identity"][field]
            with self.subTest(field=field), self.assertRaises(RecipeError):
                parse_recipe(json.dumps(bad).encode())
        example = parse_recipe(json.dumps(RECIPE_IDENTITY_EXAMPLE).encode())
        self.assertEqual(len(example.tools[0].steps), 2)
        self.assertEqual(
            dict(example.request_headers), {"Notion-Version": "2025-09-03"}
        )


class RecipeIdentityInterpreterTests(unittest.TestCase):
    connect = recipes.RecipeStoreAndInterpreterTests.connect

    def setUp(self):
        recipes.RecipeStoreAndInterpreterTests.setUp(self)
        self.recipe = identity_recipe()
        self.catalog.install(self.recipe)
        self.document = {
            "id": "stable-account",
            "name": None,
            "bot": {"workspace_name": None},
        }
        original = self.http.request

        def request(method, url, **kwargs):
            if url.endswith("/me"):
                original(method, url, **kwargs)
                return self.document.copy()
            return original(method, url, **kwargs)

        self.http.request = request

    def resolve(self):
        return identity(self.recipe, self.http, TOKEN, threading.Event())

    def test_missing_null_empty_and_nullable_parent_fall_back_in_reviewed_order(self):
        for document, expected in (
            ({"id": "stable-account", "name": "Account name"}, "Account name"),
            (
                {
                    "id": "stable-account",
                    "name": None,
                    "bot": {"workspace_name": "Workspace"},
                },
                "Workspace",
            ),
            (
                {"id": "stable-account", "name": "", "bot": {"workspace_name": None}},
                "stable-account",
            ),
            ({"id": "stable-account", "bot": None}, "stable-account"),
            ({"id": 123}, "123"),
        ):
            with self.subTest(document=document):
                self.document = document
                self.assertEqual(self.resolve(), (str(document["id"]), expected))
        source = json.loads(self.recipe.raw)
        source["identity"]["label_alternatives"] = ["/missing"]
        self.recipe = parse_recipe(json.dumps(source).encode())
        self.document = {"id": "stable-account", "name": None}
        with self.assertRaises(AccountError):
            self.resolve()

    def test_malformed_and_reflected_values_fail_without_masking_them_with_an_id(self):
        for value in (False, 123, {}, [], " ", "x" * 255, "bad\nlabel", TOKEN):
            with self.subTest(value=value):
                self.document = {"id": "stable-account", "name": value}
                with self.assertRaises(AccountError):
                    self.resolve()
        for value in (None, False, "bad/id", TOKEN):
            self.document = {"id": value, "name": "Account"}
            with self.subTest(subject=value), self.assertRaises(AccountError):
                self.resolve()
        self.document = {"id": "stable-account", "name": None, "bot": False}
        with self.assertRaises(RecipeError):
            self.resolve()

    def test_fallback_rechecks_subject_and_selected_label_before_tool_dispatch(self):
        account = self.connect()
        self.assertEqual(account.label, "stable-account")
        restored = type(self.catalog)(self.catalog.path)
        self.assertEqual(
            restored.list()[1][0].label_alternatives, self.recipe.label_alternatives
        )
        adapter = RecipeConnector(
            self.recipe, restored, account, self.accounts, http=self.http
        )
        adapter.authorize(threading.Event())
        for change in ({"id": "other-account"}, {"name": "New account label"}):
            self.document = {"id": "stable-account", "name": None, **change}
            with self.subTest(change=change), self.assertRaises(AccountError):
                adapter.authorize(threading.Event())
            self.assertTrue(
                all(url.endswith("/me") for _, url, _, _ in self.http.calls)
            )


class RecipeIdentityOAuthTests(unittest.TestCase):
    setUp = oauth.RecipeOAuthTests.setUp
    authorize = oauth.RecipeOAuthTests.authorize
    connect = oauth.RecipeOAuthTests.connect
    connector = oauth.RecipeOAuthTests.connector
    read = oauth.RecipeOAuthTests.read

    def test_cached_and_refreshed_identity_reject_all_known_credential_echoes(self):
        for reflected in (oauth.REFRESH, oauth.ROTATED, oauth.SECRET):
            self.setUp()
            source = json.loads(self.recipe.raw)
            source.update(version=6)
            source["identity"].update(
                label="/name", label_alternatives=["/email", "/id"]
            )
            self.recipe = parse_recipe(json.dumps(source).encode())
            self.catalog.install(self.recipe)
            account = self.connect()
            connector = self.connector(account)
            self.read(connector)
            original = self.http.request

            def request(method, url, reflected=reflected, original=original, **kwargs):
                result = original(method, url, **kwargs)
                if url.endswith("/me"):
                    return {**result, "name": reflected}
                return result

            self.http.request = request
            count = len(self.http.calls)
            with self.subTest(reflected=reflected), self.assertRaises(AccountError):
                self.read(connector)
            self.assertTrue(
                all(url.endswith("/me") for _, url, _, _ in self.http.calls[count:])
            )
            connector.oauth.cached = None
            with patch.object(self.store, "save_credential") as save:
                with self.assertRaises(AccountError):
                    self.read(connector)
                save.assert_not_called()
