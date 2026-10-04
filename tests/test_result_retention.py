"""User-owned result removal preserves authority, evidence and no-replay identity."""

import sqlite3
import unittest
from unittest.mock import patch

import test_archived_probes as archives
import test_connectors as fixtures
from PyQt6 import sip
from PyQt6.QtWidgets import QMessageBox
from qt_test_support import application

from services.agent_tools import TOOLS_BY_NAME
from services.connectors import ConnectorOutcome
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.task_dialog import TaskDialog


class ResultRetentionTests(unittest.TestCase):
    setUp = fixtures.ConnectorTests.setUp
    tearDown = fixtures.ConnectorTests.tearDown
    execute = fixtures.ConnectorTests.execute
    reconcile = fixtures.ConnectorTests.reconcile
    wait_for = fixtures.ConnectorTests.wait_for
    resolve = fixtures.ConnectorTests.resolve
    client = fixtures.ConnectorTests.client
    call = fixtures.ConnectorTests.call
    finished = fixtures.ConnectorTests.finished
    gate = fixtures.ConnectorTests.gate
    followup = archives.ArchivedProbeTests.followup
    authorize = archives.ArchivedProbeTests.authorize
    probe = archives.ArchivedProbeTests.probe

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def source(self):
        source, draft, _ = archives.ArchivedProbeTests.source(self)
        receipt = self.service.connector_store.update(
            source.task_id,
            "document-1",
            "uncertain",
            reference="PRIVATE_PROVIDER_LINK",
            text="PRIVATE_PROVIDER_RESULT",
        )
        return source, draft, receipt

    def test_clear_preserves_identity_state_draft_and_content_free_evidence(self):
        self.behavior = lambda request, cancel: ConnectorOutcome(
            reference="PRIVATE_PROVIDER_LINK", text="PRIVATE_PROVIDER_RESULT"
        )
        client, _, draft = self.client()
        self.call(client, draft)
        before = self.finished(client)
        self.assertEqual(
            self.service.clear_app_results(client.task_id, [before.request_id]), 1
        )
        after = self.resolve(client.submit("app.status", request_id=before.request_id))
        self.assertEqual(after.reference, "")
        self.assertEqual(after.text, "")
        self.assertTrue(after.result_cleared_at)
        for field in (
            "operation_id",
            "fingerprint",
            "effect",
            "connector",
            "tool",
            "destination",
            "artifact_id",
            "state",
            "reason",
            "created_at",
        ):
            self.assertEqual(getattr(after, field), getattr(before, field))
        self.assertEqual(
            self.service.store.artifact(client.task_id, draft.artifact_id).content,
            "PRIVATE_DOCUMENT_BODY",
        )
        self.assertEqual(self.call(client, draft), after)
        self.assertEqual(len(self.calls), 1)
        events = self.service.store.events(client.task_id)
        self.assertEqual(events[-1].kind, "app.result_cleared")
        self.assertEqual(events[-1].payload, {"request_id": before.request_id})
        self.assertNotIn("PRIVATE_PROVIDER", str(events))
        self.assertEqual(
            self.service.clear_app_results(client.task_id, [before.request_id]), 0
        )
        self.assertEqual(self.service.store.events(client.task_id), events)
        self.assertEqual(
            self.service.connector_store.get(
                client.task_id, before.request_id
            ).result_cleared_at,
            after.result_cleared_at,
        )

    def test_bulk_clear_is_scoped_to_owned_receipts_and_validates_before_changes(self):
        client, _, draft = self.client()
        self.call(client, draft)
        self.finished(client)
        self.call(client, draft, request_id="document-2")
        self.finished(client, "document-2")
        other, _, other_draft = self.client()
        self.call(other, other_draft, request_id="foreign")
        foreign = self.finished(other, "foreign")
        for ids in (
            ["document-1", "foreign"],
            ["document-1"] * 2,
            ["document-1"] * 51,
            "document-1",
            [1],
        ):
            with self.assertRaises((KeyError, ValueError)):
                self.service.clear_app_results(client.task_id, ids)
        self.assertFalse(
            self.service.connector_store.get(
                client.task_id, "document-1"
            ).result_cleared_at
        )
        self.assertEqual(self.service.clear_app_results(client.task_id), 2)
        self.assertTrue(
            all(
                r.result_cleared_at and not r.reference and not r.text
                for r in self.service.connector_store.list(client.task_id)
            )
        )
        self.assertEqual(
            self.service.connector_store.get(other.task_id, "foreign"), foreign
        )
        self.assertFalse(self.service.failure)

    def test_empty_task_clear_is_noop(self):
        task = self.service._create("No provider results")
        events = self.service.store.events(task.task_id)
        self.assertEqual(self.service.clear_app_results(task.task_id), 0)
        self.assertEqual(self.service.store.events(task.task_id), events)

    def test_clearing_completed_or_uncertain_receipts_does_not_release_write_guard(
        self,
    ):
        for state in ("succeeded", "uncertain"):
            source, draft, original = self.source()
            self.service.connector_store.update(
                source.task_id, original.request_id, state
            )
            self.service.clear_app_results(source.task_id)
            client = self.followup(source, draft, approve=False)
            grant = self.service._request_grant(
                client.task_id,
                {
                    "actions": ["app", "artifact"],
                    "connector_permissions": [
                        {
                            "connector": "documents",
                            "tool": "create",
                            "effect": "write",
                            "destination": "workspace-a",
                        }
                    ],
                },
            )
            self.service.approve_grant(client.task_id, grant.grant_id)
            before = len(self.calls)
            with self.assertRaises(TaskPermissionError) as denied:
                self.call(
                    client,
                    self.service.store.artifacts(client.task_id)[0],
                    request_id="new-write",
                )
            self.assertEqual(denied.exception.code, "recovery_write_already_recorded")
            self.assertEqual(len(self.calls), before)
            self.assertFalse(self.service.failure)

    def test_shared_snapshots_are_scrubbed_atomically_without_erasing_other_evidence(
        self,
    ):
        source, draft, original = self.source()
        first = self.followup(source, draft, approve=False)
        second = self.followup(source, approve=False)
        contexts = [
            self.service.store.recovery(c.task_id)["context"] for c in (first, second)
        ]
        unrelated, _, _ = self.source()
        independent = self.followup(unrelated, approve=False)
        self.service.clear_app_results(source.task_id, [original.request_id])
        for client, before in zip((first, second), contexts, strict=True):
            after = self.service.store.recovery(client.task_id)["context"]
            receipt = after["app_receipts"][0]
            self.assertEqual(receipt["text"], "")
            self.assertEqual(receipt["reference"], "")
            self.assertFalse(receipt["text_truncated"])
            for field in (
                "source_task",
                "previous_scope",
                "activity",
                "source_events",
                "imported_artifacts",
            ):
                self.assertEqual(after[field], before[field])
            with self.service.store._db:
                row = self.service.store._db.execute(
                    "SELECT snapshot FROM task_recoveries WHERE task_id = ?",
                    (client.task_id,),
                ).fetchone()
            self.assertNotIn("PRIVATE_PROVIDER", row[0])
            self.assertEqual(
                self.service.store.events(client.task_id)[-1].kind,
                "recovery.results_cleared",
            )
        self.assertIn(
            "PRIVATE_PROVIDER_RESULT",
            str(self.service.store.recovery(independent.task_id)),
        )
        self.assertEqual(
            self.service.store.artifact(
                first.task_id,
                self.service.store.artifacts(first.task_id)[0].artifact_id,
            ).content,
            "PRIVATE_DOCUMENT_BODY",
        )
        fresh = self.followup(source, approve=False)
        self.assertEqual(
            self.service.store.recovery(fresh.task_id)["context"]["app_receipts"][0][
                "text"
            ],
            "",
        )

    def test_late_write_outcome_keeps_state_but_cannot_restore_cleared_result(self):
        gate = self.gate()
        self.behavior = lambda request, cancel: (
            gate.wait(3),
            ConnectorOutcome(reference="LATE_PRIVATE_LINK", text="LATE_PRIVATE_RESULT"),
        )[1]
        client, _, draft = self.client()
        self.call(client, draft)
        self.wait_for(lambda: bool(self.calls))
        self.service.clear_app_results(client.task_id)
        gate.set()
        result = self.finished(client)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual((result.reference, result.text), ("", ""))
        self.assertTrue(result.result_cleared_at)
        self.assertEqual(len(self.calls), 1)

    def test_queued_write_is_not_cancelled_by_retention_change(self):
        client, _, draft = self.client()
        self.service._app_call(
            client.task_id,
            "documents",
            "create",
            "queued",
            "workspace-a",
            draft.artifact_id,
        )
        self.service.clear_app_results(client.task_id)
        result = self.finished(client, "queued")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.reference, "")
        self.assertTrue(result.result_cleared_at)
        self.assertEqual(len(self.calls), 1)

    def test_new_archived_check_can_return_new_result_without_restoring_cleared_source(
        self,
    ):
        source, _, original = self.source()
        self.service.clear_app_results(source.task_id)
        client = self.followup(source)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome(
            reference="FRESH_LINK", text="FRESH_RESULT"
        )
        self.probe(client)
        checked = self.finished(client, "history-check")
        cleared = self.service.connector_store.get(source.task_id, original.request_id)
        self.assertEqual(
            (checked.reference, checked.text), ("FRESH_LINK", "FRESH_RESULT")
        )
        self.assertEqual(cleared.state, "succeeded")
        self.assertEqual((cleared.reference, cleared.text), ("", ""))
        self.assertTrue(cleared.result_cleared_at)
        self.assertEqual(self.probes[0].operation_id, original.operation_id)
        self.assertEqual(len(self.calls), 1)
        self.service.clear_app_results(client.task_id)
        self.assertEqual(
            self.service.connector_store.probe_source(
                client.task_id, checked.request_id
            ),
            (source.task_id, original.request_id),
        )

    def test_late_probe_confirmation_honors_clear_markers_on_both_receipts(self):
        source, _, original = self.source()
        client = self.followup(source)
        gate = self.gate()
        self.probe_behavior = lambda probe, cancel: (
            gate.wait(3),
            ConnectorOutcome(reference="PRIVATE_LINK", text="PRIVATE_RESULT"),
        )[1]
        self.probe(client)
        self.wait_for(lambda: bool(self.probes))
        self.service.clear_app_results(source.task_id)
        self.service.clear_app_results(client.task_id)
        gate.set()
        self.finished(client, "history-check")
        for task_id, request_id in (
            (source.task_id, original.request_id),
            (client.task_id, "history-check"),
        ):
            result = self.service.connector_store.get(task_id, request_id)
            self.assertEqual(result.state, "succeeded")
            self.assertEqual((result.reference, result.text), ("", ""))
            self.assertTrue(result.result_cleared_at)
        self.assertEqual(len(self.calls), 1)

    def test_clearing_independently_confirmed_source_keeps_late_worker_protection(self):
        source, _, original = self.source()
        client = self.followup(source)
        self.probe(client)
        self.finished(client, "history-check")
        confirmed = self.service.connector_store.get(
            source.task_id, original.request_id
        )
        self.assertEqual(confirmed.reason, "read_only_followup")
        self.service.clear_app_results(source.task_id)
        self.service.connector_store.update(
            source.task_id,
            original.request_id,
            "uncertain",
            reference="OLDER_RESULT",
            text="OLDER_TEXT",
        )
        result = self.service.connector_store.get(source.task_id, original.request_id)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.reason, "read_only_followup")
        self.assertEqual((result.reference, result.text), ("", ""))

    def test_journal_failure_rolls_back_receipt_markers_and_shared_snapshots(self):
        source, _, original = self.source()
        client = self.followup(source, approve=False)
        before = self.service.store.recovery(client.task_id)
        append = self.service.store._append

        def fail_followup(task_id, kind, payload):
            if kind == "recovery.results_cleared":
                raise sqlite3.OperationalError("Synthetic journal failure")
            append(task_id, kind, payload)

        with patch.object(self.service.store, "_append", side_effect=fail_followup):
            with self.assertRaises(sqlite3.Error):
                self.service.clear_app_results(source.task_id)
        self.assertTrue(self.service.failure)
        self.assertEqual(
            self.service.connector_store.get(source.task_id, original.request_id),
            original,
        )
        self.assertEqual(self.service.store.recovery(client.task_id), before)
        self.assertEqual(
            self.service.store._db.execute(
                "SELECT count(*) FROM connector_result_clears"
            ).fetchone()[0],
            0,
        )

    def test_corrupt_shared_snapshot_rolls_back_clear_and_freezes_access(self):
        source, _, original = self.source()
        client = self.followup(source, approve=False)
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE task_recoveries SET snapshot = 'not-json' WHERE task_id = ?",
                (client.task_id,),
            )
        with self.assertRaises(RuntimeError):
            self.service.clear_app_results(source.task_id)
        self.assertTrue(self.service.failure)
        self.assertEqual(
            self.service.connector_store.get(source.task_id, original.request_id),
            original,
        )

    def test_restart_preserves_clear_marker_and_never_replays(self):
        source, _, original = self.source()
        self.service.clear_app_results(source.task_id)
        before = len(self.calls), len(self.probes)
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.path, connectors=self.registry)
        receipt = self.service.connector_store.get(source.task_id, original.request_id)
        self.assertTrue(receipt.result_cleared_at)
        self.assertEqual((receipt.reference, receipt.text), ("", ""))
        self.assertEqual(receipt.state, original.state)
        self.assertFalse(self.service._grants)
        self.assertEqual((len(self.calls), len(self.probes)), before)

    def test_schema_seven_migration_preserves_existing_receipts_and_journal(self):
        source, _, original = self.source()
        events = self.service.store.events(source.task_id)
        self.service.close()
        sip.delete(self.service)
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE connector_result_clears")
            database.execute("PRAGMA user_version = 7")
        self.service = TaskService(self.path, connectors=self.registry)
        self.assertEqual(
            self.service.store._db.execute("PRAGMA user_version").fetchone()[0], 17
        )
        self.assertEqual(
            self.service.connector_store.get(source.task_id, original.request_id),
            original,
        )
        self.assertEqual(self.service.store.events(source.task_id), events)
        self.service.clear_app_results(source.task_id)
        self.assertTrue(
            self.service.connector_store.get(
                source.task_id, original.request_id
            ).result_cleared_at
        )

    def test_agents_cannot_clear_results_through_task_facade_or_public_tools(self):
        client, _, draft = self.client()
        self.call(client, draft)
        before = self.finished(client)
        with self.assertRaises(TaskPermissionError):
            self.resolve(client.submit("app.clear_results"))
        self.assertNotIn("clear_app_results", self.service.broker._handlers)
        self.assertNotIn("app.clear_results", self.service.broker._handlers)
        self.assertNotIn("steed_app_clear_results", TOOLS_BY_NAME)
        self.assertEqual(
            self.service.connector_store.get(client.task_id, before.request_id), before
        )

    def test_user_clear_requires_confirmation_and_supports_previous_rows(self):
        source, _, original = self.source()
        client = self.followup(source, approve=False)
        panel = TaskDialog(self.service)
        try:
            row = next(
                i
                for i, (task, _) in enumerate(panel._rows)
                if task.task_id == client.task_id
            )
            panel.table.selectRow(row)
            self.assertTrue(panel.clear_app_result_button.isEnabled())
            self.assertFalse(panel.clear_task_results_button.isEnabled())
            with patch(
                "ui.task_dialog.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as question:
                panel.clear_app_result_button.click()
                self.assertEqual(
                    question.call_args.args[-1], QMessageBox.StandardButton.No
                )
            self.assertEqual(
                self.service.connector_store.get(source.task_id, original.request_id),
                original,
            )
            with patch(
                "ui.task_dialog.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                panel.clear_app_result_button.click()
            self.assertIn("Saved result cleared:", panel.app_preview.toPlainText())
            self.assertNotIn("PRIVATE_PROVIDER", panel.app_preview.toPlainText())
            self.assertFalse(panel.clear_app_result_button.isEnabled())
            self.assertEqual(
                self.service.connector_store.get(
                    source.task_id, original.request_id
                ).state,
                "uncertain",
            )
        finally:
            panel.close()
            sip.delete(panel)

    def test_bulk_ui_clear_does_not_change_visible_previous_receipts(self):
        source, _, original = self.source()
        client = self.followup(source)
        self.probe(client)
        checked = self.finished(client, "history-check")
        source_after = self.service.connector_store.get(
            source.task_id, original.request_id
        )
        panel = TaskDialog(self.service)
        try:
            row = next(
                i
                for i, (task, _) in enumerate(panel._rows)
                if task.task_id == client.task_id
            )
            panel.table.selectRow(row)
            self.assertTrue(panel.clear_task_results_button.isEnabled())
            with patch(
                "ui.task_dialog.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                panel.clear_task_results_button.click()
            own = self.service.connector_store.get(client.task_id, checked.request_id)
            self.assertTrue(own.result_cleared_at)
            self.assertEqual(own.text, "")
            self.assertEqual(
                self.service.connector_store.get(source.task_id, original.request_id),
                source_after,
            )
        finally:
            panel.close()
            sip.delete(panel)


if __name__ == "__main__":
    unittest.main()
