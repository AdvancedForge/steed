"""Explicit bounded JSON projections for manually refreshed native account views."""

import json
import math

from browser.data_panel_format import MAX_ITEM_CHARS, MAX_LIST_ITEMS
from browser.layout_preferences import clean_name
from services.connector_recipes import RecipeError, pointer, select


def mapping_pointer(value):
    if value == "":
        return value
    pointer(value)
    if "*" in value.split("/"):
        raise ValueError("Use exact JSON fields, not wildcards")
    return value


def validate_mapping(kind, value):
    expected = {"items", "label" if kind == "list" else "columns"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Use the exact native account projection fields")
    mapping_pointer(value["items"])
    if kind == "list":
        mapping_pointer(value["label"])
    else:
        columns = value["columns"]
        if not isinstance(columns, list) or not 1 <= len(columns) <= 8:
            raise ValueError("Choose between one and eight table columns")
        seen = set()
        for column in columns:
            if not isinstance(column, dict) or set(column) != {"title", "value"}:
                raise ValueError("Use exact column title/value fields")
            title = clean_name(column["title"])
            if not title or title != column["title"] or title in seen:
                raise ValueError("Use distinct printable table column names")
            seen.add(title)
            mapping_pointer(column["value"])


def empty_content(widget):
    if widget["kind"] == "text":
        return ""
    if widget["kind"] == "list":
        return ()
    return {
        "columns": tuple(c["title"] for c in widget["mapping"]["columns"]),
        "rows": (),
    }


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous JSON result")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("Invalid JSON number")


def _field(data, path):
    return data if path == "" else select(data, path)


def project_result(widget, text):
    if widget["kind"] == "text":
        return {"content": text}
    try:
        data = json.loads(
            text, object_pairs_hook=_unique, parse_constant=_invalid_constant
        )
        mapping = widget["mapping"]
        items = _field(data, mapping["items"])
        if not isinstance(items, list):
            raise ValueError("Projection needs a JSON array")
        clipped = 0

        def cell(item, path):
            nonlocal clipped
            value = _field(item, path)
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise ValueError("Projection accepts scalar fields only")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Projection accepts finite numbers only")
            value = (
                value if isinstance(value, str) else json.dumps(value, allow_nan=False)
            )
            if len(value) > MAX_ITEM_CHARS:
                clipped += 1
                return value[: MAX_ITEM_CHARS - 1] + "…"
            return value

        visible = items[:MAX_LIST_ITEMS]
        if widget["kind"] == "list":
            content = tuple(cell(item, mapping["label"]) for item in visible)
        else:
            content = {
                "columns": tuple(c["title"] for c in mapping["columns"]),
                "rows": tuple(
                    tuple(cell(item, c["value"]) for c in mapping["columns"])
                    for item in visible
                ),
            }
        return {
            "content": content,
            "source_count": len(items),
            "shown_count": len(visible),
            "clipped_cells": clipped,
        }
    except (ValueError, KeyError, IndexError, TypeError, RecursionError, RecipeError):
        raise ValueError(
            "Account result does not match the reviewed projection"
        ) from None
