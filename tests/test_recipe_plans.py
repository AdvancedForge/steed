"""Result-dependent writes each require a child receipt and exact user review."""

import copy
import json
import threading
import unittest
from unittest.mock import patch

import test_recipe_http as transport
import test_recipe_writes as writes
from PyQt6 import sip

from services.app_oauth import ApiError
from services.connector_recipes import RECIPE_NESTED_EXAMPLE, RecipeError, parse_recipe
from services.recipe_oauth import token_http
from services.task_service import TaskService


def plan_data():
    data = copy.deepcopy(RECIPE_NESTED_EXAMPLE)
    data["version"] = 4
    first = data["tools"][0]
    first.pop("reconciliation")
    first["plan"] = [
        {
            "name": "append_note",
            "description": "Append the reviewed text to the newly created note",
            "method": "POST",
            "path": "/notes/{step.create_note.id}/comments",
            "request": {
                "note_id": {"$bind": "step.create_note.id"},
                "comment": {"$bind": "artifact.content"},
                "operation_id": {"$bind": "operation_id"},
            },
            "response": [{"label": "id", "pointer": "/id"}],
        }
    ]
    return data


def plan_recipe():
    return parse_recipe(json.dumps(plan_data()).encode())


class RecipePlanGrammarTests(unittest.TestCase):
    def test_plan_compiles_ordered_prior_output_bindings(self):
        compiled = plan_recipe()
        self.assertEqual(
            [step.name for step in compiled.tools[0].steps],
            ["create_note", "append_note"],
        )
        self.assertEqual(
            compiled.metadata()["tools"][0]["plan"][1]["path"],
            "/notes/{step.create_note.id}/comments",
        )

    def test_forward_unknown_duplicate_effect_and_path_escape_reject(self):
        cases = [
            {"name": "create_note"},
            {"method": "GET"},
            {"method": "DELETE"},
            {"effect": "delete"},
            {"destination": "account"},
            {"path": "https://foreign.example/notes"},
            {"path": "/notes/../{step.create_note.id}"},
            {"path": "/notes/prefix-{step.create_note.id}"},
            {"path": "/notes/{step.create_note.id}/{step.create_note.id}"},
            {"path": "/notes/{step.future.id}"},
            {"request": {"body": {"$bind": "step.future.id"}}},
            {"request": {"body": {"$bind": "step.create_note.id"}}},
            {
                "request": {
                    "body": {"$bind": "artifact.content"},
                    "secret": {"$bind": "credentials.token"},
                }
            },
        ]
        for change in cases:
            data = plan_data()
            data["tools"][0]["plan"][0].update(change)
            with self.subTest(change=change), self.assertRaises(RecipeError):
                parse_recipe(json.dumps(data).encode())


