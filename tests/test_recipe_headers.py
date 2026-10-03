"""Reviewed non-secret API headers, exact wire behavior and source/receipt binding."""

import json
import unittest
from unittest.mock import patch

import test_recipe_http as transport
import test_recipe_writes as writes
from PyQt6.QtWidgets import QPlainTextEdit
from recipe_support import recipe_data

from services.connector_recipes import (
    RECIPE_HEADERS_EXAMPLE,
    RECIPE_NESTED_EXAMPLE,
    RecipeError,
    parse_recipe,
)
from services.recipe_connector import RecipeConnector
from services.recipe_http import RecipeHttp
from services.recipe_oauth import token_http
from ui.recipe_review import RecipeReviewDialog

HEADERS = {"Notion-Version": "2025-09-03"}
ALL_HEADERS = {
    **HEADERS,
    "X-GitHub-Api-Version": "2022-11-28",
    "Zotero-API-Version": "3",
    "OCS-APIRequest": "true",
}


def header_recipe():
    return parse_recipe(
        json.dumps(
            {**RECIPE_NESTED_EXAMPLE, "version": 5, "request_headers": HEADERS}
        ).encode()
    )


class RecipeHeaderGrammarTests(unittest.TestCase):
    def test_headers_are_immutable_reviewed_source_and_versions_one_to_four_remain_strict(
        self,
    ):
        data = {**recipe_data(), "version": 5, "request_headers": ALL_HEADERS.copy()}
        recipe = parse_recipe(json.dumps(data).encode())
        self.assertEqual(dict(recipe.request_headers), ALL_HEADERS)
        metadata = recipe.metadata()
        metadata["request_headers"]["Notion-Version"] = "2026-01-01"
        self.assertEqual(dict(recipe.request_headers), ALL_HEADERS)
        data["request_headers"]["Notion-Version"] = "2026-01-01"
        changed = parse_recipe(json.dumps(data).encode())
        self.assertNotEqual(recipe.sha256, changed.sha256)
        self.assertNotEqual(recipe.provider, changed.provider)
        for version in (1, 2, 3, 4):
            with self.subTest(version=version):
                data["version"] = version
                with self.assertRaises(RecipeError):
                    parse_recipe(json.dumps(data).encode())
                legacy = {
                    key: value
                    for key, value in data.items()
                    if key != "request_headers"
                }
                self.assertEqual(
                    parse_recipe(json.dumps(legacy).encode()).request_headers, ()
                )

    def test_secret_transport_noncanonical_and_unbounded_headers_are_rejected(self):
        cases = [None, [], {}, {**ALL_HEADERS, "Other-Version": "1"}]
        for name in (
            "Authorization",
            "Host",
            "Cookie",
            "Origin",
            "Content-Type",
            "Content-Length",
            "Transfer-Encoding",
            "Connection",
            "Accept",
            "X-Figma-Token",
            "Zotero-API-Key",
            "Idempotency-Key",
            "notion-version",
        ):
            cases.append({name: "SYNTHETIC_TOKEN"})
        for value in (
            "2025-02-30",
            "2025-9-3",
            "2025-09-03\r\nHost: foreign.example",
            "x" * 256,
            3,
            True,
        ):
            cases.append({"Notion-Version": value})
        for value in ("0", "100", "03", "3\n", "SYNTHETIC_TOKEN"):
            cases.append({"Zotero-API-Version": value})
        for value in ("false", "True", "true\n", True):
            cases.append({"OCS-APIRequest": value})
        for headers in cases:
            with self.subTest(headers=headers), self.assertRaises(RecipeError):
                parse_recipe(
                    json.dumps(
                        {**recipe_data(), "version": 5, "request_headers": headers}
                    ).encode()
                )
        for headers in (
            b'{"Notion-Version":"2025-09-03","Notion-Version":"2026-01-01"}',
            b'{"Notion-Version":"2025-09-03","notion-version":"2026-01-01"}',
        ):
            raw = json.dumps(
                {**recipe_data(), "version": 5, "request_headers": {}}
            ).encode()
            with self.assertRaises(RecipeError):
                parse_recipe(
                    raw.replace(
                        b'"request_headers": {}', b'"request_headers": ' + headers
                    )
                )

    def test_version_five_retains_nested_requests_and_sequential_plan_compilation(self):
        recipe = parse_recipe(json.dumps(RECIPE_HEADERS_EXAMPLE).encode())
        self.assertEqual(len(recipe.tools[0].steps), 2)
        self.assertEqual(
            recipe.tools[0].steps[1].path, "/notes/{step.create_note.id}/comments"
        )
        self.assertEqual(dict(recipe.request_headers), HEADERS)
        with self.assertRaises(RecipeError):
            parse_recipe(json.dumps({**recipe_data(), "version": 5}).encode())


