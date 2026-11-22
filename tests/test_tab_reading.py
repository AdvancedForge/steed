"""An agent reading the tab you have open, once, with your OK: no control of
your tabs, just the page's title, address and text."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import access_levels
from browser.browser_window import BrowserWindow
from services.task_policy import TaskPermissionError
from ui.tab_read_review import TabReadReviewDialog


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<meta charset=utf-8><title>Kettles</title><p>Steel kettles last longest.</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class TabReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/kettles"

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
        self.service = self.window.profile_manager.registry.tasks

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

    def command(self, command, /, *, client=None, **arguments):
        future = (client or self.service.broker).submit(command, **arguments)
        self.wait(future.done)
        return future.result()

    def open_page(self):
        tab = self.window._new_tab(url=self.url, return_tab=True)
        self.wait(lambda: tab.title() == "Kettles" and not tab.page().isLoading())
        task = self.command("task.create", objective="What is this page about?")
        return task.task_id, self.service.client(task.task_id)

    def test_allowed_once_the_agent_gets_the_page_text(self):
        task, client = self.open_page()
        receipt = self.command("browser.read_tab", client=client, request_id="r1")
        self.assertEqual(receipt["state"], "awaiting_approval")
        self.assertEqual((receipt["title"], receipt["url"]), ("Kettles", self.url))
        self.assertNotIn("text", receipt)
        (request,) = self.service.tab_reading.pending(task)
        dialog = TabReadReviewDialog(self.service, request)
        dialog.apply()
        self.wait(
            lambda: (
                self.command("browser.read_tab_status", client=client, request_id="r1")[
                    "state"
                ]
                == "answered"
            )
        )
        answer = self.command("browser.read_tab_status", client=client, request_id="r1")
        self.assertIn("Steel kettles last longest.", answer["text"])
        self.assertIn("not instructions", answer["note"])
        # Reading is not control: no grant, the tab stays the person's.
        self.assertEqual(self.service.store.grants(task), [])
        self.assertEqual(self.service.tab_reading.pending(task), [])

    def test_deny_never_and_pages_that_are_not_websites(self):
        task, client = self.open_page()
        self.command("browser.read_tab", client=client, request_id="r1")
        (request,) = self.service.tab_reading.pending(task)
        dialog = TabReadReviewDialog(self.service, request)
        dialog.decline()
        denied = self.command("browser.read_tab_status", client=client, request_id="r1")
        self.assertEqual(denied["state"], "denied")
        self.assertNotIn("text", denied)
        # Always answers at once only for public websites; this one is local.
        access_levels.set_policy(self.window.config, "agents", "browse", "always")
        again = self.command("browser.read_tab", client=client, request_id="r2")
        self.assertEqual(again["state"], "awaiting_approval")
        access_levels.set_policy(self.window.config, "agents", "browse", "never")
        with self.assertRaises(TaskPermissionError):
            self.command("browser.read_tab", client=client, request_id="r3")
        access_levels.set_policy(self.window.config, "agents", "browse", "ask")
        self.window._new_tab(url="steed:newtab")
        with self.assertRaises(ValueError):
            self.command("browser.read_tab", client=client, request_id="r4")

    def test_the_tool_is_offered_to_agents(self):
        from services.agent_tools import TOOLS

        names = {tool.name for tool in TOOLS}
        self.assertIn("steed_browser_read_tab", names)
        self.assertIn("steed_browser_read_tab_status", names)


if __name__ == "__main__":
    unittest.main()
