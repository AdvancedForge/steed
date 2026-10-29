"""Strict portable layout definitions: data, never executable extensions."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass

from layout_components import (
    MAX_PANEL_ACTIONS,
    MAX_PANELS,
    PANEL_ACTIONS,
    PANEL_EMPHASES,
    PANEL_PLACEMENTS,
    TEXT_SIZES,
    panel_id,
)
from layout_preferences import (
    DENSITIES,
    MENU_ITEMS,
    SIDEBAR_SIDES,
    SIDEBAR_WIDTH_RANGE,
    TOOLBAR_ITEMS,
    TOOLBAR_ORDER,
    LayoutPreferences,
    clean_appearance,
    clean_name,
    default_appearance,
)
from tab_appearance import capabilities as tab_capabilities
from tab_appearance import describe_tabs, validate_tabs

MAX_LAYOUT_BYTES = 16384
LAYOUT_EXAMPLE = {"version": 3, "name": "Focus", "layout": default_appearance()}
PANEL_EXAMPLE = {
    "kind": "quick_actions",
    "id": "daily-tools",
    "title": "Daily tools",
    "placement": "top",
    "emphasis": "accent",
    "actions": ["new_tab", "reload", "find", "agents"],
}
LABELS = {
    "back": "Back",
    "forward": "Forward",
    "reload": "Reload",
    "home": "Home",
    "address": "Address/search",
    "zoom": "Reset zoom",
    "passwords": "Passwords",
    "menu": "Browser menu",
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
    if type(document["version"]) is not int or document["version"] not in (1, 2, 3):
        raise ValueError("Unsupported portable layout version")
    name = clean_name(document["name"])
    if not name:
        raise ValueError("Use a printable layout name of at most 48 characters")
    appearance = document["layout"]
    fields = ["toolbar", "menus", "sidebar", "density"]
    if document["version"] >= 2:
        fields.extend(["text_size", "panels"])
    if document["version"] == 3:
        fields.append("tabs")
    _fields(appearance, fields)
    _fields(appearance["toolbar"], ("order", "hidden"))
    # Older portable layouts predate the movable, mandatory browser-menu button.
    toolbar_order = appearance["toolbar"]["order"]
    known = TOOLBAR_ORDER
    if isinstance(toolbar_order, list) and "menu" not in toolbar_order:
        known = tuple(name for name in known if name != "menu")
    _ids(toolbar_order, known, complete=True)
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
    if document["version"] == 3:
        validate_tabs(appearance["tabs"])
    if document["version"] >= 2:
        if appearance["text_size"] not in TEXT_SIZES:
            raise ValueError("Choose normal or large browser text")
        panels = appearance["panels"]
        if not isinstance(panels, list) or len(panels) > MAX_PANELS:
            raise ValueError("Use at most two native action panels")
        seen = set()
        for panel in panels:
            _fields(panel, ("kind", "id", "title", "placement", "emphasis", "actions"))
            if (
                panel["kind"] != "quick_actions"
                or not panel_id(panel["id"])
                or panel["id"] in seen
                or not clean_name(panel["title"])
                or panel["placement"] not in PANEL_PLACEMENTS
                or panel["emphasis"] not in PANEL_EMPHASES
            ):
                raise ValueError(
                    "Use the published native quick-action component format"
                )
            _ids(panel["actions"], tuple(PANEL_ACTIONS))
            if not 1 <= len(panel["actions"]) <= MAX_PANEL_ACTIONS:
                raise ValueError("Use between one and eight actions per panel")
            seen.add(panel["id"])
    return LayoutDefinition(
        source, hashlib.sha256(encoded).hexdigest(), name, clean_appearance(appearance)
    )


def export_layout(preferences, name="Current layout"):
    source = json.dumps(
        {"version": 3, "name": name, "layout": preferences.to_dict()["layout"]},
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
    if old["text_size"] != new["text_size"]:
        changes.append("Browser text: " + after.text_size)
    if old["panels"] != new["panels"]:
        for panel in new["panels"]:
            changes.append(
                f"Panel {panel['title']} ({panel['placement']}, {panel['emphasis']}): "
                + ", ".join(PANEL_ACTIONS[n] for n in panel["actions"])
            )
        removed = [
            p["title"]
            for p in old["panels"]
            if p["id"] not in {n["id"] for n in new["panels"]}
        ]
        if removed:
            changes.append("Remove panels: " + ", ".join(removed))
    changes.extend(describe_tabs(old["tabs"], new["tabs"]))
    return changes or ["No presentation changes."]


def layout_capabilities():
    return {
        "version": 3,
        "supported_versions": [1, 2, 3],
        "tabs": tab_capabilities(),
        "text_sizes": list(TEXT_SIZES),
        "panel_actions": dict(PANEL_ACTIONS),
        "panel_example": deepcopy(PANEL_EXAMPLE),
        "panel_limit": MAX_PANELS,
        "panel_action_limit": MAX_PANEL_ACTIONS,
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
