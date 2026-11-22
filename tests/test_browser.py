"""Regression checks using temporary profiles and local pages only."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWebEngineCore import QWebEngineFullScreenRequest
from PyQt6.QtWidgets import QDialog, QFileDialog
from qt_test_support import application, keep_directory

from browser.ad_blocker import FilterEngine
from browser.browser_window import BrowserWindow
from browser.config import Config
from browser.cookie_manager import CookieInfo
from browser.cookie_sharing import cookie_site, delete_exact
from browser.tab_manager import TabManager, TabState
from browser.utils import load_json, sanitize_profile_name, save_json
from browser.webengine import Permission as QWebEnginePermission
from main import configure_chromium_flags
from ui.cookie_dialog import CookieDialog
from ui.prompts import MessageBox as QMessageBox
from ui.settings_dialog import SettingsDialog
from ui.tab_strip import TabStrip


class PersistenceTests(unittest.TestCase):
    def test_failed_write_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tabs.json"
            save_json(path, [{"url": "https://example.org"}])
            previous = path.read_bytes()
            with patch(
                "browser.utils.json.dump", side_effect=ValueError("invalid data")
            ):
                with self.assertRaises(ValueError):
                    save_json(path, object())
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_config_updates_preserve_unknown_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("pathlib.Path.home", return_value=Path(directory)):
                config = Config()
                config.update({"custom_preference": 42, "dark_mode": True})
                restored = Config()
                self.assertTrue(restored.dark_mode)
                self.assertEqual(restored.get("custom_preference"), 42)
                save_json(config.config_file, [])
                self.assertEqual(Config().default_profile, "global")

    def test_tabs_round_trip_and_malformed_data(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TabManager(Path(directory))
            states = [TabState("https://example.org", "Example", "Work")]
            manager.save_tabs(states)
            self.assertEqual(manager.get_saved_tabs(), states)
            save_json(manager.tabs_file, [None, {"url": "https://example.org"}])
            self.assertEqual(len(manager.get_saved_tabs()), 1)
            save_json(manager.tabs_file, {})
            self.assertEqual(manager.get_saved_tabs(), [])

    def test_invalid_json_uses_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            save_json(path, {"value": 1})
            with patch(
                "browser.utils.json.load",
                side_effect=json.JSONDecodeError("bad", "", 0),
            ):
                with self.assertLogs("browser.utils", level="WARNING"):
                    self.assertEqual(load_json(path, []), [])

    def test_profile_names_stay_inside_profile_directory(self):
        for name in (".", "..", "../other", "a/b", "a\\b"):
            safe_name = sanitize_profile_name(name)
            self.assertNotIn(safe_name, (".", ".."))
            self.assertNotIn("/", safe_name)
            self.assertNotIn("\\", safe_name)


class StartupTests(unittest.TestCase):
    def test_default_flags_do_not_force_opengl(self):
        with patch.dict(os.environ, {"QTWEBENGINE_CHROMIUM_FLAGS": ""}):
            configure_chromium_flags()
            flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
            self.assertNotIn("--use-gl", flags)
            self.assertNotIn("--ignore-gpu-blocklist", flags)
            self.assertIn("WebRTCPipeWireCapturer", flags)

    def test_flags_preserve_user_options_and_merge_features(self):
        original = "--disable-gpu --enable-features=ExistingFeature"
        with patch.dict(os.environ, {"QTWEBENGINE_CHROMIUM_FLAGS": original}):
            configure_chromium_flags()
            first = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
            configure_chromium_flags()
            self.assertEqual(os.environ["QTWEBENGINE_CHROMIUM_FLAGS"], first)
            self.assertIn("--disable-gpu", first)
            self.assertEqual(first.count("--enable-features="), 1)
            self.assertIn("ExistingFeature,WebRTCPipeWireCapturer", first)

    def test_thin_overlay_scrollbars_unless_turned_off(self):
        with patch.dict(os.environ, {"QTWEBENGINE_CHROMIUM_FLAGS": ""}):
            configure_chromium_flags()
            self.assertIn(
                "FluentOverlayScrollbar", os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
            )
        with patch.dict(os.environ, {"QTWEBENGINE_CHROMIUM_FLAGS": ""}):
            configure_chromium_flags(thin_scrollbars=False)
            flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
            self.assertNotIn("FluentOverlayScrollbar", flags)
            self.assertIn("WebRTCPipeWireCapturer", flags)

    def test_one_scrollbar_style_for_steed_pages(self):
        web = Path(__file__).resolve().parents[1] / "ui" / "web"
        root = (web / "root.css").read_text(encoding="utf-8")
        self.assertIn("::-webkit-scrollbar-thumb", root)
        self.assertIn("::-webkit-scrollbar-button { display: none", root)
        # scrollbar-width/-color make Chromium skip the shared rules (only the
        # tab strip hides its scrollbar on purpose).
        for sheet in web.glob("*.css"):
            if sheet.name == "tabs.css":
                continue
            text = sheet.read_text(encoding="utf-8")
            for name in ("scrollbar-width:", "scrollbar-color:"):
                self.assertNotIn(name, text, sheet.name)


class BrowserTests(unittest.TestCase):
    def test_menu_categories_keep_shortcuts_and_profile_controls(self):
        menus = {
            action.text(): action.menu() for action in self.window.menuBar().actions()
        }
        self.assertEqual(
            list(menus), ["General", "Profile", "Tabs", "View", "History", "Agents"]
        )
        self.assertIn(self.window.reload_action, menus["Tabs"].actions())
        self.assertIn(self.window.find_action, menus["View"].actions())
        self.assertIn(self.window.agent_sidebar_action, menus["Agents"].actions())
        setup = next(
            a.menu()
            for a in menus["Agents"].actions()
            if a.text() == "Access and Setup"
        )
        self.assertIn(self.window.file_library_action, setup.actions())
        profiles = menus["Profile"]
        self.assertIn(
            self.window.profile_shortcuts_menu.menuAction(), profiles.actions()
        )
        history = next(
            action
            for action in menus["History"].actions()
            if action.text() == "Clear Browsing History…"
        )
        with patch.object(self.window.history, "clear") as clear:
            with patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ):
                history.trigger()
            clear.assert_called_once()

    def test_tab_group_menu_operates_on_current_tab(self):
        tabs = next(
            action.menu()
            for action in self.window.menuBar().actions()
            if action.text() == "Tabs"
        )
        groups = next(
            action.menu() for action in tabs.actions() if action.text() == "Tab Groups"
        )
        actions = {action.text(): action for action in groups.actions()}
        with patch(
            "browser.browser_window.QInputDialog.getText",
            return_value=("Research", True),
        ):
            actions["Set Current Tab Group…"].trigger()
        current = self.window.tabs[self.window.current_tab_index]
        self.assertEqual(current.group, "Research")
        group = self.window.tab_manager.groups[current.group_id]
        groups.aboutToShow.emit()
        actions["Collapse / Expand Current Group"].trigger()
        self.assertTrue(group.collapsed)
        actions["Remove Current Tab from Group"].trigger()
        self.assertIsNone(current.group_id)
        groups.aboutToShow.emit()
        self.assertFalse(actions["Collapse / Expand Current Group"].isEnabled())

    def test_task_permissions_menu_reports_storage_failure_without_crashing(self):
        with patch(
            "services.task_service.TaskService",
            side_effect=sqlite3.OperationalError("fixture unavailable"),
        ):
            tab = self.window._open_tasks()
        self.assertEqual(tab.workspace_name, "tasks")
        self.assertEqual(
            tab.workspace_controller.source.windowTitle(), "Page unavailable"
        )
        self.assertIsNone(self.window.profile_manager.registry._tasks)

    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.directories = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/file":
                    payload = b"verified-download\n" * 4096
                    content_type = "application/octet-stream"
                elif self.path == "/advert.js":
                    payload = b"window.adLoaded = true;"
                    content_type = "text/javascript"
                elif self.path == "/filters":
                    payload = (
                        "[Adblock Plus 2.0]\n" + "||fixture-ads.example^\n" * 20
                    ).encode()
                    content_type = "text/plain"
                elif self.path == "/frames":
                    payload = (
                        '<html><body><div class="advert">Main ad</div>'
                        '<iframe id="child" src="/frame"></iframe>'
                        '<iframe id="inline" srcdoc="<div class=inline-ad>Inline ad</div>"></iframe>'
                        f'<iframe id="cross" src="http://localhost:{self.server.server_port}/frame"></iframe>'
                        "</body></html>"
                    ).encode()
                    content_type = "text/html"
                elif self.path == "/frame":
                    payload = b'<html><body><div class="frame-ad">Frame ad</div><iframe src="/nested"></iframe></body></html>'
                    content_type = "text/html"
                elif self.path == "/nested":
                    payload = b'<html><body><div class="nested-ad">Nested ad</div></body></html>'
                    content_type = "text/html"
                else:
                    payload = b'<html><head><title>Fixture</title></head><body><div class="advert">Ad</div><div>Content</div><script src="/advert.js"></script></body></html>'
                    content_type = "text/html"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                if self.path == "/file":
                    self.send_header(
                        "Content-Disposition", 'attachment; filename="fixture.bin"'
                    )
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A deliberately blocked/cancelled request closed it.

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.directories.append(self.directory)
        keep_directory(self.directory)
        self.home_patch = patch(
            "pathlib.Path.home", return_value=Path(self.directory.name)
        )
        self.home_patch.start()
        config = Config()
        self.local_page = (
            Path(__file__).resolve().parents[1] / "codec_test.html"
        ).as_uri()
        config.update({"home_page": self.local_page, "new_tab_page": self.local_page})
        self.window = BrowserWindow("default")

    def tearDown(self):
        if not self.window._closing:
            self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    # Chromium warm-up plus the DevTools cookie snapshot costs several seconds
    # on a cold engine, so the default budget is generous on purpose.
    SNAPSHOT_TIMEOUT = 30

    # Budget for an assertion that must observe a complete engine round trip
    # while other windows from earlier tests are still shutting down.
    SNAPSHOT_ROUND_TRIP = 120

    def wait_for(self, predicate, timeout=None):
        deadline = time.monotonic() + (timeout or self.SNAPSHOT_TIMEOUT)
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for local Chromium fixture")

    def javascript(self, script):
        result = []
        self.window.tabs[self.window.current_tab_index].page().runJavaScript(
            script, result.append
        )
        self.wait_for(lambda: bool(result))
        return result[0]

    def test_closed_tab_restores_position_group_and_identity(self):
        window = self.window
        window._new_tab(self.local_page)
        window._set_tab_group_existing(1, "Work")
        identity = window.tabs[1].tab_id
        window._new_tab(self.local_page)
        window._close_tab(1)
        window._restore_closed_tab()
        self.assertEqual(window.tabs[1].tab_id, identity)
        self.assertEqual(window.tabs[1].group, "Work")
        self.assertEqual(window.current_tab_index, 1)
        self.assertFalse(window.restore_tab_action.isEnabled())

    def test_closed_tab_profile_isolation(self):
        self.window._new_tab(self.local_page)
        self.window._close_tab(1)
        self.window._switch_to_profile("search")
        self.window._restore_closed_tab()
        self.assertEqual(len(self.window.tabs), 1)
        self.window._switch_to_profile("default")
        self.window._restore_closed_tab()
        self.assertEqual(len(self.window.tabs), 2)

    def test_groups_rename_collapse_reorder_delete_and_restore(self):
        window = self.window
        window._new_tab(self.local_page)
        window._new_tab(self.local_page)
        window._set_tab_group_existing(1, "Work")
        window._set_tab_group_existing(2, "Work")
        group_id = window.tabs[1].group_id
        window.rename_group(group_id, "Renamed")
        window.toggle_group(group_id)
        self.assertTrue(window.tab_bar.is_tab_visible(1))
        self.assertFalse(window.tab_bar.is_tab_visible(2))
        window.move_group(group_id)
        self.assertEqual(window.tabs[0].group_id, group_id)
        self.assertEqual(window.tabs[1].group_id, group_id)
        window._switch_to_profile("search")
        window._switch_to_profile("default")
        self.assertEqual(window.tabs[0].group, "Renamed")
        self.assertTrue(window.tab_manager.groups[group_id].collapsed)
        window.delete_group(group_id)
        self.assertTrue(all(tab.group_id is None for tab in window.tabs))
        self.assertTrue(all(window.tab_bar.is_tab_visible(i) for i in range(3)))

    def test_windows_share_profiles_until_last_owner_closes(self):
        second = BrowserWindow("default")
        profile = self.window.profile_manager.current_profile
        self.assertIs(second.profile_manager.current_profile, profile)
        second.close()
        self.assertFalse(sip.isdeleted(profile))
        self.window.close()
        self.assertTrue(sip.isdeleted(profile))

    def test_startup_sharing_flag_gates_all_profiles_mode(self):
        """The startup checkbox must also gate all-profiles sharing.

        Otherwise the preference looks functional while inheritance is applied
        anyway, which can overwrite local logins on every launch.
        """
        manager = self.window.profile_manager
        registry = manager.registry
        self.window.config.update(
            {
                "global_cookie_sharing_mode": "all_profiles",
                "global_cookie_sharing_apply_on_startup": False,
            }
        )
        manager.refresh_settings()
        self.assertFalse(registry.sharing_applies_to_all())
        self.assertEqual(registry.sharing.enabled, set())
        self.window.config.set("global_cookie_sharing_apply_on_startup", True)
        manager.refresh_settings()
        self.assertTrue(registry.sharing_applies_to_all())
        self.assertIn("default", registry.sharing.enabled)

    def test_adblock_settings_change_is_visible_to_request_path(self):
        service = self.window.adblock
        self.assertTrue(service.applies("https://fixture.example/page"))
        self.window.config.set("adblock_enabled", False)
        self.assertFalse(
            service.applies("https://fixture.example/page"),
            "config writes must invalidate the cached request-path state",
        )
        self.window.config.set("adblock_enabled", True)
        self.window.config.set("adblock_exceptions", ["fixture.example"])
        self.assertTrue(service.host_excluded("fixture.example"))
        self.assertTrue(service.host_excluded("www.fixture.example"))
        self.assertFalse(service.host_excluded("notfixture.example"))
        self.assertFalse(service.applies("https://fixture.example/page"))

    def test_closing_the_last_tab_closes_the_window(self):
        window = BrowserWindow()
        window.show()
        self.assertEqual(len(window.tabs), 1)
        window._close_tab(0)
        self.wait_for(lambda: sip.isdeleted(window) or window._closing)
        self.assertTrue(window._closing)
        # Other windows stay open.
        self.assertFalse(self.window._closing)

    def test_restoring_into_collapsed_group_expands_it_deliberately(self):
        window = self.window
        window._new_tab(self.local_page)
        window._new_tab(self.local_page)
        window._set_tab_group_existing(1, "Work")
        window._set_tab_group_existing(2, "Work")
        group_id = window.tabs[1].group_id
        window._close_tab(2)
        window.toggle_group(group_id)
        self.assertTrue(window.tab_manager.groups[group_id].collapsed)
        window._restore_closed_tab()
        self.assertFalse(
            window.tab_manager.groups[group_id].collapsed,
            "a restored tab must not stay hidden inside a collapsed group",
        )
        restored = next(t for t in window.tabs if t.group_id == group_id)
        self.assertTrue(window.tab_bar.is_tab_visible(window.tabs.index(restored)))

    def test_shutdown_confirms_active_downloads_only_for_last_window(self):
        """Closing must ask exactly when this window's close ends the downloads."""
        second = BrowserWindow()
        self.addCleanup(second.close)
        self.assertIs(
            second.download_manager,
            self.window.download_manager,
            "windows on one registry share download state",
        )
        with (
            patch.object(self.window.download_manager, "active", return_value=["key"]),
            patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No
            ) as question,
        ):
            self.window.close()
        self.assertFalse(
            question.called,
            "another window keeps the shared downloads running, so nothing is cancelled",
        )
        # The last window closes every remaining owner, so it must ask; declining
        # leaves the window open and its downloads intact.
        with (
            patch.object(self.window.download_manager, "active", return_value=["key"]),
            patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No
            ) as question,
        ):
            second.close()
        self.assertTrue(
            question.called, "the last window must ask before cancelling downloads"
        )
        self.assertFalse(second._closing, "declining must abort the close")

    def test_permission_and_picker_dialogs_open_after_the_engine_callback(self):
        """Asking inside the engine's callback hung the window on a blank dialog."""
        permission = Mock()
        permission.isValid.return_value = True
        request = Mock()
        with (
            patch.object(self.window, "_ask_permission", return_value=True) as asked,
            patch.object(self.window, "_pick_desktop_media") as picked,
            # The real copy is covered by test_permission_prompts.
            patch("browser.webengine.keep_permission", side_effect=lambda p: p),
        ):
            self.window._on_permission_requested(permission)
            self.window._on_desktop_media_requested(request)
            asked.assert_not_called()
            picked.assert_not_called()
            self.wait_for(lambda: permission.grant.called and picked.called)
        picked.assert_called_once_with(request)
        self.window._pending_desktop_request = None

    def test_overlapping_media_picker_declines_second_request(self):
        """A second request while the picker is open must be declined, not lost."""
        first = Mock()
        first.screensModel.return_value.rowCount.return_value = 0
        first.windowsModel.return_value.rowCount.return_value = 0
        second = Mock()

        def hold_dialog(dialog):
            dialog = getattr(dialog, "source", dialog)
            self.window._on_desktop_media_requested(second)
            dialog.reject()
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", hold_dialog):
            self.window._pick_desktop_media(first)
        second.cancel.assert_called_once()
        first.cancel.assert_called_once()
        self.assertIsNone(self.window._pending_desktop_request)

    def test_user_agent_applies_at_creation_and_clear_restores_default(self):
        manager = self.window.profile_manager
        original = manager.current_profile.httpUserAgent()
        self.assertNotIn("Chrome/122.0.0.0", original)
        self.window.config.set("user_agent_override", "Fixture-UA")
        manager.refresh_settings()
        manager.create_profile("ua-test")
        for profile in (
            manager.get_profile("ua-test"),
            manager.global_profile,
            manager.search_profile,
        ):
            self.assertEqual(profile.httpUserAgent(), "Fixture-UA")
        self.window.config.set("user_agent_override", "")
        manager.refresh_settings()
        self.assertEqual(manager.current_profile.httpUserAgent(), original)

    def test_native_global_cookies_and_local_override(self):
        manager = self.window.profile_manager
        sharing = manager.registry.sharing
        global_store = manager.global_profile.cookieStore()
        target_store = manager.current_profile.cookieStore()
        cookie = QNetworkCookie(b"test_session", b"shared")
        cookie.setDomain(".cookie-fixture.example")
        cookie.setPath("/")
        global_store.setCookie(cookie, QUrl("https://cookie-fixture.example"))
        self.wait_for(
            lambda: any(
                c.value() == b"shared" for c in sharing.observed["default"].values()
            )
        )
        cookie.setValue(b"independent")
        target_store.setCookie(cookie, QUrl("https://cookie-fixture.example"))
        self.wait_for(lambda: sharing.local("default", "cookie-fixture.example"))
        cookie.setValue(b"changed-global")
        global_store.setCookie(cookie, QUrl("https://cookie-fixture.example"))
        self.wait_for(
            lambda: any(
                c.value() == b"changed-global" for c in sharing.global_cookies.values()
            )
        )
        values = {
            bytes(c.value())
            for c in sharing.observed["default"].values()
            if cookie_site(c) == "cookie-fixture.example"
        }
        self.assertEqual(values, {b"independent"})

    def test_native_host_only_cookie_scope(self):
        sharing = self.window.profile_manager.registry.sharing
        cookie = QNetworkCookie(b"host_cookie", b"value")
        cookie.setPath("/")
        self.window.profile_manager.global_profile.cookieStore().setCookie(
            cookie, QUrl("https://host-fixture.example")
        )
        self.wait_for(
            lambda: any(
                bytes(c.name()) == b"host_cookie"
                for c in sharing.observed["default"].values()
            )
        )
        target = next(
            c
            for c in sharing.observed["default"].values()
            if bytes(c.name()) == b"host_cookie"
        )
        self.assertEqual(target.domain(), "host-fixture.example")

    def test_native_global_update_and_logout_delete_inherited_cookie(self):
        manager = self.window.profile_manager
        sharing = manager.registry.sharing
        cookie = QNetworkCookie(b"session", b"first")
        cookie.setDomain(".update-fixture.example")
        cookie.setPath("/")
        store = manager.global_profile.cookieStore()
        origin = QUrl("https://update-fixture.example")
        store.setCookie(cookie, origin)
        self.wait_for(
            lambda: any(
                bytes(c.value()) == b"first"
                for c in sharing.observed["default"].values()
            )
        )
        cookie.setValue(b"second")
        store.setCookie(cookie, origin)
        self.wait_for(
            lambda: any(
                bytes(c.value()) == b"second"
                for c in sharing.observed["default"].values()
            )
        )
        self.assertFalse(sharing.local("default", "update-fixture.example"))
        store.deleteCookie(cookie, origin)
        self.wait_for(lambda: not sharing.observed["default"])
        self.assertFalse(sharing.local("default", "update-fixture.example"))

    def test_inheritance_persists_and_global_loads_without_visible_tab(self):
        data = Path(self.directory.name) / "restart"
        data.mkdir()
        script = Path(__file__).with_name("profile_restart.py")
        for phase in ("write", "read"):
            result = subprocess.run(
                [sys.executable, str(script), "--data", str(data), "--phase", phase],
                capture_output=True,
                text=True,
                timeout=60,  # A busy machine starts the engine slowly.
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_startup_sharing_can_be_disabled(self):
        self.window.close()
        Config().set("global_cookie_sharing_apply_on_startup", False)
        self.window = BrowserWindow()
        sharing = self.window.profile_manager.registry.sharing
        self.assertEqual(sharing.enabled, set())
        self.window._switch_to_profile("default")
        self.assertIn("default", sharing.enabled)

    def test_cookie_viewer_filtered_selection_targets_displayed_cookie(self):
        manager = self.window.cookie_manager
        first = QNetworkCookie(b"first", b"1")
        first.setDomain(".first.example")
        second = QNetworkCookie(b"second", b"2")
        second.setDomain(".second.example")
        manager.cookies = [CookieInfo(first), CookieInfo(second)]
        dialog = CookieDialog(manager, self.window)
        dialog.filter_input.setText("second")
        dialog.table.selectRow(0)
        with (
            patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ),
            patch.object(manager, "delete_cookie") as delete,
        ):
            dialog.delete_selected()
        self.assertEqual(delete.call_args.args[0].name, "second")
        dialog.accept()

    def test_cookie_enumeration_preserves_identical_rows_but_clears_changed_targets(
        self,
    ):
        manager = self.window.cookie_manager
        cookie = QNetworkCookie(b"stable", b"first")
        cookie.setDomain(".row-fixture.example")
        manager.cookies = [CookieInfo(cookie)]
        dialog = CookieDialog(manager, self.window)
        dialog.table.selectRow(0)
        item = dialog.table.item(0, 0)
        dialog.update_cookies([CookieInfo(QNetworkCookie(cookie))])
        self.assertIs(dialog.table.item(0, 0), item)
        self.assertTrue(dialog.table.selectedItems())
        replacement = QNetworkCookie(cookie)
        replacement.setValue(b"changed")
        dialog.update_cookies([CookieInfo(replacement)])
        self.assertFalse(dialog.table.selectedItems())
        self.assertEqual(dialog.table.item(0, 2).text(), "changed")
        dialog.accept()

    def test_exact_cookie_deletion_preserves_same_name_on_other_path(self):
        manager = self.window.cookie_manager
        store = manager.cookie_store
        root = QNetworkCookie(b"same_name", b"root")
        root.setDomain(".paths-fixture.example")
        root.setPath("/")
        nested = QNetworkCookie(root)
        nested.setPath("/nested")
        nested.setValue(b"nested")
        # Mirror the store's own signals instead of CookieManager: the manager
        # re-reads through the private DevTools adapter, whose enumeration costs
        # seconds, and this test covers exact-path deletion rather than that
        # adapter. Qt's signals are authoritative for cookies written here.
        seen = {}

        def record(cookie):
            seen[(bytes(cookie.name()), cookie.domain(), cookie.path())] = bytes(
                cookie.value()
            )

        def forget(cookie):
            seen.pop((bytes(cookie.name()), cookie.domain(), cookie.path()), None)

        store.cookieAdded.connect(record)
        store.cookieRemoved.connect(forget)

        def nested_paths():
            return {path for (name, _, path) in seen if name == b"same_name"}

        try:
            store.setCookie(root, QUrl("http://paths-fixture.example"))
            store.setCookie(nested, QUrl("http://paths-fixture.example/nested"))
            self.wait_for(lambda: nested_paths() == {"/", "/nested"})
            self.window.profile_manager.protect_cookie_deletion([nested])
            delete_exact(store, nested)
            self.wait_for(lambda: nested_paths() == {"/"})
            self.assertEqual(
                seen[(b"same_name", ".paths-fixture.example", "/")],
                b"root",
                "deleting /nested must not remove the same name on /",
            )
        finally:
            # Disconnect while the store is still alive; tearDown deletes the
            # profile afterwards, so a deferred disconnect would touch freed
            # engine objects.
            store.cookieAdded.disconnect(record)
            store.cookieRemoved.disconnect(forget)

    def test_real_download_completes_and_empty_save_cancels(self):
        destination = Path(self.directory.name) / "fixture.bin"
        page = self.window.tabs[0].page()
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=(str(destination), "")
        ):
            page.download(QUrl(self.origin + "/file"))
            self.wait_for(lambda: bool(self.window.download_manager.records))
            self.wait_for(
                lambda: any(
                    r.state == "DownloadCompleted"
                    for r in self.window.download_manager.records.values()
                )
            )
        self.assertEqual(destination.read_bytes(), b"verified-download\n" * 4096)
        with patch.object(
            QFileDialog, "getSaveFileName", return_value=("", "")
        ) as picker:
            page.download(QUrl(self.origin + "/file"))
            self.wait_for(lambda: picker.called)
            self.wait_for(lambda: not self.window.download_manager.active())

    def test_actual_request_filter_and_cosmetic_hiding(self):
        self.window.adblock.engine = FilterEngine(
            ["/advert.js$script", "127.0.0.1##.advert"]
        )
        loaded = []
        self.window.tabs[0].loadFinished.connect(loaded.append)
        self.window.tabs[0].setUrl(QUrl(self.origin + "/index"))
        self.wait_for(lambda: any(loaded) and self.window.adblock.count > 0)
        self.wait_for(
            lambda: (
                self.javascript(
                    "getComputedStyle(document.querySelector('.advert')).display"
                )
                == "none"
            )
        )
        self.assertFalse(self.javascript("window.adLoaded === true"))
        self.window.config.set("adblock_exceptions", ["127.0.0.1"])
        loaded.clear()
        self.window.tabs[0].reload()
        self.wait_for(lambda: bool(loaded))
        self.assertTrue(self.javascript("window.adLoaded === true"))

    def load_frame_fixture(self):
        self.window.adblock.engine = FilterEngine(
            [
                "127.0.0.1##.advert",
                "##.frame-ad",
                "##.nested-ad",
                "##.inline-ad",
            ]
        )
        loaded = []
        self.window.tabs[0].loadFinished.connect(loaded.append)
        self.window.tabs[0].setUrl(QUrl(self.origin + "/frames"))
        self.wait_for(lambda: any(loaded))

    def test_cosmetic_hiding_applies_to_nested_and_srcdoc_frames(self):
        self.load_frame_fixture()
        script = """(() => {
            const child = document.querySelector('#child').contentDocument;
            const nested = child?.querySelector('iframe')?.contentDocument;
            const inline = document.querySelector('#inline').contentDocument;
            if (!nested || !inline) return [];
            const display = (doc, selector) => doc.defaultView.getComputedStyle(doc.querySelector(selector)).display;
            return [display(document, '.advert'), display(child, '.frame-ad'),
                    display(nested, '.nested-ad'), display(inline, '.inline-ad')];
        })();"""
        self.wait_for(lambda: self.javascript(script) == ["none"] * 4)
        self.assertTrue(
            self.javascript("document.querySelector('#cross').contentDocument === null")
        )
        # Generic rules discovered in a child belong to that document only.
        parent_css = self.javascript(
            "document.getElementById('__custom_browser_adblock').textContent"
        )
        self.assertNotIn(".frame-ad", parent_css)
        self.assertNotIn(".nested-ad", parent_css)

    def test_cosmetic_frame_exception_takes_effect_after_reload(self):
        self.load_frame_fixture()
        self.wait_for(
            lambda: self.javascript(
                "document.querySelector('#child').contentDocument.getElementById('__custom_browser_adblock') !== null"
            )
        )
        self.window.config.set("adblock_exceptions", ["127.0.0.1"])
        loaded = []
        self.window.tabs[0].loadFinished.connect(loaded.append)
        self.window.tabs[0].reload()
        self.wait_for(lambda: any(loaded))
        self.assertEqual(
            self.javascript("""(() => {
                const child = document.querySelector('#child').contentDocument;
                const nested = child.querySelector('iframe').contentDocument;
                return [document, child, nested].map(doc => doc.getElementById('__custom_browser_adblock') === null);
            })();"""),
            [True, True, True],
        )
        self.assertNotEqual(
            self.javascript("""(() => {
                const child = document.querySelector('#child').contentDocument;
                return child.defaultView.getComputedStyle(child.querySelector('.frame-ad')).display;
            })();"""),
            "none",
        )

    def test_filter_list_update_compiles_in_worker(self):
        service = self.window.adblock
        with patch("browser.ad_blocker.SOURCES", (self.origin + "/filters",)):
            service.update()
            self.wait_for(lambda: not service.updating)
        self.assertTrue(
            service.engine.blocked(
                "https://fixture-ads.example/ad", "https://example.org", "script"
            )
        )
        self.assertTrue(service.cache.exists())

    def test_quick_switch_available_before_first_profile_switch(self):
        manager = self.window.profile_manager
        self.assertEqual(manager.current_profile_name, "default")
        actions = self.window.profile_shortcuts_menu.actions()
        self.assertEqual(
            [action.text() for action in actions],
            ["default", "Global"],
        )
        # A new window must list profiles that already exist, without switching.
        manager.create_profile("work")
        second = BrowserWindow()
        try:
            actions = second.profile_shortcuts_menu.actions()
            self.assertEqual(
                [action.text() for action in actions],
                ["default", "Global", "work"],
            )
            second.show()
            second.activateWindow()
            self.wait_for(second.isActiveWindow)
            second.address_bar.focus_address()
            QTest.keyClick(
                second.address_bar.address_input,
                Qt.Key.Key_3,
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
            )
            self.app.processEvents()
            self.assertEqual(second.profile_manager.current_profile_name, "work")
        finally:
            second.close()

    def test_tabs_and_profile_switching(self):
        self.window._new_tab(self.local_page, "Codec")
        self.window.tabs[-1].group = "Media"
        self.window._save_current_tabs()
        self.assertEqual(len(self.window.tab_manager.get_saved_tabs()), 2)
        self.window.profile_manager.create_profile("work")
        self.window._switch_to_profile("work")
        self.assertEqual(len(self.window.tabs), 1)
        self.window._switch_to_profile("default")
        self.assertEqual(len(self.window.tabs), 2)
        self.assertEqual(self.window.tabs[-1].group, "Media")
        self.window._close_tab(1)
        self.assertEqual(len(self.window.tabs), 1)

    def test_incognito_does_not_save_tabs(self):
        self.window._switch_to_profile("search")
        self.assertTrue(self.window.profile_manager.current_profile.isOffTheRecord())
        self.window._new_tab(self.local_page)
        self.assertFalse((Path.cwd() / "tabs.json").exists())
        self.assertFalse((self.window.config.profiles_dir / "search").exists())

    def test_group_order_follows_tab_order(self):
        bar = TabStrip()
        moves = []
        bar.tab_moved.connect(lambda a, b: moves.append((a, b)))
        for index, name in enumerate(("A", "B", "C")):
            bar.insert_tab(index, name)
        bar.set_current_tab(0)
        bar.set_tab_group(0, "First")
        bar.set_tab_group(2, "Last")
        bar.move_tab(0, 2)
        self.assertEqual(moves, [(0, 2)])
        self.assertEqual(bar.get_current_index(), 2)
        self.assertEqual(bar.groups, {0: None, 1: "Last", 2: "First"})
        bar.insert_tab(1, "Inserted")
        self.assertEqual(bar.groups, {0: None, 1: None, 2: "Last", 3: "First"})
        self.assertEqual(bar.get_current_index(), 3)
        bar.clear_all_tabs()
        self.assertEqual(bar.groups, {})
        bar.dispose()
        bar.deleteLater()

    @unittest.skipIf(QWebEnginePermission is None, "Requires Qt 6.8 permissions")
    def test_permission_response_uses_request_object(self):
        permission = Mock()
        permission.permissionType.return_value = (
            QWebEnginePermission.PermissionType.Notifications
        )
        permission.origin.return_value = QUrl("https://example.org")
        with patch.object(self.window, "_ask_permission", return_value=True):
            self.window._resolve_permission(permission)
        permission.grant.assert_called_once()
        permission.deny.assert_not_called()

    @unittest.skipIf(QWebEnginePermission is None, "Requires Qt 6.8 permissions")
    def test_website_permission_uses_requesting_page_profile_and_is_reusable(self):
        page = self.window.tabs[0].page()
        original = self.window.profile_manager.registry.site_permission_context(
            page.profile()
        )
        self.window.profile_manager.create_profile("other")
        self.window.profile_manager.switch_to_profile("other")
        feature = QWebEnginePermission.PermissionType.MediaVideoCapture
        with patch("browser.browser_window.SitePermissionDialog") as factory:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.remember.currentData.return_value = "profile"
            self.assertTrue(
                self.window._ask_permission(QUrl("https://example.org"), feature, page)
            )
            self.assertEqual(factory.call_args.args[2], original["profile"])
        rules = self.window.profile_manager.registry.site_permissions.rules()
        self.assertEqual(rules[0]["key"], original["key"])
        with patch("browser.browser_window.SitePermissionDialog") as factory:
            self.assertTrue(
                self.window._ask_permission(QUrl("https://example.org"), feature, page)
            )
            factory.assert_not_called()

    @unittest.skipIf(QWebEnginePermission is None, "Requires Qt 6.8 permissions")
    def test_allow_once_and_denial_never_save_website_rules(self):
        feature = QWebEnginePermission.PermissionType.Notifications
        with patch("browser.browser_window.SitePermissionDialog") as factory:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.remember.currentData.return_value = "once"
            self.assertTrue(
                self.window._ask_permission(QUrl("https://example.org"), feature)
            )
            factory.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.assertFalse(
                self.window._ask_permission(QUrl("https://example.org"), feature)
            )
            self.assertEqual(factory.call_count, 2)
        self.assertFalse(self.window.profile_manager.registry.site_permissions.rules())

    @unittest.skipIf(QWebEnginePermission is None, "Requires Qt 6.8 permissions")
    def test_website_persistence_failure_denies_engine_request(self):
        permission = Mock()
        permission.permissionType.return_value = (
            QWebEnginePermission.PermissionType.Notifications
        )
        permission.origin.return_value = QUrl("https://example.org")
        with (
            patch("browser.browser_window.SitePermissionDialog") as factory,
            patch(
                "services.site_permissions.save_json", side_effect=OSError("fixture")
            ),
            patch.object(QMessageBox, "warning"),
        ):
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.remember.currentData.return_value = "global"
            self.window._resolve_permission(permission, self.window.tabs[0].page())
        permission.deny.assert_called_once()
        permission.grant.assert_not_called()
        self.assertFalse(self.window.profile_manager.registry.site_permissions.rules())

    @unittest.skipIf(QWebEnginePermission is None, "Requires Qt 6.8 permissions")
    def test_native_permission_policy_always_defers_to_steed_choices(self):
        registry = self.window.profile_manager.registry
        for profile in (*registry.profiles.values(), registry.search_profile):
            self.assertEqual(
                profile.persistentPermissionsPolicy(),
                profile.PersistentPermissionsPolicy.AskEveryTime,
            )

    def test_global_browser_profile_has_its_own_website_permission_identity(self):
        registry = self.window.profile_manager.registry
        context = registry.site_permission_context(registry.global_profile)
        self.assertEqual(context["profile"], "global")
        self.assertFalse(context["private"])
        self.assertEqual(len(context["key"]), 32)
        self.assertNotEqual(
            context["key"],
            registry.site_permission_context(
                self.window.profile_manager.current_profile
            )["key"],
        )

    @unittest.skipIf(QWebEnginePermission is None, "Requires Qt 6.8 permissions")
    def test_shutdown_during_website_prompt_does_not_persist_choice(self):
        feature = QWebEnginePermission.PermissionType.MediaVideoCapture

        def close_during_prompt():
            self.window._closing = True
            return QDialog.DialogCode.Accepted

        try:
            with patch("browser.browser_window.SitePermissionDialog") as factory:
                factory.return_value.exec.side_effect = close_during_prompt
                factory.return_value.remember.currentData.return_value = "global"
                self.assertFalse(
                    self.window._ask_permission(QUrl("https://example.org"), feature)
                )
            self.assertFalse(
                self.window.profile_manager.registry.site_permissions.rules()
            )
        finally:
            self.window._closing = False

    def test_fullscreen_enter_and_exit_follow_requested_state(self):
        request = Mock(spec=QWebEngineFullScreenRequest)
        initial_state = self.window.windowState()
        request.toggleOn.return_value = True
        self.window._on_fullscreen_requested(request)
        self.assertTrue(self.window._was_fullscreen)
        request.toggleOn.return_value = False
        self.window._on_fullscreen_requested(request)
        self.assertFalse(self.window._was_fullscreen)
        self.assertEqual(self.window.windowState(), initial_state)
        self.assertEqual(request.accept.call_count, 2)
        request.reject.assert_not_called()

    def test_repeated_fullscreen_entry_does_not_toggle_off(self):
        request = Mock(spec=QWebEngineFullScreenRequest)
        request.toggleOn.return_value = True
        self.window._on_fullscreen_requested(request)
        saved_geometry = self.window._saved_geometry
        self.window._on_fullscreen_requested(request)
        self.assertTrue(self.window._was_fullscreen)
        self.assertEqual(self.window._saved_geometry, saved_geometry)
        self.window._exit_fullscreen()

    def test_fullscreen_exit_while_normal_is_harmless(self):
        request = Mock(spec=QWebEngineFullScreenRequest)
        request.toggleOn.return_value = False
        self.window._on_fullscreen_requested(request)
        self.assertFalse(self.window._was_fullscreen)
        request.accept.assert_called_once()
        request.reject.assert_not_called()

    def test_media_picker_cancel_calls_request_once(self):
        request = Mock()
        request.screensModel.return_value.rowCount.return_value = 0
        request.windowsModel.return_value.rowCount.return_value = 0

        def reject_dialog(dialog):
            dialog = getattr(dialog, "source", dialog)
            dialog.reject()
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", reject_dialog):
            self.window._pick_desktop_media(request)
        request.cancel.assert_called_once()

    def test_media_picker_selected_source_is_not_cancelled_on_cleanup(self):
        for screen in (True, False):
            with self.subTest(screen=screen):
                request = Mock()
                request.screensModel.return_value.rowCount.return_value = 1
                request.windowsModel.return_value.rowCount.return_value = 1

                def select_source(dialog, screen=screen):
                    dialog = getattr(dialog, "source", dialog)
                    items = dialog.screens_list if screen else dialog.windows_list
                    button = dialog.share_screen if screen else dialog.share_window
                    items.setCurrentRow(0)
                    button.click()
                    return QDialog.DialogCode.Accepted

                with patch.object(QDialog, "exec", select_source):
                    self.window._pick_desktop_media(request)
                selected = request.selectScreen if screen else request.selectWindow
                selected.assert_called_once()
                request.cancel.assert_not_called()
                self.assertIsNone(self.window._pending_desktop_request)

    def test_media_picker_construction_failure_cancels_request(self):
        request = Mock()
        with patch(
            "browser.browser_window.DesktopMediaDialog",
            side_effect=RuntimeError("no picker"),
        ):
            with self.assertRaisesRegex(RuntimeError, "no picker"):
                self.window._pick_desktop_media(request)
        request.cancel.assert_called_once()
        self.assertIsNone(self.window._pending_desktop_request)

    def test_global_cookie_all_profiles_path(self):
        manager = self.window.profile_manager
        cookie = QNetworkCookie(b"session", b"test")
        cookie.setDomain("example.org")
        cookie.setPath("/")
        self.window.config.set("global_cookie_sharing_mode", "all_profiles")
        manager.refresh_settings()
        sharing = manager.registry.sharing
        with patch.object(sharing, "copy") as apply:
            sharing.global_added(cookie)
        apply.assert_called_once()
        self.assertEqual(len(sharing.global_cookies), 1)

    def test_settings_save_once(self):
        dialog = SettingsDialog(self.window.config)
        self.assertTrue(dialog.state["preferences"]["google_login_compatibility"])
        dialog.set_preference("google_login_compatibility", False)
        with patch.object(self.window.config, "save_config") as save:
            dialog.apply()
        save.assert_called_once()
        self.assertFalse(self.window.config.get("google_login_compatibility"))
        dialog.reject()
        dialog.deleteLater()

    def test_ctrl_r_and_f5_reload_active_tab_from_both_focus_locations(self):
        self.window._new_tab(self.local_page)
        active, inactive = self.window.tabs[1], self.window.tabs[0]
        self.window.show()
        self.window.activateWindow()
        self.wait_for(self.window.isActiveWindow)
        with (
            patch.object(active, "reload") as reload,
            patch.object(inactive, "reload") as other,
        ):
            self.window.address_bar.focus_address()
            QTest.keyClick(
                self.window.address_bar.address_input,
                Qt.Key.Key_R,
                Qt.KeyboardModifier.ControlModifier,
            )
            active.setFocus()
            QTest.keyClick(active, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(active, Qt.Key.Key_F5)
        self.assertEqual(reload.call_count, 3)
        other.assert_not_called()

    def _find_fixture(self, html="<p>Alpha alpha ALPHA</p>"):
        self.window.show()
        self.window.activateWindow()
        self.wait_for(self.window.isActiveWindow)
        page = self.window.tabs[self.window.current_tab_index].page()
        finished = QSignalSpy(page.loadFinished)
        page.setHtml(
            f"<html><title>Find fixture</title><body>{html}</body></html>",
            QUrl(self.origin),
        )
        self.wait_for(lambda: len(finished) > 0)
        self.window.tabs[self.window.current_tab_index].setFocus()
        QTest.keyClick(
            self.window.tabs[self.window.current_tab_index],
            Qt.Key.Key_F,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.assertTrue(self.window.find_bar.isVisible())
        self.assertTrue(self.window.find_bar.input.hasFocus())
        return self.window.find_bar

    def test_find_in_page_shortcuts_counts_direction_case_and_escape(self):
        bar = self._find_fixture()
        QTest.keyClicks(bar.input, "alpha")
        self.wait_for(lambda: bar.result_label.text() == "1 of 3")
        QTest.keyClick(bar.input, Qt.Key.Key_Return)
        self.wait_for(lambda: bar.result_label.text() == "2 of 3")
        QTest.keyClick(bar.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.wait_for(lambda: bar.result_label.text() == "1 of 3")
        QTest.keyClick(bar.input, Qt.Key.Key_F3)
        self.wait_for(lambda: bar.result_label.text() == "2 of 3")
        QTest.keyClick(bar.input, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
        self.wait_for(lambda: bar.result_label.text() == "1 of 3")
        bar.match_case.setChecked(True)
        self.wait_for(lambda: bar.result_label.text() == "1 of 1")
        bar.input.setText("absent-word")
        self.wait_for(lambda: bar.result_label.text() == "No matches")
        bar.input.clear()
        self.assertEqual(bar.result_label.text(), "")
        self.assertFalse(bar.next_button.isEnabled())
        page = self.window.tabs[self.window.current_tab_index]
        page.setFocus()
        QTest.keyClick(page, Qt.Key.Key_Escape)
        self.assertFalse(bar.isVisible())

    def test_find_in_page_follows_active_tab_navigation_and_clears_on_profile_switch(
        self,
    ):
        bar = self._find_fixture()
        bar.input.setText("alpha")
        self.wait_for(lambda: bar.result_label.text() == "1 of 3")
        self.window._new_tab("about:blank")
        page = self.window.tabs[self.window.current_tab_index].page()
        finished = QSignalSpy(page.loadFinished)
        page.setHtml("<p>alpha alpha</p>", QUrl(self.origin))
        self.wait_for(lambda: len(finished) > 0 and bar.result_label.text() == "1 of 2")
        self.window._switch_tab(0)
        self.wait_for(lambda: bar.result_label.text() in ("1 of 3", "2 of 3", "3 of 3"))
        page = self.window.tabs[0].page()
        finished = QSignalSpy(page.loadFinished)
        page.setHtml("<p>No matching text</p>", QUrl(self.origin))
        self.wait_for(
            lambda: len(finished) > 0 and bar.result_label.text() == "No matches"
        )
        self.window._switch_to_profile("search")
        self.assertFalse(bar.isVisible())
        self.assertEqual(bar.input.text(), "")

    def test_find_in_page_hides_for_fullscreen_and_restores_search(self):
        bar = self._find_fixture()
        bar.input.setText("alpha")
        self.wait_for(lambda: bar.result_label.text() == "1 of 3")
        self.window._enter_fullscreen()
        self.assertFalse(bar.isVisible())
        self.window._exit_fullscreen()
        self.assertTrue(bar.isVisible())
        self.wait_for(lambda: bar.result_label.text().endswith("of 3"))

    def test_find_in_page_discards_late_results_after_query_change_and_close(self):
        bar = self._find_fixture()
        callbacks = []

        def delayed(page, text, **options):
            if text:
                callbacks.append((text, options["callback"]))

        with patch.object(self.window.navigation, "find", side_effect=delayed):
            bar.input.setText("first-query")
            bar.find_next()
            bar.input.setText("second-query")
            bar.find_next()
            callbacks[0][1](True)
            self.app.processEvents()
            self.assertEqual(callbacks[-1][0], "second-query")
            self.assertEqual(bar.result_label.text(), "Searching…")
            callbacks[-1][1](False)
            self.app.processEvents()
            self.assertEqual(bar.result_label.text(), "No matches")
            bar.find_next()
            bar.close_find()
            callbacks[-1][1](True)
            self.app.processEvents()
            self.assertEqual(bar.result_label.text(), "")

    def test_zoom_shortcuts_active_tab_limits_reset_and_toolbar(self):
        self.window._new_tab(self.local_page)
        active, inactive = self.window.tabs[1], self.window.tabs[0]
        self.window.show()
        self.window.activateWindow()
        self.wait_for(self.window.isActiveWindow)
        self.window.address_bar.focus_address()
        QTest.keyClick(
            self.window.address_bar.address_input,
            Qt.Key.Key_Equal,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.assertAlmostEqual(active.zoomFactor(), 1.1)
        self.assertEqual(self.window.address_bar.zoom_btn.text(), "110%")
        active.setFocus()
        QTest.keyClick(active, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
        self.assertAlmostEqual(active.zoomFactor(), 1.25)
        QTest.keyClick(active, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        self.assertAlmostEqual(active.zoomFactor(), 1.1)
        self.assertAlmostEqual(inactive.zoomFactor(), 1.0)
        self.window._switch_tab(0)
        self.assertEqual(self.window.address_bar.zoom_btn.text(), "100%")
        self.window._switch_tab(1)
        self.assertEqual(self.window.address_bar.zoom_btn.text(), "110%")
        active.setFocus()
        QTest.keyClick(active, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        self.assertAlmostEqual(active.zoomFactor(), 1.0)
        for _ in range(30):
            self.window._zoom(1)
        self.assertAlmostEqual(active.zoomFactor(), 5.0)
        for _ in range(30):
            self.window._zoom(-1)
        self.assertAlmostEqual(active.zoomFactor(), 0.25)
        self.window.address_bar.zoom_btn.click()
        self.assertAlmostEqual(active.zoomFactor(), 1.0)

    def test_history_completion_matches_title_and_enter_navigates_once(self):
        address = self.window.address_bar
        target = "https://history-fixture.example/docs"
        self.window.history.record(target, "Fixture Documentation")
        address.url_changed.disconnect(self.window._navigate_to_url)
        chosen = QSignalSpy(address.url_changed)
        self.window.show()
        self.window.activateWindow()
        address.focus_address()
        self.app.processEvents()
        QTest.keyClicks(address.address_input, "Fixture Doc")
        self.wait_for(lambda: address.completer.popup().isVisible())
        # Row 0 is the typed "Search … for" action; the history page follows.
        address.completer.popup().setCurrentIndex(
            address.completer.completionModel().index(1, 0)
        )
        QTest.keyClick(address.address_input, Qt.Key.Key_Return)
        self.assertEqual(list(chosen), [[target]])
        self.assertEqual(address.address_input.text(), target)

    def test_history_is_shared_between_windows_and_isolated_between_profiles(self):
        history = self.window.history
        target = "https://history-fixture.example/work"
        history.record(target, "Work")
        second = BrowserWindow("default")
        try:
            self.assertIs(second.history, history)
            self.assertEqual(len(second.address_bar.history_entries), 1)
            self.window._switch_to_profile("search")
            self.assertIsNone(self.window.history.path)
            self.assertEqual(len(self.window.address_bar.history_entries), 0)
            self.window.history.record("https://private-fixture.example/", "Private")
            self.assertEqual(len(second.address_bar.history_entries), 1)
            self.window._switch_to_profile("default")
            self.assertIs(self.window.history, history)
            self.assertEqual(len(self.window.address_bar.history_entries), 1)
        finally:
            second.close()

    def test_successful_http_load_adds_history_but_failed_load_does_not(self):
        self.window._new_tab(self.origin + "/history")
        tab = self.window.tabs[self.window.current_tab_index]
        self.wait_for(
            lambda: any(
                entry.url == self.origin + "/history"
                for entry in self.window.history.entries()
            )
        )
        before = self.window.history.entries()
        self.window._on_load_finished(tab, False)
        self.assertEqual(self.window.history.entries(), before)

    def test_autotype_window_title_uses_active_host_not_page_title(self):
        self.window._new_tab(self.local_page)
        active, background = self.window.tabs[1], self.window.tabs[0]
        self.window._on_tab_url_changed(active, QUrl("https://login.example.org/"))
        self.assertEqual(
            self.window.windowTitle(), "Custom Browser - default - login.example.org"
        )
        self.window._on_tab_title_changed(active, "bank.example.com")
        self.window._on_tab_url_changed(background, QUrl("https://bank.example.com/"))
        self.assertEqual(
            self.window.windowTitle(), "Custom Browser - default - login.example.org"
        )
        self.window._switch_tab(0)
        self.assertNotIn("login.example.org", self.window.windowTitle())

    def test_profile_rename_flushes_history_and_reopens_at_new_path(self):
        manager = self.window.profile_manager
        self.assertTrue(manager.create_profile("work"))
        history = manager.registry.get_history("work")
        history.record("https://history-fixture.example/work", "Work")
        old_path = history.path
        self.assertTrue(manager.rename_profile("work", "renamed"))
        self.assertFalse(old_path.parent.exists())
        history.record("https://history-fixture.example/late", "Late")
        history.flush()
        self.assertFalse(old_path.parent.exists())
        restored = manager.registry.get_history("renamed")
        self.assertEqual(restored.entries()[0].title, "Work")
        self.assertEqual(len(restored.entries()), 1)

    def test_shutdown_destroys_pages_before_profiles(self):
        pages = [self.window.tabs[0].page()]
        profiles = [self.window.profile_manager.current_profile]
        self.window._switch_to_profile("search")
        pages.append(self.window.tabs[0].page())
        profiles.append(self.window.profile_manager.current_profile)
        self.window.close()
        self.assertTrue(all(sip.isdeleted(page) for page in pages))
        self.assertTrue(all(sip.isdeleted(profile) for profile in profiles))

    def test_cookie_snapshot_enumerates_persisted_cookies(self):
        """The DevTools snapshot adapter is load-bearing for B2 and B6.

        It is a private-API path, so a Qt upgrade that breaks it must fail
        loudly here instead of silently emptying the cookie viewer.
        """
        manager = self.window.cookie_manager
        self.assertIsNotNone(manager.snapshot, "viewer must use the snapshot path")
        store = manager.cookie_store
        persisted = QNetworkCookie(b"snapshot_probe", b"stored")
        persisted.setDomain(".snapshot-fixture.example")
        persisted.setPath("/")
        store.setCookie(persisted, QUrl("http://snapshot-fixture.example"))
        self.wait_for(lambda: any(c.name == "snapshot_probe" for c in manager.cookies))
        reported = next(c for c in manager.cookies if c.name == "snapshot_probe")
        self.assertEqual(reported.domain, ".snapshot-fixture.example")
        self.assertEqual(reported.value, "stored")
        self.assertEqual(reported.path, "/")

    def test_idle_cookie_snapshot_releases_renderers_and_wakes_on_writes(self):
        from PyQt6.QtWebEngineCore import QWebEnginePage

        registry = self.window.profile_manager.registry
        name = self.window.profile_manager.current_profile_name
        snapshot = registry.snapshots[name]
        manager = self.window.cookie_manager
        self.wait_for(lambda: snapshot.ready)
        snapshot.idle.setInterval(100)
        snapshot.refresh()
        self.wait_for(lambda: snapshot.sleeping)
        self.assertIsNone(snapshot.frontend)
        self.assertIsNone(snapshot.inspected.devToolsPage())
        self.assertEqual(
            snapshot.inspected.lifecycleState(),
            QWebEnginePage.LifecycleState.Discarded,
        )
        snapshot.idle.setInterval(60000)
        cookie = QNetworkCookie(b"wake_probe", b"after_sleep")
        cookie.setDomain(".wake-fixture.example")
        cookie.setPath("/")
        manager.cookie_store.setCookie(cookie, QUrl("http://wake-fixture.example"))
        self.wait_for(lambda: not snapshot.sleeping and snapshot.ready)
        self.assertEqual(
            snapshot.inspected.lifecycleState(), QWebEnginePage.LifecycleState.Active
        )
        snapshot.refresh()
        self.wait_for(lambda: any(c.name == "wake_probe" for c in manager.cookies))
        # Releasing a profile whose snapshot sleeps must not touch a missing page.
        snapshot.idle.setInterval(100)
        snapshot.refresh()
        self.wait_for(lambda: snapshot.sleeping)
        snapshot.dispose()
        self.assertIsNone(snapshot.frontend)

    def test_cookie_viewer_opens_with_stored_cookies(self):
        """Opening the viewer must list existing cookies, not only live ones."""
        store = self.window.cookie_manager.cookie_store
        stored = QNetworkCookie(b"viewer_probe", b"persisted")
        stored.setDomain(".viewer-fixture.example")
        stored.setPath("/")
        store.setCookie(stored, QUrl("http://viewer-fixture.example"))
        manager = self.window.cookie_manager
        self.wait_for(lambda: any(c.name == "viewer_probe" for c in manager.cookies))
        dialog = CookieDialog(manager, self.window)
        self.wait_for(lambda: dialog.table.rowCount() > 0)
        names = {
            dialog.table.item(row, 1).text() for row in range(dialog.table.rowCount())
        }
        self.assertIn("viewer_probe", names)
        dialog.accept()

    def test_closing_twice_preserves_saved_session(self):
        tabs_file = self.window.tab_manager.tabs_file
        self.window.close()
        saved = load_json(tabs_file)
        self.window.close()
        self.assertEqual(load_json(tabs_file), saved)
        self.assertEqual(len(saved["windows"]), 1)
        self.assertEqual(len(saved["windows"][0]), 1)


if __name__ == "__main__":
    unittest.main()
