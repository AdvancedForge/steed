"""Search what you've read: page text kept on this computer (when on), found
by any words, never for sign-in pages or excluded sites, cleared with
history, and handed to your agent on request."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from services import page_memory
from services.page_memory import PageMemory
from ui.read_search_dialog import ReadSearchDialog, agent_message


class StoreTests(unittest.TestCase):
    def test_remember_search_forget(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = PageMemory(Path(directory) / "read.sqlite")
            memory.remember(
                "https://a.test/", "Tides", "The moon pulls the sea twice a day.", 100.0
            )
            memory.remember(
                "https://b.test/",
                "Bread",
                "Sourdough needs a starter and patience.",
                200.0,
            )
            found = memory.search("moon sea")
            self.assertEqual([r["url"] for r in found], ["https://a.test/"])
            self.assertIn("«moon»", found[0]["snippet"])
            self.assertEqual(memory.search("sour")[0]["title"], "Bread")  # Prefix.
            self.assertEqual(memory.search('"; DROP TABLE pages; --'), [])
            # Reading a page again keeps one copy, with the new text.
            memory.remember(
                "https://a.test/", "Tides", "Spring tides are strongest.", 300.0
            )
            self.assertEqual(memory.count(), 2)
            self.assertEqual(memory.search("moon"), [])
            memory.forget(250.0)
            self.assertEqual(
                [r["url"] for r in memory.search("sourdough")], ["https://b.test/"]
            )
            self.assertEqual(memory.search("spring"), [])
            with patch.object(page_memory, "MAX_PAGES", 2):
                for n in range(4):
                    memory.remember(f"https://{n}.test/", "x", "word", 400.0 + n)
            self.assertEqual(memory.count(), 2)
            memory.forget(0)
            self.assertEqual(memory.count(), 0)
            memory.close()

    def test_the_agent_gets_the_pages_text(self):
        text = agent_message(
            "Which recipe?",
            [{"title": "Bread", "url": "https://b.test/", "text": "flour " * 2000}],
        )
        self.assertTrue(
            text.startswith("Which recipe?\n\n### 1. Bread\nhttps://b.test/")
        )
        self.assertLess(len(text), 3300)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.window.config.set("remember_pages", True)
        self.memory = self.window.history.memory

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

    def read(self, url, html):
        self.tab.setHtml(html, QUrl(url))
        self.wait(lambda: self.tab.url().toString() == url and not self.tab.is_loading)
        self.window._remember_page(self.tab)
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            self.app.processEvents()

    def test_pages_are_kept_except_sign_ins_and_excluded_sites(self):
        self.read(
            "https://tides.test/", "<title>Tides</title><p>The moon pulls the sea."
        )
        self.wait(lambda: self.memory.search("moon"))
        self.read(
            "https://login.test/",
            "<title>Sign in</title><p>secret words<input type=password>",
        )
        self.window.config.set("remember_pages_except", "bank.test, other.test")
        self.read("https://www.bank.test/", "<title>Bank</title><p>balance words")
        self.assertEqual(self.memory.search("secret"), [])
        self.assertEqual(self.memory.search("balance"), [])
        # The dialog finds it and hands it to the agent.
        dialog = ReadSearchDialog(self.window)
        try:
            dialog.query.setText("moon")
            self.assertEqual(dialog.table.rowCount(), 1)
            with patch.object(self.window, "agent_message") as message:
                dialog.ask_agent()
            self.assertIn("The moon pulls the sea.", message.call_args.args[0])
        finally:
            dialog.deleteLater()
        # Clearing history clears it.
        self.window.history.clear()
        self.assertEqual(self.memory.count(), 0)

    def test_nothing_is_kept_when_off(self):
        self.window.config.set("remember_pages", False)
        self.tab.setHtml("<title>T</title><p>quiet words", QUrl("https://q.test/"))
        self.wait(lambda: self.tab.title() == "T")
        end = time.monotonic() + 3
        while time.monotonic() < end:
            self.app.processEvents()
        self.assertEqual(self.memory.count(), 0)


if __name__ == "__main__":
    unittest.main()
