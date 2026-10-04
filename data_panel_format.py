"""Native data-panel declarations contain presentation and owned artifact references."""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass

from layout_preferences import clean_name

MAX_PANEL_SOURCE_BYTES = 16384
MAX_WIDGETS = 8
MAX_LIST_ITEMS = 64
MAX_ITEM_CHARS = 512
WIDGET_KINDS = ("task", "text", "list")
PANEL_EXAMPLE = {
    "version": 1,
    "name": "Task dashboard",
    "widgets": [
        {"id": "progress", "kind": "task", "title": "Progress"},
        {
            "id": "summary",
            "kind": "text",
            "title": "Summary",
            "artifact_id": "OWNED_ARTIFACT_ID",
        },
    ],
}


def stable_id(value, limit=64):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1," + str(limit) + r"}", value) is not None
    )


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate panel fields are not allowed")
        result[key] = value
    return result


@dataclass(frozen=True)
class PanelDefinition:
    source: str
    sha256: str
    name: str
    widgets: tuple[dict, ...]

    @property
    def artifact_widgets(self):
        return {w["id"]: w for w in self.widgets if w["kind"] != "task"}


def parse_panel(source):
    if not isinstance(source, str):
        raise ValueError("Provide a UTF-8 JSON panel document")
    try:
        encoded = source.encode("utf-8")
        if not 0 < len(encoded) <= MAX_PANEL_SOURCE_BYTES:
            raise ValueError("Panel source must be at most 16 KiB")
        document = json.loads(source, object_pairs_hook=_object)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("Provide valid bounded UTF-8 JSON") from None
    if not isinstance(document, dict) or set(document) != {
        "version",
        "name",
        "widgets",
    }:
        raise ValueError("Use the published panel fields")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("Unsupported data-panel version")
    name = clean_name(document["name"])
    widgets = document["widgets"]
    if (
        not name
        or not isinstance(widgets, list)
        or not 1 <= len(widgets) <= MAX_WIDGETS
    ):
        raise ValueError("Use a printable name and between one and eight widgets")
    seen = set()
    for widget in widgets:
        if not isinstance(widget, dict) or widget.get("kind") not in WIDGET_KINDS:
            raise ValueError("Use native task, text or list widgets")
        expected = {"id", "kind", "title"}
        if widget["kind"] != "task":
            expected.add("artifact_id")
        if (
            set(widget) != expected
            or not stable_id(widget["id"], 32)
            or widget["id"] in seen
            or not clean_name(widget["title"])
        ):
            raise ValueError("Use distinct widget IDs and the exact published fields")
        if "artifact_id" in widget and not stable_id(widget["artifact_id"]):
            raise ValueError("Bind a widget to one owned artifact ID")
        seen.add(widget["id"])
        widget["title"] = clean_name(widget["title"])
    return PanelDefinition(
        source, hashlib.sha256(encoded).hexdigest(), name, tuple(deepcopy(widgets))
    )


def widget_content(kind, content):
    if kind == "text":
        return content
    if kind != "list":
        raise ValueError("This widget has no artifact content")
    try:
        items = json.loads(content)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError(
            "List artifacts must contain a JSON array of strings"
        ) from None
    if (
        not isinstance(items, list)
        or len(items) > MAX_LIST_ITEMS
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > MAX_ITEM_CHARS
            for item in items
        )
    ):
        raise ValueError("Use at most 64 nonempty list items of at most 512 characters")
    return tuple(items)


def panel_capabilities():
    return {
        "version": 1,
        "widget_kinds": list(WIDGET_KINDS),
        "max_widgets": MAX_WIDGETS,
        "max_source_bytes": MAX_PANEL_SOURCE_BYTES,
        "max_list_items": MAX_LIST_ITEMS,
        "max_item_characters": MAX_ITEM_CHARS,
        "source": "owned_immutable_artifact",
        "profile": "user_selected_normal_profile",
        "approval": "native_preview_and_profile_choice",
        "updates": "same_task_artifact_bindings_with_exact_revision",
        "forms": "future_reviewed_effects",
        "panels_per_profile": 8,
        "proposals_per_task": 20,
        "pending_per_task": 3,
        "pending_global": 32,
        "updates_per_panel": 256,
        "retention": "saved_drafts_survive_task_end_without_update_authority",
        "example": deepcopy(PANEL_EXAMPLE),
    }
