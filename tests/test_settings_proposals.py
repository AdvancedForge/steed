"""Your agent proposing changes to Steed's settings: preferences, shortcuts,
search keywords, ad-blocking rules and per-site settings, applied only after
you review every change; any setting but how Steed treats agents, with
changes that weaken security or privacy marked."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import search_keywords, shortcuts
from browser.ad_blocker import own_rules
from browser.browser_window import BrowserWindow
from ui.settings_review import SettingsReviewDialog


class SettingsProposalTests(unittest.TestCase):
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
        self.config = self.window.config

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

    def task(self):
        task = self.command("task.create", objective="Tidy my settings")
        return task.task_id, self.service.client(task.task_id)

    def test_capabilities_offer_every_setting_but_how_agents_are_treated(self):
        task, client = self.task()
        found = self.command("settings.capabilities", client=client)
        self.assertIn("dark_pages", found["preferences"])
        for open_to_agents in ("safe_browsing", "https_first", "reader_engine"):
            self.assertIn(open_to_agents, found["preferences"])
        self.assertIs(found["preferences"]["safe_browsing"]["weakens_when"], False)
        self.assertIs(found["preferences"]["https_first"]["now"], True)
        for agents in ("keep_agent_chats", "agent_replay"):
            self.assertNotIn(agents, found["preferences"])
        self.assertIn("Command Palette…", found["shortcuts"])
        self.assertIn("w", found["search_keywords"])

    def test_a_reviewed_proposal_changes_every_area(self):
        task, client = self.task()
        receipt = self.command(
            "settings.propose",
            client=client,
            request_id="p1",
            summary="Dark pages, palette on Ctrl+K, MDN, no banner, bigger docs",
            preferences={"dark_pages": True, "visual_effects": "minimal"},
            shortcuts={"Command Palette…": "Ctrl+K"},
            search_keywords={
                "add": {"mdn": ["MDN", "https://developer.mozilla.org/search?q={q}"]}
            },
            blocking={
                "add_rules": ["example.com##.cookie-banner"],
                "allow_sites": ["shop.example"],
            },
            sites={"docs.example": {"zoom": 1.25, "autoplay": "block"}},
        )
        self.assertEqual(receipt, {"request_id": "p1", "state": "awaiting_approval"})
        self.assertFalse(self.config.get("dark_pages"))  # Nothing yet.
        (proposal,) = self.service.settings_proposals.pending(task)
        dialog = SettingsReviewDialog(self.service, proposal)
        try:
            shown = [
                dialog.table.item(r, 1).text() for r in range(dialog.table.rowCount())
            ]
            self.assertIn("Dark websites", shown)
            self.assertIn("Command Palette…", shown)
            self.assertIn("example.com##.cookie-banner", shown)
            dialog.apply()
        finally:
            dialog.deleteLater()
        self.assertEqual(
            self.command("settings.status", client=client, request_id="p1")["state"],
            "applied",
        )
        self.assertTrue(self.config.get("dark_pages"))
        self.assertEqual(self.config.get("visual_effects"), "minimal")
        self.assertEqual(shortcuts.overrides(self.config)["Command Palette…"], "Ctrl+K")
        self.assertEqual(
            search_keywords.search_url("mdn grid", self.config),
            "https://developer.mozilla.org/search?q=grid",
        )
        self.assertIn("example.com##.cookie-banner", own_rules(self.config))
        self.assertIn("shop.example", self.config.get("adblock_exceptions"))
        profile = self.window.profile_manager.current_profile_name
        site = self.registry.site_settings(profile).get("docs.example")
        self.assertEqual((site["zoom"], site["autoplay"]), (1.25, "block"))

    def test_denied_changes_nothing_and_unsafe_proposals_are_refused(self):
        task, client = self.task()
        self.command(
            "settings.propose",
            client=client,
            request_id="d",
            preferences={"dark_pages": True},
        )
        self.service.settings_proposals.resolve(task, "d", approve=False)
        self.assertFalse(self.config.get("dark_pages"))
        for bad in (
            {"preferences": {"keep_agent_chats": False}},
            {"preferences": {"secure_dns": "strict", "secure_dns_server": "http://x"}},
            {"preferences": {"visual_effects": "wild"}},
            {"shortcuts": {"Command Palette…": "not a key"}},
            {"search_keywords": {"add": {"x": ["X", "javascript:alert(1)"]}}},
            {"blocking": {"allow_sites": ["not a host"]}},
            {"sites": {"docs.example": {"forget": "forget"}}},
            {"summary": "nothing at all"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.command("settings.propose", client=client, request_id="b", **bad)

    def test_weakening_changes_are_allowed_and_marked(self):
        task, client = self.task()
        receipt = self.command(
            "settings.propose",
            client=client,
            request_id="w",
            preferences={"safe_browsing": False, "reader_engine": "readability"},
        )
        self.assertEqual(receipt["state"], "awaiting_approval")
        document = self.service.settings_proposals.requests[(task, "w")]["document"]
        rows = self.service.settings_proposals.changes(document)
        marked = [row for row in rows if "weakens security or privacy" in row[1]]
        self.assertEqual(len(marked), 1)
        self.service.settings_proposals.resolve(task, "w", approve=True)
        self.assertIs(self.config.get("safe_browsing"), False)
        self.assertEqual(self.config.get("reader_engine"), "readability")


if __name__ == "__main__":
    unittest.main()
