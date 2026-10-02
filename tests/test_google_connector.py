"""Google request wires, least privilege, uncertain effects and identity binding."""

import base64
import hashlib
import io
import json
import tempfile
import threading
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from app_connector_support import MemorySecrets, SyntheticGoogle, install_account

from services.app_accounts import AccountError, AccountStore
from services.app_oauth import ApiError, AppHttp, desktop_authorization
from services.connectors import ConnectorProbe, ConnectorRequest
from services.google_connector import (
    SCOPES,
    SEND,
    TOKEN,
    UPLOAD,
    GoogleConnector,
    connect_google,
    email_destination,
)
from services.task_artifacts import TaskArtifact, artifact_input


def draft(content="SYNTHETIC_DRAFT_BODY", title="Synthetic draft"):
    _, size, sha, sources = artifact_input(title, content)
    return TaskArtifact(
        "artifact-1",
        "task-1",
        title,
        "markdown",
        size,
        sha,
        sources,
        "fixture-time",
        content,
    )


class GoogleConnectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.secrets = MemorySecrets()
        self.store = AccountStore(self.path / "accounts.json", self.secrets)
        self.account = install_account(self.store)
        self.http = SyntheticGoogle()
        self.adapter = GoogleConnector(self.account, self.store, http=self.http)
        self.cancelled = threading.Event()

    def request(
        self,
        *,
        tool="create_document",
        effect="write",
        destination="drive:root",
        artifact=None,
    ):
        return ConnectorRequest(
            "local-id",
            self.account.connector,
            tool,
            effect,
            destination,
            artifact or draft(),
            "a" * 32,
        )

    def test_google_docs_single_multipart_write_uses_plain_text_and_operation_property(
        self,
    ):
        outcome = self.adapter.create_document(self.request(), self.cancelled)
        self.assertEqual(
            outcome.reference, "https://docs.google.com/document/d/synthetic-doc/edit"
        )
        writes = [c for c in self.http.calls if c[1].startswith(UPLOAD)]
        self.assertEqual(len(writes), 1)
        method, url, headers, body = writes[0]
        self.assertEqual(method, "POST")
        parsed = BytesParser(policy=policy.default).parsebytes(
            (
                "Content-Type: "
                + headers["Content-Type"]
                + "\r\nMIME-Version: 1.0\r\n\r\n"
            ).encode()
            + body
        )
        parts = list(parsed.iter_parts())
        self.assertEqual(len(parts), 2)
        metadata = json.loads(parts[0].get_payload(decode=True))
        self.assertEqual(metadata["parents"], ["root"])
        self.assertEqual(metadata["mimeType"], "application/vnd.google-apps.document")
        self.assertEqual(metadata["appProperties"]["steed_operation"], "a" * 32)
        self.assertEqual(
            parts[1].get_payload(decode=True).decode(), "SYNTHETIC_DRAFT_BODY"
        )

    def test_mime_boundary_in_draft_cannot_inject_a_part(self):
        artifact = draft(
            "--steed_" + "a" * 32 + "\r\nContent-Type: text/html\r\nINJECT"
        )
        self.adapter.create_document(self.request(artifact=artifact), self.cancelled)
        headers = self.http.calls[-1][2]
        self.assertTrue(headers["Content-Type"].endswith("x"))

    def test_email_has_exact_recipient_title_plain_body_and_no_extra_headers(self):
        result = self.adapter.send_email(
            self.request(
                tool="send_email",
                effect="send",
                destination="email:Recipient@example.test",
            ),
            self.cancelled,
        )
        self.assertEqual(result.reference, "gmail-message:synthetic-message")
        raw = base64.urlsafe_b64decode(self.http.messages[0]["raw"])
        email = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(email["To"], "Recipient@example.test")
        self.assertEqual(email["From"], "fixture@example.test")
        self.assertEqual(email["Subject"], "Synthetic draft")
        self.assertIn("a" * 32, email["Message-ID"])
        self.assertIsNone(email["Bcc"])
        self.assertIn("SYNTHETIC_DRAFT_BODY", email.get_content())
        self.assertFalse(email.is_multipart())
        self.assertEqual([c[0] for c in self.http.calls if c[1] == SEND], ["POST"])

    def test_invalid_recipient_or_wrong_root_fails_before_credential_or_http_access(
        self,
    ):
        for destination in (
            "email:a@example.test,b@example.test",
            "email:Name <a@example.test>",
            "email:a@EXAMPLE.test",
            "email:a@example.test\r\nBcc:b@example.test",
            "email:.a@example.test",
            "email:a@-example.test",
            "email:a..b@example.test",
        ):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.adapter.send_email(
                    self.request(
                        tool="send_email", effect="send", destination=destination
                    ),
                    self.cancelled,
                )
        with self.assertRaises(ValueError):
            self.adapter.create_document(
                self.request(destination="drive:other"), self.cancelled
            )
        self.assertFalse(self.http.calls)
        self.assertEqual(
            email_destination("email:A+label@example.test"), "A+label@example.test"
        )

    def test_doc_lookup_is_read_only_and_absence_is_uncertain(self):
        probe = ConnectorProbe("local-id", "drive:root", "a" * 32)
        result = self.adapter.find_document(probe, self.cancelled)
        self.assertEqual(result.state, "uncertain")
        query = parse_qs(urlsplit(self.http.calls[-1][1]).query)
        self.assertIn("'root' in parents", query["q"][0])
        self.assertIn("steed_operation", query["q"][0])
        for lookup in (
            {"files": [{"id": "doc"}], "incompleteSearch": True},
            {"files": [{"id": "doc"}], "nextPageToken": "more"},
            {"files": [{"id": "doc"}, {"id": "other"}]},
        ):
            self.http.lookup = lookup
            self.assertEqual(
                self.adapter.find_document(probe, self.cancelled).state, "uncertain"
            )
        self.http.lookup = {"files": [{"id": "confirmed"}]}
        self.assertIn(
            "confirmed", self.adapter.find_document(probe, self.cancelled).reference
        )
        self.assertFalse(self.http.documents)
        self.assertFalse(self.http.messages)

    def test_lost_acknowledgement_is_not_retried_and_gmail_has_no_lookup(self):
        self.http.lose_document = True
        with self.assertRaises(ApiError):
            self.adapter.create_document(self.request(), self.cancelled)
        self.assertEqual(len(self.http.documents), 1)
        result = self.adapter.find_document(
            ConnectorProbe("local-id", "drive:root", "a" * 32), self.cancelled
        )
        self.assertEqual(result.state, "succeeded")
        self.http.lose_email = True
        with self.assertRaises(ApiError):
            self.adapter.send_email(
                self.request(
                    tool="send_email",
                    effect="send",
                    destination="email:recipient@example.test",
                ),
                self.cancelled,
            )
        self.assertEqual(len(self.http.messages), 1)
        self.assertIsNone(self.adapter.tools()[1].reconcile)

    def test_mismatched_account_subject_never_dispatches_write(self):
        for field, value in (
            ("subject", "wrong-account"),
            ("label", "wrong@example.test"),
        ):
            original = getattr(self.http, field)
            setattr(self.http, field, value)
            result = self.adapter.create_document(self.request(), self.cancelled)
            self.assertEqual(result.state, "not_applied")
            setattr(self.http, field, original)
        self.assertFalse(self.http.documents)
        self.assertFalse(self.http.messages)

    def test_stored_subject_or_provider_mismatch_never_contacts_google(self):
        for key, value in (("subject", "other"), ("provider", "other")):
            stored = self.secrets.values[self.account.account_id]
            previous = stored[key]
            stored[key] = value
            self.assertEqual(
                self.adapter.create_document(self.request(), self.cancelled).state,
                "not_applied",
            )
            stored[key] = previous
        self.assertFalse(self.http.calls)

    def test_partial_scope_and_refresh_failure_do_not_create_document(self):
        self.http.scopes = ("openid", "email", SCOPES["email"])
        self.assertEqual(
            self.adapter.create_document(self.request(), self.cancelled).state,
            "not_applied",
        )
        self.http.scopes = None
        self.http.fail_refresh = True
        self.assertEqual(
            self.adapter.create_document(self.request(), self.cancelled).state,
            "not_applied",
        )
        self.assertFalse(self.http.documents)

    def test_refresh_rotation_persists_before_write_and_cached_identity_is_bound(self):
        self.http.rotated_refresh = "ROTATED_SYNTHETIC_REFRESH"
        self.adapter.create_document(self.request(), self.cancelled)
        self.assertEqual(
            self.secrets.values[self.account.account_id]["refresh_token"],
            "ROTATED_SYNTHETIC_REFRESH",
        )
        self.adapter.create_document(self.request(), self.cancelled)
        self.assertEqual(sum(c[1] == TOKEN for c in self.http.calls), 1)
        self.store.disable(self.account.account_id)
        self.assertEqual(
            self.adapter.create_document(self.request(), self.cancelled).state,
            "not_applied",
        )
        self.assertEqual(len(self.http.documents), 2)

    def test_keyring_failure_or_cancellation_prevents_mutation(self):
        self.secrets.available = False
        self.assertEqual(
            self.adapter.create_document(self.request(), self.cancelled).state,
            "not_applied",
        )
        self.secrets.available = True
        self.cancelled.set()
        self.assertEqual(
            self.adapter.create_document(self.request(), self.cancelled).state,
            "not_applied",
        )
        self.assertFalse(self.http.calls)

    def client_file(self, data=None):
        path = self.path / "client.json"
        path.write_text(
            json.dumps(
                data
                if data is not None
                else {
                    "installed": {
                        "client_id": "synthetic.apps.googleusercontent.com",
                        "client_secret": "SYNTHETIC_SECRET",
                        "token_uri": "https://evil.invalid/token",
                    }
                }
            )
        )
        return path

    def authorize(self, endpoint, client_id, scopes, cancelled):
        self.assertNotIn("https://www.googleapis.com/auth/gmail.readonly", scopes)
        self.assertNotIn("https://www.googleapis.com/auth/drive", scopes)
        return "SYNTHETIC_CODE", "SYNTHETIC_VERIFIER", "http://127.0.0.1:1234/callback"

    def test_setup_ignores_client_supplied_endpoints_and_only_enables_selected_tools(
        self,
    ):
        account = connect_google(
            self.store,
            self.client_file(),
            ("documents",),
            self.cancelled,
            http=self.http,
            authorize=self.authorize,
        )
        tools = GoogleConnector(account, self.store, http=self.http).tools()
        self.assertEqual(
            [t.tool for t in tools],
            [
                "create_document",
                "create_folder",
                "inspect_folder",
                "inspect_document",
                "read_document",
                "append_document",
            ],
        )
        self.assertEqual(
            [c[1] for c in self.http.calls],
            [TOKEN, "https://openidconnect.googleapis.com/v1/userinfo"],
        )
        self.assertNotIn("SYNTHETIC_SECRET", self.store.path.read_text())

    def test_web_client_or_unverified_identity_is_not_persisted(self):
        initial = self.store.list()
        with self.assertRaises(AccountError):
            connect_google(
                self.store,
                self.client_file({"web": {}}),
                ("email",),
                self.cancelled,
                http=self.http,
                authorize=self.authorize,
            )
        self.http.identity_verified = False
        with self.assertRaises(AccountError):
            connect_google(
                self.store,
                self.client_file(),
                ("email",),
                self.cancelled,
                http=self.http,
                authorize=self.authorize,
            )
        self.assertEqual(self.store.list(), initial)

    def test_credential_persistence_failure_prevents_write(self):
        with patch.object(
            self.store, "save_credential", side_effect=AccountError("fixture")
        ):
            self.assertEqual(
                self.adapter.create_document(self.request(), self.cancelled).state,
                "not_applied",
            )
        self.assertFalse(self.http.documents)

    def test_saved_setup_connects_again_without_a_client_file(self):
        connect_google(
            self.store,
            self.client_file(),
            ("documents",),
            self.cancelled,
            http=self.http,
            authorize=self.authorize,
        )
        account = connect_google(
            self.store,
            None,
            ("email",),
            self.cancelled,
            http=self.http,
            authorize=self.authorize,
        )
        self.assertEqual(account.capabilities, ("email",))
        self.assertNotIn("SYNTHETIC_SECRET", self.store.path.read_text())

    def test_existing_account_upgrades_to_reusable_setup_without_file(self):
        account = connect_google(
            self.store,
            None,
            ("email",),
            self.cancelled,
            http=self.http,
            authorize=self.authorize,
        )
        self.assertEqual(account.capabilities, ("email",))
        self.assertIn("client_id", self.store.client_configuration("google"))

    def test_forgetting_setup_never_recovers_it_from_an_existing_account(self):
        self.store.forget_client_configuration("google")
        with self.assertRaises(AccountError):
            connect_google(
                self.store,
                None,
                ("email",),
                self.cancelled,
                http=self.http,
                authorize=self.authorize,
            )
        self.assertFalse(self.http.calls)
        self.assertEqual(len(self.store.list()), 1)

    def test_first_connection_without_config_shows_actionable_setup_message(self):
        empty = AccountStore(self.path / "empty.json", MemorySecrets())
        with self.assertRaisesRegex(AccountError, "Set up Google access once"):
            connect_google(
                empty,
                None,
                ("email",),
                self.cancelled,
                http=self.http,
                authorize=self.authorize,
            )
        self.assertFalse(self.http.calls)


