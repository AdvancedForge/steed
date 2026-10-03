"""Packaged starter connector profiles stay inside the documented wire contract.

Every profile here runs through the unchanged recipe interpreter with synthetic
accounts and committed synthetic responses. Nothing in this file installs a
service, connects a real account or contacts a provider.
"""

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app_connector_support import MemorySecrets

from services.app_accounts import AccountError, AccountStore
from services.app_oauth import ApiError
from services.bundled_recipes import BundledRecipes
from services.connector_recipes import RecipeError, RecipeStore, parse_recipe
from services.connectors import ConnectorRequest
from services.recipe_connector import RecipeConnector, connect_recipe
from services.task_artifacts import TaskArtifact

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = ROOT / "connectors" / "recipes"
FIXTURES = ROOT / "connectors" / "fixtures"
TOKEN = "SYNTHETIC_PROFILE_TOKEN"
TITLE = "Fixture title"
DRAFT = 'Reviewed draft with "quotes"'
# The packaged picker only offers regular JSON files whose names match this.
PACKAGED_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\.json")


def packaged_slugs():
    """Published packaged-selector name contract; templates are never listed."""

    return tuple(
        sorted(
            path.name
            for path in PACKAGED.iterdir()
            if path.is_file() and PACKAGED_NAME.fullmatch(path.name)
        )
    )


def packaged_recipe(slug):
    return parse_recipe((PACKAGED / (slug + ".json")).read_bytes())


def fixture_data(provider):
    """Committed synthetic responses; they mirror documented field names only."""

    return json.loads((FIXTURES / (provider + ".json")).read_text("utf-8"))


def draft_artifact(content=DRAFT, title=TITLE):
    return TaskArtifact(
        "profile-artifact",
        "profile-task",
        title,
        "text",
        len(content),
        hashlib.sha256(content.encode("utf-8")).hexdigest(),
        (),
        "fixture",
        content,
    )


class DocumentedApi:
    """One synthetic account per provider; unknown routes and methods fail closed."""

    def __init__(self, provider, data=None):
        self.provider = provider
        self.data = fixture_data(provider) if data is None else data
        self.calls = []
        self.posts = []
        self.pages = {}
        self.failure = None

    def fail(self, method, path, status):
        """Documented error status for one exact route, e.g. a 429 quota response."""

        self.failure = (method, path, status)
        return self

    def with_pages(self, path, pages):
        self.pages[path] = pages
        return self

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        assert not cancelled.is_set()
        assert headers["Authorization"] == "Bearer " + TOKEN
        parts = urlsplit(url)
        self.calls.append((method, url, dict(headers), body))
        failure = self.failure if self.failure else None
        if method == "GET":
            assert body is None
            assert set(headers) <= {"Authorization", "Accept"}
            if failure and failure[:2] == ("GET", parts.path):
                raise ApiError(failure[2])
            if parts.path in self.pages:
                cursor = parse_qs(parts.query).get("cursor", [""])[0]
                return copy.deepcopy(self.pages[parts.path][cursor])
            return copy.deepcopy(self.data["routes"][parts.path])
        assert method == "POST"
        assert set(headers) == {"Authorization", "Content-Type"}
        # The payload was sent before any documented error status came back.
        self.posts.append((parts.path, json.loads(body)))
        if failure and failure[:2] == ("POST", parts.path):
            raise ApiError(failure[2])
        return copy.deepcopy(self.data["created"])


