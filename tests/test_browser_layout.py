"""A real browser window honours the saved manual layout.

The layout is presentation only. These checks confirm the window applies and
persists it, keeps Settings and Reset Layout reachable when toolbar buttons are
hidden, and never changes browser, profile or library authority.

Storage validation and the widgets themselves are covered without an engine
profile in test_layout_preferences.py.
"""

import unittest
from unittest.mock import patch

import test_task_profiles as profiles
from PyQt6.QtCore import Qt

from browser.browser_window import BrowserWindow
from browser.layout_preferences import MENU_ITEMS, TOOLBAR_ITEMS, LayoutPreferences
from ui.layout_dialog import MENU_LABELS


class BrowserLayoutTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown

    def window(self) -> BrowserWindow:
        window = BrowserWindow()
        self.windows.append(window)
        return window

    def _menu_titles(self, window):
        return [action.text() for action in window.menuBar().actions()]

    def test_a_new_window_starts_on_todays_defaults(self):
        window = self.window()
        self.assertEqual(
            self._menu_titles(window), [MENU_LABELS[name] for name in MENU_ITEMS]
        )
        self.assertEqual(window.layout_preferences.visible_toolbar, list(TOOLBAR_ITEMS))
        self.assertEqual(window.address_bar.hidden_toolbar, [])

    def test_a_saved_layout_is_applied_when_the_window_opens(self):
        preferences = LayoutPreferences()
        preferences.set_toolbar(TOOLBAR_ITEMS, ["passwords", "zoom"])
        preferences.set_menu_order(
            ["agents", "tabs", "general", "profile", "view", "cookies"]
        )
        preferences.set_density("compact")
        self.config.set("layout", preferences.to_dict())

        window = self.window()
        self.assertEqual(
            self._menu_titles(window),
            ["Agents", "Tabs", "General", "Profile", "View", "History"],
        )
        self.assertEqual(window.address_bar.hidden_toolbar, ["passwords", "zoom"])
        self.assertEqual(window.address_bar.density, "compact")
        self.assertEqual(window.tab_bar.density, "compact")

    def test_menus_keep_everyday_items_and_tuck_the_rest_away(self):
        window = self.window()
        for name, most in (("general", 14), ("view", 24), ("tabs", 18)):
            menu = window._layout_menus[name]
            items = [a for a in menu.actions() if not a.isSeparator() and a.isVisible()]
            self.assertLessEqual(len(items), most, name)
        view = {a.text(): a for a in window._layout_menus["view"].actions()}
        advanced = view["Advanced"].menu()
        source = next(a for a in advanced.actions() if a.text() == "View Page Source")
        # Tucked away, still a shortcut and still in the command palette.
        self.assertIn(source, window.actions())
        self.assertEqual(source.shortcut().toString(), "Ctrl+U")
        from ui.command_palette import menu_commands

        found = {
            (path, text) for path, text, _action in menu_commands(window._layout_menus)
        }
        self.assertIn(("View › Advanced", "View Page Source"), found)

    def test_page_bars_share_the_toolbars_look(self):
        window = self.window()
        colors = window.address_bar.colors
        bars = (
            window.password_assistant.bar,
            window.passkey_bar,
            window.store_bar,
            window.page_access_bar,
        )
        for bar in bars:
            sheet = bar.styleSheet()
            self.assertIn(colors["toolbar"], sheet)
            self.assertIn('QPushButton[primary="true"]', sheet)
            self.assertIn(colors["accent"], sheet)

    def test_hiding_toolbar_buttons_leaves_settings_and_reset_reachable(self):
        window = self.window()
        preferences = window.layout_preferences
        preferences.set_toolbar(TOOLBAR_ITEMS, list(TOOLBAR_ITEMS))
        window.apply_layout(preferences)

        general = window._layout_menus["general"]
        view = window._layout_menus["view"]
        general_titles = [action.text() for action in general.actions()]
        view_titles = [action.text() for action in view.actions()]
        self.assertIn("Settings…", general_titles)
        self.assertIn("Layout Settings…", general_titles)
        self.assertIn("Reset Layout", view_titles)
        # Hidden buttons keep a menu route to the same action.
        tabs_titles = [
            action.text() for action in window._layout_menus["tabs"].actions()
        ]
        self.assertIn("Home", tabs_titles)
        self.assertIn("Reload", tabs_titles)

    def test_home_from_the_menu_still_navigates_with_the_button_hidden(self):
        window = self.window()
        preferences = window.layout_preferences
        preferences.set_toolbar(TOOLBAR_ITEMS, ["home"])
        window.apply_layout(preferences)
        with unittest.mock.patch.object(window, "_navigate_to_url") as navigate:
            window._go_home()
        navigate.assert_called_once_with(self.config.get("home_page"))

    def test_reset_layout_restores_defaults_and_saves_them(self):
        window = self.window()
        preferences = window.layout_preferences
        preferences.set_density("compact")
        preferences.set_sidebar("left", 260)
        window.apply_layout(preferences)

        window.reset_layout()
        self.assertEqual(window.layout_preferences.density, "comfortable")
        self.assertEqual(window.layout_preferences.sidebar_side, "right")
        self.assertEqual(window.layout_preferences.sidebar_width, 320)
        stored = LayoutPreferences(self.config.get("layout"))
        self.assertEqual(stored.density, "comfortable")
        self.assertEqual(stored.sidebar_side, "right")

    def test_reset_layout_keeps_saved_named_layouts(self):
        window = self.window()
        preferences = window.layout_preferences
        preferences.set_density("compact")
        preferences.save_named("Compact")
        self.registry.commit_layout(preferences)
        window.reset_layout()
        self.assertEqual(window.layout_preferences.named_layouts, ["Compact"])
        self.assertTrue(window.layout_preferences.apply_named("Compact"))
        self.assertEqual(window.layout_preferences.density, "compact")

    def test_saving_the_dialog_writes_the_edited_layout(self):
        window = self.window()

        def accept_as_compact(dialog):
            dialog.density.setCurrentIndex(0)
            dialog.toolbar_list.item(
                dialog.preferences.toolbar_order.index("back")
            ).setCheckState(Qt.CheckState.Unchecked)
            return 1

        tab = window._open_layout_settings()
        dialog = tab.workspace_controller.source
        dialog.done(accept_as_compact(dialog))

        self.assertEqual(window.address_bar.density, "compact")
        stored = LayoutPreferences(self.config.get("layout"))
        self.assertEqual(stored.density, "compact")
        self.assertEqual(stored.hidden_toolbar, ["back"])

    def test_cancelling_the_dialog_restores_the_saved_layout(self):
        window = self.window()

        def preview_then_cancel(dialog):
            # A live edit reaches the window before the dialog closes.
            dialog.density.setCurrentIndex(0)
            dialog.layout_changed.connect(window.apply_layout)
            return 0

        tab = window._open_layout_settings()
        dialog = tab.workspace_controller.source
        dialog.done(preview_then_cancel(dialog))

        self.assertEqual(window.address_bar.density, "comfortable")
        self.assertIsNone(self.config.get("layout"))

    def test_the_agent_sidebar_docks_to_the_configured_side(self):
        window = self.window()
        preferences = window.layout_preferences
        preferences.set_sidebar("left", 260)
        window.apply_layout(preferences)
        self.assertEqual(
            window._sidebar_dock_area(), Qt.DockWidgetArea.LeftDockWidgetArea
        )

        window._toggle_agent_sidebar(True)
        self.assertIsNotNone(window.agent_sidebar)
        self.assertEqual(
            window.dockWidgetArea(window.agent_sidebar),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )

        preferences.set_sidebar("right", 400)
        window.apply_layout(preferences)
        self.assertEqual(
            window.dockWidgetArea(window.agent_sidebar),
            Qt.DockWidgetArea.RightDockWidgetArea,
        )

    def test_a_layout_change_does_not_touch_browser_or_library_authority(self):
        window = self.window()
        registry = window.profile_manager.registry
        before = dict(registry.agent_context())
        self.assertTrue(window.file_library_action.isEnabled())

        preferences = window.layout_preferences
        preferences.set_sidebar("left", 260)
        preferences.set_density("compact")
        preferences.set_toolbar(TOOLBAR_ITEMS, ["passwords"])
        window.apply_layout(preferences)

        self.assertEqual(registry.agent_context(), before)
        task = registry.tasks.store.create("Synthetic layout authority check")
        self.assertEqual(registry.tasks.store.grants(task.task_id), [])
        self.assertTrue(window.file_library_action.isEnabled())
        # Private browsing still hides the library entry point.
        window._switch_to_profile("search")
        self.assertFalse(window.file_library_action.isEnabled())

    def test_a_malformed_layout_never_blocks_startup(self):
        self.config.set("layout", {"version": "one", "named": ["not", "a", "dict"]})
        window = self.window()
        self.assertEqual(
            self._menu_titles(window), [MENU_LABELS[name] for name in MENU_ITEMS]
        )
        self.assertEqual(window.layout_preferences.density, "comfortable")
        self.assertEqual(window.layout_preferences.named_layouts, [])

    def test_save_from_second_window_preserves_first_windows_named_layout(self):
        first, second = self.window(), self.window()

        def save_first(dialog):
            dialog.sidebar_side.setCurrentIndex(0)
            dialog.preferences.save_named("Shared layout")
            return 1

        tab = first._open_layout_settings()
        dialog = tab.workspace_controller.source
        dialog.done(save_first(dialog))
        self.assertEqual(second.layout_preferences.sidebar_side, "left")

        def save_second(dialog):
            self.assertEqual(dialog.preferences.named_layouts, ["Shared layout"])
            dialog.density.setCurrentIndex(0)
            return 1

        tab = second._open_layout_settings()
        dialog = tab.workspace_controller.source
        dialog.done(save_second(dialog))
        stored = LayoutPreferences(self.config.get("layout"))
        self.assertEqual(stored.sidebar_side, "left")
        self.assertEqual(stored.named_layouts, ["Shared layout"])
        self.assertEqual(first.layout_preferences.density, "compact")

    def test_cancel_preview_keeps_other_windows_committed_changes(self):
        first, second = self.window(), self.window()

        def cancel_after_external_commit(dialog):
            dialog.density.setCurrentIndex(0)
            self.assertEqual(first.layout_preferences.density, "compact")
            self.assertEqual(second.layout_preferences.density, "comfortable")
            external = LayoutPreferences(self.config.get("layout"))
            external.set_sidebar("left", 400)
            self.registry.commit_layout(external)
            return 0

        tab = first._open_layout_settings()
        dialog = tab.workspace_controller.source
        dialog.done(cancel_after_external_commit(dialog))
        self.assertEqual(first.layout_preferences.density, "comfortable")
        self.assertEqual(first.layout_preferences.sidebar_side, "left")
        self.assertEqual(second.layout_preferences.sidebar_side, "left")

    def test_concurrent_unrelated_commit_merges_and_conflict_does_not_overwrite(self):
        window = self.window()

        def accept_with_concurrent_change(dialog):
            dialog.sidebar_width.setValue(400)
            external = LayoutPreferences(self.config.get("layout"))
            external.save_named("Other window")
            self.registry.commit_layout(external)
            return 1

        tab = window._open_layout_settings()
        dialog = tab.workspace_controller.source
        dialog.done(accept_with_concurrent_change(dialog))
        self.assertEqual(window.layout_preferences.named_layouts, ["Other window"])
        self.assertEqual(window.layout_preferences.sidebar_width, 400)

        def accept_conflicting_change(dialog):
            dialog.sidebar_width.setValue(500)
            external = LayoutPreferences(self.config.get("layout"))
            external.set_sidebar("right", 600)
            self.registry.commit_layout(external)
            return 1

        with (
            patch("browser.browser_window.QMessageBox.warning") as warning,
        ):
            tab = window._open_layout_settings()
            dialog = tab.workspace_controller.source
            dialog.done(accept_conflicting_change(dialog))
        warning.assert_called_once()
        self.assertEqual(window.layout_preferences.sidebar_width, 600)
        self.assertEqual(window.layout_preferences.named_layouts, ["Other window"])

    def test_reset_propagates_to_all_windows_and_retains_presets(self):
        first, second = self.window(), self.window()
        preferences = LayoutPreferences()
        preferences.set_density("compact")
        preferences.save_named("Compact")
        self.registry.commit_layout(preferences)
        second.reset_layout()
        self.assertEqual(first.layout_preferences.density, "comfortable")
        self.assertEqual(first.layout_preferences.named_layouts, ["Compact"])

    def test_populated_sidebar_honors_bounds_and_survives_tab_presentation(self):
        window = self.window()
        window.show()
        preferences = LayoutPreferences()
        preferences.set_sidebar("left", 260)
        self.registry.commit_layout(preferences)
        window._toggle_agent_sidebar(True)
        self.app.processEvents()
        self.assertEqual(window.agent_sidebar.width(), 300)
        window._open_agent_tab()
        preferences.set_sidebar("right", 400)
        self.registry.commit_layout(preferences)
        window._toggle_agent_sidebar(True)
        self.app.processEvents()
        self.assertEqual(window.agent_sidebar.width(), 400)
        self.assertEqual(
            window.dockWidgetArea(window.agent_sidebar),
            Qt.DockWidgetArea.RightDockWidgetArea,
        )


if __name__ == "__main__":
    unittest.main()
