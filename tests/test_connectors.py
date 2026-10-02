"""Exact tool/destination grants, write identity, cancellation and recovery."""

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application

from services.connector_store import request_fingerprint
from services.connectors import (
    ConnectorOutcome,
    ConnectorRegistry,
    ConnectorRequest,
    ConnectorTool,
)
from services.task_policy import TaskPermissionError, TaskScope
from services.task_service import TaskService


class ConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.registry = ConnectorRegistry()
        self.calls, self.probes = [], []
        self.behavior = lambda request, cancel: ConnectorOutcome(
            reference="doc-" + request.request_id
        )
        self.probe_behavior = lambda probe, cancel: ConnectorOutcome(
            reference="doc-" + probe.request_id
        )
        self.registry.register(
            ConnectorTool(
                "documents",
                "create",
                "write",
                "Create a document in the approved workspace",
                True,
                self.execute,
                self.reconcile,
            )
        )
        self.registry.register(
            ConnectorTool(
                "documents",
                "read",
                "read",
                "Read the approved document",
                False,
                lambda request, cancel: ConnectorOutcome(text="REMOTE_DOCUMENT"),
            )
        )
        self.service = TaskService(self.path, connectors=self.registry)
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.wait_for(lambda: not self.service._connector_jobs)
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def execute(self, request, cancel):
        self.calls.append(request)
        return self.behavior(request, cancel)

    def reconcile(self, probe, cancel):
        self.probes.append(probe)
        return self.probe_behavior(probe, cancel)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("Connector fixture did not settle")

    def resolve(self, future):
        self.wait_for(future.done)
        return future.result()

    def client(
        self,
        *,
        effect="write",
        tool="create",
        destination="workspace-a",
        actions=("artifact", "app"),
        **limits,
    ):
        task = self.service._create("Use a synthetic app")
        scope = {
            "actions": list(actions),
            "connector_permissions": [
                {
                    "connector": "documents",
                    "tool": tool,
                    "effect": effect,
                    "destination": destination,
                }
            ],
            **limits,
        }
        grant = self.service._request_grant(task.task_id, scope)
        self.service.approve_grant(task.task_id, grant.grant_id)
        client = self.service.client(task.task_id)
        artifact = (
            self.resolve(
                client.submit(
                    "artifact.store", title="Draft", content="PRIVATE_DOCUMENT_BODY"
                )
            )
            if "artifact" in actions
            else None
        )
        return client, grant.grant_id, artifact

    def call(self, client, artifact, **changes):
        args = {
            "connector": "documents",
            "tool": "create",
            "request_id": "document-1",
            "destination": "workspace-a",
            "artifact_id": artifact.artifact_id,
        }
        args.update(changes)
        return self.resolve(client.submit("app.call_tool", **args))

    def finished(self, client, request_id="document-1"):
        self.wait_for(
            lambda: (client.task_id, request_id) not in self.service._connector_jobs
        )
        return self.service.connector_store.get(client.task_id, request_id)

    def gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def test_default_registry_discovery_needs_no_grant_or_account(self):
        service = TaskService(Path(self.directory.name) / "empty.sqlite")
        try:
            task = service._create("Discover apps")
            self.assertEqual(service._app_tools(task.task_id), [])
        finally:
            service.close()
            sip.delete(service)

    def test_remote_claimed_read_effect_cannot_downgrade_trusted_write(self):
        with self.assertRaises(ValueError):
            self.client(effect="read")
        with self.assertRaises(ValueError):
            self.client(actions=("app",))
        self.assertFalse(self.calls)

    def test_exact_destination_artifact_ownership_and_receipt_identity(self):
        client, _, artifact = self.client()
        with self.assertRaises(TaskPermissionError):
            self.call(client, artifact, destination="workspace-b")
        other, _, foreign = self.client()
        with self.assertRaises(KeyError):
            self.call(client, foreign)
        self.call(client, artifact)
        result = self.finished(client)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(self.service.store.get(client.task_id).status, "running")
        self.assertEqual(result.reference, "doc-document-1")
        self.assertEqual(self.calls[0].artifact.content, "PRIVATE_DOCUMENT_BODY")
        self.assertEqual(self.call(client, artifact), result)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(KeyError):
            self.resolve(other.submit("app.status", request_id="document-1"))
        self.assertNotIn(
            "PRIVATE_DOCUMENT_BODY", repr(self.service.store.events(client.task_id))
        )

    def test_changed_inputs_under_one_request_id_are_not_dispatched(self):
        client, _, artifact = self.client()
        self.call(client, artifact)
        self.finished(client)
        changed = self.resolve(
            client.submit("artifact.store", title="Revision", content="CHANGED_BODY")
        )
        with self.assertRaises(ValueError):
            self.call(client, changed)
        self.assertEqual(len(self.calls), 1)

    def test_two_tasks_using_one_local_request_id_have_distinct_provider_ids(self):
        first, _, first_artifact = self.client()
        second, _, second_artifact = self.client()
        self.call(first, first_artifact)
        self.call(second, second_artifact)
        first_receipt, second_receipt = self.finished(first), self.finished(second)
        self.assertNotEqual(first_receipt.operation_id, second_receipt.operation_id)
        self.assertEqual(
            {call.operation_id for call in self.calls},
            {first_receipt.operation_id, second_receipt.operation_id},
        )

    def test_draft_deletion_before_dispatch_prevents_sending_its_copy(self):
        client, _, artifact = self.client()
        self.service._app_call(
            client.task_id,
            "documents",
            "create",
            "document-1",
            "workspace-a",
            artifact.artifact_id,
        )
        self.service.store.delete_artifact(client.task_id, artifact.artifact_id)
        self.assertEqual(self.finished(client).state, "cancelled")
        self.assertFalse(self.calls)

    def test_expiry_and_exhausted_budgets_prevent_further_calls(self):
        gate = self.gate()
        self.behavior = lambda request, cancel: (
            gate.wait(3),
            ConnectorOutcome(reference="late"),
        )[1]
        client, _, artifact = self.client()
        self.call(client, artifact)
        self.wait_for(lambda: bool(self.calls))
        live = self.service._grants[client.task_id][1]
        live.deadline = time.monotonic() - 1
        self.service._expire_grants()
        self.assertEqual(
            self.service.connector_store.get(client.task_id, "document-1").state,
            "uncertain",
        )
        gate.set()
        self.finished(client)
        limited, _, artifact = self.client(max_operations=1)
        with self.assertRaises(TaskPermissionError):
            self.call(limited, artifact)

    def test_user_receipts_are_retained_plain_text_and_scoped_after_completion(self):
        from ui.task_dialog import TaskDialog

        self.behavior = lambda request, cancel: ConnectorOutcome(
            reference="created", text="<script>PRIVATE_RESULT</script>"
        )
        client, _, artifact = self.client()
        self.call(client, artifact)
        self.finished(client)
        self.resolve(client.submit("task.finish"))
        dialog = TaskDialog(self.service)
        try:
            self.assertEqual(dialog.apps.rowCount(), 1)
            self.assertIn(
                "<script>PRIVATE_RESULT</script>", dialog.app_preview.toPlainText()
            )
            self.assertNotIn("PRIVATE_RESULT", dialog.activity.toPlainText())
            self.assertFalse(dialog.reconcile_app_button.isEnabled())
            self.assertFalse(dialog.cancel_app_button.isEnabled())
            other = self.service._create("Separate task")
            dialog.refresh()
            row = next(
                i
                for i, (task, _) in enumerate(dialog._rows)
                if task.task_id == other.task_id
            )
            dialog.table.selectRow(row)
            self.assertEqual(dialog.apps.rowCount(), 0)
            self.assertEqual(dialog.app_preview.toPlainText(), "")
        finally:
            dialog.close()

    def test_user_can_reconcile_uncertain_results_through_the_receipt_panel(self):
        from ui.task_dialog import TaskDialog

        def uncertain(request, cancel):
            raise TimeoutError("Lost acknowledgement")

        self.behavior = uncertain
        client, _, artifact = self.client()
        self.call(client, artifact)
        self.finished(client)
        dialog = TaskDialog(self.service)
        try:
            self.assertTrue(dialog.reconcile_app_button.isEnabled())
            dialog.reconcile_app_button.click()
            self.finished(client)
            dialog.refresh_activity()
            self.assertEqual(dialog.apps.item(0, 2).text(), "succeeded")
            self.assertFalse(dialog.reconcile_app_button.isEnabled())
            self.assertEqual(len(self.calls), 1)
        finally:
            dialog.close()

    def test_uncertain_write_is_not_replayed_and_can_be_reconciled_read_only(self):
        def uncertain(request, cancel):
            raise TimeoutError("SECRET_PROVIDER_DIAGNOSTIC")

        self.behavior = uncertain
        client, _, artifact = self.client()
        self.call(client, artifact)
        result = self.finished(client)
        self.assertEqual(result.state, "uncertain")
        self.assertEqual(result.reason, "connector_outcome_unknown")
        self.call(client, artifact)
        self.assertEqual(len(self.calls), 1)
        probing = self.resolve(client.submit("app.reconcile", request_id="document-1"))
        self.assertEqual(probing.state, "reconciling")
        self.assertEqual(self.finished(client).state, "succeeded")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.probes), 1)
        self.assertFalse(hasattr(self.probes[0], "artifact"))
        self.assertNotIn(
            "SECRET_PROVIDER_DIAGNOSTIC",
            repr(self.service.store.events(client.task_id)),
        )

    def test_cancel_before_dispatch_sends_nothing(self):
        client, _, artifact = self.client()
        self.service._app_call(
            client.task_id,
            "documents",
            "create",
            "document-1",
            "workspace-a",
            artifact.artifact_id,
        )
        cancelled = self.service._app_cancel(client.task_id, "document-1")
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(self.finished(client).state, "cancelled")
        self.assertFalse(self.calls)

    def test_cancellation_after_dispatch_is_uncertain_until_observed_completion(self):
        gate = self.gate()

        def delayed(request, cancel):
            gate.wait(3)
            return ConnectorOutcome(reference="actually-created")

        self.behavior = delayed
        client, _, artifact = self.client()
        self.call(client, artifact)
        self.wait_for(lambda: bool(self.calls))
        cancelled = self.resolve(client.submit("app.cancel", request_id="document-1"))
        self.assertEqual(cancelled.state, "uncertain")
        with self.assertRaises(ValueError):
            self.resolve(client.submit("app.reconcile", request_id="document-1"))
        gate.set()
        self.assertEqual(self.finished(client).reference, "actually-created")

    def test_timeout_bounds_access_and_keeps_worker_capacity_until_it_finishes(self):
        gate = self.gate()
        self.service.connector_timeout_ms = 30
        self.behavior = lambda request, cancel: (
            gate.wait(3),
            ConnectorOutcome(reference="late"),
        )[1]
        client, _, artifact = self.client()
        self.call(client, artifact)
        self.wait_for(
            lambda: (
                self.service.connector_store.get(client.task_id, "document-1").state
                == "uncertain"
            )
        )
        with self.assertRaises(ValueError):
            self.call(client, artifact, request_id="document-2")
        gate.set()
        self.assertEqual(self.finished(client).state, "succeeded")

    def test_revocation_stops_new_calls_and_retains_observed_late_write_for_user(self):
        gate = self.gate()
        self.behavior = lambda request, cancel: (
            gate.wait(3),
            ConnectorOutcome(reference="late"),
        )[1]
        client, grant, artifact = self.client()
        self.call(client, artifact)
        self.wait_for(lambda: bool(self.calls))
        self.service.revoke_grant(client.task_id, grant)
        self.assertEqual(
            self.service.connector_store.get(client.task_id, "document-1").state,
            "uncertain",
        )
        with self.assertRaises(TaskPermissionError):
            self.call(client, artifact, request_id="document-2")
        gate.set()
        self.assertEqual(self.finished(client).state, "succeeded")

    def test_journal_failure_before_dispatch_prevents_remote_call(self):
        client, _, artifact = self.client()
        with patch.object(
            self.service.connector_store,
            "update",
            side_effect=sqlite3.OperationalError("disk"),
        ):
            self.call(client, artifact)
            self.wait_for(lambda: bool(self.service.failure))
        self.assertFalse(self.calls)

    def test_result_journal_failure_does_not_return_a_false_success(self):
        gate = self.gate()
        self.behavior = lambda request, cancel: (
            gate.wait(3),
            ConnectorOutcome(reference="created"),
        )[1]
        client, _, artifact = self.client()
        self.call(client, artifact)
        self.wait_for(lambda: bool(self.calls))
        with patch.object(
            self.service.connector_store,
            "update",
            side_effect=sqlite3.OperationalError("disk"),
        ):
            gate.set()
            self.wait_for(lambda: bool(self.service.failure))
        with self.assertRaises(RuntimeError):
            self.resolve(client.submit("app.status", request_id="document-1"))
        self.assertEqual(
            self.service.connector_store.get(client.task_id, "document-1").state,
            "dispatched",
        )

    def test_read_tools_return_bounded_text_without_artifact_permission(self):
        client, _, _ = self.client(
            effect="read", tool="read", destination="document-a", actions=("app",)
        )
        self.resolve(
            client.submit(
                "app.call_tool",
                connector="documents",
                tool="read",
                request_id="read-1",
                destination="document-a",
            )
        )
        result = self.finished(client, "read-1")
        self.assertEqual(result.text, "REMOTE_DOCUMENT")
        self.assertNotIn(
            "REMOTE_DOCUMENT", repr(self.service.store.events(client.task_id))
        )

    def test_restart_marks_unfinished_writes_uncertain_and_never_replays(self):
        client, _, artifact = self.client()
        request = ConnectorRequest(
            "restart-1",
            "documents",
            "create",
            "write",
            "workspace-a",
            self.service.store.artifact(client.task_id, artifact.artifact_id),
        )
        self.service.connector_store.create(
            client.task_id,
            request,
            request_fingerprint(
                "documents", "create", "write", "workspace-a", request.artifact
            ),
        )
        self.service.connector_store.update(
            client.task_id, request.request_id, "dispatched"
        )
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.path, connectors=self.registry)
        receipt = self.service.connector_store.get(client.task_id, request.request_id)
        self.assertEqual((receipt.state, receipt.reason), ("uncertain", "restart"))
        self.assertEqual(self.service.store.get(client.task_id).status, "interrupted")
        self.assertFalse(self.calls)

    def test_receipt_limit_and_invalid_scope_inputs(self):
        for scope in (
            {"actions": ["app"]},
            {
                "actions": ["app"],
                "connector_permissions": [
                    {
                        "connector": "documents",
                        "tool": "create",
                        "effect": "write",
                        "destination": "*",
                    }
                ],
            },
            {
                "actions": ["app"],
                "connector_permissions": [
                    {
                        "connector": "../outside",
                        "tool": "create",
                        "effect": "write",
                        "destination": "workspace",
                    }
                ],
            },
        ):
            with self.assertRaises(ValueError):
                TaskScope.parse(scope)
        client, _, artifact = self.client()
        body = self.service.store.artifact(client.task_id, artifact.artifact_id)
        for index in range(50):
            request = ConnectorRequest(
                f"request-{index}", "documents", "create", "write", "workspace-a", body
            )
            self.service.connector_store.create(
                client.task_id,
                request,
                request_fingerprint(
                    "documents", "create", "write", "workspace-a", body
                ),
            )
        with self.assertRaises(ValueError):
            self.call(client, artifact)
        self.assertFalse(self.calls)
