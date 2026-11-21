"""Your agent proposing a page of your own or an automation: the HTML or
every step shown, saved only after you press Add."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from services.task_policy import TaskPermissionError
from ui.creation_review import CreationReviewDialog

PAGE = {
    "name": "prices",
    "title": "My prices",
    "html": "<h1>Prices</h1><script>fetch('/api/watches')</script>",
}
AUTOMATION = {
    "name": "Friday check",
    "trigger": {"kind": "schedule", "at": "17:00", "days": [4]},
    "steps": [
        {"do": "read", "value": "https://shop.example/kettle"},
        {"do": "changed"},
        {"do": "notify", "value": "{last}"},
    ],
}


class CreationProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()
        self.registry = self.window.profile_manager.registry
        self.service = self.registry.tasks
        self.profile = self.window.profile_manager.current_profile_name

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def command(self, command, /, *, client=None, **arguments):
        future = (client or self.service.broker).submit(command, **arguments)
        self.wait(future.done)
        return future.result()

    def task(self):
        task = self.command("task.create", objective="Make me a dashboard")
        return task.task_id, self.service.client(task.task_id)

    def review(self, task, decide):
        (proposal,) = self.service.creations.pending(task)
        dialog = CreationReviewDialog(self.service, proposal, self.window)
        try:
            decide(dialog)
        finally:
            dialog.deleteLater()
        return dialog

    def test_capabilities_describe_pages_and_automations(self):
        _, client = self.task()
        found = self.command("creation.capabilities", client=client)
        self.assertIn("/api/", found["page"]["what"])
        self.assertIn("schedule", found["automation"]["valid_triggers"])
        self.assertIn("notify", found["automation"]["steps"])

    def test_a_page_is_saved_only_when_added(self):
        task, client = self.task()
        receipt = self.command(
            "custom_page.propose", client=client, request_id="p1", **PAGE
        )
        self.assertEqual(receipt["state"], "awaiting_approval")
        pages = self.registry.start_pages
        self.assertIsNone(pages.your_page(self.profile, "prices"))
        dialog = self.review(task, lambda d: d.apply())
        self.assertIn("fetch('/api/watches')", dialog.body.toPlainText())
        status = self.command("creation.status", client=client, request_id="p1")
        self.assertEqual(
            status, {"request_id": "p1", "state": "applied", "saved_as": "steed:prices"}
        )
        self.assertEqual(pages.your_page(self.profile, "prices")["title"], "My prices")

    def test_an_automation_shows_every_step_and_denying_saves_nothing(self):
        task, client = self.task()
        self.command("automation.propose", client=client, request_id="a1", **AUTOMATION)
        dialog = self.review(task, lambda d: d.decline())
        shown = [dialog.body.item(i).text() for i in range(dialog.body.count())]
        self.assertEqual(shown[0], "1. Read https://shop.example/kettle")
        self.assertEqual(len(shown), 3)
        self.assertEqual(
            self.command("creation.status", client=client, request_id="a1")["state"],
            "denied",
        )
        self.assertEqual(self.registry.automations.list(), [])

    def test_an_added_automation_is_saved_paused_never(self):
        task, client = self.task()
        self.command("automation.propose", client=client, request_id="a1", **AUTOMATION)
        self.review(task, lambda d: d.apply())
        (saved,) = self.registry.automations.list()
        self.assertEqual(saved["name"], "Friday check")
        self.assertEqual(saved["profile"], self.profile)
        status = self.command("creation.status", client=client, request_id="a1")
        self.assertEqual(status["saved_as"], saved["id"])

    def test_bad_proposals_and_limits_are_refused(self):
        task, client = self.task()
        for name, document in (
            ("custom_page.propose", {**PAGE, "name": "settings"}),
            ("custom_page.propose", {**PAGE, "name": "../x"}),
            (
                "automation.propose",
                {**AUTOMATION, "steps": [{"do": "shell", "value": "rm -rf ~"}]},
            ),
            (
                "automation.propose",
                {
                    **AUTOMATION,
                    "steps": [{"do": "read", "value": "file:///etc/passwd"}],
                },
            ),
        ):
            with self.assertRaises(ValueError, msg=document):
                self.command(name, client=client, request_id="bad", **document)
        for number in range(3):
            self.command(
                "custom_page.propose",
                client=client,
                request_id=f"p{number}",
                **{**PAGE, "name": f"page-{number}"},
            )
        with self.assertRaises(TaskPermissionError):
            self.command("custom_page.propose", client=client, request_id="p9", **PAGE)
        with self.assertRaises(ValueError):  # Same request id, other document.
            self.command(
                "custom_page.propose",
                client=client,
                request_id="p0",
                **{**PAGE, "name": "other"},
            )

    def test_another_task_cannot_see_or_resolve_the_proposal(self):
        task, client = self.task()
        self.command("custom_page.propose", client=client, request_id="p1", **PAGE)
        _, other = self.task()
        with self.assertRaises(KeyError):
            self.command("creation.status", client=other, request_id="p1")

    def test_ending_the_task_drops_waiting_proposals(self):
        task, client = self.task()
        self.command("custom_page.propose", client=client, request_id="p1", **PAGE)
        self.command("task.finish", client=client, status="cancelled")
        self.assertEqual(self.service.creations.pending(task), [])
        self.assertIsNone(self.registry.start_pages.your_page(self.profile, "prices"))


if __name__ == "__main__":
    unittest.main()
