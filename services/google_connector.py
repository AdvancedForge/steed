"""Google's first trusted mappings: new Docs and single-recipient Gmail sends."""

import base64
import json
import re
import threading
import time
from email.message import EmailMessage
from email.policy import SMTP
from urllib.parse import urlencode

from services.app_accounts import AccountError
from services.app_oauth import ApiError, AppHttp, desktop_authorization
from services.connectors import ConnectorOutcome, ConnectorTool
from services.task_artifacts import artifact_input

AUTHORIZATION = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPES = {
    "documents": "https://www.googleapis.com/auth/drive.file",
    "email": "https://www.googleapis.com/auth/gmail.send",
}


def google_http():
    return AppHttp(
        {
            "https://oauth2.googleapis.com",
            "https://openidconnect.googleapis.com",
            "https://www.googleapis.com",
            "https://gmail.googleapis.com",
        }
    )


def email_destination(destination):
    """One ASCII mailbox only; reject lists, headers, display names and aliases."""
    if not isinstance(destination, str) or not destination.startswith("email:"):
        raise ValueError("Use email:recipient@example.com as the exact destination")
    address = destination.removeprefix("email:")
    match = re.fullmatch(
        r"([A-Za-z0-9.!#$%&'+/=?^_`{|}~-]{1,64})@([a-z0-9.-]+)", address
    )
    if not match or len(address) > 254:
        raise ValueError("Provide one ASCII email address with a lowercase domain")
    local, domain = match.groups()
    labels = domain.split(".")
    if (
        local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", s) for s in labels
        )
    ):
        raise ValueError("Provide one valid email mailbox, without display names")
    return address


def document_destination(destination):
    if destination != "drive:root":
        raise ValueError("Google Docs currently supports only drive:root (My Drive)")


def _identity(http, access_token, cancelled):
    result = http.request(
        "GET",
        USERINFO,
        headers={"Authorization": "Bearer " + access_token},
        cancelled=cancelled,
    )
    subject, label = result.get("sub"), result.get("email")
    if (
        not isinstance(subject, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", subject)
        or result.get("email_verified") is not True
    ):
        raise AccountError("Google did not return a verified account identity.")
    email_destination("email:" + label if isinstance(label, str) else "")
    return subject, label


def _token_response(result, scopes):
    token = result.get("access_token")
    expires = result.get("expires_in")
    if (
        result.get("token_type", "").lower() != "bearer"
        or not isinstance(token, str)
        or not 1 <= len(token) <= 8192
        or not all(33 <= ord(c) < 127 for c in token)
        or type(expires) is not int
        or not 1 <= expires <= 86400
        or not isinstance(result.get("scope", ""), str)
    ):
        raise AccountError("Google returned an invalid token response.")
    # Google may return canonical userinfo.email instead of the email alias.
    returned = set(result.get("scope", "").split())
    returned |= (
        {"email"}
        if "https://www.googleapis.com/auth/userinfo.email" in returned
        else set()
    )
    if returned and not set(scopes).issubset(returned):
        raise AccountError("Google did not approve all selected capabilities.")
    return token, time.time() + expires


def connect_google(
    store, client_file, capabilities, cancelled, *, http=None, authorize=None
):
    """Explicit user setup, external browser consent, then OS-keyring persistence."""
    capabilities = tuple(sorted(capabilities))
    if (
        not capabilities
        or len(set(capabilities)) != len(capabilities)
        or not set(capabilities) <= SCOPES.keys()
    ):
        raise ValueError("Select Google Docs and/or Gmail send")
    store.secrets.check(unlock=True)
    try:
        raw = client_file.read_bytes()
        if len(raw) > 65536:
            raise ValueError("Client file too large")
        client = json.loads(raw)["installed"]
        client_id, client_secret = client["client_id"], client["client_secret"]
        if (
            not isinstance(client_id, str)
            or not re.fullmatch(
                r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", client_id
            )
            or not isinstance(client_secret, str)
            or not 1 <= len(client_secret) <= 1024
            or not all(33 <= ord(c) < 127 for c in client_secret)
        ):
            raise ValueError("Invalid desktop credentials")
    except (OSError, ValueError, KeyError, TypeError):
        raise AccountError(
            "Choose a Google OAuth client JSON for a Desktop app."
        ) from None
    http = http if http is not None else google_http()
    authorize = authorize if authorize is not None else desktop_authorization
    scopes = ("openid", "email", *(SCOPES[c] for c in capabilities))
    code, verifier, redirect = authorize(AUTHORIZATION, client_id, scopes, cancelled)
    result = http.request(
        "POST",
        TOKEN,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect,
                "grant_type": "authorization_code",
            }
        ).encode("ascii"),
        cancelled=cancelled,
    )
    access, expires = _token_response(result, scopes)
    refresh = result.get("refresh_token")
    if (
        not isinstance(refresh, str)
        or not 1 <= len(refresh) <= 8192
        or not all(33 <= ord(c) < 127 for c in refresh)
    ):
        raise AccountError(
            "Google did not return offline access. Connect again with consent."
        )
    subject, label = _identity(http, access, cancelled)
    return store.install(
        "google",
        subject,
        label,
        capabilities,
        {
            "provider": "google",
            "subject": subject,
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "access_token": access,
            "expires_at": expires,
            "scopes": list(scopes),
        },
        cancelled,
    )


