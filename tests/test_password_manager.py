"""Encrypted protocol interoperability, replay rejection, and pairing storage."""

import base64
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from nacl.public import Box, PrivateKey, PublicKey
from PyQt6.QtCore import QUrl

from password_forms import login_url
from password_manager import KeePassXCClient, PasswordManagerError


class KeePassXCProtocolFixture:
    """A local peer speaking the published KeePassXC protocol, with real NaCl.

    A synthetic vault models approval, saved associations, and matching entries.
    No external application, real account, or real vault is used.
    """

    def __init__(self, directory):
        self.path = Path(directory) / "keepass.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.server.listen()
        self.server.settimeout(0.1)
        self.stop = threading.Event()
        self.connections = []
        self.keyring = {}
        self.entries = []
        self.requests = []
        self.wire = []
        self.errors = []
        self.deny = False
        self.locked = False
        self.tamper = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.delay_logins = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.release.set()
        self.server.close()
        for connection in self.connections:
            connection.close()
        self.thread.join(timeout=2)

    def _serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self.connections.append(connection)
            try:
                self._session(connection)
            except (OSError, ValueError) as error:
                if not self.stop.is_set():
                    self.errors.append(error)
            finally:
                connection.close()

    @staticmethod
    def _next_nonce(nonce):
        # Independent sodium_increment-compatible implementation.
        values = bytearray(nonce)
        for index in range(len(values)):
            values[index] = (values[index] + 1) & 255
            if values[index]:
                break
        return bytes(values)

    def _session(self, connection):
        connection.settimeout(0.1)
        buffer = b""
        box = None
        associated = False
        while not self.stop.is_set():
            try:
                chunk = connection.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                return
            buffer += chunk
            try:
                request = json.loads(buffer)
            except json.JSONDecodeError:
                continue
            self.wire.append(buffer)
            buffer = b""
            action = request["action"]
            nonce = base64.b64decode(request["nonce"])
            reply_nonce = self._next_nonce(nonce)
            nonce_text = base64.b64encode(reply_nonce).decode()
            if action == "change-public-keys":
                host_key = PrivateKey.generate()
                box = Box(host_key, PublicKey(base64.b64decode(request["publicKey"])))
                response = {
                    "action": action,
                    "publicKey": base64.b64encode(bytes(host_key.public_key)).decode(),
                    "nonce": nonce_text,
                    "success": "true",
                }
            else:
                data = json.loads(
                    box.decrypt(base64.b64decode(request["message"]), nonce)
                )
                if data["action"] != action:
                    raise ValueError("Incorrect request action")
                self.requests.append(data)
                body = {"nonce": nonce_text, "success": "true", "hash": "fixture-db"}
                error = None
                if self.locked:
                    error = "1"
                elif action == "associate":
                    if self.deny:
                        error = "6"
                    else:
                        self.keyring["test-browser"] = data["idKey"]
                        associated = True
                        body["id"] = "test-browser"
                elif action == "test-associate":
                    associated = self.keyring.get(data["id"]) == data["key"]
                    if not associated:
                        error = "8"
                elif action == "get-logins":
                    if not associated or data["keys"] != [
                        {"id": "test-browser", "key": self.keyring["test-browser"]}
                    ]:
                        error = "8"
                    else:
                        self.started.set()
                        if self.delay_logins:
                            self.release.wait(timeout=3)
                        body["entries"] = self.entries
                elif action == "set-login":
                    if not associated:
                        error = "8"
                    else:
                        self.entries.append(
                            {
                                "name": "Saved",
                                "login": data["login"],
                                "password": data["password"],
                            }
                        )
                        body["error"] = "success"
                elif action != "get-databasehash":
                    raise ValueError("Unknown request")
                if error:
                    response = {
                        "action": action,
                        "errorCode": error,
                        "error": "SECRET MUST NOT BE DISPLAYED",
                    }
                else:
                    cipher = box.encrypt(
                        json.dumps(body).encode(), reply_nonce
                    ).ciphertext
                    if self.tamper == "cipher" and action == "get-logins":
                        cipher = bytes([cipher[0] ^ 1]) + cipher[1:]
                    if self.tamper == "nonce" and action == "get-logins":
                        nonce_text = request["nonce"]
                    response = {
                        "action": action,
                        "nonce": nonce_text,
                        "message": base64.b64encode(cipher).decode(),
                    }
            # Exercise notifications, concatenated objects, and fragmented reads.
            outgoing = (
                json.dumps({"action": "database-unlocked"}).encode()
                + json.dumps(response).encode()
            )
            connection.sendall(outgoing[:11])
            connection.sendall(outgoing[11:])


class PasswordProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = KeePassXCProtocolFixture(self.directory.name)
        self.pairing_path = (
            Path(self.directory.name) / "profile" / "password_manager.json"
        )

    def tearDown(self):
        self.fixture.close()
        self.directory.cleanup()

    def client(self, path=True, memory=None, cancel=None):
        return KeePassXCClient(
            self.pairing_path if path else None,
            memory if memory is not None else {},
            [self.fixture.path],
            cancel=cancel,
            timeout=3,
        )

    def pair(self):
        with self.client() as client:
            client.execute("connect", {})

    def test_pair_restart_fill_and_save_only_encrypt_credentials_on_wire(self):
        self.pair()
        self.fixture.entries = [
            {"login": "fixture-user", "password": "fixture-secret", "name": "Fixture"}
        ]
        with self.client() as client:
            entries = client.execute(
                "get-logins",
                {
                    "url": "https://example.org/",
                    "submitUrl": "https://example.org/login",
                },
            )
            self.assertEqual(entries[0]["password"], "fixture-secret")
            self.assertTrue(
                client.execute(
                    "set-login",
                    {
                        "url": "https://example.org/",
                        "login": "new-user",
                        "password": "new-secret",
                    },
                )
            )
        self.assertEqual(self.pairing_path.stat().st_mode & 0o777, 0o600)
        stored = self.pairing_path.read_text()
        self.assertNotIn("fixture-secret", stored)
        self.assertNotIn("new-secret", stored)
        self.assertNotIn("fixture-user", stored)
        self.assertNotIn(b"fixture-secret", b"".join(self.fixture.wire))
        self.assertNotIn(b"new-secret", b"".join(self.fixture.wire))
        self.assertTrue(
            any(x["action"] == "test-associate" for x in self.fixture.requests)
        )
        self.assertEqual(self.fixture.errors, [])

    def test_modified_ciphertext_and_replayed_nonce_are_rejected(self):
        self.pair()
        for tamper in ("cipher", "nonce"):
            with self.subTest(tamper=tamper):
                self.fixture.tamper = tamper
                with self.client() as client:
                    with self.assertRaisesRegex(
                        PasswordManagerError, "authentication failed"
                    ):
                        client.execute("get-logins", {"url": "https://example.org/"})

    def test_first_fill_does_not_pair_without_explicit_connect(self):
        with self.client() as client:
            with self.assertRaisesRegex(PasswordManagerError, "Connect KeePassXC"):
                client.execute("get-logins", {"url": "https://example.org/"})
        self.assertFalse(self.pairing_path.exists())
        self.assertEqual(self.fixture.keyring, {})

    def test_pairing_denial_does_not_store_or_display_peer_payload(self):
        self.fixture.deny = True
        with self.client() as client:
            with self.assertRaisesRegex(PasswordManagerError, "denied") as failure:
                client.execute("connect", {})
        self.assertNotIn("SECRET", str(failure.exception))
        self.assertFalse(self.pairing_path.exists())

    def test_private_pairing_stays_in_memory_and_missing_crypto_is_explained(self):
        memory = {}
        with self.client(path=False, memory=memory) as client:
            client.execute("connect", {})
        with self.client(path=False, memory=memory) as client:
            self.assertEqual(
                client.execute("get-logins", {"url": "https://example.org/"}), []
            )
        self.assertTrue(memory)
        self.assertFalse(self.pairing_path.exists())
        with patch("password_manager.Box", None):
            with self.assertRaisesRegex(PasswordManagerError, "requirements"):
                with self.client():
                    pass

    def test_cancellation_stops_waiting_for_vault_prompt(self):
        self.pair()
        self.fixture.delay_logins = True
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        try:
            with self.client(cancel=cancel) as client:
                with self.assertRaisesRegex(PasswordManagerError, "cancelled"):
                    client.execute("get-logins", {"url": "https://example.org/"})
        finally:
            timer.cancel()

    def test_only_secure_web_and_local_test_urls_are_eligible(self):
        self.assertEqual(
            login_url(QUrl("https://example.org/login?token=secret#fragment")),
            "https://example.org/login",
        )
        for url in (
            "http://example.org/",
            "file:///tmp/form.html",
            "https://user:secret@example.org/",
            "about:blank",
        ):
            self.assertIsNone(login_url(QUrl(url)))
        self.assertEqual(
            login_url(QUrl("http://127.0.0.1/login")), "http://127.0.0.1/login"
        )


if __name__ == "__main__":
    unittest.main()
