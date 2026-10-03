"""Real HTTPS framing/TLS plus DNS, redirect, size and credential containment."""

import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from services.app_accounts import AccountError
from services.app_oauth import ApiError
from services.recipe_http import RecipeHttp


class RecipeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.cert, cls.key = root / "cert.pem", root / "key.pem"
        subprocess.run(
            [
                "/usr/bin/openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(cls.key),
                "-out",
                str(cls.cert),
                "-days",
                "1",
                "-subj",
                "/CN=api.fixture.example",
                "-addext",
                "subjectAltName=DNS:api.fixture.example",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.calls = []
        self.status = 200
        self.body = b'{"text":"SYNTHETIC_DATA"}'
        self.encoding = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.calls.append((self.path, dict(self.headers)))
                self.send_response(owner.status)
                if owner.status == 302:
                    self.send_header("Location", "https://foreign.example/steal")
                if owner.encoding:
                    self.send_header("Content-Encoding", owner.encoding)
                self.send_header("Content-Length", str(len(owner.body)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(owner.body)
                except (OSError, ssl.SSLError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.http = RecipeHttp("https://api.fixture.example")
        self.resolved = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("9.9.9.9", 443),
            )
        ]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, *, trusted=True):
        original_socket = socket.socket
        owner = self

        class FixtureSocket(original_socket):
            def connect(self, address):
                owner.assertEqual(address, ("9.9.9.9", 443))
                return super().connect(("127.0.0.1", owner.server.server_port))

        context = (
            ssl.create_default_context(cafile=str(self.cert))
            if trusted
            else ssl.create_default_context()
        )
        with (
            patch(
                "services.recipe_http.socket.getaddrinfo", return_value=self.resolved
            ) as resolve,
            patch("services.recipe_http.socket.socket", FixtureSocket),
            patch(
                "services.recipe_http.ssl.create_default_context", return_value=context
            ),
        ):
            result = self.http.request(
                "GET",
                "https://api.fixture.example/note",
                headers={"Authorization": "Bearer SYNTHETIC_TOKEN"},
            )
            self.assertEqual(resolve.call_count, 1)
            return result

    def test_verified_tls_uses_pinned_numeric_address_and_reviewed_host(self):
        self.assertEqual(self.request(), {"text": "SYNTHETIC_DATA"})
        self.assertEqual(self.calls[0][0], "/note")
        self.assertEqual(self.calls[0][1]["Host"], "api.fixture.example")
        self.assertEqual(self.calls[0][1]["Authorization"], "Bearer SYNTHETIC_TOKEN")

    def test_untrusted_certificate_cannot_receive_authorization(self):
        with self.assertRaises(ApiError):
            self.request(trusted=False)
        self.assertEqual(self.calls, [])

    def test_authenticated_redirect_is_not_followed_or_retried(self):
        self.status = 302
        with self.assertRaises(ApiError):
            self.request()
        self.assertEqual(len(self.calls), 1)

    def test_private_mixed_mapped_multicast_and_empty_dns_answers_are_denied_before_connect(
        self,
    ):
        for addresses in (
            [],
            ["127.0.0.1"],
            ["10.0.0.1"],
            ["9.9.9.9", "192.168.1.1"],
            ["169.254.169.254"],
            ["224.0.0.1"],
            ["::ffff:9.9.9.9"],
            ["::1"],
            ["fec0::1"],
        ):
            self.resolved = [
                (
                    socket.AF_INET6 if ":" in address else socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    (address, 443),
                )
                for address in addresses
            ]
            with self.subTest(addresses=addresses), self.assertRaises(ApiError):
                self.request()
        self.assertEqual(self.calls, [])

    def test_wrong_origin_method_body_query_and_headers_are_rejected(self):
        for method, url, kwargs in (
            ("GET", "https://foreign.example/note", {}),
            ("POST", "https://api.fixture.example/note", {}),
            ("GET", "https://api.fixture.example/note", {"body": b"data"}),
            ("GET", "https://api.fixture.example/note?x=1", {}),
            (
                "GET",
                "https://api.fixture.example/note",
                {"headers": {"Host": "foreign.example"}},
            ),
        ):
            with self.subTest(url=url, method=method), self.assertRaises(ValueError):
                self.http.request(method, url, **kwargs)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(AccountError):
            self.http.request(
                "GET", "https://api.fixture.example/note", cancelled=cancelled
            )
        self.assertEqual(self.calls, [])

    def test_oversized_compressed_duplicate_nonfinite_and_non_json_responses_fail(self):
        for body, encoding in (
            (b"x" * (128 * 1024 + 1), None),
            (b"compressed", "gzip"),
            (b'{"x":1,"x":2}', None),
            (b'{"x":NaN}', None),
            (b"not JSON", None),
        ):
            self.body, self.encoding = body, encoding
            with (
                self.subTest(body=body[:24], encoding=encoding),
                self.assertRaises(ApiError),
            ):
                self.request()
