"""Link hints: letters on what you can click; typing one clicks it, or
opens a link in a new background tab (real key presses here)."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PyQt6.QtTest import QTest
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow

PAGE = (
    "<title>Hints</title><body style='margin:0'>"
    "<button onclick=\"document.title='pressed'\">Press</button>"
    "<a href='https://one.test/'>One</a> <a href='https://two.test/'>Two</a>"
    "<input id=f><div style='display:none'><a href='https://hidden.test/'>x</a></div>"
)


class LinkHintTests(unittest.TestCase):
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
        self.window.activateWindow()
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.tab.setHtml(PAGE, QUrl("https://hints.test/"))
        self.wait(lambda: self.tab.title() == "Hints")

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

    def js(self, script):
        found = []
        self.tab.page().runJavaScript(script, found.append)
        self.wait(lambda: found)
        return found[0]

    def labels(self):
        """The labels as the page shows them (the shadow root is closed, so
        ask the same script world for its count instead)."""
        return self.js("!!document.querySelector('steed-hints')")

    def type(self, text):
        target = self.tab.focusProxy() or self.tab
        for char in text:
            QTest.keyClick(target, char)
            self.app.processEvents()

    def test_typing_a_label_presses_it(self):
        self.window.link_hints()
        self.wait(self.labels)
        # Five things to click (the hidden link is left out): one letter each,
        # in order, so "a" is the button.
        self.type("a")
        self.wait(lambda: self.tab.title() == "pressed")
        self.assertFalse(self.labels())

    def test_escape_cancels_and_a_new_tab_opens_behind(self):
        self.window.link_hints()
        self.wait(self.labels)
        QTest.keyClick(self.tab.focusProxy() or self.tab, Qt.Key.Key_Escape)
        self.wait(lambda: not self.labels())
        with patch.object(self.window, "open_in_background") as opened:
            self.window.link_hints(new_tab=True)
            self.wait(self.labels)
            self.type("s")  # Only links: "a" is One, "s" is Two.
            self.wait(lambda: opened.called)
        opened.assert_called_once_with(self.tab, "https://two.test/")


if __name__ == "__main__":
    unittest.main()
