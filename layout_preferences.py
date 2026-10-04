"""Validated, persisted browser layout preferences.

Layout settings describe presentation only: toolbar button visibility and
order, menu bar order, agent sidebar placement and control density. Reading or
applying a layout never enables a browser, profile, file, folder or account
capability; that authority lives in the permission and approval layers.

Stored data is untrusted. Every value is cleaned before use: unknown
identifiers are dropped, missing ones are restored in their default position,
and a document written by another schema version falls back to today's
defaults rather than guessing at a partial layout.
"""

from copy import deepcopy
from typing import Any

LAYOUT_VERSION = 1

# The address bar buttons a layout may reorder or hide. Every one of these also
# stays reachable from the menu bar.
TOOLBAR_ITEMS = ("back", "forward", "reload", "home", "zoom", "passwords")
TOOLBAR_ORDER = ("back", "forward", "reload", "home", "address", "zoom", "passwords")

# Menus may be reordered but never hidden, so Settings and Reset Layout are
# always available.
MENU_ITEMS = ("general", "profile", "tabs", "view", "cookies", "agents")

DENSITIES = ("compact", "comfortable")
DEFAULT_DENSITY = "comfortable"

SIDEBAR_SIDES = ("left", "right")
DEFAULT_SIDEBAR_SIDE = "right"
DEFAULT_SIDEBAR_WIDTH = 320
SIDEBAR_WIDTH_RANGE = (300, 800)

MAX_NAMED_LAYOUTS = 32
MAX_LAYOUT_NAME = 48

# Key used inside the shared browser configuration file.
CONFIG_KEY = "layout"


def default_appearance() -> dict[str, Any]:
    """Return today's appearance, which is also the reset value."""
    return {
        "toolbar": {"order": list(TOOLBAR_ORDER), "hidden": []},
        "menus": {"order": list(MENU_ITEMS)},
        "sidebar": {"side": DEFAULT_SIDEBAR_SIDE, "width": DEFAULT_SIDEBAR_WIDTH},
        "density": DEFAULT_DENSITY,
    }


def clean_name(value: Any) -> str:
    """Return a usable layout name, or an empty string.

    Control characters are refused rather than folded away: a name carrying a
    newline is malformed input, not a name to tidy up. Runs of spaces are
    collapsed and surrounding space is trimmed.
    """
    if not isinstance(value, str):
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return ""
    name = " ".join(segment for segment in value.split(" ") if segment)
    if not name or len(name) > MAX_LAYOUT_NAME:
        return ""
    return name


def clean_order(value: Any, known: tuple[str, ...]) -> list[str]:
    """Keep known identifiers in stored order, then restore the missing ones."""
    ordered: list[str] = []
    if isinstance(value, (list, tuple)):
        for entry in value:
            if isinstance(entry, str) and entry in known and entry not in ordered:
                ordered.append(entry)
    ordered.extend(entry for entry in known if entry not in ordered)
    return ordered


def clean_identifiers(value: Any, known: tuple[str, ...]) -> list[str]:
    """Return the known identifiers present in value, without duplicates."""
    if not isinstance(value, (list, tuple)):
        return []
    kept: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry in known and entry not in kept:
            kept.append(entry)
    return kept


def clean_toolbar_order(value: Any) -> list[str]:
    """Keep the address field mandatory and migrate old button-only layouts."""
    order = clean_order(value, TOOLBAR_ORDER)
    if not isinstance(value, (list, tuple)) or "address" not in value:
        order.remove("address")
        order.insert(order.index("zoom"), "address")
    return order


def clean_choice(value: Any, choices: tuple[str, ...], default: str) -> str:
    """Return value when it is an accepted choice, otherwise the default."""
    if isinstance(value, str) and value in choices:
        return value
    return default


def clean_width(value: Any) -> int:
    """Return a bounded sidebar width, ignoring non-integer settings."""
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_SIDEBAR_WIDTH
    low, high = SIDEBAR_WIDTH_RANGE
    return max(low, min(high, value))


