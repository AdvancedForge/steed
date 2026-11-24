"""Addresses and cards: saved after a real submit, filled only on a click."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import nacl.utils
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PyQt6.QtTest import QTest
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow

# Synthetic values only: a Luhn-valid test number, made-up names and places.
CARD = "4111111111111111"
CHECKOUT = b"""<title>Checkout</title><form method=post action=/done>
<input id=fname autocomplete="shipping given-name">
<input id=lname autocomplete="shipping family-name">
<input id=line1 autocomplete="shipping address-line1">
<input id=line2 autocomplete="shipping address-line2">
<input id=city autocomplete="shipping address-level2">
<input id=zip autocomplete="shipping postal-code">
<select id=country autocomplete=country><option value="">Choose</option>
<option value=FR>France</option><option value=GB>United Kingdom</option></select>
<input id=email type=email autocomplete=email value="kept@example.test">
<input id=trap autocomplete=tel style="position:absolute;left:-5000px">
<input id=number autocomplete=cc-number>
<input id=exp placeholder="MM / YY" name=expiry>
<input id=cvc autocomplete=cc-csc>
<button id=pay>Pay</button></form>"""
# Labelled fields with no autocomplete hints, as many small shops have.
PLAIN = b"""<title>Shipping</title><form>
<label>Full name <input id=n></label><label>Street address <input id=s></label>
<label>Town <input id=t></label><label>Postcode <input id=p></label></form>"""
DONE = b"<title>Thanks</title>Order placed"


class Site(BaseHTTPRequestHandler):
    def respond(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond({"/checkout": CHECKOUT, "/plain": PLAIN}.get(self.path, DONE))

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.respond(DONE)

    def log_message(self, *args):
        pass


class FakeKeys:
    key_value = nacl.utils.random(32)

    def key(self, create=True):
        return self.key_value


class FormAutofillTests(unittest.TestCase):
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
            patch("browser.password_service.KeyStore", FakeKeys),
        ]
        for item in self.patches:
            item.start()
        self.window = BrowserWindow()
        self.window.resize(1000, 800)
        self.window.show()
        self.forms = self.window.password_assistant.forms
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

    def test_submitting_a_checkout_offers_the_card_then_the_address(self):
        tab = self.open("/checkout")
        self.js(
            tab,
            "fname.value='Ada'; lname.value='Example'; line1.value='1 Test Lane';"
            "city.value='Exampleton'; zip.value='EX1 2AB'; country.value='GB';"
            f"number.value='{CARD}'; exp.value='03/29'; cvc.value='123';"
            "cvc.focus(); true",
        )
        target = tab.focusProxy() or tab
        target.setFocus()
        self.app.processEvents()
        QTest.keyClick(target, Qt.Key.Key_Return)
        self.wait(lambda: self.forms.bar.isVisible())
        self.assertIn("ending 1111", self.forms.bar.label.text())
        self.assertNotIn(CARD, self.forms.bar.label.text())
        self.forms.bar.accept.click()
        card = self.vault.autofill("cards")[0]
        self.assertEqual(
            (card["number"], card["exp_month"], card["exp_year"]),
            (CARD, "03", "2029"),
        )
        self.assertNotIn("123", str(card))  # The security code is never kept.
        self.wait(lambda: self.forms.bar.isVisible())
        self.assertIn("address", self.forms.bar.label.text())
        self.forms.bar.accept.click()
        address = self.vault.autofill("addresses")[0]
        self.assertEqual(address["name"], "Ada Example")
        self.assertEqual(address["country"], "United Kingdom")
        self.assertEqual(address["email"], "kept@example.test")

    def test_fill_offer_fills_only_empty_visible_fields_on_click(self):
        self.vault.save_autofill(
            "addresses",
            {
                "name": "Bea Sample",
                "street": "2 Mock Road\nFlat 3",
                "city": "Testville",
                "postal": "TV1 1AA",
                "country": "France",
                "phone": "0123",
                "email": "bea@example.test",
            },
        )
        tab = self.open("/checkout")
        self.wait(lambda: self.forms.bar.isVisible())
        self.assertIn("Fill your address", self.forms.bar.label.text())
        # Nothing is filled before the click.
        self.assertEqual(self.js(tab, "fname.value"), "")
        self.forms.bar.accept.click()
        self.wait(lambda: self.js(tab, "city.value") == "Testville")
        values = self.js(
            tab,
            "[fname.value, lname.value, line1.value, line2.value, zip.value,"
            " country.value, email.value, trap.value]",
        )
        self.assertEqual(
            values,
            [
                "Bea",
                "Sample",
                "2 Mock Road",
                "Flat 3",
                "TV1 1AA",
                "FR",
                "kept@example.test",
                "",
            ],
        )

    def test_card_fills_number_and_expiry_but_never_the_security_code(self):
        self.vault.save_autofill(
            "cards", {"number": CARD, "exp_month": "7", "exp_year": "2031"}
        )
        tab = self.open("/checkout")
        self.wait(lambda: self.forms.bar.isVisible())
        self.assertIn("card", self.forms.bar.label.text())
        self.forms.bar.accept.click()
        self.wait(lambda: self.js(tab, "number.value") == CARD)
        self.assertEqual(self.js(tab, "[exp.value, cvc.value]"), ["07/31", ""])

    def test_labelled_fields_are_recognised_and_menu_offers_entries(self):
        address = self.vault.save_autofill(
            "addresses",
            {
                "name": "Cy Person",
                "street": "3 Fake St",
                "city": "Nowhere",
                "postal": "N0 0NE",
            },
        )
        tab = self.open("/plain")
        self.wait(lambda: self.forms.bar.isVisible())
        self.forms.bar.dismiss.click()
        menu = self.window.password_assistant.menu()
        labels = [action.text() for action in menu.actions()]
        self.assertIn("Fill Cy Person, 3 Fake St", labels)
        self.forms.fill(tab, "addresses", address)
        self.wait(lambda: self.js(tab, "t.value") == "Nowhere")
        self.assertEqual(
            self.js(tab, "[n.value, s.value, p.value]"),
            ["Cy Person", "3 Fake St", "N0 0NE"],
        )
        # Turned down once: the site is not offered again this session.
        self.open("/done")
        self.open("/plain")
        for _ in range(250):
            self.app.processEvents()
            time.sleep(0.02)
        self.assertFalse(self.forms.bar.isVisible())

    def test_passwords_page_keeps_card_numbers_out_of_the_page(self):
        from ui.passwords_page import PasswordsPage

        self.vault.save_autofill("cards", {"number": CARD, "nickname": "Travel"})
        page = PasswordsPage(self.window)
        published = []
        page.surface.publish = lambda data, force=False: published.append(data)
        page.publish()
        cards = published[-1]["autofill"]["cards"]
        self.assertEqual(cards[0]["label"], "Travel (ending 1111)")
        self.assertNotIn(CARD, str(published[-1]))
        # Editing without retyping the number keeps it.
        page.command(
            {
                "context": page.context,
                "sequence": 1,
                "action": "autofill_save",
                "kind": "cards",
                "id": cards[0]["id"],
                "number": "",
                "nickname": "Holidays",
            }
        )
        saved = self.vault.autofill("cards")[0]
        self.assertEqual((saved["number"], saved["nickname"]), (CARD, "Holidays"))
        page.disposed = True
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
