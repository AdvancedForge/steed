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
from services.recipe_http import RecipeHttp
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


def credential_values(node, key=""):
    """Yield every (key, value) pair in a fixture, for credential-shaped checks."""

    if isinstance(node, dict):
        for name, value in node.items():
            yield from credential_values(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from credential_values(value, key)
    else:
        yield key, node


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
    REPOSITORY = "REPLACE-WITH-YOUR-GITHUB-REPOSITORY"

    def setUp(self):
        self.template = (
            PACKAGED / "templates" / "github-owner-template.json"
        ).read_bytes()
        # The documented user step: copy the template and fix the owner and the
        # repository, so the reviewed source names exactly one repository.
        self.recipe = parse_recipe(
            self.template.replace(self.OWNER.encode(), b"fixture-owner").replace(
                self.REPOSITORY.encode(), b"fixture-repo"
            )
        )
        self.adapter = self.install(self.recipe, "github")

    def test_template_is_not_offered_until_a_user_fixes_owner_and_repository(self):
        self.assertNotIn("github-owner-template.json", packaged_slugs())
        unedited = parse_recipe(self.template)
        for tool in unedited.tools:
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.path.count(self.OWNER), 1)
                self.assertEqual(tool.path.count(self.REPOSITORY), 1)
        # Only an issue number is selected at call time.
        self.assertEqual(
            [t.name for t in unedited.tools if "{destination}" in t.path],
            ["read_issue"],
        )
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

    def test_repository_reads_stay_pinned_to_the_reviewed_source(self):
        result = self.read(self.adapter, "read_repository", "account")
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
        # No repository, owner or query can be chosen at call time.
        repository = self.recipe.tools[0]
        self.assertEqual(repository.destination_type, "account")
        for value in ("fixture-repo", "other-owner/repo", "repo?state=open"):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                repository.destination(value)

    def test_issue_reads_return_documented_nulls_explicitly(self):
        empty = self.read(self.adapter, "read_issue", "1")
        self.assertEqual(
            json.loads(empty.text),
            {
                "number": 1,
                "title": "Documented issue with no body",
                "state": "open",
                "state_reason": None,
                "body": None,
                "url": "https://github.com/fixture-owner/fixture-repo/issues/1",
            },
        )
        self.assertIn('"body": null', empty.text)
        self.assertNotIn(TOKEN, empty.text)
        self.assertEqual(
            self.api.calls[-1][1],
            "https://api.github.com/repos/fixture-owner/fixture-repo/issues/1",
        )
        filled = json.loads(self.read(self.adapter, "read_issue", "2").text)
        self.assertEqual(filled["state"], "closed")
        self.assertEqual(filled["state_reason"], "completed")
        self.assertEqual(filled["body"], 'Documented issue body with "quotes"')
        self.assertEqual(self.api.posts, [])
        issue = next(t for t in self.recipe.tools if t.name == "read_issue")
        for value in ("1/comments", "../../issues/1", "1?state=all", "https://x/1"):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                issue.destination(value)

    def test_a_pull_request_projects_to_the_same_fields_and_no_type_is_claimed(self):
        pull = json.loads(self.read(self.adapter, "read_issue", "3").text)
        self.assertEqual(
            pull,
            {
                "number": 3,
                "title": "Documented pull request returned by the issues route",
                "state": "open",
                "state_reason": None,
                "body": None,
                "url": "https://github.com/fixture-owner/fixture-repo/pull/3",
            },
        )
        # The documented pull_request discriminator is deliberately not selected,
        # so the projection cannot claim which kind of object it is.
        self.assertNotIn("pull_request", pull)
        self.assertIn(
            "pull request", self.definition(self.adapter, "read_issue").description
        )
        self.assertIn(
            "does not establish the object type",
            self.definition(self.adapter, "read_issue").description,
        )
        self.assertEqual(self.api.posts, [])

    def test_documented_issue_statuses_fail_without_redirects_or_guessing(self):
        route = "/repos/fixture-owner/fixture-repo/issues/1"
        for status in (301, 304, 404, 410):
            with self.subTest(status=status):
                self.api.fail("GET", route, status)
                with self.assertRaises(ApiError):
                    self.read(self.adapter, "read_issue", "1")
                self.api.failure = None
        self.assertEqual(self.api.posts, [])
        # An issue number that does not exist is the service's answer, not ours.
        with self.assertRaises((RecipeError, KeyError, ApiError)):
            self.read(self.adapter, "read_issue", "404")

    def test_issue_create_sends_only_title_and_body_to_the_pinned_repository(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "account"
        )
        self.assertEqual(json.loads(review.before), {"title": TITLE, "body": DRAFT})
        context = json.loads(review.context)
        self.assertEqual(
            context["url"],
            "https://api.github.com/repos/fixture-owner/fixture-repo/issues",
        )
        self.assertEqual(context["destination"], "account")
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
            self.adapter, "create_issue", "account"
        )
        self.api.fail("POST", "/repos/fixture-owner/fixture-repo/issues", 403)
        outcome = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(outcome.state, "uncertain")
        self.assertIn("Check its receipt", outcome.text)
        self.assertEqual(len(self.api.posts), 1)
        self.api.failure = None
        self.assertEqual(self.read(self.adapter, "read_issue", "1").state, "succeeded")
        self.assertEqual(len(self.api.posts), 1)

    def test_hidden_repository_and_changed_identity_fail_closed(self):
        self.api.fail("GET", "/repos/fixture-owner/fixture-repo", 404)
        with self.assertRaises(ApiError):
            self.read(self.adapter, "read_repository", "account")
        self.api.failure = None
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "account"
        )
        self.api.data["routes"]["/user"]["login"] = "renamed-user"
        with self.assertRaises(AccountError):
            self.read(self.adapter, "read_repository", "account")
        # A changed identity cannot even be reviewed, and an already reviewed
        # payload is refused before dispatch.
        with self.assertRaises(AccountError):
            self.reviewed_create(self.adapter, "create_issue", "account")
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
        # GitLab documents quick actions as commands that start a line of a
        # description or comment; a slash inside a title proves nothing.
        draft = "/assign @fixture-user\n/confidential\nPlease triage this draft."
        title = "Fixture title"
        artifact = draft_artifact(draft, title)
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "7", artifact
        )
        # The tool description carries the warning a source review would show.
        self.assertIn("quick action", definition.description)
        self.assertIn("/confidential", definition.description)
        self.assertIn("description or comment", definition.description)
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
        # escaped or described as plain prose, and GitLab decides what a
        # description line means.
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


