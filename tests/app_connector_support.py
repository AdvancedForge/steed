"""Synthetic credentials and Google API responses; never touch desktop secrets."""

import copy
import json
import threading
from urllib.parse import parse_qs

from services.app_accounts import AccountError
from services.app_oauth import ApiError
from services.google_connector import DRIVE, SCOPES, SEND, TOKEN, UPLOAD, USERINFO


class MemorySecrets:
    def __init__(self):
        self.values = {}
        self.available = True

    def check(self, *, unlock=False):
        if not self.available:
            raise AccountError("Synthetic keyring locked")

    def load(self, account_id):
        self.check()
        return copy.deepcopy(self.values[account_id])

    def save(self, account_id, value):
        self.check()
        self.values[account_id] = copy.deepcopy(value)

    def delete(self, account_id):
        self.check()
        self.values.pop(account_id, None)


def credential(subject="synthetic-subject", capabilities=("documents", "email")):
    return {
        "provider": "google",
        "subject": subject,
        "client_id": "synthetic.apps.googleusercontent.com",
        "client_secret": "SYNTHETIC_CLIENT_SECRET",
        "refresh_token": "SYNTHETIC_REFRESH",
        "access_token": "SYNTHETIC_ACCESS",
        "expires_at": 0,
        "scopes": ["openid", "email", *(SCOPES[c] for c in capabilities)],
    }


def install_account(
    store,
    *,
    subject="synthetic-subject",
    capabilities=("documents", "email"),
    provider="google",
):
    return store.install(
        provider,
        subject,
        "fixture@example.test",
        capabilities,
        credential(subject, capabilities),
        threading.Event(),
    )


class SyntheticGoogle:
    def __init__(self):
        self.calls = []
        self.documents = []
        self.messages = []
        self.subject = "synthetic-subject"
        self.label = "fixture@example.test"
        self.identity_verified = True
        self.scopes = None
        self.lose_document = False
        self.lose_email = False
        self.rotated_refresh = None
        self.fail_refresh = False
        self.lookup = None

    def request(self, method, url, *, headers=None, body=None, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise AccountError("Cancelled before synthetic HTTP request")
        self.calls.append((method, url, headers or {}, body))
        if url == TOKEN:
            if self.fail_refresh:
                raise ApiError(401)
            fields = parse_qs(body.decode("ascii"))
            if fields["grant_type"] == ["authorization_code"]:
                assert fields["code_verifier"] == ["SYNTHETIC_VERIFIER"]
            result = {
                "access_token": "SYNTHETIC_NEW_ACCESS",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": self.rotated_refresh or "SYNTHETIC_REFRESH",
            }
            if self.scopes is not None:
                result["scope"] = " ".join(self.scopes)
            return result
        if url == USERINFO:
            return {
                "sub": self.subject,
                "email": self.label,
                "email_verified": self.identity_verified,
            }
        if url.startswith(UPLOAD):
            self.documents.append(body)
            if self.lose_document:
                raise ApiError()
            return {"id": "synthetic-doc"}
        if url == SEND:
            self.messages.append(json.loads(body))
            if self.lose_email:
                raise ApiError()
            return {"id": "synthetic-message"}
        if url.startswith(DRIVE + "/files?"):
            return (
                self.lookup
                if self.lookup is not None
                else {"files": [{"id": "synthetic-doc"}] if self.documents else []}
            )
        raise AssertionError("Unexpected synthetic endpoint")
