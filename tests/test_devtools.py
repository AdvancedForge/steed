"""Chrome-style Developer Tools: per-tab panels, shortcuts and docking."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWebEngineCore import QWebEnginePage
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow


class DevToolsTests(unittest.TestCase):
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
        self.window.resize(1100, 700)
        self.window.show()
        self.host = self.window.devtools

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def wait(self, predicate, timeout=WAIT_SECONDS):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for Developer Tools")

    def current(self):
        return self.window.tabs[self.window.current_tab_index]

    def frontend_value(self, tab, expression):
        results = []
        self.host.views[tab].page().runJavaScript(expression, results.append)
        self.wait(lambda: bool(results))
        return results[0]

    def test_shortcut_toggles_a_panel_for_the_current_tab_only(self):
        first = self.current()
        self.window._toggle_devtools()
        self.assertTrue(self.host.is_open(first))
        self.assertIsNotNone(first.page().devToolsPage())
        self.assertTrue(self.host.stack.isVisible())
        self.window._new_tab(url="about:blank")
        self.assertFalse(self.host.stack.isVisible())
        self.window._switch_tab(self.window.tabs.index(first))
        self.assertTrue(self.host.stack.isVisible())
        self.window._toggle_devtools()
        self.assertFalse(self.host.is_open(first))
        self.assertIsNone(first.page().devToolsPage())
        self.assertFalse(self.host.stack.isVisible())

    def test_console_and_element_picker_run_in_the_frontend(self):
        tab = self.current()
        self.window._devtools_console()
        self.wait(
            lambda: (
                self.frontend_value(tab, "window.__steedDevtoolsAction") is not None
            ),
            timeout=30,
        )
        self.assertIs(self.frontend_value(tab, "window.__steedDevtoolsAction"), True)
        self.window._devtools_pick()
        self.wait(
            lambda: self.frontend_value(tab, "window.__steedDevtoolsAction") is True,
            timeout=30,
        )

    def test_docking_sides_are_remembered(self):
        self.window._toggle_devtools()
        self.host.set_side("bottom")
        self.assertEqual(self.host.content.orientation(), Qt.Orientation.Vertical)
        self.host.set_side("window")
        self.assertTrue(self.host.separate.isVisible())
        self.assertEqual(self.window.config.get("devtools_side"), "window")
        self.host.set_side("right")
        self.assertEqual(self.host.content.orientation(), Qt.Orientation.Horizontal)
        self.assertFalse(self.host.separate.isVisible())
        self.assertTrue(self.host.stack.isVisible())

    def test_closing_a_tab_closes_its_panel_and_agent_owned_pages_are_refused(self):
        self.window._new_tab(url="about:blank")
        tab = self.current()
        self.window._toggle_devtools()
        self.window._close_tab(self.window.tabs.index(tab))
        self.assertFalse(self.host.is_open(tab))
        owned = self.current()
        holder = QWebEnginePage(owned)
        owned.page().setDevToolsPage(holder)
        self.window._toggle_devtools()
        self.assertFalse(self.host.is_open(owned))
        self.assertIs(owned.page().devToolsPage(), holder)
        self.assertIn("agent", self.window.status_bar.currentMessage())
        owned.page().setDevToolsPage(None)


if __name__ == "__main__":
    unittest.main()