class ProfileCase(unittest.TestCase):
    def install(self, recipe, provider, data=None):
        """Install reviewed source, connect one synthetic account, no writes."""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        catalog = RecipeStore(root / "catalog.json")
        catalog.install(recipe)
        accounts = AccountStore(root / "accounts.json", MemorySecrets())
        api = DocumentedApi(provider, data)
        cancelled = threading.Event()
        account = connect_recipe(
            recipe,
            catalog,
            accounts,
            TOKEN,
            [t.name for t in recipe.tools],
            cancelled,
            http=api,
        )
        self.assertEqual(api.posts, [])
        self.api = api
        self.cancelled = cancelled
        return RecipeConnector(recipe, catalog, account, accounts, http=api)

    def definition(self, adapter, name):
        return next(t for t in adapter.tools() if t.tool == name)

    def read(self, adapter, name, destination):
        return self.definition(adapter, name).execute(
            ConnectorRequest(
                "read", adapter.account.connector, name, "read", destination, None
            ),
            self.cancelled,
        )

    def reviewed_create(self, adapter, name, destination, artifact=None):
        request = ConnectorRequest(
            "create",
            adapter.account.connector,
            name,
            "write",
            destination,
            artifact or draft_artifact(),
            "a" * 32,
        )
        definition = self.definition(adapter, name)
        return request, definition, definition.prepare_review(request, self.cancelled)


