"""@-mentioned tabs go with a message: title, address and bounded text."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from ui.agent_sidebar import AgentSidebar


class TabMentionTests(unittest.TestCase):
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

    def test_a_mentioned_tab_travels_with_the_message(self):
        tab = self.window._new_tab(url="about:blank", return_tab=True)
        loaded = []
        tab.loadFinished.connect(loaded.append)
        tab.setHtml(
            "<title>Kettle review</title><p>The steel kettle boils in 3 minutes.</p>"
            + "<p>"
            + "filler " * 3000
            + "</p>",
            QUrl("https://reviews.example/kettle"),
        )
        self.wait(lambda: loaded)
        self.window._toggle_agent_sidebar(True)
        sidebar = self.window.agent_sidebar
        self.assertIsInstance(sidebar, AgentSidebar)
        sidebar._publish()
        mentions = sidebar._state["mention_tabs"]
        token = next(m["id"] for m in mentions if m["title"] == "Kettle review")
        sent = []
        sidebar._task_id = "task"
        sidebar._drafts["task"] = "Is this a good kettle?"
        with patch.object(
            AgentSidebar, "_send_now", lambda self: sent.append(self.draft)
        ):
            sidebar.send_with_tabs([token, "tab-unknown"])
            self.wait(lambda: sent)
        message = sent[0]
        self.assertTrue(message.startswith("Is this a good kettle?"))
        self.assertIn(
            "[Attached tab: Kettle review — https://reviews.example/kettle]", message
        )
        self.assertIn("boils in 3 minutes", message)
        self.assertIn("(more on the page)", message)
        self.assertLessEqual(len(message.encode("utf-8")), 8192)
        # A page you asked about goes with your next message, once.
        sidebar._drafts["task"] = "What do reviewers think?"
        sidebar.attach_tab("task", tab)
        with patch.object(
            AgentSidebar, "_send_now", lambda self: sent.append(self.draft)
        ):
            sidebar.send_message()
            self.wait(lambda: len(sent) == 2)
            self.assertIn("[Attached tab: Kettle review", sent[1])
            self.assertTrue(sent[1].startswith("What do reviewers think?"))
            sidebar._drafts["task"] = "Thanks"
            sidebar.send_message()
        self.assertEqual(sent[2], "Thanks")


if __name__ == "__main__":
    unittest.main()