PACKAGED_SLUGS = {"asana.json", "gitlab.json", "raindrop.json", "todoist.json"}
TEMPLATE_NAMES = {"github-owner-template.json", "gitlab-project-template.json"}


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
            ("asana", "Asana", "app.asana.com"),
            ("raindrop", "Raindrop.io", "api.raindrop.io"),
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
        shipped_origins = {
            "api.todoist.com",
            "gitlab.com",
            "api.github.com",
            "app.asana.com",
            "api.raindrop.io",
        }
        for entry in self.services:
            if entry["status"] in ("packaged", "template", "maintained"):
                continue
            with self.subTest(service=entry["name"]):
                self.assertEqual(entry["status"], "gated")
                # An assessed candidate is never quietly shipped as a profile.
                self.assertNotIn(entry["host"], shipped_origins)
                self.assertFalse((PACKAGED / (entry["name"] + ".json")).exists())
                contract = FIXTURES / (entry["name"].split()[0].lower() + ".json")
                if contract.exists():
                    # An assessment contract is reference data, never a profile.
                    data = json.loads(contract.read_text("utf-8"))
                    self.assertEqual(data["status"], "gated")
                    self.assertTrue(data.get("blockers") or data.get("framework_gap"))

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


class SharedCapabilityGroupTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(
            (FIXTURES / "shared_capabilities.json").read_text("utf-8")
        )
        self.groups = self.registry["groups"]
        self.rows = json.loads(
            (FIXTURES / "assessed_candidates.json").read_text("utf-8")
        )["services"]
        self.names = {row["name"] for row in self.rows}

    def test_each_group_states_the_limit_it_removes_and_who_needs_it(self):
        ids = [group["id"] for group in self.groups]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 5)
        for group in self.groups:
            with self.subTest(group=group["id"]):
                self.assertTrue(group["title"])
                self.assertTrue(group["engine_limit"].endswith("."))
                self.assertTrue(group["services"])
                self.assertTrue(group["unblocks"])
                # Every named service must exist in the assessment queue.
                for name in group["services"]:
                    self.assertIn(name, self.names)
                self.assertTrue(group["requirements"])
                for requirement in group["requirements"]:
                    self.assertIn(requirement["service"], group["services"])
                    self.assertTrue(requirement["documented"].endswith("."))
                    self.assertTrue(requirement["reference"].startswith("https://"))

    def test_every_group_carries_a_minimal_fixture_contract(self):
        for group in self.groups:
            contract = group["fixture_contract"]
            with self.subTest(group=group["id"]):
                self.assertTrue(contract["description"].endswith("."))
                self.assertTrue(contract["samples"])
                for sample in contract["samples"]:
                    self.assertIn(sample["service"], self.names)
                    self.assertTrue(sample["proves"])

    def test_assessed_rows_and_groups_agree_on_the_needed_capabilities(self):
        grouped = {}
        for group in self.groups:
            for name in group["services"]:
                grouped.setdefault(name, set()).add(group["id"])
        for row in self.rows:
            with self.subTest(service=row["name"]):
                self.assertEqual(
                    set(row["capabilities"]), grouped.get(row["name"], set())
                )
                if row["status"] == "gated":
                    self.assertTrue(row["capabilities"], row["name"])

    def test_setup_guide_documents_every_group(self):
        guide = (ROOT / "connectors" / "SETUP.md").read_text("utf-8")
        for group in self.groups:
            with self.subTest(group=group["id"]):
                self.assertTrue(
                    group["title"] in guide,
                    group["title"] + " is missing from SETUP.md",
                )


