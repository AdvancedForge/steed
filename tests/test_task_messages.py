"""Durable, bounded conversations with append-only replies and private auditing."""

import json
import sqlite3
import threading
import unittest
from unittest.mock import patch

import test_task_store as fixtures

from services.agent_tools import RESPONSE_LIMIT
from services.task_messages import MESSAGE_BYTES, TASK_MESSAGES, TaskMessages
from services.task_store import TaskStore


class TaskMessageTests(unittest.TestCase):
    setUp = fixtures.TaskStoreTests.setUp
    tearDown = fixtures.TaskStoreTests.tearDown

    def conversation(self):
        return self.store.create("Synthetic conversation").task_id, TaskMessages(
            self.store
        )

    def test_user_content_is_not_in_journal_and_is_task_scoped(self):
        task, messages = self.conversation()
        other = self.store.create("Other").task_id
        item = messages.post_user(task, "EXPLICIT_PRIVATE_TEXT")
        self.assertEqual(item["role"], "user")
        self.assertEqual(messages.read(other)["messages"], [])
        self.assertNotIn("EXPLICIT_PRIVATE_TEXT", str(self.store.events(task)))
        with self.assertRaises(ValueError):
            messages.post_agent(task, item["message_id"], "Forged user reply")

    def test_streaming_retries_cursor_and_display_order(self):
        task, messages = self.conversation()
        first = messages.post_agent(task, "reply", "Part", state="streaming")
        user = messages.post_user(task, "Follow-up")
        latest = messages.post_agent(task, "reply", "Part two", revision=1)
        self.assertEqual(latest["created_sequence"], first["sequence"])
        self.assertGreater(latest["sequence"], user["sequence"])
        count = len(self.store.events(task))
        self.assertEqual(
            messages.post_agent(task, "reply", "Part two", revision=1), latest
        )
        self.assertEqual(len(self.store.events(task)), count)
        page = messages.read(task, after=user["sequence"])
        self.assertEqual(page["messages"], [latest])
        self.assertEqual(messages.read(task, after=page["cursor"])["messages"], [])

    def test_gaps_changes_and_finished_reply_edits_are_rejected(self):
        task, messages = self.conversation()
        messages.post_agent(task, "reply", "Original", state="streaming")
        for text, revision in (("Changed", 0), ("Original more", 2), ("Overwrite", 1)):
            with self.assertRaises(ValueError):
                messages.post_agent(task, "reply", text, revision, "streaming")
        messages.post_agent(task, "reply", "Original", 1)
        with self.assertRaises(ValueError):
            messages.post_agent(task, "reply", "Original more", 2)
        with self.assertRaises(ValueError):
            messages.post_agent(task, "new", "Gap", 1)

    def test_cancel_and_restart_interrupt_streams_without_replay(self):
        task, messages = self.conversation()
        messages.post_agent(task, "reply", "Unfinished", state="streaming")
        self.store.close()
        self.store = TaskStore(self.path)
        messages = TaskMessages(self.store)
        self.assertEqual(messages.read(task)["messages"][0]["state"], "interrupted")
        self.assertEqual(self.store.get(task).status, "interrupted")
        with self.assertRaises(ValueError):
            messages.post_agent(task, "reply", "Unfinished more", 1)
        task = self.store.create("Cancel").task_id
        messages.post_agent(task, "reply", "Partial", state="streaming")
        self.store.transition(task, "cancelled")
        self.assertEqual(messages.read(task)["messages"][0]["state"], "interrupted")

    def test_clearing_scrubs_text_and_prevents_id_reuse(self):
        task, messages = self.conversation()
        item = messages.post_agent(task, "reply", "SECRET_REPLY", state="streaming")
        messages.clear(task)
        row = messages.read(task, after=item["sequence"])["messages"][0]
        self.assertEqual((row["text"], row["state"]), ("", "cleared"))
        self.assertNotIn("SECRET_REPLY", str(self.store.events(task)))
        for text, revision in (("SECRET_REPLY", 0), ("SECRET_REPLY more", 1)):
            with self.assertRaises(ValueError):
                messages.post_agent(task, "reply", text, revision)

    def test_message_and_transition_failure_are_atomic(self):
        task, messages = self.conversation()
        with patch.object(
            self.store, "_append", side_effect=sqlite3.OperationalError("fixture")
        ):
            with self.assertRaises(sqlite3.Error):
                messages.post_user(task, "Must not persist")
        self.assertEqual(messages.read(task)["messages"], [])
        messages.post_agent(task, "reply", "Partial", state="streaming")
        original = self.store._append

        def fail_transition(task_id, kind, payload):
            if kind == "task.cancelled":
                raise sqlite3.OperationalError("fixture")
            return original(task_id, kind, payload)

        with patch.object(self.store, "_append", side_effect=fail_transition):
            with self.assertRaises(sqlite3.Error):
                self.store.transition(task, "cancelled")
        self.assertEqual(self.store.get(task).status, "created")
        self.assertEqual(messages.read(task)["messages"][0]["state"], "streaming")

    def test_limits_validate_bytes_controls_ids_and_cursors(self):
        task, messages = self.conversation()
        for text in (
            "",
            " ",
            "bad\x00text",
            "bad\x1btext",
            "é" * (MESSAGE_BYTES // 2 + 1),
        ):
            with self.assertRaises(ValueError):
                messages.post_user(task, text)
        for message_id in ("../path", "x" * 65, 1):
            with self.assertRaises(ValueError):
                messages.post_agent(task, message_id, "Reply")
        for after, limit in ((True, 16), (-1, 16), (0, 17), (0, False)):
            with self.assertRaises(ValueError):
                messages.read(task, after, limit)
        with self.assertRaises(ValueError):
            messages.post_agent(task, "reply", "Text", revision=True)

    def test_task_limits_and_clear_do_not_refill_message_quota(self):
        task, messages = self.conversation()
        for _ in range(TASK_MESSAGES):
            messages.post_user(task, "Text")
        with self.assertRaises(ValueError):
            messages.post_user(task, "Overflow")
        messages.clear(task)
        with self.assertRaises(ValueError):
            messages.post_user(task, "Overflow after clear")
        task = self.store.create("Byte budget").task_id
        for i in range(128):
            messages.post_agent(task, str(i), "x" * MESSAGE_BYTES)
        with self.assertRaises(ValueError):
            messages.post_user(task, "Overflow")

    def test_update_budget_and_bounded_wire_response(self):
        task, messages = self.conversation()
        with patch("services.task_messages.TASK_UPDATES", 2):
            messages.post_agent(task, "reply", "Text", state="streaming")
            messages.post_agent(task, "reply", "Text", 1, "streaming")
            with self.assertRaises(ValueError):
                messages.post_agent(task, "reply", "Text more", 2)
        for i in range(17):
            messages.post_agent(task, str(i), "😀" * (MESSAGE_BYTES // 4))
        page = messages.read(task)
        self.assertTrue(page["has_more"])
        self.assertLess(len(json.dumps(page).encode()), RESPONSE_LIMIT)
        following = messages.read(task, page["cursor"])
        self.assertFalse(following["has_more"])
        self.assertFalse(
            set(m["message_id"] for m in page["messages"])
            & set(m["message_id"] for m in following["messages"])
        )

    def test_foreign_threads_cannot_read_or_write(self):
        task, messages = self.conversation()
        errors = []

        def worker():
            for operation in (
                lambda: messages.read(task),
                lambda: messages.post_user(task, "Text"),
            ):
                try:
                    operation()
                except RuntimeError as error:
                    errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 2)

    def test_schema_fifteen_upgrade_preserves_prior_journal(self):
        task, _ = self.conversation()
        self.store.transition(task, "completed")
        before = self.store.events(task)
        self.store.close()
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE task_messages")
            db.execute("PRAGMA user_version=15")
        self.store = TaskStore(self.path)
        self.assertEqual(self.store.events(task), before)
        self.assertEqual(TaskMessages(self.store).read(task)["messages"], [])

    def test_read_marks_only_returned_user_messages_without_an_activity_event(self):
        task, messages = self.conversation()
        first = messages.post_user(task, "First")
        second = messages.post_user(task, "Second")
        before = self.store.events(task)
        self.assertFalse(
            messages.acknowledge(task, messages.read(task, after=2147483647))
        )
        self.assertEqual(messages.read_sequence(task), 0)
        self.assertTrue(messages.acknowledge(task, messages.read(task, limit=1)))
        self.assertEqual(messages.read_sequence(task), first["sequence"])
        self.assertTrue(
            messages.acknowledge(task, messages.read(task, after=first["sequence"]))
        )
        self.assertEqual(messages.read_sequence(task), second["sequence"])
        self.assertFalse(messages.acknowledge(task, messages.read(task, limit=1)))
        self.assertEqual(self.store.events(task), before)
