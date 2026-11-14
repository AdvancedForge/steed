"""Profile-aware presentation only; one atomic write when synchronizing profiles."""

import re
from copy import deepcopy
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

DEFAULTS = {
    "theme": "system",
    "accent": "#355ed6",
    "css": "",
    # Hidden unless switched on (View, or Ctrl+Shift+M for the menu bar); the
    # toolbar's menu button and an overlay for messages stand in for them.
    "menu_bar": False,
    "status_bar": False,
    "newtab_menu": True,
    # Liquid glass: translucent, glossy chrome and new tab controls.
    "glass": False,
    "loading": True,
    "loading_percent": False,
    "loading_height": 3,
    "loading_color": "#355ed6",
    "colors": {},
    # Browser colours fixed by the user; the rest follow the theme or photo.
    "chrome": {},
    # Steed's own pages take the browser's colours: the New Tab photo's, or
    # the browser colours fixed in Style.
    "pages_match_browser": True,
    # Compact mode: tabs and toolbar float over the page, shown at the top edge.
    "compact": False,
    # Lumen, Steed's look (ui/lumen.py): see LUMEN_FIELDS.
    "lumen": {},
}
# Added after styles were first saved; older saves lack them.
LATER_SETTINGS = {
    "colors",
    "glass",
    "chrome",
    "pages_match_browser",
    "compact",
    "lumen",
}
# Settings that were replaced: dropped from older saves, never an error.
# (pages_follow_photo became pages_match_browser, on for everyone.)
RETIRED_SETTINGS = {"pages_follow_photo"}
# The dark browser's own colours (browser_window.apply_dark_mode), for pages
# that match it when there is no photo.
DARK_TOOLBAR = "#1e1e1e"
DARK_ACCENT = "#8caaff"
# Every part of Lumen that is drawn natively (the light around the page, the
# page card, the typeface); what is not set takes these. Everything drawn in
# HTML (tabs, menus, Steed's pages) is styled through "css" as well.
LUMEN_DEFAULTS = {
    "light": 100,
    "follow_site": True,
    "light_colors": {},
    "card_radius": 12,
    "card_margin": 6,
    "card_shadow": 100,
    "font": "mona",
    "titles": "expanded",
}
LUMEN_FIELDS = {
    "light": "Strength of the light around the page, 0 (flat) to 200 percent",
    "follow_site": "The light takes the current site's colour",
    "light_colors": "Fixed light colours (hex): base, left, right, below",
    "card_radius": "Rounding of the page's corners, 0 to 28 pixels",
    "card_margin": "Space around the page, 0 to 24 pixels (0: edge to edge)",
    "card_shadow": "Strength of the page's shadow, 0 to 200 percent",
    "font": "mona (Steed's Mona Sans) or system (the desktop's font)",
    "titles": "Title width: expanded, semi (semi-expanded) or normal",
}
LUMEN_RANGES = {
    "light": (0, 200),
    "card_radius": (0, 28),
    "card_margin": (0, 24),
    "card_shadow": (0, 200),
}
LUMEN_CHOICES = {"font": ("mona", "system"), "titles": ("expanded", "semi", "normal")}
LIGHT_COLORS = ("base", "left", "right", "below")


def lumen_settings(value):
    """A style's Lumen settings, every field present and checked."""
    if not isinstance(value, dict) or set(value) - set(LUMEN_DEFAULTS):
        raise ValueError("Use the published Lumen settings: " + ", ".join(LUMEN_FIELDS))
    merged = {**LUMEN_DEFAULTS, **value}
    for key, (low, high) in LUMEN_RANGES.items():
        if type(merged[key]) is not int or not low <= merged[key] <= high:
            raise ValueError(
                f"Lumen {key} must be a whole number from {low} to {high}."
            )
    for key, choices in LUMEN_CHOICES.items():
        if merged[key] not in choices:
            raise ValueError(f"Lumen {key} must be one of: " + ", ".join(choices))
    if type(merged["follow_site"]) is not bool:
        raise ValueError("Lumen follow_site must be true or false.")
    colors = merged["light_colors"]
    if (
        not isinstance(colors, dict)
        or set(colors) - set(LIGHT_COLORS)
        or any(
            not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
            for color in colors.values()
        )
    ):
        raise ValueError(
            "Lumen light_colors takes six-digit hex colours for: "
            + ", ".join(LIGHT_COLORS)
        )
    merged["light_colors"] = {key: color.lower() for key, color in colors.items()}
    return merged


