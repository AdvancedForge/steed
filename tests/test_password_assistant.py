"""Saving and filling logins in real pages, frames and one-time-code fields."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import nacl.utils
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from password_vault import VaultError

LOGIN = b"""<title>Sign in</title><form method=post action=/home>
<input id=user name=username autocomplete=username>
<input id=pass name=password type=password autocomplete=current-password>
<button id=go>Sign in</button></form>"""
APP = b"""<title>App sign in</title><div id=box><input id=user name=email type=email>
<input id=pass type=password><button id=go type=button>Continue</button></div>
<script>go.onclick = () => setTimeout(() => box.remove(), 50);</script>"""
CODE = b"""<title>Code</title><input id=code autocomplete=one-time-code>"""
HOME = b"<title>Home</title>Signed in"


class Site(BaseHTTPRequestHandler):
    def respond(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/frame"):
            port = self.server.server_port
            # The embedded sign-in box comes from another origin (localhost).
            self.respond(
                f'<title>Shop</title><iframe src="http://localhost:{port}/login"'
                ' style="width:400px;height:200px"></iframe>'.encode()
            )
            return
        self.respond({"/login": LOGIN, "/app": APP, "/code": CODE}.get(self.path, HOME))

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.respond(HOME)

    def log_message(self, *args):
        pass


class FakeKeys:
    key_value = nacl.utils.random(32)

    def key(self, create=True):
        return self.key_value


class PasswordAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.patches = [
            patch("pathlib.Path.home", return_value=Path(self.directory.name)),
            patch("password_service.KeyStore", FakeKeys),
        ]
        for item in self.patches:
            item.start()
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()
        self.assistant = self.window.password_assistant
        self.vault = self.window.profile_manager.registry.passwords.vault(
            self.window.profile_manager.current_profile_name
        )

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

    def js(self, tab, code):
        out = []
        tab.page().runJavaScript(code, out.append)
        self.wait(lambda: bool(out))
        return out[0]

    def open(self, path):
        tab = self.window.tabs[self.window.current_tab_index]
        loaded = []
        tab.loadFinished.connect(loaded.append)
        tab.setUrl(QUrl(self.origin + path))
        self.wait(lambda: loaded and tab.url().path() == path)
        tab.loadFinished.disconnect(loaded.append)
        return tab

    def sign_in(self, tab, user, password):
        self.js(
            tab,
            f"user.value = {user!r}; pass.value = {password!r}; go.click(); true",
        )

    def test_form_login_offers_to_save_then_fills_next_time(self):
        self.assertTrue(self.vault.unlocked)
        tab = self.open("/login")
        self.sign_in(tab, "ada", "hunter2-strong")
        self.wait(lambda: self.assistant.bar.isVisible())
        self.assertIn("127.0.0.1", self.assistant.bar.label.text())
        self.assertIn("ada", self.assistant.bar.label.text())
        self.assistant.bar.save.click()
        (login,) = self.vault.logins(self.origin)
        self.assertEqual((login.username, login.password), ("ada", "hunter2-strong"))
        tab = self.open("/login")
        self.wait(lambda: self.js(tab, "pass.value") == "hunter2-strong")
        self.assertEqual(self.js(tab, "user.value"), "ada")
        # Logging in again with the same password asks nothing.
        self.js(tab, "go.click(); true")
        self.spin(2)
        self.assertFalse(self.assistant.bar.isVisible())

    def spin(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def test_changed_password_offers_an_update_and_never_stops_asking(self):
        self.vault.save(self.origin, "ada", "old-password")
        tab = self.open("/login")
        self.js(tab, "pass.value = ''; true")
        self.sign_in(tab, "ada", "new-password-1")
        self.wait(lambda: self.assistant.bar.isVisible())
        self.assertEqual(self.assistant.bar.save.text(), "Update password")
        self.assistant.bar.save.click()
        self.assertEqual(self.vault.logins(self.origin)[0].password, "new-password-1")
        tab = self.open("/login")
        self.js(tab, "user.value = ''; pass.value = ''; true")
        self.sign_in(tab, "grace", "other-password")
        self.wait(lambda: self.assistant.bar.isVisible())
        self.assistant.bar.never.click()
        tab = self.open("/login")
        self.js(tab, "user.value = ''; pass.value = ''; true")
        self.sign_in(tab, "linus", "third-password")
        self.spin(3)
        self.assertFalse(self.assistant.bar.isVisible())

    def test_app_login_without_navigation_is_offered_once_the_form_goes(self):
        tab = self.open("/app")
        self.sign_in(tab, "ada@example.org", "app-password-9")
        self.wait(lambda: self.assistant.bar.isVisible())
        self.assistant.bar.save.click()
        self.assertEqual(self.vault.logins(self.origin)[0].username, "ada@example.org")

    def test_typing_without_submitting_offers_nothing(self):
        tab = self.open("/login")
        self.js(tab, "user.value = 'ada'; pass.value = 'typed-only'; true")
        self.spin(3)
        self.assertFalse(self.assistant.bar.isVisible())

    def test_embedded_sign_in_frames_are_filled_with_their_own_site(self):
        frame_origin = self.origin.replace("127.0.0.1", "localhost")
        self.vault.save(frame_origin, "ada", "frame-password")
        self.vault.save(self.origin, "main", "main-password")
        tab = self.open("/frame")
        frame = []

        def filled():
            children = tab.page().mainFrame().children()
            if not children:
                return False
            children[0].runJavaScript(
                "document.getElementById('pass') && pass.value", 0, frame.append
            )
            return bool(frame and frame[-1])

        self.wait(filled)
        self.assertEqual(frame[-1], "frame-password")

    def test_one_time_codes_fill_and_key_menu_offers_actions(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        login = self.vault.save(self.origin, "ada", "pw-code", totp=secret)
        tab = self.open("/code")
        self.wait(lambda: len(self.js(tab, "code.value") or "") == 6)
        labels = [a.text() for a in self.assistant.menu().actions()]
        self.assertIn("Fill ada", labels)
        self.assertTrue(any(text.startswith("Copy code") for text in labels))
        self.assertIn("Generate strong password", labels)
        self.assertEqual(self.vault.get(login.id).username, "ada")

    def test_incognito_never_saves_or_fills(self):
        self.window._switch_to_profile("search")
        tab = self.open("/login")
        self.sign_in(tab, "ada", "private-password")
        self.spin(3)
        self.assertFalse(self.assistant.bar.isVisible())
        labels = [a.text() for a in self.assistant.menu().actions()]
        self.assertEqual(labels, ["Passwords are not saved in incognito"])

    def test_passwords_page_manages_logins(self):
        from ui.prompts import MessageBox

        login = self.vault.save(
            self.origin, "ada", "page-password", totp="GEZDGNBVGY3TQOJQ"
        )
        tab = self.window._new_tab(url="steed:passwords", return_tab=True)
        page = tab.workspace_controller
        self.wait(lambda: page.surface.ready)

        def send(**command):
            page.command(
                {"context": page.context, "sequence": page.sequence + 1, **command}
            )

        self.assertNotIn("page-password", repr(page.surface._state))
        send(action="reveal", id=login.id)
        self.assertEqual(page.revealed[login.id][0], "page-password")
        send(action="hide", id=login.id)
        self.assertNotIn(login.id, page.revealed)
        send(action="copy", id=login.id, field="password")
        from PyQt6.QtGui import QGuiApplication

        self.assertEqual(QGuiApplication.clipboard().text(), "page-password")
        send(action="copy", id=login.id, field="code")
        self.assertEqual(len(QGuiApplication.clipboard().text()), 6)
        send(
            action="save",
            id=login.id,
            origin=self.origin,
            username="ada2",
            password="",
            title="Fixture",
            totp="",
            notes="",
        )
        self.assertEqual(self.vault.get(login.id).username, "ada2")
        self.assertEqual(self.vault.get(login.id).password, "page-password")
        send(
            action="save",
            id="",
            origin="new.example",
            username="u",
            password="pw-new-site",
            title="",
            totp="",
            notes="",
        )
        self.assertEqual(
            self.vault.logins("https://new.example")[0].password, "pw-new-site"
        )
        csv_path = Path(self.directory.name) / "export.csv"
        csv_path.write_text(
            "name,url,username,password\nX,https://csv.example,c,csv-pw\n"
        )
        with patch(
            "ui.passwords_page.QFileDialog.getOpenFileName",
            return_value=(str(csv_path), ""),
        ):
            send(action="import")
        self.assertIn("Imported 1 new", page.notice)
        with patch.object(
            MessageBox, "question", return_value=MessageBox.StandardButton.Yes
        ):
            send(action="delete", id=login.id)
        with self.assertRaises(VaultError):
            self.vault.get(login.id)
        send(action="setting", key="password_autofill", value=False)
        self.assertFalse(self.window.config.get("password_autofill"))
        send(action="setting", key="anything", value=True)
        self.assertTrue(page.error)


if __name__ == "__main__":
    unittest.main()