def clean_appearance(raw: Any) -> dict[str, Any]:
    """Return one validated appearance, whatever was stored."""
    source = raw if isinstance(raw, dict) else {}
    toolbar = source.get("toolbar")
    toolbar = toolbar if isinstance(toolbar, dict) else {}
    menus = source.get("menus")
    menus = menus if isinstance(menus, dict) else {}
    sidebar = source.get("sidebar")
    sidebar = sidebar if isinstance(sidebar, dict) else {}
    return {
        "toolbar": {
            "order": clean_toolbar_order(toolbar.get("order")),
            "hidden": clean_identifiers(toolbar.get("hidden"), TOOLBAR_ITEMS),
        },
        "menus": {"order": clean_order(menus.get("order"), MENU_ITEMS)},
        "sidebar": {
            "side": clean_choice(
                sidebar.get("side"), SIDEBAR_SIDES, DEFAULT_SIDEBAR_SIDE
            ),
            "width": clean_width(sidebar.get("width")),
        },
        "density": clean_choice(source.get("density"), DENSITIES, DEFAULT_DENSITY),
    }


class LayoutPreferences:
    """The user's layout choices plus any named layouts they saved."""

    def __init__(self, raw: Any = None) -> None:
        self._layout = default_appearance()
        self._named: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._load(raw)

    def _load(self, raw: Any) -> None:
        document = raw if isinstance(raw, dict) else {}
        version = document.get("version")
        if isinstance(version, bool) or version != LAYOUT_VERSION:
            document = {}
        self._layout = clean_appearance(document.get("layout"))
        stored = document.get("named")
        self._named = {}
        if isinstance(stored, dict):
            for key, value in list(stored.items())[:MAX_NAMED_LAYOUTS]:
                name = clean_name(key)
                if name and name not in self._named:
                    self._named[name] = clean_appearance(value)
        active = clean_name(document.get("active"))
        self._active = active if active in self._named else None

    # ---------- Current appearance ----------

    @property
    def toolbar_order(self) -> list[str]:
        """Every toolbar button, in display order, hidden ones included."""
        return list(self._layout["toolbar"]["order"])

    @property
    def hidden_toolbar(self) -> list[str]:
        """Toolbar buttons the layout hides."""
        return list(self._layout["toolbar"]["hidden"])

    @property
    def visible_toolbar(self) -> list[str]:
        """Toolbar buttons the layout shows, in display order."""
        hidden = self.hidden_toolbar
        return [
            name
            for name in self.toolbar_order
            if name in TOOLBAR_ITEMS and name not in hidden
        ]

    @property
    def menu_order(self) -> list[str]:
        """Menu bar entries in display order; menus are never hidden."""
        return list(self._layout["menus"]["order"])

    @property
    def sidebar_side(self) -> str:
        """Which edge the agent sidebar docks to."""
        return self._layout["sidebar"]["side"]

    @property
    def sidebar_width(self) -> int:
        """Remembered agent sidebar width in pixels."""
        return self._layout["sidebar"]["width"]

    @property
    def density(self) -> str:
        """Control spacing for the browser chrome."""
        return self._layout["density"]

    # ---------- Named layouts ----------

    @property
    def named_layouts(self) -> list[str]:
        """Saved layout names in saved order."""
        return list(self._named)

    @property
    def active_layout(self) -> str | None:
        """Name of the layout that produced the current appearance."""
        return self._active

    def _sync_active(self) -> None:
        """Keep a saved layout selected only while it still matches."""
        if self._active is not None and self._layout != self._named.get(self._active):
            self._active = None

    def can_save(self, name: str) -> bool:
        """Report whether another layout may be stored under this name."""
        cleaned = clean_name(name)
        if not cleaned:
            return False
        return cleaned in self._named or len(self._named) < MAX_NAMED_LAYOUTS

    def save_named(self, name: str) -> bool:
        """Store the current appearance under a name and select it."""
        cleaned = clean_name(name)
        if not self.can_save(cleaned):
            return False
        self._named[cleaned] = clean_appearance(self._layout)
        self._active = cleaned
        return True

    def apply_named(self, name: str) -> bool:
        """Adopt a saved layout, leaving other saved layouts untouched."""
        cleaned = clean_name(name)
        if cleaned not in self._named:
            return False
        self._layout = clean_appearance(self._named[cleaned])
        self._active = cleaned
        return True

    def delete_named(self, name: str) -> bool:
        """Forget one saved layout without changing the current appearance."""
        cleaned = clean_name(name)
        if cleaned not in self._named:
            return False
        del self._named[cleaned]
        if self._active == cleaned:
            self._active = None
        return True

    # ---------- Edits ----------

    def set_toolbar(self, order: Any, hidden: Any = ()) -> None:
        """Record toolbar display order and hidden buttons."""
        self._layout["toolbar"] = {
            "order": clean_toolbar_order(order),
            "hidden": clean_identifiers(
                list(hidden) if isinstance(hidden, (list, tuple)) else hidden,
                TOOLBAR_ITEMS,
            ),
        }
        self._sync_active()

    def set_menu_order(self, order: Any) -> None:
        """Record menu bar order; menus stay visible whatever is stored."""
        self._layout["menus"] = {
            "order": clean_order(
                list(order) if isinstance(order, (list, tuple)) else order, MENU_ITEMS
            )
        }
        self._sync_active()

    def set_sidebar(self, side: Any, width: Any = None) -> None:
        """Record the sidebar edge and, when given, its width."""
        self._layout["sidebar"]["side"] = clean_choice(
            side, SIDEBAR_SIDES, DEFAULT_SIDEBAR_SIDE
        )
        if width is not None:
            self._layout["sidebar"]["width"] = clean_width(width)
        self._sync_active()

    def set_density(self, density: Any) -> None:
        """Record control spacing."""
        self._layout["density"] = clean_choice(density, DENSITIES, DEFAULT_DENSITY)
        self._sync_active()

    def reset(self) -> None:
        """Restore today's appearance, keeping saved layouts available."""
        self._layout = default_appearance()
        self._active = None

    # ---------- Persistence ----------

    def to_dict(self) -> dict[str, Any]:
        """Return the storable document for this layout."""
        return {
            "version": LAYOUT_VERSION,
            "active": self._active,
            "layout": clean_appearance(self._layout),
            "named": {
                name: clean_appearance(value) for name, value in self._named.items()
            },
        }

    def copy(self) -> "LayoutPreferences":
        """Return an independent copy, used to preview before saving."""
        return LayoutPreferences(self.to_dict())


