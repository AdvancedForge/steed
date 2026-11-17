"""A tab whose page process dies: a panel with Reload, and the crash log."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import crash_log
from browser.browser_window import BrowserWindow


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<title>Fine</title><p>fine"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CrashedTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"

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
        self.window.resize(1000, 700)
        self.window.show()

    def tearDown(self):
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

    def test_a_killed_page_shows_a_panel_and_is_logged(self):
        from ui.crash_panel import CrashLogDialog

        tab = self.window._new_tab(url=self.url, return_tab=True)
        self.wait(lambda: tab.title() == "Fine" and not tab.page().isLoading())
        self.assertIsNone(tab.crash_panel)
        # Chromium's own way to end a page's process.
        tab.page().setUrl(QUrl("chrome://kill"))
        self.wait(lambda: tab.crash_panel is not None)
        self.assertTrue(tab.crash_panel.isVisible())
        self.assertEqual(tab.crash_panel.geometry(), tab.rect())
        (entry,) = crash_log.entries(self.window.config.config_dir)
        self.assertEqual(entry["reason"], "killed")
        self.assertNotIn("Fine", repr(entry))
        dialog = CrashLogDialog(self.window.config.config_dir, self.window)
        self.assertIn("was stopped", dialog.list.item(0).text())
        dialog.clear()
        self.assertEqual(crash_log.entries(self.window.config.config_dir), [])
        dialog.deleteLater()
        # Loading again takes the panel away.
        tab.setUrl(QUrl(self.url))
        self.wait(lambda: tab.crash_panel is None and tab.title() == "Fine")


if __name__ == "__main__":
    unittest.main()
