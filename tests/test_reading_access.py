"""Your agent searching what you've read: Never, Ask (a popup with the
words) or Always, and only with Remember what I read on."""

import unittest

import test_task_profiles as profiles

from browser import access_levels
from services.task_policy import TaskPermissionError
from ui.reading_review import ReadingReviewDialog


class ReadingAccessTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for

    def command(self, command_name, *, client=None, **arguments):
        future = (client or self.service.broker).submit(command_name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def remember(self):
        self.config.set("remember_pages", True)
        name = self.manager.current_profile_name
        memory = self.registry.get_history(name).memory
        memory.remember(
            "https://tides.test/", "Tides", "The moon pulls the sea twice a day."
        )
        memory.remember("https://bread.test/", "Bread", "Sourdough needs patience.")

    def test_off_refused_then_asked_then_always(self):
        task = self.command("task.create", objective="Find that article")
        client = self.service.client(task.task_id)
        with self.assertRaises(ValueError):  # Nothing remembered yet.
            self.command("reading.search", client=client, query="moon", request_id="r0")
        self.remember()
        receipt = self.command(
            "reading.search", client=client, query="moon sea", request_id="r1"
        )
        self.assertEqual(receipt["state"], "awaiting_approval")
        self.assertNotIn("results", receipt)
        (request,) = self.service.reading.pending(task.task_id)
        self.assertEqual(request.query, "moon sea")
        dialog = ReadingReviewDialog(self.service, request)
        try:
            dialog.always.setChecked(True)
            dialog.apply()
        finally:
            dialog.deleteLater()
        answered = self.command("reading.status", client=client, request_id="r1")
        self.assertEqual(answered["state"], "answered")
        (page,) = answered["results"]
        self.assertEqual(page["url"], "https://tides.test/")
        self.assertIn("moon pulls the sea", page["text"])
        # Always, from now on: answered at once.
        self.assertEqual(
            access_levels.values(self.config, "agents")["reading"], "always"
        )
        quick = self.command(
            "reading.search", client=client, query="sourdough", request_id="r2"
        )
        self.assertEqual([r["title"] for r in quick["results"]], ["Bread"])
        # Never: refused.
        access_levels.set_policy(self.config, "agents", "reading", "never")
        with self.assertRaises(TaskPermissionError):
            self.command("reading.search", client=client, query="moon", request_id="r3")

    def test_denied(self):
        self.remember()
        task = self.command("task.create", objective="Search")
        client = self.service.client(task.task_id)
        self.command("reading.search", client=client, query="bread", request_id="d1")
        self.service.reading.resolve(task.task_id, "d1", approve=False)
        self.assertEqual(
            self.command("reading.status", client=client, request_id="d1"),
            {"request_id": "d1", "state": "denied"},
        )


if __name__ == "__main__":
    unittest.main()
