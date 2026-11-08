"""Split view, recently closed tabs in tab search, sites that open in a profile."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWidgets import QApplication, QWidget
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"<title>Page {self.path}</title><p>x".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class BrowsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.resize(1200, 700)
        self.window.show()

    def tearDown(self):
        for window in QApplication.topLevelWidgets():
            if isinstance(window, BrowserWindow) and window is not self.window:
                window.close()
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

    def open(self, path):
        tab = self.window._new_tab(url=self.origin + path, return_tab=True)
        self.wait(lambda: tab.title() == f"Page {path}" and not tab.page().isLoading())
        return tab

    def test_split_view_closed_tabs_and_site_profiles(self):
        window = self.window
        left, right, other = self.open("/left"), self.open("/right"), self.open("/x")
        window._switch_tab(window.tabs.index(left))
        window.show_beside(right)
        self.assertIs(window.split_tab, right)
        self.assertTrue(left.isVisible() and right.isVisible())
        self.assertFalse(other.isVisible())
        self.assertLess(left.geometry().right(), right.geometry().left())
        self.assertTrue(window._tab_keeps_running(right))
        # Clicking into the other side makes it the current tab.
        inside = right.findChildren(QWidget)
        window._focus_changed(None, inside[0] if inside else right)
        if inside:
            self.assertIs(window.tabs[window.current_tab_index], right)
            self.assertIs(window.split_tab, left)
            self.assertTrue(left.isVisible() and right.isVisible())
        window.close_split_view()
        self.assertIsNone(window.split_tab)
        self.assertEqual(
            [t.isVisible() for t in (left, right)].count(True), 1, "one tab shows"
        )
        window.show_beside(other)
        window._close_tab(window.tabs.index(other))
        self.assertIsNone(window.split_tab)

        # Tab search lists the closed tab, and choosing it reopens it.
        from ui.command_palette import CommandPalette

        palette = CommandPalette(window)
        rows = palette._rows()
        (closed,) = [r for r in rows if r["kind"] == "closed"]
        self.assertEqual(closed["detail"], self.origin + "/x")
        palette.command(
            {"context": palette.context, "action": "choose", "id": closed["id"]}
        )
        self.wait(
            lambda: any(t.url().toString() == self.origin + "/x" for t in window.tabs)
        )
        palette.dispose()
        palette.deleteLater()

        # A site set to open in another profile goes there when typed.
        window.profile_manager.create_profile("work")
        window.set_site_profile("127.0.0.1", "work")
        self.assertEqual(window.site_profile("127.0.0.1"), "work")
        count = len(window.tabs)
        left.setUrl(QUrl(self.origin + "/work"))

        def opened_in_work():
            return [
                w
                for w in QApplication.topLevelWidgets()
                if isinstance(w, BrowserWindow)
                and w is not window
                and w.profile_manager.current_profile_name == "work"
                and any(t.url().toString() == self.origin + "/work" for t in w.tabs)
            ]

        self.wait(opened_in_work)
        # The tab keeps its page (the engine shows the declined address briefly).
        self.wait(lambda: left.url().toString() == self.origin + "/left")
        self.assertEqual(len(window.tabs), count)
        window.set_site_profile("127.0.0.1", None)
        self.assertIsNone(window.site_profile("127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
