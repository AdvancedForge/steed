"""Maintained HTML preferences and profile workspace; Python owns all writes."""

import sqlite3
from secrets import token_hex

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from browser.config import DEFAULT_SEARCH_ENGINE, Config
from browser.utils import sanitize_profile_name
from browser.webengine import user_agent_mismatch
from ui.html_surface import HtmlSurface

DEFAULTS = {
    "home_page": "steed:newtab",
    "new_tab_page": "steed:newtab",
    "search_engine": DEFAULT_SEARCH_ENGINE,
    "restore_tabs_on_startup": True,
    "dark_mode": False,
    "dark_pages": False,
    "spellcheck_language": "",
    "safe_browsing": True,
    "check_updates": True,
    "open_again": "front",
    "keep_agent_chats": True,
    "remember_pages": False,
    "remember_pages_except": "",
    "user_agent_override": "",
    "google_login_compatibility": True,
    "global_cookie_sharing_mode": "on_switch",
    "global_cookie_sharing_apply_on_startup": True,
    "default_profile": "global",
    "devtools_side": "right",
    "native_interface": False,
    "save_before_closing": True,
    "search_suggestions": True,
    "address_autocomplete": True,
    "tab_previews": True,
    "visual_effects": "full",
    "tab_card_fixed_width": True,
    "auto_zoom_small_text": True,
    "strip_tracking": True,
    "https_first": True,
    "resist_fingerprinting": False,
    "translate_service": "",
    "translate_language": "",
}
SECTIONS = ("general", "profile", "tabs", "view", "cookies", "agents", "everything")
# Settings → Everything: the pages it opens (each in a new tab), and
# Dictation, which is a dialog.
DIRECTORY = frozenset(
    (
        "history", "bookmarks", "downloads", "tab-groups", "library", "passwords",
        "cookies", "permissions", "adblock", "access", "style", "layout",
        "newtab-settings", "welcome", "agents", "agent-setup", "tasks",
        "connections", "services", "panels", "apps", "userscripts", "memory",
        "features", "dictation", "watches",
    )
)  # fmt: skip


