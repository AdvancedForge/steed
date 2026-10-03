"""Reviewed create/send payloads, source/identity revocation and uncertain receipts."""

import copy
import json
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_recipe_proposals as fixtures
from PyQt6 import sip
from PyQt6.QtWidgets import QPlainTextEdit
from recipe_support import WriteRecipeHttp, write_recipe
from test_recipe_oauth import AUTH, OAuthHttp

from services.connector_recipes import RECIPE_WRITE_EXAMPLE, RecipeError, parse_recipe
from services.connectors import ConnectorProbe, ConnectorRequest, ConnectorReview
from services.recipe_connector import RecipeConnector
from services.recipe_oauth import connect_recipe_oauth
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.app_review import AppReviewDialog
from ui.recipe_review import RecipeReviewDialog


class RecipeWriteGrammarTests(unittest.TestCase):
    def test_version_two_preserves_reads_and_compiles_explicit_effects(self):
        recipe = write_recipe()
        self.assertEqual([t.effect for t in recipe.tools], ["read", "write", "send"])
        self.assertEqual(recipe.metadata()["version"], 2)
        self.assertTrue(recipe.metadata()["tools"][1]["requires_payload_review"])
        self.assertEqual(
            recipe.tools[2].destination("email:fixture@example.test"),
            "email:fixture@example.test",
        )

    def test_unsupported_effects_bindings_destinations_and_probe_paths_reject(self):
        cases = [
            {"effect": "delete"},
            {"effect": "spend"},
            {"method": "PUT"},
            {"destination": "email"},
            {"destination": "resource_id"},
            {"path": "/notes/{destination}"},
            {"request": {"body": "credential"}},
            {"request": {"body": {"source": "artifact.content"}}},
            {"request": {"bad\nkey": "artifact.content"}},
            {"request": {"title": "artifact.title"}},
            {"idempotency_header": "Authorization"},
            {"pagination": {"cursor": "/next", "parameter": "cursor", "max_pages": 2}},
            {
                "reconciliation": {
                    "path": "/operations/{destination}",
                    "operation_id": "/id",
                    "status": "/status",
                    "success": "done",
                }
            },
            {
                "reconciliation": {
                    "path": 3,
                    "operation_id": "/id",
                    "status": "/status",
                    "success": "done",
                }
            },
        ]
        for change in cases:
            source = copy.deepcopy(RECIPE_WRITE_EXAMPLE)
            source["tools"][1].update(change)
            with self.subTest(change=change), self.assertRaises(RecipeError):
                parse_recipe(json.dumps(source).encode())
        source = copy.deepcopy(RECIPE_WRITE_EXAMPLE)
        source["version"] = 1
        with self.assertRaises(RecipeError):
            parse_recipe(json.dumps(source).encode())

    def test_send_requires_payload_recipient_and_rejects_recipient_lists(self):
        source = copy.deepcopy(RECIPE_WRITE_EXAMPLE)
        del source["tools"][2]["request"]["to"]
        with self.assertRaises(RecipeError):
            parse_recipe(json.dumps(source).encode())
        for address in (
            "email:a@example.test,b@example.test",
            "email:Name <a@example.test>",
            "email:a@example.test\r\nBcc:other@example.test",
        ):
            with self.subTest(address=address), self.assertRaises(ValueError):
                write_recipe().tools[2].destination(address)