class TodoistProfileTests(ProfileCase):
    def setUp(self):
        self.recipe = packaged_recipe("todoist")
        self.adapter = self.install(self.recipe, "todoist")

    def test_identity_uses_documented_id_and_email_only(self):
        self.assertEqual(self.recipe.identity_path, "/api/v1/user")
        self.assertEqual(self.recipe.subject_pointer, "/id")
        self.assertEqual(self.recipe.label_pointer, "/email")
        self.assertEqual(self.adapter.account.subject, "1234567")
        self.assertEqual(self.adapter.account.label, "fixture@example.test")
        # Todoist's own identity document echoes the personal token; only two
        # reviewed pointers are selected, so it cannot reach a task or result.
        self.assertEqual(self.api.data["identity"]["/api/v1/user"]["token"], TOKEN)
        self.assertNotIn(TOKEN, self.api.data["routes"]["/api/v1/user"]["id"])

    def test_selected_task_reads_use_the_exact_id_and_stay_token_free(self):
        result = self.read(self.adapter, "read_task", "6XGgmFVcrG5RRjVr")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(
            json.loads(result.text),
            {"id": "6XGgmFVcrG5RRjVr", "content": "Documented task"},
        )
        self.assertNotIn(TOKEN, result.text)
        self.assertEqual(
            self.api.calls[-1][1],
            "https://api.todoist.com/api/v1/tasks/6XGgmFVcrG5RRjVr",
        )
        self.assertEqual(self.api.posts, [])

    def test_omitted_optional_task_fields_do_not_break_a_minimal_profile(self):
        # The documented description is optional and absent here, so the packaged
        # profile must not require it.
        result = self.read(self.adapter, "read_task", "6XGgmFVcrG5RRjVrB")
        self.assertEqual(
            json.loads(result.text),
            {"id": "6XGgmFVcrG5RRjVrB", "content": "Task without a description"},
        )
        # A profile that selects the optional description fails closed for a task
        # that has none instead of inventing one.
        probe = parse_recipe(
            (ROOT / "connectors" / "compatibility" / "todoist.json").read_bytes()
        )
        self.assertIn(("description", "/description"), probe.tools[0].response)
        probe_adapter = self.install(probe, "todoist")
        # select() refuses to fill in a field the service did not send.
        with self.assertRaises((RecipeError, KeyError)):
            self.read(probe_adapter, "read_task", "6XGgmFVcrG5RRjVrB")

    def test_cursor_listing_is_bounded_to_three_pages(self):
        pages = {
            "": {"results": [{"id": "1", "content": "One"}], "next_cursor": "c1"},
            "c1": {"results": [{"id": "2", "content": "Two"}], "next_cursor": "c2"},
            "c2": {"results": [{"id": "3", "content": "Three"}], "next_cursor": "c3"},
        }
        self.api.with_pages("/api/v1/tasks", pages)
        result = self.read(self.adapter, "list_tasks", "account")
        selected = json.loads(result.text)
        self.assertEqual(
            [t["content"] for t in selected["tasks"]],
            ["One", "Two", "Three"],
        )
        self.assertEqual(selected["_pagination"], {"pages": 3, "more_available": True})
        self.assertEqual(
            [urlsplit(c[1]).query for c in self.api.calls if "?" in c[1]],
            ["cursor=c1", "cursor=c2"],
        )
        self.assertNotIn("cursor=c3", [c[1] for c in self.api.calls])

    def test_task_descriptions_read_separately_and_fail_closed_when_absent(self):
        result = self.read(self.adapter, "read_task_detail", "6XGgmFVcrG5RRjVr")
        self.assertEqual(
            json.loads(result.text),
            {
                "id": "6XGgmFVcrG5RRjVr",
                "content": "Documented task",
                "description": "Documented description",
            },
        )
        self.assertEqual(self.api.posts, [])
        # description is documented as optional, so the detail read must not
        # substitute a value for a task that documents none.
        with self.assertRaises((RecipeError, KeyError)):
            self.read(self.adapter, "read_task_detail", "6XGgmFVcrG5RRjVrB")

    def test_project_metadata_reads_the_shared_documented_fields_only(self):
        result = self.read(self.adapter, "read_project", "6XGgm6PHrGgMpCFX")
        self.assertEqual(
            json.loads(result.text),
            {
                "id": "6XGgm6PHrGgMpCFX",
                "name": "Inbox",
                "shared": False,
                "favorite": False,
            },
        )
        self.assertEqual(
            self.api.calls[-1][1],
            "https://api.todoist.com/api/v1/projects/6XGgm6PHrGgMpCFX",
        )
        self.assertEqual(self.api.posts, [])
        project = next(t for t in self.recipe.tools if t.name == "read_project")
        for value in ("220474322/6XGgm6PHrGgMpCFX", "../projects", "6XGgm6PHrGg?x=1"):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                project.destination(value)
        # A project name is not a project ID: the request is made, and the
        # service's own rejection is surfaced instead of another project.
        with self.assertRaises((RecipeError, KeyError, ApiError)):
            self.read(self.adapter, "read_project", "Inbox")

    def test_project_listing_is_bounded_and_never_creates_anything(self):
        pages = {
            "": {
                "results": [{"id": "1", "name": "One"}],
                "next_cursor": "p2",
            },
            "p2": {
                "results": [{"id": "2", "name": "Two"}],
                "next_cursor": "p3",
            },
            "p3": {"results": [{"id": "3", "name": "Three"}], "next_cursor": "p4"},
        }
        self.api.with_pages("/api/v1/projects", pages)
        selected = json.loads(self.read(self.adapter, "list_projects", "account").text)
        self.assertEqual(
            [p["name"] for p in selected["projects"]], ["One", "Two", "Three"]
        )
        self.assertEqual(selected["_pagination"], {"pages": 3, "more_available": True})
        self.assertEqual(
            [urlsplit(c[1]).query for c in self.api.calls if "?" in c[1]],
            ["cursor=p2", "cursor=p3"],
        )
        self.assertEqual(self.api.posts, [])

    def test_create_sends_the_exact_reviewed_draft_to_the_inbox(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_task", "account"
        )
        self.assertEqual(
            json.loads(review.before), {"content": TITLE, "description": DRAFT}
        )
        context = json.loads(review.context)
        self.assertEqual(context["url"], "https://api.todoist.com/api/v1/tasks")
        self.assertEqual(context["destination"], "account")
        self.assertEqual(
            context["payload_sha256"],
            hashlib.sha256(review.before.encode("utf-8")).hexdigest(),
        )
        # An unreviewed or altered payload is refused before any HTTP dispatch.
        self.assertEqual(
            definition.execute(request, self.cancelled).state, "not_applied"
        )
        self.assertEqual(
            definition.execute(
                replace(request, review=replace(review, before='{"content": "Other"}')),
                self.cancelled,
            ).state,
            "not_applied",
        )
        self.assertEqual(self.api.posts, [])
        result = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(
            self.api.posts,
            [("/api/v1/tasks", {"content": TITLE, "description": DRAFT})],
        )

    def test_quota_failure_after_dispatch_is_uncertain_and_never_replayed(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_task", "account"
        )
        self.api.fail("POST", "/api/v1/tasks", 429)
        outcome = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(outcome.state, "uncertain")
        self.assertIn("do not resend automatically", outcome.text)
        self.assertEqual(len(self.api.posts), 1)
        # Checking the receipt stays a manual step; Steed never resends for the user.
        self.api.failure = None
        self.assertEqual(
            self.read(self.adapter, "list_tasks", "account").state, "succeeded"
        )
        self.assertEqual(len(self.api.posts), 1)
        self.api.fail("GET", "/api/v1/tasks", 429)
        with self.assertRaises(ApiError):
            self.read(self.adapter, "list_tasks", "account")

    def test_revoked_token_stops_before_dispatch_on_writes_and_reads(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_task", "account"
        )
        self.api.fail("GET", "/api/v1/user", 401)
        outcome = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(outcome.state, "not_applied")
        self.assertIn("was not dispatched", outcome.text)
        self.assertEqual(self.api.posts, [])
        with self.assertRaises((ApiError, AccountError)):
            self.read(self.adapter, "list_tasks", "account")

    def test_destinations_cannot_escape_a_documented_route(self):
        task = self.recipe.tools[0]
        self.assertEqual(task.destination("6XGgmFVcrG5RRjVr"), "6XGgmFVcrG5RRjVr")
        for value in (
            "1/2",
            "../../me",
            "https://api.todoist.com/x",
            "?cursor=x",
            "a b",
        ):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                task.destination(value)
        create = self.recipe.tools[2]
        self.assertEqual(create.destination_type, "account")
        with self.assertRaises(RecipeError):
            create.destination("6XGgmFVcrG5RRjVr")