class GoogleConnector:
    def __init__(self, account, store, *, http=None):
        if (
            account.provider != "google"
            or not set(account.capabilities) <= SCOPES.keys()
        ):
            raise ValueError("Unsupported Google account capabilities")
        self.account, self.store = account, store
        self.http = http if http is not None else google_http()
        self._lock = threading.Lock()
        self._credential = None

    def tools(self):
        definitions = []
        if "documents" in self.account.capabilities:
            definitions.append(
                ConnectorTool(
                    self.account.connector,
                    "create_document",
                    "write",
                    f"Google Docs ({self.account.label}): create a plain-text document in drive:root",
                    True,
                    self.create_document,
                    self.find_document,
                    document_destination,
                )
            )
        if "email" in self.account.capabilities:
            definitions.append(
                ConnectorTool(
                    self.account.connector,
                    "send_email",
                    "send",
                    f"Gmail ({self.account.label}): send a draft to email:recipient@example.com; no mailbox reading",
                    True,
                    self.send_email,
                    None,
                    email_destination,
                )
            )
        return definitions

    def _access(self, cancelled):
        """Serialize refresh and verify immutable subject before accepting a token."""
        with self._lock:
            self.store.active(self.account)
            if cancelled.is_set():
                raise AccountError("Operation cancelled.")
            credential = self._credential
            if credential is not None and credential["expires_at"] > time.time() + 60:
                return credential["access_token"]
            credential = self.store.secrets.load(self.account.account_id)
            scopes = {
                "openid",
                "email",
                *(SCOPES[c] for c in self.account.capabilities),
            }
            if (
                credential.get("provider") != "google"
                or credential.get("subject") != self.account.subject
                or not isinstance(credential.get("scopes"), list)
                or not scopes.issubset(credential["scopes"])
            ):
                raise AccountError(
                    "The stored credential does not match this connection."
                )
            # Always refresh on first use after startup; do not trust disk expiry/identity.
            result = self.http.request(
                "POST",
                TOKEN,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode(
                    {
                        "client_id": credential["client_id"],
                        "client_secret": credential["client_secret"],
                        "refresh_token": credential["refresh_token"],
                        "grant_type": "refresh_token",
                    }
                ).encode("ascii"),
                cancelled=cancelled,
            )
            access, expires = _token_response(result, scopes)
            subject, label = _identity(self.http, access, cancelled)
            if subject != self.account.subject or label != self.account.label:
                raise AccountError(
                    "Google's account identity changed. Reconnect the account."
                )
            credential = {**credential, "access_token": access, "expires_at": expires}
            if "refresh_token" in result:
                refresh = result["refresh_token"]
                if (
                    not isinstance(refresh, str)
                    or not 1 <= len(refresh) <= 8192
                    or not all(33 <= ord(c) < 127 for c in refresh)
                ):
                    raise AccountError("Invalid refreshed credential.")
                credential["refresh_token"] = refresh
            # Rotation must be saved before a write can use this token.
            self.store.active(self.account)
            self.store.save_credential(self.account, credential)
            self._credential = credential
            return access

    def _headers(self, cancelled):
        return {"Authorization": "Bearer " + self._access(cancelled)}

    def _write_input(self, request, tool, effect):
        if (
            request.connector != self.account.connector
            or request.tool != tool
            or request.effect != effect
            or request.artifact is None
            or not re.fullmatch(r"[a-f0-9]{32}", request.operation_id)
        ):
            raise ValueError("Invalid Google operation")
        artifact_input(
            request.artifact.title, request.artifact.content, request.artifact.format
        )

    def create_document(self, request, cancelled):
        self._write_input(request, "create_document", "write")
        document_destination(request.destination)
        try:
            headers = self._headers(cancelled)
        except (AccountError, ApiError):
            return ConnectorOutcome(
                "not_applied",
                text="Google authorization is unavailable; no document was requested.",
            )
        boundary = "steed_" + request.operation_id
        # A user draft cannot inject MIME parts through a matching boundary.
        while boundary in request.artifact.content:
            boundary += "x"
        metadata = {
            "name": request.artifact.title,
            "mimeType": "application/vnd.google-apps.document",
            "parents": ["root"],
            "appProperties": {"steed_operation": request.operation_id},
        }
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=utf-8\r\n\r\n"
            + json.dumps(metadata)
            + f"\r\n--{boundary}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            + request.artifact.content
            + f"\r\n--{boundary}--\r\n"
        ).encode("utf-8")
        headers["Content-Type"] = f"multipart/related; boundary={boundary}"
        self.store.active(self.account)
        result = self.http.request(
            "POST",
            UPLOAD + "?" + urlencode({"uploadType": "multipart", "fields": "id"}),
            headers=headers,
            body=body,
            cancelled=cancelled,
        )
        return self._document_outcome(result)

    def _document_outcome(self, result):
        file_id = result.get("id")
        if not isinstance(file_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,200}", file_id
        ):
            raise ApiError()
        return ConnectorOutcome(
            reference=f"https://docs.google.com/document/d/{file_id}/edit"
        )

    def find_document(self, probe, cancelled):
        document_destination(probe.destination)
        if not re.fullmatch(r"[a-f0-9]{32}", probe.operation_id):
            raise ValueError("Invalid Google operation identity")
        query = (
            "trashed = false and 'root' in parents and "
            "mimeType = 'application/vnd.google-apps.document' and "
            f"appProperties has {{ key='steed_operation' and value='{probe.operation_id}' }}"
        )
        result = self.http.request(
            "GET",
            DRIVE
            + "/files?"
            + urlencode(
                {
                    "q": query,
                    "fields": "files(id),nextPageToken,incompleteSearch",
                    "pageSize": 2,
                    "spaces": "drive",
                    "corpora": "user",
                }
            ),
            headers=self._headers(cancelled),
            cancelled=cancelled,
        )
        files = result.get("files")
        if not isinstance(files, list):
            raise ApiError()
        if (
            len(files) == 1
            and not result.get("nextPageToken")
            and not result.get("incompleteSearch")
        ):
            return self._document_outcome(files[0])
        # Search absence is not proof of non-creation (indexing delay, moves/deletion).
        return ConnectorOutcome(
            "uncertain",
            text="A unique document could not be confirmed. Check My Drive before creating another.",
        )

    def send_email(self, request, cancelled):
        self._write_input(request, "send_email", "send")
        recipient = email_destination(request.destination)
        try:
            headers = self._headers(cancelled)
        except (AccountError, ApiError):
            return ConnectorOutcome(
                "not_applied",
                text="Google authorization is unavailable; no email was requested.",
            )
        message = EmailMessage(policy=SMTP)
        message["From"] = self.account.label
        message["To"] = recipient
        message["Subject"] = request.artifact.title
        # For manual receipt identification only; Gmail does not promise deduplication.
        message["Message-ID"] = f"<steed.{request.operation_id}@steed.local>"
        message.set_content(request.artifact.content)
        body = json.dumps(
            {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
        self.store.active(self.account)
        result = self.http.request(
            "POST", SEND, headers=headers, body=body, cancelled=cancelled
        )
        message_id = result.get("id")
        if not isinstance(message_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,200}", message_id
        ):
            raise ApiError()
        return ConnectorOutcome(
            reference="gmail-message:" + message_id,
            text=f"Sent to {recipient}. Check Gmail Sent for the message.",
        )
