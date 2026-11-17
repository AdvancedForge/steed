"""Verify Google compatibility through local HTTPS redirects and POSTs.

No Google server or account is used. Chromium maps the two test hosts to a
temporary loopback server, and trusts only that server's generated certificate.
"""

import base64
import hashlib
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, Qt, QUrl, qVersion
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser.browser_window import BrowserWindow  # noqa: E402
from browser.config import Config  # noqa: E402
from browser.webengine import GOOGLE_LOGIN_USER_AGENT  # noqa: E402


def smoke_test():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append(
                (self.headers["Host"], self.path, self.headers["User-Agent"], body)
            )
            port = self.server.server_port
            redirects = {
                "/begin": f"https://accounts.google.com:{port}/start",
                "/start": "/identifier",
                "/submit": "/check",
                "/post-submit": "/post-check",
                "/outside": f"https://fixture-login.test:{port}/complete",
            }
            if self.path in redirects:
                self.send_response(307)
                self.send_header("Location", redirects[self.path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/identifier":
                payload = b'<html><script src="/script"></script></html>'
                content_type = "text/html"
            elif self.path == "/script":
                payload = (
                    b"fetch('/submit', {method:'POST', body:'fixture-post'})"
                    b".then(r => r.json()).then(data => {"
                    b"window.fixtureResult = data; document.title = 'done';})"
                    b".catch(error => { window.fixtureError = String(error); "
                    b"document.title = 'error'; });"
                )
                content_type = "application/javascript"
            elif self.path == "/postform":
                payload = (
                    b'<html><form method="POST" action="/post-submit">'
                    b'<input name="fixture" value="post"></form>'
                    b"<script>document.forms[0].submit();</script></html>"
                )
                content_type = "text/html"
            else:
                payload = json.dumps(
                    {"ua": self.headers["User-Agent"], "body": body.decode()}
                ).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, *args):
            pass

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        certificate, key = root / "cert.pem", root / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
                "-days",
                "1",
                "-subj",
                "/CN=accounts.google.com",
                "-addext",
                "subjectAltName=DNS:accounts.google.com,DNS:fixture-login.test",
            ],
            check=True,
            capture_output=True,
        )
        public_key = subprocess.run(
            ["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"],
            check=True,
            capture_output=True,
        ).stdout
        public_key_der = subprocess.run(
            ["openssl", "pkey", "-pubin", "-outform", "DER"],
            input=public_key,
            check=True,
            capture_output=True,
        ).stdout
        fingerprint = base64.b64encode(hashlib.sha256(public_key_der).digest()).decode()
        # Google hosts enforce HSTS, so Qt's certificate-error handler cannot
        # accept a self-signed fixture. Trust only this temporary public key in
        # the test process; production certificate handling is untouched.
        # Qt gives the environment variable precedence over --webEngineArgs.
        os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app = QApplication(
            [
                "google-compatibility-tests",
                "--webEngineArgs",
                "--disable-gpu",
                "--no-proxy-server",
                f"--ignore-certificate-errors-spki-list={fingerprint}",
                "--host-resolver-rules=MAP accounts.google.com 127.0.0.1, "
                "MAP fixture-login.test 127.0.0.1",
            ]
        )
        app.setQuitOnLastWindowClosed(False)
        window = None

        def wait_for(predicate):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate():
                    return
                time.sleep(0.01)
            raise AssertionError(
                f"Timed out waiting for the local HTTPS fixture: {requests}"
            )

        try:
            with patch("pathlib.Path.home", return_value=root):
                config = Config()
                config.update(
                    {
                        "home_page": "about:blank",
                        "adblock_enabled": False,
                        "user_agent_override": "Fixture-UA",
                    }
                )
                window = BrowserWindow()
                window.show()
                page = window.tabs[0].page()
                port = server.server_port
                page.setUrl(QUrl(f"https://fixture-login.test:{port}/begin"))
                wait_for(lambda: page.title() in ("done", "error"))
                output = []
                page.runJavaScript(
                    "window.fixtureResult || window.fixtureError", output.append
                )
                wait_for(lambda: bool(output))
                expected = GOOGLE_LOGIN_USER_AGENT.decode()
                assert output[0] == {"ua": expected, "body": "fixture-post"}, output
                for path in ("/start", "/identifier", "/script", "/submit", "/check"):
                    entries = [entry for entry in requests if entry[1] == path]
                    assert entries and all(entry[2] == expected for entry in entries), (
                        entries
                    )
                assert (
                    next(entry for entry in requests if entry[1] == "/begin")[2]
                    == "Fixture-UA"
                )
                page.setUrl(QUrl(f"https://accounts.google.com:{port}/postform"))
                wait_for(lambda: page.url().path() == "/post-check")
                entries = [entry for entry in requests if entry[1] == "/post-check"]
                assert len(entries) == 1 and entries[0][2:] == (
                    expected,
                    b"fixture=post",
                ), entries
                page.setUrl(QUrl(f"https://accounts.google.com:{port}/outside"))
                wait_for(lambda: any(entry[1] == "/complete" for entry in requests))
                assert (
                    next(entry for entry in requests if entry[1] == "/complete")[2]
                    == "Fixture-UA"
                )
                window.config.set("google_login_compatibility", False)
                window.profile_manager.refresh_settings()
                page.setUrl(QUrl(f"https://accounts.google.com:{port}/disabled"))
                wait_for(lambda: any(entry[1] == "/disabled" for entry in requests))
                assert (
                    next(entry for entry in requests if entry[1] == "/disabled")[2]
                    == "Fixture-UA"
                )
        finally:
            if window is not None:
                window.close()
            sip.delete(app)
            server.shutdown()
            server.server_close()
            thread.join()
    print(
        f"Google compatibility HTTPS redirects, POSTs, and opt-out passed with Qt {qVersion()}"
    )


if __name__ == "__main__":
    smoke_test()
