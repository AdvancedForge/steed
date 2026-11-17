"""The lighter native interface: Qt tab strip, menus, palette and dialogs."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint
from PyQt6.QtWidgets import QMenu
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from ui.html_surface import HtmlSurface
from ui.interface_mode import native, set_native


class NativeInterfaceTests(unittest.TestCase):
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
        first = BrowserWindow()
        first.config.set("native_interface", True)
        first.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()
        self.strip = self.window.tab_bar
        self.settle()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        set_native(False)
        self.home_patch.stop()

    def settle(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait(self, predicate, timeout=WAIT_SECONDS):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def surfaces(self):
        return [s for s in self.window.findChildren(HtmlSurface) if not s.disposed]

    def test_native_tab_strip_drives_window_tabs_without_a_renderer(self):
        self.assertTrue(native())
        self.assertEqual(type(self.strip).__name__, "NativeTabStrip")
        self.assertEqual(self.surfaces(), [])
        for url in ("about:blank#one", "about:blank#two"):
            self.window._new_tab(url=url)
        self.settle()
        bar = self.strip.bar
        self.assertEqual(bar.count(), len(self.window.tabs))
        bar.setCurrentIndex(0)
        self.wait(lambda: self.window.current_tab_index == 0)
        moved = self.window.tabs[0]
        bar.moveTab(0, 2)
        self.wait(lambda: self.window.tabs.index(moved) == 2)
        self.assertEqual(self.strip.current, self.window.current_tab_index)
        before = len(self.window.tabs)
        bar.tabCloseRequested.emit(1)
        self.wait(lambda: len(self.window.tabs) == before - 1)
        self.settle()
        self.assertEqual(bar.count(), len(self.window.tabs))

    def test_native_tab_strip_shows_groups_pins_and_collapse(self):
        self.window._new_tab(url="about:blank#grouped")
        self.settle()
        self.strip.set_tab_group(1, "Research")
        self.strip.set_tab_state(0, pinned=True)
        self.strip._publish()
        bar = self.strip.bar
        self.assertEqual(bar.tabText(0), "")
        self.assertIn("Research", bar.tabToolTip(1))
        self.assertTrue(bar.tabTextColor(1).isValid())
        self.strip.set_current_tab(0)
        self.strip.set_collapsed_groups({"Research"})
        self.strip._publish()
        self.assertFalse(bar.isTabVisible(1))
        self.strip.apply_appearance({**self.strip.appearance, "position": "left"})
        self.settle()
        self.assertEqual(bar.shape().name, "RoundedWest")

    def test_native_palette_runs_commands(self):
        before = len(self.window.tabs)
        self.window._open_palette()
        palette = self.window._palette
        self.assertTrue(palette.native)
        self.assertIsNone(palette.surface)
        palette.query.setText(">new tab")
        self.assertEqual(palette.results.item(0).text().split("    ")[0], "New Tab")
        palette.query.returnPressed.emit()
        self.wait(lambda: len(self.window.tabs) == before + 1)
        self.assertFalse(palette.isVisible())

    def test_native_context_menus_and_dialogs_use_qt_widgets(self):
        menu = QMenu(self.window)
        menu.addAction("Synthetic")
        shown = self.window._show_html_menu(
            menu, self.window.mapToGlobal(QPoint(50, 50)), context=True
        )
        self.assertIs(shown, menu)
        menu.hide()
        self.settle()
        self.assertEqual(self.surfaces(), [])
        from ui.download_dialog import DownloadDialog

        dialog = DownloadDialog(self.window.download_manager, self.window)
        dialog.show()
        self.settle()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(self.window._html_dialogs, [])
        dialog.accept()

    def test_switching_back_to_html_replaces_the_palette(self):
        self.window._open_palette()
        self.window._palette.hide()
        self.window.config.set("native_interface", False)
        self.window.profile_manager.registry.settings_changed.emit()
        self.assertFalse(native())
        self.assertIsNone(self.window._palette)
        self.window._open_palette()
        self.assertFalse(self.window._palette.native)
        self.window._palette.hide()


class PaletteReleaseTests(unittest.TestCase):
    wait = NativeInterfaceTests.wait

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
        self.window.show()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def test_hidden_html_palette_frees_its_renderer(self):
        self.window._open_palette()
        palette = self.window._palette
        self.assertFalse(palette.native)
        palette.keep_ms = 50
        palette.hide()
        self.wait(lambda: self.window._palette is None)
        self.assertTrue(palette.surface.disposed)
        # Reopening builds a new one.
        self.window._open_palette()
        self.assertIsNot(self.window._palette, palette)
        self.window._palette.hide()


if __name__ == "__main__":
    unittest.main()
