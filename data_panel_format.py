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
MAX_FORM_FIELDS = 8
MAX_FIELD_CHARS = 512
ARTIFACT_KINDS = ("text", "list", "table")
FORM_EXAMPLE = {
    "version": 2,
    "name": "Research preferences",
    "widgets": [
        {
            "id": "preferences",
            "kind": "form",
            "title": "What should I prioritize?",
            "fields": [
                {"id": "focus", "kind": "text", "title": "Focus", "required": True},
                {
                    "id": "depth",
                    "kind": "choice",
                    "title": "Detail level",
                    "required": False,
                    "options": ["Brief", "Detailed"],
                },
            ],
        }
    ],
}
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
        return {w["id"]: w for w in self.widgets if "artifact_id" in w}

    @property
    def workspace_widgets(self):
        return {w["id"]: w for w in self.widgets if "file_id" in w}

    @property
    def source_widgets(self):
        return {
            w["id"]: w for w in self.widgets if "artifact_id" in w or "file_id" in w
        }


def _form_fields(fields):
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FORM_FIELDS:
        raise ValueError("Use between one and eight form fields")
    seen = set()
    for field in fields:
        if not isinstance(field, dict) or field.get("kind") not in ("text", "choice"):
            raise ValueError("Use native text or choice fields")
        expected = {"id", "kind", "title", "required"}
        if field["kind"] == "choice":
            expected.add("options")
        if (
            set(field) != expected
            or not stable_id(field["id"], 32)
            or field["id"] in seen
            or not clean_name(field["title"])
            or type(field["required"]) is not bool
        ):
            raise ValueError("Use distinct field IDs and the exact published fields")
        field["title"] = clean_name(field["title"])
        seen.add(field["id"])
        if field["kind"] == "choice":
            options = field["options"]
            if (
                not isinstance(options, list)
                or not 1 <= len(options) <= 16
                or any(not clean_name(o) or clean_name(o) != o for o in options)
                or len(set(options)) != len(options)
            ):
                raise ValueError("Use one to sixteen distinct printable choices")


class FormInputError(ValueError):
    def __init__(self, field_id, title):
        self.field_id = field_id
        super().__init__(title + ": check the required value, choice and text limit.")


def form_values(widget, values):
    """Validate explicit user input against the exact reviewed form."""
    if not isinstance(values, dict) or set(values) != {
        f["id"] for f in widget["fields"]
    }:
        raise ValueError("Provide exactly the reviewed fields")
    result = {}
    for field in widget["fields"]:
        value = values[field["id"]]
        if (
            not isinstance(value, str)
            or len(value) > MAX_FIELD_CHARS
            or any(not c.isprintable() and c not in "\n\t" for c in value)
            or (field["required"] and not value.strip())
            or (field["kind"] == "choice" and value not in ("", *field["options"]))
        ):
            raise FormInputError(field["id"], field["title"])
        result[field["id"]] = value
    return result


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
    if type(document["version"]) is not int or document["version"] not in (1, 2, 3):
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
        kinds = WIDGET_KINDS + (("form", "table") if document["version"] >= 2 else ())
        if document["version"] == 3:
            kinds += ("image",)
        if not isinstance(widget, dict) or widget.get("kind") not in kinds:
            raise ValueError("Use the published native widget kinds")
        expected = {"id", "kind", "title"}
        if widget["kind"] == "image" or (
            document["version"] == 3
            and widget["kind"] in ARTIFACT_KINDS
            and "file_id" in widget
        ):
            expected.add("file_id")
        elif widget["kind"] in ARTIFACT_KINDS:
            expected.add("artifact_id")
        elif widget["kind"] == "form":
            expected.add("fields")
        if (
            set(widget) != expected
            or not stable_id(widget["id"], 32)
            or widget["id"] in seen
            or not clean_name(widget["title"])
        ):
            raise ValueError("Use distinct widget IDs and the exact published fields")
        if "artifact_id" in widget and not stable_id(widget["artifact_id"]):
            raise ValueError("Bind a widget to one owned artifact ID")
        if "file_id" in widget and not stable_id(widget["file_id"]):
            raise ValueError("Bind a widget to one sealed owned workspace file ID")
        seen.add(widget["id"])
        widget["title"] = clean_name(widget["title"])
        if widget["kind"] == "form":
            _form_fields(widget["fields"])
    return PanelDefinition(
        source, hashlib.sha256(encoded).hexdigest(), name, tuple(deepcopy(widgets))
    )


def widget_content(kind, content):
    if kind == "text":
        return content
    if kind not in ("list", "table"):
        raise ValueError("This widget has no artifact content")
    try:
        items = json.loads(content, object_pairs_hook=_object)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError(
            "List artifacts must contain a JSON array of strings"
        ) from None
    if kind == "table":
        if (
            not isinstance(items, dict)
            or set(items) != {"columns", "rows"}
            or not isinstance(items["columns"], list)
            or not 1 <= len(items["columns"]) <= 8
            or any(not clean_name(c) or clean_name(c) != c for c in items["columns"])
            or len(set(items["columns"])) != len(items["columns"])
            or not isinstance(items["rows"], list)
            or len(items["rows"]) > 64
            or any(
                not isinstance(row, list)
                or len(row) != len(items["columns"])
                or any(not isinstance(c, str) or len(c) > MAX_ITEM_CHARS for c in row)
                for row in items["rows"]
            )
        ):
            raise ValueError("Use up to eight unique columns and 64 bounded text rows")
        return {
            "columns": tuple(items["columns"]),
            "rows": tuple(tuple(r) for r in items["rows"]),
        }
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
        "version": 3,
        "supported_versions": [1, 2, 3],
        "widget_kinds": list(WIDGET_KINDS) + ["form", "table", "image"],
        "max_widgets": MAX_WIDGETS,
        "max_source_bytes": MAX_PANEL_SOURCE_BYTES,
        "max_list_items": MAX_LIST_ITEMS,
        "max_item_characters": MAX_ITEM_CHARS,
        "source": "owned_immutable_artifact_or_sealed_workspace_file",
        "profile": "user_selected_normal_profile",
        "approval": "native_preview_and_profile_choice",
        "updates": "same_task_owned_source_bindings_with_exact_revision",
        "source_fields": {
            "artifact_id": "text/list/table; artifact authority",
            "file_id": "version 3 text/list/table/image; sealed owned workspace file and workspace authority",
        },
        "max_workspace_text_bytes": 16384,
        "max_image_file_bytes": 8 * 1024 * 1024,
        "max_image_pixels": 4 * 1024 * 1024,
        "image_types": ["image/png", "image/jpeg"],
        "forms": "explicit_user_message_to_owning_connected_task_with_payload_preview",
        "max_form_fields": MAX_FORM_FIELDS,
        "max_field_characters": MAX_FIELD_CHARS,
        "max_choice_options": 16,
        "max_table_columns": 8,
        "max_table_rows": 64,
        "panels_per_profile": 8,
        "proposals_per_task": 20,
        "pending_per_task": 3,
        "pending_global": 32,
        "updates_per_panel": 256,
        "retention": "saved_drafts_survive_task_end_without_update_authority",
        "example": deepcopy(PANEL_EXAMPLE),
        "form_example": deepcopy(FORM_EXAMPLE),
        "image_example": {
            "version": 3,
            "name": "Generated chart",
            "widgets": [
                {
                    "id": "chart",
                    "kind": "image",
                    "title": "Chart",
                    "file_id": "SEALED_OWNED_WORKSPACE_FILE_ID",
                }
            ],
        },
    }