class RecipeHeaderHttpTests(unittest.TestCase):
    setUpClass = transport.RecipeHttpTests.__dict__["setUpClass"]
    tearDownClass = transport.RecipeHttpTests.__dict__["tearDownClass"]
    setUp = transport.RecipeHttpTests.setUp
    close = transport.RecipeHttpTests.close
    request = transport.RecipeHttpTests.request

    def test_exact_reviewed_headers_are_sent_on_get_and_json_post(self):
        self.http = RecipeHttp(
            "https://api.fixture.example",
            request_headers=ALL_HEADERS,
            post_rules=[("/notes", "Idempotency-Key")],
        )
        base = {**ALL_HEADERS, "Authorization": "Bearer SYNTHETIC_TOKEN"}
        self.request(url=self.http.origin + "/me", headers=base)
        self.request(
            method="POST",
            url=self.http.origin + "/notes",
            headers={
                **base,
                "Content-Type": "application/json",
                "Idempotency-Key": "a" * 32,
            },
            body=b'{"body":"draft"}',
        )
        for index, (_, wire) in enumerate(self.calls):
            expected = {
                "Host",
                "Accept",
                "Accept-Encoding",
                "Authorization",
                *ALL_HEADERS,
            }
            if index:
                expected |= {"Content-Type", "Content-Length", "Idempotency-Key"}
            self.assertEqual(set(wire), expected)
            self.assertEqual({name: wire[name] for name in ALL_HEADERS}, ALL_HEADERS)
        self.assertEqual(self.forms, [b'{"body":"draft"}'])

    def test_missing_changed_or_unreviewed_headers_never_reach_dns(self):
        self.http = RecipeHttp(
            "https://api.fixture.example",
            request_headers=HEADERS,
            post_rules=[("/notes", "")],
        )
        base = {**HEADERS, "Authorization": "Bearer SYNTHETIC_TOKEN"}
        cases = [
            {"Authorization": base["Authorization"]},
            {**base, "Notion-Version": "2026-01-01"},
            {**base, "notion-version": HEADERS["Notion-Version"]},
            {**base, "Zotero-API-Version": "3"},
            {**base, "Host": "foreign.example"},
        ]
        with patch("services.recipe_http.socket.getaddrinfo") as resolve:
            for headers in cases:
                with self.subTest(headers=headers), self.assertRaises(ValueError):
                    self.http.request("GET", self.http.origin + "/me", headers=headers)
                with self.subTest(post=headers), self.assertRaises(ValueError):
                    self.http.request(
                        "POST",
                        self.http.origin + "/notes",
                        headers={**headers, "Content-Type": "application/json"},
                        body=b'{"body":"draft"}',
                    )
            resolve.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_oauth_exchange_never_receives_api_headers_and_cannot_add_them(self):
        endpoint = "https://auth.fixture.example/token"
        self.http = RecipeHttp(
            "https://api.fixture.example",
            token_endpoint=endpoint,
            request_headers=HEADERS,
        )
        form_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        body = b"grant_type=refresh_token&refresh_token=SYNTHETIC_REFRESH"
        self.request(method="POST", url=endpoint, headers=form_headers, body=body)
        self.assertEqual(
            set(self.calls[0][1]),
            {"Host", "Accept", "Accept-Encoding", "Content-Type", "Content-Length"},
        )
        with self.assertRaises(ValueError):
            self.http.request(
                "POST", endpoint, headers={**form_headers, **HEADERS}, body=body
            )
        self.assertEqual(len(self.calls), 1)

    def test_compiled_plan_transport_retains_headers_on_every_step(self):
        self.http = token_http(
            parse_recipe(json.dumps(RECIPE_HEADERS_EXAMPLE).encode())
        )
        base = {
            **HEADERS,
            "Authorization": "Bearer SYNTHETIC_TOKEN",
            "Content-Type": "application/json",
        }
        for path, extra in (
            ("/notes", {"Idempotency-Key": "a" * 32}),
            ("/notes/created-note/comments", {}),
        ):
            self.request(
                method="POST",
                url=self.http.origin + path,
                headers={**base, **extra},
                body=b'{"comment":"draft"}',
            )
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(
            all(
                wire["Notion-Version"] == HEADERS["Notion-Version"]
                for _, wire in self.calls
            )
        )


