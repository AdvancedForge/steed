"""Manual layout settings: validated storage and the chrome they control.

These checks use the saved-layout model and the widgets it drives, so they run
without a WebEngine profile. Browser window integration is covered separately in
test_browser_layout.py.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import qt_test_support
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from browser_window import BrowserWindow, reorder_menus
from config import Config
from layout_preferences import (
    LAYOUT_VERSION,
    MAX_LAYOUT_NAME,
    MAX_NAMED_LAYOUTS,
    MENU_ITEMS,
    SIDEBAR_WIDTH_RANGE,
    TOOLBAR_ITEMS,
    TOOLBAR_ORDER,
    LayoutPreferences,
    preferences_from_config,
    save_preferences,
)
from ui.address_bar import AddressBar
from ui.layout_dialog import LayoutDialog
from ui.tab_strip import TabStrip


def _item_ids(list_widget):
    return [
        list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(list_widget.count())
    ]


class LayoutPreferenceStorageTests(unittest.TestCase):
    """Stored settings are validated before anything reads them."""

    def test_defaults_match_todays_menu_order_and_toolbar(self):
        preferences = LayoutPreferences()
        self.assertEqual(preferences.menu_order, list(MENU_ITEMS))
        self.assertEqual(
            preferences.menu_order,
            ["general", "profile", "tabs", "view", "cookies", "agents"],
        )
        self.assertEqual(preferences.toolbar_order, list(TOOLBAR_ORDER))
        self.assertEqual(preferences.hidden_toolbar, [])
        self.assertEqual(preferences.visible_toolbar, list(TOOLBAR_ITEMS))
        self.assertEqual(preferences.sidebar_side, "right")
        self.assertEqual(preferences.density, "comfortable")
        self.assertIsNone(preferences.active_layout)

    def test_non_dict_documents_fall_back_to_defaults(self):
        for stored in (None, "layout", 7, [], {"layout": "compact"}):
            preferences = LayoutPreferences(stored)
            self.assertEqual(preferences.menu_order, list(MENU_ITEMS))
            self.assertEqual(preferences.density, "comfortable")

    def test_unknown_schema_version_is_ignored(self):
        preferences = LayoutPreferences(
            {"version": LAYOUT_VERSION + 1, "layout": {"density": "compact"}}
        )
        self.assertEqual(preferences.density, "comfortable")
        missing = LayoutPreferences({"layout": {"density": "compact"}})
        self.assertEqual(missing.density, "comfortable")
        boolean = LayoutPreferences({"version": True, "layout": {"density": "compact"}})
        self.assertEqual(boolean.density, "comfortable")

    def test_unknown_identifiers_are_dropped_and_missing_restored(self):
        preferences = LayoutPreferences(
            {
                "version": LAYOUT_VERSION,
                "layout": {
                    "toolbar": {
                        "order": ["zoom", "nonsense", "home", "zoom"],
                        "hidden": ["nope", "back", "back"],
                    },
                    "menus": {"order": ["agents", "missing", "general"]},
                },
            }
        )
        self.assertEqual(
            preferences.toolbar_order,
            [
                "menu",
                "address",
                "zoom",
                "home",
                "back",
                "forward",
                "reload",
                "passwords",
                "downloads",
                "media",
                "extensions",
            ],
        )
        self.assertEqual(preferences.hidden_toolbar, ["back"])
        self.assertNotIn("nonsense", preferences.visible_toolbar)
        self.assertEqual(
            preferences.menu_order,
            ["agents", "general", "profile", "tabs", "view", "cookies"],
        )

    def test_sidebar_width_is_bounded_and_type_checked(self):
        low, high = SIDEBAR_WIDTH_RANGE
        stored = {"version": LAYOUT_VERSION, "layout": {"sidebar": {}}}
        for value, expected in (
            (1, low),
            (99999, high),
            ("wide", 320),
            (None, 320),
            (True, 320),
            (12.5, 320),
        ):
            document = {
                "version": LAYOUT_VERSION,
                "layout": {"sidebar": {"width": value}},
            }
            self.assertEqual(LayoutPreferences(document).sidebar_width, expected, value)
        self.assertEqual(LayoutPreferences(stored).sidebar_width, 320)

    def test_unknown_density_and_side_fall_back(self):
        preferences = LayoutPreferences(
            {
                "version": LAYOUT_VERSION,
                "layout": {"density": "enormous", "sidebar": {"side": "top"}},
            }
        )
        self.assertEqual(preferences.density, "comfortable")
        self.assertEqual(preferences.sidebar_side, "right")

    def test_preferences_survive_a_restart(self):
        original = LayoutPreferences()
        original.set_toolbar(
            ["home", "reload", "back", "forward", "zoom", "passwords"], ["forward"]
        )
        original.set_menu_order(
            ["agents", "tabs", "general", "profile", "view", "cookies"]
        )
        original.set_sidebar("left", 300)
        original.set_density("compact")
        restored = LayoutPreferences(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertEqual(restored.visible_toolbar, original.visible_toolbar)
        self.assertEqual(restored.sidebar_width, 300)
        self.assertEqual(restored.sidebar_side, "left")

    def test_round_trip_through_the_shared_configuration_file(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(Path, "home", return_value=home):
                config = Config()
                self.assertEqual(
                    preferences_from_config(config).menu_order, list(MENU_ITEMS)
                )
                preferences = LayoutPreferences()
                preferences.set_density("compact")
                preferences.set_toolbar(TOOLBAR_ITEMS, ["passwords"])
                preferences.save_named("Reading")
                save_preferences(config, preferences)

                # A later process reads the same file.
                reopened = Config()
                stored = preferences_from_config(reopened)
                self.assertEqual(stored.density, "compact")
                self.assertEqual(stored.hidden_toolbar, ["passwords"])
                self.assertEqual(stored.named_layouts, ["Reading"])
                self.assertEqual(stored.active_layout, "Reading")

    def test_a_hand_edited_configuration_never_breaks_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(Path, "home", return_value=home):
                config = Config()
                config.set("layout", {"version": LAYOUT_VERSION, "named": "broken"})
                self.assertEqual(
                    preferences_from_config(config).menu_order, list(MENU_ITEMS)
                )


class NamedLayoutTests(unittest.TestCase):
    """Saved layouts are selectable, bounded and separately removable."""

    def test_save_apply_and_delete_a_named_layout(self):
        preferences = LayoutPreferences()
        preferences.set_density("compact")
        self.assertTrue(preferences.save_named("Compact"))
        self.assertEqual(preferences.active_layout, "Compact")

        preferences.reset()
        self.assertEqual(preferences.density, "comfortable")
        self.assertIsNone(preferences.active_layout)

        self.assertTrue(preferences.apply_named("Compact"))
        self.assertEqual(preferences.density, "compact")
        self.assertEqual(preferences.active_layout, "Compact")

        self.assertTrue(preferences.delete_named("Compact"))
        self.assertEqual(preferences.named_layouts, [])
        self.assertIsNone(preferences.active_layout)
        self.assertFalse(preferences.delete_named("Compact"))

    def test_deleting_a_layout_keeps_the_current_appearance(self):
        preferences = LayoutPreferences()
        preferences.set_sidebar("left")
        preferences.save_named("Left")
        preferences.set_sidebar("right")
        preferences.delete_named("Left")
        self.assertEqual(preferences.sidebar_side, "right")

    def test_saved_layouts_are_capped(self):
        preferences = LayoutPreferences()
        for index in range(MAX_NAMED_LAYOUTS):
            self.assertTrue(preferences.save_named(f"Layout {index}"))
        self.assertFalse(preferences.can_save("One too many"))
        self.assertFalse(preferences.save_named("One too many"))
        self.assertEqual(len(preferences.named_layouts), MAX_NAMED_LAYOUTS)

    def test_over_limit_stored_layouts_are_dropped(self):
        stored = {
            "version": LAYOUT_VERSION,
            "named": {f"Layout {index}": {"density": "compact"} for index in range(60)},
            "active": "Layout 40",
        }
        preferences = LayoutPreferences(stored)
        self.assertEqual(len(preferences.named_layouts), MAX_NAMED_LAYOUTS)
        self.assertIsNone(preferences.active_layout)

    def test_unusable_names_are_refused(self):
        preferences = LayoutPreferences()
        for name in (
            "",
            "   ",
            "x" * (MAX_LAYOUT_NAME + 1),
            "bad\nname",
            "tab\there",
            None,
            5,
        ):
            self.assertFalse(preferences.can_save(name), name)
            self.assertFalse(preferences.save_named(name), name)
        self.assertEqual(preferences.named_layouts, [])

    def test_names_are_trimmed_and_collapse_whitespace(self):
        preferences = LayoutPreferences()
        self.assertTrue(preferences.save_named("  Work   Reading  "))
        self.assertEqual(preferences.named_layouts, ["Work Reading"])
        self.assertEqual(preferences.active_layout, "Work Reading")

    def test_editing_the_appearance_clears_the_active_name(self):
        preferences = LayoutPreferences()
        preferences.save_named("Work")
        preferences.set_density("compact")
        self.assertIsNone(preferences.active_layout)
        self.assertEqual(preferences.named_layouts, ["Work"])

    def test_reapplying_the_same_values_keeps_the_active_name(self):
        preferences = LayoutPreferences()
        preferences.set_density("compact")
        preferences.save_named("Work")
        # Reading the controls back must not look like an edit.
        preferences.set_density("compact")
        preferences.set_sidebar("right", 320)
        self.assertEqual(preferences.active_layout, "Work")

    def test_a_copy_is_independent(self):
        preferences = LayoutPreferences()
        preferences.set_density("compact")
        preferences.save_named("Work")
        duplicate = preferences.copy()
        duplicate.set_density("comfortable")
        duplicate.delete_named("Work")
        self.assertEqual(preferences.density, "compact")
        self.assertEqual(preferences.named_layouts, ["Work"])


class MenuOrderTests(unittest.TestCase):
    """The menu bar can be reordered but never loses a menu."""

    def setUp(self):
        qt_test_support.application()
        self.window = QMainWindow()
        self.menus = {name: self.window.menuBar().addMenu(name) for name in MENU_ITEMS}

    def tearDown(self):
        self.window.deleteLater()

    def _titles(self):
        return [action.text() for action in self.window.menuBar().actions()]

    def test_default_order_is_unchanged(self):
        reorder_menus(self.window.menuBar(), self.menus, MENU_ITEMS)
        self.assertEqual(self._titles(), list(MENU_ITEMS))

    def test_order_follows_the_layout_and_keeps_every_menu(self):
        order = ["agents", "tabs", "general", "profile", "view", "cookies"]
        reorder_menus(self.window.menuBar(), self.menus, order)
        self.assertEqual(self._titles(), order)
        self.assertEqual(sorted(self._titles()), sorted(MENU_ITEMS))

    def test_unknown_and_missing_menus_are_tolerated(self):
        reorder_menus(
            self.window.menuBar(),
            self.menus,
            ["nonsense", "view", "general"],
        )
        titles = self._titles()
        self.assertEqual(titles[:2], ["view", "general"])
        self.assertEqual(sorted(titles), sorted(MENU_ITEMS))

    def test_reordering_twice_does_not_duplicate_menus(self):
        reorder_menus(
            self.window.menuBar(),
            self.menus,
            ["cookies", "general", "profile", "tabs", "view", "agents"],
        )
        reorder_menus(self.window.menuBar(), self.menus, list(MENU_ITEMS))
        self.assertEqual(self._titles(), list(MENU_ITEMS))


class AddressBarLayoutTests(unittest.TestCase):
    """Toolbar order and visibility are driven by stable identifiers."""

    def setUp(self):
        qt_test_support.application()
        self.bar = AddressBar()

    def tearDown(self):
        self.bar.deleteLater()

    def _shown(self):
        layout = self.bar.layout()
        return [
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        ]

    def test_default_order_shows_every_button(self):
        self.assertEqual(self.bar.toolbar_order, list(TOOLBAR_ORDER))
        self.assertEqual(self.bar.hidden_toolbar, [])
        self.assertEqual(
            self._shown(),
            [
                self.bar.menu_btn,
                self.bar.back_btn,
                self.bar.forward_btn,
                self.bar.reload_btn,
                self.bar.home_btn,
                self.bar.address_input,
                self.bar.zoom_btn,
                self.bar.passwords_btn,
                # Placed but hidden until something downloads or plays.
                self.bar.downloads_btn,
                self.bar.media_btn,
                self.bar.extensions_btn,
            ],
        )
        self.assertTrue(self.bar.downloads_btn.isHidden())
        self.bar.show_downloads("↓ 40%", "1 download")
        self.assertFalse(self.bar.downloads_btn.isHidden())
        self.bar.hide_downloads()
        self.assertTrue(self.bar.downloads_btn.isHidden())

    def test_hiding_a_button_removes_it_from_the_bar(self):
        self.bar.show()
        qt_test_support.application().processEvents()
        self.bar.apply_toolbar(TOOLBAR_ITEMS, ["home", "zoom"])
        layout = self.bar.layout()
        visible = {
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        }
        self.assertNotIn(self.bar.home_btn, visible)
        self.assertNotIn(self.bar.zoom_btn, visible)
        self.assertIn(self.bar.back_btn, visible)
        self.assertIn(self.bar.address_input, visible)
        self.assertEqual(self.bar.hidden_toolbar, ["home", "zoom"])
        self.assertTrue(self.bar.home_btn.isHidden())
        self.assertTrue(self.bar.zoom_btn.isHidden())
        self.bar.address_input.setFocus()
        for _ in range(12):
            self.bar.focusNextChild()
            self.assertNotIn(
                qt_test_support.application().focusWidget(),
                [self.bar.home_btn, self.bar.zoom_btn],
            )
        self.bar.apply_toolbar(TOOLBAR_ORDER, [])
        self.assertFalse(self.bar.home_btn.isHidden())
        self.assertFalse(self.bar.zoom_btn.isHidden())

    def test_reordering_keeps_every_button_and_the_address_input(self):
        self.bar.apply_toolbar(
            ["passwords", "back", "home", "forward", "reload", "zoom"], []
        )
        layout = self.bar.layout()
        ordered = [
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        ]
        self.assertEqual(
            ordered,
            [
                self.bar.menu_btn,
                self.bar.passwords_btn,
                self.bar.back_btn,
                self.bar.home_btn,
                self.bar.forward_btn,
                self.bar.reload_btn,
                self.bar.address_input,
                self.bar.zoom_btn,
                self.bar.downloads_btn,
                self.bar.media_btn,
                self.bar.extensions_btn,
            ],
        )

    def test_a_stale_layout_still_shows_every_button(self):
        self.bar.apply_toolbar(["home", "button_from_the_future"], [])
        self.assertEqual(self.bar.toolbar_order[:2], ["menu", "home"])
        self.assertEqual(set(self.bar.toolbar_order), set(TOOLBAR_ORDER))
        self.assertEqual(len(self._shown()), len(TOOLBAR_ITEMS) + 2)

    def test_menu_can_move_across_address_but_cannot_be_hidden(self):
        self.bar.apply_toolbar(["menu", "address", "passwords"], ["menu", "passwords"])
        self.assertEqual(self._shown()[:2], [self.bar.menu_btn, self.bar.address_input])
        self.assertFalse(self.bar.menu_btn.isHidden())
        self.assertNotIn("menu", self.bar.hidden_toolbar)

    def test_density_changes_spacing(self):
        self.bar.apply_density("compact")
        compact = self.bar.layout().spacing()
        self.bar.apply_density("comfortable")
        self.assertEqual(self.bar.density, "comfortable")
        self.assertGreater(self.bar.layout().spacing(), compact)
        self.bar.apply_density("enormous")
        self.assertEqual(self.bar.density, "comfortable")

    def test_hiding_every_button_still_leaves_the_address_input(self):
        self.bar.apply_toolbar(TOOLBAR_ITEMS, list(TOOLBAR_ITEMS))
        layout = self.bar.layout()
        widgets = [
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        ]
        self.assertEqual(widgets, [self.bar.menu_btn, self.bar.address_input])


class TabStripDensityTests(unittest.TestCase):
    def setUp(self):
        qt_test_support.application()
        self.bar = TabStrip()

    def tearDown(self):
        self.bar.dispose()
        self.bar.deleteLater()

    def test_compact_density_shrinks_the_tab_row(self):
        self.bar.apply_density("comfortable")
        comfortable = self.bar.height()
        self.bar.apply_density("compact")
        self.assertLess(self.bar.height(), comfortable)
        self.assertEqual(self.bar.density, "compact")


class LayoutDialogTests(unittest.TestCase):
    """Edits preview immediately; only Save persists them."""

    def setUp(self):
        qt_test_support.application()
        self.preferences = LayoutPreferences()
        self.preferences.save_named("Work")
        self.dialog = LayoutDialog(self.preferences)
        self.published = []
        self.dialog.layout_changed.connect(self.published.append)

    def tearDown(self):
        self.dialog.deleteLater()

    def _last(self):
        self.assertTrue(self.published)
        return self.published[-1]

    def test_dialog_opens_on_the_saved_layout(self):
        self.assertEqual(_item_ids(self.dialog.toolbar_list), list(TOOLBAR_ORDER))
        self.assertEqual(_item_ids(self.dialog.menu_list), list(MENU_ITEMS))
        self.assertEqual(self.dialog.sidebar_side.currentData(), "right")
        self.assertEqual(self.dialog.density.currentData(), "comfortable")
        self.assertEqual(self.dialog.named_layouts.currentText(), "Work")
        self.assertEqual(self.published, [])

    def test_unchecking_a_button_previews_the_hidden_toolbar(self):
        self.dialog.toolbar_list.item(TOOLBAR_ORDER.index("back")).setCheckState(
            Qt.CheckState.Unchecked
        )
        self.assertEqual(self._last().hidden_toolbar, ["back"])
        self.assertNotIn("back", self._last().visible_toolbar)

    def test_moving_a_row_reorders_and_previews(self):
        self.dialog.toolbar_list.setCurrentRow(0)
        self.dialog._move_row(self.dialog.toolbar_list, 1)
        self.assertEqual(_item_ids(self.dialog.toolbar_list)[:2], ["back", "menu"])
        self.assertEqual(self._last().toolbar_order[:2], ["back", "menu"])
        # The address field can move, but cannot be hidden.
        self.dialog._move_row(self.dialog.toolbar_list, -1)
        self.assertEqual(_item_ids(self.dialog.toolbar_list)[:2], ["menu", "back"])

    def test_a_row_cannot_move_past_the_ends(self):
        self.dialog.toolbar_list.setCurrentRow(0)
        self.dialog._move_row(self.dialog.toolbar_list, -1)
        self.assertEqual(_item_ids(self.dialog.toolbar_list), list(TOOLBAR_ORDER))
        self.dialog.toolbar_list.setCurrentRow(len(TOOLBAR_ORDER) - 1)
        self.dialog._move_row(self.dialog.toolbar_list, 1)
        self.assertEqual(_item_ids(self.dialog.toolbar_list), list(TOOLBAR_ORDER))

    def test_sidebar_and_density_edits_preview(self):
        self.dialog.sidebar_side.setCurrentIndex(0)
        self.assertEqual(self._last().sidebar_side, "left")
        self.dialog.sidebar_width.setValue(300)
        self.assertEqual(self._last().sidebar_width, 300)
        self.dialog.density.setCurrentIndex(0)
        self.assertEqual(self._last().density, "compact")

    def test_reset_restores_todays_appearance(self):
        self.dialog.density.setCurrentIndex(0)
        self.dialog.toolbar_list.item(TOOLBAR_ORDER.index("back")).setCheckState(
            Qt.CheckState.Unchecked
        )
        self.dialog.reset_layout()
        self.assertEqual(self._last().menu_order, list(MENU_ITEMS))
        self.assertEqual(self._last().visible_toolbar, list(TOOLBAR_ITEMS))
        self.assertEqual(self._last().density, "comfortable")
        self.assertEqual(_item_ids(self.dialog.toolbar_list), list(TOOLBAR_ORDER))

    def test_apply_saved_layout_loads_it_into_the_controls(self):
        source = LayoutPreferences()
        source.set_density("compact")
        source.set_toolbar(TOOLBAR_ITEMS, ["passwords"])
        source.save_named("Compact")
        dialog = LayoutDialog(source)
        published = []
        dialog.layout_changed.connect(published.append)
        dialog.named_layouts.setCurrentText("Compact")
        dialog.apply_named_layout()
        self.assertEqual(published[-1].density, "compact")
        self.assertEqual(published[-1].hidden_toolbar, ["passwords"])
        self.assertEqual(dialog.density.currentData(), "compact")
        self.assertEqual(
            dialog.toolbar_list.item(TOOLBAR_ORDER.index("passwords")).checkState(),
            Qt.CheckState.Unchecked,
        )
        dialog.deleteLater()

    def test_saving_a_named_layout_records_the_current_edits(self):
        self.dialog.density.setCurrentIndex(0)
        with patch(
            "ui.layout_dialog.QInputDialog.getText", return_value=("Reading", True)
        ):
            self.dialog.save_named_layout()
        self.assertIn("Reading", self.dialog.preferences.named_layouts)
        self.assertEqual(self.dialog.preferences.active_layout, "Reading")
        self.assertEqual(self.dialog.named_layouts.currentText(), "Reading")

    def test_normalized_name_is_selected_after_save(self):
        with patch(
            "ui.layout_dialog.QInputDialog.getText",
            return_value=("  New  Layout  ", True),
        ):
            self.dialog.save_named_layout()
        self.assertEqual(self.dialog.named_layouts.currentText(), "New Layout")
        self.dialog.apply_named_layout()
        self.assertEqual(self.dialog.preferences.active_layout, "New Layout")

    def test_a_refused_name_is_reported_and_changes_nothing(self):
        with (
            patch("ui.layout_dialog.QInputDialog.getText", return_value=("   ", True)),
            patch("ui.layout_dialog.QMessageBox.warning") as warning,
        ):
            self.dialog.save_named_layout()
        warning.assert_called_once()
        self.assertEqual(self.dialog.preferences.named_layouts, ["Work"])

    def test_deleting_a_named_layout_leaves_the_appearance_alone(self):
        self.dialog.density.setCurrentIndex(0)
        self.dialog.delete_named_layout()
        self.assertEqual(self.dialog.preferences.named_layouts, [])
        self.assertEqual(self._last().density, "compact")

    def test_only_save_exposes_the_edited_layout(self):
        self.dialog.density.setCurrentIndex(0)
        self.assertEqual(self.dialog.preferences.density, "compact")


class LayoutWindowHarness(QMainWindow):
    """Apply the window's layout logic without an engine profile.

    The methods are borrowed from BrowserWindow so the checks exercise the
    code the real window runs, including the dock and resize calls that need a
    live window to matter.
    """

    apply_layout = BrowserWindow.apply_layout
    reset_layout = BrowserWindow.reset_layout
    _apply_menu_order = BrowserWindow._apply_menu_order
    _apply_sidebar_layout = BrowserWindow._apply_sidebar_layout
    _sidebar_dock_area = BrowserWindow._sidebar_dock_area
    _apply_layout_panels = BrowserWindow._apply_layout_panels
    _apply_layout_text = BrowserWindow._apply_layout_text
    _place_tab_strip = BrowserWindow._place_tab_strip


class WindowLayoutTests(unittest.TestCase):
    """The window applies a whole layout, and leaves an embedded sidebar alone."""

    def setUp(self):
        qt_test_support.application()
        self.window = LayoutWindowHarness()
        self.window.agent_tab = None
        self.window.agent_sidebar = None
        self.window.address_bar = AddressBar(self.window)
        self.window._layout_menus = {
            name: self.window.menuBar().addMenu(name) for name in MENU_ITEMS
        }
        self.sidebar = QDockWidget("Agents", self.window)
        self.window.agent_sidebar = self.sidebar
        self.window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.sidebar)
        # Dock widths only settle once the window has been laid out, and the
        # central widget needs a minimum width so the dock cannot take it all.
        central = QWidget()
        central.setMinimumWidth(400)
        self.window.setCentralWidget(central)
        self.window.chrome_row = QHBoxLayout(central)
        self.window.chrome_column = QWidget(central)
        self.window.chrome_layout = QVBoxLayout(self.window.chrome_column)
        self.window.chrome_row.addWidget(self.window.chrome_column, 1)
        self.window.tab_bar = TabStrip(self.window.chrome_column)
        self.window.tab_container = QWidget(self.window.chrome_column)
        self.window.content_area = self.window.tab_container
        for widget in (self.window.tab_bar, self.window.tab_container):
            self.window.chrome_layout.addWidget(widget)
        self.window.resize(900, 600)
        self.window.show()
        qt_test_support.application().processEvents()

    def tearDown(self):
        self.window.tab_bar.dispose()
        self.window.deleteLater()

    def test_tab_strip_moves_to_each_edge(self):
        window = self.window
        for position in ("left", "bottom", "right", "top"):
            preferences = LayoutPreferences()
            preferences.set_tabs({"position": position})
            window.apply_layout(preferences)
            in_row = window.chrome_row.indexOf(window.tab_bar)
            in_column = window.chrome_layout.indexOf(window.tab_bar)
            if position == "left":
                self.assertEqual(in_row, 0)
            elif position == "right":
                self.assertEqual(in_row, window.chrome_row.count() - 1)
            elif position == "top":
                self.assertEqual(in_column, 0)
            else:
                self.assertEqual(
                    in_column, window.chrome_layout.indexOf(window.content_area) + 1
                )
            self.assertTrue((in_row >= 0) != (in_column >= 0))
            self.assertEqual(window.tab_bar.vertical, position in ("left", "right"))

    def _menu_titles(self):
        return [action.text() for action in self.window.menuBar().actions()]

    def test_applying_a_layout_changes_menus_toolbar_and_density(self):
        preferences = LayoutPreferences()
        preferences.set_menu_order(
            ["agents", "tabs", "general", "profile", "view", "cookies"]
        )
        preferences.set_toolbar(TOOLBAR_ITEMS, ["passwords", "zoom"])
        preferences.set_density("compact")
        self.window.apply_layout(preferences)

        self.assertEqual(self._menu_titles(), preferences.menu_order)
        self.assertEqual(self.window.address_bar.hidden_toolbar, ["passwords", "zoom"])
        self.assertEqual(self.window.address_bar.density, "compact")
        self.assertEqual(self.window.tab_bar.density, "compact")

    def test_the_docked_sidebar_follows_the_layout(self):
        qt_test_support.application()
        preferences = LayoutPreferences()
        preferences.set_sidebar("left", 300)
        self.window.apply_layout(preferences)
        qt_test_support.application().processEvents()
        self.assertEqual(
            self.window.dockWidgetArea(self.sidebar),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.assertEqual(self.sidebar.width(), 300)

        preferences.set_sidebar("right", 400)
        self.window.apply_layout(preferences)
        qt_test_support.application().processEvents()
        self.assertEqual(
            self.window.dockWidgetArea(self.sidebar),
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.assertEqual(self.sidebar.width(), 400)

    def test_an_embedded_sidebar_is_left_for_its_tab(self):
        self.window.agent_tab = QWidget(self.window)
        self.sidebar.setParent(self.window.agent_tab)
        preferences = LayoutPreferences()
        preferences.set_sidebar("left", 300)
        self.window.apply_layout(preferences)
        self.assertIs(self.sidebar.parent(), self.window.agent_tab)

    def test_reset_restores_every_applied_part(self):
        preferences = LayoutPreferences()
        preferences.set_sidebar("left", 300)
        preferences.set_density("compact")
        preferences.set_toolbar(TOOLBAR_ITEMS, ["passwords"])
        self.window.apply_layout(preferences)

        self.window.layout_preferences.reset()
        self.window.apply_layout(self.window.layout_preferences)
        self.assertEqual(self._menu_titles(), list(MENU_ITEMS))
        self.assertEqual(self.window.address_bar.hidden_toolbar, [])
        self.assertEqual(self.window.address_bar.density, "comfortable")
        self.assertEqual(
            self.window.dockWidgetArea(self.sidebar),
            Qt.DockWidgetArea.RightDockWidgetArea,
        )


if __name__ == "__main__":
    unittest.main()
