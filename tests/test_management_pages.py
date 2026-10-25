"""Management pages exercise real HTML, profile boundaries and dialog lifetimes."""

import json
import unittest
from unittest.mock import patch

import test_html_menus as fixtures
import test_task_profiles as task_fixtures
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from services.start_pages import WORKSPACES, workspace_name
from ui.management_pages import PAGES
from ui.prompts import InputDialog, MessageBox


class ManagementPageTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.HtmlMenuTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.HtmlMenuTests.tearDownClass.__func__)
    setUp = fixtures.HtmlMenuTests.setUp
    tearDown = fixtures.HtmlMenuTests.tearDown
    wait_for = fixtures.HtmlMenuTests.wait_for
    value = fixtures.HtmlMenuTests.value
    window = fixtures.HtmlMenuTests.window
    ready = fixtures.HtmlMenuTests.ready
    control = fixtures.HtmlMenuTests.control
    click = fixtures.HtmlMenuTests.click
    command = task_fixtures.TaskProfileTests.command
    request = task_fixtures.TaskProfileTests.request

    def opened(self, name):
        window = self.window()
        tab = window._new_tab(url="steed:" + name, return_tab=True)
        host = tab.workspace_controller
        self.ready(host)
        return window, tab, host

    def test_narrow_page_uses_compact_navigation_and_keeps_content_width(self):
        window, tab, host = self.opened("history")
        window.resize(560, 600)
        self.wait_for(lambda: self.value(host, "innerWidth < 620"))
        self.assertEqual(
            self.value(
                host,
                "getComputedStyle(document.getElementById('workspace-nav')).display",
            ),
            "none",
        )
        self.assertNotEqual(
            self.value(
                host,
                "getComputedStyle(document.getElementById('workspace-picker')).display",
            ),
            "none",
        )
        self.value(
            host,
            "const picker=document.getElementById('workspace-picker'); picker.value='panels'; picker.dispatchEvent(new Event('change')); true",
        )
        self.wait_for(
            lambda: window.tabs[window.current_tab_index].workspace_name == "panels"
        )
        self.assertIn(tab, window.tabs)

    def test_keyboard_tabs_skip_disabled_sections_and_keep_visible_focus(self):
        from ui.html_dialog import HtmlSettingsDialog

        window = self.window()
        source = HtmlSettingsDialog(window)
        layout = QVBoxLayout(source)
        tabs = QTabWidget()
        for title in ("First", "Unavailable", "Last"):
            tabs.addTab(QWidget(), title)
        tabs.setTabEnabled(1, False)
        layout.addWidget(tabs)
        source.show()
        host = source.presentation_host()
        self.ready(host)
        self.value(
            host,
            "const tab=document.querySelector('[role=tab][aria-selected=true]'); tab.focus(); tab.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight', bubbles:true})); true",
        )
        self.wait_for(lambda: tabs.currentIndex() == 2)
        self.wait_for(
            lambda: self.value(
                host,
                "document.activeElement.textContent === 'Last' && document.activeElement.getAttribute('role') === 'tab'",
            )
        )
        self.value(
            host,
            "document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key:'Home', bubbles:true})); true",
        )
        self.wait_for(lambda: tabs.currentIndex() == 0)
        source.reject()

    def test_every_management_route_renders_html_without_native_controller_window(self):
        window = self.window()
        for name in PAGES:
            with self.subTest(page=name):
                tab = window._new_tab(url="steed:" + name, return_tab=True)
                host = tab.workspace_controller
                self.ready(host)
                self.assertEqual(tab.workspace_name, name)
                self.assertFalse(QDialog.isVisible(host.source))
                self.assertFalse(host.isWindow())
                self.assertTrue(host.surface.profile.isOffTheRecord())
                self.assertEqual(self.value(host, "state.workspace"), name)
                self.assertFalse(
                    self.value(host, "document.getElementById('workspace-nav').hidden")
                )
                self.assertIs(
                    window._new_tab(url="steed:" + name, return_tab=True), tab
                )
                window._close_tab(window.tabs.index(tab))
                self.assertTrue(host.surface.disposed)

    def test_cookie_menu_and_download_shortcut_open_pages(self):
        window = self.window()
        window._view_cookies()
        self.assertEqual(
            window.tabs[window.current_tab_index].workspace_name, "cookies"
        )
        action = next(
            a for a in window.actions() if a.shortcut().toString() == "Ctrl+J"
        )
        action.trigger()
        self.assertEqual(
            window.tabs[window.current_tab_index].workspace_name, "downloads"
        )

    def test_cookies_page_receives_live_profile_cookies_and_deletes_selected_cookie(
        self,
    ):
        window, _, host = self.opened("cookies")
        page = window._new_tab(url=self.origin + "/cookie", return_tab=True)
        self.wait_for(lambda: not page.is_loading and page.url().path() == "/cookie")
        source = host.source
        self.wait_for(
            lambda: any(
                cookie.name == "task_cookie" for cookie in source.displayed_cookies
            )
        )
        cookie_tab = next(tab for tab in window.tabs if tab.workspace_name == "cookies")
        window._switch_tab(window.tabs.index(cookie_tab))
        self.wait_for(
            lambda: self.value(
                host, "document.body.textContent.includes('task_cookie')"
            )
        )
        self.value(
            host,
            "[...document.querySelectorAll('tbody tr')].find(row => row.textContent.includes('task_cookie')).click(); true",
        )
        self.wait_for(lambda: bool(source.table.selectedItems()))
        with patch(
            "ui.cookie_dialog.QMessageBox.question",
            return_value=MessageBox.StandardButton.Yes,
        ):
            self.click(host, source.delete_btn)
            self.wait_for(
                lambda: (
                    not any(
                        cookie.name == "task_cookie"
                        for cookie in source.displayed_cookies
                    )
                )
            )

    def test_history_filters_and_opens_without_recording_internal_pages(self):
        window = self.window()
        window.history.record("https://history.example.test/report", "Quarterly report")
        window.history.record("https://other.example.test/", "Other")
        tab = window._new_tab(url="steed:history", return_tab=True)
        host = tab.workspace_controller
        self.ready(host)
        source = host.source
        token = self.control(host, source.search)
        selector = json.dumps('[data-control="' + token + '"]')
        self.value(
            host,
            f'const input=document.querySelector({selector}); input.value="quarterly"; input.dispatchEvent(new Event("input")); true',
        )
        self.wait_for(lambda: source.items.rowCount() == 1)
        source.items.selectRow(0)
        with patch.object(window, "_new_tab") as opened:
            source.open_selected()
            opened.assert_called_once_with(url="https://history.example.test/report")
        self.assertEqual(len(window.history.entries()), 2)

    def test_profile_switch_disposes_pages_and_private_library_is_unavailable(self):
        window, tab, host = self.opened("library")
        window._switch_to_profile("work")
        self.assertTrue(host.closed)
        self.assertTrue(host.surface.disposed)
        window._switch_to_profile("search")
        tab = window._new_tab(url="steed:library", return_tab=True)
        self.ready(tab.workspace_controller)
        self.assertTrue(
            self.value(
                tab.workspace_controller,
                "document.body.textContent.includes('regular profile')",
            )
        )

    def test_navigation_and_close_cancel_layout_preview(self):
        window, tab, host = self.opened("layout")
        source = host.source
        before = window.layout_preferences.density
        source.density.setCurrentIndex(0)
        self.assertTrue(window._layout_editing)
        window._navigate_tab_to_url(tab, "about:blank")
        self.assertFalse(window._layout_editing)
        self.assertEqual(window.layout_preferences.density, before)
        self.assertTrue(host.closed)

    def test_password_page_rebinds_and_returns_to_the_selected_website(self):
        window = self.window()
        first = window.tabs[0]
        second = window._new_tab(url="about:blank", return_tab=True)
        tab = window._open_passwords()
        self.ready(tab.workspace_controller)
        self.assertIs(tab.workspace_controller.source.page, second.page())
        window._switch_tab(window.tabs.index(first))
        self.assertIs(window._open_passwords(), tab)
        self.assertIs(tab.workspace_controller.source.page, first.page())
        tab.workspace_controller.source.accept()
        self.assertIs(window.tabs[window.current_tab_index], first)

    def test_hidden_page_refreshes_live_data_when_reopened(self):
        window, tab, host = self.opened("history")
        window._new_tab(url="steed:settings")
        window.history.record("https://new.example.test/", "New report")
        window._switch_tab(window.tabs.index(tab))
        self.wait_for(
            lambda: self.value(host, "document.body.textContent.includes('New report')")
        )

    def test_reload_retries_unavailable_backend_without_replaying_actions(self):
        window = self.window()
        with patch(
            "ui.management_pages.controller",
            side_effect=ValueError("Synthetic unavailable backend"),
        ):
            tab = window._new_tab(url="steed:downloads", return_tab=True)
        old = tab.workspace_controller
        self.ready(old)
        self.assertTrue(old.source.page_unavailable)
        window._reload()
        self.assertTrue(old.closed)
        self.ready(tab.workspace_controller)
        self.assertEqual(tab.workspace_controller.source.windowTitle(), "Downloads")

    def test_website_navigation_cannot_open_any_management_bridge(self):
        window = self.window()
        page = window.tabs[0].page()
        from PyQt6.QtWebEngineCore import QWebEnginePage

        for name in PAGES:
            with (
                self.subTest(page=name),
                patch.object(window, "_navigate_tab_to_url") as navigate,
            ):
                self.assertFalse(
                    page.acceptNavigationRequest(
                        QUrl("steed://" + name + "/"),
                        QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
                        True,
                    )
                )
                navigate.assert_not_called()
                self.assertIn(name, WORKSPACES)
                self.assertEqual(workspace_name("steed:" + name), name)

    def test_inputs_and_confirmation_prompts_render_html_and_return_native_values(self):
        window = self.window()
        observed = []

        def answer():
            if not window._html_dialogs:
                QTimer.singleShot(10, answer)
                return
            host = window._html_dialogs[-1]
            if not host.surface.ready:
                QTimer.singleShot(10, answer)
                return
            source = host.source
            self.assertFalse(QDialog.isVisible(source))
            observed.append(type(source))
            if isinstance(source, InputDialog):
                source.setTextValue("Synthetic answer")
                source.accept()
            else:
                source.button(MessageBox.StandardButton.No).click()

        QTimer.singleShot(10, answer)
        value, accepted = InputDialog.getText(window, "Name", "Name:")
        self.assertTrue(accepted)
        self.assertEqual(value, "Synthetic answer")
        QTimer.singleShot(10, answer)
        self.assertEqual(
            MessageBox.question(window, "Confirm", "Keep this?"),
            MessageBox.StandardButton.No,
        )
        self.assertEqual(observed, [InputDialog, MessageBox])

    def test_bookmark_search_enter_opens_a_tab_and_keeps_manager_available(self):
        window = self.window()
        window.profile_manager.registry.start_pages.add_bookmark(
            "global", "https://bookmark.example.test/", "Report"
        )
        tab = window._manage_bookmarks()
        host = tab.workspace_controller
        self.ready(host)
        token = self.control(host, host.source.search)
        selector = json.dumps('[data-control="' + token + '"]')
        self.value(
            host,
            f'const field=document.querySelector({selector}); field.value="Report"; field.dispatchEvent(new Event("input")); true',
        )
        self.wait_for(lambda: host.source.items.count() == 1)
        with patch.object(window, "_new_tab") as opened:
            self.value(
                host,
                f'document.querySelector({selector}).dispatchEvent(new KeyboardEvent("keydown", {{key:"Enter"}})); true',
            )
            self.wait_for(lambda: opened.called)
            opened.assert_called_once_with(url="https://bookmark.example.test/")
        self.assertFalse(host.closed)

    def test_html_permission_review_requires_an_explicit_user_click(self):
        self.window()
        task = self.command("task.create", objective="Review access through HTML")
        grant = self.request(task.task_id, profile="existing:work")
        self.wait_for(lambda: self.registry.task_approvals.dialog is not None)
        source = self.registry.task_approvals.dialog
        host = source.presentation_host()
        self.ready(host)
        self.assertEqual(
            self.service.store.grant(task.task_id, grant.grant_id).status, "requested"
        )
        self.assertFalse(QDialog.isVisible(source))
        self.click(host, source.approve_button)
        self.wait_for(
            lambda: (
                self.service.store.grant(task.task_id, grant.grant_id).status
                == "approved"
            )
        )

    def test_html_agent_secret_answer_is_not_published(self):
        from ui.agent_request import AgentRequestDialog

        window = self.window()
        answers = []
        source = AgentRequestDialog(
            {
                "method": "item/tool/requestUserInput",
                "params": {
                    "questions": [
                        {
                            "id": "secret",
                            "question": "Synthetic secret?",
                            "isSecret": True,
                        }
                    ]
                },
            },
            answers.append,
            window,
        )
        source.answers["secret"][1].setText("synthetic-secret-value")
        source.show()
        host = source.presentation_host()
        self.ready(host)
        self.assertNotIn(
            "synthetic-secret-value", self.value(host, "JSON.stringify(state)")
        )
        self.click(host, source.allow)
        self.wait_for(lambda: bool(answers))
        self.assertEqual(
            answers[0], {"answers": {"secret": {"answers": ["synthetic-secret-value"]}}}
        )

    def test_large_tables_page_and_refuse_stale_row_targets(self):
        from ui.html_dialog import HtmlSettingsDialog

        window = self.window()
        source = HtmlSettingsDialog(window)
        table = QTableWidget(501, 1)
        for index in range(501):
            table.setItem(index, 0, QTableWidgetItem(f"Row {index}"))
        QVBoxLayout(source).addWidget(table)
        source.show()
        host = source.presentation_host()
        self.ready(host)
        target = self.control(host, table)
        stale = self.control(host, table.item(0, 0))
        table.selectRow(0)
        host.command(
            {
                "context": host.context,
                "sequence": 1,
                "action": "page",
                "id": target,
                "value": 1,
            }
        )
        self.wait_for(lambda: self.value(host, "state.children[0].offset") == 500)
        self.assertEqual(
            self.value(host, "state.children[0].rows[0].cells[0]"), "Row 500"
        )
        self.assertEqual(table.currentRow(), -1)
        host.command(
            {
                "context": host.context,
                "sequence": 2,
                "action": "select",
                "id": target,
                "item": stale,
            }
        )
        self.assertEqual(table.currentRow(), -1)

    def test_image_previews_render_as_raster_and_clear_from_state_and_cache(self):
        from ui.html_dialog import HtmlSettingsDialog

        window = self.window()
        source = HtmlSettingsDialog(window)
        image = QImage(20, 20, QImage.Format.Format_RGB32)
        image.fill(0xFF223344)
        preview = QLabel()
        preview.setPixmap(QPixmap.fromImage(image))
        QVBoxLayout(source).addWidget(preview)
        source.show()
        host = source.presentation_host()
        self.ready(host)
        self.wait_for(
            lambda: self.value(host, "document.querySelector('img')?.complete")
        )
        self.assertTrue(
            self.value(
                host, "state.children[0].value.startsWith('data:image/png;base64,')"
            )
        )
        preview.clear()
        host.refresh_sensitive()
        self.wait_for(lambda: self.value(host, "!document.querySelector('img')"))
        self.assertEqual(host.image_cache, {})
