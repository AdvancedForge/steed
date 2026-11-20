"""Help me write: shown first, inserted only on Insert, never in secrets."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QTimer, pyqtSignal
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow

PAGE = (
    "<title>Mail</title><textarea id=t>hello wrold, see you tomorow</textarea>"
    "<input id=p type=password value=secret>"
)


class FakeOnce(QObject):
    answered = pyqtSignal(str)
    failed = pyqtSignal(str)
    asked = []

    def __init__(self, command, prompt, parent=None, **_):
        super().__init__(parent)
        FakeOnce.asked.append(prompt)
        self.running = True
        QTimer.singleShot(
            0, lambda: self.answered.emit("Hello world, see you tomorrow.")
        )

    def stop(self):
        self.running = False


class WritingHelpTests(unittest.TestCase):
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
        self.tab = self.window._new_tab(url="about:blank", return_tab=True)
        loaded = []
        self.tab.loadFinished.connect(loaded.append)
        self.tab.setHtml(PAGE)
        self.wait(lambda: loaded)

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

    def value(self, script):
        found = []
        self.tab.page().runJavaScript(script, found.append)
        self.wait(lambda: found)
        return found[0]

    def test_rewrite_is_shown_then_inserted_and_secrets_are_refused(self):
        shown = []
        with (
            patch("services.agent_once.agent_command", return_value=["fake"]),
            patch("services.agent_once.AgentOnce", FakeOnce),
            patch.object(
                BrowserWindow,
                "_present_dialog",
                lambda self, dialog, **_: shown.append(dialog),
            ),
        ):
            self.value("document.getElementById('p').focus(); true")
            self.window.help_me_write(self.tab)
            self.wait(lambda: "card fields" in self.window.status_bar.currentMessage())
            self.assertEqual(shown, [])
            self.value("document.getElementById('t').focus(); true")
            self.window.help_me_write(self.tab)
            self.wait(lambda: shown)
            dialog = shown[0]
            self.assertEqual(
                dialog.original.toPlainText(), "hello wrold, see you tomorow"
            )
            dialog.ask("Fix spelling, grammar and punctuation only.")
            self.wait(lambda: dialog.insert.isEnabled())
            self.assertIn("hello wrold", FakeOnce.asked[-1])
            self.assertNotIn("secret", FakeOnce.asked[-1])
            # Nothing changes before Insert.
            self.assertEqual(
                self.value("document.getElementById('t').value"),
                "hello wrold, see you tomorow",
            )
            dialog.put_in()
            self.wait(
                lambda: (
                    self.value("document.getElementById('t').value")
                    == "Hello world, see you tomorrow."
                )
            )


if __name__ == "__main__":
    unittest.main()
