"""Agents suggest watches from a chat ("tell me when tickets go on sale"); the
person reviews, can adjust, and adds them. Nothing is watched before that."""

import unittest

import test_task_profiles as profiles
from PyQt6.QtWidgets import QPushButton

from services.agent_tools import TOOLS
from services.task_policy import TaskPermissionError
from ui.watch_review import WatchReviewDialog

URL = "https://tickets.example/tour"


class ProposalTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for

    def command(self, name, *, client=None, **arguments):
        future = (client or self.service.broker).submit(name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def propose(self, request="one", task=None, **fields):
        task = task or self.command("task.create", objective="Concert tickets")
        arguments = {
            "url": URL,
            "title": "Tour tickets",
            "instruction": "tell me when tickets go on sale",
            "every": "hourly",
            "then": "tell me the cheapest seats",
            **fields,
        }
        receipt = self.command(
            "watch.propose", task_id=task.task_id, request_id=request, **arguments
        )
        return task, receipt

    def test_agent_proposes_and_polls_but_cannot_add_it(self):
        task, receipt = self.propose()
        client = self.service.client(task.task_id)
        capabilities = self.command("watch.capabilities", client=client)
        self.assertTrue(capabilities["available"])
        self.assertIn("hourly", capabilities["intervals"])
        self.assertEqual(
            self.command("watch.status", client=client, request_id="one"), receipt
        )
        self.assertEqual(receipt.state, "awaiting_approval")
        self.assertEqual(receipt.then_action, "tell me the cheapest seats")
        future = client.submit("watch.resolve", request_id="one", approve=True)
        self.app.processEvents()
        with self.assertRaises(TaskPermissionError):
            future.result()
        self.assertEqual(self.service.messages.summary(task.task_id)["pending"], 1)
        self.assertEqual(self.registry.watches.list(), [])
        # Identical retries return the receipt; changed reuse is refused.
        self.assertEqual(self.propose(task=task)[1], receipt)
        with self.assertRaises(ValueError):
            self.propose(task=task, every="daily")

    def test_only_web_pages_and_known_intervals(self):
        task = self.command("task.create", objective="Watch things")
        for bad in (
            {"url": "file:///etc/passwd"},
            {"url": "steed:settings"},
            {"url": "https://"},
            {"every": "minutely"},
            {"instruction": "x" * 401},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.propose(request="bad", task=task, **bad)
        (tool,) = [t for t in TOOLS if t.command == "watch.propose"]
        with self.assertRaises(ValueError):
            tool.validate({"url": URL, "request_id": "a", "every": "weekly"})
        with self.assertRaises(ValueError):
            tool.validate({"url": URL, "request_id": "a", "profile": "work"})

    def test_adding_creates_the_watch_as_reviewed(self):
        task, receipt = self.propose()
        applied = self.service.watches.resolve(
            task.task_id,
            "one",
            approve=True,
            profile="work",
            instruction="tell me when the London date goes on sale",
            every=receipt.every,
            then=receipt.then_action,
        )
        self.assertEqual(applied.state, "applied")
        (watch,) = self.registry.watches.list()
        self.assertEqual(watch["url"], URL)
        self.assertEqual(watch["profile"], "work")
        self.assertEqual(
            watch["instruction"], "tell me when the London date goes on sale"
        )
        self.assertEqual(watch["then"], "tell me the cheapest seats")
        self.assertEqual(watch["then_mode"], "ask")  # The agent cannot skip asking.
        with self.assertRaises(ValueError):
            self.service.watches.resolve(
                task.task_id, "one", approve=True, profile="work"
            )
        # Incognito is refused; declining leaves nothing.
        _, other = self.propose(request="two", task=task)
        with self.assertRaises(ValueError):
            self.service.watches.resolve(
                task.task_id, "two", approve=True, profile="search"
            )
        self.service.watches.resolve(task.task_id, "two", approve=False)
        self.assertEqual(self.service.watches.get(task.task_id, "two").state, "denied")
        self.assertEqual(len(self.registry.watches.list()), 1)

    def test_review_lets_the_person_adjust_and_add(self):
        task, receipt = self.propose()
        dialog = WatchReviewDialog(self.service, receipt)
        try:
            dialog.show()
            self.app.processEvents()
            defaults = [
                b.text() for b in dialog.findChildren(QPushButton) if b.isDefault()
            ]
            self.assertEqual(defaults, ["Add watch"])
            self.assertFalse(dialog.then_now.isChecked())
            dialog.every.setCurrentIndex(dialog.every.findData("15m"))
            dialog.then_now.setChecked(True)
            dialog.profile.setCurrentText("work")
            dialog.apply()
            (watch,) = self.registry.watches.list()
            self.assertEqual(watch["every"], "15m")
            self.assertEqual(watch["then_mode"], "now")
        finally:
            if dialog.isVisible():
                dialog.close()

    def test_ending_the_task_cancels_pending_proposals(self):
        task, _ = self.propose()
        self.command("task.finish", client=self.service.client(task.task_id))
        self.assertEqual(
            self.service.watches.get(task.task_id, "one").state, "cancelled"
        )


if __name__ == "__main__":
    unittest.main()
