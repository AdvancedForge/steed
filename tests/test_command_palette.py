"""Command palette, tab search and Chrome-style tab keyboard shortcuts."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from ui.command_palette import menu_commands


class CommandPaletteTests(unittest.TestCase):
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
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()
        self.window.activateWindow()
        self.windows = [self.window]

    def tearDown(self):
        for window in reversed(self.windows):
            window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def wait(self, predicate, timeout=WAIT_SECONDS):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def js(self, palette, code):
        results = []
        palette.surface.page.runJavaScript(code, results.append)
        self.wait(lambda: bool(results))
        return results[0]

    def opened(self, mode=""):
        # A popup closes when another window activates, so settle focus first.
        self.window.raise_()
        self.window.activateWindow()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.window._open_palette(mode)
        palette = self.window._palette
        self.wait(lambda: palette.surface.ready and palette.isVisible())
        self.wait(
            lambda: (
                self.js(palette, "document.querySelectorAll('#results li').length") > 0
            )
        )
        return palette

    def test_menu_commands_include_nested_actions_with_their_path(self):
        commands = {
            text: path for path, text, _ in menu_commands(self.window._layout_menus)
        }
        self.assertIn("New Tab", commands)
        self.assertIn("Toggle Developer Tools", commands)
        self.assertIn("Developer Tools", commands["Toggle Developer Tools"])

    def test_typing_filters_and_enter_runs_a_command(self):
        before = len(self.window.tabs)
        palette = self.opened()
        self.js(
            palette,
            """const q = document.getElementById('query'); q.value = '>new tab';
            q.dispatchEvent(new Event('input')); true""",
        )
        first = self.js(
            palette, "document.querySelector('#results li .title').textContent"
        )
        self.assertEqual(first, "New Tab")
        self.js(
            palette,
            "document.getElementById('query').dispatchEvent("
            "new KeyboardEvent('keydown', {key: 'Enter'})); true",
        )
        self.wait(lambda: len(self.window.tabs) == before + 1)
        self.wait(lambda: not palette.isVisible())

    def test_tab_search_switches_to_tabs_in_other_windows(self):
        profile = self.window.profile_manager.current_profile_name
        self.window._pop_out_profile_named(profile)
        other = self.window._child_windows[-1]
        self.windows.append(other)
        target = other._new_tab(url="about:blank#find-me", return_tab=True)
        other._switch_tab(0)
        palette = self.opened("@")
        kinds = self.js(
            palette,
            "[...document.querySelectorAll('#results li')].map(li => li.className.split(' ')[0])",
        )
        self.assertEqual(set(kinds), {"tab"})
        token = next(key for key, item in palette.items.items() if item[2] is target)
        palette.command({"context": palette.context, "action": "choose", "id": token})
        self.assertIs(other.tabs[other.current_tab_index], target)
        # Stale or forged choices do nothing.
        palette.command({"context": "other", "action": "choose", "id": token})
        palette.command({"context": palette.context, "action": "choose", "id": "x"})

    def test_keyboard_shortcuts_move_between_tabs_from_a_web_page(self):
        window = self.window
        for _ in range(3):
            window._new_tab(url="about:blank")
        window._switch_tab(0)
        page = window.tabs[0]
        page.setFocus()
        self.app.processEvents()
        target = page.focusProxy() or page
        QTest.keyClick(target, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
        self.wait(lambda: window.current_tab_index == 1)
        QTest.keyClick(
            window.tabs[1].focusProxy() or window.tabs[1],
            Qt.Key.Key_9,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.wait(lambda: window.current_tab_index == len(window.tabs) - 1)
        window._jump_to_tab(1)
        self.assertEqual(window.current_tab_index, 0)
        window._cycle_tab(-1)
        self.assertEqual(window.current_tab_index, len(window.tabs) - 1)


if __name__ == "__main__":
    unittest.main()
