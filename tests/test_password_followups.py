"""Security-key prompts, saved Keep This Tab Loaded, and KeePassXC cleanup."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineWebAuthUxRequest as Request
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from ui.prompts import InputDialog, MessageBox
from ui.web_auth import WebAuthPrompts

State = Request.WebAuthUxState


class FakeRequest(QObject):
    stateChanged = pyqtSignal(object)

    def __init__(self, state, *, names=(), pin=None, failure=None):
        super().__init__()
        self._state = state
        self.names = list(names)
        self.pin = pin
        self.failure = failure
        self.calls = []

    def state(self):
        return self._state

    def move(self, state):
        self._state = state
        self.stateChanged.emit(state)

    def relyingPartyId(self):
        return "example.org"

    def userNames(self):
        return self.names

    def pinRequest(self):
        return self.pin

    def requestFailureReason(self):
        return self.failure

    def setSelectedAccount(self, name):
        self.calls.append(("account", name))

    def setPin(self, pin):
        self.calls.append(("pin", pin))

    def cancel(self):
        self.calls.append(("cancel",))

    def retry(self):
        self.calls.append(("retry",))


def pin_request(reason, error="NoError", attempts=8):
    return SimpleNamespace(
        reason=getattr(Request.PinEntryReason, reason),
        error=getattr(Request.PinEntryError, error),
        minPinLength=4,
        remainingAttempts=attempts,
    )


class FollowupTests(unittest.TestCase):
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
        self.prompts = WebAuthPrompts(self.window)

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

    def test_security_key_account_choice(self):
        request = FakeRequest(State.SelectAccount, names=["ada", "grace"])
        with patch.object(InputDialog, "getItem", return_value=("grace", True)):
            self.prompts.handle(request)
        self.assertEqual(request.calls, [("account", "grace")])
        request = FakeRequest(State.SelectAccount, names=["ada"])
        with patch.object(InputDialog, "getItem", return_value=("", False)):
            self.prompts.handle(request)
        self.assertEqual(request.calls, [("cancel",)])

    def test_security_key_pins(self):
        request = FakeRequest(
            State.CollectPin, pin=pin_request("Challenge", "WrongPin", attempts=3)
        )
        asked = []

        def text(parent, title, label, *args, **kwargs):
            asked.append(label)
            return "1234", True

        with patch.object(InputDialog, "getText", side_effect=text):
            self.prompts.handle(request)
        self.assertEqual(request.calls, [("pin", "1234")])
        self.assertIn("That PIN was wrong.", asked[0])
        self.assertIn("3 attempts left", asked[0])
        # Setting a PIN asks twice; a mismatch sets nothing.
        request = FakeRequest(State.CollectPin, pin=pin_request("Set"))
        with (
            patch.object(
                InputDialog, "getText", side_effect=[("1234", True), ("9999", True)]
            ),
            patch.object(MessageBox, "warning") as warned,
        ):
            self.prompts.handle(request)
        self.assertEqual(request.calls, [("cancel",)])
        warned.assert_called_once()

    def test_touch_prompt_and_failures(self):
        request = FakeRequest(State.FinishTokenCollection)
        self.prompts.handle(request)
        self.assertTrue(self.prompts.touch.isVisible())
        self.assertIn("example.org", self.prompts.touch.text())
        request.move(State.Completed)
        self.assertIsNone(self.prompts.touch)
        failed = FakeRequest(
            State.RequestFailed, failure=Request.RequestFailureReason.KeyNotRegistered
        )

        def choose_retry(box):
            self.assertIn("not registered", box.text())
            box.clickedButton = lambda: next(
                b for b in box.buttons() if b.text() == "Try again"
            )
            return 0

        with patch.object(MessageBox, "exec", choose_retry):
            self.prompts.handle(failed)
        self.assertEqual(failed.calls, [("retry",)])

    def test_keep_this_tab_loaded_survives_restart(self):
        tab = self.window._new_tab(url="about:blank#kept", return_tab=True)
        self.window._set_keep_loaded(tab, True)
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.window = BrowserWindow()
        self.window.show()
        self.wait(
            lambda: any(getattr(t, "keep_loaded", False) for t in self.window.tabs)
        )
        kept = next(t for t in self.window.tabs if getattr(t, "keep_loaded", False))
        index = self.window.tabs.index(kept)
        self.assertTrue(self.window.tab_bar.records[index]["keep_loaded"])

    def test_old_keepassxc_files_are_removed_once_when_allowed(self):
        config = self.window.config
        files = [
            config.config_dir / "global_profile" / "password_manager.json",
            config.profiles_dir / "work" / "password_manager.json",
        ]
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
        with patch.object(
            MessageBox, "question", return_value=MessageBox.StandardButton.Yes
        ) as asked:
            self.window._offer_old_pairing_cleanup()
            self.window._offer_old_pairing_cleanup()
        asked.assert_called_once()
        self.assertFalse(any(path.exists() for path in files))
        self.assertTrue(config.get("keepassxc_cleanup_asked"))


if __name__ == "__main__":
    unittest.main()
