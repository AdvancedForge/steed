"""Synthetic credentials and Google API responses; never touch desktop secrets."""

import copy
import json
import threading
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs

from services.app_accounts import AccountError
from services.app_oauth import ApiError
from services.google_connector import (
    DRIVE,
    FOLDER_MIME,
    SCOPES,
    SEND,
    TOKEN,
    UPLOAD,
    USERINFO,
)
from services.google_documents import DOCS, DOCUMENT_MIME


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
        self.folders = {}
        self.folder_writes = []
        self.document_metadata = {}
        self.document_data = {}
        self.append_writes = []
        self.lose_append = False
        self.append_status = 0
        self.malformed_append_reply = False
        self.lose_folder = False
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
            parts = BytesParser(policy=policy.default).parsebytes(
                ("Content-Type: " + headers["Content-Type"] + "\r\n\r\n").encode()
                + body
            )
            parts = list(parts.iter_parts())
            metadata = json.loads(parts[0].get_payload(decode=True))
            text = parts[1].get_payload(decode=True).decode("utf-8")
            self.document_metadata["synthetic-doc"] = {
                **metadata,
                "id": "synthetic-doc",
                "mimeType": DOCUMENT_MIME,
                "trashed": False,
                "ownedByMe": True,
                "capabilities": {"canEdit": True},
            }
            self.document_data["synthetic-doc"] = {
                "documentId": "synthetic-doc",
                "title": metadata["name"],
                "revisionId": "revision-1",
                "suggestionsViewMode": "SUGGESTIONS_INLINE",
                "tabs": [synthetic_document_tab("t.0", text + "\n")],
            }
            if self.lose_document:
                raise ApiError()
            return {"id": "synthetic-doc"}
        if url == SEND:
            self.messages.append(json.loads(body))
            if self.lose_email:
                raise ApiError()
            return {"id": "synthetic-message"}
        if method == "POST" and url.startswith(DRIVE + "/files?"):
            metadata = json.loads(body)
            self.folder_writes.append(metadata)
            folder_id = "synthetic-folder-" + str(len(self.folder_writes))
            self.folders[folder_id] = {
                **metadata,
                "id": folder_id,
                "trashed": False,
                "ownedByMe": True,
                "capabilities": {"canAddChildren": True},
            }
            if self.lose_folder:
                raise ApiError()
            return {"id": folder_id}
        if url.startswith(DRIVE + "/files/"):
            folder_id = url.split("/files/", 1)[1].split("?", 1)[0]
            files = {**self.folders, **self.document_metadata}
            if folder_id not in files:
                raise ApiError(404)
            return copy.deepcopy(files[folder_id])
        if url.startswith(DOCS + "/"):
            file_id = (
                url.removeprefix(DOCS + "/")
                .split("?", 1)[0]
                .removesuffix(":batchUpdate")
            )
            document = self.document_data[file_id]
            if method == "GET":
                return copy.deepcopy(document)
            batch = json.loads(body)
            if self.append_status:
                raise ApiError(self.append_status)
            if batch["writeControl"]["requiredRevisionId"] != document["revisionId"]:
                raise ApiError(400)
            self.append_writes.append(batch)
            insert, named = batch["requests"]
            location = insert["insertText"]["location"]
            pending = list(document["tabs"])
            while pending:
                tab = pending.pop(0)
                pending.extend(tab.get("childTabs", []))
                if tab["tabProperties"]["tabId"] == location["tabId"]:
                    break
            else:
                raise AssertionError("Unknown fixture tab")
            original = "".join(
                e["textRun"]["content"]
                for item in tab["documentTab"]["body"]["content"]
                if "paragraph" in item
                for e in item["paragraph"]["elements"]
            )
            encoded = original.encode("utf-16-le")
            offset = (location["index"] - 1) * 2
            text = (
                encoded[:offset]
                + insert["insertText"]["text"].encode("utf-16-le")
                + encoded[offset:]
            ).decode("utf-16-le")
            tab["documentTab"]["body"] = synthetic_document_tab(
                location["tabId"], text
            )["documentTab"]["body"]
            name = named["createNamedRange"]["name"]
            tab["documentTab"].setdefault("namedRanges", {})[name] = {
                "name": name,
                "namedRanges": [
                    {
                        "name": name,
                        "namedRangeId": "range-" + str(len(self.append_writes)),
                        "ranges": [named["createNamedRange"]["range"]],
                    }
                ],
            }
            document["revisionId"] = "revision-" + str(len(self.append_writes) + 1)
            if self.lose_append:
                raise ApiError()
            if self.malformed_append_reply:
                return {"documentId": file_id}
            return {
                "documentId": file_id,
                "replies": [
                    {},
                    {
                        "createNamedRange": {
                            "namedRangeId": "range-" + str(len(self.append_writes))
                        }
                    },
                ],
            }
        if url.startswith(DRIVE + "/files?"):
            query = parse_qs(url.split("?", 1)[1]).get("q", [""])[0]
            if FOLDER_MIME in query:
                return (
                    self.lookup
                    if self.lookup is not None
                    else {
                        "files": [
                            {"id": key}
                            for key, value in self.folders.items()
                            if value["appProperties"]["steed_operation"] in query
                            and "'" + value["parents"][0] + "' in parents" in query
                        ]
                    }
                )
            return (
                self.lookup
                if self.lookup is not None
                else {"files": [{"id": "synthetic-doc"}] if self.documents else []}
            )
        raise AssertionError("Unexpected synthetic endpoint")


def synthetic_document_tab(tab_id="t.0", text="EXISTING_TEXT\n", title="Fixture tab"):
    end = 1 + len(text.encode("utf-16-le")) // 2
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {
            "body": {
                "content": [
                    {"startIndex": 0, "endIndex": 1, "sectionBreak": {}},
                    {
                        "startIndex": 1,
                        "endIndex": end,
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": 1,
                                    "endIndex": end,
                                    "textRun": {"content": text},
                                },
                            ]
                        },
                    },
                ]
            }
        },
    }