# Every colour of the browser around pages (see ui.chrome_style.chrome_colors).
CHROME_FIELDS = {
    "toolbar": "Toolbar and current tab",
    "strip": "Tab strip background",
    "ink": "Browser text and icons",
    "muted": "Other tabs' text",
    "field": "Address field",
    "hover": "Hover",
    "pressed": "Pressed",
    "border": "Lines and borders",
    "accent": "Accent: focus, selection, badges",
    "menu": "Menus",
    "card": "Tab hover card",
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
            ".workspace-page": "Management pages",
            "#workspace-nav": "Management page navigation",
            "#workspace-picker": "Compact management page navigation",
            ".compact-page": "HTML sidebar controls",
            ".table-wrap": "Scrollable results tables",
            ".table-paging": "Previous/next result controls",
            'body[data-page="cookies"]': "Example: style only the Cookies page",
            "html.lumen body": "Tab strip: the light behind the tabs (its own HTML)",
            ".tab": "A tab; .tab.active the current one, .tab.entering a new one",
            "#lit": "The lit capsule that glides behind the current tab",
            ".favicon.glyph": "A tab's drawn icon (.steed: Steed's pages, .site: no icon)",
            "html.glass body, nav button": "Browser menus (rows are nav button)",
            ".wordmark": "The New Tab's name",
            ".hero h1": "Welcome's headline",
        },
        "tokens": {
            "--shadow": "Card shadow; e.g. none",
            "--card": "New Tab cards; follows --surface",
            "--hover": "New Tab hover; follows --soft",
            "--steed-font": "Text typeface (Style → lumen.font sets it)",
            "--steed-title-font": "Titles (lumen.titles)",
            "--steed-display-font": "The largest titles and the New Tab's name",
            "--stride": "Steed's easing for movement",
            "--spring": "Steed's easing for switches and presses",
            "--lift": "The lit top edge of cards",
        },
        "lumen": dict(LUMEN_FIELDS),
        "lumen_defaults": dict(LUMEN_DEFAULTS),
        "lumen_note": (
            "lumen covers what Steed draws natively: the light around the page,"
            " the page card and the typeface. Patch only the keys you change,"
            ' e.g. {"lumen": {"light": 150, "card_radius": 18}}. Motion'
            " (Full, Balanced, Minimal) is the person's own setting in Settings."
        ),
        "palette_keys": "In style.colors, omit the leading --. Use other tokens through the css field.",
        "limits": "16,000 CSS characters; no HTML tags, external fonts, network requests or native permissions.",
        "precedence": "Base light/dark palette, accent, colors overrides, then custom CSS. Colors apply to both base themes; empty colors restores the base palette.",
        "example": {
            "colors": {"bg": "#18202b", "surface": "#243044", "ink": "#f1f5fb"},
            "css": ".search { border-radius: 24px; }",
        },
    }


FONTS = {
    "mona": '"Mona Sans", system-ui, sans-serif',
    "system": "system-ui, sans-serif",
}
TITLE_FONTS = {
    "expanded": ('"Mona Sans SemiExpanded"', '"Mona Sans Expanded"'),
    "semi": ('"Mona Sans SemiExpanded"', '"Mona Sans SemiExpanded"'),
    "normal": ('"Mona Sans"', '"Mona Sans"'),
}


