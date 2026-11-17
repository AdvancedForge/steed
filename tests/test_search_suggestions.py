"""Address bar search suggestions: opt-out setting, privacy limits, ranking."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import search_suggestions
from browser.browser_window import BrowserWindow
from ui.address_bar import KIND_ROLE


class Engine(BaseHTTPRequestHandler):
    queries = []

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
        type(self).queries.append((query, self.headers.get("Cookie")))
        body = json.dumps(
            [query, [query + " recipes", query + " history", query, 42, "x\u0000y"]]
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SearchSuggestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Engine)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{cls.server.server_port}/ac?q={{}}"
        cls.endpoints = patch.dict(search_suggestions.ENDPOINTS, {"duckduckgo": url})
        cls.endpoints.start()

    @classmethod
    def tearDownClass(cls):
        cls.endpoints.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Engine.queries.clear()
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home_patch = patch(
            "pathlib.Path.home", return_value=Path(self.directory.name)
        )
        self.home_patch.start()
        self.window = BrowserWindow()
        self.window.show()
        self.bar = self.window.address_bar

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def spin(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def type(self, text):
        field = self.bar.address_input
        field.setFocus()
        field.setText(text)
        self.bar._suggest(text)

    def rows(self, kind=None):
        model = self.bar.history_model
        items = [model.item(i) for i in range(model.rowCount())]
        return [
            (item.data(KIND_ROLE), item.text())
            for item in items
            if kind is None or item.data(KIND_ROLE) == kind
        ]

    def test_suggestions_follow_the_search_row_and_send_no_cookies(self):
        self.window.config.set("search_engine", "https://duckduckgo.com/?q=")
        self.window._refresh_address_sources()
        self.type("pasta")
        self.wait(lambda: self.rows("suggest"))
        self.assertEqual(self.rows()[0][0], "search")
        self.assertEqual(
            self.rows("suggest"),
            [("suggest", "pasta recipes"), ("suggest", "pasta history")],
        )
        self.assertEqual(Engine.queries, [("pasta", None)])
        # The same text is answered from the cache.
        self.type("pasta")
        self.assertEqual(len(self.rows("suggest")), 2)
        self.assertEqual(len(Engine.queries), 1)

    def test_nothing_is_sent_when_off_for_addresses_or_in_incognito(self):
        self.window.config.set("search_engine", "https://duckduckgo.com/?q=")
        self.window.config.set("search_suggestions", False)
        self.window._refresh_address_sources()
        self.type("private words")
        self.spin(0.5)
        self.window.config.set("search_suggestions", True)
        self.window._refresh_address_sources()
        self.type("example.org/secret-path")
        self.spin(0.5)
        self.window._switch_to_profile("search")
        self.type("incognito words")
        self.spin(0.5)
        self.assertEqual(Engine.queries, [])
        self.assertEqual(self.rows("suggest"), [])

    def test_an_unreachable_engine_gives_nothing_and_does_not_crash(self):
        """Offline or blocked: the failed reply has nothing to read."""
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        dead = f"http://127.0.0.1:{closed}/ac?q={{}}"
        with patch.dict(search_suggestions.ENDPOINTS, {"duckduckgo": dead}):
            self.window.config.set("search_engine", "https://duckduckgo.com/?q=")
            self.window._refresh_address_sources()
            self.type("offline query")
            remote = self.bar.remote
            self.wait(lambda: remote.reply is None and not remote.timer.isActive())
            self.spin(0.3)
        self.assertEqual(self.rows("suggest"), [])

    def test_unknown_engines_and_malformed_replies_give_nothing(self):
        self.assertIsNone(search_suggestions.endpoint("https://example.org/?q="))
        self.assertIsNotNone(search_suggestions.endpoint("https://www.google.com/?q="))
        self.assertIsNotNone(search_suggestions.endpoint("https://www.bing.com/?q="))
        for raw in (b"<html>", b"{}", b'["q", "not a list"]', b"\xff"):
            self.assertEqual(search_suggestions.parse(raw, "q"), [])
        many = json.dumps(["q", [f"q {n}" for n in range(9)]]).encode()
        self.assertEqual(len(search_suggestions.parse(many, "q")), 4)


if __name__ == "__main__":
    unittest.main()
