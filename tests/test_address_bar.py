"""Address bar edits survive page URL updates, keeping undo and redo."""

import time
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from qt_test_support import application

from ui.address_bar import AddressBar


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


if __name__ == "__main__":
    unittest.main()