class AppHttpTests(unittest.TestCase):
    def test_endpoint_allowlist_and_cancellation_precede_transport(self):
        http = AppHttp({"https://allowed.test"})
        for url in (
            "http://allowed.test",
            "https://allowed.test.evil.test",
            "https://allowed.test@evil.test",
            "https://allowed.test/path#fragment",
        ):
            with (
                self.subTest(url=url),
                patch("services.app_oauth.build_opener") as opener,
                self.assertRaises(ValueError),
            ):
                http.request("POST", url, body=b"SECRET")
                opener.assert_not_called()
        event = threading.Event()
        event.set()
        with self.assertRaises(AccountError):
            http.request("POST", "https://allowed.test", cancelled=event)

    def test_response_size_and_type_are_bounded(self):
        for raw in (b"x" * (128 * 1024 + 1), b"[]", b"not JSON"):
            with (
                self.subTest(raw=raw[:10]),
                patch("services.app_oauth.build_opener") as opener,
            ):
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(ApiError):
                    AppHttp({"https://allowed.test"}).request(
                        "GET", "https://allowed.test"
                    )

    def test_provider_error_body_is_never_exposed(self):
        with patch("services.app_oauth.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError(
                "https://allowed.test",
                403,
                "SECRET_PROVIDER_DETAIL",
                {},
                io.BytesIO(b"SECRET_BODY"),
            )
            with self.assertRaises(ApiError) as raised:
                AppHttp({"https://allowed.test"}).request(
                    "POST", "https://allowed.test"
                )
            self.assertEqual(raised.exception.status, 403)
            self.assertNotIn("SECRET", str(raised.exception))


