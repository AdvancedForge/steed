"""Typed v7 recipe reads: bounded inputs, exact bodies and honest HTTP-200 errors."""

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

import test_recipe_proposals as fixtures
from app_connector_support import MemorySecrets
from PyQt6.QtWidgets import QPlainTextEdit
from qt_test_support import application
from recipe_support import TOKEN

from services.app_accounts import AccountError, AccountStore
from services.app_oauth import ApiError
from services.connector_recipes import (
    RECIPE_EXAMPLE,
    RECIPE_QUERY_EXAMPLE,
    RecipeError,
    RecipeStore,
    parse_recipe,
    read_inputs,
)
from services.connectors import ConnectorRegistry, ConnectorRequest
from services.recipe_connector import (
    RecipeConnector,
    connect_recipe,
    input_schema,
)
from ui.recipe_review import RecipeReviewDialog


def query_data(**changes):
    data = copy.deepcopy(RECIPE_QUERY_EXAMPLE)
    data.update(changes)
    return data


def parse(data):
    return parse_recipe(json.dumps(data).encode())


class QueryRecipeHttp:
    """Synthetic typed read endpoint; every request body is recorded exactly."""

    def __init__(self):
        self.calls, self.bodies, self.subject, self.label = [], [], "u-1", "a@b.test"
        self.pages = [
            {"items": [{"id": "n1"}], "cursor": "PAGE-2"},
            {"items": [], "cursor": ""},
        ]
        self.error = None
        self.gate = None
        self.entered = threading.Event()
        self.graphql = {"data": {"note": {"id": "n1", "title": "T", "body": "B"}}}
        self.fail = False

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise ApiError()
        self.calls.append((method, url, headers, body))
        assert headers["Authorization"] == "Bearer " + TOKEN
        if method == "GET":
            return {"id": self.subject, "email": self.label}
        assert headers["Content-Type"] == "application/json"
        payload = json.loads(body)
        self.bodies.append(payload)
        if self.fail:
            raise ApiError()
        if url.endswith("/graphql"):
            return copy.deepcopy(self.graphql)
        if self.error is not None:
            return self.error
        # Independent clients can repeat a read: paging follows the request
        # cursor, rather than consuming this fixture's process-wide call count.
        cursor = payload.get("cursor")
        index = next(
            (
                i + 1
                for i, page in enumerate(self.pages[:-1])
                if cursor and page["cursor"] == cursor
            ),
            0,
        )
        return copy.deepcopy(self.pages[index])


class PostIdentityRecipeHttp(QueryRecipeHttp):
    """Dropbox-style identity POST returning the account without a body field."""

    def __init__(self):
        super().__init__()
        self.identity_bodies = []

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        if method == "POST" and url.endswith("/users/me"):
            self.identity_bodies.append(json.loads(body))
            return {"account_id": "dbid-fixture", "email": self.label}
        return super().request(
            method, url, headers=headers, body=body, cancelled=cancelled
        )


