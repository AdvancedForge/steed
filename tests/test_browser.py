"""Regression checks using temporary profiles and local pages only."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWebEngineCore import QWebEnginePermission
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from browser_window import BrowserWindow
from config import Config
from main import configure_chromium_flags
from tab_manager import TabManager, TabState
from ui.settings_dialog import SettingsDialog
from ui.tab_bar import TabBar
from utils import load_json, sanitize_profile_name, save_json


class PersistenceTests(unittest.TestCase):
    def test_failed_write_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tabs.json"
            save_json(path, [{"url": "https://example.org"}])
            previous = path.read_bytes()
            with patch("utils.json.dump", side_effect=ValueError("invalid data")):
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
                self.assertEqual(Config().default_profile, "default")

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
                "utils.json.load", side_effect=json.JSONDecodeError("bad", "", 0)
            ):
                with self.assertLogs("utils", level="WARNING"):
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


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        cls.app = QApplication.instance() or QApplication(["browser-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.directories = []

    @classmethod
    def tearDownClass(cls):
        # Chromium finishes background storage work as the application shuts
        # down. Keep temporary profile directories until that work is joined.
        sip.delete(cls.app)
        for directory in cls.directories:
            directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.directories.append(self.directory)
        self.home_patch = patch(
            "pathlib.Path.home", return_value=Path(self.directory.name)
        )
        self.home_patch.start()
        config = Config()
        self.local_page = (
            Path(__file__).resolve().parents[1] / "codec_test.html"
        ).as_uri()
        config.update({"home_page": self.local_page})
        self.window = BrowserWindow()

    def tearDown(self):
        if not self.window._closing:
            self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

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
        bar = TabBar()
        for name in ("A", "B", "C"):
            bar.add_tab(name)
        bar.set_tab_group(0, "First")
        bar.set_tab_group(2, "Last")
        bar.tab_bar.moveTab(0, 2)
        self.assertEqual(bar.groups, {0: None, 1: "Last", 2: "First"})
        bar.insert_tab(1, "Inserted")
        self.assertEqual(bar.groups, {0: None, 1: None, 2: "Last", 3: "First"})
        bar.clear_all_tabs()
        self.assertEqual(bar.groups, {})
        bar.deleteLater()

    def test_permission_response_uses_request_object(self):
        permission = Mock()
        permission.permissionType.return_value = (
            QWebEnginePermission.PermissionType.Notifications
        )
        permission.origin.return_value = QUrl("https://example.org")
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            self.window._on_permission_requested(permission)
        permission.grant.assert_called_once()
        permission.deny.assert_not_called()

    def test_media_picker_cancel_calls_request_once(self):
        request = Mock()
        request.screensModel.return_value.rowCount.return_value = 0
        request.windowsModel.return_value.rowCount.return_value = 0

        def reject_dialog(dialog):
            dialog.reject()
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", reject_dialog):
            self.window._on_desktop_media_requested(request)
        request.cancel.assert_called_once()

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
        with patch.object(self.window.config, "save_config") as save:
            dialog.apply()
        save.assert_called_once()
        dialog.deleteLater()

    def test_shutdown_destroys_pages_before_profiles(self):
        pages = [self.window.tabs[0].page()]
        profiles = [self.window.profile_manager.current_profile]
        self.window._switch_to_profile("search")
        pages.append(self.window.tabs[0].page())
        profiles.append(self.window.profile_manager.current_profile)
        self.window.close()
        self.assertTrue(all(sip.isdeleted(page) for page in pages))
        self.assertTrue(all(sip.isdeleted(profile) for profile in profiles))

    def test_closing_twice_preserves_saved_session(self):
        tabs_file = self.window.tab_manager.tabs_file
        self.window.close()
        saved = load_json(tabs_file)
        self.window.close()
        self.assertEqual(load_json(tabs_file), saved)
        self.assertEqual(len(saved), 1)


if __name__ == "__main__":
    unittest.main()