def preferences_from_config(config) -> LayoutPreferences:
    """Read validated layout preferences from the shared configuration."""
    return LayoutPreferences(config.get(CONFIG_KEY, {}))


def save_preferences(config, preferences: LayoutPreferences) -> None:
    """Persist layout preferences through the shared configuration."""
    config.set(CONFIG_KEY, preferences.to_dict())


class LayoutConflict(ValueError):
    """Another window changed the same setting while an editor was open."""


def merge_preferences(base, edited, current) -> LayoutPreferences:
    """Preserve concurrent, unrelated edits; refuse competing changes."""
    missing = object()

    def merge(before, after, latest):
        if after == before:
            return deepcopy(latest) if latest is not missing else missing
        if latest == before or latest == after:
            return deepcopy(after) if after is not missing else missing
        if all(isinstance(value, dict) for value in (before, after, latest)):
            result = {}
            for key in dict.fromkeys([*latest, *after, *before]):
                value = merge(
                    before.get(key, missing),
                    after.get(key, missing),
                    latest.get(key, missing),
                )
                if value is not missing:
                    result[key] = value
            return result
        raise LayoutConflict("Another window changed the same layout setting.")

    merged = LayoutPreferences(
        merge(base.to_dict(), edited.to_dict(), current.to_dict())
    )
    merged._sync_active()
    return merged
