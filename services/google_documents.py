"""Bounded, exact-tab reading and revision-guarded appends to app-created Docs."""

import json
import re
from urllib.parse import urlencode

from services.app_accounts import AccountError
from services.app_oauth import ApiError
from services.connectors import ConnectorOutcome, ConnectorTool

DOCS = "https://docs.googleapis.com/v1/documents"
DRIVE = "https://www.googleapis.com/drive/v3"
DOCUMENT_MIME = "application/vnd.google-apps.document"
FILE_ID = r"[A-Za-z0-9_-]{1,200}"
TAB_ID = r"[A-Za-z0-9_.-]{1,64}"


def document_identity(destination):
    if not isinstance(destination, str) or not re.fullmatch(
        "doc:" + FILE_ID, destination
    ):
        raise ValueError("Use doc:<Steed-created document ID>")
    return destination.removeprefix("doc:")


def tab_identity(destination):
    if not isinstance(destination, str):
        raise ValueError("Use doc:<document ID>:tab:<tab ID>")
    match = re.fullmatch("doc:(" + FILE_ID + "):tab:(" + TAB_ID + ")", destination)
    if not match:
        raise ValueError("Use doc:<document ID>:tab:<tab ID>")
    return match.groups()


def document_tabs(document):
    """Flatten bounded nested tabs; ambiguous identities fail closed."""
    tabs = document.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        raise ApiError()
    pending, result = [(tab, 1) for tab in reversed(tabs)], {}
    while pending:
        tab, depth = pending.pop()
        if not isinstance(tab, dict) or depth > 32 or len(result) >= 32:
            raise ApiError()
        properties = tab.get("tabProperties")
        if not isinstance(properties, dict):
            raise ApiError()
        tab_id = properties.get("tabId")
        if (
            not isinstance(tab_id, str)
            or not re.fullmatch(TAB_ID, tab_id)
            or tab_id in result
        ):
            raise ApiError()
        if not isinstance(tab.get("documentTab"), dict):
            raise ApiError()
        result[tab_id] = tab
        children = tab.get("childTabs", [])
        if not isinstance(children, list) or len(children) > 32:
            raise ApiError()
        pending.extend((child, depth + 1) for child in reversed(children))
    return result


def body_content(tab):
    body = tab["documentTab"].get("body")
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        raise ApiError()
    return body["content"]


def append_index(tab):
    content = body_content(tab)
    if (
        not isinstance(content, list)
        or not content
        or not isinstance(content[-1], dict)
    ):
        raise AccountError("The selected tab has no supported final paragraph.")
    last = content[-1]
    paragraph, end = last.get("paragraph"), last.get("endIndex")
    if not isinstance(paragraph, dict) or type(end) is not int or not 2 <= end < 2**31:
        raise AccountError("The selected tab has no supported final paragraph.")
    elements = paragraph.get("elements")
    if (
        not isinstance(elements, list)
        or not elements
        or not isinstance(elements[-1], dict)
    ):
        raise ApiError()
    final = elements[-1]
    run = final.get("textRun")
    text = run.get("content") if isinstance(run, dict) else None
    if (
        not isinstance(text, str)
        or not text.endswith("\n")
        or final.get("endIndex") != end
        or any(
            isinstance(e, dict)
            and (e.get("suggestedInsertionIds") or e.get("suggestedDeletionIds"))
            for e in elements
        )
    ):
        raise AccountError(
            "The selected tab's final paragraph cannot be safely appended."
        )
    return end - 1