class RecipeHeaderJourneyTests(unittest.TestCase):
    setUpClass = writes.RecipeWriteTests.__dict__["setUpClass"]
    tearDown = writes.RecipeWriteTests.tearDown
    wait_for = writes.RecipeWriteTests.wait_for
    call = writes.RecipeWriteTests.call
    task = writes.RecipeWriteTests.task
    write_task = writes.RecipeWriteTests.write_task
    review = writes.RecipeWriteTests.review
    decide = writes.RecipeWriteTests.decide

    def setUp(self):
        with patch.object(writes, "write_recipe", side_effect=header_recipe):
            writes.RecipeWriteTests.setUp(self)

    def test_source_payload_identity_write_and_reconciliation_share_reviewed_headers(
        self,
    ):
        dialog = RecipeReviewDialog(self.manager, self.recipe)
        self.dialogs.append(dialog)
        source = dialog.findChildren(QPlainTextEdit)[0].toPlainText()
        self.assertIn("Notion-Version: 2025-09-03", source)
        self.assertIn("never OAuth token exchange", source)
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-recipe-headers-source-preview.png")
        client, args = self.write_task(probe=True)
        context = json.loads(self.review(client).context)
        self.assertEqual(context["request_headers"], HEADERS)
        self.assertEqual(context["source_sha256"], self.recipe.sha256)
        self.http.fail_after_commit = True
        self.assertEqual(self.decide(client).state, "uncertain")
        self.call("app.reconcile", client=client, request_id="write-1")
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(
            self.call("app.status", client=client, request_id="write-1").state,
            "succeeded",
        )
        self.assertEqual(
            self.call("app.call_tool", client=client, **args).state, "succeeded"
        )
        self.assertEqual(len(self.http.payloads), 1)
        self.assertTrue(any("/operations/" in c[1] for c in self.http.calls))
        self.assertTrue(
            all(
                headers["Notion-Version"] == HEADERS["Notion-Version"]
                for _, _, headers, _ in self.http.calls
            )
        )
        capabilities = self.call("recipe.capabilities", client=client)
        self.assertEqual(
            capabilities["request_headers"]["Notion-Version"], "YYYY-MM-DD"
        )
        self.assertEqual(
            parse_recipe(json.dumps(capabilities["headers_example"]).encode()).version,
            5,
        )

    def test_changed_header_source_cannot_use_previous_account_or_payload_review(self):
        client, _ = self.write_task()
        source = json.loads(self.recipe.raw)
        source["request_headers"]["Notion-Version"] = "2026-01-01"
        changed = parse_recipe(json.dumps(source).encode())
        self.manager.install_recipe(changed)
        count = len(self.http.calls)
        with self.assertRaises(ValueError):
            RecipeConnector(
                changed,
                self.manager.recipes,
                self.account,
                self.accounts,
                http=self.http,
            )
        self.assertEqual(len(self.http.calls), count)
        self.manager.recipes.disable(self.recipe.provider)
        self.assertEqual(self.decide(client).state, "not_applied")
        self.assertEqual(self.http.payloads, [])
        self.assertNotEqual(changed.provider, self.account.provider)
