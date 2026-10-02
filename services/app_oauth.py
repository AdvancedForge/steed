"""Bounded HTTPS and desktop OAuth with PKCE, state and a loopback callback."""

import base64
import hashlib
import hmac
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from services.app_accounts import AccountError


class ApiError(RuntimeError):
    def __init__(self, status=0):
        super().__init__("App request failed")
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AppHttp:
    """Trusted providers fix destinations; never follow authenticated redirects."""

    def __init__(self, origins):
        self.origins = frozenset(origins)

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or f"https://{parsed.netloc}" not in self.origins
            or parsed.username
            or parsed.password
            or parsed.fragment
            or method not in {"GET", "POST"}
        ):
            raise ValueError("App endpoint is not allowed")
        if cancelled is not None and cancelled.is_set():
            raise AccountError("Operation cancelled before its next request.")
        try:
            request = Request(url, data=body, headers=headers or {}, method=method)
            with build_opener(NoRedirect()).open(request, timeout=10) as response:
                raw = response.read(128 * 1024 + 1)
                if len(raw) > 128 * 1024:
                    raise ApiError()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ApiError()
                return result
        except HTTPError as error:
            status = error.code
            error.close()
            raise ApiError(status) from None
        except (URLError, OSError, ValueError):
            raise ApiError() from None


def desktop_authorization(
    authorization_endpoint,
    client_id,
    scopes,
    cancelled,
    *,
    open_browser=webbrowser.open,
    timeout=180,
):
    """Return code/verifier/redirect only; never log callback URLs or responses."""
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                query = parse_qs(
                    parsed.query, keep_blank_values=True, max_num_fields=20
                )
            except ValueError:
                query = {}
            valid_callback = (
                self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                and parsed.path == "/callback"
                and len(self.path) < 8192
                and len(query.get("state", [])) == 1
                and hmac.compare_digest(
                    query["state"][0].encode("utf-8"), state.encode("ascii")
                )
            )
            good = (
                valid_callback
                and len(query.get("code", [])) == 1
                and bool(query["code"][0])
                and not query.get("error")
            )
            if good:
                result["code"] = query["code"][0]
            elif valid_callback and query.get("error"):
                result["denied"] = True
            self.send_response(200 if good else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(
                b"Authorization received. Return to Steed."
                if good
                else b"Authorization was not accepted. Return to Steed."
            )

        def log_message(self, format, *args):
            pass

    class LoopbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(1)
            return connection, address

    with LoopbackServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 0.2
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = (
            authorization_endpoint
            + "?"
            + urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect,
                    "response_type": "code",
                    "scope": " ".join(scopes),
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "access_type": "offline",
                    "prompt": "consent select_account",
                }
            )
        )
        if cancelled.is_set():
            raise AccountError("Connection cancelled.")
        if not open_browser(url):
            raise AccountError("Could not open your system browser for authorization.")
        deadline = time.monotonic() + timeout
        while not result and not cancelled.is_set() and time.monotonic() < deadline:
            server.handle_request()
        if cancelled.is_set():
            raise AccountError("Connection cancelled.")
        if not result.get("code"):
            raise AccountError(
                "Authorization was denied or timed out. Try connecting again."
            )
        return result["code"], verifier, redirect