class GoogleDocuments:
    def __init__(self, connector):
        self.connector = connector

    def tools(self):
        account = self.connector.account
        return [
            ConnectorTool(
                account.connector,
                "inspect_document",
                "read",
                f"Google Docs ({account.label}): read one exact Steed-created document's identity and tab names; no body text",
                False,
                self.inspect,
                None,
                document_identity,
            ),
            ConnectorTool(
                account.connector,
                "read_document",
                "read",
                f"Google Docs ({account.label}): read bounded plain text from one exact Steed-created document tab",
                False,
                self.read,
                None,
                tab_identity,
            ),
            ConnectorTool(
                account.connector,
                "append_document",
                "write",
                f"Google Docs ({account.label}): append draft content to one exact Steed-created document tab, preserving existing text; title unused, adds a visible operation range",
                True,
                self.append,
                self.find_append,
                tab_identity,
            ),
        ]

    def _fetch(self, file_id, cancelled):
        connector = self.connector
        headers = connector._headers(cancelled)
        connector.store.active(connector.account)
        metadata = connector.http.request(
            "GET",
            DRIVE
            + "/files/"
            + file_id
            + "?"
            + urlencode(
                {
                    "fields": "id,mimeType,trashed,ownedByMe,driveId,appProperties,capabilities(canEdit)"
                }
            ),
            headers=headers,
            cancelled=cancelled,
        )
        properties = metadata.get("appProperties")
        capabilities = metadata.get("capabilities")
        if (
            metadata.get("id") != file_id
            or metadata.get("mimeType") != DOCUMENT_MIME
            or metadata.get("trashed") is not False
            or metadata.get("ownedByMe") is not True
            or metadata.get("driveId")
            or not isinstance(properties, dict)
            or not isinstance(properties.get("steed_operation"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", properties["steed_operation"])
            or properties.get(
                "steed_account", connector._properties("")["steed_account"]
            )
            != connector._properties("")["steed_account"]
            or not isinstance(capabilities, dict)
        ):
            raise AccountError(
                "Choose an editable document created by Steed in this account."
            )
        connector.store.active(connector.account)
        result = connector.http.request(
            "GET",
            DOCS
            + "/"
            + file_id
            + "?"
            + urlencode(
                {
                    "includeTabsContent": "true",
                    "suggestionsViewMode": "SUGGESTIONS_INLINE",
                }
            ),
            headers=headers,
            cancelled=cancelled,
        )
        if (
            result.get("documentId") != file_id
            or result.get("suggestionsViewMode") != "SUGGESTIONS_INLINE"
        ):
            raise ApiError()
        return (
            result,
            document_tabs(result),
            headers,
            capabilities.get("canEdit") is True,
        )

    def _read_input(self, request, tool):
        if (
            request.connector != self.connector.account.connector
            or request.tool != tool
            or request.effect != "read"
            or request.artifact is not None
        ):
            raise ValueError("Invalid Google document read")

    def _outcome(self, file_id, text=""):
        return ConnectorOutcome(
            reference=f"https://docs.google.com/document/d/{file_id}/edit", text=text
        ).validate()

    def inspect(self, request, cancelled):
        self._read_input(request, "inspect_document")
        file_id = document_identity(request.destination)
        document, tabs, _, can_edit = self._fetch(file_id, cancelled)
        summaries = []
        for tab_id, tab in tabs.items():
            title = tab["tabProperties"].get("title")
            if not isinstance(title, str) or "\x00" in title:
                raise ApiError()
            try:
                append_index(tab)
                can_append = (
                    can_edit
                    and isinstance(document.get("revisionId"), str)
                    and bool(document["revisionId"])
                )
            except (AccountError, ApiError):
                can_append = False
            summaries.append(
                {
                    "destination": f"doc:{file_id}:tab:{tab_id}",
                    "title": title[:64],
                    "can_append": can_append,
                }
            )
        title = document.get("title")
        if not isinstance(title, str) or "\x00" in title:
            raise ApiError()
        payload = {
            "destination": request.destination,
            "title": title[:128],
            "tabs": summaries,
            "tabs_truncated": False,
        }
        while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 16000:
            summaries.pop()
            payload["tabs_truncated"] = True
        return self._outcome(file_id, json.dumps(payload, ensure_ascii=False))

    def read(self, request, cancelled):
        self._read_input(request, "read_document")
        file_id, tab_id = tab_identity(request.destination)
        _, tabs, _, _ = self._fetch(file_id, cancelled)
        tab = tabs.get(tab_id)
        if tab is None:
            raise AccountError("The approved document tab no longer exists.")
        content = body_content(tab)
        pieces, incomplete = [], False
        for item in content:
            if not isinstance(item, dict):
                raise ApiError()
            paragraph = item.get("paragraph")
            if paragraph is None:
                incomplete |= "sectionBreak" not in item
                continue
            if not isinstance(paragraph, dict) or not isinstance(
                paragraph.get("elements"), list
            ):
                raise ApiError()
            for element in paragraph["elements"]:
                if not isinstance(element, dict):
                    raise ApiError()
                incomplete |= bool(
                    element.get("suggestedInsertionIds")
                    or element.get("suggestedDeletionIds")
                )
                run = element.get("textRun")
                text = run.get("content") if isinstance(run, dict) else None
                if isinstance(text, str):
                    pieces.append(text)
                else:
                    incomplete = True
        text = "".join(pieces)
        if "\x00" in text:
            raise ApiError()
        # JSON escaping can expand sixfold. Bound the serialized result too.
        raw = text.encode("utf-8")
        bounded = raw[:8192].decode("utf-8", errors="ignore")
        payload = {
            "destination": request.destination,
            "text": bounded,
            "truncated": len(raw) > 8192,
            "incomplete": incomplete,
        }
        while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 16000:
            payload["text"] = payload["text"][: len(payload["text"]) // 2]
            payload["truncated"] = True
        return self._outcome(file_id, json.dumps(payload, ensure_ascii=False))

    def _marker(self, tab, operation_id):
        name = "steed_append_" + operation_id
        named = tab["documentTab"].get("namedRanges", {})
        if not isinstance(named, dict):
            raise ApiError()
        if name not in named:
            return None
        collection = named[name]
        if not isinstance(collection, dict) or collection.get("name") != name:
            return False
        ranges = collection.get("namedRanges")
        if (
            not isinstance(ranges, list)
            or len(ranges) != 1
            or not isinstance(ranges[0], dict)
        ):
            return False
        entry = ranges[0]
        spans = entry.get("ranges")
        if (
            entry.get("name") != name
            or not isinstance(entry.get("namedRangeId"), str)
            or not entry["namedRangeId"]
            or not isinstance(spans, list)
            or not spans
        ):
            return False
        for span in spans:
            if (
                not isinstance(span, dict)
                or span.get("segmentId")
                or span.get("tabId", tab["tabProperties"]["tabId"])
                != tab["tabProperties"]["tabId"]
                or type(span.get("startIndex")) is not int
                or type(span.get("endIndex")) is not int
                or not 1 <= span["startIndex"] < span["endIndex"] < 2**31
            ):
                return False
        return True

    def append(self, request, cancelled):
        connector = self.connector
        connector._write_input(request, "append_document", "write")
        file_id, tab_id = tab_identity(request.destination)
        text = "\n" + request.artifact.content
        if any(
            ord(c) <= 8 or 12 <= ord(c) <= 31 or 0xE000 <= ord(c) <= 0xF8FF
            for c in text
        ):
            raise ValueError(
                "The draft contains characters Google strips; revise it before appending."
            )
        try:
            document, tabs, headers, can_edit = self._fetch(file_id, cancelled)
            if not can_edit:
                raise AccountError("The document is not editable.")
            tab = tabs.get(tab_id)
            if tab is None:
                raise AccountError("The approved document tab no longer exists.")
            marker = self._marker(tab, request.operation_id)
            if marker is not None:
                return (
                    self._outcome(file_id)
                    if marker
                    else ConnectorOutcome(
                        "uncertain",
                        text="An ambiguous operation range exists; inspect the document before another edit.",
                    )
                )
            index = append_index(tab)
            revision = document.get("revisionId")
            if (
                not isinstance(revision, str)
                or not 1 <= len(revision) <= 1024
                or not revision.isprintable()
            ):
                raise ApiError()
        except (AccountError, ApiError):
            return ConnectorOutcome(
                "not_applied",
                text="Google authorization, document identity or revision is unavailable; no append was requested.",
            )
        end = index + len(text.encode("utf-16-le")) // 2
        if end >= 2**31:
            raise ValueError("Document index exceeds Google's supported range")
        body = {
            "writeControl": {"requiredRevisionId": revision},
            "requests": [
                {
                    "insertText": {
                        "text": text,
                        "location": {"index": index, "tabId": tab_id},
                    }
                },
                {
                    "createNamedRange": {
                        "name": "steed_append_" + request.operation_id,
                        "range": {
                            "startIndex": index,
                            "endIndex": end,
                            "tabId": tab_id,
                        },
                    }
                },
            ],
        }
        headers["Content-Type"] = "application/json"
        connector.store.active(connector.account)
        try:
            result = connector.http.request(
                "POST",
                DOCS + "/" + file_id + ":batchUpdate",
                headers=headers,
                body=json.dumps(body).encode("utf-8"),
                cancelled=cancelled,
            )
        except ApiError as error:
            if error.status == 400:
                return ConnectorOutcome(
                    "not_applied",
                    text="Google rejected the atomic append, possibly because the document changed. No automatic retry was made.",
                )
            raise
        replies = result.get("replies")
        if (
            result.get("documentId") != file_id
            or not isinstance(replies, list)
            or len(replies) != 2
            or not isinstance(replies[1], dict)
            or not isinstance(replies[1].get("createNamedRange"), dict)
            or not isinstance(replies[1]["createNamedRange"].get("namedRangeId"), str)
            or not replies[1]["createNamedRange"]["namedRangeId"]
        ):
            raise ApiError()
        return self._outcome(file_id)

    def find_append(self, probe, cancelled):
        file_id, tab_id = tab_identity(probe.destination)
        if not re.fullmatch(r"[a-f0-9]{32}", probe.operation_id):
            raise ValueError("Invalid Google operation identity")
        _, tabs, _, _ = self._fetch(file_id, cancelled)
        tab = tabs.get(tab_id)
        if tab is not None and self._marker(tab, probe.operation_id) is True:
            return self._outcome(
                file_id,
                "The unique operation range confirms the atomic append was applied; collaborators may have since edited its text.",
            )
        return ConnectorOutcome(
            "uncertain",
            text="A unique append operation range could not be confirmed. Inspect the approved tab before another edit.",
        )
