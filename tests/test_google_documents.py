"""Exact-tab Docs reads/appends, revision conflicts and read-only reconciliation."""

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app_connector_support import (
    MemorySecrets,
    SyntheticGoogle,
    install_account,
    synthetic_document_tab,
)
from test_google_connector import draft

from services.app_accounts import AccountError, AccountStore
from services.app_oauth import ApiError
from services.connectors import ConnectorProbe, ConnectorRequest
from services.google_connector import GoogleConnector, google_http
from services.google_documents import DOCS, document_tabs


class GoogleDocumentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = AccountStore(
            Path(directory.name) / "accounts.json", MemorySecrets()
        )
        self.account = install_account(self.store)
        self.http = SyntheticGoogle()
        self.adapter = GoogleConnector(self.account, self.store, http=self.http)
        self.docs = self.adapter.documents
        self.cancelled = threading.Event()
        self.adapter.create_document(
            ConnectorRequest(
                "create",
                self.account.connector,
                "create_document",
                "write",
                "drive:root",
                draft("EXISTING_TEXT"),
                "d" * 32,
            ),
            self.cancelled,
        )
        self.http.calls.clear()

    def request(
        self,
        tool="append_document",
        *,
        destination="doc:synthetic-doc:tab:t.0",
        content="SYNTHETIC_APPEND",
        operation="a",
    ):
        write = tool == "append_document"
        return ConnectorRequest(
            "append",
            self.account.connector,
            tool,
            "write" if write else "read",
            destination,
            draft(content) if write else None,
            operation * 32,
        )

    def tab(self, tab_id="t.0"):
        return document_tabs(self.http.document_data["synthetic-doc"])[tab_id]

    def text(self, tab_id="t.0"):
        return "".join(
            e["textRun"]["content"]
            for item in self.tab(tab_id)["documentTab"]["body"]["content"]
            if "paragraph" in item
            for e in item["paragraph"]["elements"]
        )

    def probe(self, destination="doc:synthetic-doc:tab:t.0"):
        return self.docs.find_append(
            ConnectorProbe("append", destination, "a" * 32), self.cancelled
        )

    def test_append_atomic_batch_uses_current_revision_exact_tab_and_utf16_indices(
        self,
    ):
        result = self.docs.append(self.request(content="Hello 😀"), self.cancelled)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(self.text(), "EXISTING_TEXT\nHello 😀\n")
        self.assertEqual(len(self.http.append_writes), 1)
        batch = self.http.append_writes[0]
        self.assertEqual(batch["writeControl"], {"requiredRevisionId": "revision-1"})
        insert, marker = batch["requests"]
        self.assertEqual(
            insert,
            {
                "insertText": {
                    "text": "\nHello 😀",
                    "location": {"index": 14, "tabId": "t.0"},
                }
            },
        )
        self.assertEqual(
            marker["createNamedRange"]["range"],
            {"startIndex": 14, "endIndex": 23, "tabId": "t.0"},
        )
        self.assertEqual(marker["createNamedRange"]["name"], "steed_append_" + "a" * 32)
        self.assertNotIn("Synthetic draft", json.dumps(batch))
        query = parse_qs(
            urlsplit(
                [
                    call[1]
                    for call in self.http.calls
                    if call[1].startswith(DOCS) and call[0] == "GET"
                ][0]
            ).query
        )
        self.assertEqual(query["includeTabsContent"], ["true"])
        self.assertEqual(query["suggestionsViewMode"], ["SUGGESTIONS_INLINE"])
        self.assertIn("https://docs.googleapis.com", google_http().origins)

    def test_nested_tab_edit_and_read_never_target_or_return_first_tab(self):
        self.tab()["childTabs"] = [synthetic_document_tab("t.child", "CHILD\n")]
        result = self.docs.inspect(
            self.request("inspect_document", destination="doc:synthetic-doc"),
            self.cancelled,
        )
        self.assertEqual(
            [t["destination"] for t in json.loads(result.text)["tabs"]],
            ["doc:synthetic-doc:tab:t.0", "doc:synthetic-doc:tab:t.child"],
        )
        self.assertNotIn("EXISTING_TEXT", result.text)
        self.docs.append(
            self.request(destination="doc:synthetic-doc:tab:t.child"), self.cancelled
        )
        result = self.docs.read(
            self.request("read_document", destination="doc:synthetic-doc:tab:t.child"),
            self.cancelled,
        )
        self.assertEqual(json.loads(result.text)["text"], "CHILD\nSYNTHETIC_APPEND\n")
        self.assertNotIn("EXISTING_TEXT", result.text)
        self.assertEqual(self.text(), "EXISTING_TEXT\n")

    def test_unsafe_destination_and_stripped_draft_characters_fail_before_http(self):
        for destination in (
            "doc:synthetic-doc",
            "doc:other:tab:",
            "doc:../secret:tab:t.0",
            "doc:synthetic-doc:tab:t.0?x=y",
        ):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.docs.append(self.request(destination=destination), self.cancelled)
        for content in ("\x01danger", "\rReturn", "\ue000Private"):
            with self.subTest(content=repr(content)), self.assertRaises(ValueError):
                self.docs.append(self.request(content=content), self.cancelled)
        self.assertFalse(self.http.calls)

    def test_lost_append_ack_is_confirmed_without_repeating_batch(self):
        self.http.lose_append = True
        with self.assertRaises(ApiError):
            self.docs.append(self.request(), self.cancelled)
        self.assertEqual(self.probe().state, "succeeded")
        self.assertEqual(len(self.http.append_writes), 1)
        self.assertEqual(
            self.docs.append(self.request(), self.cancelled).state, "succeeded"
        )
        self.assertEqual(len(self.http.append_writes), 1)

    def test_missing_marker_or_removed_tab_never_proves_non_application(self):
        self.assertEqual(self.probe().state, "uncertain")
        self.assertEqual(self.probe("doc:synthetic-doc:tab:missing").state, "uncertain")
        self.assertFalse(self.http.append_writes)

    def test_duplicate_or_invalid_marker_is_uncertain_and_blocks_same_operation(self):
        self.docs.append(self.request(), self.cancelled)
        name = "steed_append_" + "a" * 32
        original = copy.deepcopy(self.tab()["documentTab"]["namedRanges"][name])
        changes = [
            {"namedRanges": original["namedRanges"] * 2},
            {"name": "wrong"},
            {
                "namedRanges": [
                    {
                        "name": name,
                        "namedRangeId": "range",
                        "ranges": [
                            {"tabId": "t.other", "startIndex": 1, "endIndex": 2}
                        ],
                    }
                ]
            },
            {
                "namedRanges": [
                    {
                        "name": name,
                        "namedRangeId": "range",
                        "ranges": [{"tabId": "t.0", "startIndex": True, "endIndex": 2}],
                    }
                ]
            },
        ]
        for change in changes:
            self.tab()["documentTab"]["namedRanges"][name] = {**original, **change}
            self.assertEqual(self.probe().state, "uncertain")
            self.assertEqual(
                self.docs.append(self.request(), self.cancelled).state, "uncertain"
            )
        self.assertEqual(len(self.http.append_writes), 1)

    def test_tab_local_marker_without_optional_tab_id_still_confirms(self):
        self.docs.append(self.request(), self.cancelled)
        marker = self.tab()["documentTab"]["namedRanges"]["steed_append_" + "a" * 32]
        marker["namedRanges"][0]["ranges"][0].pop("tabId")
        self.assertEqual(self.probe().state, "succeeded")

    def test_marker_retains_confirmation_after_collaborator_changes_text(self):
        self.docs.append(self.request(), self.cancelled)
        self.tab()["documentTab"]["body"] = synthetic_document_tab(
            text="COLLABORATOR_EDIT\n"
        )["documentTab"]["body"]
        self.assertEqual(self.probe().state, "succeeded")
        self.assertIn("collaborators", self.probe().text)

    def test_foreign_trashed_shared_drive_shortcut_and_unmarked_document_never_reads_body_or_writes(
        self,
    ):
        metadata = copy.deepcopy(self.http.document_metadata["synthetic-doc"])
        for change in (
            {"ownedByMe": False},
            {"trashed": True},
            {"driveId": "shared"},
            {"mimeType": "application/vnd.google-apps.shortcut"},
            {"appProperties": {}},
            {
                "appProperties": {
                    "steed_operation": "d" * 32,
                    "steed_account": "foreign",
                }
            },
        ):
            self.http.document_metadata["synthetic-doc"] = {**metadata, **change}
            self.http.calls.clear()
            self.assertEqual(
                self.docs.append(self.request(), self.cancelled).state, "not_applied"
            )
            self.assertFalse(any(call[1].startswith(DOCS) for call in self.http.calls))
        self.assertFalse(self.http.append_writes)

    def test_legacy_steed_created_document_without_account_marker_remains_eligible(
        self,
    ):
        self.http.document_metadata["synthetic-doc"]["appProperties"].pop(
            "steed_account"
        )
        self.assertEqual(
            self.docs.append(self.request(), self.cancelled).state, "succeeded"
        )

    def test_read_only_document_can_be_read_and_reconciled_but_not_appended(self):
        self.docs.append(self.request(), self.cancelled)
        self.http.document_metadata["synthetic-doc"]["capabilities"]["canEdit"] = False
        self.assertEqual(self.probe().state, "succeeded")
        result = self.docs.read(self.request("read_document"), self.cancelled)
        self.assertIn("SYNTHETIC_APPEND", result.text)
        result = self.docs.append(self.request(operation="b"), self.cancelled)
        self.assertEqual(result.state, "not_applied")
        self.assertEqual(len(self.http.append_writes), 1)

    def test_revision_conflict_returns_not_applied_and_does_not_retry(self):
        original = self.http.request

        def request(method, url, **kwargs):
            result = original(method, url, **kwargs)
            if url.startswith(DOCS) and method == "GET":
                self.http.document_data["synthetic-doc"]["revisionId"] = (
                    "collaborator-revision"
                )
            return result

        self.http.request = request
        self.assertEqual(
            self.docs.append(self.request(), self.cancelled).state, "not_applied"
        )
        self.assertEqual(
            len([call for call in self.http.calls if call[0] == "POST"]), 1
        )
        self.assertFalse(self.http.append_writes)

    def test_server_failure_is_uncertain_but_atomic_rejection_is_not_applied(self):
        for status in (401, 403, 429, 500):
            self.http.append_status = status
            with self.subTest(status=status), self.assertRaises(ApiError):
                self.docs.append(self.request(), self.cancelled)
        self.http.append_status = 400
        self.assertEqual(
            self.docs.append(self.request(), self.cancelled).state, "not_applied"
        )
        self.assertFalse(self.http.append_writes)

    def test_missing_revision_tab_or_unsupported_final_paragraph_prevents_append(self):
        original = copy.deepcopy(self.http.document_data["synthetic-doc"])
        for value in (None, "", "\nBad"):
            self.http.document_data["synthetic-doc"]["revisionId"] = value
            self.assertEqual(
                self.docs.append(self.request(), self.cancelled).state, "not_applied"
            )
        self.http.document_data["synthetic-doc"] = original
        self.assertEqual(
            self.docs.append(
                self.request(destination="doc:synthetic-doc:tab:missing"),
                self.cancelled,
            ).state,
            "not_applied",
        )
        self.tab()["documentTab"]["body"]["content"][-1] = {"table": {}, "endIndex": 20}
        self.assertEqual(
            self.docs.append(self.request(), self.cancelled).state, "not_applied"
        )
        self.assertFalse(self.http.append_writes)

    def test_duplicate_excessive_or_malformed_tabs_fail_closed(self):
        for tabs in (
            [self.tab(), self.tab()],
            [synthetic_document_tab(str(i)) for i in range(33)],
            [{"tabProperties": {"tabId": "t.0"}, "documentTab": None}],
            [],
        ):
            self.http.document_data["synthetic-doc"]["tabs"] = tabs
            self.assertEqual(
                self.docs.append(self.request(), self.cancelled).state, "not_applied"
            )
        self.assertFalse(self.http.append_writes)

    def test_suggested_final_paragraph_cannot_be_appended_and_read_marks_incomplete(
        self,
    ):
        self.tab()["documentTab"]["body"]["content"][-1]["paragraph"]["elements"][0][
            "suggestedDeletionIds"
        ] = ["suggestion"]
        self.assertEqual(
            self.docs.append(self.request(), self.cancelled).state, "not_applied"
        )
        result = self.docs.read(self.request("read_document"), self.cancelled)
        self.assertTrue(json.loads(result.text)["incomplete"])

    def test_read_text_and_inspection_are_bounded_and_unsupported_content_is_explicit(
        self,
    ):
        tab = synthetic_document_tab(text="\x01" * 10000 + "😀\n")
        tab["documentTab"]["body"]["content"].insert(1, {"table": {}})
        self.http.document_data["synthetic-doc"]["tabs"] = [tab]
        result = self.docs.read(
            self.request("read_document"), self.cancelled
        ).validate()
        payload = json.loads(result.text)
        self.assertTrue(payload["truncated"])
        self.assertTrue(payload["incomplete"])
        self.assertLess(len(result.text.encode("utf-8")), 16384)
        self.http.document_data["synthetic-doc"]["tabs"] = [
            synthetic_document_tab("t." + str(i).zfill(2) + "x" * 60, title="\x01" * 64)
            for i in range(32)
        ]
        result = self.docs.inspect(
            self.request("inspect_document", destination="doc:synthetic-doc"),
            self.cancelled,
        ).validate()
        self.assertTrue(json.loads(result.text)["tabs_truncated"])

    def test_revocation_or_cancellation_after_document_read_stops_before_batch(self):
        original = self.http.request

        def request(method, url, **kwargs):
            result = original(method, url, **kwargs)
            if url.startswith(DOCS) and method == "GET":
                self.cancelled.set()
            return result

        self.http.request = request
        with self.assertRaises(AccountError):
            self.docs.append(self.request(), self.cancelled)
        self.assertFalse(self.http.append_writes)

    def test_malformed_success_reply_remains_uncertain_but_marker_can_confirm(self):
        self.http.malformed_append_reply = True
        with self.assertRaises(ApiError):
            self.docs.append(self.request(), self.cancelled)
        self.assertEqual(self.probe().state, "succeeded")
        self.assertEqual(len(self.http.append_writes), 1)
