"""Watches: Steed looks at a page in the background, with the profile's
sign-ins, and tells you when it changes in the way you asked about, asking
your agent (a stand-in here) to judge."""

import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from services import agent_once, watches

AGENT = [
    sys.executable,
    str(Path(__file__).parent / "fixtures" / "fake_watch_agent.py"),
]


class Shop(BaseHTTPRequestHandler):
    price = "59"
    cookie = None
    broken = False
    signed_out = None  # "redirect" or "form" while the session has expired

    def do_GET(self):
        Shop.cookie = self.headers.get("Cookie")
        if Shop.signed_out == "redirect" and self.path == "/kettle":
            self.send_response(302)
            self.send_header("Location", "/account/login?next=/kettle")
            self.end_headers()
            return
        if Shop.signed_out == "form" or self.path.startswith("/account/login"):
            body = b"<title>Sign in</title><form><input name=u><input type=password></form>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if Shop.broken:
            self.send_response(500)
            self.end_headers()
            return
        body = (
            "<title>Kettle</title><h1>Kettle</h1>"
            f"<p id=price>Price: ${Shop.price}</p>"
            f"<p id=time>Updated {time.monotonic()}</p>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class PartsTests(unittest.TestCase):
    def test_what_changed_and_what_the_agent_said(self):
        self.assertEqual(
            watches.change_summary("Price: $59 today", "Price: $44 today"),
            "“$59” is now “$44”.",
        )
        self.assertEqual(watches.change_summary("a", "a b"), "New: “b”.")
        self.assertEqual(
            watches.verdict_of("hmm\n**NOTIFY: Back in stock.**"), "Back in stock."
        )
        self.assertEqual(watches.verdict_of("QUIET"), "")
        self.assertIsNone(watches.verdict_of("I am not sure"))
        self.assertEqual(watches.clean_text("  a \n\n  b   c "), "a\nb c")


class WatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Shop)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/kettle"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Shop.price, Shop.broken, Shop.signed_out = "59", False, None
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.settle = patch.object(watches, "SETTLE_MS", 50)
        self.settle.start()
        self.window = BrowserWindow()
        self.service = self.window.profile_manager.registry.watches
        self.fired = []
        self.service.fired.connect(lambda watch, summary: self.fired.append(summary))

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.settle.stop()
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def look(self, watch):
        """Make it due, look, and wait for the look to finish."""
        for item in self.service.watches:
            if item["id"] == watch["id"]:
                item["last_checked"] = 0.0
        self.service.tick()
        self.wait(lambda: self.service.current is None and self.service.judge is None)

    def test_the_agent_decides_whether_a_change_is_news(self):
        watch = self.service.add(
            self.url,
            "global",
            "Kettle",
            "tell me when it's under $50",
            "#price",
            "Price: $59",
            "hourly",
        )
        with patch.object(agent_once, "agent_commands", return_value={"fake": AGENT}):
            Shop.price = "55"  # A change, but not what was asked: quiet.
            self.look(watch)
            self.assertEqual(self.fired, [])
            Shop.price = "44"
            self.look(watch)
        self.assertEqual(self.fired, ["The price dropped to $44."])
        saved = json.loads(self.service.path.read_text())[0]
        self.assertEqual(saved["last_text"], "Price: $44")
        self.assertEqual(saved["last_summary"], "The price dropped to $44.")

    def test_without_an_agent_any_change_is_reported_and_noise_is_ignored(self):
        part = self.service.add(
            self.url, "global", "Kettle", "", "#price", "Price: $59"
        )
        with patch.object(agent_once, "agent_commands", return_value={}):
            self.look(part)  # Only the clock on the page changed: nothing.
            self.assertEqual(self.fired, [])
            Shop.price = "61"
            self.look(part)
        self.assertEqual(self.fired, ["“$59” is now “$61”."])

    def test_a_sign_in_page_is_reported_once_and_watching_resumes(self):
        signed = []
        self.service.signed_out.connect(lambda watch: signed.append(watch["title"]))
        watch = self.service.add(self.url, "global", "Kettle", "", "#price", "")
        with patch.object(agent_once, "agent_commands", return_value={}):
            self.look(watch)  # The first look: what the page is like.
            self.assertFalse(self.service.list()[0]["password"])
            for mode in ("redirect", "form", "redirect"):
                Shop.signed_out = mode
                self.look(watch)
            # Told once, never as a change, and never counted as a failure.
            self.assertEqual(signed, ["Kettle"])
            self.assertEqual(self.fired, [])
            stored = self.service.list()[0]
            self.assertTrue(stored["signed_out"])
            self.assertEqual(stored["failures"], 0)
            self.assertEqual(stored["last_text"], "Price: $59")
            # Signed in again: back to watching, and changes count from before.
            Shop.signed_out, Shop.price = None, "45"
            self.look(watch)
        self.assertFalse(self.service.list()[0]["signed_out"])
        self.assertEqual(self.fired, ["“$59” is now “$45”."])

    def test_unreadable_pages_pause_after_five_tries_and_incognito_is_refused(self):
        watch = self.service.add(self.url, "global", "Kettle", "", "#nowhere", "x")
        for _ in range(5):
            self.look(watch)  # The part is gone from the page.
        self.assertTrue(self.service.list()[0]["paused"])
        with self.assertRaises(ValueError):
            self.service.add(self.url, "search", "Kettle")
        with self.assertRaises(ValueError):
            self.service.add("file:///etc/passwd", "global", "Local")
        self.service.remove(watch["id"])
        self.assertEqual(self.service.list(), [])

    def test_watches_survive_a_restart_and_close_with_steed(self):
        self.service.add(self.url, "global", "Kettle", "any change")
        again = watches.WatchService(self.window.profile_manager.registry)
        self.assertEqual([w["title"] for w in again.list()], ["Kettle"])
        again.close()
        self.assertTrue(again.closed)
        self.assertFalse(again.timer.isActive())

    def test_the_page_menu_offers_watching_and_the_page_lists_them(self):
        from PyQt6.QtWidgets import QMenu

        from ui.management_pages import controller
        from ui.watch_dialog import WatchDialog

        tab = self.window._new_tab(url=self.url, return_tab=True)
        self.wait(lambda: tab.title() == "Kettle")
        menu = QMenu()
        menu.addAction("Back")
        self.window._add_ask_agent_item(menu, tab, None)
        labels = [a.text() for a in menu.actions()]
        self.assertIn("Watch This Page with Agent…", labels)
        with patch.object(agent_once, "agent_commands", return_value={}):
            dialog = WatchDialog(
                self.service, self.url, "global", "Kettle", None, "Price: $59"
            )
            dialog.instruction.setText("when it's back in stock")
            dialog.save()
        page = controller(self.window, "watches")
        self.assertEqual(page.items.rowCount(), 1)
        self.assertEqual(page.items.item(0, 1).text(), "when it's back in stock")
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