class SettingsDialog(QDialog):
    """A nonmodal workspace with explicit Save, versioned drafts and reviews."""

    applied = pyqtSignal()

    def __init__(self, config: Config, parent=None, effective_ua: str = ""):
        super().__init__(parent)
        self.config = config
        self.window = parent
        self.manager = getattr(parent, "profile_manager", None)
        self.effective_ua = effective_ua
        self.setWindowTitle("Steed · Settings & profiles")
        self.setMinimumSize(560, 420)
        available = self.screen().availableGeometry()
        self.resize(
            min(940, max(560, available.width() - 48)),
            min(730, max(420, available.height() - 48)),
        )
        self._base = self._read_preferences()
        self._draft = dict(self._base)
        self._version = 0
        self._context = 0
        self._section = "general"
        self._intent = ""
        self._intent_revision = 0
        self._last_sequence = 0
        self._ack = 0
        self._identities = {}
        self._targets = {}
        self._review = None
        self._error = ""
        self._notice = ""
        try:
            from browser import default_browser

            self._default_browser = default_browser.is_default()
        except OSError:
            self._default_browser = False
        self._closed = False
        self._profiles = []
        self._default_pin = None
        try:
            self._refresh_profiles()
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            self._error = (
                "Profiles could not be loaded. Close and reopen settings to try again."
            )
        self._pin_default()
        self.surface = HtmlSurface("settings", self, label="settings")
        self.surface.command.connect(self._on_command)
        self.surface.reloading.connect(self._reload)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        if self.manager is not None:
            self.manager.profiles_updated.connect(self.refresh)
            self.manager.profile_changed.connect(self._profile_changed)
            self.manager.registry.settings_changed.connect(self.refresh)
            self.manager.registry.profile_usage_changed.connect(self.refresh)
        self.refresh()

    def _read_preferences(self):
        return {
            key: value
            if type(value := self.config.get(key, fallback)) is type(fallback)
            else fallback
            for key, fallback in DEFAULTS.items()
        }

    @property
    def state(self):
        return {
            "context": self._context,
            "draft_version": self._version,
            "ack": self._ack,
            "section": self._section,
            "intent": self._intent,
            "intent_revision": self._intent_revision,
            "preferences": dict(self._draft),
            "dirty": self._draft != self._base,
            "external_changes": self._read_preferences() != self._base,
            "dark": self._draft["dark_mode"],
            "error": self._error,
            "notice": self._notice,
            "effective_ua": self.manager.current_profile.httpUserAgent()
            if self.manager
            else self.effective_ua,
            "ua_warning": user_agent_mismatch(self._draft["user_agent_override"]),
            "profiles": list(self._profiles),
            "profiles_available": self.manager is not None,
            "current_profile": self.manager.current_profile_name
            if self.manager
            else "",
            "private": self.manager.is_search_profile() if self.manager else False,
            "review": dict(self._review) if self._review else None,
            "window_links": self.manager is not None,
            "default_browser": self._default_browser,
        }

    def _publish(self):
        if not self._closed:
            self.surface.publish(self.state)

    def open_section(self, section="general", *, intent=""):
        if section in SECTIONS:
            self._section = section
            self._intent = intent
            self._intent_revision += 1
            self._publish()

    def _signature(self, name):
        if name == "search":
            return ("private", id(self.manager.search_profile))
        path = self.manager.get_profile_path(name)
        info = path.stat()
        return (info.st_dev, info.st_ino)

    def _block_reason(self, name):
        if name in ("global", "search"):
            return "Built-in profiles cannot be renamed or archived."
        if name == self.config.default_profile:
            return "Choose another startup profile and save before renaming or archiving this one."
        owners = sum(
            o.current_profile_name == name for o in self.manager.registry.owners
        )
        if owners:
            return f"Open in {owners} window(s). Switch those windows away first."
        if name in self.manager.registry.task_profiles:
            return "An agent task is using this profile. End its access first."
        if self.manager.registry.passwords.busy(name):
            return "A password operation is using this profile. Finish it first."
        return ""

    def _refresh_profiles(self):
        if self.manager is None:
            return
        identities, targets, rows = {}, {}, []
        for name in [*self.manager.list_profiles(), "search"]:
            signature = self._signature(name)
            previous = self._identities.get(name)
            token = (
                previous[1] if previous and previous[0] == signature else token_hex(16)
            )
            identities[name] = (signature, token)
            targets[token] = (name, signature)
            reason = self._block_reason(name)
            rows.append(
                {
                    "id": token,
                    "name": name,
                    "label": "Global"
                    if name == "global"
                    else "Private browsing"
                    if name == "search"
                    else name,
                    "current": name == self.manager.current_profile_name,
                    "default": name == self.config.default_profile,
                    "private": name == "search",
                    "reason": reason,
                    "can_mutate": not reason,
                }
            )
        self._identities, self._targets, self._profiles = identities, targets, rows

    def _pin_default(self):
        entry = self._identities.get(self._draft["default_profile"])
        self._default_pin = entry[1] if entry else None

    def refresh(self):
        if self._closed:
            return
        try:
            self._refresh_profiles()
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            self._profiles, self._targets = [], {}
            self._error = (
                "Profiles could not be loaded. Close and reopen settings to try again."
            )
        self._publish()

    def _profile_changed(self, _name):
        self.surface.conceal()
        self._context += 1
        self._review = None
        self.refresh()

    def _reload(self):
        self._context += 1
        self._last_sequence = self._ack = 0
        self._review = None
        self.refresh()

    @staticmethod
    def _validate_values(values):
        if not isinstance(values, dict) or set(values) != set(DEFAULTS):
            raise ValueError("Use the supported preference fields.")
        for key, fallback in DEFAULTS.items():
            value = values[key]
            if type(value) is not type(fallback):
                raise ValueError("Use the expected preference value types.")
            if isinstance(value, str) and (
                len(value) > 4096 or any(ord(c) < 32 for c in value)
            ):
                raise ValueError(
                    "Preference text must be at most 4,096 characters without control characters."
                )
        if values["global_cookie_sharing_mode"] not in ("on_switch", "all_profiles"):
            raise ValueError("Choose one of the supported cookie sharing modes.")
        if values["devtools_side"] not in ("right", "bottom", "window"):
            raise ValueError("Choose where Developer Tools open.")
        if values["visual_effects"] not in ("full", "balanced", "minimal"):
            raise ValueError("Choose how much motion and light Steed shows.")
        from browser.page_translation import check_settings

        check_settings(
            values["translate_service"].strip(), values["translate_language"].strip()
        )

    def set_preference(self, key, value, *, version=None):
        if not isinstance(key, str) or key not in DEFAULTS:
            raise ValueError("Unknown preference.")
        candidate = {**self._draft, key: value}
        self._validate_values(candidate)
        version = self._version + 1 if version is None else version
        if type(version) is not int or not self._version < version <= 2**53 - 1:
            raise ValueError("The settings draft changed. Try the edit again.")
        if key == "default_profile" and self.manager is not None:
            self._refresh_profiles()
            if value == "search" or value not in self._identities:
                raise ValueError("Choose an existing regular startup profile.")
        self._draft = candidate
        self._version = version
        if key == "default_profile":
            self._pin_default()
        self._error = ""
        self._notice = ""
        self._publish()

    def apply(self):
        """Atomically merge edited keys; refuse conflicting edits from another window."""
        self._validate_values(self._draft)
        values = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in self._draft.items()
        }
        for key in ("home_page", "new_tab_page", "search_engine"):
            values[key] = values[key] or DEFAULTS[key]
        changed = {k: v for k, v in values.items() if v != self._base[k]}
        current = self._read_preferences()
        if any(
            current[k] != self._base[k] and current[k] != v for k, v in changed.items()
        ):
            raise ValueError(
                "These settings changed in another window. Reload saved values before saving."
            )
        if "default_profile" in changed and self.manager is not None:
            self._refresh_profiles()
            entry = self._identities.get(values["default_profile"])
            if (
                not entry
                or entry[1] != self._default_pin
                or values["default_profile"] == "search"
            ):
                raise ValueError(
                    "The startup profile changed. Choose it again before saving."
                )
        if changed:
            self.config.update(changed)
        self._base = self._read_preferences()
        self._draft = dict(self._base)
        self._pin_default()
        self.applied.emit()

    def _target(self, token, *, mutation=False):
        if not isinstance(token, str):
            raise ValueError("Choose a profile from this workspace.")
        before = self._targets.get(token)
        self._refresh_profiles()
        if not before or self._targets.get(token) != before:
            raise ValueError("That profile changed or was removed. Choose it again.")
        name = before[0]
        if mutation and (reason := self._block_reason(name)):
            raise ValueError(reason)
        return name

    @staticmethod
    def _name(value):
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 80
            or any(ord(c) < 32 for c in value)
            or sanitize_profile_name(value) != value.strip()
            or value.strip() in ("global", "search")
        ):
            raise ValueError(
                "Use a non-reserved profile name of 1–80 characters without path separators or control characters."
            )
        return value.strip()

    def _prepare(self, kind, token, name=""):
        source = self._target(token, mutation=True)
        target = self._name(name) if kind == "rename" else ""
        if target and target in self.manager.list_profiles():
            raise ValueError("That profile name already exists.")
        self._review = {
            "id": token_hex(16),
            "kind": kind,
            "profile": token,
            "source": source,
            "name": target,
        }

    def _confirm(self, token):
        review = self._review
        self._review = None  # One attempt; failures need a fresh visible review.
        if not review or token != review["id"]:
            raise ValueError("Open a fresh profile review before confirming.")
        name = self._target(review["profile"], mutation=True)
        if review["kind"] == "rename":
            ok = self.manager.rename_profile(name, review["name"])
            success = f"Renamed {name} to {review['name']}."
        else:
            ok = self.manager.delete_profile(name)
            success = f"Archived {name}. It can be recovered from deleted_profiles in the browser data directory."
        if not ok:
            raise ValueError(
                "The profile is still in use, its name is unavailable, or its files could not be changed. Switch other windows and agent views away, then try again."
            )
        self._notice = success

    def _on_command(self, command):
        action = command.get("action")
        fields = {
            "navigate": {"section"},
            "edit": {"key", "value", "version"},
            "save": {"values", "version"},
            "cancel": set(),
            "reset": set(),
            "create": {"name"},
            "switch": {"profile"},
            "open_window": {"profile"},
            "rename": {"profile", "name"},
            "archive": {"profile"},
            "confirm": {"review"},
            "cancel_review": set(),
            "open": {"destination"},
            "open_page": {"page"},
            "make_default": set(),
        }
        if (
            self._closed
            or not isinstance(action, str)
            or action not in fields
            or type(command.get("context")) is not int
            or command["context"] != self._context
            or type(command.get("sequence")) is not int
            or not self._last_sequence < command["sequence"] <= 2**53 - 1
            or set(command) != {"action", "context", "sequence"} | fields[action]
        ):
            return
        self._last_sequence = sequence = command["sequence"]
        self._error = ""
        try:
            if action == "navigate":
                self.open_section(command["section"])
            elif action == "edit":
                self.set_preference(
                    command["key"], command["value"], version=command["version"]
                )
            elif action == "save":
                self._validate_values(command["values"])
                version = command["version"]
                if type(version) is not int or not self._version < version <= 2**53 - 1:
                    raise ValueError("The settings draft changed. Try Save again.")
                if (
                    command["values"]["default_profile"]
                    != self._draft["default_profile"]
                ):
                    raise ValueError("Choose the startup profile before saving.")
                self._draft = dict(command["values"])
                self._version = version
                self.apply()
                self.accept()
            elif action == "cancel":
                self.reject()
            elif action == "reset":
                self._base = self._read_preferences()
                self._draft = dict(self._base)
                self._version += 1
                self._review = None
                self._pin_default()
                self._notice = (
                    "Loaded saved preferences. Unsaved preference edits were discarded."
                )
            elif action == "create":
                name = self._name(command["name"])
                if not self.manager or not self.manager.create_profile(name):
                    raise ValueError(
                        "That profile name already exists or is unavailable."
                    )
                self.window._switch_to_profile(name)
                self._notice = f"Created and opened {name}."
            elif action in ("switch", "open_window"):
                name = self._target(command["profile"])
                if action == "switch":
                    if name != self.manager.current_profile_name:
                        self.window._switch_to_profile(name)
                else:
                    self.window._pop_out_profile_named(name)
            elif action in ("rename", "archive"):
                self._prepare(action, command["profile"], command.get("name", ""))
            elif action == "confirm":
                self._confirm(command["review"])
            elif action == "cancel_review":
                self._review = None
            elif action == "make_default":
                from browser import default_browser

                try:
                    self._notice = default_browser.make_default()
                except OSError as error:
                    raise ValueError(str(error)) from None
                self._default_browser = default_browser.is_default()
            elif action == "open_page":
                page = command["page"]
                if page not in DIRECTORY or self.window is None:
                    raise ValueError("That is not a Steed page.")
                if page == "dictation":
                    self.window.open_dictation_settings()
                else:
                    self.window._new_tab(url="steed:" + page)
            elif action == "open":
                method = (
                    {
                        "layout": "_open_layout_settings",
                        "newtab": "_customize_new_tab",
                        "setup": "_open_agent_setup",
                        "agents": "_open_agent_tab",
                        "style": "_open_page_style",
                    }.get(command["destination"])
                    if isinstance(command["destination"], str)
                    else None
                )
                if method and self.window is not None:
                    if (
                        command["destination"] == "newtab"
                        and self.manager.is_search_profile()
                    ):
                        raise ValueError(
                            "Switch to a regular profile to customize its New Tab page."
                        )
                    host = getattr(self, "_html_host", None)
                    previous = self.window._menu_anchor
                    if host is not None and not host.closed:
                        self.window._menu_anchor = host.pos()
                    try:
                        getattr(self.window, method)()
                    finally:
                        self.window._menu_anchor = previous
        except ValueError as error:
            self._error = str(error)
        except (OSError, RuntimeError, sqlite3.Error):
            self._error = "The change could not be completed. Saved preferences are preserved; check profile use and try again."
        finally:
            self._ack = sequence
            if not self._closed:
                self.refresh()

    def done(self, result):
        if self._closed:
            return
        self._closed = True
        if self.manager is not None:
            self.manager.profiles_updated.disconnect(self.refresh)
            self.manager.profile_changed.disconnect(self._profile_changed)
            self.manager.registry.settings_changed.disconnect(self.refresh)
            self.manager.registry.profile_usage_changed.disconnect(self.refresh)
        self.surface.dispose()
        super().done(result)

    def accept(self):
        self.done(QDialog.DialogCode.Accepted)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)