class DesktopOAuthTests(unittest.TestCase):
    def test_pkce_state_host_path_and_duplicate_parameters(self):
        requests = []
        captured = {}

        def open_browser(url):
            query = parse_qs(urlsplit(url).query)
            captured.update(query)

            def respond():
                redirect, state = query["redirect_uri"][0], query["state"][0]
                invalid = (
                    {"state": "wrong", "code": "BAD"},
                    {"state": "é", "code": "BAD"},
                    {"state": [state, state], "code": "BAD"},
                    {"state": state, "code": ["BAD", "OTHER"]},
                )
                for fields in invalid:
                    try:
                        urlopen(
                            redirect + "?" + urlencode(fields, doseq=True), timeout=2
                        )
                    except HTTPError as error:
                        requests.append(error.code)
                with urlopen(
                    redirect
                    + "?"
                    + urlencode({"state": state, "code": "SYNTHETIC_CODE"}),
                    timeout=2,
                ) as response:
                    requests.append(response.status)

            threading.Thread(target=respond, daemon=True).start()
            return True

        code, verifier, redirect = desktop_authorization(
            "https://authorization.test",
            "CLIENT",
            ("openid",),
            threading.Event(),
            open_browser=open_browser,
            timeout=5,
        )
        self.assertEqual(code, "SYNTHETIC_CODE")
        self.assertTrue(redirect.startswith("http://127.0.0.1:"))
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(captured["code_challenge"], [challenge])
        self.assertEqual(captured["code_challenge_method"], ["S256"])
        self.assertEqual(requests[:4], [400] * 4)

    def test_denial_cancellation_timeout_and_browser_failure(self):
        with self.assertRaises(AccountError):
            desktop_authorization(
                "https://authorization.test",
                "CLIENT",
                ("openid",),
                threading.Event(),
                open_browser=lambda _: False,
            )
        with self.assertRaises(AccountError):
            desktop_authorization(
                "https://authorization.test",
                "CLIENT",
                ("openid",),
                threading.Event(),
                open_browser=lambda _: True,
                timeout=0.05,
            )
        event = threading.Event()
        event.set()
        with self.assertRaises(AccountError):
            desktop_authorization(
                "https://authorization.test",
                "CLIENT",
                ("openid",),
                event,
                open_browser=lambda _: self.fail("Cancelled setup opened browser"),
            )
