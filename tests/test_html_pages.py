"""Maintained page routing, shared CSS, hidden bars and active-tab progress."""

import json
import unittest
from unittest.mock import patch

import test_html_settings as fixtures
import test_task_profiles as task_fixtures
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox

from browser_window import BrowserWindow
from services.page_style import DEFAULTS
from services.start_pages import image_data


class HtmlPageTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.HtmlSettingsTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.HtmlSettingsTests.tearDownClass.__func__)
    setUp = fixtures.HtmlSettingsTests.setUp
    tearDown = fixtures.HtmlSettingsTests.tearDown
    wait_for = fixtures.HtmlSettingsTests.wait_for
    value = fixtures.HtmlSettingsTests.value
    click = fixtures.HtmlSettingsTests.click
    command = task_fixtures.TaskProfileTests.command

    def loaded(self, name="style"):
        window = BrowserWindow()
        self.windows.append(window)
        window.show()
        tab = window._new_tab(url="steed:" + name, return_tab=True)
        controller = tab.workspace_controller
        self.wait_for(lambda: controller.surface.ready)
        self.wait_for(lambda: self.value(controller, "!!state"))
        return window, tab, controller

    def test_style_page_sets_tab_and_toolbar_style_for_every_window(self):
        from layout_preferences import preferences_from_config

        window, _, controller = self.loaded("style")
        other = BrowserWindow()
        self.windows.append(other)
        self.wait_for(
            lambda: self.value(controller, "!!document.getElementById('tab-position')")
        )
        self.assertEqual(self.value(controller, "$('tab-position').value"), "top")
        self.assertTrue(self.value(controller, "$('tab-vertical_width').disabled"))
        self.value(
            controller,
            """$('tab-position').value = 'left';
            $('tab-position').dispatchEvent(new Event('input'));
            $('tab-style').value = 'pill'; $('tab-vertical_width').value = '300';
            $('tab-tab_count').checked = true; $('layout-density').value = 'compact';
            $('save-layout').click(); true""",
        )
        self.wait_for(
            lambda: preferences_from_config(window.config).tabs["position"] == "left"
        )
        saved = preferences_from_config(window.config)
        self.assertEqual(saved.tabs["style"], "pill")
        self.assertEqual(saved.tabs["vertical_width"], 300)
        self.assertIs(saved.tabs["tab_count"], True)
        self.assertEqual(saved.density, "compact")
        for each in (window, other):
            self.wait_for(lambda each=each: each.tab_bar.vertical)
            self.assertEqual(each.tab_bar.width(), 300)
        self.wait_for(
            lambda: "every window" in self.value(controller, "$('notice').textContent")
        )
        # Other appearance settings were not touched.
        self.assertEqual(
            window.profile_manager.registry.page_style.get(
                window.profile_manager.current_profile_name
            ),
            controller.base,
        )

    def test_routes_reuse_workspace_and_keep_canonical_saved_addresses(self):
        window, tab, controller = self.loaded("settings")
        count = len(window.tabs)
        window._new_tab(url="steed://settings/")
        self.assertEqual(len(window.tabs), count)
        self.assertEqual(window.address_bar.address_input.text(), "steed:settings")
        self.assertIs(tab.native_panel, controller.surface)
        self.assertTrue(controller.surface.profile.isOffTheRecord())
        window._save_current_tabs()
        self.assertIn(
            "steed:settings", [s.url for s in window.tab_manager.get_saved_tabs()]
        )
        self.assertEqual(tab.url().toString(), "about:blank")
        window._navigate_tab_to_url(tab, "steed:style")
        self.assertEqual(tab.workspace_name, "style")
        self.assertIs(controller.surface.parentWidget(), controller)

    def test_style_form_saves_once_syncs_windows_and_preserves_other_data(self):
        window, tab, controller = self.loaded()
        other = BrowserWindow()
        self.windows.append(other)
        other.show()
        other._switch_to_profile("work")
        window.config.set("unrelated", {"keep": True})
        self.value(
            controller,
            "$('accent').value = '#aa3366'; $('css').value = ':root { --muted: rgb(11, 22, 33); }'; $('menu_bar').checked = false; $('status_bar').checked = false; $('all_profiles').checked = true; true",
        )
        revision = window.config.revision
        self.click(controller, "save")
        self.wait_for(lambda: bool(controller.notice))
        self.assertEqual(window.config.revision, revision + 1)
        self.assertTrue(window.menuBar().isHidden())
        self.assertTrue(other.menuBar().isHidden())
        self.assertTrue(window.status_bar.isHidden())
        self.assertFalse(window.address_bar.menu_btn.isHidden())
        self.assertEqual(window.config.get("unrelated"), {"keep": True})
        self.assertEqual(
            self.value(
                controller,
                "getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()",
            ),
            "#aa3366",
        )
        self.assertEqual(
            self.value(
                controller,
                "getComputedStyle(document.documentElement).getPropertyValue('--muted').trim()",
            ),
            "rgb(11, 22, 33)",
        )
        self.assertEqual(
            [a.text() for a in window.browser_menu.actions()],
            ["General", "Profile", "Tabs", "View", "Cookies", "Agents"],
        )

    def test_css_applies_across_pages_and_custom_html_can_override(self):
        window, tab, controller = self.loaded()
        styles = window.profile_manager.registry.page_style
        styles.save("global", {**DEFAULTS, "css": ":root { --ink: rgb(91, 32, 67); }"})
        settings_tab = window._new_tab(url="steed:settings", return_tab=True)
        settings = settings_tab.workspace_controller
        self.wait_for(lambda: settings.surface.ready)
        self.wait_for(lambda: self.value(settings, "!!state"))
        self.assertEqual(
            self.value(settings, "getComputedStyle(document.body).color"),
            "rgb(91, 32, 67)",
        )
        pages = window.profile_manager.registry.start_pages
        self.assertIn(
            "--ink: rgb(91, 32, 67)", pages.render("global", "newtab").decode()
        )
        pages.configure(
            "global",
            pages.state("global")["settings"],
            "<style>:root { --ink: blue; }</style><h1>Mine</h1>",
        )
        html = pages.render("global", "newtab").decode()
        self.assertLess(html.index("--ink: rgb(91, 32, 67)"), html.index("--ink: blue"))
        self.assertNotIn("qwebchannel", html)

    def test_newtab_background_covers_viewport_and_menu_survives_minimal(self):
        window, _, controller = self.loaded()
        path = self.config.config_dir / "background.png"
        image = QImage(60, 40, QImage.Format.Format_RGB32)
        image.fill(0xD6CBAA)
        image.save(str(path))
        pages = window.profile_manager.registry.start_pages
        pages.configure(
            "global",
            {
                "modules": ["image", "search"],
                "image": image_data(path),
                "minimal": True,
                "layout": "classic",
            },
        )
        tab = window._new_tab(url="steed:newtab", return_tab=True)

        class Probe:
            surface = type("Surface", (), {"page": tab.page()})()

        probe = Probe()
        self.wait_for(
            lambda: self.value(probe, "!!document.querySelector('.has-background')")
        )
        self.assertEqual(
            self.value(
                probe, "getComputedStyle(document.body, '::before').backgroundSize"
            ),
            "cover",
        )
        self.assertEqual(
            self.value(probe, "getComputedStyle(document.body, '::before').position"),
            "fixed",
        )
        self.assertTrue(
            self.value(
                probe,
                "getComputedStyle(document.body, '::before').backgroundImage.includes('data:image/png;base64,')",
            )
        )
        self.assertEqual(self.value(probe, "document.images.length"), 0)
        self.assertNotEqual(
            self.value(
                probe,
                "getComputedStyle(document.querySelector('.browser-menu')).display",
            ),
            "none",
        )
        value = window.profile_manager.registry.page_style.get("global")
        window.profile_manager.registry.page_style.save(
            "global", {**value, "newtab_menu": False}
        )
        self.assertNotIn(
            '<details class="browser-menu">', pages.render("global", "newtab").decode()
        )

    def test_hidden_bars_keep_shortcuts_and_menu_reachable(self):
        window, _, _ = self.loaded()
        window._set_bar("menu_bar", False)
        window._set_bar("status_bar", False)
        window.activateWindow()
        window.address_bar.address_input.setFocus()
        count = len(window.tabs)
        QTest.keyClick(
            window.address_bar.address_input,
            Qt.Key.Key_T,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.wait_for(lambda: len(window.tabs) == count + 1)
        QTest.keyClick(
            window.address_bar.address_input,
            Qt.Key.Key_M,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        self.wait_for(lambda: not window.menuBar().isHidden())
        self.assertTrue(window.status_bar.isHidden())

    def test_loading_indicator_tracks_selected_tab_and_clears_on_failure(self):
        window, style_tab, controller = self.loaded()
        first = window._new_tab(url="about:blank", return_tab=True)
        second = window._new_tab(url="about:blank", return_tab=True)
        self.wait_for(lambda: not first.is_loading and not second.is_loading)
        window._on_load_started(first)
        window._on_load_progress(first, 37)
        self.assertTrue(window.loading_indicator.isHidden())
        window._switch_tab(window.tabs.index(first))
        self.assertFalse(window.loading_indicator.isHidden())
        self.assertEqual(window.loading_indicator.value(), 37)
        window._switch_tab(window.tabs.index(second))
        self.assertTrue(window.loading_indicator.isHidden())
        window._switch_tab(window.tabs.index(first))
        window._on_load_finished(first, False)
        self.assertTrue(window.loading_indicator.isHidden())
        value = window.profile_manager.registry.page_style.get("global")
        window.profile_manager.registry.page_style.save(
            "global",
            {
                **value,
                "loading_height": 6,
                "loading_color": "#aa3377",
                "loading_percent": True,
            },
        )
        window._on_load_started(first)
        window._on_load_progress(first, 62)
        self.assertEqual(window.loading_indicator.text(), "62%")
        self.assertTrue(window.loading_indicator.isTextVisible())
        self.assertIn("#aa3377", window.loading_indicator.styleSheet())
        window._switch_tab(window.tabs.index(style_tab))
        self.assertTrue(window.loading_indicator.isHidden())

    def test_profile_specific_chrome_restores_when_switching(self):
        window, _, _ = self.loaded()
        window.profile_manager.create_profile("different")
        # The menu bar is hidden by default; shown here for this profile only.
        window._set_bar("menu_bar", True)
        window._switch_to_profile("different")
        self.assertTrue(window.menuBar().isHidden())
        window._switch_to_profile("global")
        self.assertFalse(window.menuBar().isHidden())

    def test_style_io_error_and_conflict_keep_form_and_saved_preferences(self):
        window, _, controller = self.loaded()
        self.value(controller, "$('css').value = 'body { color: red; }'; true")
        with patch.object(
            window.config, "save_config", side_effect=OSError("disk full")
        ):
            self.click(controller, "save")
            self.wait_for(lambda: bool(controller.error))
        self.assertEqual(controller.styles.get("global"), DEFAULTS)
        self.assertEqual(
            self.value(controller, "$('css').value"), "body { color: red; }"
        )
        controller.styles.save("global", {**DEFAULTS, "theme": "dark"})
        self.click(controller, "save")
        self.wait_for(lambda: "another window" in controller.error)
        self.click(controller, "reload")
        self.wait_for(lambda: controller.base["theme"] == "dark")
        self.wait_for(lambda: self.value(controller, "$('theme').value") == "dark")

    def test_old_document_commands_and_duplicate_saves_are_rejected(self):
        window, _, controller = self.loaded()
        command = {
            "context": controller.context,
            "sequence": 1,
            "action": "save",
            "values": dict(DEFAULTS),
            "all_profiles": False,
        }
        revision = window.config.revision
        controller.command(command)
        controller.command(command)
        self.assertEqual(window.config.revision, revision + 1)
        old_generation = controller.surface.generation
        controller.surface.reload()
        self.wait_for(lambda: controller.surface.ready)
        controller.surface._dispatch(
            json.dumps({**command, "generation": old_generation})
        )
        self.assertEqual(window.config.revision, revision + 1)

    def test_custom_html_cannot_open_browser_menu_native_actions(self):
        window, _, _ = self.loaded()
        pages = window.profile_manager.registry.start_pages
        pages.configure(
            "global", pages.state("global")["settings"], "<h1>Authored page</h1>"
        )
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.wait_for(lambda: tab.url().host() == "newtab")
        with patch.object(window._layout_menus["general"], "popup") as popup:
            window._start_page_action(tab, QUrl("steed://action/menu?name=general"))
            popup.assert_not_called()

    def test_internal_workspaces_cannot_be_delegated_during_navigation(self):
        window, tab, _ = self.loaded()
        task = self.command("task.create", objective="Check workspace isolation")
        # Even if the underlying engine still reports the preceding website,
        # the maintained HTML overlay is not a shareable browsing surface.
        with patch.object(tab, "url", return_value=QUrl(self.origin)):
            with self.assertRaisesRegex(ValueError, "internal Steed workspaces"):
                self.service.live_tabs.request(task.task_id, [tab])

    def test_root_style_tab_restores_and_reloads_without_replaying_save(self):
        window, tab, controller = self.loaded()
        self.value(controller, "$('theme').value = 'dark'; true")
        self.click(controller, "save")
        self.wait_for(lambda: bool(controller.notice))
        revision = window.config.revision
        old_generation = controller.surface.generation
        window._reload()
        self.wait_for(
            lambda: (
                controller.surface.ready
                and controller.surface.generation != old_generation
            )
        )
        self.assertEqual(window.config.revision, revision)
        self.assertEqual(self.value(controller, "$('theme').value"), "dark")
        window.config.set("restore_tabs_on_startup", True)
        window._save_current_tabs()
        # Restore on a fresh start; a second window of an open profile starts empty.
        window.close()
        restored = BrowserWindow()
        self.windows.append(restored)
        self.assertIn("style", [t.workspace_name for t in restored.tabs])

    def test_builtin_start_page_links_open_maintained_tabs(self):
        window, _, controller = self.loaded()
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        result = []

        def loaded():
            tab.page().runJavaScript(
                "!!document.querySelector('a[href=\"steed://settings/\"]')",
                result.append,
            )
            return bool(result and result[-1])

        self.wait_for(loaded)
        tab.page().runJavaScript(
            "document.querySelector('a[href=\"steed://settings/\"]').click()"
        )
        self.wait_for(lambda: tab.workspace_name == "settings")
        self.assertEqual(window.address_bar.address_input.text(), "steed:settings")

    def test_find_and_zoom_follow_the_visible_page_when_leaving_workspace(self):
        window, tab, controller = self.loaded()
        window._open_find()
        self.assertIs(window.find_bar._page, controller.surface.page)
        window._zoom(1)
        self.assertGreater(controller.surface.page.zoomFactor(), 1)
        self.assertEqual(tab.zoomFactor(), 1)
        window._navigate_tab_to_url(tab, self.origin + "/before-workspace")
        self.assertTrue(controller.disposed)
        self.assertIs(window.find_bar._page, tab.page())
        self.wait_for(lambda: tab.title() == "Task fixture" and not tab.is_loading)
        window._navigate_tab_to_url(tab, "steed:style")
        replacement = tab.workspace_controller
        self.wait_for(lambda: replacement.surface.ready and not tab.is_loading)
        self.assertEqual(replacement.surface.page.zoomFactor(), 1)
        window._go_back()
        self.assertIsNone(tab.workspace_name)
        self.assertTrue(replacement.disposed)
        self.assertIs(window.find_bar._page, tab.page())
        self.wait_for(lambda: tab.url().path() == "/before-workspace")

    def test_closing_browser_preserves_settings_workspace_in_session(self):
        window, _, _ = self.loaded("settings")
        window.config.set("restore_tabs_on_startup", True)
        window.close()
        restored = BrowserWindow()
        self.windows.append(restored)
        self.assertIn("settings", [t.workspace_name for t in restored.tabs])

    def test_native_reset_recovers_hidden_html_without_touching_authored_pages(self):
        window, _, controller = self.loaded()
        pages = window.profile_manager.registry.start_pages
        pages.configure(
            "global", pages.state("global")["settings"], "<h1>My saved page</h1>"
        )
        style = {**DEFAULTS, "css": "body { display: none; }", "menu_bar": False}
        controller.styles.save("global", style)
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ):
            window._reset_page_style()
        self.assertEqual(controller.styles.get("global"), style)
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window._reset_page_style()
        self.assertEqual(controller.styles.get("global"), DEFAULTS)
        self.assertTrue(window.menuBar().isHidden())  # Hidden by default.
        self.assertEqual(pages.state("global")["html"], "<h1>My saved page</h1>")

    def test_private_style_editor_saves_only_for_this_session(self):
        window, _, _ = self.loaded()
        window._switch_to_profile("search")
        tab = window._new_tab(url="steed:style", return_tab=True)
        controller = tab.workspace_controller
        self.wait_for(lambda: controller.surface.ready)
        self.wait_for(lambda: self.value(controller, "!!state"))
        before = window.config.config_file.read_bytes()
        self.assertTrue(self.value(controller, "$('all_profiles').disabled"))
        self.value(controller, "$('menu_bar').checked = true; true")
        self.click(controller, "save")
        self.wait_for(lambda: bool(controller.notice))
        self.assertFalse(window.menuBar().isHidden())
        self.assertEqual(window.config.config_file.read_bytes(), before)
        window._switch_to_profile("global")
        self.assertTrue(window.menuBar().isHidden())

    def test_reentering_agents_address_keeps_its_tab_and_conversation(self):
        window, tab, _ = self.loaded()
        window._navigate_tab_to_url(tab, "steed:agents")
        sidebar = window.agent_sidebar
        count = len(window.tabs)
        window._navigate_tab_to_url(tab, "steed://agents/")
        self.assertIs(window.agent_tab, tab)
        self.assertIs(window.agent_sidebar, sidebar)
        self.assertEqual(len(window.tabs), count)
        self.assertEqual(window.address_bar.address_input.text(), "steed:agents")
