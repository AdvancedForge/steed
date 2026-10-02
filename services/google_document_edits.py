"""Whole plain-text tab replacement, reviewed by default and revision-bound."""

import hashlib
import json

from services.app_accounts import AccountError
from services.app_oauth import ApiError
from services.connectors import ConnectorOutcome, ConnectorReview, ConnectorTool
from services.google_documents import DOCS, body_content, tab_identity


def editable_text(tab):
    """Account for every UTF-16 unit; never silently drop rich/unsupported content."""
    pieces, index = [], 1
    for item in body_content(tab):
        if not isinstance(item, dict):
            raise ApiError()
        if "sectionBreak" in item and item.get("endIndex") == 1 and not pieces:
            continue
        paragraph = item.get("paragraph")
        if not isinstance(paragraph, dict) or item.get("startIndex") != index:
            raise AccountError(
                "Replacement supports complete plain-text tab bodies only."
            )
        elements = paragraph.get("elements")
        if (
            not isinstance(elements, list)
            or not elements
            or paragraph.get("suggestedParagraphStyleChanges")
        ):
            raise AccountError(
                "Review suggested edits in Google Docs before replacing text."
            )
        for element in elements:
            if not isinstance(element, dict):
                raise ApiError()
            run = element.get("textRun")
            if (
                not isinstance(run, dict)
                or element.get("startIndex") != index
                or element.get("suggestedInsertionIds")
                or element.get("suggestedDeletionIds")
                or any(
                    key not in {"startIndex", "endIndex", "textRun"} for key in element
                )
            ):
                raise AccountError(
                    "Replacement does not support non-text or suggested content."
                )
            text = run.get("content")
            if not isinstance(text, str) or "\x00" in text:
                raise ApiError()
            index += len(text.encode("utf-16-le")) // 2
            if type(element.get("endIndex")) is not int or element["endIndex"] != index:
                raise ApiError()
            pieces.append(text)
        if item.get("endIndex") != index:
            raise ApiError()
    text = "".join(pieces)
    if not text.endswith("\n") or len(text.encode("utf-8")) > 16384:
        raise AccountError("Replacement requires a complete tab of at most 16 KiB.")
    return text[:-1], index - 1


class GoogleDocumentEdits:
    def __init__(self, documents):
        self.documents = documents

    def tools(self):
        account = self.documents.connector.account
        return [
            ConnectorTool(
                account.connector,
                "replace_document",
                "write",
                f"Google Docs ({account.label}): propose replacing one exact tab's plain-text body; each before/after change needs user review even with saved task permissions",
                True,
                self.apply_review,
                self.find_edit,
                tab_identity,
                self.prepare,
            ),
            ConnectorTool(
                account.connector,
                "replace_document_direct",
                "write",
                f"Google Docs ({account.label}): DIRECTLY replace one exact tab's plain-text body without per-change review; separate reusable permission, formatting may change",
                True,
                self.apply_direct,
                self.find_edit,
                tab_identity,
            ),
        ]

    def _snapshot(self, request, cancelled, tool):
        connector = self.documents.connector
        connector._write_input(request, tool, "write")
        file_id, tab_id = tab_identity(request.destination)
        after = request.artifact.content
        if any(
            ord(c) <= 8 or 12 <= ord(c) <= 31 or 0xE000 <= ord(c) <= 0xF8FF
            for c in after
        ):
            raise ValueError(
                "Revise characters Google strips before proposing a replacement."
            )
        document, tabs, headers, can_edit = self.documents._fetch(file_id, cancelled)
        if not can_edit or tab_id not in tabs:
            raise AccountError("The approved tab is no longer editable.")
        before, end = editable_text(tabs[tab_id])
        revision = document.get("revisionId")
        if (
            not isinstance(revision, str)
            or not 1 <= len(revision) <= 1024
            or not revision.isprintable()
        ):
            raise ApiError()
        context = json.dumps(
            {
                "revision": revision,
                "end": end,
                "before_sha": hashlib.sha256(before.encode("utf-8")).hexdigest(),
            }
        )
        return (
            ConnectorReview(before, after, context).validate(),
            file_id,
            tab_id,
            headers,
            tabs[tab_id],
        )

    def prepare(self, request, cancelled):
        return self._snapshot(request, cancelled, "replace_document")[0]

    def apply_review(self, request, cancelled):
        if request.review is None:
            raise ValueError("This tool requires trusted user review")
        request.review.validate()
        return self._apply(request, cancelled, "replace_document", request.review)

    def apply_direct(self, request, cancelled):
        return self._apply(request, cancelled, "replace_document_direct", None)

    def _apply(self, request, cancelled, tool, reviewed):
        try:
            current, file_id, tab_id, headers, tab = self._snapshot(
                request, cancelled, tool
            )
        except (AccountError, ApiError):
            return ConnectorOutcome(
                "not_applied",
                text="The tab could not be verified; no replacement was requested.",
            )
        if reviewed is not None and (
            reviewed.after != current.after
            or reviewed.before != current.before
            or reviewed.context != current.context
        ):
            return ConnectorOutcome(
                "not_applied",
                text="The document changed since the preview. Create a new proposal; this change was not sent.",
            )
        marker = self.documents._marker(
            tab, request.operation_id, prefix="steed_replace_"
        )
        if marker is not None:
            return (
                self.documents._outcome(file_id)
                if marker
                else ConnectorOutcome(
                    "uncertain",
                    text="The replacement operation range is ambiguous; inspect the tab.",
                )
            )
        context = json.loads(current.context)
        requests = []
        if context["end"] > 1:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": 1,
                            "endIndex": context["end"],
                            "tabId": tab_id,
                        }
                    }
                }
            )
        requests.extend(
            [
                {
                    "insertText": {
                        "text": current.after,
                        "location": {"index": 1, "tabId": tab_id},
                    }
                },
                {
                    "createNamedRange": {
                        "name": "steed_replace_" + request.operation_id,
                        "range": {
                            "startIndex": 1,
                            "endIndex": 1 + len(current.after.encode("utf-16-le")) // 2,
                            "tabId": tab_id,
                        },
                    }
                },
            ]
        )
        headers["Content-Type"] = "application/json"
        connector = self.documents.connector
        connector.store.active(connector.account)
        try:
            result = connector.http.request(
                "POST",
                DOCS + "/" + file_id + ":batchUpdate",
                headers=headers,
                body=json.dumps(
                    {
                        "writeControl": {"requiredRevisionId": context["revision"]},
                        "requests": requests,
                    }
                ).encode("utf-8"),
                cancelled=cancelled,
            )
        except ApiError as error:
            if error.status == 400:
                return ConnectorOutcome(
                    "not_applied",
                    text="Google rejected the atomic replacement, possibly because the revision changed. No retry was made.",
                )
            raise
        replies = result.get("replies")
        if (
            result.get("documentId") != file_id
            or not isinstance(replies, list)
            or len(replies) != len(requests)
            or not isinstance(replies[-1], dict)
            or not isinstance(replies[-1].get("createNamedRange"), dict)
            or not replies[-1]["createNamedRange"].get("namedRangeId")
        ):
            raise ApiError()
        return self.documents._outcome(file_id)

    def find_edit(self, probe, cancelled):
        return self.documents.find_append(probe, cancelled, prefix="steed_replace_")
