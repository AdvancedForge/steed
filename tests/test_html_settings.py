"""Preferences/profile workflows through the real maintained HTML bridge."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_task_profiles as fixtures
from PyQt6 import sip
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QDialog

from browser_window import BrowserWindow
from config import DEFAULT_SEARCH_ENGINE
from ui.settings_dialog import DEFAULTS, SettingsDialog


def themed(window):
    """A dark (or custom) theme is applied, beyond the always-present scroll
    bar rules."""
    return "QWidget {background" in window.styleSheet()


class HtmlSettingsTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = fixtures.TaskProfileTests.setUp
    tearDown = fixtures.TaskProfileTests.tearDown
    wait_for = fixtures.TaskProfileTests.wait_for

    def loaded(self, *, section="general"):
        window = BrowserWindow()
        self.windows.append(window)
        window.show()
        window._open_settings(section)
        dialog = window.settings_dialog
        self.wait_for(lambda: dialog.surface.ready)
        self.wait_for(lambda: self.value(dialog, "!!state"))
        return window, dialog

    def value(self, dialog, script):
        result = []
        dialog.surface.page.runJavaScript(script, result.append)
        self.wait_for(lambda: bool(result))
        return result[0]

    def click(self, dialog, identifier):
        self.value(
            dialog, f"document.getElementById({json.dumps(identifier)}).click(); true"
        )

    def input(self, dialog, key, value):
        self.value(
            dialog,
            f"document.getElementById({json.dumps(key)}).value = {json.dumps(value)}; document.getElementById({json.dumps(key)}).dispatchEvent(new Event('input')); true",
        )
        self.wait_for(lambda: dialog.state["preferences"][key] == value)

    def native_command(self, dialog, action, **values):
        dialog._on_command(
            {
                "context": dialog.state["context"],
                "sequence": dialog._last_sequence + 1,
                "action": action,
                **values,
            }
        )

    def profile_row(self, dialog, name):
        return next(p for p in dialog.state["profiles"] if p["name"] == name)

    def profile_click(self, dialog, name, action):
        token = self.profile_row(dialog, name)["id"]
        self.value(
            dialog,
            f'document.querySelector(\'[data-profile="{token}"] button[data-action="{action}"]\').click(); true',
        )

    def test_sections_initial_values_nonmodal_reuse_and_cancel(self):
        window, dialog = self.loaded()
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.NonModal)
        self.assertEqual(
            self.value(
                dialog,
                "[...document.querySelectorAll('[data-section]')].map(n => n.textContent)",
            ),
            ["General", "Profile", "Tabs", "View", "Cookies", "Agents", "Everything"],
        )
        self.assertEqual(set(dialog.state["preferences"]), set(DEFAULTS))
        before = self.config.config_file.read_bytes()
        self.input(dialog, "home_page", "https://unsaved.example.test/")
        window._create_profile()
        self.assertIs(window.settings_dialog, dialog)
        self.assertEqual(dialog.state["section"], "profile")
        self.assertEqual(
            dialog.state["preferences"]["home_page"], "https://unsaved.example.test/"
        )
        self.click(dialog, "cancel")
        self.wait_for(lambda: window.settings_dialog is None)
        self.assertEqual(self.config.config_file.read_bytes(), before)

    def test_initial_size_fits_a_small_logical_desktop(self):
        window = BrowserWindow()
        self.windows.append(window)
        with patch.object(SettingsDialog, "screen") as screen:
            screen.return_value.availableGeometry.return_value = QRect(0, 0, 800, 600)
            window._open_settings()
        dialog = window.settings_dialog
        self.assertLessEqual(dialog.width(), 752)
        self.assertLessEqual(dialog.height(), 552)

    def test_save_once_updates_all_windows_and_keeps_unknown_preferences(self):
        window, dialog = self.loaded()
        second = BrowserWindow()
        self.windows.append(second)
        self.config.set("custom_future_preference", 42)
        changes = {
            "home_page": "https://home.example.test/",
            "new_tab_page": "steed:welcome",
            "search_engine": "https://search.example.test/?q=",
            "dark_mode": True,
            "restore_tabs_on_startup": False,
            "google_login_compatibility": False,
            "user_agent_override": "SyntheticSteed/1",
            "global_cookie_sharing_mode": "all_profiles",
            "global_cookie_sharing_apply_on_startup": False,
            "default_profile": "work",
        }
        for key, value in changes.items():
            dialog.set_preference(key, value)
        self.wait_for(
            lambda: self.value(dialog, "state.preferences.default_profile === 'work'")
        )
        with patch.object(
            self.config, "save_config", wraps=self.config.save_config
        ) as save:
            self.click(dialog, "save")
            self.wait_for(lambda: window.settings_dialog is None)
        save.assert_called_once()
        for key, value in changes.items():
            self.assertEqual(self.config.get(key), value)
        self.assertEqual(self.config.get("custom_future_preference"), 42)
        self.assertTrue(themed(window))
        self.assertEqual(window.styleSheet(), second.styleSheet())
        self.assertEqual(self.profile.httpUserAgent(), "SyntheticSteed/1")

    def test_blank_search_engine_restores_the_configured_default(self):
        window, dialog = self.loaded()
        self.assertEqual(self.config.get("search_engine"), DEFAULT_SEARCH_ENGINE)
        dialog.set_preference("search_engine", " ")
        self.click(dialog, "save")
        self.wait_for(lambda: window.settings_dialog is None)
        self.assertEqual(self.config.get("search_engine"), DEFAULT_SEARCH_ENGINE)

    def test_developer_tools_dock_side_is_a_saved_preference(self):
        window, dialog = self.loaded()
        with self.assertRaises(ValueError):
            dialog.set_preference("devtools_side", "floating")
        dialog.set_preference("devtools_side", "bottom")
        self.click(dialog, "save")
        self.wait_for(lambda: window.settings_dialog is None)
        self.assertEqual(self.config.get("devtools_side"), "bottom")
        self.wait_for(lambda: window.devtools.side == "bottom")

    def test_failed_disk_write_keeps_saved_values_and_visible_draft(self):
        _, dialog = self.loaded()
        before = self.config.config_file.read_bytes()
        self.input(dialog, "home_page", "https://draft.example.test/")
        with patch.object(self.config, "save_config", side_effect=OSError("synthetic")):
            self.click(dialog, "save")
            self.wait_for(lambda: bool(dialog.state["error"]))
        self.assertEqual(self.config.config_file.read_bytes(), before)
        self.assertEqual(
            dialog.state["preferences"]["home_page"], "https://draft.example.test/"
        )
        self.assertTrue(self.value(dialog, "!document.getElementById('error').hidden"))
        self.assertFalse(dialog._closed)

    def test_conflicting_edits_stop_save_independent_edits_merge(self):
        first_window, first = self.loaded()
        _, second = self.loaded()
        first.set_preference("home_page", "https://first.example.test/")
        second.set_preference("home_page", "https://second.example.test/")
        second.apply()
        with self.assertRaisesRegex(ValueError, "another window"):
            first.apply()
        self.assertEqual(
            first.state["preferences"]["home_page"], "https://first.example.test/"
        )
        self.assertTrue(first.state["external_changes"])
        self.native_command(first, "reset")
        first.set_preference("dark_mode", True)
        second.set_preference("new_tab_page", "steed:welcome")
        second.apply()
        first.apply()
        self.assertEqual(self.config.get("home_page"), "https://second.example.test/")
        self.assertEqual(self.config.get("new_tab_page"), "steed:welcome")
        self.assertTrue(self.config.dark_mode)
        self.assertTrue(themed(first_window))

    def test_html_preview_reload_and_cancel_do_not_save_appearance(self):
        window, dialog = self.loaded(section="view")
        before = self.config.dark_mode
        self.click(dialog, "dark_mode")
        self.wait_for(
            lambda: self.value(
                dialog, "document.documentElement.dataset.theme === 'dark'"
            )
        )
        self.assertEqual(self.config.dark_mode, before)
        self.assertFalse(themed(window))
        self.click(dialog, "reset")
        self.wait_for(
            lambda: self.value(
                dialog, "document.documentElement.dataset.theme === 'light'"
            )
        )
        self.assertEqual(dialog.state["preferences"]["dark_mode"], before)

    def test_create_switch_once_preserves_settings_draft_without_saving_it(self):
        window, dialog = self.loaded(section="profile")
        dialog.set_preference("home_page", "https://not-saved.example.test/")
        self.value(
            dialog,
            "document.getElementById('new-profile-name').value = 'Research'; true",
        )
        with patch.object(
            window.profile_manager,
            "create_profile",
            wraps=window.profile_manager.create_profile,
        ) as create:
            self.value(
                dialog,
                "document.getElementById('create-profile').click(); document.getElementById('create-profile').click(); true",
            )
            self.wait_for(
                lambda: window.profile_manager.current_profile_name == "Research"
            )
        create.assert_called_once_with("Research")
        self.assertIn("Research", window.profile_manager.list_profiles())
        self.assertEqual(
            dialog.state["preferences"]["home_page"], "https://not-saved.example.test/"
        )
        dialog.reject()
        self.assertEqual(self.config.get("home_page"), "about:blank")
        self.assertIn("Research", window.profile_manager.list_profiles())

    def test_profile_switch_and_new_window_keep_shared_engine_ownership(self):
        window, dialog = self.loaded(section="profile")
        self.profile_click(dialog, "work", "switch")
        self.wait_for(lambda: window.profile_manager.current_profile_name == "work")
        self.wait_for(lambda: self.value(dialog, "state.current_profile === 'work'"))
        self.profile_click(dialog, "work", "open_window")
        self.wait_for(lambda: bool(window._child_windows))
        child = window._child_windows[-1]
        self.windows.append(child)
        self.assertIs(
            child.profile_manager.current_profile,
            window.profile_manager.current_profile,
        )
        self.assertEqual(child.profile_manager.current_profile_name, "work")

    def test_rename_review_can_be_cancelled_then_consumed_once(self):
        _, dialog = self.loaded(section="profile")
        identity = self.registry.approval_profile_key("existing:work", create=True)
        self.profile_click(dialog, "work", "rename_form")
        self.value(
            dialog, "document.getElementById('rename-name').value = 'Renamed'; true"
        )
        self.click(dialog, "review-rename")
        self.wait_for(lambda: dialog.state["review"] is not None)
        self.assertIn("work", self.manager.list_profiles())
        self.click(dialog, "cancel-review")
        self.wait_for(lambda: dialog.state["review"] is None)
        self.click(dialog, "review-rename")
        self.wait_for(lambda: dialog.state["review"] is not None)
        review_id = dialog.state["review"]["id"]
        self.click(dialog, "confirm-review")
        self.wait_for(lambda: "Renamed" in self.manager.list_profiles())
        self.assertNotIn("work", self.manager.list_profiles())
        self.assertEqual(
            self.registry.approval_profile_key("existing:Renamed"), identity
        )
        self.native_command(dialog, "confirm", review=review_id)
        self.assertTrue(dialog.state["error"])
        self.assertIn("Renamed", self.manager.list_profiles())

    def test_archive_is_reviewed_and_recoverable(self):
        _, dialog = self.loaded(section="profile")
        marker = self.manager.get_profile_path("work") / "synthetic-marker"
        marker.write_text("recover me")
        self.profile_click(dialog, "work", "archive")
        self.wait_for(lambda: dialog.state["review"] is not None)
        self.assertTrue(marker.exists())
        self.click(dialog, "confirm-review")
        self.wait_for(lambda: "work" not in self.manager.list_profiles())
        recovered = list(
            (self.config.config_dir / "deleted_profiles").glob(
                "work-*/synthetic-marker"
            )
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].read_text(), "recover me")

    def test_retired_engine_writes_cannot_reopen_or_contaminate_a_recreated_profile(
        self,
    ):
        _, dialog = self.loaded(section="profile")
        old_storage = Path(self.profile.persistentStoragePath())
        self.profile_click(dialog, "work", "archive")
        self.wait_for(lambda: dialog.state["review"] is not None)
        self.click(dialog, "confirm-review")
        self.wait_for(lambda: "work" not in self.manager.list_profiles())
        old_storage.mkdir(parents=True, exist_ok=True)
        (old_storage / "late-engine-fixture").write_text("old identity")
        self.assertNotIn("work", self.manager.list_profiles())
        with self.assertRaisesRegex(ValueError, "renamed or archived"):
            self.registry.get("work")
        self.assertTrue(self.manager.create_profile("work"))
        fresh = self.registry.get("work")
        self.assertNotEqual(Path(fresh.persistentStoragePath()), old_storage)
        self.assertFalse(
            (Path(fresh.persistentStoragePath()) / "late-engine-fixture").exists()
        )
        self.assertIn("work", self.manager.list_profiles())

    def test_other_window_opening_profile_blocks_pending_archive(self):
        _, dialog = self.loaded(section="profile")
        self.profile_click(dialog, "work", "archive")
        self.wait_for(lambda: dialog.state["review"] is not None)
        other = BrowserWindow(profile_name="work")
        self.windows.append(other)
        self.click(dialog, "confirm-review")
        self.wait_for(lambda: "Switch those windows" in dialog.state["error"])
        self.assertIn("work", self.manager.list_profiles())

    def test_stale_profile_and_startup_choice_do_not_retarget_recreated_name(self):
        _, dialog = self.loaded(section="profile")
        token = self.profile_row(dialog, "work")["id"]
        dialog.set_preference("default_profile", "work")
        self.native_command(dialog, "archive", profile=token)
        review = dialog.state["review"]["id"]
        self.assertTrue(self.manager.rename_profile("work", "Original"))
        self.assertTrue(self.manager.create_profile("work"))
        self.native_command(dialog, "confirm", review=review)
        self.assertIn("changed", dialog.state["error"])
        with self.assertRaisesRegex(ValueError, "startup profile changed"):
            dialog.apply()
        self.assertEqual(self.config.default_profile, "global")
        self.assertIn("work", self.manager.list_profiles())

    def test_builtin_active_default_and_task_profiles_explain_mutation_blocks(self):
        _, dialog = self.loaded(section="profile")
        for name in ("global", "search"):
            self.assertFalse(self.profile_row(dialog, name)["can_mutate"])
        self.config.set("default_profile", "work")
        dialog.refresh()
        self.assertIn("startup profile", self.profile_row(dialog, "work")["reason"])
        self.config.set("default_profile", "global")
        self.registry.task_profiles["work"] = "synthetic-task"
        try:
            dialog.refresh()
            self.assertIn("agent task", self.profile_row(dialog, "work")["reason"])
        finally:
            self.registry.task_profiles.pop("work")

    def test_malformed_commands_do_not_write_or_create_profiles(self):
        _, dialog = self.loaded()
        before = self.config.config_file.read_bytes()
        common = {"context": dialog.state["context"], "sequence": 1}
        for sequence, command in enumerate(
            (
                {**common, "action": "create", "name": "Never", "extra": True},
                {
                    **common,
                    "action": "edit",
                    "key": "unknown",
                    "value": True,
                    "version": 1,
                },
                {
                    **common,
                    "action": "edit",
                    "key": "dark_mode",
                    "value": "true",
                    "version": 1,
                },
                {**common, "action": "create", "name": "../outside"},
                {**common, "action": ["save"]},
                {**common, "context": -1, "action": "create", "name": "Never"},
            ),
            1,
        ):
            command["sequence"] = sequence
            dialog._on_command(command)
        self.assertEqual(self.config.config_file.read_bytes(), before)
        self.assertNotIn("Never", self.manager.list_profiles())
        self.assertEqual(dialog.state["preferences"]["dark_mode"], False)

    def test_reload_keeps_preferences_drops_review_and_rejects_old_document(self):
        _, dialog = self.loaded(section="profile")
        dialog.set_preference("home_page", "https://draft.example.test/")
        self.native_command(
            dialog, "archive", profile=self.profile_row(dialog, "work")["id"]
        )
        self.assertIsNotNone(dialog.state["review"])
        generation = dialog.surface.generation
        context = dialog.state["context"]
        dialog.surface._failed()
        dialog.surface.reload()
        self.wait_for(lambda: dialog.surface.ready)
        self.wait_for(
            lambda: self.value(
                dialog,
                "document.getElementById('home_page').value === 'https://draft.example.test/'",
            )
        )
        self.assertIsNone(dialog.state["review"])
        dialog.surface._dispatch(
            json.dumps(
                {
                    "generation": generation,
                    "context": context,
                    "sequence": 100,
                    "action": "create",
                    "name": "Never",
                }
            )
        )
        self.assertNotIn("Never", self.manager.list_profiles())
        self.assertEqual(self.config.get("home_page"), "about:blank")

    def test_private_transition_preserves_global_draft_with_clear_scope_notice(self):
        window, dialog = self.loaded()
        self.input(dialog, "home_page", "https://draft.example.test/")
        window._switch_to_profile("search")
        self.assertIs(dialog.surface.stack.currentWidget(), dialog.surface.fallback)
        self.wait_for(
            lambda: self.value(dialog, "!document.getElementById('private').hidden")
        )
        self.assertEqual(
            dialog.state["preferences"]["home_page"], "https://draft.example.test/"
        )
        self.assertEqual(self.config.get("home_page"), "about:blank")

    def test_window_shutdown_disposes_dialog_page_before_profile(self):
        window, dialog = self.loaded()
        page, profile = dialog.surface.page, dialog.surface.profile
        window.close()
        self.assertIsNone(window.settings_dialog)
        self.assertTrue(sip.isdeleted(page))
        self.assertTrue(sip.isdeleted(profile))
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
