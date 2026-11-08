"""Copy Clean Link, Duplicate with History and Send Tab to Profile."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWidgets import QApplication
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from link_cleaning import clean_link


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"<title>{self.path.strip('/')}</title>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CleanLinkTests(unittest.TestCase):
    def test_tracking_parameters_go_and_the_rest_stay(self):
        self.assertEqual(
            clean_link(
                "https://shop.example/item?id=7&utm_source=mail&fbclid=X&color=red"
                "&UTM_Medium=y&gclid=Z#reviews"
            ),
            "https://shop.example/item?id=7&color=red#reviews",
        )
        self.assertEqual(
            clean_link("https://a.example/?utm_source=x"), "https://a.example/"
        )
        for unchanged in (
            "https://a.example/search?q=utm_source",
            "https://a.example/",
            "file:///tmp/x.html?utm_source=x",
        ):
            self.assertEqual(clean_link(unchanged), unchanged)


class QuickActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
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
        self.window.show()
        self.windows = [self.window]

    def tearDown(self):
        for window in reversed(self.windows + list(self.window._child_windows)):
            if not sip.isdeleted(window):
                window.close()
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

    def visit(self, tab, name):
        tab.setUrl(QUrl(f"{self.origin}/{name}"))
        self.wait(lambda: tab.title() == name and not tab.page().isLoading())

    def test_copy_clean_link(self):
        tab = self.window._new_tab(
            url=self.origin + "/page?utm_source=x&id=3", return_tab=True
        )
        self.wait(lambda: not tab.page().isLoading())
        link = self.window.copy_clean_link(tab)
        self.assertEqual(link, self.origin + "/page?id=3")
        self.assertEqual(QApplication.clipboard().text(), link)

    def test_duplicate_with_history_can_go_back(self):
        tab = self.window._new_tab(url=self.origin + "/one", return_tab=True)
        self.wait(lambda: tab.title() == "one")
        self.visit(tab, "two")
        self.visit(tab, "three")
        copy = self.window.duplicate_with_history(tab)
        self.wait(lambda: copy.title() == "three" and not copy.page().isLoading())
        self.assertEqual(self.window.tabs.index(copy), self.window.tabs.index(tab) + 1)
        self.assertEqual(copy.history().count(), 3)
        copy.back()
        self.wait(lambda: copy.title() == "two")
        self.assertEqual(tab.title(), "three")

    def test_send_tab_to_another_profile_and_to_incognito(self):
        self.window.profile_manager.create_profile("work")
        tab = self.window._new_tab(url=self.origin + "/report", return_tab=True)
        self.wait(lambda: tab.title() == "report")
        opened = self.window.send_tab_to_profile(tab, "work")
        target = opened.browser_window
        self.assertEqual(target.profile_manager.current_profile_name, "work")
        self.wait(lambda: opened.title() == "report")
        self.assertNotIn(tab, self.window.tabs)
        tab = self.window._new_tab(url=self.origin + "/secret", return_tab=True)
        self.wait(lambda: tab.title() == "secret")
        private = self.window.send_tab_to_profile(tab, "search")
        self.assertEqual(
            private.browser_window.profile_manager.current_profile_name, "search"
        )
        self.wait(lambda: private.title() == "secret")
        # A window keeps its last tab.
        only = self.window.tabs[0]
        for other in list(self.window.tabs[1:]):
            self.window._close_tab_object(other)
        self.window.send_tab_to_profile(only, "work")
        self.assertIn(only, self.window.tabs)


if __name__ == "__main__":
    unittest.main()
