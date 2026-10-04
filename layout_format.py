"""Strict portable layout definitions: data, never executable extensions."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass

from layout_preferences import (
    DENSITIES,
    MENU_ITEMS,
    SIDEBAR_SIDES,
    SIDEBAR_WIDTH_RANGE,
    TOOLBAR_ITEMS,
    TOOLBAR_ORDER,
    LayoutPreferences,
    clean_name,
    default_appearance,
)

MAX_LAYOUT_BYTES = 16384
LAYOUT_EXAMPLE = {"version": 1, "name": "Focus", "layout": default_appearance()}
LABELS = {
    "back": "Back",
    "forward": "Forward",
    "reload": "Reload",
    "home": "Home",
    "address": "Address/search",
    "zoom": "Reset zoom",
    "passwords": "Passwords",
    **{key: key.title() for key in MENU_ITEMS},
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate layout keys are not allowed")
        result[key] = value
    return result


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("Layout fields must match the published format exactly")


def _ids(value, known, *, complete=False):
    if (
        not isinstance(value, list)
        or len(value) > len(known)
        or any(not isinstance(v, str) or v not in known for v in value)
        or len(set(value)) != len(value)
        or (complete and set(value) != set(known))
    ):
        raise ValueError(
            "Use distinct published component IDs and keep mandatory controls"
        )


@dataclass(frozen=True)
class LayoutDefinition:
    source: str
    sha256: str
    name: str
    appearance: dict

    def preferences(self, current):
        """Replace appearance only, retaining the user's saved presets."""
        document = current.to_dict()
        document["layout"] = deepcopy(self.appearance)
        document["active"] = None
        return LayoutPreferences(document)


def parse_layout(source):
    if not isinstance(source, str):
        raise ValueError("Provide a UTF-8 JSON layout document")
    try:
        encoded = source.encode("utf-8")
        if not 0 < len(encoded) <= MAX_LAYOUT_BYTES:
            raise ValueError("Layout source must be at most 16 KiB")
        document = json.loads(source, object_pairs_hook=_object)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("Provide valid bounded UTF-8 JSON") from None
    _fields(document, ("version", "name", "layout"))
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("Unsupported portable layout version")
    name = clean_name(document["name"])
    if not name:
        raise ValueError("Use a printable layout name of at most 48 characters")
    appearance = document["layout"]
    _fields(appearance, ("toolbar", "menus", "sidebar", "density"))
    _fields(appearance["toolbar"], ("order", "hidden"))
    _ids(appearance["toolbar"]["order"], TOOLBAR_ORDER, complete=True)
    _ids(appearance["toolbar"]["hidden"], TOOLBAR_ITEMS)
    _fields(appearance["menus"], ("order",))
    _ids(appearance["menus"]["order"], MENU_ITEMS, complete=True)
    _fields(appearance["sidebar"], ("side", "width"))
    sidebar = appearance["sidebar"]
    low, high = SIDEBAR_WIDTH_RANGE
    if (
        sidebar["side"] not in SIDEBAR_SIDES
        or type(sidebar["width"]) is not int
        or not low <= sidebar["width"] <= high
    ):
        raise ValueError("Use a left/right sidebar and a width from 300 to 800 px")
    if appearance["density"] not in DENSITIES:
        raise ValueError("Choose compact or comfortable spacing")
    return LayoutDefinition(
        source, hashlib.sha256(encoded).hexdigest(), name, appearance
    )


def export_layout(preferences, name="Current layout"):
    source = json.dumps(
        {"version": 1, "name": name, "layout": preferences.to_dict()["layout"]},
        indent=2,
    )
    parse_layout(source)
    return source


def layout_changes(before, after):
    """Describe the actual change to native browser controls, as plain text."""
    old, new = before.to_dict()["layout"], after.to_dict()["layout"]
    changes = []
    for key, label in (("toolbar", "Address bar order"), ("menus", "Menu order")):
        if old[key]["order"] != new[key]["order"]:
            changes.append(
                label + ": " + " → ".join(LABELS[n] for n in new[key]["order"])
            )
    if old["toolbar"]["hidden"] != new["toolbar"]["hidden"]:
        hidden = new["toolbar"]["hidden"]
        changes.append(
            "Hidden buttons: " + (", ".join(LABELS[n] for n in hidden) or "None")
        )
    if old["sidebar"] != new["sidebar"]:
        changes.append(f"Agent sidebar: {after.sidebar_side}, {after.sidebar_width} px")
    if old["density"] != new["density"]:
        changes.append("Control spacing: " + after.density)
    return changes or ["No presentation changes."]


def layout_capabilities():
    return {
        "version": 1,
        "max_source_bytes": MAX_LAYOUT_BYTES,
        "toolbar_ids": list(TOOLBAR_ORDER),
        "hideable_ids": list(TOOLBAR_ITEMS),
        "menu_ids": list(MENU_ITEMS),
        "sidebar_width": list(SIDEBAR_WIDTH_RANGE),
        "densities": list(DENSITIES),
        "approval": "exact_layout_user_review",
        "proposals_per_task": 20,
        "pending_per_task": 3,
        "pending_global": 32,
        "example": deepcopy(LAYOUT_EXAMPLE),
    }