class GitHubOwnerTemplateTests(ProfileCase):
    OWNER = "REPLACE-WITH-YOUR-GITHUB-OWNER"

    def setUp(self):
        self.template = (
            PACKAGED / "templates" / "github-owner-template.json"
        ).read_bytes()
        self.assertIn(self.OWNER.encode(), self.template)
        # The documented user step: copy the template and fix one owner segment.
        self.recipe = parse_recipe(
            self.template.replace(self.OWNER.encode(), b"fixture-owner")
        )
        self.adapter = self.install(self.recipe, "github")

    def test_owner_template_is_not_offered_until_a_user_fixes_an_owner(self):
        self.assertNotIn("github-owner-template.json", packaged_slugs())
        unedited = parse_recipe(self.template)
        self.assertEqual([t.path.count(self.OWNER) for t in unedited.tools], [1, 1])
        # No packaged or template source ships a real account owner.
        for path in sorted(PACKAGED.rglob("*.json")):
            self.assertNotIn(b"octocat", path.read_bytes(), path.name)

    def test_nullable_private_email_is_never_the_account_label(self):
        self.assertEqual(self.recipe.identity_path, "/user")
        self.assertEqual(self.recipe.subject_pointer, "/id")
        self.assertEqual(self.recipe.label_pointer, "/login")
        self.assertIsNone(self.api.data["identity"]["/user"]["email"])
        self.assertEqual(self.adapter.account.subject, "42")
        self.assertEqual(self.adapter.account.label, "fixture-user")

    def test_repository_reads_stay_pinned_to_the_reviewed_owner(self):
        result = self.read(self.adapter, "read_repository", "fixture-repo")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(
            json.loads(result.text),
            {
                "id": 7,
                "name": "fixture-owner/fixture-repo",
                "url": "https://github.com/fixture-owner/fixture-repo",
            },
        )
        self.assertEqual(
            self.api.calls[-1][1],
            "https://api.github.com/repos/fixture-owner/fixture-repo",
        )
        self.assertEqual(self.api.posts, [])
        repository = self.recipe.tools[0]
        for value in (
            "other-owner/repo",
            "../../other-owner/repo",
            "https://github.com/other-owner/repo",
            "repo?state=open",
        ):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                repository.destination(value)

    def test_issue_create_sends_only_title_and_body_to_the_pinned_owner(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "fixture-repo"
        )
        self.assertEqual(json.loads(review.before), {"title": TITLE, "body": DRAFT})
        context = json.loads(review.context)
        self.assertEqual(
            context["url"],
            "https://api.github.com/repos/fixture-owner/fixture-repo/issues",
        )
        self.assertEqual(context["destination"], "fixture-repo")
        self.assertIsNone(context["operation_header"])
        self.assertEqual(
            context["payload_sha256"],
            hashlib.sha256(review.before.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            definition.execute(request, self.cancelled).state, "not_applied"
        )
        self.assertEqual(self.api.posts, [])
        result = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(
            json.loads(result.text),
            {
                "id": 9001,
                "number": 1,
                "url": "https://github.com/fixture-owner/fixture-repo/issues/1",
            },
        )
        self.assertEqual(
            self.api.posts,
            [
                (
                    "/repos/fixture-owner/fixture-repo/issues",
                    {"title": TITLE, "body": DRAFT},
                )
            ],
        )

    def test_rate_limit_after_dispatch_is_uncertain_without_replay(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "fixture-repo"
        )
        self.api.fail("POST", "/repos/fixture-owner/fixture-repo/issues", 403)
        outcome = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(outcome.state, "uncertain")
        self.assertIn("Check its receipt", outcome.text)
        self.assertEqual(len(self.api.posts), 1)
        self.api.failure = None
        self.assertEqual(
            self.read(self.adapter, "read_repository", "fixture-repo").state,
            "succeeded",
        )
        self.assertEqual(len(self.api.posts), 1)

    def test_hidden_repository_and_changed_identity_fail_closed(self):
        self.api.fail("GET", "/repos/fixture-owner/fixture-repo", 404)
        with self.assertRaises(ApiError):
            self.read(self.adapter, "read_repository", "fixture-repo")
        self.api.failure = None
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "fixture-repo"
        )
        self.api.data["routes"]["/user"]["login"] = "renamed-user"
        with self.assertRaises(AccountError):
            self.read(self.adapter, "read_repository", "fixture-repo")
        # A changed identity cannot even be reviewed, and an already reviewed
        # payload is refused before dispatch.
        with self.assertRaises(AccountError):
            self.reviewed_create(self.adapter, "create_issue", "fixture-repo")
        outcome = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(outcome.state, "not_applied")
        self.assertEqual(self.api.posts, [])


