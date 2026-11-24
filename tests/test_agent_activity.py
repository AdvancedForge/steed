"""Agent activity: one day of every task's journal, counted per task."""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from services import agent_activity as activity
from services.task_store import TaskStore


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.directory.name) / "tasks.sqlite")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def record(self, task, kind, **payload):
        self.store.record_event(task, kind, payload)

    def test_a_day_counts_sites_reads_actions_permissions_and_usage(self):
        chat = self.store.create("Claude Code conversation").task_id
        quiet = self.store.create("Codex conversation").task_id
        self.record(chat, "conversation.updated", message_id="m1", role="user")
        self.record(chat, "conversation.updated", message_id="m1", role="user")
        self.record(chat, "grant.requested", grant_id="g", scope={})
        self.record(chat, "grant.approved", grant_id="g")
        self.record(chat, "grant.requested", grant_id="h", scope={})
        self.record(chat, "grant.denied", grant_id="h")
        self.record(chat, "session.opened", url="https://shop.example", title="x")
        self.record(chat, "session.navigation_requested", url="https://docs.example")
        self.record(chat, "page.inspected", session_id="s")
        self.record(chat, "page.inspected", session_id="s")
        for action, operation in (("a1", "enter"), ("a2", "submit")):
            self.record(
                chat, "page.action_requested", action_id=action, operation=operation
            )
            self.record(chat, "page.action_finished", action_id=action)
        self.record(chat, "download.finished", download_id="d")
        self.record(chat, "handoff.requested", request_id="r", reason="captcha")
        self.record(
            chat,
            "agent.usage",
            input_tokens=12000,
            output_tokens=800,
            cached_tokens=9000,
            cost_usd=0.25,
            agent="Claude Code",
        )
        self.record(chat, "agent.usage", input_tokens=1000, output_tokens=200)
        self.record(quiet, "conversation.updated", message_id="m2", role="user")
        days = activity.summarize(self.store, date.today())
        self.assertEqual([d.task_id for d in days], [chat, quiet])
        day = days[0]
        self.assertEqual(day.sites, {"shop.example", "docs.example"})
        self.assertEqual((day.pages_read, day.actions, day.forms), (2, 2, 1))
        self.assertEqual((day.downloads, day.asked_you, day.messages), (1, 1, 1))
        self.assertEqual(
            (day.permissions_asked, day.permissions_approved, day.permissions_refused),
            (2, 1, 1),
        )
        self.assertEqual((day.tokens, day.turns, day.turns_with_cost), (14000, 2, 1))
        self.assertEqual(day.agent, "Claude Code")
        self.assertEqual(
            activity.task_line(day),
            "1 message from you; visited docs.example, shop.example; read 2 pages; "
            "took 2 actions (1 form sent); downloaded 1 file; asked you for help 1 time",
        )
        self.assertEqual(
            activity.permissions_line(day), "Asked 2 times: 1 allowed, 1 refused"
        )
        self.assertEqual(activity.usage_line(day), "14k tokens, $0.25 (where reported)")
        self.assertEqual(activity.usage_line(days[1]), "")
        self.assertEqual(
            activity.totals_line(days),
            "2 conversations and tasks: read 2 pages, took 2 actions, asked for "
            "permission 2 times (1 refused), used 14k tokens ($0.25 reported).",
        )

    def test_other_days_are_empty(self):
        task = self.store.create("Task").task_id
        self.record(task, "page.inspected", session_id="s")
        self.assertEqual(
            activity.summarize(self.store, date.today() - timedelta(1)), []
        )
        self.assertEqual(activity.totals_line([]), "")

    def test_bounds_cover_the_local_day_in_utc(self):
        start, end = activity.day_bounds(date(2026, 11, 22))
        self.assertLess(start, end)
        self.assertTrue(start.endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