class AssessedContractTests(unittest.TestCase):
    """Reference contracts for gated services: documented, versioned, secret-free."""

    def contract(self, provider):
        return json.loads((FIXTURES / (provider + ".json")).read_text("utf-8"))

    def test_notion_assessment_row_matches_the_contract(self):
        row = next(
            entry
            for entry in json.loads(
                (FIXTURES / "assessed_candidates.json").read_text("utf-8")
            )["services"]
            if entry["name"] == "Notion"
        )
        self.assertEqual(row["status"], "gated")
        self.assertIn("/v1/users/me", row["identity"])
        self.assertIn("null", row["identity"])
        self.assertIn("Notion-Version", row["gate"])
        self.assertIn("static_headers", row["capabilities"])

    def test_no_fixture_or_header_example_carries_a_credential(self):
        # Documentation may name a scheme and a fixture may echo the shape of a
        # token field, but a credential-shaped value must always be an obvious
        # placeholder or a prose description of the documented scheme.
        prefixed = ("ntn_", "figd_", "ghp_", "github_pat_", "glpat-", "secret_")
        shaped = re.compile(r"authorization|token|secret|api[-_]?key|password", re.I)
        for path in sorted(FIXTURES.glob("*.json")):
            body = path.read_text("utf-8").lower()
            for marker in prefixed:
                with self.subTest(fixture=path.name, marker=marker):
                    self.assertNotIn(marker, body, path.name + " names a token prefix")
            for key, value in credential_values(json.loads(path.read_text("utf-8"))):
                if not shaped.search(key) or not isinstance(value, str):
                    continue
                with self.subTest(fixture=path.name, key=key):
                    self.assertTrue(
                        "<" in value or "synthetic" in value.lower() or " " in value,
                        path.name + " carries a credential value under " + key,
                    )
        shared = json.loads((FIXTURES / "shared_capabilities.json").read_text("utf-8"))
        credentials = ("authorization", "x-figma-token", "zotero-api-key")
        for group in shared["groups"]:
            for requirement in group["requirements"]:
                example = requirement.get("header_example")
                if example is None:
                    continue
                for header, value in example.items():
                    with self.subTest(group=group["id"], header=header):
                        self.assertNotIn("secret", value.lower())
                        if header.lower() in credentials:
                            self.assertIn("<", value)


