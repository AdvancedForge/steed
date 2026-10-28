"""Explicit continuation paths, boolean stop/success contracts and old grammar."""

import copy
import json
import threading
import unittest

import test_recipe_queries as queries
from test_recipe_queries import parse, query_data

from services.connector_recipes import RECIPE_CONTINUATION_EXAMPLE, RecipeError
from services.recipe_connector import RecipeConnector
from services.recipe_oauth import token_http


def continued():
    return copy.deepcopy(RECIPE_CONTINUATION_EXAMPLE)


class ContinuationGrammarTests(unittest.TestCase):
    def test_new_fields_do_not_change_version_seven_grammar(self):
        data = continued()
        data["version"] = 7
        with self.assertRaises(RecipeError):
            parse(data)
        self.assertIsNone(parse(query_data()).tools[0].pagination.continuation)

    def test_continuations_have_fixed_reviewed_paths_and_cursor_only_bodies(self):
        for field, value in (
            ("path", "https://foreign.example/continue"),
            ("path", "/continue/{destination}"),
            ("request", {"cursor": {"$bind": "input.query"}}),
            ("request", {"cursor": {"$bind": "artifact.content"}}),
            ("request", {"cursor": "literal"}),
        ):
            data = continued()
            data["tools"][0]["pagination"]["continuation"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RecipeError):
                parse(data)

    def test_resource_query_pagination_is_supported_without_initial_cursor_field(self):
        data = continued()
        tool = data["tools"][0]
        tool["path"] = "/sources/{destination}/query"
        tool["destination"] = "resource_id"
        tool["request"].pop("cursor")
        recipe = parse(data)
        self.assertEqual(
            recipe.tools[0].pagination.continuation.path, "/notes/search/continue"
        )
        # Connecting builds the transport; resource paths must not be rejected.
        http = token_http(recipe)
        self.assertTrue(http.read_rule("/sources/source-1/query"))

    def test_success_rules_use_real_booleans_and_declare_missing_behavior(self):
        for field, value in (
            ("value", 1),
            ("value", "true"),
            ("required", 0),
            ("required", None),
        ):
            data = continued()
            data["tools"][0]["success"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RecipeError):
                parse(data)

    def test_read_and_write_post_routes_cannot_overlap(self):
        from services.connector_recipes import RECIPE_WRITE_EXAMPLE

        for read_path in ("/notes", "/notes/{destination}"):
            data = continued()
            data["tools"][0]["path"] = read_path
            if "{destination}" in read_path:
                data["tools"][0]["destination"] = "resource_id"
            write = copy.deepcopy(
                next(
                    t
                    for t in RECIPE_WRITE_EXAMPLE["tools"]
                    if t.get("effect") == "write"
                )
            )
            write["path"] = "/notes" if read_path == "/notes" else "/notes/fixed"
            data["tools"].append(write)
            with self.assertRaises(RecipeError):
                parse(data)


class ContinuationReviewTests(unittest.TestCase):
    def test_native_review_discloses_stop_success_and_exact_continuation_body(self):
        from PyQt6.QtWidgets import QPlainTextEdit
        from qt_test_support import application

        from ui.recipe_review import RecipeReviewDialog

        app = application()
        dialog = RecipeReviewDialog(None, parse(continued()))
        self.addCleanup(dialog.close)
        text = dialog.findChildren(QPlainTextEdit)[0].toPlainText()
        self.assertIn("Stop signal: /has_more (exact boolean)", text)
        self.assertIn('"required": true', text)
        self.assertIn("Continuation: POST /notes/search/continue", text)
        self.assertIn('"$bind": "cursor"', text)
        dialog.show()
        app.processEvents()
        dialog.grab().save("/tmp/steed-continuation-review.png")


class ContinuationExecutionTests(unittest.TestCase):
    setUp = queries.TypedReadExecutionTests.setUp
    connect = queries.TypedReadExecutionTests.connect
    request = queries.TypedReadExecutionTests.request

    def configure(self, data=None):
        self.recipe = parse(data or continued())
        self.catalog.install(self.recipe)
        self.http.pages = [
            {"ok": True, "items": [{"id": "n1"}], "cursor": "PAGE-2", "has_more": True},
            {"ok": True, "items": [], "cursor": "FINAL-CURSOR", "has_more": False},
        ]
        self.account = self.connect()
        self.adapter = RecipeConnector(
            self.recipe, self.catalog, self.account, self.accounts, http=self.http
        )

    def read(self):
        return self.adapter.execute(
            self.recipe.tools[0], self.request(inputs={"query": "q"}), threading.Event()
        )

    def test_continuation_has_exact_cursor_body_and_final_cursor_does_not_loop(self):
        self.configure()
        result = self.read()
        self.assertEqual(
            json.loads(result.text)["_pagination"],
            {"pages": 2, "more_available": False},
        )
        posts = [
            (url, json.loads(body))
            for method, url, _, body in self.http.calls
            if method == "POST"
        ]
        self.assertEqual(
            posts[-1],
            (self.recipe.origin + "/notes/search/continue", {"cursor": "PAGE-2"}),
        )
        self.assertIn("text", posts[0][1])
        http = token_http(self.recipe)
        self.assertTrue(http.read_rule("/notes/search/continue"))
        self.assertFalse(http.read_rule("/notes/search/continue/foreign"))

    def test_page_limit_reports_more_available_instead_of_hiding_truncation(self):
        data = continued()
        data["tools"][0]["pagination"]["max_pages"] = 1
        self.configure(data)
        self.assertEqual(
            json.loads(self.read().text)["_pagination"],
            {"pages": 1, "more_available": True},
        )

    def test_true_more_flag_requires_a_cursor_and_typed_boolean(self):
        for flag, cursor in (
            ("false", "PAGE-2"),
            (1, "PAGE-2"),
            (True, None),
            (True, ""),
        ):
            self.configure()
            self.http.pages[0].update(has_more=flag, cursor=cursor)
            before = len(self.http.calls)
            with self.subTest(flag=flag, cursor=cursor), self.assertRaises(RecipeError):
                self.read()
            self.assertEqual(
                sum(call[0] == "POST" for call in self.http.calls[before:]), 1
            )

    def test_false_or_malformed_success_flag_never_returns_reviewed_data(self):
        for flag in (False, None, 1, "true"):
            self.configure()
            self.http.pages[0]["ok"] = flag
            with self.subTest(flag=flag), self.assertRaises(RecipeError):
                self.read()

    def test_optional_absent_success_flag_still_rejects_explicit_false(self):
        data = continued()
        data["tools"][0]["success"]["required"] = False
        self.configure(data)
        for page in self.http.pages:
            page.pop("ok")
        self.assertEqual(json.loads(self.read().text)["notes"], [{"id": "n1"}])
        self.http.pages[0]["ok"] = False
        with self.assertRaises(RecipeError):
            self.read()

    def test_failed_identity_flag_prevents_any_read_dispatch(self):
        data = continued()
        data["identity"]["success"] = {
            "pointer": "/ok",
            "value": True,
            "required": True,
        }
        self.recipe = parse(data)
        self.catalog.install(self.recipe)
        before = len(self.http.calls)
        with self.assertRaises(RecipeError):
            self.connect()
        self.assertFalse(any(c[0] == "POST" for c in self.http.calls[before:]))

    def test_empty_string_inputs_support_root_folder_paths(self):
        self.configure()
        self.assertEqual(self.recipe.tools[0].inputs[0].coerce(""), "")
