"""Passkeys: WebAuthn create/get answered by Steed with keys in the vault."""

import base64
import hashlib
import json
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

import passkeys
import webengine
from browser_window import BrowserWindow


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<title>Site</title><p>sign in"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FakeKeys:
    key_value = nacl.utils.random(32)

    def key(self, create=True):
        return self.key_value


def unpadded(data: bytes) -> bytes:
    return base64.b64decode(data)


class RulesTests(unittest.TestCase):
    def test_cbor_origins_and_relying_parties(self):
        self.assertEqual(passkeys.cbor({1: 2, 3: -7}), bytes.fromhex("a2010203 26"))
        self.assertEqual(passkeys.cbor(b"ab"), b"\x42ab")
        self.assertEqual(passkeys.cbor("fmt"), b"\x63fmt")
        self.assertEqual(passkeys.cbor(-1), b"\x20")
        self.assertEqual(passkeys.cbor(500), b"\x19\x01\xf4")
        self.assertEqual(
            passkeys.page_origin("https://www.example.com/a"),
            ("https://www.example.com", "www.example.com"),
        )
        self.assertEqual(
            passkeys.page_origin("http://localhost:8080/"),
            ("http://localhost:8080", "localhost"),
        )
        self.assertIsNone(passkeys.page_origin("http://example.com/"))
        self.assertEqual(
            passkeys.relying_party(None, "www.example.com"), "www.example.com"
        )
        self.assertEqual(
            passkeys.relying_party("example.com", "www.example.com"), "example.com"
        )
        for requested, host in (("other.com", "example.com"), ("com", "example.com")):
            with self.assertRaises(passkeys.PasskeyError):
                passkeys.relying_party(requested, host)

    def test_a_made_passkey_signs_what_the_site_verifies(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        options = {
            "challenge": "AQID",
            "rp": {"name": "Example"},
            "user": {"id": "CQkJ", "name": "ana", "displayName": "Ana"},
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
        }
        record, made = passkeys.register(options, "https://example.com", "example.com")
        self.assertEqual(record["rp_id"], "example.com")
        authenticator = passkeys.unb64url(made["authenticatorData"])
        self.assertEqual(authenticator[:32], hashlib.sha256(b"example.com").digest())
        self.assertEqual(authenticator[32], 0x45)
        signed = passkeys.assert_with(
            record, {"challenge": "BwcH"}, "https://example.com"
        )
        public = serialization.load_der_public_key(passkeys.unb64url(made["publicKey"]))
        data = passkeys.unb64url(signed["clientDataJSON"])
        public.verify(
            passkeys.unb64url(signed["signature"]),
            passkeys.unb64url(signed["authenticatorData"])
            + hashlib.sha256(data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        self.assertEqual(
            json.loads(data),
            {
                "type": "webauthn.get",
                "challenge": "BwcH",
                "origin": "https://example.com",
                "crossOrigin": False,
            },
        )
        with self.assertRaises(passkeys.PasskeyError):
            passkeys.register(
                {**options, "pubKeyCredParams": [{"alg": -257}]},
                "https://example.com",
                "example.com",
            )


class VaultTests(unittest.TestCase):
    def test_passkeys_are_sealed_kept_and_replaced_per_account(self):
        import password_vault as pv

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "passwords.vault"
            vault = pv.PasswordVault(path, FakeKeys())
            vault.unlock()
            options = {"challenge": "AQ", "user": {"id": "AQ", "name": "ana"}}
            first, _ = passkeys.register(options, "https://example.com", "example.com")
            vault.save_passkey(first)
            again, _ = passkeys.register(options, "https://example.com", "example.com")
            vault.save_passkey(again)
            other, _ = passkeys.register(
                {**options, "user": {"id": "Ag", "name": "bo"}},
                "https://example.com",
                "example.com",
            )
            vault.save_passkey(other)
            vault.lock()
            vault.unlock()
            self.assertEqual(
                {p["id"] for p in vault.passkeys("example.com")},
                {again["id"], other["id"]},
            )
            self.assertNotIn(again["key"], path.read_text())
            vault.delete_passkey(other["id"])
            self.assertEqual([p["id"] for p in vault.passkeys()], [again["id"]])
            with self.assertRaises(pv.VaultError):
                vault.save_passkey({"id": "x"})


CEREMONY = """
(async () => {
  const b = (buffer) => btoa(String.fromCharCode(...new Uint8Array(buffer)));
  const user = { id: new Uint8Array([9, 9, 9]), name: "ana@example.com", displayName: "Ana" };
  const params = [{ type: "public-key", alg: -7 }];
  const created = await navigator.credentials.create({
    publicKey: { challenge: new Uint8Array([1, 2, 3, 4]), rp: { name: "Test" }, user, pubKeyCredParams: params },
  });
  const out = {
    id: created.id,
    type: created.type,
    credential: created instanceof PublicKeyCredential,
    json: created.toJSON().response.publicKey,
    created: new TextDecoder().decode(created.response.clientDataJSON),
    publicKey: b(created.response.getPublicKey()),
  };
  const got = await navigator.credentials.get({
    publicKey: { challenge: new Uint8Array([7, 7, 7]), rpId: "localhost",
      allowCredentials: [{ type: "public-key", id: created.rawId }] },
  });
  out.gotId = got.id;
  out.data = b(got.response.clientDataJSON);
  out.authenticator = b(got.response.authenticatorData);
  out.signature = b(got.response.signature);
  out.userHandle = b(got.response.userHandle);
  try {
    await navigator.credentials.create({
      publicKey: { challenge: new Uint8Array([1]), rp: { name: "Test" }, user, pubKeyCredParams: params,
        excludeCredentials: [{ type: "public-key", id: created.rawId }] },
    });
    out.excluded = "created";
  } catch (error) {
    out.excluded = error.name;
  }
  window.__result = JSON.stringify(out);
})().catch((error) => { window.__result = JSON.stringify({ error: error.name + ": " + error.message }); });
"""


class CeremonyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://localhost:{cls.server.server_port}/"

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
            patch("ui.passkey_prompt.confirm_new_passkey", return_value="steed"),
            patch(
                "ui.passkey_prompt.choose_passkey",
                side_effect=lambda window, rp_id, saved: saved[0],
            ),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()
        self.name = self.window.profile_manager.current_profile_name
        self.vault = self.window.profile_manager.registry.passwords.vault(self.name)

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

    def evaluate(self, tab, script):
        found = []
        webengine.run_script(
            tab.page(), script, found.append, world=webengine.PAGE_WORLD
        )
        self.wait(lambda: found)
        return found[0]

    def open(self):
        tab = self.window._new_tab(url=self.url, return_tab=True)
        self.wait(lambda: tab.title() == "Site" and not tab.page().isLoading())
        return tab

    def result(self, tab):
        found = []

        def done():
            found[:] = [self.evaluate(tab, "window.__result || ''")]
            return found[0]

        self.wait(done)
        return json.loads(found[0])

    def test_create_sign_in_cancel_and_a_page_posing_as_steed(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        tab = self.open()
        webengine.run_script(tab.page(), CEREMONY, world=webengine.PAGE_WORLD)
        out = self.result(tab)
        self.assertNotIn("error", out)
        self.assertEqual(out["type"], "public-key")
        self.assertTrue(out["credential"])
        self.assertEqual(out["gotId"], out["id"])
        self.assertEqual(out["excluded"], "InvalidStateError")
        origin = self.url.rstrip("/")
        self.assertEqual(json.loads(out["created"])["origin"], origin)
        data = unpadded(out["data"])
        self.assertEqual(json.loads(data)["challenge"], "BwcH")
        authenticator = unpadded(out["authenticator"])
        self.assertEqual(authenticator[:32], hashlib.sha256(b"localhost").digest())
        public = serialization.load_der_public_key(unpadded(out["publicKey"]))
        public.verify(
            unpadded(out["signature"]),
            authenticator + hashlib.sha256(data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        self.assertEqual(unpadded(out["userHandle"]), bytes([9, 9, 9]))
        (saved,) = self.vault.passkeys("localhost")
        self.assertEqual(saved["user_name"], "ana@example.com")
        self.assertGreater(saved["last_used"], 0)
        # The vault file holds no readable key.
        vault_text = Path(self.vault.path).read_text()
        self.assertNotIn(saved["key"], vault_text)
        self.assertNotIn("ana@example.com", vault_text)

        # One window for both parts: two windows in one test process can crash
        # PyQt's cleanup at exit (it happens on main too; see ROADMAP.md).
        tab = self.open()
        self.mocks[2].return_value = ""
        webengine.run_script(
            tab.page(),
            "navigator.credentials.create({publicKey: {challenge: new Uint8Array([1]),"
            " rp: {name: 'T'}, user: {id: new Uint8Array([1]), name: 'a'},"
            " pubKeyCredParams: [{type: 'public-key', alg: -7}]}})"
            ".then(() => window.__result = '\"made\"',"
            " (e) => window.__result = JSON.stringify(e.name))",
            world=webengine.PAGE_WORLD,
        )
        self.assertEqual(self.result(tab), "NotAllowedError")
        self.mocks[2].reset_mock()
        saved_before = self.vault.passkeys()
        webengine.run_script(
            tab.page(),
            "console.debug('\\u0001steedpasskey ' + JSON.stringify({token: 'guess',"
            " ask: 1, kind: 'create', options: {}}))",
            world=webengine.PAGE_WORLD,
        )
        time.sleep(0.3)
        self.app.processEvents()
        self.mocks[2].assert_not_called()
        self.assertEqual(self.vault.passkeys(), saved_before)


if __name__ == "__main__":
    unittest.main()