class RecipeWriteTests(unittest.TestCase):
    setUpClass = fixtures.RecipeProposalTests.__dict__["setUpClass"]
    tearDown = fixtures.RecipeProposalTests.tearDown
    wait_for = fixtures.RecipeProposalTests.wait_for
    call = fixtures.RecipeProposalTests.call
    upload = fixtures.RecipeProposalTests.upload

    def setUp(self):
        fixtures.RecipeProposalTests.setUp(self)
        self.http = WriteRecipeHttp()
        self.recipe = write_recipe()
        self.manager.install_recipe(self.recipe)
        self.manager.connect_recipe_account(
            self.recipe.provider,
            "SYNTHETIC_RECIPE_TOKEN",
            [t.name for t in self.recipe.tools],
        )
        self.wait_for(lambda: not self.manager.busy)
        self.assertFalse(self.manager.failure)
        self.account = self.accounts.list()[0]
        self.adapter = RecipeConnector(
            self.recipe,
            self.manager.recipes,
            self.account,
            self.accounts,
            http=self.http,
        )

    def task(self, actions=None, **scope):
        task = self.service._create("Synthetic recipe action")
        grant = self.service._request_grant(
            task.task_id, {"actions": actions or ["workspace", "connector"], **scope}
        )
        if grant.status == "requested":
            self.service.approve_grant(task.task_id, grant.grant_id)
        return self.service.client(task.task_id)

    def write_task(
        self, tool="create_note", destination="account", effect="write", *, probe=False
    ):
        permissions = [
            {
                "connector": self.account.connector,
                "tool": tool,
                "effect": effect,
                "destination": destination,
            }
        ]
        if probe:
            permissions.append({**permissions[0], "effect": "read"})
        client = self.task(["app", "artifact"], connector_permissions=permissions)
        artifact = self.call(
            "artifact.store",
            client=client,
            title='A "quoted" title',
            content='Draft with "quotes", <html> and\nnewlines',
            format="text",
        )
        args = {
            "connector": self.account.connector,
            "tool": tool,
            "destination": destination,
            "request_id": "write-1",
            "artifact_id": artifact.artifact_id,
        }
        self.call("app.call_tool", client=client, **args)
        self.wait_for(lambda: not self.service._connector_jobs)
        return client, args

    def review(self, client):
        return self.service.app_reviews.get(client.task_id, "write-1")

    def decide(self, client, approve=True):
        self.service.resolve_app_review(client.task_id, "write-1", approve=approve)
        self.wait_for(lambda: not self.service._connector_jobs)
        return self.call("app.status", client=client, request_id="write-1")

    def test_create_payload_popup_then_single_dispatch_and_replay_receipt(self):
        client, args = self.write_task(probe=True)
        review = self.review(client)
        self.assertEqual(self.http.payloads, [])
        preview = json.loads(review.before_text)
        self.assertEqual(preview["body"], review.after_text)
        self.assertEqual(preview["title"], 'A "quoted" title')
        dialog = AppReviewDialog(
            self.service, self.service.store.get(client.task_id), review
        )
        self.dialogs.append(dialog)
        self.assertTrue(dialog.deny_button.isDefault())
        self.assertEqual(dialog.windowTitle(), "Review App Action")
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-recipe-create-preview.png")
        dialog.approve_button.click()
        self.wait_for(lambda: not self.service._connector_jobs)
        result = self.call("app.status", client=client, request_id="write-1")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(self.http.payloads, [preview])
        self.assertEqual(self.call("app.call_tool", client=client, **args), result)
        self.assertEqual(len(self.http.payloads), 1)
        self.assertEqual(self.review(client).before_text, "")
        self.assertNotIn("app.resolve_review", self.service.broker._handlers)

    def test_send_binds_single_recipient_and_requires_its_own_default_deny_preview(
        self,
    ):
        client, _ = self.write_task(
            "send_message", "email:recipient@example.test", "send"
        )
        review = self.review(client)
        self.assertEqual(json.loads(review.before_text)["to"], "recipient@example.test")
        dialog = AppReviewDialog(
            self.service, self.service.store.get(client.task_id), review
        )
        self.dialogs.append(dialog)
        self.assertEqual(dialog.approve_button.text(), "Send This Message")
        self.assertTrue(dialog.deny_button.isDefault())
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-recipe-send-preview.png")
        dialog.deny_button.click()
        self.assertEqual(self.http.payloads, [])
        self.assertEqual(
            self.call("app.status", client=client, request_id="write-1").state,
            "cancelled",
        )

    def test_account_every_time_switch_still_requires_payload_review(self):
        self.manager.set_access(self.account.account_id, "write", True)
        client, _ = self.write_task()
        self.assertEqual(self.review(client).status, "pending")
        self.assertEqual(self.http.payloads, [])
        self.assertEqual(self.decide(client).state, "succeeded")

    def test_direct_entry_missing_or_forged_review_cannot_post(self):
        client, args = self.write_task()
        artifact = self.service.store.artifact(client.task_id, args["artifact_id"])
        receipt = self.service.connector_store.get(client.task_id, "write-1")
        request = ConnectorRequest(
            "write-1",
            self.account.connector,
            "create_note",
            "write",
            "account",
            artifact,
            receipt.operation_id,
        )
        tool = self.adapter.tools()[1]
        self.assertEqual(tool.execute(request, threading.Event()).state, "not_applied")
        forged = ConnectorReview("{}", artifact.content, self.review(client).context)
        self.assertEqual(
            tool.execute(replace(request, review=forged), threading.Event()).state,
            "not_applied",
        )
        self.assertEqual(self.http.payloads, [])

    def test_account_identity_changed_after_preview_rejects_before_post(self):
        client, _ = self.write_task()
        self.http.subject = "different-user"
        self.assertEqual(self.decide(client).state, "not_applied")
        self.assertEqual(self.http.payloads, [])

    def test_catalog_disabled_after_preview_rejects_before_post(self):
        client, _ = self.write_task()
        self.manager.recipes.disable(self.recipe.provider)
        self.assertEqual(self.decide(client).state, "not_applied")
        self.assertEqual(self.http.payloads, [])

    def test_account_disconnected_cancels_pending_review_and_never_posts(self):
        client, _ = self.write_task()
        self.service.disconnect_connector(self.account.connector)
        with self.assertRaises(ValueError):
            self.decide(client)
        self.assertEqual(self.http.payloads, [])

    def test_committed_timeout_is_uncertain_replay_never_posts_and_read_probe_confirms(
        self,
    ):
        client, args = self.write_task(probe=True)
        self.http.fail_after_commit = True
        result = self.decide(client)
        self.assertEqual(result.state, "uncertain")
        self.assertEqual(
            self.call("app.call_tool", client=client, **args).state, "uncertain"
        )
        self.assertEqual(len(self.http.payloads), 1)
        self.call("app.reconcile", client=client, request_id="write-1")
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(
            self.call("app.status", client=client, request_id="write-1").state,
            "succeeded",
        )
        self.assertEqual(len(self.http.payloads), 1)
        self.assertTrue(
            any("/operations/" in c[1] and c[0] == "GET" for c in self.http.calls)
        )

    def test_probe_requires_exact_operation_and_success_absence_never_proves_not_applied(
        self,
    ):
        tool = self.adapter.tools()[1]
        operation = "a" * 32
        probe = ConnectorProbe("write-1", "account", operation)
        for response in (
            {},
            {"operation_id": "b" * 32, "status": "succeeded", "id": "wrong"},
            {"operation_id": operation, "status": "pending", "id": "waiting"},
        ):
            self.http.operations[operation] = response
            self.assertEqual(
                tool.reconcile(probe, threading.Event()).state, "uncertain"
            )
        self.assertEqual(self.http.payloads, [])

    def test_revocation_after_post_entry_preserves_uncertainty(self):
        client, _ = self.write_task()
        self.http.gate = threading.Event()
        self.service.resolve_app_review(client.task_id, "write-1", approve=True)
        self.wait_for(self.http.entered.is_set)
        self.service.revoke_grant(
            client.task_id, self.service.store.grants(client.task_id)[-1].grant_id
        )
        self.http.gate.set()
        self.wait_for(lambda: not self.service._connector_jobs)
        receipt = self.service.connector_store.get(client.task_id, "write-1")
        self.assertEqual(receipt.state, "uncertain")
        self.assertEqual(len(self.http.payloads), 1)

    def test_source_popup_lists_actual_effects_bindings_and_operation_probe(self):
        dialog = RecipeReviewDialog(self.manager, self.recipe)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-recipe-writes-source-preview.png")
        self.assertTrue(dialog.deny_button.isDefault())
        self.assertIn(
            "POST /messages",
            dialog.findChildren(QPlainTextEdit)[0].toPlainText(),
        )

    def test_payload_overflow_is_cancelled_without_preview_or_post(self):
        client = self.task(
            ["app", "artifact"],
            connector_permissions=[
                {
                    "connector": self.account.connector,
                    "tool": "create_note",
                    "effect": "write",
                    "destination": "account",
                }
            ],
        )
        artifact = self.call(
            "artifact.store", client=client, title="Large draft", content="x" * 16384
        )
        self.call(
            "app.call_tool",
            client=client,
            connector=self.account.connector,
            tool="create_note",
            destination="account",
            request_id="large",
            artifact_id=artifact.artifact_id,
        )
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(
            self.call("app.status", client=client, request_id="large").state,
            "cancelled",
        )
        self.assertEqual(self.http.payloads, [])

    def test_foreign_draft_and_destination_scope_cannot_create_receipt(self):
        client, args = self.write_task()
        foreign = self.task(
            ["app", "artifact"],
            connector_permissions=[
                {
                    "connector": self.account.connector,
                    "tool": "create_note",
                    "effect": "write",
                    "destination": "account",
                }
            ],
        )
        with self.assertRaises(KeyError):
            self.call(
                "app.call_tool", client=foreign, **{**args, "request_id": "foreign"}
            )
        with self.assertRaises(TaskPermissionError):
            self.call(
                "app.call_tool",
                client=client,
                **{
                    **args,
                    "tool": "send_message",
                    "destination": "email:other@example.test",
                    "request_id": "wrong-scope",
                },
            )
        self.assertEqual(self.http.payloads, [])

    def test_restart_clears_pending_preview_without_posting_and_retains_uncertain_receipt(
        self,
    ):
        client, _ = self.write_task()
        pending = client.task_id
        second, _ = self.write_task()
        self.http.fail_after_commit = True
        self.assertEqual(self.decide(second).state, "uncertain")
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(
            self.root / "tasks.sqlite",
            connectors=self.manager.connectors,
            connections=self.manager,
        )
        self.manager.task_service = self.service
        self.assertEqual(
            self.service.app_reviews.get(pending, "write-1").before_text, ""
        )
        self.assertEqual(
            self.service.connector_store.get(pending, "write-1").state, "cancelled"
        )
        self.assertEqual(
            self.service.connector_store.get(second.task_id, "write-1").state,
            "uncertain",
        )
        self.assertEqual(len(self.http.payloads), 1)

    def test_oauth_refresh_and_payload_execution_keep_form_exchange_separate(self):
        source = copy.deepcopy(RECIPE_WRITE_EXAMPLE)
        source["authentication"] = dict(AUTH)
        selected = parse_recipe(json.dumps(source).encode())
        self.manager.install_recipe(selected)
        writes, oauth = self.http, OAuthHttp()

        class Http:
            def request(inner, method, url, **kwargs):
                if url == AUTH["token_endpoint"] or url.endswith("/me"):
                    return oauth.request(method, url, **kwargs)
                headers = dict(kwargs["headers"])
                self.assertEqual(headers["Authorization"], "Bearer " + oauth.access)
                # The synthetic write fixture has a fixed bearer token. Check the
                # actual refreshed token above before routing its private copy.
                headers["Authorization"] = "Bearer SYNTHETIC_RECIPE_TOKEN"
                return writes.request(method, url, **{**kwargs, "headers": headers})

        client_file = self.root / "oauth-client.json"
        client_file.write_text('{"client_id":"SYNTHETIC_CLIENT"}')
        http = Http()
        self.account = connect_recipe_oauth(
            selected,
            self.manager.recipes,
            self.accounts,
            client_file,
            [t.name for t in selected.tools],
            threading.Event(),
            http=http,
            authorize=lambda *args: (
                "SYNTHETIC_CODE",
                "SYNTHETIC_VERIFIER",
                "http://127.0.0.1:12345/callback",
            ),
        )
        self.adapter = RecipeConnector(
            selected, self.manager.recipes, self.account, self.accounts, http=http
        )
        for definition in self.adapter.tools():
            self.manager.connectors.register(definition)
        client, _ = self.write_task()
        self.assertEqual(self.http.payloads, [])
        self.assertEqual(len(oauth.posts), 2)
        self.assertEqual(self.decide(client).state, "succeeded")
        self.assertEqual(len(self.http.payloads), 1)
        self.assertEqual(len(oauth.posts), 2)
        self.assertNotIn(
            oauth.access,
            self.call("app.status", client=client, request_id="write-1").text,
        )

    def test_response_selection_failure_after_commit_stays_uncertain(self):
        client, _ = self.write_task()
        original = self.http.request

        def malformed(method, url, **kwargs):
            result = original(method, url, **kwargs)
            return {} if method == "POST" else result

        with patch.object(self.http, "request", side_effect=malformed):
            self.assertEqual(self.decide(client).state, "uncertain")
        self.assertEqual(len(self.http.payloads), 1)
