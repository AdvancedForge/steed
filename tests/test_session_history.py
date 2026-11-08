"""Session history: snapshots and closed windows, recorded and reopened."""

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

import session_history
from browser_window import BrowserWindow
from session_history import SessionHistory, describe
from tab_manager import TabState


def state(url, title=""):
    return TabState(url=url, title=title or url)


class StoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = SessionHistory(Path(directory.name))

    def test_unchanged_snapshots_are_skipped_and_limits_kept(self):
        windows = [[state("https://a.example/")]]
        self.assertIsNotNone(self.history.record("snapshot", windows))
        self.assertIsNone(self.history.record("snapshot", windows))
        self.history.record("closed_window", windows)
        self.assertEqual(
            [e["kind"] for e in self.history.entries()], ["closed_window", "snapshot"]
        )
        with patch.object(session_history, "MAX_SNAPSHOTS", 3):
            for n in range(5):
                self.history.record("snapshot", [[state(f"https://{n}.example/")]])
        kinds = [e["kind"] for e in self.history.entries()]
        self.assertEqual(kinds.count("snapshot"), 3)
        self.assertEqual(kinds.count("closed_window"), 1)
        self.assertIsNone(self.history.record("snapshot", [[]]))

    def test_file_is_validated_and_incognito_keeps_nothing(self):
        self.history.path.write_text(
            json.dumps(
                [
                    {
                        "id": "a",
                        "kind": "snapshot",
                        "saved_at": "x",
                        "windows": [[{"url": "https://a.example/", "title": "A"}]],
                        "groups": [{"group_id": "g", "name": "G", "color": "plaid"}],
                    },
                    {"id": "b", "kind": "other", "windows": []},
                    {"id": "c", "kind": "closed_window", "windows": [[]]},
                    "junk",
                ]
            )
        )
        (entry,) = self.history.entries()
        self.assertEqual(entry["windows"][0][0].title, "A")
        self.assertEqual(entry["groups"][0]["color"], "")
        private = SessionHistory(Path())
        self.assertIsNone(private.record("snapshot", [[state("https://a.example/")]]))
        self.assertEqual(private.entries(), [])

    def test_descriptions(self):
        now = datetime(2026, 11, 7, 15, 0).astimezone()
        entry = {
            "kind": "snapshot",
            "saved_at": (now - timedelta(days=1)).isoformat(),
            "windows": [[state("a")], [state("b"), state("c")]],
        }
        self.assertTrue(describe(entry, now).startswith("Yesterday"))
        self.assertTrue(describe(entry, now).endswith("Session · 2 windows, 3 tabs"))
        entry = {**entry, "kind": "closed_window", "windows": [[state("a")]]}
        self.assertTrue(describe(entry, now).endswith("Closed window · 1 tab"))


class WindowTests(unittest.TestCase):
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
        self.windows = [self.window]

    def tearDown(self):
        from PyQt6 import sip

        for window in reversed(self.windows):
            if not sip.isdeleted(window):
                window.close()
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

    def second_window(self, urls):
        profile = self.window.profile_manager.current_profile_name
        other = self.window._open_window(profile)
        self.windows.append(other)
        for url in urls:
            other._new_tab(url=url)
        return other

    def test_a_closed_window_can_be_reopened(self):
        other = self.second_window(
            ["https://example.org/one", "https://example.org/two"]
        )
        other.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        (closed,) = [
            e
            for e in self.window._session_history().entries()
            if e["kind"] == "closed_window"
        ]
        urls = [s.url for s in closed["windows"][0]]
        self.assertIn("https://example.org/one", urls)
        (reopened,) = self.window.reopen_closed_window()
        self.windows.append(reopened)
        tabs = [t.url().toString() for t in reopened.tabs]
        self.assertIn("https://example.org/two", tabs)
        # Only the shown tab loads; the rest wait to be selected.
        self.assertTrue(any(t.pending_url is not None for t in reopened.tabs))
        self.assertEqual(
            [
                e
                for e in self.window._session_history().entries()
                if e["kind"] == "closed_window"
            ],
            [],
        )
        self.assertEqual(self.window.reopen_closed_window(), [])

    def test_a_session_brings_back_its_windows_and_groups(self):
        tab = self.window._new_tab(url="https://example.org/grouped", return_tab=True)
        self.window._set_tab_group_existing(self.window.tabs.index(tab), "Trip")
        group = self.window.tab_manager.ensure_group("Trip")
        self.window.set_group_color(group.group_id, "green")
        self.second_window(["https://example.org/elsewhere"])
        entry = self.window._record_session("snapshot")
        self.assertEqual(len(entry["windows"]), 2)
        self.window.delete_group(group.group_id)
        opened = self.window.restore_session_entry(entry["id"])
        self.windows.extend(opened)
        self.assertEqual(len(opened), 2)
        restored = self.window.tab_manager.groups[group.group_id]
        self.assertEqual((restored.name, restored.color), ("Trip", "green"))
        grouped = [
            t
            for w in opened
            for t in w.tabs
            if t.url().toString() == "https://example.org/grouped"
        ]
        self.assertEqual(grouped[0].group, "Trip")
        # Sessions stay listed after opening.
        self.assertTrue(
            any(
                e["id"] == entry["id"] for e in self.window._session_history().entries()
            )
        )

    def test_dialog_lists_and_opens_entries(self):
        from ui.session_history_dialog import SessionHistoryDialog

        self.window._new_tab(url="https://example.org/listed")
        self.window._record_session("snapshot")
        dialog = SessionHistoryDialog(self.window)
        try:
            self.assertEqual(dialog.list.count(), 1)
            self.assertIn("Session", dialog.list.item(0).text())
            titles = [
                dialog.preview.item(i).toolTip() for i in range(dialog.preview.count())
            ]
            self.assertIn("https://example.org/listed", titles)
            with patch.object(
                self.window, "_open_window", wraps=self.window._open_window
            ) as opened:
                dialog.open_selected()
            self.assertEqual(opened.call_count, 1)
            dialog.delete_selected()
            self.assertEqual(self.window._session_history().entries(), [])
        finally:
            dialog.deleteLater()
            for child in list(self.window._child_windows):
                if child not in self.windows:
                    self.windows.append(child)

    def test_incognito_keeps_no_history(self):
        self.window._switch_to_profile("search")
        self.assertIsNone(self.window._record_session("snapshot"))


if __name__ == "__main__":
    unittest.main()
