"""Validated tab strip appearance: presentation only, never browser authority."""

from copy import deepcopy
from typing import Any

TAB_POSITIONS = ("top", "bottom", "left", "right")
TAB_STYLES = ("rounded", "pill", "underline", "boxed", "minimal")
TAB_SIZES = ("compact", "standard", "wide")
TAB_CLOSE_BUTTONS = ("hover", "always", "active", "never")
TAB_NEW_BUTTONS = ("after_tabs", "end", "hidden")
TAB_GROUPINGS = ("manual", "site", "none")
TAB_OVERFLOWS = ("shrink", "scroll", "wrap")
TAB_ACCENTS = ("theme", "blue", "purple", "green", "orange", "pink", "slate")
TAB_WIDTH_RANGE = (160, 480)
DEFAULT_TAB_WIDTH = 240

CHOICES = {
    "position": (TAB_POSITIONS, "top"),
    "style": (TAB_STYLES, "rounded"),
    "size": (TAB_SIZES, "standard"),
    "close_button": (TAB_CLOSE_BUTTONS, "hover"),
    "new_tab_button": (TAB_NEW_BUTTONS, "after_tabs"),
    "grouping": (TAB_GROUPINGS, "manual"),
    "overflow": (TAB_OVERFLOWS, "shrink"),
    "accent": (TAB_ACCENTS, "theme"),
}
FLAGS = {"favicons": True, "tab_count": False}

LABELS = {
    "position": "Tab position",
    "style": "Tab style",
    "size": "Tab size",
    "close_button": "Close buttons",
    "new_tab_button": "New tab button",
    "grouping": "Grouping",
    "overflow": "When tabs don't fit",
    "accent": "Accent",
    "favicons": "Site icons",
    "tab_count": "Tab count",
    "vertical_width": "Side tab width",
}


def default_tabs() -> dict[str, Any]:
    return {
        **{key: default for key, (_, default) in CHOICES.items()},
        **FLAGS,
        "vertical_width": DEFAULT_TAB_WIDTH,
    }


def clean_tabs(raw: Any) -> dict[str, Any]:
    """Return a complete tab appearance; unknown or invalid values use defaults."""
    source = raw if isinstance(raw, dict) else {}
    result = default_tabs()
    for key, (choices, _) in CHOICES.items():
        if source.get(key) in choices:
            result[key] = source[key]
    for key in FLAGS:
        if type(source.get(key)) is bool:
            result[key] = source[key]
    width = source.get("vertical_width")
    low, high = TAB_WIDTH_RANGE
    if type(width) is int:
        result["vertical_width"] = max(low, min(high, width))
    return result


def validate_tabs(value: Any) -> dict[str, Any]:
    """Strict check for portable layouts: exact fields and accepted values."""
    if not isinstance(value, dict) or set(value) != set(default_tabs()):
        raise ValueError("Tab appearance fields must match the published format")
    for key, (choices, _) in CHOICES.items():
        if value[key] not in choices:
            raise ValueError(f"Choose a published value for {LABELS[key].lower()}")
    if any(type(value[key]) is not bool for key in FLAGS):
        raise ValueError("Tab icon and count settings must be true or false")
    low, high = TAB_WIDTH_RANGE
    if type(value["vertical_width"]) is not int or not (
        low <= value["vertical_width"] <= high
    ):
        raise ValueError(f"Use a side tab width from {low} to {high} px")
    return deepcopy(value)


def describe_tabs(before: dict, after: dict) -> list[str]:
    return [
        f"{LABELS[key]}: {str(after[key]).replace('_', ' ')}"
        for key in default_tabs()
        if before.get(key) != after.get(key)
    ]


def capabilities() -> dict[str, Any]:
    return {
        **{key: list(choices) for key, (choices, _) in CHOICES.items()},
        "flags": list(FLAGS),
        "vertical_width": list(TAB_WIDTH_RANGE),
        "default": default_tabs(),
    }
