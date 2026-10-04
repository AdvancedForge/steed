"""Download filtering keeps controls and copied paths bound to stable records."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QPushButton
from qt_test_support import application

from download_manager import DownloadManager, DownloadRecord
from ui.download_dialog import DownloadDialog


class DownloadDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.manager = DownloadManager(SimpleNamespace(config_dir=self.root))
        self.manager.records = {
            "budget": DownloadRecord(
                "budget",
                "Budget %.pdf",
                str(self.root / "Exports" / "Budget %.pdf"),
                "work",
            ),
            "notes": DownloadRecord(
                "notes", "Notes.txt", str(self.root / "Notes.txt"), "global"
            ),
        }
        self.dialog = DownloadDialog(self.manager)

    def tearDown(self):
        self.dialog.accept()
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def select(self, key):
        for row in range(self.dialog.table.rowCount()):
            self.dialog.table.setCurrentCell(row, 0)
            if self.dialog.selected_key() == key:
                return
        self.fail("Expected download is missing")

    def test_literal_search_matches_filename_profile_and_destination(self):
        for query in ("BUDGET", "work", "/exports/", "%"):
            with self.subTest(query=query):
                self.dialog.search.setText(query)
                self.assertEqual(self.dialog.table.rowCount(), 1)
                self.dialog.table.setCurrentCell(0, 0)
                self.assertEqual(self.dialog.selected_key(), "budget")
        self.dialog.search.setText("not present")
        self.assertEqual(self.dialog.table.rowCount(), 0)
        self.dialog.search.clear()
        self.assertEqual(self.dialog.table.rowCount(), 2)

    def test_progress_and_new_rows_preserve_identity_but_filtering_clears_it(self):
        self.select("budget")
        self.manager.records["budget"].received = 20
        self.manager.changed.emit()
        self.assertEqual(self.dialog.selected_key(), "budget")
        self.manager.records["new"] = DownloadRecord(
            "new", "New.txt", str(self.root / "New.txt"), "global"
        )
        self.manager.changed.emit()
        self.assertEqual(self.dialog.selected_key(), "budget")
        self.dialog.search.setText("notes")
        self.assertIsNone(self.dialog.selected_key())
        with patch.object(self.manager, "control") as control:
            self.dialog.perform("cancel")
            control.assert_not_called()
            self.select("notes")
            self.dialog.perform("pause")
            control.assert_called_once_with("notes", "pause")

    def test_enter_in_search_does_not_pause_or_close_the_selected_download(self):
        self.dialog.show()
        self.dialog.search.setText("budget")
        self.select("budget")
        self.dialog.search.setFocus()
        self.app.processEvents()
        with patch.object(self.manager, "control") as control:
            QTest.keyClick(self.dialog.search, Qt.Key.Key_Return)
            control.assert_not_called()
            self.assertTrue(self.dialog.isVisible())
            self.assertEqual(self.dialog.selected_key(), "budget")

    def test_copy_path_button_uses_full_selected_destination_without_opening_it(self):
        self.dialog.search.setText("budget")
        self.select("budget")
        button = next(
            b for b in self.dialog.findChildren(QPushButton) if b.text() == "Copy Path"
        )
        with (
            patch("ui.download_dialog.QApplication.clipboard") as clipboard,
            patch("ui.download_dialog.QDesktopServices.openUrl") as open_url,
            patch.object(self.manager, "control") as control,
        ):
            button.click()
            clipboard.return_value.setText.assert_called_once_with(
                self.manager.records["budget"].path
            )
            open_url.assert_not_called()
            control.assert_not_called()

    def test_copy_without_selection_or_after_record_removal_leaves_clipboard_alone(
        self,
    ):
        with patch("ui.download_dialog.QApplication.clipboard") as clipboard:
            self.dialog.perform("copy_path")
            self.select("notes")
            del self.manager.records["notes"]
            self.dialog.perform("copy_path")
            clipboard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
