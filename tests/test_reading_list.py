"""Reading list: save with the article text, read offline, mark, remove."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from browser.reading_list import ReadingList

TEXT = "A long and calm paragraph of article text that keeps going for a while. " * 3
ARTICLE = (
    "<title>Story</title><article><h1>Story</h1>" + f"<p>{TEXT}</p>" * 8 + "</article>"
).encode()


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ARTICLE if self.path == "/story" else b"<title>Short</title><p>hi"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class StoreTests(unittest.TestCase):
    def test_add_again_mark_remove_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReadingList(Path(directory))
            first = store.add("https://a.example/", "A")
            store.add(
                "https://b.example/",
                "B",
                {
                    "title": "B",
                    "blocks": [
                        {"type": "p", "text": "x"},
                        {"type": "img", "text": "pic"},
                    ],
                },
            )
            store.set_read(first["id"], True)
            entries = store.entries()
            self.assertEqual([e["title"] for e in entries], ["B", "A"])
            self.assertEqual(
                entries[0]["article"]["blocks"], [{"type": "p", "text": "x"}]
            )
            store.add("https://a.example/", "A again")
            self.assertFalse(
                next(e for e in store.entries() if e["title"] == "A again")["read"]
            )
            store.remove(first["id"])
            self.assertEqual([e["title"] for e in store.entries()], ["B"])
            for bad in ("file:///x", "javascript:alert(1)"):
                with self.assertRaises(ValueError):
                    store.add(bad, "x")
            private = ReadingList(Path())
            with self.assertRaises(ValueError):
                private.add("https://a.example/", "A")
            self.assertEqual(private.entries(), [])


class WindowTests(unittest.TestCase):
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

    def test_save_then_read_offline_from_the_list(self):
        from ui.reading_list_dialog import ReadingListDialog

        tab = self.window._new_tab(url=self.origin + "/story", return_tab=True)
        self.wait(lambda: tab.title() == "Story" and not tab.page().isLoading())
        self.window.save_to_reading_list(tab)
        store = self.window._reading_list()
        self.wait(lambda: store.entries())
        (entry,) = store.entries()
        self.assertIsNotNone(entry["article"])
        self.assertIn("offline", self.window.status_bar.currentMessage())
        short = self.window._new_tab(url=self.origin + "/short", return_tab=True)
        self.wait(lambda: short.title() == "Short" and not short.page().isLoading())
        self.window.save_to_reading_list(short)
        self.wait(lambda: len(store.entries()) == 2)
        dialog = ReadingListDialog(self.window)
        try:
            row = next(i for i, e in enumerate(dialog.entries) if e["title"] == "Story")
            dialog.list.setCurrentRow(row)
            self.assertTrue(dialog.buttons["Read Offline"].isEnabled())
            before = len(self.window.tabs)
            dialog.read_offline()
            self.assertEqual(len(self.window.tabs), before + 1)
            offline = self.window.tabs[self.window.current_tab_index]
            panel = offline.reader_panel
            self.wait(lambda: panel.surface.ready)
            self.wait(lambda: offline.url().toString() == "about:blank")
            self.assertIsNotNone(offline.reader_panel)
            self.assertTrue(
                next(e for e in store.entries() if e["title"] == "Story")["read"]
            )
            row = next(i for i, e in enumerate(dialog.entries) if e["title"] == "Short")
            dialog.list.setCurrentRow(row)
            self.assertFalse(dialog.buttons["Read Offline"].isEnabled())
            dialog.remove()
            self.assertEqual([e["title"] for e in store.entries()], ["Story"])
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