class TypedReadGrammarTests(unittest.TestCase):
    def test_query_example_compiles_with_typed_inputs_and_fixed_graphql(self):
        recipe = parse(query_data())
        self.assertEqual(recipe.version, 7)
        search, graphql = recipe.tools
        self.assertEqual(search.method, "POST")
        self.assertEqual(search.effect, "read")
        self.assertEqual(
            [i.name for i in search.inputs], ["query", "limit", "archived"]
        )
        self.assertEqual(search.inputs[1].default, 10)
        self.assertEqual(search.pagination.location, "body")
        self.assertEqual(search.errors.pointer, "/error")
        self.assertFalse(search.idempotency_header)
        self.assertEqual(graphql.query.operation, "NoteById")
        self.assertEqual(graphql.query.data_pointer, "/data")
        self.assertEqual(
            [i["name"] for i in input_schema(search)], ["query", "limit", "archived"]
        )

    def test_typed_reads_stay_read_only_and_never_gain_write_authority(self):
        data = query_data()
        data["tools"][0]["idempotency_header"] = "Idempotency-Key"
        data["tools"][0]["reconciliation"] = {
            "path": "/operations/{operation_id}",
            "operation_id": "/operation_id",
            "status": "/status",
            "success": "succeeded",
        }
        with self.assertRaises(RecipeError):
            parse(data)

    def test_unbounded_or_mistyped_inputs_are_rejected(self):
        base = query_data()["tools"][0]
        for inputs in (
            {"limit": {"type": "integer", "minimum": 10, "maximum": 1}},
            {"limit": {"type": "integer", "minimum": True, "maximum": 5}},
            {"limit": {"type": "array", "minimum": 1, "maximum": 5}},
            {"limit": {"type": "integer"}},
            {"Limit": {"type": "boolean"}},
            {"limit": {"type": "string", "max_length": 0}},
            {"limit": {"type": "string", "max_length": 513}},
            {f"input_{i}": {"type": "boolean"} for i in range(9)},
        ):
            data = query_data()
            data["tools"][0] = {**base, "inputs": inputs}
            with self.subTest(inputs=inputs), self.assertRaises(RecipeError):
                parse(data)

    def test_read_defaults_must_satisfy_their_own_bounds(self):
        for default in (0, 51, True):
            data = query_data()
            data["tools"][0]["inputs"]["limit"]["default"] = default
            with self.subTest(default=default), self.assertRaises(RecipeError):
                parse(data)

    def test_request_bodies_bind_only_reviewed_inputs(self):
        for request in (
            {"text": {"$bind": "artifact.content"}},
            {"text": {"$bind": "operation_id"}},
            {"text": {"$bind": "input.unknown"}},
        ):
            data = query_data()
            data["tools"][0]["request"] = request
            with self.subTest(request=request), self.assertRaises(RecipeError):
                parse(data)

    def test_graphql_documents_reject_mutations_and_unbalanced_text(self):
        for document in (
            "mutation NoteById($id: ID!) { note(id: $id) { id } }",
            "subscription NoteById { note { id } }",
            "query NoteById { note { id }",
            "query NoteById { note { id } } query Other { x }",
            "query { note { id } }",
            "x" * 4097,
        ):
            data = query_data()
            data["tools"][1]["query"]["document"] = document
            with self.subTest(document=document[:32]), self.assertRaises(RecipeError):
                parse(data)

    def test_graphql_variables_may_only_use_the_reviewed_destination(self):
        for variables in (
            {"id": {"$bind": "artifact.content"}},
            {"id": {"$bind": "input.limit"}},
        ):
            data = query_data()
            data["tools"][1]["query"]["variables"] = variables
            with self.subTest(variables=variables), self.assertRaises(RecipeError):
                parse(data)

    def test_typed_reads_reject_older_versions_and_write_shaped_requests(self):
        data = query_data(version=6)
        with self.assertRaises(RecipeError):
            parse(data)
        data = query_data()
        data["tools"][0]["effect"] = "write"
        with self.assertRaises(RecipeError):
            parse(data)
        data = query_data()
        data["tools"][0]["destination"] = "email"
        with self.assertRaises(RecipeError):
            parse(data)

    def test_v1_to_v6_recipes_are_unchanged_by_version_seven(self):
        recipe = parse(RECIPE_EXAMPLE)
        self.assertEqual(recipe.tools[0].method, "GET")
        self.assertEqual(recipe.tools[0].inputs, ())
        self.assertIsNone(recipe.tools[0].errors)
        self.assertIsNone(recipe.tools[0].query)
        self.assertEqual(recipe.identity_method, "GET")

    def test_reviewed_post_identity_bodies_are_literal_and_bounded(self):
        data = query_data()
        data["identity"] = {
            "path": "/users/me",
            "method": "POST",
            "request": {"query": "query Me { me { id } }"},
            "subject": "/account_id",
            "label": "/email",
        }
        recipe = parse(data)
        self.assertEqual(recipe.identity_method, "POST")
        self.assertEqual(
            recipe.identity_request.source(), {"query": "query Me { me { id } }"}
        )
        self.assertEqual(recipe.metadata()["identity"]["method"], "POST")
        for request in ({"query": {"$bind": "destination"}}, "null", [1, 2]):
            data["identity"]["request"] = request
            with self.subTest(request=request), self.assertRaises(RecipeError):
                parse(data)
        data["identity"]["method"] = "PUT"
        with self.assertRaises(RecipeError):
            parse(data)
        data["identity"] = {
            "path": "/users/me",
            "subject": "/id",
            "label": "/email",
            "method": "POST",
        }
        with self.assertRaises(RecipeError):
            parse(data)


class TypedInputResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tool = parse(query_data()).tools[0]

    def test_optional_inputs_default_and_required_ones_must_be_present(self):
        self.assertEqual(
            read_inputs({"query": "notes"}, self.tool.inputs),
            {"query": "notes", "limit": 10, "archived": False},
        )
        self.assertEqual(
            read_inputs({"query": "n"}, self.tool.inputs),
            {"query": "n", "limit": 10, "archived": False},
        )
        for values in (None, {}):
            with self.subTest(values=values), self.assertRaises(RecipeError):
                read_inputs(values, self.tool.inputs)

    def test_unknown_names_types_and_ranges_fail_closed(self):
        for values in (
            {"query": "x", "other": 1},
            {"query": 5},
            {"query": "x", "limit": "5"},
            {"query": "x", "limit": 0},
            {"query": "x", "limit": 51},
            {"query": "x", "archived": "true"},
            {"query": "x" * 201},
            {"query": "x", "limit": 2**53},
            {"query": "x", "limit": [10]},
            {"query": "x", "limit": None},
        ):
            with self.subTest(values=values), self.assertRaises(RecipeError):
                read_inputs(values, self.tool.inputs)


class TypedReadExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.catalog = RecipeStore(self.root / "recipes.json")
        self.recipe = parse(query_data())
        self.catalog.install(self.recipe)
        self.secrets = MemorySecrets()
        self.accounts = AccountStore(self.root / "accounts.json", self.secrets)
        self.http = QueryRecipeHttp()
        self.account = self.connect()
        self.tools = {
            tool.tool: tool
            for tool in RecipeConnector(
                self.recipe, self.catalog, self.account, self.accounts, http=self.http
            ).tools()
        }

    def connect(self, capabilities=("search_notes", "read_note_graphql"), http=None):
        return connect_recipe(
            self.recipe,
            self.catalog,
            self.accounts,
            TOKEN,
            capabilities,
            threading.Event(),
            http=http or self.http,
        )

    def request(self, tool="search_notes", destination="account", inputs=None):
        return ConnectorRequest(
            "read-1",
            self.account.connector,
            tool,
            "read",
            destination,
            None,
            inputs=inputs,
        )

    def test_typed_read_posts_the_exact_reviewed_body_with_default_inputs(self):
        outcome = self.tools["search_notes"].execute(
            self.request(inputs={"query": "budget"}), threading.Event()
        )
        self.assertEqual(
            json.loads(outcome.text),
            {
                "notes": [{"id": "n1"}],
                "_pagination": {"pages": 2, "more_available": False},
            },
        )
        self.assertEqual(
            self.http.bodies,
            [
                {
                    "text": "budget",
                    "limit": 10,
                    "include_archived": False,
                    "cursor": None,
                },
                {
                    "text": "budget",
                    "limit": 10,
                    "include_archived": False,
                    "cursor": "PAGE-2",
                },
            ],
        )
        for method, url, headers, _ in self.http.calls:
            if method != "POST":
                continue
            self.assertTrue(url.endswith("/notes/search"))
            self.assertNotIn("Idempotency-Key", headers)

    def test_bounded_input_values_reach_the_body_and_invalid_ones_never_dispatch(self):
        self.tools["search_notes"].execute(
            self.request(inputs={"query": "q", "limit": 3, "archived": True}),
            threading.Event(),
        )
        self.assertEqual(self.http.bodies[0]["limit"], 3)
        self.assertIs(self.http.bodies[0]["include_archived"], True)
        count = len(self.http.calls)
        for inputs in (
            {"query": "q", "limit": 0},
            {"unknown": 1},
            {"query": "q" * 300},
        ):
            with self.subTest(inputs=inputs), self.assertRaises(RecipeError):
                self.tools["search_notes"].execute(
                    self.request(inputs=inputs), threading.Event()
                )
        self.assertEqual(len(self.http.calls), count)

    def test_graphql_read_sends_the_fixed_document_and_never_query_text(self):
        outcome = self.tools["read_note_graphql"].execute(
            self.request(tool="read_note_graphql", destination="note-1"),
            threading.Event(),
        )
        self.assertEqual(
            json.loads(outcome.text), {"note": {"id": "n1", "title": "T", "body": "B"}}
        )
        sent = self.http.bodies[-1]
        self.assertEqual(sent["operationName"], "NoteById")
        self.assertEqual(sent["variables"], {"id": "note-1"})
        self.assertTrue(sent["query"].startswith("query NoteById"))
        self.assertNotIn("mutation", sent["query"])
        for inputs in ({"query": "anything"},):
            with self.assertRaises(RecipeError):
                self.tools["read_note_graphql"].execute(
                    self.request(
                        tool="read_note_graphql", destination="note-1", inputs=inputs
                    ),
                    threading.Event(),
                )

    def test_graphql_errors_and_partial_data_are_reported_not_returned(self):
        self.http.graphql = {
            "data": {"note": None},
            "errors": [
                {"message": "not authorized", "extensions": {"code": "FORBIDDEN"}},
                {"message": "second problem"},
            ],
        }
        with self.assertRaises(RecipeError) as caught:
            self.tools["read_note_graphql"].execute(
                self.request(tool="read_note_graphql", destination="note-1"),
                threading.Event(),
            )
        self.assertIn("FORBIDDEN", str(caught.exception))
        self.assertIn("not authorized", str(caught.exception))
        self.http.graphql = {"data": None, "errors": [{"message": "boom"}]}
        with self.assertRaises(RecipeError):
            self.tools["read_note_graphql"].execute(
                self.request(tool="read_note_graphql", destination="note-1"),
                threading.Event(),
            )
        self.http.graphql = {"data": None}
        with self.assertRaises(RecipeError):
            self.tools["read_note_graphql"].execute(
                self.request(tool="read_note_graphql", destination="note-1"),
                threading.Event(),
            )

    def test_application_error_contract_reports_http_200_failures(self):
        self.http.error = {
            "error": {"reason": "invalid_query", "summary": "Unsupported search text"},
            "items": [],
            "cursor": "",
        }
        with self.assertRaises(RecipeError) as caught:
            self.tools["search_notes"].execute(
                self.request(inputs={"query": "x"}), threading.Event()
            )
        self.assertIn("invalid_query", str(caught.exception))
        self.assertIn("Unsupported search text", str(caught.exception))
        for absent in ({"error": None, "items": [], "cursor": ""},):
            self.http.error = absent
            with self.subTest(absent=sorted(absent)):
                outcome = self.tools["search_notes"].execute(
                    self.request(inputs={"query": "x"}), threading.Event()
                )
                self.assertEqual(json.loads(outcome.text)["notes"], [])

    def test_credentials_are_redacted_in_typed_read_results_and_errors(self):
        self.http.pages = [{"items": [{"id": TOKEN}], "cursor": ""}]
        outcome = self.tools["search_notes"].execute(
            self.request(inputs={"query": "x"}), threading.Event()
        )
        self.assertNotIn(TOKEN, outcome.text)
        self.http.error = {
            "error": {"reason": TOKEN, "summary": "reflected " + TOKEN},
            "items": [],
            "cursor": "",
        }
        with self.assertRaises(RecipeError) as caught:
            self.tools["search_notes"].execute(
                self.request(inputs={"query": "x"}), threading.Event()
            )
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_identity_rechecks_and_cancellation_stop_typed_reads(self):
        self.http.subject = "different-user"
        with self.assertRaises(AccountError):
            self.tools["search_notes"].execute(
                self.request(inputs={"query": "x"}), threading.Event()
            )
        self.http.subject = "u-1"
        stopped = threading.Event()
        stopped.set()
        with self.assertRaises(AccountError):
            self.tools["search_notes"].execute(
                self.request(inputs={"query": "x"}), stopped
            )
        self.catalog.disable(self.recipe.provider)
        count = len(self.http.calls)
        with self.assertRaises(RecipeError):
            self.tools["search_notes"].execute(
                self.request(inputs={"query": "x"}), threading.Event()
            )
        self.assertEqual(len(self.http.calls), count)

    def test_repeated_cursors_and_reflected_credentials_fail_closed(self):
        self.http.pages = [{"items": [{"id": "n1"}], "cursor": "SAME"}] * 3
        with self.assertRaises(RecipeError):
            self.tools["search_notes"].execute(
                self.request(inputs={"query": "x"}), threading.Event()
            )
        self.http.pages = [{"items": [{"id": "n1"}], "cursor": TOKEN}]
        self.http.gate_pages = None
        with self.assertRaises(RecipeError):
            self.tools["search_notes"].execute(
                self.request(inputs={"query": "x"}), threading.Event()
            )

    def test_tool_discovery_publishes_inputs_without_granting_authority(self):
        registry = ConnectorRegistry()
        for tool in self.tools.values():
            registry.register(tool)
        described = {t["tool"]: t for t in registry.describe()}
        self.assertEqual(
            [i["name"] for i in described["search_notes"]["inputs"]],
            ["query", "limit", "archived"],
        )
        self.assertEqual(described["read_note_graphql"]["inputs"], [])
        self.assertEqual(described["search_notes"]["effect"], "read")
        self.assertFalse(described["search_notes"]["requires_review"])
        self.assertFalse(described["search_notes"]["requires_artifact"])


