"""Fresh read-only authority and durable ownership for archived app checks."""

import sqlite3
import unittest
from unittest.mock import patch

import test_connectors as fixtures
from PyQt6 import sip
from PyQt6.QtWidgets import QLabel, QPlainTextEdit
from qt_test_support import application

from services.connectors import ConnectorOutcome, ConnectorTool
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.task_approval import TaskApprovalDialog
from ui.task_dialog import TaskDialog


class ArchivedProbeTests(unittest.TestCase):
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

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def source(self):
        self.behavior = lambda request, cancel: ConnectorOutcome("uncertain")
        client, _, artifact = self.client()
        self.call(client, artifact)
        receipt = self.finished(client)
        self.service._finish_task(client.task_id)
        return client, artifact, receipt

    def followup(self, source, artifact=None, *, evidence=True, approve=True, **limits):
        task = self.service._create("Check a previous result")
        self.service._request_recovery(task.task_id)
        self.service.approve_recovery(
            task.task_id,
            source.task_id,
            [artifact.artifact_id] if artifact else [],
            evidence=evidence,
        )
        if approve:
            self.authorize(task.task_id, **limits)
        return self.service.client(task.task_id)

    def authorize(self, task_id, **limits):
        grant = self.service._request_grant(
            task_id,
            {
                "actions": ["app"],
                "connector_permissions": [
                    {
                        "connector": "documents",
                        "tool": "create",
                        "effect": "read",
                        "destination": "workspace-a",
                    }
                ],
                **limits,
            },
        )
        self.assertEqual(grant.status, "requested")
        self.service.approve_grant(task_id, grant.grant_id)
        return grant

    def probe(self, client, request_id="history-check", source_request_id="document-1"):
        return self.resolve(
            client.submit(
                "app.reconcile_history",
                request_id=request_id,
                source_request_id=source_request_id,
            )
        )

    def test_read_only_check_uses_original_identity_and_never_reads_draft_or_writes(
        self,
    ):
        source, _, original = self.source()
        client = self.followup(source)
        requested = self.probe(client)
        outcome = self.finished(client, "history-check")
        self.assertEqual(outcome.state, "succeeded")
        self.assertEqual(outcome.effect, "read")
        self.assertNotEqual(requested.operation_id, original.operation_id)
        self.assertEqual(self.probes[0].operation_id, original.operation_id)
        self.assertEqual(self.probes[0].request_id, original.request_id)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.service.store.artifacts(client.task_id))
        self.assertNotIn(source.task_id, self.service._grants)
        self.assertEqual(self.service.store.get(source.task_id).status, "completed")
        self.assertEqual(
            self.service.connector_store.get(source.task_id, original.request_id).state,
            "succeeded",
        )
        self.assertEqual(self.probe(client), outcome)
        self.assertEqual(len(self.probes), 1)
        self.assertEqual(
            self.resolve(client.submit("app.status", request_id="history-check")),
            outcome,
        )
        with self.assertRaises(KeyError):
            self.resolve(client.submit("app.status", request_id="document-1"))

    def test_read_authority_cannot_send_and_old_write_authority_cannot_probe(self):
        source, draft, _ = self.source()
        client = self.followup(source, draft)
        imported = self.service.store.artifacts(client.task_id)[0]
        with self.assertRaises(TaskPermissionError):
            self.call(client, imported, request_id="new-write")
        other = self.followup(source, draft, approve=False)
        grant = self.service._request_grant(
            other.task_id,
            {
                "actions": ["artifact", "app"],
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
        self.service.approve_grant(other.task_id, grant.grant_id)
        with self.assertRaises(TaskPermissionError):
            self.probe(other)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.probes)
        self.assertFalse(self.service.failure)

    def test_unshared_and_foreign_receipts_need_explicit_user_selection(self):
        source, draft, _ = self.source()
        client = self.followup(source, draft, evidence=False)
        with self.assertRaises(TaskPermissionError) as denied:
            self.probe(client)
        self.assertEqual(denied.exception.code, "recovery_receipt_not_shared")
        allowed = self.followup(source)
        with self.assertRaises(TaskPermissionError):
            self.probe(allowed, source_request_id="foreign-receipt")
        plain, _, _ = self.client(effect="read", actions=("app",))
        with self.assertRaises(TaskPermissionError):
            self.probe(plain)
        self.assertFalse(self.probes)
        self.assertFalse(self.service.failure)

    def test_fresh_approval_and_exact_destination_are_required(self):
        source, _, _ = self.source()
        client = self.followup(source, approve=False)
        with self.assertRaises(TaskPermissionError):
            self.probe(client)
        grant = self.service._request_grant(
            client.task_id,
            {
                "actions": ["app"],
                "connector_permissions": [
                    {
                        "connector": "documents",
                        "tool": "create",
                        "effect": "read",
                        "destination": "workspace-b",
                    }
                ],
            },
        )
        self.service.approve_grant(client.task_id, grant.grant_id)
        with self.assertRaises(TaskPermissionError):
            self.probe(client)
        self.assertFalse(self.probes)

    def test_unsupported_provider_is_not_replaced_by_execute(self):
        source, _, _ = self.source()
        self.registry.unregister("documents")
        self.registry.register(
            ConnectorTool(
                "documents",
                "create",
                "write",
                "No receipt lookup",
                True,
                self.execute,
            )
        )
        client = self.followup(source, approve=False)
        with self.assertRaises(ValueError):
            self.authorize(client.task_id)
        with self.assertRaises(ValueError):
            self.probe(client)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.probes)

    def test_unknown_result_can_be_rechecked_without_replaying_original(self):
        source, _, original = self.source()
        client = self.followup(source)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome("uncertain")
        self.probe(client)
        self.assertEqual(self.finished(client, "history-check").state, "uncertain")
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome(
            reference="confirmed"
        )
        self.resolve(client.submit("app.reconcile", request_id="history-check"))
        self.assertEqual(self.finished(client, "history-check").reference, "confirmed")
        self.assertEqual(
            [p.operation_id for p in self.probes], [original.operation_id] * 2
        )
        self.assertEqual(len(self.calls), 1)

    def test_duplicate_id_cannot_bind_another_source_receipt(self):
        source, _, _ = self.source()
        first = self.service.connector_store.get(source.task_id, "document-1")
        from services.connectors import ConnectorRequest

        self.service.connector_store.create(
            source.task_id,
            ConnectorRequest(
                "second",
                first.connector,
                first.tool,
                first.effect,
                first.destination,
                None,
            ),
            "fixture-second",
        )
        self.service.connector_store.update(source.task_id, "second", "uncertain")
        client = self.followup(source)
        self.probe(client)
        self.finished(client, "history-check")
        with self.assertRaises(ValueError):
            self.probe(client, source_request_id="second")
        self.assertEqual(len(self.probes), 1)

    def test_revocation_before_worker_entry_prevents_provider_requests(self):
        source, _, _ = self.source()
        client = self.followup(source)
        self.service._app_reconcile_history(client.task_id, "document-1", "queued")
        self.service.cancel_task(client.task_id)
        self.finished(client, "queued")
        self.assertFalse(self.probes)
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "uncertain",
        )

    def test_cancelled_worker_checks_fresh_grant_before_further_network(self):
        source, _, _ = self.source()
        client = self.followup(source)
        gate = self.gate()
        observed = []

        def slow(probe, cancel):
            gate.wait(3)
            observed.append(cancel.is_set())
            raise RuntimeError("Cancelled synthetic provider")

        self.probe_behavior = slow
        self.probe(client)
        self.wait_for(lambda: bool(self.probes))
        self.resolve(client.submit("app.cancel", request_id="history-check"))
        gate.set()
        self.assertEqual(self.finished(client, "history-check").state, "uncertain")
        self.assertEqual(observed, [True])
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "uncertain",
        )

    def test_timeout_preserves_original_uncertainty_and_late_confirmation(self):
        source, _, _ = self.source()
        client = self.followup(source)
        gate = self.gate()
        self.service.connector_timeout_ms = 10

        def slow(probe, cancel):
            gate.wait(3)
            return ConnectorOutcome(reference="late-confirmed")

        self.probe_behavior = slow
        self.probe(client)
        self.wait_for(
            lambda: (
                self.service.connector_store.get(client.task_id, "history-check").reason
                == "connector_timeout"
            )
        )
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "uncertain",
        )
        gate.set()
        self.assertEqual(self.finished(client, "history-check").state, "succeeded")
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").reference,
            "late-confirmed",
        )

    def test_failure_never_downgrades_confirmed_source(self):
        source, _, _ = self.source()
        client = self.followup(source)
        gate = self.gate()

        def slow(probe, cancel):
            gate.wait(3)
            return ConnectorOutcome("uncertain")

        self.probe_behavior = slow
        self.probe(client)
        self.wait_for(lambda: bool(self.probes))
        self.service.connector_store.update(
            source.task_id, "document-1", "succeeded", reference="original-late-reply"
        )
        gate.set()
        self.finished(client, "history-check")
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").reference,
            "original-late-reply",
        )
        self.assertEqual(len(self.calls), 1)

    def test_probe_and_source_confirmation_are_atomic_on_journal_failure(self):
        source, _, _ = self.source()
        client = self.followup(source)
        gate = self.gate()

        def slow(probe, cancel):
            gate.wait(3)
            return ConnectorOutcome(reference="confirmed")

        self.probe_behavior = slow
        self.probe(client)
        self.wait_for(lambda: bool(self.probes))
        append = self.service.store._append

        def fail_source(task_id, kind, payload):
            if task_id == source.task_id:
                raise sqlite3.OperationalError("synthetic journal failure")
            append(task_id, kind, payload)

        with patch.object(self.service.store, "_append", side_effect=fail_source):
            gate.set()
            self.wait_for(lambda: not self.service._connector_jobs)
        self.assertTrue(self.service.failure)
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "uncertain",
        )
        self.assertNotEqual(
            self.service.connector_store.get(client.task_id, "history-check").state,
            "succeeded",
        )

    def test_probe_creation_failure_rolls_back_mapping_and_prevents_dispatch(self):
        source, _, _ = self.source()
        client = self.followup(source)
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.probe(client)
        self.assertTrue(self.service.failure)
        self.assertFalse(self.probes)
        self.assertFalse(self.service.connector_store.list(client.task_id))
        self.assertEqual(
            self.service.store._db.execute(
                "SELECT count(*) FROM connector_probes"
            ).fetchone()[0],
            0,
        )

    def test_restart_retains_probe_mapping_without_network_or_authority(self):
        source, _, original = self.source()
        client = self.followup(source)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome("uncertain")
        self.probe(client)
        self.finished(client, "history-check")
        self.service.close()
        sip.delete(self.service)
        before = len(self.calls), len(self.probes)
        self.service = TaskService(self.path, connectors=self.registry)
        self.assertEqual(
            self.service.connector_store.probe_source(client.task_id, "history-check"),
            (source.task_id, original.request_id),
        )
        self.assertFalse(self.service._grants)
        self.assertFalse(self.service._connector_jobs)
        self.assertEqual(before, (len(self.calls), len(self.probes)))

    def test_late_original_worker_cannot_erase_confirmed_provider_evidence(self):
        gate = self.gate()
        self.behavior = lambda request, cancel: (
            gate.wait(3),
            ConnectorOutcome("uncertain"),
        )[1]
        source, _, artifact = self.client()
        self.service.connector_timeout_ms = 10
        self.call(source, artifact)
        self.wait_for(
            lambda: (
                self.service.connector_store.get(source.task_id, "document-1").state
                == "uncertain"
            )
        )
        self.service._finish_task(source.task_id)
        client = self.followup(source)
        self.probe(client)
        self.finished(client, "history-check")
        gate.set()
        self.finished(source)
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "succeeded",
        )
        self.assertEqual(len(self.calls), 1)

    def test_confirmed_absence_can_release_guard_only_under_new_write_permission(self):
        source, draft, _ = self.source()
        client = self.followup(source, draft)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome("not_applied")
        self.probe(client)
        self.finished(client, "history-check")
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "not_applied",
        )
        # Read authority remains insufficient even after confirmed absence.
        with self.assertRaises(TaskPermissionError):
            self.call(client, self.service.store.artifacts(client.task_id)[0])
        self.service._finish_task(client.task_id)
        next_task = self.service._create("Make a newly approved write")
        self.service._request_recovery(next_task.task_id)
        self.service.approve_recovery(
            next_task.task_id, source.task_id, [draft.artifact_id]
        )
        grant = self.service._request_grant(
            next_task.task_id,
            {
                "actions": ["artifact", "app"],
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
        self.service.approve_grant(next_task.task_id, grant.grant_id)
        self.call(
            self.service.client(next_task.task_id),
            self.service.store.artifacts(next_task.task_id)[0],
        )
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(len(self.calls), 2)

    def test_absence_does_not_release_guard_while_original_worker_is_running(self):
        gate = self.gate()
        self.behavior = lambda request, cancel: (
            gate.wait(3),
            ConnectorOutcome("uncertain"),
        )[1]
        source, _, draft = self.client()
        self.service.connector_timeout_ms = 10
        self.call(source, draft)
        self.wait_for(
            lambda: (
                self.service.connector_store.get(source.task_id, "document-1").state
                == "uncertain"
            )
        )
        self.service._finish_task(source.task_id)
        checker = self.followup(source)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome("not_applied")
        self.probe(checker)
        checked = self.finished(checker, "history-check")
        self.assertEqual(checked.state, "uncertain")
        self.assertEqual(checked.reason, "original_operation_running")
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "uncertain",
        )
        writer = self.followup(source, draft, approve=False)
        grant = self.service._request_grant(
            writer.task_id,
            {
                "actions": ["artifact", "app"],
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
        self.service.approve_grant(writer.task_id, grant.grant_id)
        with self.assertRaises(TaskPermissionError):
            self.call(writer, self.service.store.artifacts(writer.task_id)[0])
        self.assertEqual(len(self.calls), 1)
        gate.set()
        self.finished(source)

    def test_account_disconnect_cancels_read_probe_without_changing_original(self):
        source, _, _ = self.source()
        client = self.followup(source)
        gate = self.gate()
        observed = []

        def slow(probe, cancel):
            gate.wait(3)
            observed.append(cancel.is_set())
            raise RuntimeError("Disconnected synthetic account")

        self.probe_behavior = slow
        self.probe(client)
        self.wait_for(lambda: bool(self.probes))
        self.service.disconnect_connector("documents")
        gate.set()
        self.finished(client, "history-check")
        self.assertEqual(observed, [True])
        self.assertEqual(
            self.service.connector_store.get(source.task_id, "document-1").state,
            "uncertain",
        )

    def test_budget_exhaustion_blocks_new_probe_and_further_provider_requests(self):
        source, _, _ = self.source()
        client = self.followup(source, max_operations=1)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome("uncertain")
        self.probe(client)
        self.finished(client, "history-check")
        with self.assertRaises(TaskPermissionError):
            self.probe(client, request_id="another-check")
        self.assertEqual(len(self.probes), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.service.failure)

    def test_schema_six_and_inflight_probe_restart_keep_uncertainty_without_replay(
        self,
    ):
        source, _, _ = self.source()
        self.service.close()
        sip.delete(self.service)
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE connector_probes")
            database.execute("PRAGMA user_version = 6")
        self.service = TaskService(self.path, connectors=self.registry)
        client = self.followup(source)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome("uncertain")
        self.probe(client)
        self.finished(client, "history-check")
        # Simulate process exit between probe dispatch and receipt persistence.
        self.service.connector_store.update(
            client.task_id, "history-check", "reconciling"
        )
        self.service.close()
        sip.delete(self.service)
        before = len(self.calls), len(self.probes)
        self.service = TaskService(self.path, connectors=self.registry)
        self.assertEqual(
            self.service.store._db.execute("PRAGMA user_version").fetchone()[0], 10
        )
        self.assertEqual(
            self.service.connector_store.get(client.task_id, "history-check").state,
            "uncertain",
        )
        self.assertEqual(self.service.store.get(client.task_id).status, "interrupted")
        self.assertEqual(before, (len(self.calls), len(self.probes)))
        self.assertFalse(self.service._grants)

    def test_archived_probe_receipt_is_not_mistaken_for_original_operation(self):
        source, _, _ = self.source()
        first = self.followup(source)
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome("uncertain")
        self.probe(first)
        self.finished(first, "history-check")
        self.service._finish_task(first.task_id)
        second = self.followup(first)
        with self.assertRaisesRegex(ValueError, "original task"):
            self.probe(second, source_request_id="history-check")
        self.assertEqual(len(self.probes), 1)

    def test_ui_explains_read_only_permission_and_can_check_shared_receipt(self):
        source, _, _ = self.source()
        client = self.followup(source, approve=False)
        grant = self.service._request_grant(
            client.task_id,
            {
                "actions": ["app"],
                "connector_permissions": [
                    {
                        "connector": "documents",
                        "tool": "create",
                        "effect": "read",
                        "destination": "workspace-a",
                    }
                ],
            },
        )
        approval = TaskApprovalDialog(
            self.service, self.service.store.get(client.task_id), grant
        )
        try:
            text = "\n".join(label.text() for label in approval.findChildren(QLabel))
            text += "\n".join(
                edit.toPlainText() for edit in approval.findChildren(QPlainTextEdit)
            )
            self.assertIn("Check a previous result (read only)", text)
        finally:
            sip.delete(approval)
        self.service.approve_grant(client.task_id, grant.grant_id)
        panel = TaskDialog(self.service)
        try:
            row = next(
                i
                for i, (task, _) in enumerate(panel._rows)
                if task.task_id == client.task_id
            )
            panel.table.selectRow(row)
            self.assertIn("Previous WRITE", panel.apps.item(0, 0).text())
            self.assertTrue(panel.reconcile_app_button.isEnabled())
            panel.reconcile_app_button.click()
            self.wait_for(lambda: not self.service._connector_jobs)
            self.assertEqual(len(self.probes), 1)
            self.assertEqual(len(self.calls), 1)
        finally:
            panel.close()
            sip.delete(panel)


if __name__ == "__main__":
    unittest.main()
