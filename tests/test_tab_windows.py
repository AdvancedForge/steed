"""Per-window sessions, fresh second windows, and moving tabs between windows."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application, keep_directory

import tab_manager
from browser_window import _TAB_DRAGS, BrowserWindow


class TabWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home_patch = patch(
            "pathlib.Path.home", return_value=Path(self.directory.name)
        )
        self.home_patch.start()
        self.windows = []

    def tearDown(self):
        for window in reversed(self.windows):
            if not sip.isdeleted(window):
                window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        tab_manager._OPEN.clear()
        _TAB_DRAGS.clear()
        self.home_patch.stop()

    def root(self):
        window = BrowserWindow()
        window.show()
        self.windows.append(window)
        return window

    def settle(self):
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def session(self, window):
        return json.loads(window.tab_manager.tabs_file.read_text())

    def urls(self, window):
        return [window._tab_url(tab) for tab in window.tabs]

    def test_second_window_starts_fresh_and_each_window_saves_its_own_tabs(self):
        root = self.root()
        root._new_tab(url="about:blank#one")
        before = len(root.tabs)
        root._pop_out_profile_named(root.profile_manager.current_profile_name)
        child = root._child_windows[-1]
        self.assertEqual(len(child.tabs), 1)
        self.assertEqual(len(root.tabs), before)
        child._new_tab(url="about:blank#child")
        root._save_current_tabs()
        windows = self.session(root)["windows"]
        self.assertEqual(len(windows), 2)
        self.assertEqual(self.session(root)["version"], 2)
        # Closing a non-last window drops its tabs from the session.
        child.close()
        self.settle()
        self.assertEqual(len(self.session(root)["windows"]), 1)

    def test_quitting_keeps_every_window_and_restart_reopens_them(self):
        root = self.root()
        root._new_tab(url="about:blank#root")
        root._pop_out_profile_named(root.profile_manager.current_profile_name)
        child = root._child_windows[-1]
        child._new_tab(url="about:blank#child")
        root.close()
        self.settle()
        tab_manager._OPEN.clear()
        restarted = self.root()
        self.assertIn("about:blank#root", self.urls(restarted))
        reopened = [w for w in restarted._child_windows if not sip.isdeleted(w)]
        self.assertEqual(len(reopened), 1)
        self.assertIn("about:blank#child", self.urls(reopened[0]))
        self.assertNotIn("about:blank#child", self.urls(restarted))

    def test_old_single_list_sessions_still_load(self):
        root = self.root()
        root.close()
        self.settle()
        tab_manager._OPEN.clear()
        tabs_file = root.tab_manager.tabs_file
        tabs_file.write_text(
            json.dumps([{"url": "about:blank#legacy", "title": "Legacy"}])
        )
        restarted = self.root()
        self.assertEqual(self.urls(restarted), ["about:blank#legacy"])

    def test_moving_tabs_to_new_and_existing_windows(self):
        root = self.root()
        root._new_tab(url="about:blank#moving")
        root._new_tab(url="about:blank#second")
        moving = root.tabs[-2]
        target = root._move_tab(moving)
        self.assertNotIn(moving, root.tabs)
        self.assertEqual(self.urls(target), ["about:blank#moving"])
        profile = root.profile_manager.current_profile_name
        self.assertFalse(
            any(
                closed.state.url == "about:blank#moving"
                for closed in root.closed_tabs.entries(profile)
            )
        )
        second = root.tabs[-1]
        root._move_tab(second, target)
        self.assertIn("about:blank#second", self.urls(target))
        # A window's last tab cannot be moved away.
        self.assertEqual(len(root.tabs), 1)
        self.assertIsNone(root._move_tab(root.tabs[0], target))
        self.assertEqual(len(root.tabs), 1)

    def test_drag_tokens_move_tabs_and_forged_tokens_do_nothing(self):
        root = self.root()
        root._new_tab(url="about:blank#dragged")
        root._pop_out_profile_named(root.profile_manager.current_profile_name)
        other = root._child_windows[-1]
        index = len(root.tabs) - 1
        root._tab_drag_started("a" * 32, index)
        other._tab_dropped_here("b" * 32, 0)
        self.assertIn("about:blank#dragged", self.urls(root))
        other._tab_dropped_here("a" * 32, 0)
        self.assertNotIn("about:blank#dragged", self.urls(root))
        self.assertEqual(self.urls(other)[0], "about:blank#dragged")
        # Tokens are single use.
        other._tab_dropped_here("a" * 32, 0)
        self.assertEqual(self.urls(other).count("about:blank#dragged"), 1)


if __name__ == "__main__":
    unittest.main()
