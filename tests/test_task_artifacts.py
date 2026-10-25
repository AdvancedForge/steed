"""Atomic retained drafts, scoped evidence, bounded storage and plain-text review."""

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application

from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from services.task_store import TaskStore
from ui.task_dialog import TaskDialog


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.store = TaskStore(self.path)
        self.task = self.store.create("Retain a draft").task_id

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_content_is_immutable_task_scoped_and_not_in_evidence(self):
        summary = self.store.store_artifact(self.task, "Draft", "SYNTHETIC_CONTENT")
        self.assertFalse(hasattr(summary, "content"))
        self.assertEqual(
            self.store.artifact(self.task, summary.artifact_id).content,
            "SYNTHETIC_CONTENT",
        )
        self.assertEqual(self.store.artifacts(self.task), [summary])
        other = self.store.create("Other").task_id
        with self.assertRaises(KeyError):
            self.store.artifact(other, summary.artifact_id)
        self.assertNotIn("SYNTHETIC_CONTENT", repr(self.store.events(self.task)))
        self.assertEqual(len(summary.sha256), 64)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_sources_must_be_successful_inspections_owned_by_the_task(self):
        self.store.record_event(
            self.task, "page.inspected", {"snapshot_id": "observed"}
        )
        source = self.store.events(self.task)[-1].sequence
        other = self.store.create("Foreign").task_id
        self.store.record_event(other, "page.inspected", {})
        foreign = self.store.events(other)[-1].sequence
        for sources in ([foreign], [source + 1000], [1], [source, source], [True]):
            with self.assertRaises(ValueError):
                self.store.store_artifact(
                    self.task, "Invalid source", "Body", source_events=sources
                )
        saved = self.store.store_artifact(
            self.task, "Sourced", "Body", source_events=[source]
        )
        self.assertEqual(saved.source_events, (source,))

    def test_utf8_limits_and_invalid_inputs_are_rejected_without_storage(self):
        for arguments in (
            {"title": "", "content": "Body"},
            {"title": "bad\nlabel", "content": "Body"},
            {"title": "Draft", "content": "\ud800"},
            {"title": "Draft", "content": "a\x00b"},
            {"title": "Draft", "content": "☃" * 6000},
            {"title": "Draft", "content": "Body", "format": "html"},
        ):
            with self.assertRaises(ValueError):
                self.store.store_artifact(self.task, **arguments)
        self.assertEqual(self.store.artifacts(self.task), [])

    def test_count_and_total_byte_limits_are_independent(self):
        for _ in range(50):
            self.store.store_artifact(self.task, "Small", "x")
        with self.assertRaises(ValueError):
            self.store.store_artifact(self.task, "Too many", "x")
        other = self.store.create("Large drafts").task_id
        for _ in range(32):
            self.store.store_artifact(other, "Large", "x" * 16384)
        with self.assertRaises(ValueError):
            self.store.store_artifact(other, "Too large", "x")

    def test_journal_failure_rolls_back_content_and_metadata(self):
        before = self.store.events(self.task)
        with patch.object(
            self.store, "_append", side_effect=sqlite3.OperationalError("disk")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.store_artifact(self.task, "Uncommitted", "PRIVATE_DRAFT")
        self.assertEqual(self.store.artifacts(self.task), [])
        self.assertEqual(self.store.events(self.task), before)

    def test_restart_keeps_artifacts_without_restoring_task_authority(self):
        saved = self.store.store_artifact(self.task, "Draft", "Retained")
        self.store.close()
        self.store = TaskStore(self.path)
        self.assertEqual(self.store.get(self.task).status, "interrupted")
        self.assertEqual(
            self.store.artifact(self.task, saved.artifact_id).content, "Retained"
        )
        with self.assertRaises(ValueError):
            self.store.store_artifact(self.task, "New", "Body")

    def test_schema_two_migration_preserves_existing_journal(self):
        events = self.store.events(self.task)
        self.store.close()
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE artifacts")
            database.execute("PRAGMA user_version = 2")
        self.store = TaskStore(self.path)
        self.assertEqual(self.store.events(self.task)[:-1], events)
        self.assertEqual(self.store.artifacts(self.task), [])
        with sqlite3.connect(self.path) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 21)

    def test_user_deletion_is_atomic_and_keeps_content_free_evidence(self):
        saved = self.store.store_artifact(self.task, "Draft", "PRIVATE_DRAFT")
        self.store.transition(self.task, "completed")
        with patch.object(
            self.store, "_append", side_effect=sqlite3.OperationalError("disk")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.delete_artifact(self.task, saved.artifact_id)
        self.assertEqual(len(self.store.artifacts(self.task)), 1)
        self.store.delete_artifact(self.task, saved.artifact_id)
        self.assertEqual(self.store.artifacts(self.task), [])
        self.assertEqual(self.store.events(self.task)[-1].kind, "artifact.deleted")
        self.assertNotIn("PRIVATE_DRAFT", repr(self.store.events(self.task)))


class ArtifactServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = TaskService(Path(self.directory.name) / "tasks.sqlite")

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def resolve(self, future):
        deadline = time.monotonic() + 5
        while not future.done() and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertTrue(future.done())
        return future.result()

    def client(self, actions=("artifact",), **limits):
        task = self.service._create("Retain a task draft")
        grant = self.service._request_grant(
            task.task_id, {"actions": list(actions), **limits}
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        return self.service.client(task.task_id), grant.grant_id

    def test_explicit_permission_ownership_revocation_and_budget(self):
        metadata, _ = self.client(actions=("inspect",))
        with self.assertRaises(TaskPermissionError):
            self.resolve(
                metadata.submit("artifact.store", title="Denied", content="Body")
            )
        first, grant = self.client()
        saved = self.resolve(
            first.submit("artifact.store", title="Draft", content="PRIVATE_DRAFT")
        )
        second, _ = self.client()
        with self.assertRaises(KeyError):
            self.resolve(second.submit("artifact.read", artifact_id=saved.artifact_id))
        with self.assertRaises(TaskPermissionError):
            self.resolve(
                second.submit(
                    "artifact.read",
                    task_id=first.task_id,
                    artifact_id=saved.artifact_id,
                )
            )
        self.assertEqual(
            self.resolve(
                first.submit("artifact.read", artifact_id=saved.artifact_id)
            ).content,
            "PRIVATE_DRAFT",
        )
        self.service.revoke_grant(first.task_id, grant)
        with self.assertRaises(TaskPermissionError):
            self.resolve(first.submit("artifact.list"))
        self.assertEqual(self.service.store.artifacts(first.task_id), [saved])
        limited, _ = self.client(max_operations=1)
        self.resolve(limited.submit("artifact.list"))
        with self.assertRaises(TaskPermissionError):
            self.resolve(
                limited.submit("artifact.store", title="Budget", content="Body")
            )

    def test_failed_journal_prevents_disclosure_and_disables_commands(self):
        client, _ = self.client()
        saved = self.resolve(
            client.submit("artifact.store", title="Draft", content="PRIVATE_DRAFT")
        )
        with patch.object(
            self.service.store,
            "record_event",
            side_effect=sqlite3.OperationalError("disk"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.resolve(
                    client.submit("artifact.read", artifact_id=saved.artifact_id)
                )
        self.assertTrue(self.service.failure)

    def test_user_can_review_completed_drafts_as_plain_text_with_task_isolation(self):
        client, _ = self.client()
        self.resolve(
            client.submit(
                "artifact.store",
                title="Draft",
                content='<script>alert("PRIVATE_DRAFT")</script>',
            )
        )
        self.resolve(client.submit("task.finish"))
        dialog = TaskDialog(self.service)
        try:
            self.assertEqual(dialog.artifacts.count(), 1)
            self.assertIn(
                '<script>alert("PRIVATE_DRAFT")</script>',
                dialog.artifact_preview.toPlainText(),
            )
            self.assertNotIn("PRIVATE_DRAFT", dialog.activity.toPlainText())
            other = self.service._create("Separate task")
            dialog.refresh()
            row = next(
                i
                for i, (task, _) in enumerate(dialog._rows)
                if task.task_id == other.task_id
            )
            dialog.table.selectRow(row)
            self.assertEqual(dialog.artifacts.count(), 0)
            self.assertEqual(dialog.artifact_preview.toPlainText(), "")
        finally:
            dialog.close()

    def test_user_delete_requires_confirmation_and_agents_cannot_delete(self):
        from ui.prompts import MessageBox as QMessageBox

        client, _ = self.client()
        self.resolve(client.submit("artifact.store", title="Draft", content="Body"))
        dialog = TaskDialog(self.service)
        try:
            with patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No
            ):
                dialog.delete_artifact_button.click()
            self.assertEqual(dialog.artifacts.count(), 1)
            with self.assertRaises(TaskPermissionError):
                self.resolve(client.submit("artifact.delete"))
            with patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ):
                dialog.delete_artifact_button.click()
            self.assertEqual(dialog.artifacts.count(), 0)
            self.assertEqual(dialog.artifact_preview.toPlainText(), "")
        finally:
            dialog.close()