class EmpiricalEvidenceTests(unittest.TestCase):
    """Documentation, framework tests and a real account prove different things."""

    def setUp(self):
        self.shared = json.loads(
            (FIXTURES / "shared_capabilities.json").read_text("utf-8")
        )
        self.groups = {group["id"]: group for group in self.shared["groups"]}
        self.contracts = {
            provider: json.loads((FIXTURES / (provider + ".json")).read_text("utf-8"))
            for provider in ("notion", "zotero", "figma")
        }

    def test_the_evidence_policy_separates_docs_tests_and_a_real_account(self):
        policy = self.shared["evidence_policy"]
        self.assertTrue(policy["documentation_proves"])
        self.assertTrue(policy["framework_tests_prove"])
        self.assertTrue(policy["only_a_real_account_proves"])
        joined = " ".join(policy["not_evidence"]).lower()
        # A mock or fixture is never recorded as provider evidence.
        self.assertIn("not live provider evidence", joined)
        self.assertIn(
            "never recorded as acceptance", policy["how_pending_checks_are_kept"]
        )
        self.assertTrue(policy["how_pending_checks_are_kept"])

    def test_every_contract_keeps_its_unproven_questions_pending(self):
        for provider, contract in self.contracts.items():
            status = contract["empirical_status"]
            with self.subTest(provider=provider):
                self.assertTrue(status["settled_by_documentation"])
                self.assertTrue(status["pending"])
                self.assertIn(
                    "stay pending until a real account is used", status["policy"]
                )
                for item in status["pending"]:
                    self.assertTrue(item["question"].endswith("?"))
                    self.assertEqual(item["status"], "pending", item["question"])
                    self.assertTrue(item["settled_by"].endswith("."))
                    self.assertTrue(item["blocks"])

    def test_credential_and_response_claims_are_never_marked_settled_by_tests(self):
        for provider, contract in self.contracts.items():
            questions = " ".join(
                item["question"].lower()
                for item in contract["empirical_status"]["pending"]
            )
            with self.subTest(provider=provider):
                # A test can never settle whether a provider accepts a token or
                # sends a field, so those must still be pending questions.
                self.assertTrue(
                    "personal access token" in questions
                    or "internal integration token" in questions
                    or "bearer" in questions,
                    provider + " must keep its credential question pending",
                )

    def test_figma_does_not_claim_the_document_tree_read_is_small(self):
        figma = self.contracts["figma"]
        read = figma["read_contract"]
        self.assertEqual(read["smallest_useful_read"]["path"], "/v1/files/{key}/meta")
        self.assertIn(
            "no document tree",
            read["smallest_useful_read"]["why_this_endpoint"],
        )
        tree = read["document_tree_read"]
        self.assertEqual(tree["path"], "/v1/files/{key}")
        self.assertEqual(tree["transport_bound"]["status"], "pending_empirical")
        self.assertIn("128 KiB", tree["transport_bound"]["engine_fact"])
        self.assertIn(
            "publishes no response-size limit",
            tree["transport_bound"]["documented_provider_fact"],
        )
        self.assertIn("query_inputs", figma["framework_gap"]["capabilities"])
        self.assertTrue(
            any(
                "transport bound" in item["question"]
                for item in figma["empirical_status"]["pending"]
            )
        )

    def test_zotero_records_a_pinned_library_alternative_with_disclosure(self):
        zotero = self.contracts["zotero"]
        alternative = zotero["pinned_library_id_alternative"]
        self.assertEqual(alternative["status"], "assessed_alternative")
        self.assertTrue(alternative["what_it_still_needs"])
        # The pinned id is user-supplied and the interpreter cannot check it.
        disclosure = alternative["mismatch_disclosure"]
        self.assertIn("user-supplied and unchecked", disclosure["required_disclosure"])
        self.assertIn("does not verify", disclosure["engine_fact"])
        self.assertIn("404", disclosure["documented_statuses"])
        # The key's library access flag has to be disclosed too.
        self.assertIn("access", alternative["scope_disclosure"]["documented"])
        # One sign-in stays the default goal, and nothing is shipped.
        self.assertIn("not as a shipped template", alternative["note"])
        self.assertFalse(
            (PACKAGED / "templates" / "zotero-library-template.json").exists()
        )

    def test_query_inputs_are_a_separate_capability_from_pagination_and_headers(self):
        group = self.groups["query_inputs"]
        self.assertIn("Figma", group["services"])
        self.assertIn("request-shaping capability", group["distinct_from"])
        self.assertNotIn(group["id"], self.groups["pagination"]["id"])
        self.assertNotIn(group["id"], self.groups["static_headers"]["id"])
        self.assertNotIn(group["id"], self.groups["credential_headers"]["id"])
        figma = next(r for r in group["requirements"] if r["service"] == "Figma")
        self.assertEqual(figma["blocks"], ["single_item_read"])
        self.assertIn("depth", figma["documented"])
        for requirement in group["requirements"]:
            self.assertIn("blocks", requirement)
            self.assertTrue(requirement["documented"].endswith("."))
            self.assertTrue(requirement["reference"].startswith("https://"))

    def test_setup_guide_states_the_evidence_split_and_both_corrections(self):
        guide = (ROOT / "connectors" / "SETUP.md").read_text("utf-8")
        self.assertIn("What each kind of evidence proves", guide)
        self.assertIn("proves Steed's own behaviour only", guide)
        self.assertIn("projecting less is not the same as transferring less", guide)
        self.assertIn("pinned library id is a real alternative", guide)
        self.assertIn(self.groups["query_inputs"]["title"], guide)


