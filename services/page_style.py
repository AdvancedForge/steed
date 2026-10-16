"""Profile-aware presentation only; one atomic write when synchronizing profiles."""

import re
from copy import deepcopy
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

DEFAULTS = {
    "theme": "system",
    "accent": "#355ed6",
    "css": "",
    "menu_bar": True,
    "status_bar": True,
    "newtab_menu": True,
    "loading": True,
    "loading_percent": False,
    "loading_height": 3,
    "loading_color": "#355ed6",
    "colors": {},
}
ROOT_CSS = Path(__file__).resolve().parents[1] / "ui" / "web" / "root.css"
COLOR_FIELDS = {
    "bg": "Page background",
    "surface": "Cards and menus",
    "ink": "Main text",
    "muted": "Secondary text",
    "border": "Borders",
    "soft": "Hover and selection",
    "accent": "Accent",
    "accent-hover": "Accent hover",
    "green": "Success text",
    "green-soft": "Success background",
    "warning": "Warning text",
    "warning-bg": "Warning background",
    "error": "Error text",
    "error-bg": "Error background",
}


def palette(dark=False):
    source = ROOT_CSS.read_text(encoding="utf-8")
    blocks = re.findall(r':root(?:\[data-theme="dark"\])?\s*\{([^}]*)\}', source)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", blocks[int(dark)]))


def style_guide():
    return {
        "colors": {"--" + key: label for key, label in COLOR_FIELDS.items()},
        "selectors": {
            ".settings-shell": "Settings",
            ".workspace": "Agents",
            ".style-shell": "Style editor",
            ".search": "New Tab search",
            ".card": "New Tab cards",
            ".browser-menu": "New Tab menu",
            ".dialog-header": "Settings popover header",
            ".form-group": "Settings form groups",
        },
        "tokens": {
            "--shadow": "Card shadow; e.g. none",
            "--card": "New Tab cards; follows --surface",
            "--hover": "New Tab hover; follows --soft",
        },
        "palette_keys": "In style.colors, omit the leading --. Use other tokens through the css field.",
        "limits": "16,000 CSS characters; no HTML tags, external fonts, network requests or native permissions.",
        "precedence": "Base light/dark palette, accent, colors overrides, then custom CSS. Colors apply to both base themes; empty colors restores the base palette.",
        "example": {
            "colors": {"bg": "#18202b", "surface": "#243044", "ink": "#f1f5fb"},
            "css": ".search { border-radius: 24px; }",
        },
    }


def clean_style(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError("Choose all appearance settings before saving.")
    for key, fallback in DEFAULTS.items():
        if type(value[key]) is not type(fallback):
            raise ValueError("Invalid appearance setting: " + key)
    if value["theme"] not in ("system", "light", "dark"):
        raise ValueError("Choose System, Light or Dark.")
    for key in ("accent", "loading_color"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value[key]):
            raise ValueError("Use a six-digit hex color, such as #355ed6.")
    if set(value["colors"]) - set(COLOR_FIELDS) or any(
        not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
        for color in value["colors"].values()
    ):
        raise ValueError("Use published palette names and six-digit hex colors.")
    if not 1 <= value["loading_height"] <= 10:
        raise ValueError("Loading bar height must be between 1 and 10 pixels.")
    if len(value["css"]) > 16000 or any(c in value["css"] for c in "<\x00"):
        raise ValueError("Use CSS only, up to 16,000 characters, without HTML tags.")
    try:
        value["css"].encode("utf-8")
    except UnicodeError as error:
        raise ValueError("Use valid Unicode text in the stylesheet.") from error
    return {**value, "colors": dict(value["colors"])}


class PageStyle(QObject):
    changed = pyqtSignal()

    def __init__(self, registry):
        super().__init__(registry)
        self.registry = registry
        self.config = registry.config
        self.private = None

    def _document(self):
        raw = self.config.get("page_style", {})
        if (
            not isinstance(raw, dict)
            or type(raw.get("version")) is not int
            or raw["version"] != 1
        ):
            return {"version": 1, "default": deepcopy(DEFAULTS), "profiles": {}}
        return deepcopy(raw)

    def _key(self, profile):
        return self.registry.start_pages.profile_key(profile, create=True)

    def get(self, profile):
        document = self._document()
        value = document.get("default", DEFAULTS)
        overrides = document.get("profiles", {})
        if profile == "search":
            value = self.private if self.private is not None else value
        elif isinstance(overrides, dict):
            value = overrides.get(self._key(profile), value)
        try:
            if isinstance(value, dict) and set(value) == set(DEFAULTS) - {"colors"}:
                value = {**value, "colors": {}}
            return clean_style(value)
        except ValueError:
            return deepcopy(DEFAULTS)

    def save(self, profile, value, *, all_profiles=False, expected=None):
        value = clean_style(value)
        if type(all_profiles) is not bool:
            raise ValueError("Choose an explicit profile scope.")
        if expected is not None and self.get(profile) != expected:
            raise ValueError(
                "Appearance changed in another window. Reload before saving."
            )
        if profile == "search":
            if all_profiles:
                raise ValueError("Switch to a regular profile to change all profiles.")
            self.private = value
        else:
            document = self._document()
            if all_profiles:
                document = {"version": 1, "default": value, "profiles": {}}
            else:
                profiles = document.get("profiles", {})
                profiles = dict(profiles) if isinstance(profiles, dict) else {}
                profiles[self._key(profile)] = value
                document["profiles"] = profiles
            self.config.set("page_style", document)
            if all_profiles:
                self.private = None
        self.changed.emit()

    def dark(self, profile):
        theme = self.get(profile)["theme"]
        return self.config.dark_mode if theme == "system" else theme == "dark"

    def css(self, profile):
        value = self.get(profile)
        accent = (
            ':root, :root[data-theme="dark"] { --accent: '
            + value["accent"]
            + "; --accent-hover: "
            + value["accent"]
            + "; }\n"
        )
        colors = "; ".join(
            "--" + key + ": " + color for key, color in value["colors"].items()
        )
        return (
            (accent if value["accent"] != DEFAULTS["accent"] else "")
            + (':root, :root[data-theme="dark"] {' + colors + "}\n" if colors else "")
            + value["css"]
        )
