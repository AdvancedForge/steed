"""Real HTML menus/editors keep native controller behavior and stable targets."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_html_settings as fixtures
from PyQt6.QtCore import QPoint, QStandardPaths
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QDialog,
    QLineEdit,
    QMenuBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from browser.browser_window import BrowserWindow
from browser.layout_preferences import TOOLBAR_ITEMS
from ui.adblock_dialog import AdBlockDialog
from ui.download_dialog import choose_download_destination
from ui.layout_dialog import LayoutDialog


def selector(attribute, value):
    return json.dumps(f'[{attribute}="{value}"]')


class HtmlMenuTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.HtmlSettingsTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.HtmlSettingsTests.tearDownClass.__func__)
    setUp = fixtures.HtmlSettingsTests.setUp
    tearDown = fixtures.HtmlSettingsTests.tearDown
    wait_for = fixtures.HtmlSettingsTests.wait_for
    value = fixtures.HtmlSettingsTests.value

    def window(self):
        window = BrowserWindow()
        self.windows.append(window)
        window.show()
        return window

    def ready(self, host):
        self.wait_for(lambda: host.surface.ready)
        self.wait_for(lambda: self.value(host, "!!state"))
        self.wait_for(lambda: host.surface.stack.currentWidget() is host.surface.view)

    def present(self, source, window):
        host = window._present_dialog(source, modal=False, force_html=True)
        self.ready(host)
        return host

    def control(self, host, widget):
        return next(key for key, obj in host.objects.items() if obj is widget)

    def click(self, host, widget):
        key = self.control(host, widget)
        self.value(
            host,
            f"document.querySelector({json.dumps('[data-control=' + chr(34) + key + chr(34) + ']')}).click(); true",
        )

    def test_adblock_editor_roundtrip_keeps_controller_validation_and_save(self):
        window = self.window()
        source = AdBlockDialog(window.adblock, window)
        host = self.present(source, window)
        self.assertFalse(QDialog.isVisible(source))
        self.assertTrue(host.surface.profile.isOffTheRecord())
        key = self.control(host, source.exceptions)
        self.value(
            host,
            f'const input = document.querySelector({selector("data-control", key)}); input.value="Example.test, other.test"; input.dispatchEvent(new Event("input")); true',
        )
        self.wait_for(lambda: source.exceptions.text() == "Example.test, other.test")
        save = next(
            button
            for button in source.findChildren(QPushButton)
            if button.text() == "Save"
        )
        self.click(host, save)
        self.wait_for(lambda: host.closed)
        self.assertEqual(
            window.config.get("adblock_exceptions"), ["example.test", "other.test"]
        )

    def newtab(self, window):
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        proxy = SimpleNamespace(surface=SimpleNamespace(page=tab.page()))
        self.wait_for(
            lambda: self.value(proxy, "!!document.querySelector('.browser-menu')")
        )
        self.wait_for(lambda: not tab.is_loading)
        return tab, proxy

    def test_layout_html_renders_custom_group_and_edits_panel_title(self):
        window = self.window()
        source = LayoutDialog(window.layout_preferences, window)
        host = self.present(source, window)
        self.assertTrue(
            self.value(
                host, "document.body.textContent.includes('Quick-action panels')"
            )
        )
        toolbar = self.control(host, source.toolbar_list)
        self.assertEqual(
            self.value(
                host,
                f'document.querySelector({selector("data-control", toolbar)}).querySelectorAll("input[type=checkbox]").length',
            ),
            len(TOOLBAR_ITEMS),
        )
        self.click(host, source.panel_editor.add_button)
        self.wait_for(lambda: bool(source.preferences.panels))
        key = self.control(host, source.panel_editor.title)
        self.value(
            host,
            f'const input=document.querySelector({selector("data-control", key)}); input.value="Daily tools"; input.dispatchEvent(new Event("input")); true',
        )
        self.wait_for(lambda: source.preferences.panels[0]["title"] == "Daily tools")
        self.assertEqual(source.preferences.toolbar_order[0], "menu")

    def test_an_editable_choice_is_a_full_width_field_with_its_hint(self):
        from ui.bookmarks_dialog import BookmarkDialog

        window = self.window()
        source = BookmarkDialog(
            window.profile_manager.registry.start_pages,
            "global",
            window,
            "https://a.example/",
            "A",
        )
        host = self.present(source, window)
        key = self.control(host, source.folder)
        field = f"document.querySelector({selector('data-control', key)})"
        # Waits: the page lays the field out after it first appears.
        self.wait_for(lambda: self.value(host, f"!!{field}"))
        self.assertEqual(self.value(host, field + ".type"), "text")
        self.assertEqual(
            self.value(host, field + ".placeholder"), "Unfiled, or type a folder name"
        )
        self.wait_for(
            lambda: self.value(host, field + ".getBoundingClientRect().width") > 400
        )
        host.reject()

    def test_maintained_settings_controllers_render_in_html_hosts(self):
        from ui.agent_setup import AgentSetupDialog
        from ui.app_connections import AppConnectionsDialog
        from ui.bookmarks_dialog import BookmarksDialog
        from ui.cookie_dialog import CookieDialog
        from ui.download_dialog import DownloadDialog
        from ui.file_library import FileLibraryDialog
        from ui.group_dialog import GroupDialog
        from ui.start_page_dialog import StartPageDialog
        from ui.task_dialog import TaskDialog

        window = self.window()
        registry = window.profile_manager.registry
        sources = (
            lambda: AgentSetupDialog(registry, window),
            lambda: AppConnectionsDialog(registry.app_connections, window),
            lambda: BookmarksDialog(registry.start_pages, "global", window),
            lambda: CookieDialog(window.cookie_manager, window),
            lambda: DownloadDialog(window.download_manager, window),
            lambda: FileLibraryDialog(registry.tasks, window),
            lambda: GroupDialog(window),
            lambda: StartPageDialog(registry.start_pages, "global", window),
            lambda: TaskDialog(registry.tasks, window),
        )
        for create in sources:
            source = create()
            with self.subTest(editor=source.windowTitle()):
                host = self.present(source, window)
                self.assertFalse(QDialog.isVisible(source))
                self.assertEqual(
                    self.value(host, "document.getElementById('title').textContent"),
                    source.windowTitle(),
                )
                self.assertGreater(host.surface.view.width(), 100)
                host.reject()
                self.assertTrue(host.surface.disposed)

    def test_custom_html_tab_renders_and_edits_multiline_source_as_text(self):
        from PyQt6.QtWidgets import QTabWidget

        from ui.start_page_dialog import StartPageDialog

        window = self.window()
        source = StartPageDialog(
            window.profile_manager.registry.start_pages, "global", window
        )
        host = self.present(source, window)
        tabs = source.findChild(QTabWidget)
        token = self.control(host, tabs)
        self.value(
            host,
            f'document.querySelector({selector("data-control", token)}).querySelectorAll("nav button")[1].click(); true',
        )
        self.wait_for(lambda: tabs.currentIndex() == 1)
        key = self.control(host, source.html)
        self.wait_for(
            lambda: self.value(
                host, "!!document.querySelector(" + selector("data-control", key) + ")"
            )
        )
        self.value(
            host,
            f'const field=document.querySelector({selector("data-control", key)}); field.value="<h1>My page</h1>"; field.dispatchEvent(new Event("input")); true',
        )
        self.wait_for(lambda: source.html.toPlainText() == "<h1>My page</h1>")
        self.assertIs(host.surface.stack.currentWidget(), host.surface.view)

    def test_render_failure_offers_reload_without_saving_or_replaying(self):
        window = self.window()
        host = self.present(AdBlockDialog(window.adblock, window), window)
        revision = window.config.revision
        self.value(
            host,
            "window.steedUI.render=()=>{throw new Error('synthetic render failure')}; true",
        )
        host.surface.publish(host.surface._state, force=True)
        self.wait_for(lambda: not host.surface.ready)
        self.assertIs(host.surface.stack.currentWidget(), host.surface.fallback)
        self.assertTrue(host.surface.retry.isVisible())
        host.surface.reload()
        self.ready(host)
        self.assertEqual(window.config.revision, revision)

    def test_desktop_menus_remain_native(self):
        window = self.window()
        self.assertIs(type(window.menuBar()), QMenuBar)
        self.assertIs(window.address_bar.menu_btn.menu(), window.browser_menu)

    def test_top_menus_stay_small_and_scroll(self):
        from ui.chrome_style import MENU_MAX_HEIGHT

        window = self.window()
        window.apply_dark_mode()
        menus = [a.menu() for a in window.menuBar().actions() if a.menu()]
        while menus:
            menu = menus.pop()
            with self.subTest(menu=menu.title()):
                self.assertLessEqual(menu.maximumHeight(), MENU_MAX_HEIGHT)
                # One column: no menu grows wider than its longest row.
                self.assertLess(menu.sizeHint().width(), 480)
            menus += [a.menu() for a in menu.actions() if a.menu()]

    def test_newtab_category_click_anchors_html_and_footer_opens_matching_settings(
        self,
    ):
        window = self.window()
        tab, proxy = self.newtab(window)
        self.value(proxy, "document.querySelector('.browser-menu').open=true; true")
        rect = self.value(
            proxy,
            "(() => {const r=document.querySelector('.browser-menu a').getBoundingClientRect();return [r.right,r.top]})()",
        )
        self.value(proxy, "document.querySelector('.browser-menu a').click(); true")
        self.wait_for(lambda: window._html_menu is not None)
        menu = window._html_menu
        self.ready(menu)
        self.assertEqual(self.value(menu, "state.title"), "General")
        point = tab.mapToGlobal(QPoint(int(rect[0]), int(rect[1])))
        self.assertLess((menu.anchor - point).manhattanLength(), 8)
        self.assertTrue(window.screen().availableGeometry().contains(menu.geometry()))
        self.assertFalse(window._layout_menus["general"].isVisible())
        self.value(menu, "document.getElementById('open-settings').click(); true")
        self.wait_for(lambda: window.settings_dialog is not None)
        self.assertTrue(menu.closed)
        self.assertEqual(window.settings_dialog.state["section"], "general")
        self.assertEqual(
            window.tabs[window.current_tab_index].workspace_name, "settings"
        )

    def test_newtab_long_hover_opens_summary_and_category(self):
        window = self.window()
        tab, proxy = self.newtab(window)
        window.activateWindow()
        tab.setFocus()

        def move(selector):
            point = self.value(
                proxy,
                "(() => { const r=document.querySelector("
                + json.dumps(selector)
                + ").getBoundingClientRect();return [r.x+12,r.y+r.height/2]})()",
            )
            QTest.mouseMove(tab.focusProxy(), QPoint(int(point[0]), int(point[1])))

        move(".browser-menu summary")
        self.wait_for(
            lambda: self.value(proxy, "document.querySelector('.browser-menu').open")
        )
        move(".browser-menu a")
        self.wait_for(lambda: window._html_menu is not None)
        self.ready(window._html_menu)
        self.assertEqual(self.value(window._html_menu, "state.title"), "General")
        menu = window._html_menu
        self.value(
            menu,
            "document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape'})); true",
        )
        self.wait_for(lambda: window._html_menu is None)
        QTest.qWait(1100)
        self.assertIsNone(window._html_menu)

    def test_footer_maps_profile_category_to_settings_profile(self):
        window = self.window()
        menu = window._show_html_menu(
            window._layout_menus["profile"], window.mapToGlobal(QPoint(24, 24))
        )
        self.ready(menu)
        self.value(menu, "document.getElementById('open-settings').click(); true")
        self.wait_for(lambda: window.settings_dialog is not None)
        self.assertEqual(window.settings_dialog.state["section"], "profile")

    def test_newtab_menu_anchor_accounts_for_page_zoom(self):
        window = self.window()
        tab, proxy = self.newtab(window)
        tab.setZoomFactor(1.5)
        self.wait_for(
            lambda: abs(self.value(proxy, "innerWidth") * 1.5 - tab.width()) < 3
        )
        self.value(proxy, "document.querySelector('.browser-menu').open=true; true")
        rect = self.value(
            proxy,
            "(() => {const r=document.querySelector('.browser-menu a').getBoundingClientRect();return [r.right,r.top]})()",
        )
        with patch.object(window, "_show_html_menu") as show:
            window._newtab_menu_at(tab, "general")
            self.wait_for(lambda: show.called)
        expected = tab.mapToGlobal(QPoint(int(rect[0] * 1.5), int(rect[1] * 1.5)))
        self.assertLess((show.call_args.args[1] - expected).manhattanLength(), 8)

    def test_hidden_parent_controls_cannot_mutate_and_table_activation_uses_native_signal(
        self,
    ):
        window = self.window()
        source = QDialog(window)
        from PyQt6.QtWidgets import QWidget

        group = QWidget()
        field = QLineEdit("Saved")
        QVBoxLayout(group).addWidget(field)
        table = QTableWidget(1, 1)
        item = QTableWidgetItem("Synthetic row")
        table.setItem(0, 0, item)
        body = QVBoxLayout(source)
        body.addWidget(group)
        body.addWidget(table)
        selected = []
        table.itemDoubleClicked.connect(selected.append)
        host = self.present(source, window)
        key = self.control(host, field)
        group.hide()
        host.command(
            {
                "context": host.context,
                "sequence": host.sequence + 1,
                "action": "edit",
                "id": key,
                "value": "Changed",
            }
        )
        self.assertEqual(field.text(), "Saved")
        host.command(
            {
                "context": host.context,
                "sequence": host.sequence + 1,
                "action": "activate",
                "id": self.control(host, table),
                "item": self.control(host, item),
            }
        )
        self.assertEqual(selected, [item])

    def test_custom_html_and_stale_geometry_cannot_open_native_menu(self):
        window = self.window()
        tab, _ = self.newtab(window)
        callbacks = []
        with patch.object(
            tab.page(),
            "runJavaScript",
            side_effect=lambda script, *rest: callbacks.append(rest[-1]),
        ):
            window._newtab_menu_at(tab, "general")
        pages = window.profile_manager.registry.start_pages
        pages.configure(
            "global", pages.state("global")["settings"], "<h1>Authored page</h1>"
        )
        with patch.object(window, "_show_html_menu") as show:
            callbacks[0]([100, 100])
            window._newtab_menu_at(tab, "general")
            show.assert_not_called()

    def test_password_values_are_not_published_to_html(self):
        window = self.window()
        source = QDialog(window)
        field = QLineEdit("synthetic-secret")
        field.setEchoMode(QLineEdit.EchoMode.Password)
        QVBoxLayout(source).addWidget(field)
        host = self.present(source, window)
        self.assertNotIn("synthetic-secret", self.value(host, "JSON.stringify(state)"))
        self.assertTrue(self.value(host, "state.children[0].has_value"))

    def test_hover_opens_nothing_but_sub_menus_and_never_runs_commands(self):
        window = self.window()
        menu = window._show_html_menu(
            window._layout_menus["general"], window.mapToGlobal(QPoint(24, 24))
        )
        self.ready(menu)
        action = next(
            a for a in menu.actions.values() if a.text().startswith("Downloads")
        )
        token = next(key for key, a in menu.actions.items() if a is action)
        self.value(
            menu,
            f'document.querySelector({selector("data-action", token)}).dispatchEvent(new PointerEvent("pointerenter")); true',
        )
        QTest.qWait(1000)  # Longer than any hover delay: still nothing opened.
        self.assertFalse(window._html_dialogs)
        self.assertFalse(menu.closed)
        self.value(
            menu,
            f"document.querySelector({selector('data-action', token)}).click(); true",
        )
        self.wait_for(lambda: bool(window._html_dialogs))
        host = window._html_dialogs[-1]
        self.ready(host)
        self.assertEqual(host.source.windowTitle(), "Downloads")
        self.assertTrue(menu.closed)
        host.reject()
        menu = window._show_html_menu(
            window._layout_menus["tabs"], window.mapToGlobal(QPoint(24, 24))
        )
        self.ready(menu)
        action = next(a for a in menu.actions.values() if a.text() == "Close Tab")
        token = next(key for key, a in menu.actions.items() if a is action)
        count = len(window.tabs)
        menu.command(
            {
                "context": menu.context,
                "sequence": menu.sequence + 1,
                "action": "hover",
                "id": token,
                "x": 10,
                "y": 10,
            }
        )
        self.assertEqual(len(window.tabs), count)

    def test_stale_rows_cannot_select_a_replacement_record(self):
        window = self.window()
        source = QDialog(window)
        source.setWindowTitle("Synthetic table")
        table = QTableWidget(1, 1)
        original = QTableWidgetItem("Original")
        table.setItem(0, 0, original)
        QVBoxLayout(source).addWidget(table)
        host = self.present(source, window)
        key, row = self.control(host, table), self.control(host, original)
        table.setItem(0, 0, QTableWidgetItem("Replacement"))
        host.command(
            {
                "context": host.context,
                # After anything the page already sent (its own fit, say).
                "sequence": host.sequence + 1,
                "action": "select",
                "id": key,
                "item": row,
            }
        )
        self.assertIn("changed", host.error)
        self.assertFalse(table.item(0, 0).isSelected())

    def test_disabled_readonly_and_old_document_commands_do_not_mutate(self):
        window = self.window()
        source = QDialog(window)
        field = QLineEdit("Saved")
        field.setReadOnly(True)
        QVBoxLayout(source).addWidget(field)
        host = self.present(source, window)
        key = self.control(host, field)
        command = {
            "context": host.context,
            "sequence": host.sequence + 1,
            "action": "edit",
            "id": key,
            "value": "Changed",
        }
        host.command(command)
        self.assertEqual(field.text(), "Saved")
        field.setReadOnly(False)
        old_generation = host.surface.generation
        host.surface.reload()
        self.ready(host)
        host.surface._dispatch(json.dumps({**command, "generation": old_generation}))
        self.assertEqual(field.text(), "Saved")

    def test_profile_switch_and_window_close_dispose_editor_pages(self):
        window = self.window()
        host = self.present(AdBlockDialog(window.adblock, window), window)
        window._switch_to_profile("work")
        self.assertTrue(host.closed)
        self.assertTrue(host.surface.disposed)
        second = self.present(AdBlockDialog(window.adblock, window), window)
        window.close()
        self.assertTrue(second.closed)
        self.assertTrue(second.surface.disposed)

    def test_download_picker_starts_in_system_downloads_and_sanitizes_name(self):
        with (
            patch.object(
                QStandardPaths,
                "writableLocation",
                return_value="/tmp/synthetic-downloads",
            ),
            patch(
                "ui.download_dialog.QFileDialog.getSaveFileName", return_value=("", "")
            ) as picker,
        ):
            self.assertEqual(choose_download_destination("../report.pdf"), "")
        self.assertEqual(
            picker.call_args.args[2], "/tmp/synthetic-downloads/report.pdf"
        )

    def test_palette_fields_and_css_guide_save_in_style_workspace(self):
        window = self.window()
        tab = window._new_tab(url="steed:style", return_tab=True)
        controller = tab.workspace_controller
        self.ready(controller)
        self.value(
            controller,
            "$('color-bg').value='#101927'; $('color-bg').dispatchEvent(new Event('input')); $('color-ink').value='#f5f6fa'; $('color-ink').dispatchEvent(new Event('input')); $('save').click(); true",
        )
        self.wait_for(lambda: bool(controller.notice))
        self.assertEqual(controller.styles.get("global")["colors"]["bg"], "#101927")
        self.assertEqual(
            self.value(
                controller,
                "getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()",
            ),
            "#101927",
        )
        self.assertTrue(
            self.value(controller, "$('css-guide').textContent.includes('--surface')")
        )
