"""Group colours and saved groups: save and close, reopen, delete."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from tab_manager import GROUP_COLORS, TabManager
from ui.tab_strip import GROUP_COLORS as AUTOMATIC
from ui.tab_strip import group_palette


class PaletteTests(unittest.TestCase):
    def test_chosen_colours_win_and_the_rest_are_automatic(self):
        records = [{"group": name} for name in ("Work", "Fun", "Work", None, "News")]
        colors = group_palette(records, {"Fun": "#e5484d"})
        self.assertEqual(colors["Fun"], "#e5484d")
        self.assertEqual(colors["Work"], AUTOMATIC[0])
        self.assertEqual(colors["News"], AUTOMATIC[1])


class GroupTests(unittest.TestCase):
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

    def grouped(self, name, urls):
        tabs = []
        for url in urls:
            tab = self.window._new_tab(url=url, return_tab=True)
            self.window._set_tab_group_existing(self.window.tabs.index(tab), name)
            tabs.append(tab)
        return self.window.tab_manager.ensure_group(name), tabs

    def test_colours_are_shown_and_kept(self):
        group, _ = self.grouped("Work", ["https://example.org/a"])
        self.window.set_group_color(group.group_id, "red")
        self.assertEqual(
            self.window.tab_bar.group_colors, {"Work": GROUP_COLORS["red"]}
        )
        self.window.set_group_color(group.group_id, "chartreuse")
        self.assertEqual(group.color, "red")
        stored = json.loads(self.window.tab_manager.groups_file.read_text())
        self.assertEqual(stored[0]["color"], "red")
        again = TabManager(self.window.tab_manager.profile_dir)
        self.assertEqual(again.groups[group.group_id].color, "red")
        self.window.set_group_color(group.group_id, "")
        self.assertEqual(self.window.tab_bar.group_colors, {})

    def test_save_and_close_then_reopen(self):
        group, tabs = self.grouped(
            "Research", ["https://example.org/one", "https://example.org/two"]
        )
        self.window.set_group_color(group.group_id, "teal")
        before = len(self.window.tabs)
        saved = self.window.save_and_close_group(group.group_id)
        self.assertEqual(len(self.window.tabs), before - 2)
        self.assertNotIn(group.group_id, self.window.tab_manager.groups)
        self.assertEqual(
            [t["url"] for t in saved["tabs"]],
            ["https://example.org/one", "https://example.org/two"],
        )
        (listed,) = self.window.tab_manager.saved_groups()
        self.assertEqual(listed["color"], "teal")
        self.window._fill_saved_groups_menu()
        (entry,) = self.window.saved_groups_menu.actions()
        self.assertEqual(entry.text(), "Research (2 tabs)")
        # A group with the same name is open: the reopened one is renamed.
        self.grouped("Research", ["https://example.org/other"])
        reopened = self.window.open_saved_group(listed["group_id"])
        self.assertEqual(reopened.name, "Research 2")
        self.assertEqual(reopened.color, "teal")
        members = [t for t in self.window.tabs if t.group_id == reopened.group_id]
        self.assertEqual(
            [t.url().toString() for t in members],
            ["https://example.org/one", "https://example.org/two"],
        )
        # Only the selected one loads; the other waits until it is selected.
        self.assertIsNone(members[0].pending_url)
        self.assertIsNotNone(members[1].pending_url)
        self.assertEqual(self.window.tab_manager.saved_groups(), [])
        self.assertIsNone(self.window.open_saved_group(listed["group_id"]))

    def test_closing_the_only_group_keeps_the_window_and_delete_removes(self):
        for tab in list(self.window.tabs):
            tab.group = None
        group, _ = self.grouped("Only", ["https://example.org/x"])
        for tab in list(self.window.tabs):
            if tab.group_id != group.group_id:
                self.window._close_tab_object(tab)
        self.assertEqual(len(self.window.tabs), 1)
        self.window.save_and_close_group(group.group_id)
        self.assertEqual(len(self.window.tabs), 1)
        self.assertIsNone(self.window.tabs[0].group_id)
        (saved,) = self.window.tab_manager.saved_groups()
        self.window.tab_manager.delete_saved_group(saved["group_id"])
        self.assertEqual(self.window.tab_manager.saved_groups(), [])
        self.window._fill_saved_groups_menu()
        (empty,) = self.window.saved_groups_menu.actions()
        self.assertFalse(empty.isEnabled())

    def test_incognito_does_not_save_groups(self):
        manager = TabManager(Path())
        self.assertTrue(manager.private)
        self.assertEqual(manager.saved_groups(), [])
        group = manager.ensure_group("Secret")
        with self.assertRaises(ValueError):
            manager.save_closed_group(
                group, [{"url": "https://a.example/", "title": ""}]
            )

    def test_saved_file_is_validated(self):
        manager = self.window.tab_manager
        manager.saved_groups_file.write_text(
            json.dumps(
                [
                    {
                        "group_id": "a",
                        "name": "Good",
                        "color": "blue",
                        "tabs": [{"url": "https://x.example/", "title": "X"}],
                    },
                    {"group_id": "b", "name": "No tabs", "tabs": []},
                    {
                        "group_id": "c",
                        "name": "Bad colour",
                        "color": "plaid",
                        "tabs": [{"url": "https://y.example/"}, {"title": "no url"}],
                    },
                    "junk",
                ]
            )
        )
        names = [
            (g["name"], g["color"], len(g["tabs"])) for g in manager.saved_groups()
        ]
        self.assertEqual(names, [("Good", "blue", 1), ("Bad colour", "", 1)])


if __name__ == "__main__":
    unittest.main()
