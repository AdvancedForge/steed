"""Exact app-created folder parents, identity checks and uncertain creation."""

import copy
import json
import socket
import tempfile
import threading
import unittest
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener

from app_connector_support import MemorySecrets, SyntheticGoogle, install_account
from test_google_connector import draft

from services.app_accounts import AccountError, AccountStore
from services.app_oauth import ApiError, NoRedirect
from services.connectors import ConnectorProbe, ConnectorRequest
from services.google_connector import DRIVE, UPLOAD, GoogleConnector


class GoogleFolderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = AccountStore(
            Path(directory.name) / "accounts.json", MemorySecrets()
        )
        self.account = install_account(self.store)
        self.http = SyntheticGoogle()
        self.adapter = GoogleConnector(self.account, self.store, http=self.http)
        self.cancelled = threading.Event()

    def request(
        self,
        tool="create_folder",
        destination="drive:root",
        operation="a",
        artifact=True,
    ):
        return ConnectorRequest(
            "local-id",
            self.account.connector,
            tool,
            "write" if artifact else "read",
            destination,
            draft() if artifact else None,
            operation * 32,
        )

    def folder(self):
        result = self.adapter.create_folder(self.request(), self.cancelled)
        return json.loads(result.text)["destination"]

    def test_folder_title_only_and_one_metadata_write_returns_exact_destination(self):
        destination = self.folder()
        self.assertEqual(destination, "drive:folder:synthetic-folder-1")
        metadata = self.http.folder_writes[0]
        self.assertEqual(metadata["name"], "Synthetic draft")
        self.assertEqual(metadata["parents"], ["root"])
        self.assertEqual(metadata["appProperties"]["steed_operation"], "a" * 32)
        self.assertEqual(len(metadata["appProperties"]["steed_account"]), 64)
        self.assertNotIn("SYNTHETIC_DRAFT_BODY", json.dumps(metadata))
        self.assertFalse(self.http.documents)

    def test_nested_folder_and_document_revalidate_exact_parent_before_write(self):
        destination = self.folder()
        self.adapter.create_folder(
            self.request(destination=destination, operation="b"), self.cancelled
        )
        self.assertEqual(self.http.folder_writes[-1]["parents"], ["synthetic-folder-1"])
        self.adapter.create_document(
            self.request("create_document", destination, "c"), self.cancelled
        )
        method, url, headers, body = self.http.calls[-1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.startswith(UPLOAD))
        self.assertIn("/files/synthetic-folder-1?", self.http.calls[-2][1])
        parts = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + headers["Content-Type"] + "\r\n\r\n").encode() + body
        )
        metadata = json.loads(list(parts.iter_parts())[0].get_payload(decode=True))
        self.assertEqual(metadata["parents"], ["synthetic-folder-1"])

    def test_non_owned_non_writable_trashed_shortcut_or_other_account_never_writes(
        self,
    ):
        destination = self.folder()
        original = copy.deepcopy(self.http.folders["synthetic-folder-1"])
        changes = [
            {"ownedByMe": False},
            {"trashed": True},
            {"driveId": "shared-drive"},
            {"mimeType": "application/vnd.google-apps.shortcut"},
            {"capabilities": {"canAddChildren": False}},
            {"appProperties": {"steed_operation": "b" * 32, "steed_account": "other"}},
            {"appProperties": {}},
            {"id": "different"},
        ]
        for change in changes:
            self.http.folders["synthetic-folder-1"] = {**original, **change}
            for tool in ("create_folder", "create_document"):
                with self.subTest(change=change, tool=tool):
                    result = getattr(self.adapter, tool)(
                        self.request(tool, destination, "b"), self.cancelled
                    )
                    self.assertEqual(result.state, "not_applied")
        self.assertEqual(len(self.http.folder_writes), 1)
        self.assertFalse(self.http.documents)

    def test_missing_folder_is_not_applied_and_syntax_rejected_before_network(self):
        for destination in (
            "drive:folder:../root",
            "drive:folder:x?fields=id",
            "drive:folder:",
            "drive:other",
        ):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.adapter.create_folder(
                    self.request(destination=destination), self.cancelled
                )
        self.assertFalse(self.http.calls)
        outcome = self.adapter.create_folder(
            self.request(destination="drive:folder:missing"), self.cancelled
        )
        self.assertEqual(outcome.state, "not_applied")
        self.assertFalse(self.http.folder_writes)

    def test_lost_folder_ack_reconciles_exact_parent_without_second_write(self):
        self.http.lose_folder = True
        with self.assertRaises(ApiError):
            self.folder()
        result = self.adapter.find_folder(
            ConnectorProbe("local-id", "drive:root", "a" * 32), self.cancelled
        )
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(len(self.http.folder_writes), 1)
        self.assertEqual(
            json.loads(result.text)["destination"], "drive:folder:synthetic-folder-1"
        )
        wrong = self.adapter.find_folder(
            ConnectorProbe("local-id", "drive:folder:other", "a" * 32), self.cancelled
        )
        self.assertEqual(wrong.state, "uncertain")
        query = parse_qs(urlsplit(self.http.calls[-1][1]).query)["q"][0]
        self.assertIn("'other' in parents", query)

    def test_folder_absence_duplicates_or_incomplete_search_never_authorize_replay(
        self,
    ):
        self.folder()
        for lookup in (
            {"files": []},
            {"files": [{"id": "x"}, {"id": "y"}]},
            {"files": [{"id": "x"}], "incompleteSearch": True},
            {"files": [{"id": "x"}], "nextPageToken": "more"},
        ):
            self.http.lookup = lookup
            self.assertEqual(
                self.adapter.find_folder(
                    ConnectorProbe("local-id", "drive:root", "a" * 32), self.cancelled
                ).state,
                "uncertain",
            )
        self.assertEqual(len(self.http.folder_writes), 1)

    def test_inspection_has_no_listing_and_requires_exact_read_request(self):
        destination = self.folder()
        self.http.calls.clear()
        result = self.adapter.inspect_folder(
            self.request("inspect_folder", destination, artifact=False), self.cancelled
        )
        self.assertEqual(json.loads(result.text)["name"], "Synthetic draft")
        self.assertEqual(
            [(method, url.split("?")[0]) for method, url, _, _ in self.http.calls],
            [("GET", DRIVE + "/files/synthetic-folder-1")],
        )
        with self.assertRaises(ValueError):
            self.adapter.inspect_folder(
                self.request("inspect_folder", destination), self.cancelled
            )
        with self.assertRaises(ValueError):
            self.adapter.inspect_folder(
                self.request("inspect_folder", artifact=False), self.cancelled
            )

    def test_disconnection_or_cancellation_during_parent_read_prevents_mutation(self):
        destination = self.folder()
        original = self.http.request

        def request(method, url, **kwargs):
            result = original(method, url, **kwargs)
            if "/files/synthetic-folder-1?" in url:
                self.store.disable(self.account.account_id)
            return result

        self.http.request = request
        with self.assertRaises(AccountError):
            self.adapter.create_folder(
                self.request(destination=destination, operation="b"), self.cancelled
            )
        self.assertEqual(len(self.http.folder_writes), 1)

    def test_same_subject_reconnect_can_use_folder_with_new_connector(self):
        destination = self.folder()
        account = install_account(self.store)
        adapter = GoogleConnector(account, self.store, http=self.http)
        request = self.request(destination=destination, operation="b")
        request = ConnectorRequest(
            request.request_id,
            account.connector,
            request.tool,
            request.effect,
            request.destination,
            request.artifact,
            request.operation_id,
        )
        self.assertNotEqual(account.connector, self.account.connector)
        self.assertEqual(
            adapter.create_folder(request, self.cancelled).state, "succeeded"
        )

    def test_real_http_lost_folder_ack_recovers_and_doc_uses_verified_parent(self):
        fixture = self.http
        fixture.lose_folder = True

        class Handler(BaseHTTPRequestHandler):
            def handle_api(self):
                url = (
                    "https://oauth2.googleapis.com"
                    if self.path == "/token"
                    else "https://openidconnect.googleapis.com"
                    if self.path == "/v1/userinfo"
                    else "https://www.googleapis.com"
                ) + self.path
                body = (
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    if self.command == "POST"
                    else None
                )
                try:
                    result = fixture.request(
                        self.command, url, headers=dict(self.headers), body=body
                    )
                except ApiError:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                raw = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = handle_api
            do_POST = handle_api

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)

            class Opener:
                def open(self, request, timeout):
                    parsed = urlsplit(request.full_url)
                    routed = Request(
                        f"http://127.0.0.1:{server.server_port}"
                        + parsed.path
                        + ("?" + parsed.query if parsed.query else ""),
                        data=request.data,
                        headers=dict(request.header_items()),
                        method=request.get_method(),
                    )
                    return build_opener(NoRedirect()).open(routed, timeout=timeout)

            adapter = GoogleConnector(self.account, self.store)
            with patch("services.app_oauth.build_opener", return_value=Opener()):
                with self.assertRaises(ApiError):
                    adapter.create_folder(self.request(), self.cancelled)
                recovered = adapter.find_folder(
                    ConnectorProbe("local-id", "drive:root", "a" * 32), self.cancelled
                )
                self.assertEqual(recovered.state, "succeeded")
                adapter.create_document(
                    self.request(
                        "create_document", "drive:folder:synthetic-folder-1", "b"
                    ),
                    self.cancelled,
                )
            server.shutdown()
            thread.join(2)
        self.assertEqual(len(fixture.folder_writes), 1)
        self.assertEqual(len(fixture.documents), 1)
