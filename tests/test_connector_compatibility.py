"""Documented starter API shapes run through unchanged v1/v2 recipe execution."""

import hashlib
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from app_connector_support import MemorySecrets

from services.app_accounts import AccountStore
from services.connector_recipes import RecipeStore, parse_recipe
from services.connectors import ConnectorRequest
from services.recipe_connector import RecipeConnector, connect_recipe
from services.task_artifacts import TaskArtifact

ROOT = Path(__file__).resolve().parents[1] / "connectors" / "compatibility"
TOKEN = "SYNTHETIC_COMPATIBILITY_TOKEN"


class DocumentedWireFixture:
    """Only documented routes and payloads; an unknown request fails closed."""

    def __init__(self, provider):
        self.provider, self.calls, self.posts = provider, [], []
        self.responses = {
            "todoist": {
                "/api/v1/user": {"id": "1234567", "email": "fixture@example.test"},
                "/api/v1/tasks/fixture-item": {
                    "id": "fixture-item",
                    "content": "Task",
                    "description": "Details",
                },
                "/api/v1/tasks": {
                    "results": [{"id": "fixture-item", "content": "Task"}],
                    "next_cursor": None,
                },
            },
            "gitlab": {
                "/api/v4/user": {"id": 42, "username": "fixture-user"},
                "/api/v4/projects/fixture-item": {
                    "id": 7,
                    "name": "Fixture project",
                    "web_url": "https://gitlab.com/fixture/project",
                },
            },
            "github-owner": {
                "/user": {"id": 42, "login": "fixture-user", "email": None},
                "/repos/octocat/fixture-item": {
                    "id": 7,
                    "full_name": "octocat/fixture-item",
                    "html_url": "https://github.com/octocat/fixture-item",
                },
            },
            "microsoft-read": {
                "/v1.0/me": {
                    "id": "fixture-user-id",
                    "userPrincipalName": "fixture@example.test",
                    "mail": None,
                },
                "/v1.0/me/drive/items/fixture-item": {
                    "id": "fixture-item",
                    "name": "Fixture document",
                    "webUrl": "https://fixture.example/document",
                },
                "/v1.0/me/events": {
                    "value": [{"id": "fixture-event", "subject": "Fixture meeting"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/events?$skip=10",
                },
                "/v1.0/me/todo/lists": {
                    "value": [{"id": "fixture-list", "displayName": "Fixture list"}]
                },
            },
        }[provider]

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        assert not cancelled.is_set()
        assert headers["Authorization"] == "Bearer " + TOKEN
        path = urlsplit(url).path
        assert not urlsplit(url).query
        self.calls.append((method, url, headers, body))
        if method == "GET":
            assert body is None
            return self.responses[path]
        assert method == "POST" and headers["Content-Type"] == "application/json"
        payload = json.loads(body)
        expected = {
            "todoist": (
                "/api/v1/tasks",
                {
                    "content": "Fixture title",
                    "description": 'Fixture draft with "quotes"',
                },
            ),
            "gitlab": (
                "/api/v4/projects/fixture-item/issues",
                {
                    "title": "Fixture title",
                    "description": 'Fixture draft with "quotes"',
                },
            ),
            "github-owner": (
                "/repos/octocat/fixture-item/issues",
                {"title": "Fixture title", "body": 'Fixture draft with "quotes"'},
            ),
        }[self.provider]
        assert (path, payload) == expected
        self.posts.append(payload)
        return {
            "id": "fixture-created",
            "iid": 1,
            "number": 1,
            "content": "Fixture title",
            "web_url": "https://fixture.example/issue",
            "html_url": "https://fixture.example/issue",
        }


class StarterCompatibilityTests(unittest.TestCase):
    def fixture(self, name):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        recipe = parse_recipe((ROOT / (name + ".json")).read_bytes())
        catalog = RecipeStore(root / "catalog.json")
        catalog.install(recipe)
        accounts = AccountStore(root / "accounts.json", MemorySecrets())
        http, cancelled = DocumentedWireFixture(name), threading.Event()
        account = connect_recipe(
            recipe,
            catalog,
            accounts,
            TOKEN,
            [t.name for t in recipe.tools],
            cancelled,
            http=http,
        )
        return (
            recipe,
            RecipeConnector(recipe, catalog, account, accounts, http=http),
            http,
            cancelled,
        )

    def test_all_documented_read_subsets_use_current_engine_and_nullable_identity_labels(
        self,
    ):
        for name in ("todoist", "gitlab", "github-owner", "microsoft-read"):
            with self.subTest(name=name):
                recipe, adapter, http, cancelled = self.fixture(name)
                for tool, definition in zip(recipe.tools, adapter.tools(), strict=True):
                    if tool.effect != "read":
                        continue
                    destination = (
                        "fixture-item"
                        if tool.destination_type == "resource_id"
                        else "account"
                    )
                    result = definition.execute(
                        ConnectorRequest(
                            "read",
                            adapter.account.connector,
                            tool.name,
                            "read",
                            destination,
                            None,
                        ),
                        cancelled,
                    )
                    self.assertEqual(result.state, "succeeded")
                    self.assertNotIn(TOKEN, result.text)
                self.assertEqual(http.posts, [])

    def test_documented_create_subsets_materialize_exact_payload_after_review_only(
        self,
    ):
        content = 'Fixture draft with "quotes"'
        artifact = TaskArtifact(
            "fixture-artifact",
            "fixture-task",
            "Fixture title",
            "text",
            len(content),
            hashlib.sha256(content.encode()).hexdigest(),
            (),
            "fixture",
            content,
        )
        for name in ("todoist", "gitlab", "github-owner"):
            with self.subTest(name=name):
                recipe, adapter, http, cancelled = self.fixture(name)
                for tool, definition in zip(recipe.tools, adapter.tools(), strict=True):
                    if tool.effect == "read":
                        continue
                    destination = (
                        "fixture-item"
                        if tool.destination_type == "resource_id"
                        else "account"
                    )
                    request = ConnectorRequest(
                        "create",
                        adapter.account.connector,
                        tool.name,
                        "write",
                        destination,
                        artifact,
                        "a" * 32,
                    )
                    self.assertEqual(
                        definition.execute(request, cancelled).state, "not_applied"
                    )
                    review = definition.prepare_review(request, cancelled)
                    self.assertEqual(http.posts, [])
                    result = definition.execute(
                        replace(request, review=review), cancelled
                    )
                    self.assertEqual(result.state, "succeeded")
                    self.assertEqual(len(http.posts), 1)

    def test_github_owner_is_immutable_and_private_email_is_not_used_as_identity(self):
        recipe, adapter, _, _ = self.fixture("github-owner")
        self.assertEqual(recipe.label_pointer, "/login")
        self.assertEqual(adapter.account.label, "fixture-user")
        for tool in recipe.tools:
            self.assertIn("/octocat/", tool.path)
            with self.assertRaises(ValueError):
                tool.destination("other-owner/repository")

    def test_microsoft_collection_reads_are_first_page_and_never_follow_next_link(self):
        recipe, adapter, http, cancelled = self.fixture("microsoft-read")
        definition = next(
            t for t in adapter.tools() if t.tool == "list_events_first_page"
        )
        result = definition.execute(
            ConnectorRequest(
                "read",
                adapter.account.connector,
                definition.tool,
                "read",
                "account",
                None,
            ),
            cancelled,
        )
        self.assertEqual(
            json.loads(result.text),
            {"events": [{"id": "fixture-event", "subject": "Fixture meeting"}]},
        )
        self.assertFalse(any("?" in c[1] for c in http.calls))
        self.assertTrue(all(t.effect == "read" for t in recipe.tools))
