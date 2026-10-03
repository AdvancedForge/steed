"""Bounded selected-field pagination, exact queries and cancellation boundaries."""

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app_connector_support import MemorySecrets
from recipe_support import TOKEN, SyntheticRecipeHttp

from services.app_accounts import AccountError, AccountStore
from services.connector_recipes import (
    RECIPE_PAGINATED_EXAMPLE,
    RecipeStore,
    parse_recipe,
)
from services.connectors import ConnectorRegistry, ConnectorRequest
from services.recipe_connector import RecipeConnector, connect_recipe


class PageHttp(SyntheticRecipeHttp):
    def __init__(self):
        super().__init__()
        self.pages = [
            {"items": [1], "next": "PAGE_2/+?=&", "unshared": "UNSHARED_FIELD"},
            {"items": [2], "next": None},
        ]
        self.on_page = None
        self.reads = 0

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        if url.endswith("/me"):
            return super().request(
                method, url, headers=headers, body=body, cancelled=cancelled
            )
        assert (
            method == "GET"
            and headers == {"Authorization": "Bearer " + TOKEN}
            and body is None
        )
        self.calls.append((method, url, headers, body))
        result = copy.deepcopy(self.pages[self.reads])
        self.reads += 1
        if self.on_page:
            self.on_page()
        return result


class RecipePaginationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = copy.deepcopy(RECIPE_PAGINATED_EXAMPLE)
        self.data["authentication"] = {"type": "bearer"}
        self.catalog = RecipeStore(self.root / "recipes.json")
        self.store = AccountStore(self.root / "accounts.json", MemorySecrets())
        self.http, self.cancelled = PageHttp(), threading.Event()

    def connector(self):
        recipe = parse_recipe(json.dumps(self.data).encode())
        self.catalog.install(recipe)
        account = connect_recipe(
            recipe,
            self.catalog,
            self.store,
            TOKEN,
            ["list_notes"],
            self.cancelled,
            http=self.http,
        )
        return RecipeConnector(
            recipe, self.catalog, account, self.store, http=self.http
        )

    def read(self, connector):
        request = ConnectorRequest(
            "read-pages",
            connector.account.connector,
            "list_notes",
            "read",
            "account",
            None,
        )
        return connector.tools()[0].execute(request, self.cancelled)

    def test_selected_arrays_merge_with_progress_and_encoded_cursor_only(self):
        result = self.read(self.connector())
        self.assertEqual(
            json.loads(result.text),
            {"notes": [1, 2], "_pagination": {"pages": 2, "more_available": False}},
        )
        self.assertNotIn("UNSHARED_FIELD", result.text)
        self.assertNotIn("PAGE_2", result.text)
        urls = [call[1] for call in self.http.calls if not call[1].endswith("/me")]
        self.assertEqual(urls[0], "https://api.fixture.example/notes")
        self.assertEqual(parse_qs(urlsplit(urls[1]).query), {"cursor": ["PAGE_2/+?=&"]})
        self.assertEqual(urlsplit(urls[1]).netloc, "api.fixture.example")

    def test_page_bound_reports_more_without_fetching_another_page(self):
        self.data["tools"][0]["pagination"]["max_pages"] = 1
        result = self.read(self.connector())
        self.assertEqual(self.http.reads, 1)
        self.assertEqual(
            json.loads(result.text)["_pagination"], {"pages": 1, "more_available": True}
        )

    def test_long_valid_provider_and_account_labels_fit_discovery_and_registration(
        self,
    ):
        self.data["name"] = "n" * 80
        self.data["origin"] = "https://" + ".".join(["a" * 61] * 3 + ["b" * 52, "com"])
        self.data["tools"][0]["description"] = "d" * 300
        self.http.label = "l" * 254
        connector = self.connector()
        definition = connector.tools()[0]
        self.assertLessEqual(len(definition.description), 512)
        self.assertIn(connector.recipe.origin, definition.description)
        self.assertIn("up to 3 pages", definition.description)
        registry = ConnectorRegistry()
        registry.register(definition)

    def test_repeated_cursor_invalid_cursor_and_wrong_response_shape_fail(self):
        for pages in (
            [{"items": [1], "next": "same"}, {"items": [2], "next": "same"}],
            [{"items": [1], "next": TOKEN}],
            [{"items": [1], "next": 7}],
            [{"items": [1], "next": "x" * 513}],
            [{"items": "scalar", "next": None}],
            [{"items": [1]}],
        ):
            with self.subTest(pages=pages):
                self.http.pages, self.http.reads = pages, 0
                with self.assertRaises((ValueError, KeyError)):
                    self.read(self.connector())

    def test_output_budget_stops_before_following_another_cursor(self):
        self.http.pages = [{"items": ["x" * 17000], "next": "page2"}]
        with self.assertRaises(ValueError):
            self.read(self.connector())
        self.assertEqual(self.http.reads, 1)

    def test_revocation_or_cancellation_between_pages_cannot_publish_results(self):
        for mutate in (
            lambda connector: self.store.disable(connector.account.account_id),
            lambda connector: self.catalog.disable(connector.recipe.provider),
            lambda connector: self.cancelled.set(),
        ):
            self.cancelled.clear()
            self.http.reads = 0
            connector = self.connector()
            self.http.on_page = lambda mutate=mutate, connector=connector: mutate(
                connector
            )
            with (
                self.subTest(mutate=mutate),
                self.assertRaises((AccountError, ValueError)),
            ):
                self.read(connector)
            self.assertEqual(self.http.reads, 1)
        self.http.on_page = None

    def test_identity_change_after_one_page_denies_the_next_request(self):
        connector = self.connector()
        self.http.on_page = lambda: setattr(self.http, "subject", "changed-user")
        with self.assertRaises(AccountError):
            self.read(connector)
        self.assertEqual(self.http.reads, 1)

    def test_schema_bounds_pages_parameters_resource_paths_and_reserved_outputs(self):
        for changes in (
            {"max_pages": 0},
            {"max_pages": True},
            {"max_pages": 6},
            {"parameter": "cursor&other"},
            {"parameter": "x" * 33},
            {"cursor": "missing/slash"},
        ):
            data = copy.deepcopy(self.data)
            data["tools"][0]["pagination"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_recipe(json.dumps(data).encode())
        for changes in (
            {"path": "/notes/{destination}"},
            {"response": [{"label": "_pagination", "pointer": "/items"}]},
        ):
            data = copy.deepcopy(self.data)
            data["tools"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_recipe(json.dumps(data).encode())
