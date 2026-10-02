"""Explicit source disclosure, fresh authority, atomic imports and write guards."""

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QLabel, QPushButton
from qt_test_support import application

from services.agent_tools import TOOLS_BY_NAME
from services.connectors import ConnectorOutcome, ConnectorRegistry, ConnectorTool
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.task_approval import TaskApprovalDialog
from ui.task_recovery import TaskRecoveryDialog


class TaskRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.calls = []
        self.registry = ConnectorRegistry()
        self.registry.register(
            ConnectorTool(
                "fixture",
                "send",
                "send",
                "Send to the approved recipient",
                True,
                self.execute,
            )
        )
        self.service = TaskService(self.path, connectors=self.registry)
        self.dialogs = []

    def execute(self, request, cancelled):
        self.calls.append(request)
        return ConnectorOutcome(reference="synthetic-message")

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.close()
                sip.delete(dialog)
        self.wait_for(lambda: not self.service._connector_jobs)
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("Recovery fixture timed out")

    def scope(self):
        return {
            "actions": ["artifact", "app"],
            "connector_permissions": [
                {
                    "connector": "fixture",
                    "tool": "send",
                    "effect": "send",
                    "destination": "email:recipient@example.test",
                }
            ],
        }

    def source(self, *, state=None, remember="once", source_events=False, extra=False):
        task = self.service._create("Original synthetic work")
        grant = self.service._request_grant(task.task_id, self.scope())
        self.service.approve_grant(task.task_id, grant.grant_id, remember=remember)
        sequences = []
        if source_events:
            self.service.store.record_event(
                task.task_id,
                "page.inspected",
                {
                    "origin": "https://example.test",
                    "snapshot_id": "historical-reference",
                },
            )
            sequences.append(self.service.store.events(task.task_id)[-1].sequence)
        draft = self.service._store_artifact(
            task.task_id, "Synthetic draft", "SYNTHETIC_BODY", source_events=sequences
        )
        for index in range(int(extra)):
            self.service._store_artifact(
                task.task_id, f"Not shared {index}", "UNSELECTED_PRIVATE_BODY"
            )
        if state:
            receipt = self.service._app_call(
                task.task_id,
                "fixture",
                "send",
                "original-send",
                "email:recipient@example.test",
                draft.artifact_id,
            )
            self.wait_for(lambda: not self.service._connector_jobs)
            self.service.connector_store.update(
                task.task_id,
                receipt.request_id,
                state,
                reference="synthetic-old-result",
            )
        self.service._finish_task(task.task_id)
        return task, draft

    def followup(self, source, draft=None, *, evidence=True):
        task = self.service._create("Continue selected work")
        self.service._request_recovery(task.task_id)
        self.service.approve_recovery(
            task.task_id,
            source.task_id,
            [draft.artifact_id] if draft else [],
            evidence=evidence,
        )
        return task

    def approve(self, task):
        grant = self.service._request_grant(task.task_id, self.scope())
        self.assertEqual(grant.status, "requested")
        self.service.approve_grant(task.task_id, grant.grant_id)
        return grant

    def imported(self, task):
        return self.service.store.artifacts(task.task_id)[0]

    def send(self, task, draft, request_id="followup-send"):
        return self.service._app_call(
            task.task_id,
            "fixture",
            "send",
            request_id,
            "email:recipient@example.test",
            draft.artifact_id,
        )

    def test_request_never_enumerates_archives_or_accepts_a_source_from_agent(self):
        self.source()
        task = self.service._create("Ask for historical work")
        result = self.service._request_recovery(task.task_id)
        self.assertEqual(
            result, {"status": "requested", "source_task_id": None, "context": {}}
        )
        self.assertFalse(self.service.store.artifacts(task.task_id))
        with self.assertRaises(ValueError):
            TOOLS_BY_NAME["steed_task_recover"].validate({"source_task_id": "foreign"})
        self.assertNotIn("approve_recovery", self.service.broker._handlers)
        self.assertNotIn("deny_recovery", self.service.broker._handlers)

    def test_pending_recovery_precedes_grants_and_deny_shares_nothing(self):
        task = self.service._create("Choose material")
        self.service._request_recovery(task.task_id)
        with self.assertRaises(ValueError):
            self.service._request_grant(task.task_id, self.scope())
        self.service.deny_recovery(task.task_id)
        self.assertEqual(self.service._recovery_info(task.task_id)["status"], "denied")
        self.assertFalse(self.service.store.artifacts(task.task_id))
        self.assertEqual(
            self.service._request_grant(task.task_id, self.scope()).status, "requested"
        )

    def test_selection_copies_only_selected_drafts_and_never_imports_live_targets(self):
        source, draft = self.source(source_events=True, extra=True)
        task = self.followup(source, draft)
        imported = self.imported(task)
        self.assertEqual(len(self.service.store.artifacts(task.task_id)), 1)
        self.assertNotEqual(imported.artifact_id, draft.artifact_id)
        self.assertEqual(imported.source_events, ())
        context = self.service._recovery_info(task.task_id)["context"]
        self.assertEqual(context["source_events"][0]["task_id"], source.task_id)
        self.assertEqual(
            context["imported_artifacts"][0]["source_artifact_id"], draft.artifact_id
        )
        self.assertEqual(self.service.store.grants(task.task_id), [])
        self.assertFalse(self.service._sessions)
        with self.assertRaises(TaskPermissionError):
            self.service._read_artifact(task.task_id, imported.artifact_id)
        self.approve(task)
        self.assertEqual(
            self.service._read_artifact(task.task_id, imported.artifact_id).content,
            "SYNTHETIC_BODY",
        )
        with self.assertRaises(KeyError):
            self.service._read_artifact(task.task_id, draft.artifact_id)

    def test_recovery_always_requires_fresh_approval_even_with_a_saved_rule(self):
        source, draft = self.source(remember="global")
        task = self.followup(source, draft)
        grant = self.service._request_grant(task.task_id, self.scope())
        self.assertEqual(grant.status, "requested")
        self.assertIsNone(self.service.store.grant_rule_id(grant.grant_id))
        dialog = TaskApprovalDialog(self.service, task, grant)
        self.dialogs.append(dialog)
        self.assertEqual(dialog.remember.currentData(), "once")
        self.assertIn(
            "Follow-up task",
            "\n".join(label.text() for label in dialog.findChildren(QLabel)),
        )

    def test_recovery_rejects_active_source_foreign_draft_and_invalid_selection(self):
        source, draft = self.source()
        active = self.service._create("Still active")
        task = self.service._create("Requester")
        self.service._request_recovery(task.task_id)
        for source_id, ids, evidence in (
            (active.task_id, [], True),
            (source.task_id, ["foreign"], True),
            (source.task_id, [draft.artifact_id] * 2, True),
            (source.task_id, [], False),
            (source.task_id, [draft.artifact_id], "yes"),
        ):
            with (
                self.subTest(source_id=source_id, ids=ids),
                self.assertRaises((ValueError, KeyError)),
            ):
                self.service.approve_recovery(
                    task.task_id, source_id, ids, evidence=evidence
                )
        self.assertEqual(
            self.service.store.recovery(task.task_id)["status"], "requested"
        )
        self.assertFalse(self.service.store.artifacts(task.task_id))

    def test_recovery_cannot_be_added_after_any_grant_or_work(self):
        task = self.service._create("Normal task")
        self.service._request_grant(task.task_id, self.scope())
        with self.assertRaises(ValueError):
            self.service._request_recovery(task.task_id)

    def test_failed_journal_rolls_back_selection_import_and_guards(self):
        source, draft = self.source(state="succeeded")
        task = self.service._create("Atomic follow-up")
        self.service._request_recovery(task.task_id)
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.service.approve_recovery(
                    task.task_id, source.task_id, [draft.artifact_id]
                )
        self.assertTrue(self.service.failure)
        self.assertEqual(
            self.service.store.recovery(task.task_id)["status"], "requested"
        )
        self.assertFalse(self.service.store.artifacts(task.task_id))
        self.assertEqual(
            self.service.store._db.execute(
                "SELECT count(*) FROM recovery_effects WHERE task_id = ?",
                (task.task_id,),
            ).fetchone()[0],
            0,
        )

    def test_corrupt_draft_checksum_prevents_copying_or_disclosure(self):
        source, draft = self.source()
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE artifacts SET content = 'CHANGED' WHERE artifact_id = ?",
                (draft.artifact_id,),
            )
        task = self.service._create("Verify retained data")
        self.service._request_recovery(task.task_id)
        with self.assertRaises(ValueError):
            self.service.approve_recovery(
                task.task_id, source.task_id, [draft.artifact_id]
            )
        self.assertFalse(self.service.store.artifacts(task.task_id))

    def test_completed_and_uncertain_writes_cannot_be_replayed_with_new_request_ids(
        self,
    ):
        for state in ("succeeded", "uncertain"):
            source, draft = self.source(state=state)
            task = self.followup(source, draft)
            self.approve(task)
            before = len(self.calls)
            with self.assertRaises(TaskPermissionError) as failure:
                self.send(task, self.imported(task), request_id="new-" + state)
            self.assertEqual(failure.exception.code, "recovery_write_already_recorded")
            self.assertEqual(len(self.calls), before)
            self.assertEqual(self.service.connector_store.list(task.task_id), [])

    def test_recreating_identical_body_still_blocks_but_new_content_can_run(self):
        source, draft = self.source(state="uncertain")
        task = self.followup(source, draft)
        self.approve(task)
        identical = self.service._store_artifact(
            task.task_id, "Different title", "SYNTHETIC_BODY"
        )
        with self.assertRaises(TaskPermissionError):
            self.send(task, identical)
        changed = self.service._store_artifact(
            task.task_id, "New work", "NEW_SYNTHETIC_BODY"
        )
        self.send(task, changed)
        self.wait_for(lambda: not self.service._connector_jobs)
        self.assertEqual(self.calls[-1].artifact.content, "NEW_SYNTHETIC_BODY")

    def test_omitting_evidence_and_deleting_original_do_not_remove_write_guard(self):
        source, draft = self.source(state="succeeded")
        self.service.store.delete_artifact(source.task_id, draft.artifact_id)
        task = self.followup(source, evidence=True)
        self.approve(task)
        recreated = self.service._store_artifact(
            task.task_id, "Recreated", "SYNTHETIC_BODY"
        )
        with self.assertRaises(TaskPermissionError):
            self.send(task, recreated)
        source2, draft2 = self.source(state="uncertain")
        task2 = self.followup(source2, draft2, evidence=False)
        self.approve(task2)
        self.assertEqual(
            self.service._recovery_info(task2.task_id)["context"]["app_receipts"], []
        )
        self.assertIsNone(
            self.service._recovery_info(task2.task_id)["context"]["previous_scope"]
        )
        with self.assertRaises(TaskPermissionError):
            self.send(task2, self.imported(task2))

    def test_previously_not_applied_or_cancelled_operations_can_run_once(self):
        for state in ("not_applied", "cancelled"):
            source, draft = self.source(state=state)
            task = self.followup(source, draft)
            self.approve(task)
            receipt = self.send(task, self.imported(task))
            self.wait_for(lambda: not self.service._connector_jobs)
            self.assertEqual(
                self.service.connector_store.get(
                    task.task_id, receipt.request_id
                ).state,
                "succeeded",
            )
            before = len(self.calls)
            self.send(task, self.imported(task))
            self.assertEqual(len(self.calls), before)

    def test_write_guards_follow_ancestry_and_current_receipt_outcomes(self):
        source, draft = self.source(state="uncertain")
        first = self.followup(source, draft)
        imported = self.imported(first)
        self.service._finish_task(first.task_id)
        second = self.followup(first, imported)
        self.approve(second)
        self.service.connector_store.update(
            source.task_id, "original-send", "succeeded"
        )
        with self.assertRaises(TaskPermissionError):
            self.send(second, self.imported(second))
        self.assertEqual(len(self.service.store.recovery_ancestors(second.task_id)), 3)

    def test_restart_retains_copies_and_guard_without_restoring_grants_or_requests(
        self,
    ):
        source, draft = self.source(state="uncertain")
        task = self.followup(source, draft)
        self.approve(task)
        pending = self.service._create("Pending picker")
        self.service._request_recovery(pending.task_id)
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.path, connectors=self.registry)
        self.assertEqual(self.service.store.get(task.task_id).status, "interrupted")
        self.assertEqual(
            self.service.store.recovery(pending.task_id)["status"], "ended"
        )
        self.assertFalse(self.service._grants)
        self.assertFalse(self.service._sessions)
        next_task = self.followup(task, self.imported(task))
        self.approve(next_task)
        with self.assertRaises(TaskPermissionError):
            self.send(next_task, self.imported(next_task))

    def test_popup_requires_explicit_draft_selection_and_handles_cancelled_request(
        self,
    ):
        source, draft = self.source()
        task = self.service._create("Continue safely")
        self.service._request_recovery(task.task_id)
        dialog = TaskRecoveryDialog(self.service, task)
        self.dialogs.append(dialog)
        self.assertEqual(dialog.source.currentData(), source.task_id)
        self.assertEqual(dialog.artifacts.item(0).checkState(), Qt.CheckState.Unchecked)
        self.assertTrue(
            next(
                button
                for button in dialog.findChildren(QPushButton)
                if button.text() == "Deny"
            ).isDefault()
        )
        dialog.artifacts.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.decide(True)
        self.assertEqual(self.imported(task).sha256, draft.sha256)
        second = self.service._create("Cancel pending source selection")
        self.service._request_recovery(second.task_id)
        popup = TaskRecoveryDialog(self.service, second)
        self.dialogs.append(popup)
        popup.show()
        self.service.cancel_task(second.task_id)
        self.app.processEvents()
        self.assertTrue(sip.isdeleted(popup) or not popup.isVisible())

    def test_selection_limit_and_snapshot_overflow_roll_back_without_sharing(self):
        source, draft = self.source(extra=10)
        task = self.service._create("Bounded selected material")
        self.service._request_recovery(task.task_id)
        ids = [
            item.artifact_id for item in self.service.store.artifacts(source.task_id)
        ]
        with self.assertRaises(ValueError):
            self.service.approve_recovery(task.task_id, source.task_id, ids)
        self.assertFalse(self.service.store.artifacts(task.task_id))
        with patch(
            "services.task_service.recovery_snapshot",
            return_value={"evidence_included": True, "padding": "x" * (128 * 1024)},
        ):
            with self.assertRaises(ValueError):
                self.service.approve_recovery(
                    task.task_id, source.task_id, [draft.artifact_id]
                )
        self.assertEqual(
            self.service.store.recovery(task.task_id)["status"], "requested"
        )
        self.assertFalse(self.service.store.artifacts(task.task_id))
        self.service.approve_recovery(
            task.task_id, source.task_id, ids[:10], evidence=False
        )
        self.assertEqual(len(self.service.store.artifacts(task.task_id)), 10)

    def test_old_schema_migrates_receipts_and_draft_evidence_before_recovery(self):
        source, draft = self.source(state="uncertain")
        self.service.close()
        sip.delete(self.service)
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE recovery_effects")
            database.execute("DROP TABLE task_recoveries")
            database.execute("PRAGMA user_version = 5")
        self.service = TaskService(self.path, connectors=self.registry)
        self.assertEqual(
            self.service.store._db.execute("PRAGMA user_version").fetchone()[0], 10
        )
        task = self.followup(source, draft)
        self.approve(task)
        with self.assertRaises(TaskPermissionError):
            self.send(task, self.imported(task))

    def test_guard_storage_failure_prevents_dispatch_and_freezes_authority(self):
        source, draft = self.source(state="uncertain")
        task = self.followup(source, draft)
        self.approve(task)
        before = len(self.calls)
        with patch.object(
            self.service.store,
            "recovery_effects",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.send(task, self.imported(task))
        self.assertEqual(len(self.calls), before)
        self.assertTrue(self.service.failure)

    def test_followup_depth_is_bounded_without_reactivating_any_task(self):
        source, _ = self.source()
        for _ in range(31):
            source = self.followup(source)
            self.service._finish_task(source.task_id)
        task = self.service._create("Too many follow-ups")
        self.service._request_recovery(task.task_id)
        with self.assertRaises(ValueError):
            self.service.approve_recovery(task.task_id, source.task_id, [])
        self.assertEqual(
            self.service.store.recovery(task.task_id)["status"], "requested"
        )
        self.assertFalse(self.service._grants)


if __name__ == "__main__":
    unittest.main()