class RecipePlanTransportTests(unittest.TestCase):
    setUpClass = transport.RecipeHttpTests.__dict__["setUpClass"]
    tearDownClass = transport.RecipeHttpTests.__dict__["tearDownClass"]
    setUp = transport.RecipeHttpTests.setUp
    close = transport.RecipeHttpTests.close
    request = transport.RecipeHttpTests.request

    def test_result_segment_post_is_pinned_to_the_reviewed_plan_path(self):
        self.http = token_http(plan_recipe())
        headers = {
            "Authorization": "Bearer SYNTHETIC_TOKEN",
            "Content-Type": "application/json",
        }
        payload = (
            b'{"comment":"draft","operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
        )
        self.request(
            method="POST",
            url=self.http.origin + "/notes",
            headers={**headers, "Idempotency-Key": "a" * 32},
            body=b'{"data":{"body":{"text":"draft"},"operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}',
        )
        self.request(
            method="POST",
            url=self.http.origin + "/notes/created-note/comments",
            headers=headers,
            body=payload,
        )
        for path in (
            "/notes/../comments",
            "/notes/id/extra",
            "/foreign/id/comments",
            "/notes/https://foreign.example/comments",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.http.request(
                    "POST", self.http.origin + path, headers=headers, body=payload
                )
        self.assertEqual(len(self.calls), 2)
        for count in (0, 5):
            data = plan_data()
            data["tools"][0]["plan"] *= count
            with self.assertRaises(RecipeError):
                parse_recipe(json.dumps(data).encode())


class RecipePlanTests(unittest.TestCase):
    setUpClass = writes.RecipeWriteTests.__dict__["setUpClass"]
    tearDown = writes.RecipeWriteTests.tearDown
    wait_for = writes.RecipeWriteTests.wait_for
    call = writes.RecipeWriteTests.call
    task = writes.RecipeWriteTests.task
    write_task = writes.RecipeWriteTests.write_task

    def setUp(self):
        with patch.object(writes, "write_recipe", side_effect=plan_recipe):
            writes.RecipeWriteTests.setUp(self)
        self.fail_at = 0
        self.created_id = "created-note"
        original = self.http.request

        def request(method, url, **kwargs):
            if method != "POST":
                return original(method, url, **kwargs)
            self.http.calls.append((method, url, kwargs["headers"], kwargs["body"]))
            self.http.payloads.append(json.loads(kwargs["body"]))
            if self.http.gate:
                self.http.entered.set()
                self.http.gate.wait(5)
            if len(self.http.payloads) == self.fail_at:
                raise ApiError()
            return {"id": self.created_id if url.endswith("/notes") else "comment-id"}

        self.http.request = request

    def parent(self, client):
        return self.service.connector_store.get(client.task_id, "write-1")

    def review(self, client):
        return self.service.app_reviews.get(
            client.task_id, self.parent(client).reference
        )

    def decide(self, client, approve=True):
        self.service.resolve_app_review(
            client.task_id, self.review(client).request_id, approve=approve
        )
        self.wait_for(lambda: not self.service._connector_jobs)
        return self.parent(client)

    def test_two_reviews_materialize_previous_id_and_never_replay_parent(self):
        source = writes.RecipeReviewDialog(self.manager, self.recipe)
        self.dialogs.append(source)
        source.show()
        self.app.processEvents()
        source.grab().save("/tmp/steed-plan-source-preview.png")
        self.assertIn(
            "Sequential plan (separate review and receipt per POST)",
            source.findChildren(writes.QPlainTextEdit)[0].toPlainText(),
        )
        client, args = self.write_task()
        first = self.review(client)
        self.assertEqual(json.loads(first.context)["step"], 1)
        self.assertEqual(self.http.payloads, [])
        self.assertEqual(self.decide(client).state, "requested")
        second = self.review(client)
        self.assertNotEqual(first.request_id, second.request_id)
        details = json.loads(second.context)
        self.assertEqual(details["step"], 2)
        self.assertEqual(
            details["url"], "https://api.fixture.example/notes/created-note/comments"
        )
        self.assertEqual(json.loads(second.before_text)["note_id"], "created-note")
        dialog = writes.AppReviewDialog(
            self.service, self.service.store.get(client.task_id), second
        )
        self.dialogs.append(dialog)
        self.assertTrue(dialog.deny_button.isDefault())
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-plan-step-preview.png")
        dialog.approve_button.click()
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(self.parent(client).state, "succeeded")
        summary = json.loads(self.parent(client).text)["steps"]
        self.assertEqual(len(summary), 2)
        self.assertEqual(len({step["operation_id"] for step in summary}), 2)
        self.assertTrue(all(len(step["payload_sha256"]) == 64 for step in summary))
        self.assertEqual(
            self.call("app.call_tool", client=client, **args).state, "succeeded"
        )
        self.assertEqual(len(self.http.payloads), 2)
        self.assertEqual(
            self.service.app_reviews.get(client.task_id, first.request_id).before_text,
            "",
        )

    def test_uncertain_first_step_stops_before_second_and_stable_id_cannot_replay(self):
        client, args = self.write_task()
        self.fail_at = 1
        self.assertEqual(self.decide(client).state, "uncertain")
        self.assertEqual(len(self.http.payloads), 1)
        self.assertEqual(
            len(self.service.app_plans.children(client.task_id, "write-1")), 1
        )
        self.assertEqual(
            self.call("app.call_tool", client=client, **args).state, "uncertain"
        )
        self.assertEqual(len(self.http.payloads), 1)

    def test_second_denial_preserves_known_partial_completion(self):
        client, _ = self.write_task()
        self.decide(client)
        self.assertEqual(self.decide(client, False).state, "failed")
        self.assertEqual(len(self.http.payloads), 1)
        states = [
            item["state"] for item in json.loads(self.parent(client).text)["steps"]
        ]
        self.assertEqual(states, ["succeeded", "cancelled"])

    def test_clearing_parent_results_scrubs_children_and_stops_pending_step(self):
        client, _ = self.write_task()
        self.decide(client)
        second = self.review(client).request_id
        self.assertEqual(self.service.clear_app_results(client.task_id, ["write-1"]), 3)
        self.assertEqual(self.parent(client).state, "failed")
        self.assertEqual(self.parent(client).text, "")
        for row in self.service.app_plans.children(client.task_id, "write-1"):
            child = self.service.connector_store.get(
                client.task_id, row["child_request_id"]
            )
            self.assertEqual(child.text, "")
            self.assertTrue(child.result_cleared_at)
        self.assertEqual(
            self.service.app_reviews.get(client.task_id, second).before_text, ""
        )
        self.assertEqual(len(self.http.payloads), 1)

    def test_changed_review_cannot_dispatch_a_plan_step(self):
        client, _ = self.write_task()
        pending = self.review(client)
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE connector_reviews SET before_text='{}' WHERE task_id=? AND request_id=?",
                (client.task_id, pending.request_id),
            )
        with self.assertRaises(ValueError):
            self.decide(client)
        self.assertEqual(self.http.payloads, [])

    def test_pending_plans_reserve_receipts_for_later_steps(self):
        client, args = self.write_task()
        self.assertEqual(self.service.app_plans.reserved(client.task_id), 1)
        original = self.service.connector_store.list
        with patch.object(
            self.service.connector_store,
            "list",
            side_effect=lambda task_id: (
                [None] * 47 if task_id == client.task_id else original(task_id)
            ),
        ):
            with self.assertRaises(ValueError):
                self.call(
                    "app.call_tool",
                    client=client,
                    **{**args, "request_id": "exhausted"},
                )
        self.assertEqual(len(original(client.task_id)), 2)
        self.assertEqual(self.http.payloads, [])

    def test_timeout_stops_plan_and_late_reply_cannot_advance(self):
        client, _ = self.write_task()
        self.http.gate = threading.Event()
        self.service.resolve_app_review(
            client.task_id, self.review(client).request_id, approve=True
        )
        self.wait_for(self.http.entered.is_set)
        (task_id, child_id), job = next(iter(self.service._connector_jobs.items()))
        self.service._app_timeout(task_id, child_id, job)
        self.assertEqual(self.parent(client).state, "uncertain")
        self.http.gate.set()
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(self.parent(client).state, "uncertain")
        self.assertEqual(
            len(self.service.app_plans.children(client.task_id, "write-1")), 1
        )

    def test_lost_second_reply_keeps_both_receipts_and_no_third_dispatch(self):
        client, args = self.write_task()
        self.decide(client)
        self.fail_at = 2
        self.assertEqual(self.decide(client).state, "uncertain")
        self.assertEqual(
            [item["state"] for item in json.loads(self.parent(client).text)["steps"]],
            ["succeeded", "uncertain"],
        )
        self.assertEqual(
            self.call("app.call_tool", client=client, **args).state, "uncertain"
        )
        self.assertEqual(len(self.http.payloads), 2)

    def test_first_denial_and_parent_cancellation_never_post(self):
        client, _ = self.write_task()
        self.assertEqual(self.decide(client, False).state, "cancelled")
        other, _ = self.write_task()
        self.assertEqual(
            self.call("app.cancel", client=other, request_id="write-1").state,
            "cancelled",
        )
        self.assertEqual(self.http.payloads, [])

    def test_unsafe_or_nonstr_output_stops_after_first_without_followup(self):
        for value in ("../other", "https://foreign.example", ["id"], 123):
            self.created_id = value
            client, _ = self.write_task()
            self.assertEqual(self.decide(client).state, "failed")
            self.assertEqual(
                len(self.service.app_plans.children(client.task_id, "write-1")), 1
            )
        self.assertEqual(len(self.http.payloads), 4)

    def test_revocation_between_steps_cancels_review_and_preserves_completed_step(self):
        client, _ = self.write_task()
        self.decide(client)
        self.service.revoke_grant(
            client.task_id, self.service.store.grants(client.task_id)[-1].grant_id
        )
        self.assertEqual(self.parent(client).state, "failed")
        self.assertEqual(len(self.http.payloads), 1)

    def test_cancel_during_post_keeps_parent_uncertain_and_does_not_advance(self):
        client, _ = self.write_task()
        self.http.gate = threading.Event()
        self.service.resolve_app_review(
            client.task_id, self.review(client).request_id, approve=True
        )
        self.wait_for(self.http.entered.is_set)
        self.assertEqual(
            self.call("app.cancel", client=client, request_id="write-1").state,
            "uncertain",
        )
        self.http.gate.set()
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(self.parent(client).state, "uncertain")
        self.assertEqual(len(self.http.payloads), 1)

    def test_restart_retains_partial_receipts_and_does_not_resume_second_step(self):
        client, _ = self.write_task()
        self.decide(client)
        pending = self.review(client).request_id
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(
            self.root / "tasks.sqlite",
            connectors=self.manager.connectors,
            connections=self.manager,
        )
        self.manager.task_service = self.service
        self.assertEqual(self.parent(client).state, "failed")
        self.assertEqual(
            self.service.app_reviews.get(client.task_id, pending).before_text, ""
        )
        self.assertEqual(len(self.http.payloads), 1)

    def test_source_disabled_after_second_review_blocks_its_post(self):
        client, _ = self.write_task()
        self.decide(client)
        self.manager.recipes.disable(self.recipe.provider)
        self.assertEqual(self.decide(client).state, "failed")
        self.assertEqual(len(self.http.payloads), 1)