class GitLabProfileTests(ProfileCase):
    def setUp(self):
        self.recipe = packaged_recipe("gitlab")
        self.adapter = self.install(self.recipe, "gitlab")

    def test_identity_uses_the_documented_user_id_and_username(self):
        self.assertEqual(self.recipe.identity_path, "/api/v4/user")
        self.assertEqual(self.recipe.subject_pointer, "/id")
        self.assertEqual(self.recipe.label_pointer, "/username")
        self.assertEqual(self.adapter.account.subject, "42")
        self.assertEqual(self.adapter.account.label, "fixture-user")

    def test_project_reads_use_a_numeric_id_segment_only(self):
        result = self.read(self.adapter, "read_project", "7")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(
            json.loads(result.text),
            {
                "id": 7,
                "name": "fixture-owner/fixture-project",
                "url": "https://gitlab.com/fixture-owner/fixture-project",
            },
        )
        self.assertEqual(self.api.calls[-1][1], "https://gitlab.com/api/v4/projects/7")
        self.assertEqual(self.api.posts, [])
        project = self.recipe.tools[0]
        for value in (
            "fixture-owner/fixture-project",
            "../7",
            "7?private_token=x",
            "https://gitlab.com/api/v4/projects/7",
            "7#note",
        ):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                project.destination(value)

    def test_issue_create_sends_the_reviewed_draft_to_the_selected_project(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "7"
        )
        self.assertEqual(
            json.loads(review.before), {"title": TITLE, "description": DRAFT}
        )
        context = json.loads(review.context)
        self.assertEqual(context["url"], "https://gitlab.com/api/v4/projects/7/issues")
        self.assertEqual(context["destination"], "7")
        self.assertIsNone(context["operation_header"])
        self.assertEqual(
            definition.execute(request, self.cancelled).state, "not_applied"
        )
        self.assertEqual(self.api.posts, [])
        result = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(
            json.loads(result.text),
            {
                "id": 7001,
                "iid": 1,
                "url": "https://gitlab.com/fixture-owner/fixture-project/-/issues/1",
            },
        )
        self.assertEqual(
            self.api.posts,
            [("/api/v4/projects/7/issues", {"title": TITLE, "description": DRAFT})],
        )

    def test_quick_action_text_is_warned_about_and_sent_verbatim(self):
        draft = "/assign @fixture-user\n/confidential\nPlease triage this draft."
        title = "Fixture /close title"
        artifact = draft_artifact(draft, title)
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "7", artifact
        )
        # The tool description carries the warning a source review would show.
        self.assertIn("quick action", definition.description)
        self.assertIn("/confidential", definition.description)
        self.assertEqual(
            json.loads(review.before), {"title": title, "description": draft}
        )
        self.assertEqual(
            json.loads(review.context)["payload_sha256"],
            hashlib.sha256(review.before.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            definition.execute(replace(request, review=review), self.cancelled).state,
            "succeeded",
        )
        path, payload = self.api.posts[0]
        self.assertEqual(path, "/api/v4/projects/7/issues")
        # The reviewed draft is submitted verbatim: no command is stripped,
        # escaped or described as plain prose.
        self.assertEqual(payload, {"title": title, "description": draft})

    def test_no_operation_lookup_claims_to_prove_a_create(self):
        for tool in self.recipe.tools:
            self.assertIsNone(tool.reconciliation)
            self.assertEqual(tool.idempotency_header, "")
            self.assertIsNone(self.definition(self.adapter, tool.name).reconcile)
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "7"
        )
        self.api.fail("POST", "/api/v4/projects/7/issues", 429)
        outcome = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(outcome.state, "uncertain")
        self.assertIn("Check its receipt", outcome.text)
        self.assertEqual(len(self.api.posts), 1)
        # Nothing is replayed and nothing is reconciled automatically.
        self.api.failure = None
        self.assertEqual(
            self.read(self.adapter, "read_project", "7").state, "succeeded"
        )
        self.assertEqual(len(self.api.posts), 1)

    def test_incomplete_create_response_is_uncertain_rather_than_succeeded(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "7"
        )
        del self.api.data["created"]["web_url"]
        result = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(result.state, "uncertain")
        self.assertEqual(len(self.api.posts), 1)

    def test_missing_scope_and_unknown_project_fail_before_any_write(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "7"
        )
        self.api.fail("POST", "/api/v4/projects/7/issues", 403)
        self.assertEqual(
            definition.execute(replace(request, review=review), self.cancelled).state,
            "uncertain",
        )
        self.assertEqual(len(self.api.posts), 1)
        self.api.failure = ("GET", "/api/v4/projects/7", 404)
        with self.assertRaises(ApiError):
            self.read(self.adapter, "read_project", "7")
        self.assertEqual(len(self.api.posts), 1)


