"""Agents seeing and changing what you made, with your review of every change."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import access_levels
from browser.browser_window import BrowserWindow
from services.task_policy import TaskPermissionError


class CustomizationChangeTests(unittest.TestCase):
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
        self.registry.start_pages.save_your_page(
            self.profile, "notes", "My notes", "<p>Old notes</p>"
        )
        self.skill = self.registry.skills.add(
            {"name": "Summarise", "ask": "Summarise this page.", "context": "page"}
        )
        task = self.command("task.create", objective="Tidy what I made")
        self.task, self.client = task.task_id, self.service.client(task.task_id)

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

    def command(self, name, *, client=None, **arguments):
        future = (client or self.service.broker).submit(name, **arguments)
        self.wait(future.done)
        return future.result()

    def agent(self, name, **arguments):
        return self.command(name, client=self.client, **arguments)

    def test_agents_list_and_read_what_you_made(self):
        found = self.agent("customizations.list")
        self.assertEqual(found["pages"], [{"id": "notes", "name": "My notes"}])
        self.assertIn({"id": self.skill["id"], "name": "Summarise"}, found["skills"])
        page = self.agent("customizations.read", kind="page", id="notes")
        self.assertEqual(page["item"]["html"], "<p>Old notes</p>")

    def test_a_reviewed_change_replaces_and_a_declined_one_does_not(self):
        receipt = self.agent(
            "customizations.change",
            request_id="c1",
            kind="page",
            id="notes",
            action="replace",
            item={"title": "Notes", "html": "<h1>New notes</h1>"},
        )
        self.assertEqual(receipt["state"], "awaiting_approval")
        # Nothing changes before the person's review.
        self.assertIn(
            "Old notes",
            self.registry.start_pages.your_page(self.profile, "notes")["html"],
        )
        (request,) = self.service.customization_changes.pending(self.task)
        from ui.customization_change_review import CustomizationChangeDialog

        dialog = CustomizationChangeDialog(self.service, request, self.window)
        dialog.apply()
        page = self.registry.start_pages.your_page(self.profile, "notes")
        self.assertEqual((page["title"], page["html"]), ("Notes", "<h1>New notes</h1>"))
        self.assertEqual(
            self.agent("customizations.status", request_id="c1")["state"], "applied"
        )
        before = len(self.registry.skills.list())
        self.agent(
            "customizations.change",
            request_id="c2",
            kind="skill",
            id=self.skill["id"],
            action="remove",
        )
        self.service.customization_changes.resolve(self.task, "c2", approve=False)
        self.assertEqual(len(self.registry.skills.list()), before)

    def test_changes_are_checked_and_never_can_refuse_it_all(self):
        with self.assertRaises(ValueError):
            self.agent(
                "customizations.change",
                request_id="bad",
                kind="skill",
                id=self.skill["id"],
                action="replace",
                item={"name": "", "ask": ""},
            )
        with self.assertRaises(KeyError):
            self.agent("customizations.read", kind="page", id="missing")
        access_levels.set_policy(self.window.config, "agents", "customize", "never")
        with self.assertRaises(TaskPermissionError):
            self.agent("customizations.list")


if __name__ == "__main__":
    unittest.main()
