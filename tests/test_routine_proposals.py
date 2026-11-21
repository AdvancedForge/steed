"""Agents suggest routines ("every morning, brief me"); the person reviews,
can change it, and adds it. Nothing runs before that."""

import unittest

import test_task_profiles as profiles
from PyQt6.QtWidgets import QPushButton

from services.task_policy import TaskPermissionError
from ui.routines_dialog import RoutineReviewDialog

BRIEF = {
    "name": "Morning brief",
    "ask": "Tell me what needs my attention today.",
    "kind": "daily",
    "at": "08:00",
    "days": [0, 1, 2, 3, 4],
    "pages": ["https://calendar.example.org/"],
}


class RoutineProposalTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for

    def command(self, command_name, *, client=None, **arguments):
        future = (client or self.service.broker).submit(command_name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def propose(self, request="one", task=None, **fields):
        task = task or self.command("task.create", objective="Mornings")
        receipt = self.command(
            "routine.propose",
            task_id=task.task_id,
            request_id=request,
            **{**BRIEF, **fields},
        )
        return task, receipt

    def test_proposed_reviewed_and_added(self):
        task, receipt = self.propose()
        client = self.service.client(task.task_id)
        self.assertTrue(
            self.command("routine.capabilities", client=client)["available"]
        )
        self.assertEqual(
            self.command("routine.status", client=client, request_id="one"), receipt
        )
        self.assertEqual(receipt.routine["days"], [0, 1, 2, 3, 4])
        self.assertEqual(self.service.messages.summary(task.task_id)["pending"], 1)
        self.assertEqual(self.registry.routines.list(), [])
        future = client.submit("routine.resolve", request_id="one", approve=True)
        self.app.processEvents()
        with self.assertRaises(TaskPermissionError):
            future.result()
        self.assertEqual(self.propose(task=task)[1], receipt)  # Same retry.
        with self.assertRaises(ValueError):
            self.propose(task=task, at="09:00")
        for bad in (
            {"at": "25:61"},
            {"kind": "hours", "every": 5},
            {"pages": ["ftp://x"]},
            {"days": ["x"]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.propose(request="bad", task=task, **bad)
        dialog = RoutineReviewDialog(self.service, receipt)
        try:
            buttons = [b.text() for b in dialog.findChildren(QPushButton)]
            self.assertIn("Add routine", buttons)
            self.assertIn("Decline", buttons)
            dialog.at.setText("07:30")  # The person changes it.
            dialog.apply()
        finally:
            if dialog.isVisible():
                dialog.close()
        # An added routine is an automation now.
        (routine,) = self.registry.automations.list()
        self.assertEqual(
            (routine["name"], routine["trigger"]["at"]), ("Morning brief", "07:30")
        )
        self.assertEqual(routine["steps"][-1]["do"], "chat")
        self.assertEqual(
            self.service.routines.get(task.task_id, "one").state, "applied"
        )
        _, other = self.propose(request="two", task=task, name="Evening")
        self.service.routines.resolve(task.task_id, "two", approve=False)
        self.assertEqual(self.service.routines.get(task.task_id, "two").state, "denied")
        self.assertEqual(len(self.registry.automations.list()), 1)

    def test_ending_the_task_cancels_pending_proposals(self):
        task, _ = self.propose()
        self.command("task.finish", client=self.service.client(task.task_id))
        self.assertEqual(
            self.service.routines.get(task.task_id, "one").state, "cancelled"
        )


if __name__ == "__main__":
    unittest.main()