class TypedReadReviewTests(unittest.TestCase):
    """The trusted review shows the exact typed read and GraphQL wire."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_review_discloses_inputs_documents_errors_and_identity_body(self):
        data = query_data()
        data["identity"] = {
            "path": "/users/me",
            "method": "POST",
            "request": {"query": "query Me { me { id } }"},
            "subject": "/account_id",
            "label": "/email",
        }
        recipe = parse(data)
        dialog = RecipeReviewDialog(None, recipe)
        self.addCleanup(dialog.close)
        text = dialog.findChildren(QPlainTextEdit)[0].toPlainText()
        self.assertIn("Account identity: POST /users/me", text)
        self.assertIn("query Me { me { id } }", text)
        self.assertIn("query: string, max 200 characters (required)", text)
        self.assertIn("limit: integer, 1..50, default 10", text)
        self.assertIn("archived: boolean, default false", text)
        self.assertIn("query NoteById", text)
        self.assertIn("note(id: $id)", text)
        self.assertIn("data: /data; errors reported from /errors", text)
        self.assertIn("Application errors reported from /error", text)
        self.assertIn("body parameter cursor", text)
        self.assertNotIn("mutation", text)


class PostIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.catalog = RecipeStore(self.root / "recipes.json")
        data = query_data()
        data["identity"] = {
            "path": "/users/me",
            "method": "POST",
            "request": {"null": None},
            "subject": "/account_id",
            "label": "/email",
        }
        data["tools"] = data["tools"][:1]
        self.recipe = parse(data)
        self.catalog.install(self.recipe)
        self.secrets = MemorySecrets()
        self.accounts = AccountStore(self.root / "accounts.json", self.secrets)
        self.http = PostIdentityRecipeHttp()

    def test_post_identity_uses_its_reviewed_body_for_connect_and_every_read(self):
        account = connect_recipe(
            self.recipe,
            self.catalog,
            self.accounts,
            TOKEN,
            ("search_notes",),
            threading.Event(),
            http=self.http,
        )
        self.assertEqual(account.subject, "dbid-fixture")
        self.assertEqual(self.http.identity_bodies, [{"null": None}])
        connector = RecipeConnector(
            self.recipe, self.catalog, account, self.accounts, http=self.http
        )
        tool = connector.tools()[0]
        tool.execute(
            ConnectorRequest(
                "read-1",
                account.connector,
                "search_notes",
                "read",
                "account",
                None,
                inputs={"query": "x"},
            ),
            threading.Event(),
        )
        # connect, authorize before the read, then the pagination identity recheck.
        self.assertEqual(len(self.http.identity_bodies), 3)


if __name__ == "__main__":
    unittest.main()


class TypedReadServiceTests(unittest.TestCase):
    """Typed inputs travel through the real app.call_tool receipt pipeline."""

    setUpClass = fixtures.RecipeProposalTests.__dict__["setUpClass"]
    tearDown = fixtures.RecipeProposalTests.tearDown
    wait_for = fixtures.RecipeProposalTests.wait_for
    call = fixtures.RecipeProposalTests.call
    task = fixtures.RecipeProposalTests.task

    def setUp(self):
        fixtures.RecipeProposalTests.setUp(self)
        self.http = QueryRecipeHttp()
        self.recipe = parse(query_data())
        self.manager.install_recipe(self.recipe)
        self.manager.connect_recipe_account(
            self.recipe.provider,
            TOKEN,
            [t.name for t in self.recipe.tools],
        )
        self.wait_for(lambda: not self.manager.busy)
        self.assertFalse(self.manager.failure)
        self.account = self.accounts.list()[0]

    def read_task(self, tool="search_notes", destination="account"):
        client = self.task(
            ["app"],
            connector_permissions=[
                {
                    "connector": self.account.connector,
                    "tool": tool,
                    "effect": "read",
                    "destination": destination,
                }
            ],
        )
        return client, {
            "connector": self.account.connector,
            "tool": tool,
            "destination": destination,
            "request_id": "read-1",
        }

    def test_agent_supplied_inputs_reach_the_exact_reviewed_body(self):
        client, args = self.read_task()
        self.call(
            "app.call_tool",
            client=client,
            **args,
            inputs={"query": "budget", "limit": 5},
        )
        self.wait_for(
            lambda: (
                self.call("app.status", client=client, request_id="read-1").state
                == "succeeded"
            )
        )
        self.assertEqual(self.http.bodies[0]["text"], "budget")
        self.assertEqual(self.http.bodies[0]["limit"], 5)
        receipt = self.call("app.status", client=client, request_id="read-1")
        self.assertEqual(json.loads(receipt.text)["notes"], [{"id": "n1"}])
        listed = self.call("app.list_tools", client=client)
        search = [t for t in listed if t["tool"] == "search_notes"][0]
        self.assertEqual(
            [i["name"] for i in search["inputs"]], ["query", "limit", "archived"]
        )

    def test_request_ids_bind_inputs_and_invalid_ones_never_dispatch(self):
        client, args = self.read_task()
        self.call("app.call_tool", client=client, **args, inputs={"query": "a"})
        self.wait_for(
            lambda: (
                self.call("app.status", client=client, request_id="read-1").state
                == "succeeded"
            )
        )
        before = len(self.http.bodies)
        with self.assertRaises(ValueError):
            self.call(
                "app.call_tool", client=client, **args, inputs={"query": "different"}
            )
        for inputs in ({"query": "a", "limit": 0}, {"unknown": 1}, {}):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                self.call(
                    "app.call_tool",
                    client=client,
                    **{**args, "request_id": "read-" + str(len(inputs)) + "x"},
                    inputs=inputs,
                )
        self.assertEqual(len(self.http.bodies), before)
        self.assertEqual(
            self.call(
                "app.call_tool", client=client, **args, inputs={"query": "a"}
            ).state,
            "succeeded",
        )
        self.assertEqual(len(self.http.bodies), before)

    def test_typed_reads_reject_artifacts_and_write_destinations(self):
        client, args = self.read_task()
        with self.assertRaises(ValueError):
            self.call(
                "app.call_tool",
                client=client,
                **args,
                artifact_id="artifact-1",
                inputs={"query": "a"},
            )
        # A grant for one destination cannot be spent on another account.
        with self.assertRaises(ValueError):
            self.call(
                "app.call_tool",
                client=client,
                **{**args, "request_id": "read-2", "destination": "other-account"},
                inputs={"query": "a"},
            )
        self.assertEqual(self.http.bodies, [])