def lumen_css(value):
    """The typeface as tokens every Steed page and HTML part uses
    (--steed-font, --steed-title-font, --steed-display-font, --steed-radius)."""
    lumen = lumen_settings(value.get("lumen", {}))
    if lumen["font"] == "system":
        body = title = display = FONTS["system"]
    else:
        body = FONTS["mona"]
        title, display = (f"{name}, {body}" for name in TITLE_FONTS[lumen["titles"]])
    return (
        f":root {{ --steed-font: {body}; --steed-title-font: {title};"
        f" --steed-display-font: {display}; --steed-radius: {lumen['card_radius']}px; }}\n"
    )


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
    if not isinstance(value["chrome"], dict) or (
        set(value["chrome"]) - set(CHROME_FIELDS)
        or any(
            not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
            for color in value["chrome"].values()
        )
    ):
        raise ValueError("Use browser colour names and six-digit hex colors.")
    if not 1 <= value["loading_height"] <= 10:
        raise ValueError("Loading bar height must be between 1 and 10 pixels.")
    if len(value["css"]) > 16000 or any(c in value["css"] for c in "<\x00"):
        raise ValueError("Use CSS only, up to 16,000 characters, without HTML tags.")
    try:
        value["css"].encode("utf-8")
    except UnicodeError as error:
        raise ValueError("Use valid Unicode text in the stylesheet.") from error
    # Only what differs from Lumen's defaults is kept.
    lumen = lumen_settings(value["lumen"])
    return {
        **value,
        "lumen": {k: v for k, v in lumen.items() if v != LUMEN_DEFAULTS[k]},
        "colors": dict(value["colors"]),
        "chrome": {key: color.lower() for key, color in value["chrome"].items()},
    }


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
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k not in RETIRED_SETTINGS}
        try:
            if (
                isinstance(value, dict)
                and set(value) <= set(DEFAULTS)
                and (set(DEFAULTS) - set(value) <= LATER_SETTINGS)
            ):  # Saved before these settings existed: their defaults.
                value = {**deepcopy(DEFAULTS), **value}
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
            + lumen_css(value)
            + self._photo_css(profile, value)
            + (':root, :root[data-theme="dark"] {' + colors + "}\n" if colors else "")
            + value["css"]
        )

    def _photo_css(self, profile, value):
        """The browser's colours for Steed's pages (the New Tab photo's, or
        the toolbar and accent fixed in Style), unless turned off; page
        colours chosen by hand still win (they come after)."""
        pages = getattr(self.registry, "start_pages", None)
        if not value["pages_match_browser"] or not hasattr(pages, "theme_tint"):
            return ""
        from PyQt6 import sip

        if sip.isdeleted(pages):  # Shutting down: pages render no more.
            return ""
        from ui.chrome_style import mix, tint_colors

        dark = self.dark(profile)
        photo = pages.theme_tint(profile)
        tint = photo or value["chrome"].get("toolbar")
        if not tint:
            if not dark:  # The light pages already match the light browser.
                accent = value["chrome"].get("accent")
                return (
                    f':root, :root[data-theme="dark"] {{ --accent: {accent}; }}\n'
                    if accent
                    else ""
                )
            tint = DARK_TOOLBAR  # The dark browser's own neutral.
        accent = value["chrome"].get("accent") or (
            tint_colors(tint, dark)[1] if photo else DARK_ACCENT if dark else None
        )
        accent = accent or tint_colors(tint, dark)[1]
        if dark:
            ink, base = "#e8edf6", "#101010"
            shades = {"bg": 0.84, "surface": 0.74, "soft": 0.6, "border": 0.55}
            hover = mix(accent, "#ffffff", 0.18)
        else:
            ink, base = "#202b40", "#ffffff"
            shades = {"bg": 0.9, "surface": 0.97, "soft": 0.84, "border": 0.72}
            hover = mix(accent, "#000000", 0.15)
        palette = {key: mix(tint, base, amount) for key, amount in shades.items()}
        palette.update(
            accent=accent, **{"accent-hover": hover}, muted=mix(ink, palette["bg"], 0.4)
        )
        return (
            ':root, :root[data-theme="dark"] {'
            + "; ".join(f"--{key}: {color}" for key, color in palette.items())
            + "}\n"
        )
