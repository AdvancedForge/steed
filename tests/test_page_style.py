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

    def test_private_changes_are_memory_only_and_cannot_sync(self):
        self.config.set("unrelated", True)
        before = self.config.config_file.read_bytes()
        self.styles.save("search", {**DEFAULTS, "menu_bar": False})
        self.assertFalse(self.styles.get("search")["menu_bar"])
        self.assertEqual(self.config.config_file.read_bytes(), before)
        self.assertTrue(PageStyle(self.registry).get("search")["menu_bar"])
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
