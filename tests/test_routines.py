"""Routines: your agent on a schedule, with pages read first (a local site
here); each run is a new conversation; missed runs catch up within a day."""

import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from services import routines
from services.routines import check, describe, last_due
from ui.desktop_notify import DesktopNotifier
from ui.routines_dialog import RoutineEditor


class Inbox(BaseHTTPRequestHandler):
    orders_seen = 0
    news = 0

    def do_GET(self):
        body = b"<title>Inbox</title><p>3 unread: rent due Friday</p>"
        if self.path == "/start":
            body = (
                b"<title>Shop</title><label>Search <input id=q></label>"
                b"<a href='/orders'>Open orders</a>"
            )
        if self.path == "/orders":
            body = b"<title>Orders</title><p>Order 42 shipped</p>"
            Inbox.orders_seen += 1
        if self.path == "/news":
            Inbox.news += 1
            body = f"<title>News</title><p>Story {Inbox.news}</p>".encode()
        if self.path == "/mail":
            body = (
                b"<title>Mail</title><p>Secret mail list</p>"
                b"<form>Password <input type=password></form>"
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ScheduleTests(unittest.TestCase):
    def test_checking_and_when(self):
        daily = check(
            {
                "name": " Morning ",
                "ask": "Brief me",
                "at": "8:05",
                "days": [0, 1, 2, 3, 4],
            }
        )
        self.assertEqual((daily["name"], daily["at"]), ("Morning", "08:05"))
        self.assertEqual(describe(daily), "Weekdays at 08:05")
        for bad in (
            {"name": "", "ask": "x"},
            {"name": "a", "ask": "x", "at": "25:00"},
            {"name": "a", "ask": "x", "days": []},
            {"name": "a", "ask": "x", "kind": "hours", "every": 5},
            {"name": "a", "ask": "x", "pages": ["file:///etc/passwd"]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check(bad)
        # Thursday 2026-11-12 10:00: the last weekday 08:05 was that morning.
        thursday = datetime(2026, 11, 12, 10, 0)
        self.assertEqual(last_due(daily, thursday), datetime(2026, 11, 12, 8, 5))
        # Sunday: Friday's run.
        sunday = datetime(2026, 11, 15, 9, 0)
        self.assertEqual(last_due(daily, sunday), datetime(2026, 11, 13, 8, 5))
        hourly = {
            **check({"name": "a", "ask": "x", "kind": "hours", "every": 2}),
            "created": thursday.timestamp(),
        }
        self.assertIsNone(last_due(hourly, datetime(2026, 11, 12, 11, 0)))
        self.assertEqual(
            last_due(hourly, datetime(2026, 11, 12, 15, 30)),
            datetime(2026, 11, 12, 14, 0),
        )


class RoutineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Inbox)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/inbox"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.settle = patch.object(routines, "SETTLE_MS", 50)
        self.settle.start()
        self.window = BrowserWindow()
        self.window.show()
        self.service = self.window.profile_manager.registry.routines

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

    def test_a_run_reads_its_pages_and_starts_a_new_conversation(self):
        self.window._watch_alerts()  # Connects routine delivery too.
        routine = self.service.add(
            {"name": "Morning", "ask": "What matters today?", "pages": [self.url]}
        )
        self.assertIsNone(self.service.due())  # Not before its time.
        with (
            patch.object(
                BrowserWindow, "agent_message", return_value="task"
            ) as message,
            patch.object(DesktopNotifier, "notify") as notify,
        ):
            self.service.run(routine)
            self.wait(lambda: message.called)
        text = message.call_args.args[0]
        self.assertTrue(text.startswith("Routine “Morning”: What matters today?"))
        self.assertIn("rent due Friday", text)
        self.assertEqual(message.call_args.kwargs, {"send": True, "new_chat": True})
        self.assertIn("is running", notify.call_args.args[0])
        # Missed while closed: it runs once, then not again.
        for item in self.service.routines:
            item["last_run"] = 0
            item["kind"], item["at"], item["days"] = "daily", "00:00", list(range(7))
        self.assertIsNotNone(self.service.due())
        # Paused agents (Agents → Pause All Agents): it waits, then runs.
        self.window.pause_agents_action.setChecked(True)
        self.assertTrue(self.window.profile_manager.registry.tasks.agents_paused)
        self.service.tick()
        self.assertIsNone(self.service.running)
        self.assertEqual(self.window.address_bar.menu_btn.text(), "⏸ Agents paused")
        self.window.pause_agents_action.setChecked(False)
        with patch.object(BrowserWindow, "agent_message", return_value="task"):
            self.service.tick()
            self.wait(lambda: self.service.running is None)
        self.assertIsNone(self.service.due())
        self.service.pause(routine["id"])
        self.assertTrue(self.service.list()[0]["paused"])

    def test_a_page_that_asks_you_to_sign_in_comes_to_you(self):
        self.window._watch_alerts()
        mail = self.url.replace("/inbox", "/mail")
        routine = self.service.add(
            {"name": "Mail", "ask": "Anything new?", "pages": [mail, self.url]}
        )
        with (
            patch.object(
                BrowserWindow, "agent_message", return_value="task"
            ) as message,
            patch.object(DesktopNotifier, "notify") as notify,
        ):
            self.service.run(routine)
            self.wait(lambda: message.called)
        text = message.call_args.args[0]
        self.assertIn(routines.SIGNED_OUT, text)
        self.assertNotIn("Secret mail list", text)
        self.assertIn("rent due Friday", text)
        titles = [call.args[0] for call in notify.call_args_list]
        self.assertIn("Routine “Mail” needs you to sign in", titles)

    def run_and_wait(self, routine):
        with (
            patch.object(
                BrowserWindow, "agent_message", return_value="task"
            ) as message,
            patch.object(DesktopNotifier, "notify") as notify,
        ):
            self.service.run(routine)
            self.wait(lambda: self.service.running is None)
        return message, notify

    def test_saved_steps_run_without_a_model_and_hand_over_when_they_break(self):
        self.window._watch_alerts()
        start = self.url.replace("/inbox", "/start")
        routine = self.service.add(
            {
                "name": "Orders",
                "ask": "Anything shipped?",
                "steps": [
                    {"open": start},
                    {"type": "Search", "text": "kettle"},
                    {"click": "Open orders"},
                    {"wait": "shipped"},
                    {"read": True},
                ],
            }
        )
        message, _ = self.run_and_wait(routine)
        text = message.call_args.args[0]
        self.assertIn("Order 42 shipped", text)
        self.assertNotIn("stopped", text)
        broken = self.service.add(
            {
                "name": "Broken",
                "ask": "Anything shipped?",
                "steps": [{"open": start}, {"click": "No such link"}, {"read": True}],
            }
        )
        message, _ = self.run_and_wait(broken)
        text = message.call_args.args[0]
        self.assertIn("stopped at step 2, “Click “No such link””", text)
        self.assertIn("could not find it on the page", text)

    def test_tell_me_of_changes_needs_no_assistant(self):
        self.window._watch_alerts()
        news = self.url.replace("/inbox", "/news")
        routine = self.service.add({"name": "News", "pages": [news], "tell": "changes"})
        message, notify = self.run_and_wait(routine)
        message.assert_not_called()  # First run: only remembered.
        notify.assert_not_called()
        message, notify = self.run_and_wait(routine)
        message.assert_not_called()
        titles = [c.args[0] for c in notify.call_args_list]
        self.assertIn("Routine “News”: something changed", titles)
        with self.assertRaises(ValueError):
            self.service.add({"name": "Nothing", "tell": "changes"})

    def test_a_morning_brief_adds_what_steed_noticed(self):
        from ui.routines_dialog import RoutinesDialog

        self.window._watch_alerts()
        dialog = RoutinesDialog(self.window)
        shown = []
        with patch.object(
            BrowserWindow, "_present_dialog", lambda s, d, **_: shown.append(d)
        ):
            editor = dialog.add_brief()
        values = editor.values()
        self.assertTrue(values["brief"])
        self.assertEqual(values["days"], [0, 1, 2, 3, 4])
        self.assertIn("what you read", values["ask"])
        values["pages"] = [self.url]
        routine = self.service.add(values)
        watches = self.window.profile_manager.registry.watches
        with patch.object(
            type(watches),
            "list",
            return_value=[
                {
                    "title": "Rent portal",
                    "url": "https://rent.example/",
                    "last_change": time.time(),
                    "last_summary": "Payment due moved to Friday",
                },
                {
                    "title": "Old",
                    "url": "https://old.example/",
                    "last_change": 1,
                    "last_summary": "ancient",
                },
            ],
        ):
            message, _ = self.run_and_wait(routine)
        text = message.call_args.args[0]
        self.assertIn("rent due Friday", text)
        self.assertIn("Rent portal (https://rent.example/): Payment due moved", text)
        self.assertNotIn("ancient", text)
        editor.deleteLater()
        dialog.deleteLater()

    def test_without_an_agent_you_are_told(self):
        self.window._watch_alerts()
        routine = self.service.add({"name": "Brief", "ask": "Hi"})
        with (
            patch.object(BrowserWindow, "agent_message", return_value=None),
            patch.object(DesktopNotifier, "notify") as notify,
        ):
            self.service.run(routine)
            self.wait(lambda: notify.called)
        self.assertIn("could not start", notify.call_args.args[0])

    def test_the_editor_saves_and_explains_mistakes(self):
        editor = RoutineEditor(self.service, "global", parent=self.window)
        try:
            editor.name.setText("Evening")
            editor.save()
            self.assertIn("say what to ask", editor.status.text())
            editor.ask.setPlainText("Summarize my day")
            editor.kind.setCurrentIndex(1)
            editor.save()
            (saved,) = self.service.list()
            self.assertEqual(
                (saved["name"], saved["kind"], saved["every"]), ("Evening", "hours", 4)
            )
        finally:
            editor.deleteLater()
        with self.assertRaises(ValueError):
            self.service.add({"name": "x", "ask": "y"}, profile="search")


if __name__ == "__main__":
    unittest.main()