class GitLabProjectTemplateTests(ProfileCase):
    """A per-project template, because a project ID and an issue IID are both fixed."""

    PLACEHOLDER = "REPLACE-WITH-YOUR-GITLAB-PROJECT-ID"

    def setUp(self):
        self.template = (
            PACKAGED / "templates" / "gitlab-project-template.json"
        ).read_bytes()
        self.assertIn(self.PLACEHOLDER.encode(), self.template)
        # The documented user step: copy the template and fix one project ID.
        self.recipe = parse_recipe(
            self.template.replace(self.PLACEHOLDER.encode(), b"7")
        )
        self.adapter = self.install(self.recipe, "gitlab")

    def test_template_stays_out_of_the_picker_until_a_project_is_fixed(self):
        self.assertNotIn("gitlab-project-template.json", packaged_slugs())
        unedited = parse_recipe(self.template)
        for tool in unedited.tools:
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.path.count(self.PLACEHOLDER), 1)
        self.assertEqual(
            [t.name for t in unedited.tools if "{destination}" in t.path],
            ["read_issue"],
        )
        # No source ships a real account, owner or project.
        for path in sorted(PACKAGED.rglob("*.json")):
            body = path.read_bytes()
            self.assertNotIn(b"octocat", body, path.name)
            self.assertNotIn(b"fixture-owner", body, path.name)

    def test_issue_read_uses_the_per_project_iid_and_returns_nulls_explicitly(self):
        result = self.read(self.adapter, "read_issue", "3")
        self.assertEqual(
            json.loads(result.text),
            {
                "iid": 3,
                "title": "Documented issue with a body",
                "state": "opened",
                "description": "Documented issue body",
                "url": "https://gitlab.com/fixture-owner/fixture-project/-/issues/3",
            },
        )
        self.assertEqual(
            self.api.calls[-1][1],
            "https://gitlab.com/api/v4/projects/7/issues/3",
        )
        empty = json.loads(self.read(self.adapter, "read_issue", "4").text)
        self.assertIsNone(empty["description"])
        self.assertIn(
            '"description": null', self.read(self.adapter, "read_issue", "4").text
        )
        self.assertEqual(self.api.posts, [])

    def test_only_fields_documented_for_every_tier_are_projected(self):
        issue = next(t for t in self.recipe.tools if t.name == "read_issue")
        self.assertEqual(
            [label for label, _ in issue.response],
            ["iid", "title", "state", "description", "url"],
        )
        # Documented as tier-conditional or not documented at all, so a Free tier
        # response cannot break the read.
        for pointer in ("/weight", "/epic", "/health_status", "/assignee", "/links"):
            self.assertNotIn(pointer, [p for _, p in issue.response])

    def test_issue_destinations_reject_anything_but_one_iid(self):
        issue = self.recipe.tools[1]
        self.assertEqual(issue.destination("3"), "3")
        for value in ("3/notes", "../3", "7/issues/3", "3?scope=all", "https://x/3"):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                issue.destination(value)
        # A global issue ID is not valid on this route; only the IID is documented.
        self.assertEqual(
            self.definition(self.adapter, "read_issue").description.count("IID"), 1
        )

    def test_missing_or_invisible_issue_fails_closed_without_a_write(self):
        self.api.fail("GET", "/api/v4/projects/7/issues/3", 404)
        with self.assertRaises(ApiError):
            self.read(self.adapter, "read_issue", "3")
        self.assertEqual(self.api.posts, [])
        with self.assertRaises((RecipeError, KeyError, ApiError)):
            self.read(self.adapter, "read_issue", "9999")

    def test_created_issue_can_be_confirmed_by_reading_its_returned_iid(self):
        request, definition, review = self.reviewed_create(
            self.adapter, "create_issue", "account"
        )
        result = definition.execute(replace(request, review=review), self.cancelled)
        self.assertEqual(result.state, "succeeded")
        created = json.loads(result.text)
        self.assertEqual(created["iid"], 1)
        self.assertEqual(
            self.api.posts,
            [("/api/v4/projects/7/issues", {"title": TITLE, "description": DRAFT})],
        )
        # The documented confirm read exists and is usable by hand...
        self.api.data["routes"]["/api/v4/projects/7/issues/1"] = dict(
            self.api.data["routes"]["/api/v4/projects/7/issues/3"],
            iid=1,
            title=TITLE,
            description=DRAFT,
        )
        confirmed = json.loads(self.read(self.adapter, "read_issue", "1").text)
        self.assertEqual(confirmed["title"], TITLE)
        self.assertEqual(confirmed["description"], DRAFT)
        # ...and Steed still never runs it on its own.
        self.assertEqual(len(self.api.posts), 1)
        self.assertIsNone(self.definition(self.adapter, "create_issue").reconcile)


CONTRACT_ORIGINS = {
    "Notion": "https://api.notion.com",
    "Zotero": "https://api.zotero.org",
    "Figma": "https://api.figma.com",
}


