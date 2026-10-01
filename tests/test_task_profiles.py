"""Real engine checks for exclusive access to synthetic signed-in profiles."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_task_services as fixtures
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWidgets import QMessageBox
from qt_test_support import keep_directory

from browser_window import BrowserWindow
from config import Config
from download_manager import DownloadRecord
from profile_manager import ProfileManager
from services.task_policy import TaskPermissionError
from ui.task_dialog import TaskDialog


class TaskProfileTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    wait_for = fixtures.TaskServiceTests.wait_for
    command = fixtures.TaskServiceTests.command
    request = fixtures.TaskServiceTests.request

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch.object(Path, "home", return_value=Path(self.directory.name))
        self.home.start()
        self.config = Config()
        self.config.update(
            {"home_page": "about:blank", "restore_tabs_on_startup": False}
        )
        self.manager = ProfileManager(self.config)
        self.manager.create_profile("work")
        self.registry = self.manager.registry
        self.profile = self.registry.get("work")
        self.service = self.registry.tasks
        self.pages = []
        self.windows = []
        self.requests.clear()

    def tearDown(self):
        for page in self.pages:
            if not sip.isdeleted(page):
                sip.delete(page)
        for window in self.windows:
            window.close()
            sip.delete(window)
        self.manager.close()
        sip.delete(self.manager)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def named_request(self):
        task = self.command("task.create", objective="Read synthetic signed-in content")
        grant = self.request(task.task_id, profile="existing:work")
        return task.task_id, grant.grant_id

    def named_session(self):
        task_id, grant_id = self.named_request()
        self.service.approve_grant(task_id, grant_id)
        snapshot = self.command(
            "session.open", task_id=task_id, profile="existing:work"
        )
        return task_id, grant_id, snapshot.session_id

    def page(self):
        page = QWebEnginePage(self.profile)
        self.pages.append(page)
        return page

    def load(self, page, path, success=True):
        finished = []
        page.loadFinished.connect(finished.append)
        try:
            page.setUrl(QUrl(self.origin + path))
            self.wait_for(lambda: bool(finished))
            self.assertEqual(finished[-1], success)
        finally:
            page.loadFinished.disconnect(finished.append)

    def navigate(self, task_id, session_id, path, success=True):
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + path,
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is success
        )

    def test_signed_in_cookies_and_storage_use_the_same_profile_and_survive_finish(
        self,
    ):
        seed = self.page()
        self.load(seed, "/cookie")
        stored = []
        seed.runJavaScript(
            "localStorage.setItem('fixture', 'signed-in'); true", stored.append
        )
        self.wait_for(lambda: bool(stored))
        sip.delete(seed)
        task_id, _, session_id = self.named_session()
        session = self.service.backend._sessions[session_id]
        self.assertIs(session.profile, self.profile)
        self.assertFalse(session.profile.isOffTheRecord())
        self.assertFalse(session.owns_profile)
        self.navigate(task_id, session_id, "/signed-in-check")
        self.assertIn(("/signed-in-check", "task_cookie=fixture"), self.requests)
        self.assertFalse(
            session.page.settings().testAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled
            )
        )
        self.assertTrue(
            self.profile.settings().testAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled
            )
        )
        self.command("task.finish", task_id=task_id)
        self.assertTrue(sip.isdeleted(session.page))
        self.assertFalse(sip.isdeleted(self.profile))
        self.manager.switch_to_profile("work")
        check = self.page()
        self.load(check, "/after-finish")
        values = []
        check.runJavaScript("localStorage.getItem('fixture')", values.append)
        self.wait_for(lambda: bool(values))
        self.assertEqual(values, ["signed-in"])
        self.assertIn(("/after-finish", "task_cookie=fixture"), self.requests)

    def test_busy_profile_approval_and_concurrent_task_access_are_rejected(self):
        task_id, grant_id = self.named_request()
        self.manager.switch_to_profile("work")
        with self.assertRaisesRegex(ValueError, "Switch away"):
            self.service.approve_grant(task_id, grant_id)
        self.assertEqual(self.registry.task_profiles, {})
        self.manager.switch_to_profile("default")
        with patch.object(self.registry.passwords, "busy", return_value=True):
            with self.assertRaises(ValueError):
                self.service.approve_grant(task_id, grant_id)
        self.registry.downloads.records["busy"] = DownloadRecord(
            "busy", "fixture", "", "work"
        )
        self.registry.downloads.requests["busy"] = Mock()
        with self.assertRaises(ValueError):
            self.service.approve_grant(task_id, grant_id)
        self.registry.downloads.records.clear()
        self.registry.downloads.requests.clear()
        self.service.approve_grant(task_id, grant_id)
        second_id, second_grant = self.named_request()
        with self.assertRaises(ValueError):
            self.service.approve_grant(second_id, second_grant)
        self.assertEqual(self.registry.task_profiles, {"work": task_id})
        with self.assertRaises(ValueError):
            self.manager.switch_to_profile("work")
        self.assertEqual(self.manager.current_profile_name, "default")
        self.assertFalse(self.manager.rename_profile("work", "other"))
        self.assertFalse(self.manager.delete_profile("work"))
        owners = set(self.registry.owners)
        with self.assertRaises(ValueError):
            ProfileManager(self.config, "work")
        self.assertEqual(self.registry.owners, owners)

    def test_browser_switch_and_popout_keep_existing_tabs_when_profile_is_reserved(
        self,
    ):
        window = BrowserWindow()
        self.windows.append(window)
        task_id, _ = self.named_request()
        grant = self.service.store.grants(task_id)[0]
        self.service.approve_grant(task_id, grant.grant_id)
        tabs = list(window.tabs)
        with patch.object(QMessageBox, "warning") as warning:
            window._switch_to_profile("work")
            window._pop_out_profile_named("work")
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(window.tabs, tabs)
        self.assertEqual(window.profile_manager.current_profile_name, "default")
        self.assertEqual(window._child_windows, [])

    def test_unknown_profiles_paths_and_other_profile_sessions_are_denied(self):
        task = self.command("task.create", objective="Invalid profile")
        for selector in ("existing:missing", "existing:global", "existing:../work"):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                self.request(task.task_id, profile=selector)
        outside = Path(self.directory.name) / "outside"
        outside.mkdir()
        (self.config.profiles_dir / "linked").symlink_to(
            outside, target_is_directory=True
        )
        with self.assertRaises(ValueError):
            self.request(task.task_id, profile="existing:linked")
        (self.config.profiles_dir / "alias").symlink_to(
            self.config.profiles_dir / "work", target_is_directory=True
        )
        with self.assertRaises(ValueError):
            self.request(task.task_id, profile="existing:alias")
        task_id, _, _ = self.named_session()
        for selector in ("private", "existing:other", None):
            with (
                self.subTest(selector=selector),
                self.assertRaises(TaskPermissionError),
            ):
                self.command("session.open", task_id=task_id, profile=selector)

    def test_shared_profile_redirects_resources_and_downloads_are_guarded(self):
        task_id, _, session_id = self.named_session()
        self.navigate(task_id, session_id, "/scope-page")
        self.assertEqual(self.service.backend.inspect(session_id).title, "Scoped page")
        self.navigate(task_id, session_id, "/redirect-out", success=False)
        self.assertFalse(any("forbidden" in path for path, _ in self.requests))
        states = []
        self.profile.downloadRequested.connect(
            lambda request: states.append(request.state().name)
        )
        with patch.object(self.registry.downloads, "destination_picker") as picker:
            self.command(
                "session.navigate",
                task_id=task_id,
                session_id=session_id,
                url=self.origin + "/download",
            )
            self.wait_for(lambda: bool(states))
        picker.assert_not_called()
        self.assertEqual(states, ["DownloadCancelled"])
        self.assertEqual(self.registry.downloads.records, {})

    def test_existing_service_worker_cannot_fetch_outside_the_profile_grant(self):
        seed = self.page()
        self.load(seed, "/worker-seed")
        seed.runJavaScript(
            "navigator.serviceWorker.addEventListener('controllerchange', "
            "() => document.title = 'Worker ready'); "
            "navigator.serviceWorker.register('/service-worker.js');"
        )
        self.wait_for(lambda: seed.title() == "Worker ready")
        sip.delete(seed)
        task_id, _, session_id = self.named_session()
        self.navigate(task_id, session_id, "/worker-controlled")
        self.assertEqual(
            self.service.backend.inspect(session_id).title, "Worker guarded"
        )
        self.assertFalse(any(path == "/forbidden-worker" for path, _ in self.requests))
        self.wait_for(
            lambda: any(
                event.payload.get("reason") == "origin_out_of_scope"
                for event in self.service.store.events(task_id)
            )
        )

    def test_revocation_blocks_residual_profile_requests_until_user_reactivation(self):
        task_id, grant_id, session_id = self.named_session()
        page = self.service.backend._sessions[session_id].page
        self.service.revoke_grant(task_id, grant_id)
        self.assertTrue(sip.isdeleted(page))
        self.assertEqual(self.registry.task_profiles, {})
        guard = self.profile.task_interceptor
        self.assertEqual(guard.grant.reason(), "revoked")
        self.load(self.page(), "/after-revoke-denied", success=False)
        self.assertFalse(
            any(path == "/after-revoke-denied" for path, _ in self.requests)
        )
        self.manager.refresh_settings()
        self.assertTrue(self.profile.task_restricted)
        self.config.set("global_cookie_sharing_mode", "all_profiles")
        self.registry.refresh_settings()
        self.assertNotIn("work", self.registry.sharing.enabled)
        self.assertIn("work", self.registry.sharing.suspended)
        self.manager.switch_to_profile("work")
        self.assertFalse(self.profile.task_restricted)
        self.assertNotIn("work", self.registry.sharing.suspended)
        self.assertIn("work", self.registry.sharing.enabled)
        self.load(self.page(), "/after-revoke-allowed")
        self.assertTrue(
            any(path == "/after-revoke-allowed" for path, _ in self.requests)
        )

    def test_persistence_failure_rolls_back_reservation_and_cannot_install_authority(
        self,
    ):
        task_id, grant_id = self.named_request()
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.service.approve_grant(task_id, grant_id)
        self.assertEqual(self.registry.task_profiles, {})
        self.assertNotIn(task_id, self.service._grants)
        self.assertFalse(getattr(self.profile, "task_restricted", False))
        self.assertEqual(
            self.service.store.grant(task_id, grant_id).status, "requested"
        )

    def test_expiry_releases_an_unused_reservation_and_blocks_late_commands(self):
        now = [100.0]
        self.service._clock = lambda: now[0]
        task_id, grant_id = self.named_request()
        self.service.approve_grant(task_id, grant_id)
        now[0] = 1000.0
        self.wait_for(lambda: not self.registry.task_profiles)
        self.assertEqual(self.service.store.grant(task_id, grant_id).status, "expired")
        self.assertTrue(self.profile.task_restricted)
        with self.assertRaises(TaskPermissionError):
            self.command("session.open", task_id=task_id, profile="existing:work")

    def test_partial_engine_setup_failure_freezes_authority_and_releases_reservation(
        self,
    ):
        task_id, grant_id = self.named_request()
        activate = self.registry.activate_task_profile

        def fail_after_activation(*arguments):
            activate(*arguments)
            raise RuntimeError("fixture setup failure")

        with patch.object(
            self.registry, "activate_task_profile", side_effect=fail_after_activation
        ):
            with self.assertRaises(RuntimeError):
                self.service.approve_grant(task_id, grant_id)
        self.assertIn("could not be initialized", self.service.failure)
        self.wait_for(lambda: not self.registry.task_profiles)
        self.assertEqual(
            self.profile.task_interceptor.grant.reason(), "approval_failed"
        )
        self.load(self.page(), "/failed-setup-denied", success=False)
        self.assertFalse(
            any(path == "/failed-setup-denied" for path, _ in self.requests)
        )

    def test_revocation_journal_failure_freezes_and_releases_the_named_profile(self):
        task_id, grant_id, session_id = self.named_session()
        page = self.service.backend._sessions[session_id].page
        with patch.object(
            self.service.store,
            "resolve_grant",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.service.revoke_grant(task_id, grant_id)
        self.assertTrue(self.service.failure)
        self.wait_for(lambda: sip.isdeleted(page))
        self.assertEqual(self.registry.task_profiles, {})
        self.assertEqual(self.profile.task_interceptor.grant.reason(), "revoked")
        self.load(self.page(), "/journal-failure-denied", success=False)
        self.assertFalse(
            any(path == "/journal-failure-denied" for path, _ in self.requests)
        )

    def test_task_guard_preserves_ordinary_ad_filtering(self):
        task_id, _, session_id = self.named_session()
        with patch.object(
            self.registry.adblock.engine, "blocked", return_value=True
        ) as filtered:
            self.navigate(task_id, session_id, "/ad-filter-denied", success=False)
        self.assertTrue(filtered.called)
        self.assertFalse(any(path == "/ad-filter-denied" for path, _ in self.requests))
        self.command("task.finish", task_id=task_id)
        self.manager.switch_to_profile("work")
        with patch.object(self.registry.adblock.engine, "blocked", return_value=True):
            self.load(self.page(), "/ordinary-filter-denied", success=False)
        self.assertFalse(
            any(path == "/ordinary-filter-denied" for path, _ in self.requests)
        )

    def test_shared_registry_closes_task_pages_before_persistent_profiles(self):
        other = ProfileManager(self.config)
        task_id, _, session_id = self.named_session()
        page = self.service.backend._sessions[session_id].page
        self.manager.close()
        self.assertFalse(sip.isdeleted(page))
        self.assertEqual(self.registry.task_profiles, {"work": task_id})
        order = []
        page.destroyed.connect(lambda: order.append("page"))
        self.profile.destroyed.connect(lambda: order.append("profile"))
        other.close()
        sip.delete(other)
        self.assertEqual(order, ["page", "profile"])

    def test_approval_discloses_shared_storage_and_exclusive_access(self):
        self.named_request()
        dialog = TaskDialog(self.service)
        try:
            details = dialog.details.toPlainText()
            self.assertIn("existing:work", details)
            self.assertIn("Site changes persist", details)
            self.assertIn("entire profile", details)
            self.assertIn("background", details)
            self.assertIn("URL/title metadata only", details)
        finally:
            dialog.reject()
            sip.delete(dialog)

    def test_signed_in_page_content_requires_a_read_grant_and_stays_out_of_the_journal(
        self,
    ):
        seed = self.page()
        self.load(seed, "/cookie")
        sip.delete(seed)
        task = self.command("task.create", objective="Read signed-in fixture")
        requested = self.request(
            task.task_id, profile="existing:work", actions=["navigate", "read"]
        )
        dialog = TaskDialog(self.service)
        try:
            self.assertIn(
                "readable page text and control labels", dialog.details.toPlainText()
            )
        finally:
            dialog.reject()
            sip.delete(dialog)
        self.service.approve_grant(task.task_id, requested.grant_id)
        opened = self.command(
            "session.open", task_id=task.task_id, profile="existing:work"
        )
        self.navigate(task.task_id, opened.session_id, "/signed-in-read")
        snapshot = self.command(
            "page.inspect", task_id=task.task_id, session_id=opened.session_id
        )
        self.assertIn("Independent page", snapshot.frames[0].text)
        self.assertIn(("/signed-in-read", "task_cookie=fixture"), self.requests)
        self.assertNotIn(
            "Independent page", repr(self.service.store.events(task.task_id))
        )