PACKAGED_SLUGS = {"gitlab.json", "todoist.json"}
TEMPLATE_NAMES = {"github-owner-template.json"}


class PackagedCatalogTests(unittest.TestCase):
    def test_only_reviewable_profiles_are_listed_by_the_picker(self):
        self.assertEqual(set(packaged_slugs()), PACKAGED_SLUGS)
        self.assertEqual(
            {p.name for p in (PACKAGED / "templates").glob("*.json")}, TEMPLATE_NAMES
        )
        # A template directory is never offered as an installable service.
        self.assertNotIn("templates", PACKAGED_SLUGS)

    def test_every_packaged_profile_parses_with_the_unchanged_engine(self):
        for slug in sorted(PACKAGED_SLUGS):
            with self.subTest(slug=slug):
                recipe = packaged_recipe(slug[:-5])
                self.assertLessEqual(recipe.version, 2)
                self.assertEqual(recipe.authentication.kind, "bearer")
                self.assertTrue(recipe.tools)
                for tool in recipe.tools:
                    self.assertTrue(tool.response)
                    self.assertEqual(
                        tool.destination_type in ("account", "resource_id", "email"),
                        True,
                    )


class AssessedCandidateQueueTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(
            (FIXTURES / "assessed_candidates.json").read_text("utf-8")
        )
        self.services = self.registry["services"]

    def test_each_documented_family_is_assessed_once_from_an_official_source(self):
        names = [entry["name"] for entry in self.services]
        self.assertEqual(len(names), 21)
        self.assertEqual(len(set(names)), 21)
        for entry in self.services:
            for key in (
                "family",
                "reference",
                "auth",
                "identity",
                "paging",
                "errors",
                "host",
                "status",
                "gate",
            ):
                with self.subTest(service=entry["name"], key=key):
                    self.assertIsInstance(entry[key], str)
                    self.assertTrue(entry[key].strip())
            self.assertTrue(entry["reference"].startswith("https://"))

    def test_shipped_profiles_match_the_assessment_and_gated_ones_are_absent(self):
        by_name = {entry["name"]: entry for entry in self.services}
        for name, label, host in (
            ("todoist", "Todoist", "api.todoist.com"),
            ("gitlab", "GitLab.com", "gitlab.com"),
        ):
            with self.subTest(service=name):
                entry = by_name[label]
                self.assertEqual(entry["status"], "packaged")
                self.assertIn("Bearer", entry["auth"])
                recipe = packaged_recipe(name)
                self.assertEqual(urlsplit(recipe.origin).hostname, host)
                self.assertEqual(urlsplit(recipe.origin).hostname, entry["host"])
        template = parse_recipe(
            (PACKAGED / "templates" / "github-owner-template.json").read_bytes()
        )
        self.assertEqual(by_name["GitHub"]["status"], "template")
        self.assertIn("Bearer", by_name["GitHub"]["auth"])
        self.assertEqual(urlsplit(template.origin).hostname, "api.github.com")
        shipped_origins = {"api.todoist.com", "gitlab.com", "api.github.com"}
        for entry in self.services:
            if entry["status"] in ("packaged", "template", "maintained"):
                continue
            with self.subTest(service=entry["name"]):
                self.assertEqual(entry["status"], "gated")
                # An assessed candidate is never quietly shipped as a profile.
                self.assertNotIn(entry["host"], shipped_origins)
                self.assertFalse((PACKAGED / (entry["name"] + ".json")).exists())
                self.assertFalse((FIXTURES / (entry["name"] + ".json")).exists())

    def test_only_bearer_services_and_public_hosts_can_become_profiles(self):
        for entry in self.services:
            if "Bearer" not in entry["auth"]:
                with self.subTest(service=entry["name"], auth=entry["auth"]):
                    self.assertIn(entry["status"], ("gated", "maintained"))
            if "localhost" in entry["host"] or "instance" in entry["host"]:
                with self.subTest(service=entry["name"], host=entry["host"]):
                    self.assertEqual(entry["status"], "gated")

    def test_every_gate_is_a_written_explanation(self):
        for entry in self.services:
            if entry["status"] != "gated":
                continue
            with self.subTest(service=entry["name"]):
                self.assertGreater(len(entry["gate"]), 60)
                self.assertTrue(entry["gate"].endswith("."))


class PackagedSelectorTests(unittest.TestCase):
    @unittest.skipUnless(
        hasattr(os, "O_NOFOLLOW"),
        "packaged reads use POSIX O_NOFOLLOW, which Windows Python lacks",
    )
    def test_picker_offers_only_installable_profiles_through_bundled_recipes(self):
        catalog = BundledRecipes()
        entries = catalog.list()
        self.assertEqual([entry.slug for entry in entries], ["gitlab", "todoist"])
        for entry in entries:
            with self.subTest(slug=entry.slug):
                self.assertEqual(catalog.select(entry).sha256, entry.recipe.sha256)
        # The owner template stays outside the picker on every platform.
        with self.assertRaises(OSError):
            catalog.read("github-owner-template")
