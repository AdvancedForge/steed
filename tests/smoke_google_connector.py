"""Synthetic OAuth → draft → Google Docs/Gmail HTTP → uncertainty/restart checks."""

import base64
import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, urlopen

from PyQt6 import sip

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_connector_support import MemorySecrets  # noqa: E402
from qt_test_support import application  # noqa: E402

from services.app_accounts import AccountStore  # noqa: E402
from services.app_connections import AppConnections, AppProvider  # noqa: E402
from services.app_oauth import NoRedirect, desktop_authorization  # noqa: E402
from services.google_connector import GoogleConnector, connect_google  # noqa: E402
from services.task_service import TaskService  # noqa: E402
from ui.app_connections import AppConnectionsDialog  # noqa: E402
from ui.task_approval import TaskApprovalDialog  # noqa: E402


def check():
    app = application()
    wire, documents, messages, authorization = [], [], [], {}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, data):
            encoded = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            wire.append(("POST", self.path))
            if self.path == "/token":
                fields = parse_qs(body.decode())
                if fields["grant_type"] == ["authorization_code"]:
                    verifier = fields["code_verifier"][0]
                    challenge = (
                        base64.urlsafe_b64encode(
                            hashlib.sha256(verifier.encode()).digest()
                        )
                        .rstrip(b"=")
                        .decode()
                    )
                    assert authorization["code_challenge"] == [challenge]
                self.reply(
                    {
                        "access_token": "SYNTHETIC_ACCESS",
                        "refresh_token": "SYNTHETIC_REFRESH",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    }
                )
                return
            assert self.headers["Authorization"] == "Bearer SYNTHETIC_ACCESS"
            if self.path.startswith("/upload/drive/v3/files?"):
                parsed = BytesParser(policy=policy.default).parsebytes(
                    (
                        "Content-Type: "
                        + self.headers["Content-Type"]
                        + "\r\nMIME-Version: 1.0\r\n\r\n"
                    ).encode()
                    + body
                )
                parts = list(parsed.iter_parts())
                assert len(parts) == 2
                metadata = json.loads(parts[0].get_payload(decode=True))
                assert metadata["parents"] == ["root"]
                assert metadata["mimeType"] == "application/vnd.google-apps.document"
                assert parts[1].get_payload(decode=True) == b"SYNTHETIC_RESEARCH_DRAFT"
                documents.append(metadata)
            elif self.path == "/gmail/v1/users/me/messages/send":
                raw = base64.urlsafe_b64decode(json.loads(body)["raw"])
                email = BytesParser(policy=policy.default).parsebytes(raw)
                assert email["To"] == "recipient@example.test"
                assert email["From"] == "fixture@example.test"
                assert (
                    "https://docs.google.com/document/d/synthetic-doc/edit"
                    in email.get_content()
                )
                messages.append(email)
            else:
                raise AssertionError("Unexpected fixture API")
            # Effect applied, TCP reply lost. Neither write may be replayed.
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def do_GET(self):
            wire.append(("GET", self.path))
            assert self.headers["Authorization"] == "Bearer SYNTHETIC_ACCESS"
            if self.path == "/v1/userinfo":
                self.reply(
                    {
                        "sub": "synthetic-subject",
                        "email": "fixture@example.test",
                        "email_verified": True,
                    }
                )
            elif self.path.startswith("/drive/v3/files?"):
                query = parse_qs(urlsplit(self.path).query)
                assert documents[0]["appProperties"]["steed_operation"] in query["q"][0]
                assert "'root' in parents" in query["q"][0]
                self.reply({"files": [{"id": "synthetic-doc"}]})
            else:
                raise AssertionError("Unexpected fixture lookup")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    class FixtureOpener:
        def open(self, request, timeout):
            parsed = urlsplit(request.full_url)
            assert parsed.scheme == "https"
            # AppHttp checks the real provider allowlist first. Only this fixture
            # redirects the transport to synthetic HTTP; production never does.
            routed = Request(
                base + parsed.path + ("?" + parsed.query if parsed.query else ""),
                data=request.data,
                headers=dict(request.header_items()),
                method=request.get_method(),
            )
            return build_opener(NoRedirect()).open(routed, timeout=timeout)

    def open_browser(url):
        authorization.update(parse_qs(urlsplit(url).query))

        def respond():
            with urlopen(
                authorization["redirect_uri"][0]
                + "?"
                + urlencode(
                    {"state": authorization["state"][0], "code": "SYNTHETIC_CODE"}
                ),
                timeout=3,
            ):
                pass

        threading.Thread(target=respond, daemon=True).start()
        return True

    def authorize(endpoint, client_id, scopes, cancelled):
        return desktop_authorization(
            endpoint, client_id, scopes, cancelled, open_browser=open_browser, timeout=5
        )

    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        raise AssertionError("Google fixture timed out")

    def resolve(future):
        wait_for(future.done)
        return future.result()

    service = manager = None
    try:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("services.app_oauth.build_opener", return_value=FixtureOpener()),
        ):
            root = Path(directory)
            store = AccountStore(root / "accounts.json", MemorySecrets())
            client_file = root / "client.json"
            client_file.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "synthetic.apps.googleusercontent.com",
                            "client_secret": "SYNTHETIC_SECRET",
                        }
                    }
                )
            )
            provider = AppProvider(
                "google",
                "Google fixture",
                (("documents", "Docs"), ("email", "Gmail send")),
                lambda store, file, capabilities, cancel: connect_google(
                    store, file, capabilities, cancel, authorize=authorize
                ),
                lambda account, store: GoogleConnector(account, store).tools(),
            )
            manager = AppConnections(store.path, store=store, providers=(provider,))
            manager.connect_account("google", client_file, ("documents", "email"))
            wait_for(lambda: not manager.busy)
            assert not manager.failure and len(store.list()) == 1, manager.message
            account = store.list()[0]
            service = TaskService(root / "tasks.sqlite", connectors=manager.connectors)
            manager.task_service = service
            task = service._create("Create and email a synthetic research draft")
            permissions = [
                {
                    "connector": account.connector,
                    "tool": tool,
                    "effect": effect,
                    "destination": destination,
                }
                for tool, effect, destination in (
                    ("create_document", "write", "drive:root"),
                    ("send_email", "send", "email:recipient@example.test"),
                )
            ]
            grant = service._request_grant(
                task.task_id,
                {"actions": ["artifact", "app"], "connector_permissions": permissions},
            )
            approval = TaskApprovalDialog(service, task, grant)
            approval.show()
            app.processEvents()
            approval.grab().save("/tmp/steed-google-approval.png")
            approval.close()
            sip.delete(approval)
            service.approve_grant(task.task_id, grant.grant_id)
            client = service.client(task.task_id)
            artifact = resolve(
                client.submit(
                    "artifact.store",
                    title="Synthetic research",
                    content="SYNTHETIC_RESEARCH_DRAFT",
                )
            )
            args = {
                "connector": account.connector,
                "tool": "create_document",
                "request_id": "document",
                "destination": "drive:root",
                "artifact_id": artifact.artifact_id,
            }
            resolve(client.submit("app.call_tool", **args))
            wait_for(lambda: not service._connector_jobs)
            assert (
                resolve(client.submit("app.status", request_id="document")).state
                == "uncertain"
            )
            assert resolve(client.submit("app.call_tool", **args)).state == "uncertain"
            resolve(client.submit("app.reconcile", request_id="document"))
            wait_for(lambda: not service._connector_jobs)
            document = resolve(client.submit("app.status", request_id="document"))
            assert document.state == "succeeded"
            email = resolve(
                client.submit(
                    "artifact.store",
                    title="Synthetic document link",
                    content=document.reference,
                )
            )
            args = {
                "connector": account.connector,
                "tool": "send_email",
                "request_id": "email",
                "destination": "email:recipient@example.test",
                "artifact_id": email.artifact_id,
            }
            resolve(client.submit("app.call_tool", **args))
            wait_for(lambda: not service._connector_jobs)
            assert (
                resolve(client.submit("app.status", request_id="email")).state
                == "uncertain"
            )
            assert resolve(client.submit("app.call_tool", **args)).state == "uncertain"
            assert len(documents) == len(messages) == 1
            panel = AppConnectionsDialog(manager)
            panel.show()
            app.processEvents()
            panel.grab().save("/tmp/steed-google-connections.png")
            panel.close()
            sip.delete(panel)
            service.close()
            sip.delete(service)
            service = None
            before = list(wire)
            reopened = TaskService(root / "tasks.sqlite", connectors=manager.connectors)
            assert (
                reopened.connector_store.get(task.task_id, "email").state == "uncertain"
            )
            assert (
                reopened.store.artifact(task.task_id, artifact.artifact_id).content
                == "SYNTHETIC_RESEARCH_DRAFT"
            )
            assert not reopened._grants and not reopened._connector_jobs
            reopened.close()
            sip.delete(reopened)
            assert wire == before
            print(
                "Synthetic OAuth, real HTTP payloads, scoped Docs/Gmail, lost acknowledgements, read-only Doc recovery and restart passed; exactly one document/email, no replay."
            )
    finally:
        if service is not None:
            service.close()
            sip.delete(service)
        if manager is not None:
            manager.close()
            sip.delete(manager)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    check()
