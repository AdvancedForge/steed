"""Clearing browsing data: by time, per site (with its stored data), on exit."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

import site_data
import webengine
from browser_window import BrowserWindow
from browsing_history import BrowsingHistory
from download_manager import DownloadManager, DownloadRecord


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<title>Site</title><p>hi"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", "visit=1; Path=/; Max-Age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Config(dict):
    config_dir = None

    def get(self, key, default=None):
        return super().get(key, default)


class PartsTests(unittest.TestCase):
    def test_history_and_downloads_by_time_and_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text('[{"url": "https://old.example/", "title": "Old"}]')
            history = BrowsingHistory(path)
            history.record("https://new.example/", "New")
            self.assertEqual(
                history.remove_since(time.time() - 60), ["https://new.example/"]
            )
            self.assertEqual(
                [e.url for e in history.entries()], ["https://old.example/"]
            )
            self.assertEqual(history.remove_since(0), ["https://old.example/"])
            history.close()

            config = Config()
            config.config_dir = Path(directory)
            with patch("pathlib.Path.home", return_value=Path(directory)):
                downloads = DownloadManager(config)
            downloads.history_file = Path(directory) / "downloads.json"
            downloads.records = {
                "a": DownloadRecord("a", "old", "/x/old", "p", "DownloadCompleted"),
                "b": DownloadRecord(
                    "b", "new", "/x/new", "p", "DownloadCompleted", started=time.time()
                ),
                "c": DownloadRecord(
                    "c", "other", "/x/o", "q", "DownloadCompleted", started=time.time()
                ),
            }
            self.assertEqual(downloads.forget_since(time.time() - 60, "p"), 1)
            self.assertEqual(sorted(downloads.records), ["a", "c"])
        self.assertEqual(
            site_data.origins_for(["Example.com"]),
            [
                "https://example.com",
                "http://example.com",
                "https://www.example.com",
                "http://www.example.com",
            ],
        )
        self.assertEqual(
            site_data.origins_for(["127.0.0.1", "localhost", "bad host"]),
            [
                "https://127.0.0.1",
                "http://127.0.0.1",
                "https://localhost",
                "http://localhost",
            ],
        )
        for host in ("www.127.0.0.1", "a.b.123", "bad host", "x..y", ""):
            self.assertFalse(site_data.safe_host(host), host)
        for host in ("127.0.0.1", "::1", "localhost", "example.com", "a.b.example"):
            self.assertTrue(site_data.safe_host(host), host)
        self.assertEqual(site_data.origins_for(["www.10.0.0.1"]), [])
        self.assertEqual(site_data.human_size(1536), "1.5 KB")
        self.assertEqual(site_data.human_size(12), "12 bytes")


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.servers = []
        cls.origins = []
        for _ in range(2):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.servers.append(server)
        # Two sites: 127.0.0.1 and localhost.
        cls.one = f"http://127.0.0.1:{cls.servers[0].server_port}"
        cls.two = f"http://localhost:{cls.servers[1].server_port}"

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()

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

    def evaluate(self, tab, script):
        found = []
        webengine.run_script(
            tab.page(), script, found.append, world=webengine.PAGE_WORLD
        )
        self.wait(lambda: found)
        return found[0]

    def visit(self, origin, path="/"):
        tab = self.window._new_tab(url=origin + path, return_tab=True)
        self.wait(lambda: tab.title() == "Site" and not tab.page().isLoading())
        self.evaluate(tab, "localStorage.setItem('kept', 'yes'); 1")
        return tab

    def stored(self, tab):
        tab.reload()
        self.wait(lambda: not tab.page().isLoading())
        return self.evaluate(tab, "localStorage.getItem('kept')")

    def cookies(self, host):
        return self.window.cookie_manager.get_cookies_for_domain(host)

    def test_one_site_its_data_and_not_the_others(self):
        one, two = self.visit(self.one), self.visit(self.two)
        self.wait(lambda: self.cookies("127.0.0.1") and self.cookies("localhost"))
        self.window.clear_site_data("127.0.0.1", ask=False)
        self.wait(lambda: not self.cookies("127.0.0.1"))
        self.wait(lambda: self.stored(one) is None)
        self.assertEqual(self.stored(two), "yes")
        self.assertTrue(self.cookies("localhost"))

    def test_a_time_range_clears_only_what_it_covers_and_exit_clears_too(self):
        from ui.clear_data_dialog import ClearDataDialog

        old = self.visit(self.one, "/old")
        history = self.window.history
        # Visited long ago (before the range).
        history._entries = {
            url: entry.__class__(entry.url, entry.title, 1.0)
            for url, entry in history._entries.items()
        }
        recent = self.visit(self.two, "/recent")
        self.window._close_tab(self.window.tabs.index(old))
        self.assertTrue(
            self.window.closed_tabs.entries(
                self.window.profile_manager.current_profile_name
            )
        )
        dialog = ClearDataDialog(self.window)
        try:
            dialog.range.setCurrentText("Last hour")
            for kind, box in dialog.boxes.items():
                box.setChecked(kind in ("history", "cookies", "closed"))
            dialog.exit.setChecked(True)
            dialog.clear()
            self.wait(lambda: dialog.status.text().startswith("Cleared"))
        finally:
            dialog.deleteLater()
        urls = [e.url for e in history.entries()]
        self.assertIn(self.one + "/old", urls)
        self.assertNotIn(self.two + "/recent", urls)
        self.wait(lambda: not self.cookies("localhost"))
        self.assertTrue(self.cookies("127.0.0.1"))
        self.assertIsNone(self.stored(recent))
        self.assertEqual(
            self.window.closed_tabs.entries(
                self.window.profile_manager.current_profile_name
            ),
            [],
        )
        self.assertEqual(
            self.window.config.get("clear_on_exit"), ["history", "cookies", "closed"]
        )
        # On closing, the chosen kinds go entirely.
        path = history.path
        self.window.close()
        self.assertEqual(BrowsingHistory(path).entries(), [])
        self.window.config.set("clear_on_exit", [])


if __name__ == "__main__":
    unittest.main()
