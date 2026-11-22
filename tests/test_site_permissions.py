"""Website choices are exact, durable, private-session isolated and removable."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from qt_test_support import application

from services.site_permissions import SitePermissions
from ui.site_permissions import SitePermissionDialog, SitePermissionsDialog


class SitePermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "site_permissions.json"
        self.store = SitePermissions(self.path)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            sip.delete(dialog)
        self.directory.cleanup()

    def remember(self, mode="global", **changes):
        return self.store.remember(
            "https://example.org",
            "MediaVideoCapture",
            mode,
            key="a" * 32,
            profile="work",
            **changes,
        )

    def allowed(self, **changes):
        values = {
            "origin": "https://example.org",
            "feature": "MediaVideoCapture",
            "key": "a" * 32,
            **changes,
        }
        return self.store.allowed(**values)

    def test_global_is_exact_site_and_capability_across_regular_profiles(self):
        self.remember()
        self.assertTrue(self.allowed(key="b" * 32))
        for changes in (
            {"origin": "http://example.org"},
            {"origin": "https://sub.example.org"},
            {"origin": "https://example.org:8443"},
            {"feature": "MediaAudioCapture"},
            {"private": True},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.allowed(**changes))

    def test_profile_identity_is_exact_and_survives_label_rename(self):
        self.remember("profile")
        self.assertTrue(self.allowed())
        self.assertFalse(self.allowed(key="b" * 32))
        self.assertTrue(
            SitePermissions(self.path).allowed(
                "https://example.org", "MediaVideoCapture", key="a" * 32
            )
        )

    def test_private_rules_never_persist_or_inherit_regular_rules(self):
        self.remember()
        persistent = self.path.read_bytes()
        self.assertFalse(self.allowed(private=True))
        self.remember(private=True)
        self.assertTrue(self.allowed(private=True))
        self.assertFalse(self.allowed(private=True, key="b" * 32))
        self.assertEqual(self.path.read_bytes(), persistent)
        self.assertFalse(
            SitePermissions(self.path).allowed(
                "https://example.org", "MediaVideoCapture", key="a" * 32, private=True
            )
        )

    def test_private_only_choice_does_not_create_a_file(self):
        self.remember(private=True)
        self.assertFalse(self.path.exists())
        self.store.forget(self.store.rules()[0]["id"])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.allowed(private=True))

    def test_failed_save_does_not_grant_or_replace_old_choices(self):
        self.remember("profile")
        previous = self.path.read_bytes()
        with patch(
            "services.site_permissions.save_json", side_effect=OSError("fixture")
        ):
            with self.assertRaises(OSError):
                self.remember()
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertFalse(self.allowed(key="b" * 32))

    def test_remove_and_restart_prompts_again_without_losing_other_rules(self):
        camera = self.remember()
        self.store.remember(
            "https://example.org",
            "Notifications",
            "global",
            key="a" * 32,
            profile="work",
        )
        self.store.forget(camera["id"])
        self.assertFalse(self.allowed())
        self.assertTrue(self.allowed(feature="Notifications"))
        self.assertEqual(len(SitePermissions(self.path).rules()), 1)

    def test_duplicate_choice_replaces_instead_of_growing(self):
        self.remember()
        self.remember()
        self.assertEqual(len(self.store.rules()), 1)

    def test_file_is_owner_only(self):
        self.remember()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_malformed_unknown_and_private_disk_rules_fail_closed(self):
        valid = self.remember()
        for change in (
            {"feature": "NewAuthority"},
            {"mode": "anything"},
            {"origin": "https://example.org/path"},
            {"private": True},
            {"extra": "permission"},
        ):
            self.path.write_text(
                json.dumps({"version": 1, "rules": [{**valid, **change}]})
            )
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.allowed()

    def test_invalid_origins_and_unknown_capabilities_are_never_saved(self):
        for origin in (
            "file:///tmp/page",
            "https://example.org/path",
            "https://*.example.org",
            "https://user:password@example.org",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.store.remember(
                    origin, "Notifications", "global", key="a" * 32, profile="work"
                )
        with self.assertRaises(ValueError):
            self.store.remember(
                "https://example.org",
                "Unsupported",
                "global",
                key="a" * 32,
                profile="work",
            )
        self.assertFalse(self.path.exists())

    def test_prompt_explains_exact_scope_and_keeps_once_default(self):
        dialog = SitePermissionDialog(
            "https://example.org", "MediaVideoCapture", "work"
        )
        self.dialogs.append(dialog)
        self.assertEqual(dialog.remember.currentData(), "once")
        dialog.remember.setCurrentIndex(1)
        self.assertIn("every regular profile", dialog.notice.text())
        self.assertIn("Incognito is excluded", dialog.notice.text())
        dialog.remember.setCurrentIndex(2)
        self.assertIn("profile work only", dialog.notice.text())
        self.assertIn("do not authorize agents", dialog.notice.text())

    def test_private_and_screen_share_prompts_explain_lifetime_and_picker(self):
        dialog = SitePermissionDialog(
            "https://example.org", "DesktopVideoCapture", "Search", private=True
        )
        self.dialogs.append(dialog)
        self.assertEqual(dialog.remember.count(), 2)
        dialog.remember.setCurrentIndex(1)
        self.assertIn("forgotten when Steed closes", dialog.notice.text())
        self.assertIn("choose a screen or window each time", dialog.notice.text())

    def test_management_forget_removes_exact_selected_rule(self):
        self.remember()
        dialog = SitePermissionsDialog(self.store)
        self.dialogs.append(dialog)
        dialog.table.selectRow(0)
        dialog.forget()
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(self.allowed())
