"""Maintained component IDs and bounded settings for native quick-action shelves."""

import re

PANEL_ACTIONS = {
    "new_tab": "New tab",
    "reload": "Reload",
    "home": "Home",
    "address": "Address/search",
    "find": "Find in page",
    "zoom_in": "Zoom in",
    "zoom_out": "Zoom out",
    "zoom_reset": "Reset zoom",
    "agents": "Agent sidebar",
    "downloads": "Downloads",
    "passwords": "Passwords",
    "layout_settings": "Layout settings",
}
TEXT_SIZES = ("normal", "large")
PANEL_PLACEMENTS = ("top", "bottom")
PANEL_EMPHASES = ("neutral", "accent")
MAX_PANELS = 2
MAX_PANEL_ACTIONS = 8
DEFAULT_PANEL_ACTIONS = ["new_tab", "reload", "find", "agents"]


def panel_id(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value) is not None
    )


def clean_panels(raw):
    """Recover known native components from untrusted stored preferences."""
    from browser.layout_preferences import clean_name

    if not isinstance(raw, list):
        return []
    result = []
    seen = set()
    for entry in raw[:MAX_PANELS]:
        if (
            not isinstance(entry, dict)
            or entry.get("kind") != "quick_actions"
            or not panel_id(entry.get("id"))
            or entry["id"] in seen
        ):
            continue
        title = clean_name(entry.get("title"))
        if not title:
            continue
        actions = []
        if isinstance(entry.get("actions"), list):
            for name in entry["actions"]:
                if (
                    isinstance(name, str)
                    and name in PANEL_ACTIONS
                    and name not in actions
                ):
                    actions.append(name)
        if not actions:
            continue
        seen.add(entry["id"])
        result.append(
            {
                "kind": "quick_actions",
                "id": entry["id"],
                "title": title,
                "placement": entry.get("placement")
                if entry.get("placement") in PANEL_PLACEMENTS
                else "top",
                "emphasis": entry.get("emphasis")
                if entry.get("emphasis") in PANEL_EMPHASES
                else "neutral",
                "actions": actions[:MAX_PANEL_ACTIONS],
            }
        )
    return result
