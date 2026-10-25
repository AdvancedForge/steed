"""Native shelves keep existing action state, bounded configuration and recovery."""

import json
import unittest
from copy import deepcopy

import qt_test_support
import test_task_profiles as profiles
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWebEngineCore import QWebEngineSettings

from browser_window import BrowserWindow
from layout_components import PANEL_ACTIONS
from layout_format import LAYOUT_EXAMPLE, PANEL_EXAMPLE, export_layout, parse_layout
from layout_preferences import LayoutPreferences
from ui.layout_panel import QuickActionsPanel
from ui.layout_panel_editor import PanelEditor


class PanelFormatTests(unittest.TestCase):
    def document(self):
        doc = deepcopy(LAYOUT_EXAMPLE)
        doc["layout"]["panels"] = [deepcopy(PANEL_EXAMPLE)]
        return doc

    def test_old_portable_and_stored_layouts_migrate_without_new_controls(self):
        old = json.loads(export_layout(LayoutPreferences()))
        old["version"] = 1
        del old["layout"]["panels"]
        del old["layout"]["text_size"]
        definition = parse_layout(json.dumps(old))
        self.assertEqual(definition.preferences(LayoutPreferences()).panels, [])
        preferences = LayoutPreferences({"version": 1, "layout": old["layout"]})
        self.assertEqual(preferences.text_size, "normal")
        self.assertEqual(preferences.to_dict()["version"], 2)

    def test_unknown_actions_callbacks_panel_limits_and_hidden_unicode_are_refused(
        self,
    ):
        mutations = [
            lambda d: d["layout"]["panels"][0].update(callback="os.system"),
            lambda d: d["layout"]["panels"][0].update(kind="html"),
            lambda d: d["layout"]["panels"][0].update(actions=["grant_approve"]),
            lambda d: d["layout"]["panels"][0].update(actions=[]),
            lambda d: d["layout"]["panels"][0].update(actions=list(PANEL_ACTIONS)),
            lambda d: d["layout"]["panels"][0].update(title="Misleading\u202elabel"),
            lambda d: d["layout"].update(text_size=999),
            lambda d: d["layout"]["panels"].append(deepcopy(PANEL_EXAMPLE)),
            lambda d: d["layout"].update(panels=[deepcopy(PANEL_EXAMPLE)] * 3),
        ]
        for mutation in mutations:
            doc = self.document()
            mutation(doc)
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                parse_layout(json.dumps(doc))

    def test_stored_unknown_components_are_inert_and_returned_panels_are_copies(self):
        doc = self.document()
        preferences = LayoutPreferences({"version": 2, "layout": doc["layout"]})
        returned = preferences.panels
        returned[0]["actions"].append("arbitrary_script")
        self.assertNotIn("arbitrary_script", preferences.panels[0]["actions"])
        doc["layout"]["panels"][0]["kind"] = "script"
        self.assertEqual(
            LayoutPreferences({"version": 2, "layout": doc["layout"]}).panels, []
        )


class NativePanelTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_test_support.application()
        self.actions = {name: QAction(label) for name, label in PANEL_ACTIONS.items()}
        self.panel = None
        self.editor = None

    def tearDown(self):
        if self.panel is not None:
            sip.delete(self.panel)
        if self.editor is not None:
            sip.delete(self.editor)

    def test_creation_is_inert_and_click_uses_the_existing_action(self):
        observed = []
        self.actions["reload"].triggered.connect(observed.append)
        self.panel = QuickActionsPanel(PANEL_EXAMPLE, self.actions)
        self.assertEqual(observed, [])
        self.panel.buttons["reload"].click()
        self.assertEqual(len(observed), 1)
        self.actions["reload"].setEnabled(False)
        self.assertFalse(self.panel.buttons["reload"].isEnabled())
        self.panel.buttons["reload"].click()
        self.assertEqual(len(observed), 1)

    def test_checkable_state_tracks_action_without_triggering_it(self):
        action = self.actions["agents"]
        action.setCheckable(True)
        self.panel = QuickActionsPanel(PANEL_EXAMPLE, self.actions)
        action.setChecked(True)
        self.assertTrue(self.panel.buttons["agents"].isChecked())
        self.panel.buttons["agents"].click()
        self.assertFalse(action.isChecked())

    def test_small_panel_scrolls_to_keyboard_focus(self):
        definition = deepcopy(PANEL_EXAMPLE)
        definition["actions"] = list(PANEL_ACTIONS)[:8]
        self.panel = QuickActionsPanel(definition, self.actions)
        self.panel.resize(360, 120)
        self.panel.show()
        self.app.processEvents()
        self.assertGreater(self.panel.scroll.horizontalScrollBar().maximum(), 0)
        last = self.panel.buttons[definition["actions"][-1]]
        last.setFocus()
        self.app.processEvents()
        self.assertGreater(self.panel.scroll.horizontalScrollBar().value(), 0)
        self.assertEqual(
            last.accessibleName(), PANEL_ACTIONS[definition["actions"][-1]]
        )

    def test_deleted_panel_does_not_leave_action_receivers(self):
        self.panel = QuickActionsPanel(PANEL_EXAMPLE, self.actions)
        sip.delete(self.panel)
        self.panel = None
        self.actions["reload"].setEnabled(False)
        self.app.processEvents()

    def test_editor_preserves_order_and_ids_and_bounds_panel_count(self):
        self.editor = PanelEditor()
        definition = deepcopy(PANEL_EXAMPLE)
        definition["actions"] = ["agents", "find", "reload"]
        self.editor.set_panels([definition])
        self.editor.title.setText("Renamed")
        self.editor._edit()
        self.assertEqual(self.editor.panels[0]["id"], definition["id"])
        self.assertEqual(self.editor.panels[0]["actions"], definition["actions"])
        self.editor.add_panel()
        self.editor.add_panel()
        self.assertEqual(len(self.editor.panels), 2)
        self.assertFalse(self.editor.add_button.isEnabled())
        self.editor.remove_panel()
        self.assertEqual(len(self.editor.panels), 1)

    def test_editor_restores_an_invalid_action_selection(self):
        self.editor = PanelEditor()
        self.editor.add_panel()
        for row in range(self.editor.actions.count()):
            self.editor.actions.item(row).setCheckState(Qt.CheckState.Unchecked)
        checked = [
            self.editor.actions.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.editor.actions.count())
            if self.editor.actions.item(i).checkState() == Qt.CheckState.Checked
        ]
        self.assertEqual(len(checked), 1)
        self.assertEqual(checked, self.editor.panels[0]["actions"])


class BrowserPanelTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown

    def window(self):
        window = BrowserWindow()
        self.windows.append(window)
        return window

    def preferences(self):
        preferences = LayoutPreferences()
        preferences.set_panels([deepcopy(PANEL_EXAMPLE)])
        return preferences

    def test_click_adds_a_normal_tab_but_applying_panels_never_does(self):
        window = self.window()
        initial = len(window.tabs)
        self.registry.commit_layout(self.preferences())
        self.assertEqual(len(window.tabs), initial)
        self.assertEqual(set(window._native_layout_actions), set(PANEL_ACTIONS))
        window._layout_panels[0].buttons["new_tab"].click()
        self.assertEqual(len(window.tabs), initial + 1)

    def test_native_chat_disables_find_reload_and_resets_panel_on_return(self):
        window = self.window()
        self.registry.commit_layout(self.preferences())
        window._open_agent_tab()
        self.assertFalse(window._layout_panels[0].buttons["reload"].isEnabled())
        self.assertFalse(window._layout_panels[0].buttons["find"].isEnabled())
        window._switch_tab(0)
        self.assertTrue(window._layout_panels[0].buttons["reload"].isEnabled())

    def test_panel_order_font_noncompounding_and_engine_settings_unchanged(self):
        window = self.window()
        engine_font = (
            window.tabs[0]
            .settings()
            .fontSize(QWebEngineSettings.FontSize.DefaultFontSize)
        )
        preferences = self.preferences()
        preferences.set_text_size("large")
        second = deepcopy(PANEL_EXAMPLE)
        second.update(id="second", title="Second", placement="bottom")
        first = deepcopy(PANEL_EXAMPLE)
        first.update(placement="bottom")
        preferences.set_panels([first, second])
        self.registry.commit_layout(preferences)
        font_size = window.font().pointSizeF()
        window.apply_layout(preferences.copy())
        self.assertEqual(window.font().pointSizeF(), font_size)
        self.assertEqual(
            [p.panel_id for p in window._layout_panels], ["daily-tools", "second"]
        )
        self.assertEqual(
            window.tabs[0]
            .settings()
            .fontSize(QWebEngineSettings.FontSize.DefaultFontSize),
            engine_font,
        )
        window.reset_layout()
        self.assertEqual(window._layout_panels, [])
        self.assertLess(window.font().pointSizeF(), font_size)

    def test_manual_panel_preview_cancel_keeps_global_settings_and_other_window(self):
        first, second = self.window(), self.window()

        def cancel(dialog):
            dialog.panel_editor.add_panel()
            self.assertEqual(len(first._layout_panels), 1)
            self.assertEqual(second._layout_panels, [])
            return 0

        tab = first._open_layout_settings()
        dialog = tab.workspace_controller.source
        dialog.done(cancel(dialog))
        self.assertEqual(first._layout_panels, [])
        self.assertIsNone(self.config.get("layout"))

    def test_private_profile_retains_normal_native_actions_without_new_agent_access(
        self,
    ):
        window = self.window()
        self.registry.commit_layout(self.preferences())
        window._switch_to_profile("search")
        self.assertIsNone(self.registry.agent_gateway)
        count = len(window.tabs)
        window._layout_panels[0].buttons["new_tab"].click()
        self.assertEqual(len(window.tabs), count + 1)
        self.assertTrue(window.tabs[-1].profile.isOffTheRecord())
        self.assertFalse(window.file_library_action.isEnabled())

    def test_large_text_survives_dark_theme_and_returns_to_normal(self):
        window = self.window()
        preferences = self.preferences()
        baseline = window.address_bar.reload_btn.font().pointSizeF()
        preferences.set_text_size("large")
        self.registry.commit_layout(preferences)
        large = window.address_bar.reload_btn.font().pointSizeF()
        self.assertGreater(large, baseline)
        self.config.set("dark_mode", True)
        window.apply_dark_mode()
        self.assertEqual(window.address_bar.reload_btn.font().pointSizeF(), large)
        self.assertEqual(
            window._layout_panels[0].buttons["reload"].font().pointSizeF(), large
        )
        window.reset_layout()
        self.assertEqual(window.address_bar.reload_btn.font().pointSizeF(), baseline)


if __name__ == "__main__":
    unittest.main()
