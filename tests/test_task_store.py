"""Transactional task storage and restart recovery with temporary data."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.task_store import TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.store = TaskStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_transitions_events_and_restart_recovery(self):
        interrupted = self.store.create("Visit a fixture")
        completed = self.store.create("Finished fixture")
        self.store.transition(interrupted.task_id, "running")
        self.store.transition(completed.task_id, "completed")
        before = self.store.events(interrupted.task_id)
        self.store.close()
        self.store = TaskStore(self.path)
        self.assertEqual(self.store.get(interrupted.task_id).status, "interrupted")
        self.assertEqual(self.store.get(completed.task_id).status, "completed")
        events = self.store.events(interrupted.task_id, after=before[-1].sequence)
        self.assertEqual(
            [(event.kind, event.payload) for event in events],
            [("task.interrupted", {"reason": "restart"})],
        )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.store.transition(interrupted.task_id, "running")

    def test_event_failure_rolls_back_task_creation_and_transition(self):
        with patch.object(
            self.store,
            "_append",
            side_effect=sqlite3.OperationalError("fixture failure"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.create("Must not persist")
        self.assertEqual(self.store.list_tasks(), [])
        task = self.store.create("Atomic transition")
        with patch.object(
            self.store, "_append", side_effect=ValueError("fixture failure")
        ):
            with self.assertRaises(ValueError):
                self.store.transition(task.task_id, "completed")
        self.assertEqual(self.store.get(task.task_id).status, "created")
        self.assertEqual(len(self.store.events(task.task_id)), 1)

    def test_journal_is_append_only_and_cursors_are_task_scoped(self):
        first = self.store.create("First task")
        second = self.store.create("Second task")
        self.store.record_event(first.task_id, "fixture.event", {"value": 42})
        events = self.store.events(first.task_id, limit=1)
        later = self.store.events(first.task_id, after=events[-1].sequence)
        self.assertEqual([event.kind for event in later], ["fixture.event"])
        self.assertEqual([event.task_id for event in later], [first.task_id])
        with sqlite3.connect(self.path) as connection:
            for statement in (
                "UPDATE events SET kind = 'forged'",
                "DELETE FROM events",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM events").fetchone()[0], 3
            )
            payload = connection.execute(
                "SELECT payload FROM events WHERE task_id = ? ORDER BY sequence DESC",
                (first.task_id,),
            ).fetchone()[0]
            self.assertEqual(json.loads(payload), {"value": 42})
        self.assertEqual(len(self.store.events(second.task_id)), 1)

    def test_invalid_input_is_rejected_without_mutation(self):
        for objective in ("", "   ", "x" * 4097, None):
            with self.assertRaises(ValueError):
                self.store.create(objective)
        self.assertEqual(self.store.list_tasks(), [])
        task = self.store.create("Valid task")
        with self.assertRaises(KeyError):
            self.store.get("missing")
        with self.assertRaises(ValueError):
            self.store.events(task.task_id, limit=0)
        with self.assertRaises(ValueError):
            self.store.transition(task.task_id, "invented")
        self.assertEqual(len(self.store.events(task.task_id)), 1)

    def test_grant_decisions_and_terminal_task_cleanup_are_atomic(self):
        task = self.store.create("Grant transaction")
        with patch.object(self.store, "_append", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.request_grant(task.task_id, {"origins": []})
        self.assertEqual(self.store.grants(task.task_id), [])
        grant = self.store.request_grant(task.task_id, {"origins": []})
        with self.assertRaises(ValueError):
            self.store.request_grant(task.task_id, {"origins": []})
        with patch.object(self.store, "_append", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.resolve_grant(task.task_id, grant.grant_id, "approved")
        self.assertEqual(
            self.store.grant(task.task_id, grant.grant_id).status, "requested"
        )
        self.store.resolve_grant(
            task.task_id, grant.grant_id, "approved", expires_at="fixture"
        )
        with patch.object(self.store, "_append", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.transition(task.task_id, "cancelled")
        self.assertEqual(
            self.store.grant(task.task_id, grant.grant_id).status, "approved"
        )
        self.assertEqual(self.store.get(task.task_id).status, "created")
        self.store.transition(task.task_id, "completed")
        self.assertEqual(self.store.grant(task.task_id, grant.grant_id).status, "ended")

    def test_restart_ends_pending_and_approved_grants_without_restoring_authority(self):
        for status in ("requested", "approved"):
            task = self.store.create(status)
            grant = self.store.request_grant(task.task_id, {"origins": []})
            if status == "approved":
                self.store.resolve_grant(task.task_id, grant.grant_id, status)
        self.store.close()
        self.store = TaskStore(self.path)
        self.assertEqual({grant.status for grant in self.store.grants()}, {"ended"})
        self.assertEqual(
            {task.status for task in self.store.list_tasks()}, {"interrupted"}
        )
        self.assertEqual(self.store.events(task.task_id)[-1].kind, "task.interrupted")

    def test_grant_cannot_be_resolved_under_another_task(self):
        first, second = self.store.create("First"), self.store.create("Second")
        grant = self.store.request_grant(first.task_id, {"origins": []})
        with self.assertRaises(KeyError):
            self.store.resolve_grant(second.task_id, grant.grant_id, "approved")
        self.assertEqual(
            self.store.grant(first.task_id, grant.grant_id).status, "requested"
        )

    def test_schema_one_migrates_with_existing_journal_intact(self):
        task = self.store.create("Original A1 database")
        self.store.transition(task.task_id, "completed")
        events = self.store.events(task.task_id)
        self.store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE grants")
            connection.execute("PRAGMA user_version = 1")
        self.store = TaskStore(self.path)
        self.assertEqual(self.store.events(task.task_id), events)
        self.assertEqual(self.store.grants(), [])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)

    def test_ui_tail_selects_recent_task_scoped_and_filtered_events(self):
        first, second = self.store.create("First"), self.store.create("Second")
        for index in range(210):
            self.store.record_event(first.task_id, "page.inspected", {"index": index})
        self.store.record_event(
            first.task_id, "download.finished", {"state": "completed"}
        )
        self.store.record_event(
            second.task_id, "download.finished", {"state": "foreign"}
        )
        recent = self.store.recent_events(first.task_id, limit=3)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[-1].kind, "download.finished")
        self.assertEqual(
            [e.sequence for e in recent], sorted(e.sequence for e in recent)
        )
        self.assertTrue(all(e.task_id == first.task_id for e in recent))
        receipts = self.store.recent_events(first.task_id, kinds=("download.finished",))
        self.assertEqual([e.payload["state"] for e in receipts], ["completed"])
        with self.assertRaises(ValueError):
            self.store.recent_events(first.task_id, limit=1001)
