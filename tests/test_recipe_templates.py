"""Nested templates keep exact payload review, bounds and string-only binding."""

import copy
import json
import unittest
from unittest.mock import patch

import test_recipe_http as transport
import test_recipe_writes as writes
from recipe_support import TOKEN

from services.app_oauth import ApiError
from services.connector_recipes import RECIPE_NESTED_EXAMPLE, RecipeError, parse_recipe
from services.recipe_http import RecipeHttp
from services.recipe_templates import json_template


def nested_recipe():
    return parse_recipe(json.dumps(RECIPE_NESTED_EXAMPLE).encode())


class RecipeTemplateGrammarTests(unittest.TestCase):
    def test_immutable_compilation_and_explicit_bindings(self):
        recipe = nested_recipe()
        template = recipe.tools[0].request_template
        self.assertEqual(
            template.source(), RECIPE_NESTED_EXAMPLE["tools"][0]["request"]
        )
        self.assertEqual(
            template.bindings(),
            frozenset(("artifact.title", "artifact.content", "operation_id")),
        )
        self.assertEqual(recipe.metadata()["version"], 3)
        exported = recipe.metadata()["tools"][0]["request"]
        exported["data"]["published"] = True
        self.assertFalse(template.source()["data"]["published"])
        sources = {key: '{"injected":true}' for key in template.bindings()}
        self.assertEqual(
            template.materialize(sources)["data"]["body"]["text"],
            sources["artifact.content"],
        )
        self.assertEqual(
            json_template({"text": "artifact.content"}).bindings(), frozenset()
        )

    def test_literals_bounds_and_unknown_bindings(self):
        cases = [
            {"body": {"$bind": "credentials.access_token"}},
            {"body": {"$bind": "artifact.content", "extra": True}},
            {"body": {"$bind": []}},
            {"body": "artifact.content"},
            {"body": {"$bind": "artifact.title"}},
            {"body": {"$bind": "artifact.content"}, "bad\nkey": 3},
            {"body": {"$bind": "artifact.content"}, "literal": "\x00"},
            {"body": {"$bind": "artifact.content"}, "literal": float("inf")},
            {"body": {"$bind": "artifact.content"}, "literal": 2**53},
            {"body": {"$bind": "artifact.content"}, "items": [0] * 21},
            {"body": {"$bind": "artifact.content"}, "items": [[0] * 20] * 5},
        ]
        deep = {"$bind": "artifact.content"}
        for _ in range(9):
            deep = {"child": deep}
        cases.append(deep)
        for value in cases:
            source = copy.deepcopy(RECIPE_NESTED_EXAMPLE)
            source["tools"][0]["request"] = value
            with self.subTest(value=value), self.assertRaises(RecipeError):
                parse_recipe(json.dumps(source).encode())

    def test_nested_recipient_binding_is_required_for_send(self):
        source = copy.deepcopy(RECIPE_NESTED_EXAMPLE)
        tool = source["tools"][0]
        tool.update(effect="send", destination="email")
        tool["request"] = {
            "message": {"text": {"$bind": "artifact.content"}},
            "recipients": [{"address": {"$bind": "destination"}}],
        }
        selected = parse_recipe(json.dumps(source).encode())
        self.assertIn("destination", selected.tools[0].request_template.bindings())
        tool["request"]["recipients"] = [{"address": "someone@example.test"}]
        with self.assertRaises(RecipeError):
            parse_recipe(json.dumps(source).encode())