def contract_case_recipe(case):
    """Build the recipe a case describes, from documented fields only."""

    request = case["request"]
    identity = case["identity"]
    data = {
        "version": 2,
        "name": "Contract case " + case["id"],
        "origin": CONTRACT_ORIGINS[case["provider"]],
        "authentication": {"type": "bearer"},
        "identity": {
            "path": identity["request_path"],
            "subject": identity["subject"],
            "label": identity["label"],
        },
        "tools": [
            {
                "name": "contract_read",
                "description": "Documented case read " + case["id"],
                "method": "GET",
                "path": request["path"],
                "response": case["read"]["response"],
            }
        ],
    }
    return parse_recipe(json.dumps(data).encode("utf-8"))


class CaseTransport:
    """Serves one case's documented identity and read responses."""

    def __init__(self, case):
        self.case = case
        self.requests = []
        self.identity = case["identity"]["response"]
        self.read = case.get("documented_response")
        self.failure = case.get("failure")

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        assert method == "GET" and body is None
        self.requests.append((method, url, dict(headers)))
        path = urlsplit(url).path
        if path == self.case["identity"]["request_path"]:
            return copy.deepcopy(self.identity)
        if self.failure is not None:
            raise ApiError(self.failure["status"])
        return copy.deepcopy(self.read)


