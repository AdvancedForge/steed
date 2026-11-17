"""Restored tabs load when selected and come back at their scroll position."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import memory_modes, webengine
from browser.browser_window import BrowserWindow
from browser.tab_manager import _parse_states

LONG = b"<title>%s</title>" + b"<p>line</p>" * 1500


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        body = LONG.replace(b"%s", self.path.strip("/").encode())
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LazyRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Pages)
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

    def js(self, tab, source):
        out = []
        webengine.run_script(tab.page(), source, out.append, world=webengine.PAGE_WORLD)
        self.wait(lambda: out)
        return out[0]

    def loaded(self, tab, title):
        self.wait(lambda: tab.title() == title and not tab.page().isLoading())

    def restart(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.window = BrowserWindow()
        self.window.show()

    def open_three(self):
        for name in ("one", "two", "three"):
            tab = self.window._new_tab(url=f"{self.origin}/{name}", return_tab=True)
            self.loaded(tab, name)
        two = self.window.tabs[-2]
        self.window._switch_tab(self.window.tabs.index(two))
        self.js(two, "window.scrollTo(0, 3000); true")
        self.wait(lambda: two.page().scrollPosition().y() >= 2999)
        self.window._close_tab(0)  # the start page
        self.window._switch_tab(0)

    def test_only_the_shown_tab_loads_and_scroll_comes_back(self):
        self.open_three()
        self.restart()
        tabs = self.window.tabs
        self.loaded(tabs[0], "one")
        self.assertEqual([t.title() for t in tabs], ["one", "two", "three"])
        for tab in tabs[1:]:
            self.assertIsNotNone(tab.pending_url)
            self.assertTrue(tab.page().url().isEmpty())
            self.assertTrue(tab.url().toString().startswith(self.origin))
        # Saving the session again keeps unloaded tabs exactly.
        self.window._save_current_tabs()
        saved = self.window.tab_manager.get_saved_tabs()
        self.assertEqual([s.title for s in saved], ["one", "two", "three"])
        self.assertEqual(saved[1].scroll, [0.0, 3000.0])
        self.window._switch_tab(1)
        self.loaded(tabs[1], "two")
        self.assertIsNone(tabs[1].pending_url)
        self.wait(lambda: self.js(tabs[1], "scrollY") >= 2999)
        self.assertIsNotNone(tabs[2].pending_url)

    def test_performance_mode_loads_every_restored_tab(self):
        self.open_three()
        memory_modes.apply_mode(self.window.config, "performance")
        self.restart()
        for tab, name in zip(self.window.tabs, ("one", "two", "three"), strict=True):
            self.assertIsNone(tab.pending_url)
            self.loaded(tab, name)

    def test_unloaded_tab_keeps_its_scroll_in_the_session(self):
        tab = self.window._new_tab(url=f"{self.origin}/far", return_tab=True)
        self.loaded(tab, "far")
        self.js(tab, "window.scrollTo(0, 2000); true")
        self.wait(lambda: tab.page().scrollPosition().y() >= 1999)
        self.window._switch_tab(0)
        self.wait(lambda: webengine.lightest_state(tab.page()) != webengine.ACTIVE)
        self.window._discard_tab(tab)
        self.wait(lambda: webengine.tab_state(tab.page()) == webengine.UNLOADED)
        self.window._save_current_tabs()
        saved = self.window.tab_manager.get_saved_tabs()
        self.assertEqual(saved[-1].scroll, [0.0, 2000.0])

    def test_saved_scroll_is_validated(self):
        states = _parse_states(
            [
                {"url": "https://a/", "scroll": [0, 10]},
                {"url": "https://b/", "scroll": [True, 1]},
                {"url": "https://c/", "scroll": [1, -5]},
                {"url": "https://d/", "scroll": "10"},
                {"url": "https://e/", "scroll": [1, 2, 3]},
            ]
        )
        self.assertEqual(
            [s.scroll for s in states], [[0.0, 10.0], None, None, None, None]
        )


if __name__ == "__main__":
    unittest.main()