class RecipeTemplateTransportTests(unittest.TestCase):
    setUpClass = transport.RecipeHttpTests.__dict__["setUpClass"]
    tearDownClass = transport.RecipeHttpTests.__dict__["tearDownClass"]
    setUp = transport.RecipeHttpTests.setUp
    tearDown = transport.RecipeHttpTests.tearDown
    request = transport.RecipeHttpTests.request
    close = transport.RecipeHttpTests.close

    def test_nested_wire_payload_requires_a_reviewed_nested_post_rule(self):
        headers = {
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        }
        payload = b'{"data":{"body":"draft","tags":["tag"],"enabled":false,"count":1,"empty":null}}'
        self.http = RecipeHttp(
            "https://api.fixture.example", post_rules=[("/notes", "")]
        )
        with self.assertRaises(ValueError):
            self.request(
                method="POST",
                url=self.http.origin + "/notes",
                headers=headers,
                body=payload,
            )
        self.assertEqual(self.calls, [])
        self.http = RecipeHttp(
            "https://api.fixture.example", post_rules=[("/notes", "", True)]
        )
        self.request(
            method="POST",
            url=self.http.origin + "/notes",
            headers=headers,
            body=payload,
        )
        self.assertEqual(self.forms, [payload])
        for body in (
            b'{"data":{"body":"a","body":"b"}}',
            b'{"data":{"body":"draft","value":NaN}}',
            b'{"data":{"body":"draft","value":Infinity}}',
            b'{"data":{"body":"draft","value":"\\u0000"}}',
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.request(
                    method="POST",
                    url=self.http.origin + "/notes",
                    headers=headers,
                    body=body,
                )
        self.assertEqual(len(self.calls), 1)


class RecipeTemplateWriteTests(unittest.TestCase):
    setUpClass = writes.RecipeWriteTests.__dict__["setUpClass"]
    tearDown = writes.RecipeWriteTests.tearDown
    wait_for = writes.RecipeWriteTests.wait_for
    call = writes.RecipeWriteTests.call
    task = writes.RecipeWriteTests.task
    write_task = writes.RecipeWriteTests.write_task
    review = writes.RecipeWriteTests.review
    decide = writes.RecipeWriteTests.decide

    def setUp(self):
        with patch.object(writes, "write_recipe", side_effect=nested_recipe):
            writes.RecipeWriteTests.setUp(self)
        original = self.http.request

        def request(method, url, **kwargs):
            if method != "POST":
                return original(method, url, **kwargs)
            self.http.calls.append((method, url, kwargs["headers"], kwargs["body"]))
            payload = json.loads(kwargs["body"])
            self.http.payloads.append(payload)
            if self.http.fail_after_commit:
                raise ApiError()
            return {"id": "nested-created"}

        self.http.request = request

    def test_exact_nested_preview_is_the_only_posted_payload(self):
        source = writes.RecipeReviewDialog(self.manager, self.recipe)
        self.dialogs.append(source)
        source.show()
        self.app.processEvents()
        source.grab().save("/tmp/steed-nested-recipe-source-preview.png")
        self.assertIn(
            '"$bind": "artifact.content"',
            source.findChildren(writes.QPlainTextEdit)[0].toPlainText(),
        )
        client, args = self.write_task()
        review = self.review(client)
        payload = json.loads(review.before_text)
        self.assertEqual(payload["data"]["body"]["text"], review.after_text)
        self.assertFalse(payload["data"]["published"])
        self.assertEqual(self.http.payloads, [])
        dialog = writes.AppReviewDialog(
            self.service, self.service.store.get(client.task_id), review
        )
        self.dialogs.append(dialog)
        self.assertTrue(dialog.deny_button.isDefault())
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-nested-recipe-payload-preview.png")
        dialog.approve_button.click()
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(
            self.call("app.status", client=client, request_id="write-1").state,
            "succeeded",
        )
        self.assertEqual(self.http.payloads, [payload])
        self.assertEqual(
            self.call("app.call_tool", client=client, **args).state, "succeeded"
        )
        self.assertEqual(len(self.http.payloads), 1)
        capabilities = self.call("recipe.capabilities", client=client)
        self.assertEqual(capabilities["supported_versions"], [1, 2, 3, 4, 5])
        self.assertEqual(
            parse_recipe(json.dumps(capabilities["nested_example"]).encode()),
            self.recipe,
        )

    def test_lost_nested_reply_remains_uncertain_without_replay(self):
        client, args = self.write_task()
        self.http.fail_after_commit = True
        self.assertEqual(self.decide(client).state, "uncertain")
        self.assertEqual(
            self.call("app.call_tool", client=client, **args).state, "uncertain"
        )
        self.assertEqual(len(self.http.payloads), 1)

    def test_account_changed_after_nested_preview_prevents_post(self):
        client, _ = self.write_task()
        self.http.subject = "changed"
        self.assertEqual(self.decide(client).state, "not_applied")
        self.assertEqual(self.http.payloads, [])

    def test_denied_nested_review_never_posts(self):
        client, _ = self.write_task()
        self.assertEqual(self.decide(client, False).state, "cancelled")
        self.assertEqual(self.http.payloads, [])
