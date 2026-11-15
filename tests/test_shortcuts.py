"""Your own keyboard shortcuts: change, clear, take one from another
command, reset; every window follows and they are kept."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication
from qt_test_support import application, keep_directory

import shortcuts
from browser_window import BrowserWindow
from ui.shortcuts_dialog import ShortcutsDialog


def keys(window, name):
    action = dict(shortcuts.commands(window))[name]
    return [
        k.toString(QKeySequence.SequenceFormat.PortableText) for k in action.shortcuts()
    ]


class ShortcutTests(unittest.TestCase):
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
        for window in list(QApplication.topLevelWidgets()):
            if isinstance(window, BrowserWindow) and not window._closing:
                window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def test_change_take_clear_and_reset(self):
        window = self.window
        names = [name for name, _ in shortcuts.commands(window)]
        self.assertIn("Reload", names)
        self.assertEqual(keys(window, "New Tab"), ["Ctrl+T"])
        zoom = keys(window, "Zoom In")
        self.assertGreaterEqual(len(zoom), 1)
        other = window.new_window()
        self.assertIsNone(shortcuts.change(window, "Reload", "Ctrl+Alt+K"))
        self.assertEqual(keys(window, "Reload"), ["Ctrl+Alt+K"])
        self.assertEqual(keys(other, "Reload"), ["Ctrl+Alt+K"])  # Every window.
        self.assertEqual(window.config.get("shortcuts"), {"Reload": "Ctrl+Alt+K"})
        # Taking New Tab's shortcut leaves New Tab without one.
        self.assertEqual(shortcuts.change(window, "Reload", "Ctrl+T"), "New Tab")
        self.assertEqual(keys(window, "New Tab"), [])
        shortcuts.change(window, "Print…", "")
        self.assertEqual(keys(window, "Print…"), [])
        self.assertEqual(keys(window, "Zoom In"), zoom)  # Untouched ones keep all.
        shortcuts.reset(window, "Reload")
        self.assertNotEqual(keys(window, "Reload"), ["Ctrl+T"])
        shortcuts.reset(window)
        self.assertEqual(keys(window, "New Tab"), ["Ctrl+T"])
        self.assertEqual(keys(window, "Print…"), ["Ctrl+P"])
        self.assertEqual(window.config.get("shortcuts"), {})
        # A new window starts with yours.
        shortcuts.change(window, "Reload", "Ctrl+Alt+K")
        third = window.new_window()
        self.assertEqual(keys(third, "Reload"), ["Ctrl+Alt+K"])

    def test_the_page_finds_and_clears(self):
        dialog = ShortcutsDialog(self.window)
        try:
            dialog.search.setText("print")
            self.assertEqual(dialog.table.rowCount(), 1)
            dialog.table.selectRow(0)
            dialog.clear_selected()
            self.assertEqual(keys(self.window, "Print…"), [])
            dialog.reset_all()
            self.assertEqual(keys(self.window, "Print…"), ["Ctrl+P"])
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
