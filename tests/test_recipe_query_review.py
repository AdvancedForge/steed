"""Review regressions: persisted receipts, exact methods and structural queries."""

import hashlib
import json
import unittest
from types import SimpleNamespace

from test_recipe_queries import parse, query_data

from services.connector_recipes import RecipeError
from services.connector_store import request_fingerprint
from services.recipe_connector import RecipeConnector


class QueryReviewTests(unittest.TestCase):
    def test_legacy_request_hashes_survive_upgrade_without_typed_inputs(self):
        for artifact in (None, SimpleNamespace(artifact_id="a", sha256="d")):
            old = [
                "connector",
                "tool",
                "read",
                "account",
                "a" if artifact else "",
                "d" if artifact else "",
            ]
            digest = hashlib.sha256(
                json.dumps(old, ensure_ascii=True).encode()
            ).hexdigest()
            self.assertEqual(
                request_fingerprint("connector", "tool", "read", "account", artifact),
                digest,
            )
            self.assertEqual(
                request_fingerprint(
                    "connector", "tool", "read", "account", artifact, {}
                ),
                digest,
            )
            self.assertNotEqual(
                request_fingerprint(
                    "connector", "tool", "read", "account", artifact, {"q": "value"}
                ),
                digest,
            )

    def test_typed_get_cannot_silently_dispatch_as_post(self):
        data = query_data()
        data["tools"][0]["method"] = "GET"
        with self.assertRaises(RecipeError):
            parse(data)

    def test_post_pagination_cannot_generate_unapproved_query_urls(self):
        data = query_data()
        data["tools"][0]["pagination"]["location"] = "query"
        with self.assertRaises(RecipeError):
            parse(data)

    def test_literal_and_empty_read_bodies_and_null_identity_are_supported(self):
        for body in ({"limit": 10}, {}, None):
            data = query_data()
            tool = data["tools"][0]
            tool.pop("pagination")
            tool.pop("inputs")
            tool["request"] = body
            data["identity"].update(method="POST", request=None)
            recipe = parse(data)
            self.assertEqual(recipe.tools[0].request_template.materialize({}), body)
            self.assertIsNone(recipe.identity_request.materialize({}))

    def test_graphql_empty_variables_are_valid_for_fixed_viewer_query(self):
        data = query_data()
        tool = data["tools"][1]
        tool.pop("destination")
        tool["query"].update(
            operation="Me", document="query Me { viewer { id name } }", variables={}
        )
        self.assertEqual(parse(data).tools[1].query.variables.materialize({}), {})

    def test_graphql_operation_name_variables_and_syntax_are_checked(self):
        for document, variables in (
            (
                "query Wrong($id: ID!) { note(id: $id) { id } }",
                {"id": {"$bind": "destination"}},
            ),
            (
                "query NoteById($id: ID! { note(id: $id) { id } }",
                {"id": {"$bind": "destination"}},
            ),
            ("query NoteById($id: ID!) { note(id: $id) { id } }", {}),
            ("query NoteById { note { id } }", {"id": {"$bind": "destination"}}),
            ("query NoteById { note @skip(if: false) { id } }", {}),
            ("query NoteById { note { ... on Note { id } } }", {}),
        ):
            data = query_data()
            data["tools"][1]["query"].update(document=document, variables=variables)
            with self.subTest(document=document), self.assertRaises(RecipeError):
                parse(data)

    def test_graphql_comments_and_literal_keywords_are_not_operations(self):
        data = query_data()
        tool = data["tools"][1]
        tool.pop("destination")
        tool["query"].update(
            operation="Me",
            document='# mutation is a comment\nquery Me { note(text: "query mutation { ") { id } }',
            variables={},
        )
        self.assertEqual(parse(data).tools[1].query.operation, "Me")

    def test_graphql_document_limit_is_utf8_bytes(self):
        data = query_data()
        tool = data["tools"][1]
        tool.pop("destination")
        tool["query"].update(
            operation="Me",
            document='query Me { note(text: "' + "界" * 1400 + '") { id } }',
            variables={},
        )
        with self.assertRaises(RecipeError):
            parse(data)

    def test_initial_body_cursor_retains_the_exact_reviewed_value(self):
        # Use a compiled literal initial cursor, rather than the example's null.
        data = query_data()
        data["tools"][0]["request"]["cursor"] = "REVIEWED-START"
        tool = parse(data).tools[0]
        adapter = object.__new__(RecipeConnector)
        inputs = {"query": "q", "limit": 10, "archived": False}
        self.assertEqual(
            adapter.typed_body(tool, "account", inputs)["cursor"], "REVIEWED-START"
        )
        self.assertEqual(
            adapter.typed_body(tool, "account", inputs, cursor="NEXT")["cursor"], "NEXT"
        )
