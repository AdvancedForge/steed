"""Local HTTP acceptance: folders, exact-tab edits, restart and archived checks."""

import json
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from PyQt6 import sip

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_connector_support import (  # noqa: E402
    MemorySecrets,
    SyntheticGoogle,
    install_account,
    synthetic_document_tab,
)
from qt_test_support import application  # noqa: E402

from services.app_accounts import AccountStore  # noqa: E402
from services.app_oauth import ApiError, NoRedirect  # noqa: E402
from services.connectors import ConnectorRegistry  # noqa: E402
from services.google_connector import GoogleConnector  # noqa: E402
from services.task_service import TaskService  # noqa: E402
from ui.app_review import AppReviewDialog  # noqa: E402
from ui.task_dialog import TaskDialog  # noqa: E402


def check():
    app, fixture = application(), SyntheticGoogle()

    class Handler(BaseHTTPRequestHandler):
        def api(self):
            origin = (
                "https://oauth2.googleapis.com"
                if self.path == "/token"
                else "https://openidconnect.googleapis.com"
                if self.path == "/v1/userinfo"
                else "https://docs.googleapis.com"
                if self.path.startswith("/v1/documents/")
                else "https://www.googleapis.com"
            )
            body = (
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.command == "POST"
                else None
            )
            try:
                result = fixture.request(
                    self.command,
                    origin + self.path,
                    headers=dict(self.headers),
                    body=body,
                )
            except ApiError as error:
                if error.status:
                    self.send_error(error.status)
                else:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                return
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = api
        do_POST = api

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    class FixtureOpener:
        def open(self, request, timeout):
            # Production AppHttp still validates Google's fixed HTTPS origins.
            parsed = urlsplit(request.full_url)
            assert parsed.scheme == "https"
            routed = Request(
                f"http://127.0.0.1:{server.server_port}"
                + parsed.path
                + ("?" + parsed.query if parsed.query else ""),
                data=request.data,
                headers=dict(request.header_items()),
                method=request.get_method(),
            )
            return build_opener(NoRedirect()).open(routed, timeout=timeout)

    service = None
    directory = tempfile.TemporaryDirectory()
    try:
        with patch("services.app_oauth.build_opener", return_value=FixtureOpener()):
            root = Path(directory.name)
            store = AccountStore(root / "accounts.json", MemorySecrets())
            account = install_account(store)

            def new_service():
                registry = ConnectorRegistry()
                for definition in GoogleConnector(account, store).tools():
                    registry.register(definition)
                return TaskService(root / "tasks.sqlite", connectors=registry)

            service = new_service()

            def wait_for(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    app.processEvents()
                    if predicate():
                        return
                    time.sleep(0.002)
                raise AssertionError("Google document fixture timed out")

            def resolve(future):
                wait_for(future.done)
                return future.result()

            def authorize(task_id, tool, destination, effect="write"):
                grant = service._request_grant(
                    task_id,
                    {
                        "actions": ["app", "artifact"]
                        if effect == "write"
                        else ["app"],
                        "connector_permissions": [
                            {
                                "connector": account.connector,
                                "tool": tool,
                                "effect": effect,
                                "destination": destination,
                            }
                        ],
                    },
                )
                service.approve_grant(task_id, grant.grant_id)
                return service.client(task_id)

            def task(tool, destination, effect="write"):
                record = service._create("Synthetic " + tool)
                return authorize(record.task_id, tool, destination, effect)

            def draft(client, content):
                return resolve(
                    client.submit(
                        "artifact.store", title="Synthetic output", content=content
                    )
                )

            def call(client, tool, destination, request_id, artifact=None):
                params = {
                    "connector": account.connector,
                    "tool": tool,
                    "destination": destination,
                    "request_id": request_id,
                }
                if artifact:
                    params["artifact_id"] = artifact.artifact_id
                return resolve(client.submit("app.call_tool", **params))

            def settled(client, request_id):
                wait_for(
                    lambda: (client.task_id, request_id) not in service._connector_jobs
                )
                return resolve(client.submit("app.status", request_id=request_id))

            folder_client = task("create_folder", "drive:root")
            call(
                folder_client,
                "create_folder",
                "drive:root",
                "folder",
                draft(folder_client, "Unused folder body"),
            )
            folder = settled(folder_client, "folder")
            assert folder.state == "succeeded"
            parent = json.loads(folder.text)["destination"]
            create_client = task("create_document", parent)
            call(
                create_client,
                "create_document",
                parent,
                "document",
                draft(create_client, "EXISTING_TEXT"),
            )
            created = settled(create_client, "document")
            assert created.state == "succeeded"
            identity = json.loads(created.text)["destination"]
            fixture.document_data["synthetic-doc"]["tabs"].append(
                synthetic_document_tab("t.other", "OTHER_TAB_PRIVATE\n")
            )
            inspect_client = task("inspect_document", identity, "read")
            call(inspect_client, "inspect_document", identity, "inspection")
            inspection = settled(inspect_client, "inspection")
            tab = json.loads(inspection.text)["tabs"][0]["destination"]
            assert "EXISTING_TEXT" not in inspection.text
            assert "OTHER_TAB_PRIVATE" not in inspection.text

            append_client = task("append_document", tab)
            artifact = draft(append_client, "COLLABORATIVE_APPEND 😀")
            fixture.lose_append = True
            call(append_client, "append_document", tab, "append", artifact)
            assert settled(append_client, "append").state == "uncertain"
            assert (
                call(append_client, "append_document", tab, "append", artifact).state
                == "uncertain"
            )
            service._finish_task(append_client.task_id)
            source_id = append_client.task_id
            service.close()
            sip.delete(service)
            service = None
            service = new_service()

            followup = service._create("Check earlier append after restart")
            service._request_recovery(followup.task_id)
            service.approve_recovery(followup.task_id, source_id, [], evidence=True)
            reader = authorize(followup.task_id, "append_document", tab, "read")
            resolve(
                reader.submit(
                    "app.reconcile_history",
                    source_request_id="append",
                    request_id="checked-append",
                )
            )
            checked = settled(reader, "checked-append")
            assert checked.state == "succeeded" and checked.effect == "read"
            assert not checked.artifact_id
            assert not service.store.artifacts(followup.task_id)
            assert service.connector_store.get(source_id, "append").state == "succeeded"

            read_client = task("read_document", tab, "read")
            call(read_client, "read_document", tab, "read")
            read = settled(read_client, "read")
            payload = json.loads(read.text)
            assert payload["text"] == "EXISTING_TEXT\nCOLLABORATIVE_APPEND 😀\n"
            assert "OTHER_TAB_PRIVATE" not in read.text

            fixture.append_status = 400
            rejected = task("append_document", tab)
            call(
                rejected,
                "append_document",
                tab,
                "conflict",
                draft(rejected, "REJECTED_APPEND"),
            )
            assert settled(rejected, "conflict").state == "not_applied"
            assert len(fixture.append_writes) == 1
            assert len(fixture.folder_writes) == 1
            assert len(fixture.documents) == 1
            assert not fixture.messages

            fixture.append_status = 0
            replacement_client = task("replace_document", tab)
            call(
                replacement_client,
                "replace_document",
                tab,
                "replacement",
                draft(replacement_client, "REVIEWED_REPLACEMENT"),
            )
            proposed = settled(replacement_client, "replacement")
            assert (
                proposed.state == "requested" and proposed.reason == "awaiting_review"
            )
            assert not fixture.replacement_writes
            review = service.app_reviews.get(replacement_client.task_id, "replacement")
            review_dialog = AppReviewDialog(
                service, service.store.get(replacement_client.task_id), review
            )
            review_dialog.show()
            app.processEvents()
            assert review_dialog.grab().save("/tmp/steed-google-document-review.png")
            fixture.lose_replacement = True
            review_dialog.decide(True)
            sip.delete(review_dialog)
            assert settled(replacement_client, "replacement").state == "uncertain"
            resolve(
                replacement_client.submit("app.reconcile", request_id="replacement")
            )
            assert settled(replacement_client, "replacement").state == "succeeded"
            assert len(fixture.replacement_writes) == 1
            assert not service.app_reviews.get(
                replacement_client.task_id, "replacement"
            ).before_text

            panel = TaskDialog(service)
            row = next(
                i
                for i, (record, _) in enumerate(panel._rows)
                if record.task_id == followup.task_id
            )
            panel.table.selectRow(row)
            panel.panels.setCurrentIndex(
                next(
                    i
                    for i in range(panel.panels.count())
                    if panel.panels.tabText(i) == "Apps"
                )
            )
            panel.show()
            app.processEvents()
            assert panel.grab().save("/tmp/steed-google-document-receipts.png")
            panel.close()
            sip.delete(panel)
            print(
                "Local Google HTTP: exactly one folder, document, append and user-reviewed replacement; exact-tab reads, lost acknowledgements, read-only recovery after restart, atomic revision rejection, content preview and no repeated mutations passed. No real accounts used."
            )
    finally:
        if service is not None:
            service.close()
            sip.delete(service)
        server.shutdown()
        server.server_close()
        worker.join(2)
        directory.cleanup()


if __name__ == "__main__":
    check()
