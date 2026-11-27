"""Maintained HTML editor for the shared Steed stylesheet and browser bars."""

from secrets import token_hex

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from browser.layout_components import TEXT_SIZES
from browser.layout_preferences import (
    DENSITIES,
    LayoutConflict,
    preferences_from_config,
)
from browser.tab_appearance import (
    CHOICES,
    FLAG_LABELS,
    LABELS,
    TAB_WIDTH_RANGE,
    VALUE_LABELS,
)
from services.page_style import CHROME_FIELDS, DEFAULTS, palette, style_guide
from ui.html_surface import HtmlSurface


class StylePage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.manager = window.profile_manager
        self.styles = self.manager.registry.page_style
        self.layout_base = preferences_from_config(window.config)
        self.context = token_hex(16)
        self.sequence = 0
        self.base = self.styles.get(self.manager.current_profile_name)
        self.error = ""
        self.notice = ""
        self.disposed = False
        self.surface = HtmlSurface("style", self, label="appearance")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        self.surface.reloading.connect(self.reset)
        self.manager.profile_changed.connect(self.reset)
        self.styles.changed.connect(self.publish)
        self.manager.registry.layout_changed.connect(self._layout_changed)
        # The automatic browser colours follow the New Tab photo too.
        pages = self.manager.registry.start_pages
        pages.photos.changed.connect(self._colors_changed)
        pages.changed.connect(self._colors_changed)
        self.publish()

    def _colors_changed(self, *_):
        # After the window has worked out its new colours.
        QTimer.singleShot(0, self.publish)

    def _automatic_colors(self):
        colors = getattr(self.window, "automatic_colors", None) or {}
        return {
            key: colors[key]
            for key in CHROME_FIELDS
            if isinstance(colors.get(key), str)
        }

    def _layout_changed(self, *_):
        self.layout_base = preferences_from_config(self.window.config)
        self.publish()

    def _layout_state(self):
        layout = self.layout_base
        return {
            "values": {
                "tabs": layout.tabs,
                "density": layout.density,
                "text_size": layout.text_size,
            },
            "choices": {
                key: [
                    [value, VALUE_LABELS.get(value, value.title())] for value in values
                ]
                for key, (values, _) in CHOICES.items()
            },
            "labels": {**LABELS, **FLAG_LABELS},
            "flags": list(FLAG_LABELS),
            "width": list(TAB_WIDTH_RANGE),
            "densities": list(DENSITIES),
            "text_sizes": list(TEXT_SIZES),
        }

    def reset(self, *_):
        self.context = token_hex(16)
        self.sequence = 0
        self.base = self.styles.get(self.manager.current_profile_name)
        self.layout_base = preferences_from_config(self.window.config)
        self.error = self.notice = ""
        self.publish()

    def publish(self):
        if self.disposed:
            return
        profile = self.manager.current_profile_name
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "values": self.base,
                "defaults": DEFAULTS,
                "guide": style_guide(),
                "palettes": {"light": palette(), "dark": palette(True)},
                "chrome_fields": CHROME_FIELDS,
                "chrome_auto": self._automatic_colors(),
                "profile": "Incognito · temporary for this session"
                if profile == "search"
                else profile,
                "private": profile == "search",
                "dark": self.styles.dark(profile),
                "error": self.error,
                "notice": self.notice,
                "external_changes": self.styles.get(profile) != self.base,
                "layout": self._layout_state(),
            }
        )

    def command(self, command):
        if self.disposed or command.get("context") != self.context:
            return
        sequence = command.get("sequence")
        if type(sequence) is not int or sequence <= self.sequence:
            return
        self.sequence = sequence
        self.error = self.notice = ""
        try:
            action = command.get("action")
            if action == "reload":
                self.reset()
                return
            if action == "save_layout":
                self._save_layout(command)
                self.publish()
                return
            if action == "theme":
                self._save_theme(command)
                self.publish()
                return
            if action != "save" or set(command) != {
                "context",
                "sequence",
                "action",
                "values",
                "all_profiles",
            }:
                raise ValueError("Unsupported appearance request.")
            self.styles.save(
                self.manager.current_profile_name,
                command["values"],
                all_profiles=command["all_profiles"],
                expected=self.base,
            )
            self.base = self.styles.get(self.manager.current_profile_name)
            self.notice = (
                "Saved for all profiles, including new profiles."
                if command["all_profiles"]
                else "Saved for this profile."
            )
        except ValueError as error:
            self.error = str(error)
        except (OSError, RuntimeError):
            self.error = (
                "Appearance could not be saved. Your current settings are preserved."
            )
        self.publish()

    def _save_theme(self, command):
        """Light, dark or the system's: applied at once, like other browsers'
        theme switches, without the rest of the form."""
        value = command.get("value")
        if set(command) != {"context", "sequence", "action", "value"} or value not in (
            "system",
            "light",
            "dark",
        ):
            raise ValueError("Choose light, dark or the system's theme.")
        profile = self.manager.current_profile_name
        saved = self.styles.get(profile)
        saved["theme"] = value
        self.styles.save(profile, saved, expected=self.base)
        self.base = self.styles.get(profile)
        self.notice = {
            "system": "Steed follows your system's light or dark setting.",
            "light": "Steed is light.",
            "dark": "Steed is dark.",
        }[value]

    def _save_layout(self, command):
        """Tabs, spacing and text size are browser-wide: every window follows."""
        values = command.get("values")
        if set(command) != {"context", "sequence", "action", "values"} or not (
            isinstance(values, dict)
            and set(values) == {"tabs", "density", "text_size"}
            and isinstance(values["tabs"], dict)
        ):
            raise ValueError("Unsupported appearance request.")
        if values["density"] not in DENSITIES or values["text_size"] not in TEXT_SIZES:
            raise ValueError("Choose a listed spacing and text size.")
        saved = self.layout_base
        edited = saved.copy()
        edited.set_tabs(values["tabs"])
        edited.set_density(values["density"])
        edited.set_text_size(values["text_size"])
        try:
            self.manager.registry.commit_layout(edited, base=saved)
        except LayoutConflict:
            self.error = (
                "Another window changed the same setting. Reload saved settings "
                "and apply again."
            )
            return
        self.layout_base = preferences_from_config(self.window.config)
        self.notice = "Tab and toolbar style applied to every window."

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.manager.registry.layout_changed.disconnect(self._layout_changed)
        self.manager.profile_changed.disconnect(self.reset)
        self.styles.changed.disconnect(self.publish)
        self.surface.dispose()
