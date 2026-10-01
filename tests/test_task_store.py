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
