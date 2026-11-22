"""Packs: pages, skills and automations shared as one file, read with the
same checks as anything you make, and added only as you tick them."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from qt_test_support import application, keep_directory

from browser.browser_window import BrowserWindow
from services import packs

AUTOMATION = {
    "name": "Kettle check",
    "trigger": {"kind": "schedule", "at": "09:00", "days": [0]},
    "steps": [
        {"do": "read", "value": "https://shop.example/kettle"},
        {"do": "skill", "value": "Price check"},
        {"do": "notify", "value": "{last}"},
    ],
}


class PackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.registry = self.window.profile_manager.registry
        self.profile = self.window.profile_manager.current_profile_name

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def make_things(self):
        self.registry.start_pages.save_your_page(
            self.profile, "board", "Board", "<h1>Board</h1>"
        )
        skill = self.registry.skills.add(
            {
                "name": "Price check",
                "ask": "What does this cost?",
                "steps": [
                    {"open": "https://shop.example/"},
                    {"type": "#q", "text": "my street address"},
                ],
            }
        )
        automation = self.registry.automations.add(AUTOMATION, self.profile)
        watched = self.registry.automations.add(
            {
                "name": "On change",
                "trigger": {"kind": "watch", "watch": "w123"},
                "steps": [{"do": "notify", "value": "{last}"}],
            },
            self.profile,
        )
        return skill, automation, watched

    def test_export_holds_only_what_each_item_is(self):
        skill, automation, watched = self.make_things()
        pack = packs.build(
            self.registry,
            self.profile,
            pages=["board"],
            skills=[skill["id"]],
            automations=[automation["id"], watched["id"]],
            name="Shop tools",
        )
        text = packs.dumps(pack)
        for private in (skill["id"], automation["id"], "last_run", "profile", "w123"):
            self.assertNotIn(private, text)
        self.assertEqual(
            pack["pages"],
            [{"name": "board", "title": "Board", "html": "<h1>Board</h1>"}],
        )
        self.assertEqual(pack["automations"][1]["trigger"], {"kind": "manual"})
        self.assertTrue(packs.private_text("skills", pack["skills"][0]))
        self.assertFalse(packs.private_text("automations", pack["automations"][0]))

    def test_import_adds_ticked_items_paused_and_renamed_when_taken(self):
        skill, automation, _ = self.make_things()
        pack = packs.build(
            self.registry,
            self.profile,
            pages=["board"],
            skills=[skill["id"]],
            automations=[automation["id"]],
        )
        # The same skill name with another ask comes in renamed, and the
        # automation's skill step follows it.
        pack["skills"][0]["ask"] = "Something else"
        found = packs.read(packs.dumps(pack))
        outcome = packs.install(
            self.registry,
            self.profile,
            found,
            {"pages": [0], "skills": [0], "automations": [0]},
        )
        self.assertEqual(outcome["skipped"], [])
        self.assertIsNotNone(
            self.registry.start_pages.your_page(self.profile, "board-2")
        )
        self.assertIsNotNone(self.registry.skills.find("Price check (2)"))
        added = [a for a in self.registry.automations.list() if a["paused"]]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["steps"][1]["value"], "Price check (2)")
        # The identical skill is not added twice; unticked items are not added.
        found = packs.read(
            packs.dumps(packs.build(self.registry, self.profile, skills=[skill["id"]]))
        )
        outcome = packs.install(self.registry, self.profile, found, {"skills": [0]})
        self.assertEqual(outcome["added"], [])
        self.assertEqual(len(outcome["skipped"]), 1)
        with self.assertRaises(ValueError):
            packs.install(self.registry, "search", found, {"skills": [0]})

    def test_reading_checks_every_item(self):
        for bad in (
            "not json",
            json.dumps({"kind": "other"}),
            json.dumps({"kind": "steed-pack", "version": 99, "pages": []}),
            json.dumps({"kind": "steed-pack", "version": 1}),
            "x" * (packs.MAX_BYTES + 1),
        ):
            with self.assertRaises(ValueError, msg=bad[:40]):
                packs.read(bad)
        found = packs.read(
            json.dumps(
                {
                    "kind": "steed-pack",
                    "version": 1,
                    "pages": [
                        {"name": "settings", "title": "x", "html": "x"},
                        {"name": "ok", "title": "Fine", "html": "<p>fine</p>"},
                    ],
                    "automations": [
                        {"name": "bad", "steps": [{"do": "shell", "value": "rm"}]},
                        {
                            "name": "bad url",
                            "steps": [{"do": "open", "value": "file:///etc/passwd"}],
                        },
                    ],
                    "skills": "nonsense",
                }
            )
        )
        self.assertEqual([p["name"] for p in found["pages"]], ["ok"])
        self.assertEqual(found["automations"], [])
        self.assertEqual(found["skills"], [])
        self.assertEqual(len(found["problems"]), 3)

    def test_the_dialogs_save_and_review_a_pack(self):
        skill, automation, _ = self.make_things()
        tab = self.window.manage_packs()
        self.assertEqual(tab.workspace_name, "packs")  # A tab, like Downloads.
        dialog = tab.workspace_controller.source
        rows = [dialog.items.item(r) for r in range(dialog.items.count())]
        flagged = [r.text() for r in rows if "text a step types" in r.text()]
        self.assertEqual(
            flagged, ["Skill: Price check (includes text a step types: check it first)"]
        )
        path = Path(self.directory.name) / "shop.steedpack"
        self.assertFalse(dialog.save_to(path))  # Nothing ticked yet.
        for row in rows:
            if row.text().startswith(("Page", "Automation: Kettle")):
                row.setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.save_to(path))
        saved = json.loads(path.read_text())
        self.assertEqual(len(saved["pages"]), 1)
        self.assertEqual(saved["skills"], [])
        review = dialog.open_pack(path)
        self.assertEqual(review.items.count(), 2)
        self.assertIn("<h1>Board</h1>", review.detail.toPlainText())
        review.items.setCurrentRow(1)
        self.assertIn(
            "1. Read https://shop.example/kettle", review.detail.toPlainText()
        )
        review.items.item(0).setCheckState(Qt.CheckState.Unchecked)
        outcome = review.install()
        self.assertEqual(outcome["added"], ["Automation “Kettle check” (paused)"])
        self.assertIsNone(self.registry.start_pages.your_page(self.profile, "board-2"))
        broken = Path(self.directory.name) / "broken.steedpack"
        broken.write_text("{}")
        self.assertIsNone(dialog.open_pack(broken))
        self.assertIn("not a Steed pack", dialog.status.text())
        for name in ("pages", "automations", "skills"):
            opened = getattr(
                self.window,
                {"pages": "manage_your_pages"}.get(name, "manage_" + name),
            )()
            self.assertEqual(opened.workspace_name, name)
            self.assertEqual(
                self.window.address_bar.address_input.text(), "steed:" + name
            )


if __name__ == "__main__":
    unittest.main()
