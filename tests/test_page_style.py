"""Appearance persistence, profile scope and untrusted configuration handling."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QObject

from config import Config
from services.page_style import DEFAULTS, PageStyle, clean_style


class PageStyleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        home.start()
        self.addCleanup(home.stop)
        self.config = Config()
        self.registry = QObject()
        self.registry.config = self.config
        self.identities = {"global": "global-id", "work": "work-id"}
        self.registry.start_pages = SimpleNamespace(
            profile_key=lambda name, **_: self.identities[name]
        )
        self.styles = PageStyle(self.registry)

    def test_individual_scope_all_scope_and_future_profiles(self):
        work = {**DEFAULTS, "accent": "#ff1122", "menu_bar": False}
        self.styles.save("work", work)
        self.assertEqual(self.styles.get("work"), work)
        self.assertEqual(self.styles.get("global"), DEFAULTS)
        self.styles.save("search", {**DEFAULTS, "theme": "light"})
        self.styles.save("global", {**DEFAULTS, "theme": "dark"}, all_profiles=True)
        self.assertEqual(self.styles.get("work")["theme"], "dark")
        self.assertEqual(self.styles.get("search")["theme"], "dark")
        self.identities["future"] = "future-id"
        self.assertEqual(self.styles.get("future")["theme"], "dark")
        self.registry.config = Config()
        restarted = PageStyle(self.registry)
        self.assertEqual(restarted.get("work")["theme"], "dark")
        self.assertEqual(self.config.get("page_style")["profiles"], {})

    def test_profile_identity_survives_rename_but_not_recreation(self):
        self.styles.save("work", {**DEFAULTS, "css": "body { color: red; }"})
        self.identities["renamed"] = self.identities.pop("work")
        self.identities["work"] = "new-work-id"
        self.assertTrue(self.styles.get("renamed")["css"])
        self.assertEqual(self.styles.get("work"), DEFAULTS)

    def test_legacy_styles_preserve_saved_preferences_without_palette(self):
        legacy = {key: value for key, value in DEFAULTS.items() if key != "colors"}
        legacy.update(theme="dark", css=".card { border-radius: 12px; }")
        self.config.set("page_style", {"version": 1, "default": legacy, "profiles": {}})
        self.assertEqual(self.styles.get("global"), {**legacy, "colors": {}})

    def test_styles_saved_before_glass_keep_their_choices(self):
        older = {
            key: value
            for key, value in DEFAULTS.items()
            if key not in ("colors", "glass")
        }
        older.update(theme="dark", accent="#123456")
        self.config.set("page_style", {"version": 1, "default": older, "profiles": {}})
        self.assertEqual(
            self.styles.get("global"), {**older, "colors": {}, "glass": False}
        )
        with self.assertRaises(ValueError):
            clean_style({**DEFAULTS, "glass": "yes"})

    def test_palette_overrides_are_validated_and_returned_as_independent_copies(self):
        colors = {"bg": "#102030", "ink": "#ffffff"}
        self.styles.save("work", {**DEFAULTS, "colors": colors})
        result = self.styles.get("work")
        result["colors"]["bg"] = "#aabbcc"
        self.assertEqual(self.styles.get("work")["colors"], colors)
        self.assertIn("--bg: #102030", self.styles.css("work"))
        self.config.set("page_style", {"version": 1, "default": {"css": "broken"}})
        fallback = self.styles.get("global")
        fallback["colors"]["bg"] = "#aabbcc"
        self.assertEqual(DEFAULTS["colors"], {})
        for invalid in ({"unknown": "#ffffff"}, {"bg": "red"}, {"bg": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                clean_style({**DEFAULTS, "colors": invalid})

    def test_private_changes_are_memory_only_and_cannot_sync(self):
        self.config.set("unrelated", True)
        before = self.config.config_file.read_bytes()
        self.styles.save("search", {**DEFAULTS, "menu_bar": True})
        self.assertTrue(self.styles.get("search")["menu_bar"])
        self.assertEqual(self.config.config_file.read_bytes(), before)
        self.assertFalse(PageStyle(self.registry).get("search")["menu_bar"])
        with self.assertRaisesRegex(ValueError, "regular profile"):
            self.styles.save("search", DEFAULTS, all_profiles=True)

    def test_failed_atomic_write_preserves_styles_and_does_not_notify(self):
        events = []
        self.styles.changed.connect(lambda: events.append(True))
        self.styles.save("work", {**DEFAULTS, "theme": "dark"})
        with patch.object(self.config, "save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.styles.save("global", DEFAULTS, all_profiles=True)
        self.assertEqual(self.styles.get("work")["theme"], "dark")
        self.assertEqual(events, [True])

    def test_conflicting_drafts_are_not_silently_overwritten(self):
        before = self.styles.get("global")
        self.styles.save("global", {**DEFAULTS, "theme": "dark"})
        with self.assertRaisesRegex(ValueError, "another window"):
            self.styles.save("global", DEFAULTS, expected=before)

    def test_invalid_storage_falls_back_and_edits_are_strict(self):
        self.assertEqual(
            clean_style({**DEFAULTS, "css": "nav > a { color: red; }"})["css"],
            "nav > a { color: red; }",
        )
        self.config.set("page_style", {"version": 1, "default": {"css": "broken"}})
        self.assertEqual(self.styles.get("global"), DEFAULTS)
        for change in (
            {"theme": "bogus"},
            {"loading_height": True},
            {"loading_height": 100},
            {"accent": "red; display:none"},
            {"css": "</style><script>"},
            {"css": "a" * 16001},
            {"css": "\ud800"},
            {"status_bar": "false"},
        ):
            with self.subTest(change=next(iter(change))):
                with self.assertRaises(ValueError):
                    clean_style({**DEFAULTS, **change})
        with self.assertRaises(ValueError):
            clean_style({**DEFAULTS, "extra": True})


class UnifiedPagesTests(unittest.TestCase):
    """By default Steed's pages wear the browser's colours (the photo's, the
    browser colours set in Style, or the plain dark browser's); choices made
    by hand still win, and it can be turned off."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        home.start()
        self.addCleanup(home.stop)
        self.config = Config()
        self.registry = QObject()
        self.registry.config = self.config
        self.tint = None
        test = self

        class Pages(QObject):  # A stand-in with just what styles ask.
            def profile_key(self, name, **_):
                return name + "-id"

            def theme_tint(self, profile):
                return test.tint

        self.registry.start_pages = Pages(self.registry)
        self.styles = PageStyle(self.registry)

    def palette(self, **style):
        self.styles.save("global", {**DEFAULTS, "theme": "dark", **style})
        css = self.styles.css("global")
        found = {}
        for block in css.split("}"):
            if ":root" in block:
                for part in block.split("{", 1)[-1].split(";"):
                    if ":" in part:
                        key, value = part.split(":", 1)
                        found[key.strip()] = value.strip()
        return found

    def test_older_saves_now_match_the_browser_and_keep_everything_else(self):
        old = {k: v for k, v in DEFAULTS.items() if k != "pages_match_browser"}
        old.update(glass=True, theme="dark", pages_follow_photo=False)
        self.config.set("page_style", {"version": 1, "default": old, "profiles": {}})
        style = self.styles.get("global")
        self.assertTrue(style["pages_match_browser"])
        self.assertTrue(style["glass"])  # Kept, not reset.
        self.assertNotIn("pages_follow_photo", style)

    def test_pages_take_the_photo_or_the_browser_colours(self):
        self.tint = "#c06030"  # A warm photo.
        warm = self.palette()
        self.assertIn("--bg", warm)
        bg = warm["--bg"]
        self.assertGreater(int(bg[1:3], 16), int(bg[5:7], 16))  # Red over blue.
        # No photo: the browser colours fixed in Style.
        self.tint = None
        chosen = self.palette(chrome={"toolbar": "#204060", "accent": "#40c0a0"})
        self.assertEqual(chosen["--accent"], "#40c0a0")
        self.assertGreater(int(chosen["--bg"][5:7], 16), int(chosen["--bg"][1:3], 16))
        # Nothing chosen: the plain dark browser's own neutral and accent.
        plain = self.palette()
        self.assertEqual(plain["--accent"], "#8caaff")
        red, green, blue = (int(plain["--bg"][i : i + 2], 16) for i in (1, 3, 5))
        self.assertLessEqual(max(red, green, blue) - min(red, green, blue), 2)

    def test_hand_chosen_page_colours_win_and_matching_can_be_off(self):
        self.tint = "#c06030"
        self.styles.save(
            "global", {**DEFAULTS, "theme": "dark", "colors": {"bg": "#000000"}}
        )
        css = self.styles.css("global")
        self.assertGreater(css.rindex("--bg: #000000"), css.index("--bg:"))
        off = self.palette(pages_match_browser=False)
        self.assertNotIn("--bg", off)
        self.assertNotIn("--accent", off)
