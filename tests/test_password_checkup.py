"""Password Checkup (reused, weak, breached) and passkeys on steed:passwords."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import nacl.utils
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import passkeys
from browser import password_checkup as checkup
from browser.browser_window import BrowserWindow


class FakeKeys:
    key_value = nacl.utils.random(32)

    def key(self, create=True):
        return self.key_value


BREACHED = "hunter22"


class Ranges(BaseHTTPRequestHandler):
    """Have I Been Pwned's range search, with one known password."""

    asked = []

    def do_GET(self):
        prefix = self.path.rsplit("/", 1)[-1]
        Ranges.asked.append((prefix, self.headers.get("Add-Padding")))
        digest = checkup.sha1(BREACHED)
        lines = ["0000000000000000000000000000000000A:0"]  # Padding.
        if prefix == digest[:5]:
            lines.append(f"{digest[5:]}:1234")
        body = "\r\n".join(lines).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Login:
    def __init__(self, login_id, password):
        self.id, self.password = login_id, password


class RulesTests(unittest.TestCase):
    def test_weak_reused_and_range_answers(self):
        for password in ("short", "password", "aaaaaaaaaa", "12345678901", "abcdefgh"):
            self.assertTrue(checkup.weak(password), password)
        for password in ("correct horse battery", "Tr0ub4dor&3x", "1234567890123"):
            self.assertFalse(checkup.weak(password), password)
        logins = [Login("a", "same one!!"), Login("b", "same one!!"), Login("c", "x")]
        self.assertEqual(checkup.reused(logins), {"a": 2, "b": 2})
        self.assertEqual(
            checkup.counts_in("ABC:3\r\nDEF:0\nbad line\nfed:2"), {"ABC": 3, "FED": 2}
        )
        self.assertEqual(
            checkup.sha1("password"), "5BAA61E4C9B93F3F0682250B6CF8331B7EE68FD8"
        )


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Ranges)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.patches = [
            patch("pathlib.Path.home", return_value=Path(self.directory.name)),
            patch("browser.password_service.KeyStore", FakeKeys),
            patch.object(
                checkup,
                "RANGE_URL",
                f"http://127.0.0.1:{self.server.server_port}/range/{{prefix}}",
            ),
        ]
        for item in self.patches:
            item.start()
        Ranges.asked = []
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()
        name = self.window.profile_manager.current_profile_name
        self.vault = self.window.profile_manager.registry.passwords.vault(name)

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def test_checkup_flags_logins_and_passkeys_can_be_deleted(self):
        from ui.prompts import MessageBox

        breached = self.vault.save("https://a.example", "ana", BREACHED)
        first = self.vault.save("https://b.example", "bo", "the same long one")
        second = self.vault.save("https://c.example", "cy", "the same long one")
        fine = self.vault.save("https://d.example", "di", "Unique & long enough 7")
        record, _ = passkeys.register(
            {
                "challenge": "AQ",
                "rp": {"name": "Example"},
                "user": {"id": "AQID", "name": "ana", "displayName": "Ana"},
            },
            "https://example.com",
            "example.com",
        )
        self.vault.save_passkey(record)
        tab = self.window._new_tab(url="steed:passwords", return_tab=True)
        page = tab.workspace_controller
        self.wait(lambda: page.surface.ready)

        def send(**command):
            page.command(
                {"context": page.context, "sequence": page.sequence + 1, **command}
            )

        def logins():
            return {row["id"]: row for row in page.surface._state["logins"]}

        send(action="checkup", online=False)
        self.assertEqual(Ranges.asked, [])
        self.assertTrue(logins()[first.id]["reused"])
        self.assertTrue(logins()[breached.id]["weak"] is False)
        send(action="checkup", online=True)
        self.wait(
            lambda: "Have I Been Pwned" in page.surface._state["checkup"]["status"]
        )
        rows = logins()
        self.assertEqual(rows[breached.id]["breached"], 1234)
        self.assertEqual(rows[first.id]["reused"], 2)
        self.assertEqual(rows[second.id]["reused"], 2)
        self.assertEqual(rows[fine.id]["breached"], 0)
        self.assertEqual(page.surface._state["checkup"]["breached"], 1)
        # Only 5 characters of each hash were sent, always padded.
        self.assertTrue(Ranges.asked)
        for prefix, padding in Ranges.asked:
            self.assertRegex(prefix, "^[0-9A-F]{5}$")
            self.assertEqual(padding, "true")
        self.assertNotIn(BREACHED, repr(page.surface._state))

        (shown,) = page.surface._state["passkeys"]
        self.assertEqual((shown["site"], shown["account"]), ("example.com", "Ana"))
        self.assertNotIn(record["key"], repr(page.surface._state))
        with patch.object(
            MessageBox, "question", return_value=MessageBox.StandardButton.Yes
        ):
            send(action="delete_passkey", id=shown["id"])
        self.assertEqual(self.vault.passkeys(), [])
        self.assertEqual(page.surface._state["passkeys"], [])


if __name__ == "__main__":
    unittest.main()
