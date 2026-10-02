"""User-only review, exact reusable direct-edit scope and no replay on failures."""

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app_connector_support import MemorySecrets, SyntheticGoogle, install_account
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject
from PyQt6.QtWidgets import QWidget
from qt_test_support import application

from services.app_accounts import AccountStore
from services.connectors import ConnectorRegistry, ConnectorRequest
from services.google_connector import GoogleConnector
from services.google_document_edits import GoogleDocumentEdits
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.app_review import AppReviewDialog
from ui.task_approval import TaskApprovalQueue
from ui.task_dialog import TaskDialog


class AppReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.store = AccountStore(self.path / "accounts.json", MemorySecrets())
        self.account = install_account(self.store)
        self.http = SyntheticGoogle()
        self.adapter = GoogleConnector(self.account, self.store, http=self.http)
        self.edits = GoogleDocumentEdits(self.adapter.documents)
        self.registry = ConnectorRegistry()
        for definition in self.adapter.tools():
            self.registry.register(definition)
        self.service = TaskService(self.path / "tasks.sqlite", connectors=self.registry)
        self.dialogs = []
        client = self.task("create_document", "drive:root")
        artifact = self.draft(client, "ORIGINAL 😀")
        self.call(client, "create_document", "drive:root", artifact)
        self.settled(client)
        self.destination = "doc:synthetic-doc:tab:t.0"

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.close()
                sip.delete(dialog)
        self.wait(lambda: not self.service._connector_jobs)
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("App review fixture timed out")

    def resolve(self, future):
        self.wait(future.done)
        return future.result()

    def task(self, tool, destination, *, remember="once"):
        task = self.service._create("Synthetic document change")
        grant = self.service._request_grant(
            task.task_id,
            {
                "actions": ["app", "artifact"],
                "connector_permissions": [
                    {
                        "connector": self.account.connector,
                        "tool": tool,
                        "effect": "write",
                        "destination": destination,
                    }
                ],
            },
        )
        if grant.status == "requested":
            self.service.approve_grant(task.task_id, grant.grant_id, remember=remember)
        return self.service.client(task.task_id)

    def draft(self, client, content="REPLACEMENT 😀"):
        summary = self.resolve(
            client.submit("artifact.store", title="Proposed content", content=content)
        )
        return self.service.store.artifact(client.task_id, summary.artifact_id)

    def call(self, client, tool, destination, artifact, request_id="change"):
        return self.resolve(
            client.submit(
                "app.call_tool",
                connector=self.account.connector,
                tool=tool,
                destination=destination,
                artifact_id=artifact.artifact_id,
                request_id=request_id,
            )
        )

    def settled(self, client, request_id="change"):
        self.wait(
            lambda: (client.task_id, request_id) not in self.service._connector_jobs
        )
        return self.service.connector_store.get(client.task_id, request_id)

    def proposal(self, *, remember="once"):
        client = self.task("replace_document", self.destination, remember=remember)
        artifact = self.draft(client)
        self.call(client, "replace_document", self.destination, artifact)
        self.settled(client)
        return client, artifact

    def test_preparation_captures_full_preview_without_mutation_or_agent_body_disclosure(
        self,
    ):
        client, _ = self.proposal()
        receipt = self.settled(client)
        review = self.service.app_reviews.get(client.task_id, "change")
        self.assertEqual(receipt.state, "requested")
        self.assertEqual(receipt.reason, "awaiting_review")
        self.assertFalse(receipt.text)
        self.assertEqual(review.before_text, "ORIGINAL 😀")
        self.assertEqual(review.after_text, "REPLACEMENT 😀")
        self.assertFalse(self.http.replacement_writes)
        events = json.dumps(
            [event.payload for event in self.service.store.events(client.task_id)]
        )
        self.assertNotIn("ORIGINAL", events)
        self.assertNotIn("REPLACEMENT", events)

    def test_trusted_approval_applies_exact_draft_once_and_scrubs_preview(self):
        client, artifact = self.proposal()
        self.service.resolve_app_review(client.task_id, "change", approve=True)
        self.assertEqual(self.settled(client).state, "succeeded")
        self.assertEqual(len(self.http.replacement_writes), 1)
        review = self.service.app_reviews.get(client.task_id, "change")
        self.assertEqual(
            (review.status, review.before_text, review.after_text, review.context),
            ("approved", "", "", ""),
        )
        self.assertEqual(
            self.call(client, "replace_document", self.destination, artifact).state,
            "succeeded",
        )
        with self.assertRaises(ValueError):
            self.service.resolve_app_review(client.task_id, "change", approve=True)
        batch = self.http.replacement_writes[0]
        self.assertEqual(batch["writeControl"], {"requiredRevisionId": "revision-1"})
        self.assertEqual(
            batch["requests"][0]["deleteContentRange"]["range"],
            {"startIndex": 1, "endIndex": 12, "tabId": "t.0"},
        )
        self.assertEqual(batch["requests"][1]["insertText"]["text"], artifact.content)

    def test_deny_cancel_finish_or_revoke_never_applies_and_scrubs_pending_preview(
        self,
    ):
        for method in ("deny", "cancel", "finish", "revoke"):
            client, _ = self.proposal()
            if method == "deny":
                self.service.resolve_app_review(client.task_id, "change")
            elif method == "cancel":
                self.service._app_cancel(client.task_id, "change")
            elif method == "finish":
                self.service._finish_task(client.task_id)
            else:
                self.service.revoke_grant(
                    client.task_id, self.service._grants[client.task_id][0]
                )
            review = self.service.app_reviews.get(client.task_id, "change")
            self.assertIn(review.status, {"denied", "cancelled"})
            self.assertFalse(review.before_text or review.after_text or review.context)
            self.assertEqual(
                self.service.connector_store.get(client.task_id, "change").state,
                "cancelled",
            )
        self.assertFalse(self.http.replacement_writes)

    def test_saved_reviewed_permission_still_requires_each_content_review(self):
        client, _ = self.proposal(remember="global")
        self.service.resolve_app_review(client.task_id, "change")
        second, _ = self.proposal()
        self.assertEqual(
            self.service.app_reviews.get(second.task_id, "change").status, "pending"
        )
        self.assertFalse(self.http.replacement_writes)

    def test_direct_edit_requires_its_own_permission_and_can_use_saved_approval(self):
        client, artifact = self.proposal(remember="global")
        with self.assertRaises(PermissionError):
            self.call(
                client, "replace_document_direct", self.destination, artifact, "direct"
            )
        direct = self.task(
            "replace_document_direct", self.destination, remember="global"
        )
        self.call(
            direct, "replace_document_direct", self.destination, self.draft(direct)
        )
        self.assertEqual(self.settled(direct).state, "succeeded")
        self.assertFalse(self.service.app_reviews.pending(direct.task_id))
        next_direct = self.task("replace_document_direct", self.destination)
        self.call(
            next_direct,
            "replace_document_direct",
            self.destination,
            self.draft(next_direct, "NEXT_BODY"),
        )
        self.assertEqual(self.settled(next_direct).state, "succeeded")
        self.assertEqual(len(self.http.replacement_writes), 2)

    def test_revision_change_after_preview_returns_not_applied_without_post(self):
        client, _ = self.proposal()
        self.http.document_data["synthetic-doc"]["revisionId"] = "collaborator-change"
        self.service.resolve_app_review(client.task_id, "change", approve=True)
        self.assertEqual(self.settled(client).state, "not_applied")
        self.assertFalse(self.http.replacement_writes)

    def test_lost_reply_reconciles_unique_edit_range_without_second_mutation(self):
        client, _ = self.proposal()
        self.http.lose_replacement = True
        self.service.resolve_app_review(client.task_id, "change", approve=True)
        self.assertEqual(self.settled(client).state, "uncertain")
        self.resolve(client.submit("app.reconcile", request_id="change"))
        self.assertEqual(self.settled(client).state, "succeeded")
        self.assertEqual(len(self.http.replacement_writes), 1)

    def test_agent_has_no_review_approval_command(self):
        client, _ = self.proposal()
        with self.assertRaises(PermissionError):
            self.resolve(client.submit("app.approve_review", request_id="change"))
        self.assertEqual(
            self.service.app_reviews.get(client.task_id, "change").status, "pending"
        )

    def test_restart_discards_pending_preview_and_never_replays(self):
        client, _ = self.proposal()
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.path / "tasks.sqlite", connectors=self.registry)
        review = self.service.app_reviews.get(client.task_id, "change")
        self.assertEqual(review.status, "cancelled")
        self.assertFalse(review.before_text or review.after_text)
        self.assertEqual(
            self.service.connector_store.get(client.task_id, "change").state,
            "cancelled",
        )
        self.assertFalse(self.http.replacement_writes)

    def test_review_dialog_is_plain_text_and_default_deny_and_management_can_reopen(
        self,
    ):
        client, _ = self.proposal()
        review = self.service.app_reviews.get(client.task_id, "change")
        dialog = AppReviewDialog(
            self.service, self.service.store.get(client.task_id), review
        )
        self.dialogs.append(dialog)
        self.assertTrue(dialog.deny_button.isDefault())
        self.assertFalse(dialog.approve_button.autoDefault())
        dialog.decide(False)
        self.assertEqual(
            self.service.app_reviews.get(client.task_id, "change").status, "denied"
        )
        panel = TaskDialog(self.service)
        self.dialogs.append(panel)
        row = next(
            i
            for i, (record, _) in enumerate(panel._rows)
            if record.task_id == client.task_id
        )
        panel.table.selectRow(row)
        self.assertFalse(panel.review_app_button.isEnabled())

    def test_foreign_task_cannot_resolve_another_tasks_proposal(self):
        client, _ = self.proposal()
        other = self.task("replace_document", self.destination)
        with self.assertRaises(KeyError):
            self.service.resolve_app_review(other.task_id, "change", approve=True)
        self.assertEqual(
            self.service.app_reviews.get(client.task_id, "change").status, "pending"
        )

    def test_automatic_queue_presents_review_and_never_autoapproves_content(self):
        client, _ = self.proposal()
        window = QWidget()
        registry = QObject()
        owner = QObject(window)
        registry.owners = [owner]
        queue = TaskApprovalQueue(registry, self.service)
        self.wait(lambda: isinstance(queue.dialog, AppReviewDialog))
        self.assertEqual(queue.dialog.request_key, f"review:{client.task_id}:change")
        self.assertFalse(self.http.replacement_writes)
        queue.dialog.decide(False)
        sip.delete(registry)
        sip.delete(window)

    def test_authority_ending_between_preparation_and_review_does_not_freeze_service(
        self,
    ):
        original = self.service._app_grant

        def grant(task_id, request, **kwargs):
            try:
                receipt = self.service.connector_store.get(task_id, request.request_id)
            except KeyError:
                receipt = None
            if receipt is not None and receipt.reason == "preparing_review":
                raise TaskPermissionError("approval_required")
            return original(task_id, request, **kwargs)

        with patch.object(self.service, "_app_grant", side_effect=grant):
            client, _ = self.proposal()
        self.assertEqual(self.settled(client).state, "cancelled")
        self.assertFalse(self.service.failure)
        self.assertFalse(self.http.replacement_writes)

    def test_changed_draft_or_review_store_failure_prevents_dispatch(self):
        client, _ = self.proposal()
        with (
            patch.object(
                self.service.app_reviews,
                "resolve",
                side_effect=OSError("Synthetic failure"),
            ),
            self.assertRaises(OSError),
        ):
            self.service.resolve_app_review(client.task_id, "change", approve=True)
        self.assertFalse(self.http.replacement_writes)

    def test_reviewed_adapter_cannot_run_without_captured_review(self):
        client, artifact = self.proposal()
        request = ConnectorRequest(
            "raw",
            self.account.connector,
            "replace_document",
            "write",
            self.destination,
            artifact,
            "a" * 32,
        )
        with self.assertRaises(ValueError):
            self.edits.apply_review(request, threading.Event())
        review = self.service.app_reviews.get(client.task_id, "change").payload()
        result = self.edits.apply_review(
            replace(request, review=review), threading.Event()
        )
        self.assertEqual(result.state, "succeeded")
