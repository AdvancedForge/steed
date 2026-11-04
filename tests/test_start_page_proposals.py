"""External editing, native decisions, stale previews and recovery checks."""

import json
import sqlite3
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_task_profiles as profiles
from PyQt6 import sip
from PyQt6.QtWidgets import QPushButton

from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from start_page_format import EXAMPLE, parse_page, state_digest
from ui.start_page_review import StartPageReviewDialog
from ui.start_page_share import PageSourceShareDialog
from ui.task_dialog import TaskDialog


class PageFormatTests(unittest.TestCase):
    def test_format_validates_exact_data_and_keeps_local_image(self):
        definition = parse_page(json.dumps(EXAMPLE))
        state = {
            "settings": {"modules": [], "image": "", "feed": ""},
            "html": "old",
            "welcome_html": "welcome",
            "bookmarks": [],
        }
        candidate = definition.candidate(state)
        self.assertEqual(candidate["html"], "")
        self.assertEqual(candidate["welcome_html"], "welcome")
        self.assertEqual(state["html"], "old")
        bad = ["", "[]", '{"version":1,"version":1}', " " * 32769, "\ud800"]
        for source in bad:
            with self.subTest(source=source[:20]), self.assertRaises(ValueError):
                parse_page(source)
        for key, value in (
            ("version", True),
            ("page", "cookies"),
            ("mode", "execute"),
            ("callback", "eval"),
        ):
            document = {**EXAMPLE, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_page(json.dumps(document))

    def test_custom_welcome_and_native_fields_are_separate(self):
        definition = parse_page(
            json.dumps(
                {
                    "version": 1,
                    "name": "My welcome",
                    "page": "welcome",
                    "mode": "custom",
                    "html": "<h1>Hello</h1>",
                }
            )
        )
        self.assertEqual(definition.page, "welcome")
        document = deepcopy(EXAMPLE)
        document["settings"]["image"] = "file:///etc/passwd"
        with self.assertRaises(ValueError):
            parse_page(json.dumps(document))


class PageProposalTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for
    request = profiles.TaskProfileTests.request

    def command(self, name, *, client=None, **arguments):
        future = (client or self.service.broker).submit(name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def propose(self, task=None, request="one", source=None):
        task = task or self.command("task.create", objective="Design my new-tab page")
        receipt = self.command(
            "start_page.propose",
            task_id=task.task_id,
            request_id=request,
            source=source or json.dumps(EXAMPLE),
        )
        return task, receipt

    def apply(self, task, receipt, profile="work", **overrides):
        arguments = {
            "approve": True,
            "profile": profile,
            "profile_key": self.registry.approval_profile_key(
                "existing:" + profile, create=True
            ),
            "base": state_digest(self.registry.start_pages.state(profile)),
        }
        return self.service.start_pages.resolve(
            task.task_id, receipt.request_id, **{**arguments, **overrides}
        )

    def test_task_facade_discovers_proposes_and_polls_without_data_grant(self):
        task, receipt = self.propose()
        client = self.service.client(task.task_id)
        capabilities = self.command("start_page.capabilities", client=client)
        self.assertTrue(capabilities["available"])
        self.assertEqual(capabilities["pages"], ["newtab", "welcome", "style"])
        self.assertEqual(
            self.registry.start_pages.state("work")["settings"]["modules"],
            ["search", "bookmarks", "recent"],
        )
        self.assertEqual(self.service.store.grants(task.task_id), [])
        self.assertEqual(
            self.command("start_page.status", client=client, request_id="one"), receipt
        )
        future = client.submit("start_page.resolve", request_id="one", approve=True)
        self.app.processEvents()
        with self.assertRaises(TaskPermissionError):
            future.result()
        self.assertEqual(self.service.messages.summary(task.task_id)["pending"], 1)

    def test_global_profile_can_be_shared_explicitly_and_has_stable_identity(self):
        task = self.command("task.create", objective="Use Global with user review")
        grant = self.request(task.task_id, profile="existing:global")
        self.assertEqual(grant.status, "requested")
        self.assertNotIn(task.task_id, self.service._grants)
        key = self.registry.approval_profile_key("existing:global", create=True)
        self.assertEqual(key, self.registry.start_pages.profile_key("global"))
        self.assertEqual(
            self.registry.validate_task_profile("existing:global"), "global"
        )

    def test_user_application_is_profile_local_and_retry_does_not_apply(self):
        task, receipt = self.propose()
        applied = self.apply(task, receipt)
        self.assertEqual(applied.state, "applied")
        self.assertEqual(
            self.registry.start_pages.state("work")["settings"]["modules"],
            ["search", "bookmarks"],
        )
        self.assertIn(
            "recent", self.registry.start_pages.state("global")["settings"]["modules"]
        )
        self.assertEqual(self.propose(task)[1], applied)
        with self.assertRaises(ValueError):
            self.apply(task, receipt)
        with self.assertRaises(ValueError):
            self.propose(task, source=json.dumps({**EXAMPLE, "name": "Changed"}))

    def test_stale_page_identity_and_private_target_are_refused(self):
        task, receipt = self.propose()
        base = state_digest(self.registry.start_pages.state("work"))
        self.registry.start_pages.add_bookmark(
            "work", "https://example.org", "User edit"
        )
        with self.assertRaises(ValueError):
            self.apply(task, receipt, base=base)
        with self.assertRaises(ValueError):
            self.apply(task, receipt, profile_key="wrong")
        with self.assertRaises(ValueError):
            self.service.start_pages.resolve(
                task.task_id,
                receipt.request_id,
                approve=True,
                profile="search",
                profile_key="private",
                base=base,
            )
        self.assertEqual(
            self.service.start_pages.get(task.task_id, "one").state, "awaiting_approval"
        )

    def test_review_is_inert_defaults_decline_and_management_can_reopen(self):
        source = json.dumps(
            {
                "version": 1,
                "name": "Custom welcome",
                "page": "welcome",
                "mode": "custom",
                "html": "<script>window.location='https://example.org'</script><h1>Welcome</h1>",
            }
        )
        task, receipt = self.propose(source=source)
        dialog = StartPageReviewDialog(self.service, receipt)
        manager = TaskDialog(self.service)
        try:
            dialog.show()
            self.app.processEvents()
            self.assertEqual(
                self.registry.start_pages.state("global")["welcome_html"], ""
            )
            defaults = [
                b.text() for b in dialog.findChildren(QPushButton) if b.isDefault()
            ]
            self.assertEqual(defaults, ["Decline"])
            self.assertIn(
                "Pages & Scripts",
                [manager.panels.tabText(i) for i in range(manager.panels.count())],
            )
            dialog.profile.setCurrentText("work")
            dialog.apply()
            self.assertIn(
                "<h1>Welcome</h1>",
                self.registry.start_pages.state("work")["welcome_html"],
            )
        finally:
            if not sip.isdeleted(dialog):
                dialog.close()
            manager.close()
            manager.deleteLater()

    def test_source_sharing_requires_explicit_send_to_connected_task(self):
        task = self.command("task.create", objective="Edit the page source")
        self.service.external_connection(task.task_id, True)
        dialog = PageSourceShareDialog(
            self.service, "work", "newtab", "<h1>My page</h1>"
        )
        try:
            self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])
            dialog.submit()
            messages = self.service.messages.read(task.task_id)["messages"]
            self.assertEqual(len(messages), 1)
            payload = json.loads(messages[0]["text"])
            self.assertEqual(payload["html"], "<h1>My page</h1>")
            self.assertEqual(payload["type"], "steed_page_source")
            self.assertEqual(self.service.store.grants(task.task_id), [])
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_journal_failure_rolls_back_proposal_and_stops_broker(self):
        task = self.command("task.create", objective="Failed proposal")
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("failure"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.propose(task)
        self.assertEqual(self.service.start_pages.list(task.task_id), [])
        self.assertTrue(self.service.failure)

    def test_restart_interrupts_pending_and_does_not_reapply_enabled_page(self):
        task, receipt = self.propose()
        self.apply(task, receipt)
        self.propose(task, request="pending")
        self.service.close()
        restarted = TaskService(
            self.config.config_dir / "tasks.sqlite", profiles=self.registry
        )
        try:
            self.assertEqual(
                restarted.start_pages.get(task.task_id, "one").state, "applied"
            )
            self.assertEqual(
                restarted.start_pages.get(task.task_id, "pending").state, "interrupted"
            )
            self.assertEqual(
                restarted.store._db.execute("PRAGMA user_version").fetchone()[0], 21
            )
        finally:
            restarted.close()