class ExecutableContractCaseTests(unittest.TestCase):
    """Each case states what already passes and the smallest missing behaviour."""

    def setUp(self):
        self.registry = json.loads(
            (FIXTURES / "executable_cases.json").read_text("utf-8")
        )
        self.cases = self.registry["cases"]

    def case(self, name):
        return next(c for c in self.cases if c["id"] == name)

    def test_every_case_is_documented_and_states_both_halves_of_its_gap(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIn(case["provider"], CONTRACT_ORIGINS)
                self.assertIn(case["kind"], self.registry["kinds"])
                self.assertEqual(case["request"]["method"], "GET")
                self.assertTrue(case["request"]["path"].startswith("/"))
                self.assertTrue(case["currently_passes"].endswith("."))
                self.assertTrue(case["smallest_missing_behavior"].endswith("."))
                self.assertTrue(case["expected"]["outcome"])
                if case["kind"] == "dispatch":
                    self.assertIn("request_path", case["identity"])
                    self.assertIn("destination", case["read"])
                    self.assertTrue(case["identity"]["subject"].startswith("/"))
                    self.assertTrue(case["identity"]["label"].startswith("/"))

    def test_recipes_generated_from_cases_parse_with_the_unchanged_engine(self):
        for case in self.cases:
            if case["kind"] != "dispatch":
                continue
            with self.subTest(case=case["id"]):
                recipe = contract_case_recipe(case)
                self.assertEqual(recipe.origin, CONTRACT_ORIGINS[case["provider"]])
                self.assertEqual(recipe.tools[0].name, "contract_read")
                # No credential ever reaches a generated recipe.
                self.assertNotIn("token", json.dumps(recipe.metadata()).lower())

    def test_dispatch_cases_prove_documented_reads_through_the_adapter(self):
        for case in self.cases:
            if case["kind"] != "dispatch":
                continue
            expected = case["expected"]
            with self.subTest(case=case["id"]):
                recipe = contract_case_recipe(case)
                transport = CaseTransport(case)
                if expected["outcome"] == "refused_before_install":
                    with self.assertRaises(AccountError) as caught:
                        self.connect(recipe, transport)
                    self.assertIn(expected["message_fragment"], str(caught.exception))
                    # The identity request was sent and nothing else was.
                    self.assertEqual(
                        [urlsplit(c[1]).path for c in transport.requests],
                        [case["identity"]["request_path"]],
                    )
                    continue
                adapter = self.connect(recipe, transport)
                definition = adapter.tools()[0]
                request = ConnectorRequest(
                    "read",
                    adapter.account.connector,
                    "contract_read",
                    "read",
                    case["read"]["destination"],
                    None,
                )
                if expected["outcome"] == "service_failure_not_guessed":
                    with self.assertRaises(ApiError):
                        definition.execute(request, self.cancelled)
                    self.assertEqual(transport.requests[-1][0], "GET")
                    continue
                result = definition.execute(request, self.cancelled)
                self.assertEqual(result.state, expected["outcome"])
                self.assertEqual(json.loads(result.text), expected["projected"])
                # The reviewed path is used verbatim and nothing else is called.
                self.assertEqual(
                    transport.requests[-1][1],
                    CONTRACT_ORIGINS[case["provider"]]
                    + recipe.tools[0].path.replace(
                        "{destination}", case["read"]["destination"]
                    ),
                )
                self.assertNotIn(TOKEN, result.text)

    def connect(self, recipe, transport):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        catalog = RecipeStore(root / "catalog.json")
        catalog.install(recipe)
        accounts = AccountStore(root / "accounts.json", MemorySecrets())
        self.cancelled = threading.Event()
        account = connect_recipe(
            recipe,
            catalog,
            accounts,
            TOKEN,
            ["contract_read"],
            self.cancelled,
            http=transport,
        )
        return RecipeConnector(recipe, catalog, account, accounts, http=transport)

    def test_transport_cases_prove_the_header_and_query_gates_refuse_early(self):
        for name in ("figma_credential_token_header", "notion_static_version_header"):
            case = self.case(name)
            with self.subTest(case=name):
                http = RecipeHttp(CONTRACT_ORIGINS[case["provider"]])
                with self.assertRaises(ValueError) as caught:
                    http.request(
                        "GET",
                        CONTRACT_ORIGINS[case["provider"]] + case["request"]["path"],
                        headers=case["request"]["headers"],
                        cancelled=threading.Event(),
                    )
                self.assertIn(
                    case["expected"]["message_fragment"], str(caught.exception)
                )
                self.assertEqual(case["expected"]["outcome"], "refused_before_dispatch")

    def test_an_unreviewed_query_parameter_is_refused_before_dispatch(self):
        documented = self.case("figma_document_read_body_bound")["request"]["query"]
        self.assertEqual(documented, {"depth": "1"})
        http = RecipeHttp(CONTRACT_ORIGINS["Figma"])
        with self.assertRaises(ValueError) as caught:
            http.request(
                "GET",
                CONTRACT_ORIGINS["Figma"] + "/v1/files/fixture-file-key?depth=1",
                headers={"Authorization": "Bearer " + TOKEN},
                cancelled=threading.Event(),
            )
        self.assertIn("pagination parameter", str(caught.exception))

    def test_documented_bounds_stay_recorded_as_pending_not_executable_here(self):
        for name in (
            "figma_document_read_body_bound",
            "figma_oversized_success_is_a_failure",
        ):
            case = self.case(name)
            with self.subTest(case=name):
                bound = case["engine_bound"]["body_bytes"]
                self.assertEqual(bound, 128 * 1024)
                self.assertGreater(case["synthetic_body_bytes"], bound)
                self.assertIn("services/recipe_http.py", case["engine_bound"]["source"])
        local = self.case("figma_document_read_body_bound")
        self.assertEqual(local["expected"]["outcome"], "not_executable_locally")
        self.assertIn("public HTTPS domain", local["expected"]["reason"])
        # An oversized success must remain a failure, not a partial read.
        oversized = self.case("figma_oversized_success_is_a_failure")
        self.assertEqual(oversized["expected"]["outcome"], "must_remain_a_failure")
        self.assertEqual(oversized["expected"]["status"], 200)
        self.assertIn("None", oversized["smallest_missing_behavior"])


class AsanaProfileTests(ProfileCase):
    """C2 audit: a personal-token single-resource read, proved through the adapter."""

    def setUp(self):
        self.recipe = packaged_recipe("asana")
        self.adapter = self.install(self.recipe, "asana")

    def test_identity_uses_the_documented_me_route_and_data_envelope(self):
        self.assertEqual(self.recipe.identity_path, "/api/1.0/users/me")
        self.assertEqual(self.recipe.subject_pointer, "/data/gid")
        self.assertEqual(self.recipe.label_pointer, "/data/name")
        self.assertEqual(self.adapter.account.subject, "12345")
        self.assertEqual(self.adapter.account.label, "Fixture User")
        # email is opt-in through field selection, so it is never the label.
        self.assertNotEqual(self.recipe.label_pointer, "/data/email")

    def test_project_read_projects_documented_fields_and_explicit_nulls(self):
        result = self.read(self.adapter, "read_project", "1331")
        self.assertEqual(result.state, "succeeded")
        selected = json.loads(result.text)
        self.assertEqual(selected["gid"], "1331")
        self.assertEqual(selected["name"], "Fixture project")
        self.assertEqual(selected["due_on"], "2019-09-15")
        self.assertEqual(selected["owner"]["name"], "Fixture User")
        self.assertEqual(selected["url"], "https://app.asana.com/0/12345/1331/list")
        empty = json.loads(self.read(self.adapter, "read_project", "1332").text)
        self.assertIsNone(empty["due_on"])
        self.assertIsNone(empty["owner"])
        self.assertEqual(self.api.posts, [])
        self.assertEqual(
            self.api.calls[-1][1], "https://app.asana.com/api/1.0/projects/1332"
        )

    def test_task_read_returns_documented_nullable_fields_as_null(self):
        selected = json.loads(self.read(self.adapter, "read_task", "12345").text)
        self.assertEqual(selected["gid"], "12345")
        self.assertFalse(selected["completed"])
        self.assertIsNone(selected["due_on"])
        self.assertEqual(selected["assignee"]["name"], "Fixture User")
        self.assertEqual(
            [label for label, _ in self.recipe.tools[1].response],
            ["gid", "name", "notes", "completed", "due_on", "assignee", "url"],
        )
        self.assertNotIn(TOKEN, json.dumps(selected))

    def test_gid_destinations_reject_paths_queries_and_spaces(self):
        for tool in self.recipe.tools:
            for value in (
                "1331/notes",
                "../1331",
                "1331?opt_fields=name",
                "a b",
                "x/y",
            ):
                with (
                    self.subTest(tool=tool.name, value=value),
                    self.assertRaises(RecipeError),
                ):
                    tool.destination(value)
            self.assertEqual(tool.destination("1331"), "1331")

    def test_documented_failures_surface_and_never_create_anything(self):
        for status in (401, 403, 404, 429):
            with self.subTest(status=status):
                self.api.fail("GET", "/api/1.0/projects/1331", status)
                with self.assertRaises(ApiError):
                    self.read(self.adapter, "read_project", "1331")
                self.api.failure = None
        self.assertEqual(self.api.posts, [])

    def test_no_write_is_offered_because_a_create_needs_a_body_destination(self):
        self.assertEqual([t.effect for t in self.recipe.tools], ["read", "read"])
        for tool in self.recipe.tools:
            self.assertFalse(tool.request)
            self.assertFalse(tool.request_template)
            self.assertEqual(tool.idempotency_header, "")
            self.assertIsNone(tool.reconciliation)
        # Asana documents creating a task with a workspace or project named in
        # the body, which no reviewed path can carry, so nothing claims to do it.
        self.assertEqual(len(self.recipe.tools), 2)


class RaindropProfileTests(ProfileCase):
    """C2 audit: a bookmark service whose collection reads need no new capability."""

    def setUp(self):
        self.recipe = packaged_recipe("raindrop")
        self.adapter = self.install(self.recipe, "raindrop")

    def test_identity_uses_the_documented_user_document(self):
        self.assertEqual(self.recipe.origin, "https://api.raindrop.io")
        self.assertEqual(self.recipe.identity_path, "/rest/v1/user")
        self.assertEqual(self.recipe.subject_pointer, "/user/_id")
        self.assertEqual(self.recipe.label_pointer, "/user/fullName")
        self.assertEqual(self.adapter.account.subject, "32")
        self.assertEqual(self.adapter.account.label, "Fixture User")
        self.assertEqual(self.recipe.version, 2)

    def test_collection_read_projects_only_documented_fields(self):
        result = self.read(self.adapter, "read_collection", "8492393")
        self.assertEqual(
            json.loads(result.text),
            {"id": 8492393, "title": "Development", "count": 16, "view": "list"},
        )
        self.assertEqual(
            self.api.calls[-1][1],
            "https://api.raindrop.io/rest/v1/collections/8492393",
        )
        self.assertEqual(self.api.posts, [])
        collection = self.recipe.tools[0]
        for value in ("8492393/children", "../8492393", "8492393?page=1"):
            with self.subTest(value=value), self.assertRaises(RecipeError):
                collection.destination(value)

    def test_root_collection_listing_is_one_documented_response(self):
        selected = json.loads(
            self.read(self.adapter, "list_root_collections", "account").text
        )
        self.assertEqual(
            [c["title"] for c in selected["collections"]], ["Development", "Reading"]
        )
        self.assertEqual(self.api.posts, [])
        # Paging is documented as page and perpage, so no cursor is declared.
        self.assertIsNone(self.recipe.tools[1].pagination)

    def test_an_application_level_failure_fails_closed_instead_of_looking_empty(self):
        self.api.data["routes"]["/rest/v1/collections"] = {
            "result": False,
            "error": "view",
            "errorMessage": "Collection validation failed: view: `bla` is not a valid enum value.",
        }
        with self.assertRaises((RecipeError, KeyError, ApiError)):
            self.read(self.adapter, "list_root_collections", "account")
        self.assertEqual(self.api.posts, [])
        self.assertEqual(
            self.api.data["routes"]["/rest/v1/collections"]["result"], False
        )

    def test_no_write_is_offered_because_a_bookmark_needs_a_link_input(self):
        self.assertEqual([t.effect for t in self.recipe.tools], ["read", "read"])
        self.assertEqual(self.recipe.tools[0].destination_type, "resource_id")
        self.assertEqual(self.recipe.tools[1].destination_type, "account")
        for tool in self.recipe.tools:
            self.assertFalse(tool.request)
            self.assertFalse(tool.pagination)
