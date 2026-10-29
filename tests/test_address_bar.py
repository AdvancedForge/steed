"""Address bar edits survive page URL updates, keeping undo and redo."""

import time
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from qt_test_support import application

from ui.address_bar import AddressBar
from ui.chrome_style import chrome_colors


class AddressBarEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.bar = AddressBar()
        self.bar.show()
        self.bar.activateWindow()
        self.field = self.bar.address_input
        self.bar.set_url("https://example.org/start")
        self.field.setFocus()
        deadline = time.monotonic() + 5
        while not self.field.hasFocus() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(self.field.hasFocus())

    def tearDown(self):
        self.bar.close()
        self.bar.deleteLater()

    def type(self, text):
        self.field.selectAll()
        QTest.keyClicks(self.field, text)

    def test_page_updates_do_not_erase_typing_or_redo(self):
        self.type("steed docs")
        self.field.undo()
        self.assertTrue(self.field.isRedoAvailable())
        # A single-page app changes its URL while the user is mid-edit.
        self.bar.set_url("https://example.org/start#section")
        self.assertTrue(self.field.isRedoAvailable())
        self.field.redo()
        self.assertEqual(self.field.text(), "steed docs")

    def test_redo_shortcuts_work_in_the_field(self):
        self.type("abc")
        QTest.keyClick(self.field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        undone = self.field.text()
        self.assertNotEqual(undone, "abc")
        QTest.keyClick(
            self.field,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(self.field.text(), "abc")
        QTest.keyClick(self.field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(self.field, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.field.text(), "abc")

    def test_escape_restores_and_navigation_commits(self):
        self.type("draft")
        self.bar.set_url("https://example.org/next")
        self.assertEqual(self.field.text(), "draft")
        QTest.keyClick(self.field, Qt.Key.Key_Escape)
        self.assertEqual(self.field.text(), "https://example.org/next")
        self.type("example.com")
        emitted = []
        self.bar.url_changed.connect(emitted.append)
        QTest.keyClick(self.field, Qt.Key.Key_Return)
        self.assertEqual(emitted, ["example.com"])
        self.bar.set_url("https://example.com/")
        self.assertEqual(self.field.text(), "https://example.com/")


class ToolbarStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.bar = AddressBar()

    def tearDown(self):
        self.bar.deleteLater()

    def test_site_indicator_follows_the_page_scheme(self):
        for url, kind in (
            ("https://example.org/", "lock"),
            ("http://example.org/", "unlocked"),
            ("steed:welcome", "internal"),
            ("about:blank", "search"),
        ):
            self.bar.set_url(url)
            self.assertEqual(self.bar.site_kind, kind)
        self.bar.set_url("http://x.test/")
        self.assertIn("not encrypted", self.bar.site_action.toolTip())

    def test_buttons_use_icons_with_accessible_names_and_a_menu_badge(self):
        for name in ("back", "forward", "reload", "home", "passwords", "menu"):
            button = self.bar.buttons[name]
            self.assertFalse(button.icon().isNull(), name)
            self.assertTrue(button.accessibleName(), name)
            self.assertEqual(button.text(), "")
        self.bar.set_menu_badge("YOLO")
        self.assertEqual(self.bar.menu_btn.text(), "YOLO")
        self.assertEqual(self.bar.menu_btn.objectName(), "TextButton")
        self.bar.set_menu_badge("")
        self.assertEqual(self.bar.menu_btn.objectName(), "")

    def test_suggestions_keep_title_and_address(self):
        from browsing_history import HistoryEntry
        from ui.address_bar import TITLE_ROLE

        self.bar.set_history([HistoryEntry("https://docs.example/a", "Docs")])
        item = self.bar.history_model.item(0)
        self.assertEqual(item.data(TITLE_ROLE), "Docs")
        self.assertEqual(
            self.bar.completer.pathFromIndex(item.index()), "https://docs.example/a"
        )
        dark = chrome_colors("#1e1e1e", True)
        self.bar.apply_style(dark)
        self.assertIn(dark["accent"], self.bar.styleSheet())


if __name__ == "__main__":
    unittest.main()
