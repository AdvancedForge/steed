"""Real engine checks for collaborative access to synthetic signed-in profiles."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_task_services as fixtures
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWebEngineCore import (
    QWebEngineHttpRequest,
    QWebEnginePage,
    QWebEngineSettings,
)
from qt_test_support import keep_directory

from browser_window import BrowserWindow
from config import Config
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
        type(self).page_fixtures = {}
        type(self).post_responses = {}
        type(self).posted = []

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

    def test_remembered_permission_identity_survives_rename_not_recreation(self):
        key = self.registry.approval_profile_key("existing:work", create=True)
        identity = self.config.profiles_dir / "work" / ".steed_permission_id"
        self.assertEqual(identity.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.manager.rename_profile("work", "renamed"))
        self.assertEqual(self.registry.approval_profile_key("existing:renamed"), key)
        self.assertTrue(self.manager.delete_profile("renamed"))
        self.assertTrue(self.manager.create_profile("renamed"))
        self.assertEqual(self.registry.approval_profile_key("existing:renamed"), "")
        self.assertNotEqual(
            self.registry.approval_profile_key("existing:renamed", create=True), key
        )

    def test_permission_identity_rejects_symlinks_and_invalid_contents(self):
        identity = self.config.profiles_dir / "work" / ".steed_permission_id"
        target = self.config.config_dir / "identity-fixture"
        target.write_text("a" * 32)
        identity.symlink_to(target)
        with self.assertRaises(ValueError):
            self.registry.approval_profile_key("existing:work", create=True)
        identity.unlink()
        identity.write_text("invalid")
        with self.assertRaises(ValueError):
            self.registry.approval_profile_key("existing:work")

    def test_closing_watch_parent_returns_control_without_closing_shared_task_profile(
        self,
    ):
        first, second = BrowserWindow(), BrowserWindow()
        self.windows.extend((first, second))
        first._switch_to_profile("work")
        task = self.command(
            "task.create", objective="Collaborate on a shared-profile page"
        )
        grant = self.request(
            task.task_id,
            profile="existing:work",
            actions=["navigate", "inspect", "read", "attach", "edit"],
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command(
            "session.open", task_id=task.task_id, profile="existing:work"
        )
        self.navigate(task.task_id, opened.session_id, "/fixture")
        with patch("profile_manager.QApplication.activeWindow", return_value=first):
            self.command(
                "session.watch", task_id=task.task_id, session_id=opened.session_id
            )
        watch = self.service._watchers[opened.session_id]
        self.assertIs(watch.parent(), first)
        page = watch.view.page()
        self.assertIs(page.profile(), self.profile)
        self.service.take_control(task.task_id, opened.session_id, watch.control_key)
        first.close()
        self.assertFalse(self.service._controls)
        self.assertNotIn(opened.session_id, self.service._watchers)
        self.assertFalse(sip.isdeleted(page))
        self.assertFalse(sip.isdeleted(self.profile))
        self.assertEqual(
            self.command(
                "session.inspect", task_id=task.task_id, session_id=opened.session_id
            ).control_owner,
            "agent",
        )

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

    def test_shared_profile_control_edits_preserve_user_controls_and_scripts(self):
        type(self).page_fixtures["/edit"] = (
            b'<input id="field" aria-label="Field" value="INITIAL_SECRET">'
        )
        self.manager.switch_to_profile("work")
        user = self.page()
        self.load(user, "/edit")
        changed = []
        user.runJavaScript(
            "document.getElementById('field').value='USER_SECRET';true", changed.append
        )
        self.wait_for(lambda: bool(changed))
        task = self.command("task.create", objective="Edit a synthetic control")
        grant = self.request(
            task.task_id, profile="existing:work", actions=["navigate", "read", "edit"]
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command(
            "session.open", task_id=task.task_id, profile="existing:work"
        )
        self.navigate(task.task_id, opened.session_id, "/edit")
        snapshot = self.command(
            "page.inspect", task_id=task.task_id, session_id=opened.session_id
        )
        client = self.service.client(task.task_id)
        future = client.submit(
            "page.act",
            session_id=opened.session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=snapshot.frames[0].targets[0].target_id,
            operation="enter",
            text="TASK_SECRET",
        )
        self.wait_for(future.done)
        self.assertTrue(future.result().text_matches)
        values = []
        user.runJavaScript("document.getElementById('field').value", values.append)
        self.wait_for(lambda: bool(values))
        self.assertEqual(values, ["USER_SECRET"])
        self.assertNotIn("SECRET", repr(self.service.store.events(task.task_id)))
        self.service.revoke_grant(task.task_id, grant.grant_id)
        self.assertFalse(sip.isdeleted(user))
        self.assertTrue(
            user.settings().testAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled
            )
        )

    def test_shared_profile_native_keys_preserve_user_controls_and_focus_state(self):
        self.manager.switch_to_profile("work")
        type(self).page_fixtures["/native"] = (
            b'<input id="field" value="USER_SECRET"><input type="checkbox" aria-label="Check">'
        )
        user = self.page()
        self.load(user, "/native")
        original_focus = []
        user.runJavaScript(
            "document.getElementById('field').focus();document.hasFocus()",
            original_focus.append,
        )
        self.wait_for(lambda: bool(original_focus))
        task = self.command("task.create", objective="Change a synthetic checkbox")
        grant = self.request(
            task.task_id, profile="existing:work", actions=["navigate", "read", "edit"]
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command(
            "session.open", task_id=task.task_id, profile="existing:work"
        )
        self.navigate(task.task_id, opened.session_id, "/native")
        snapshot = self.command(
            "page.inspect", task_id=task.task_id, session_id=opened.session_id
        )
        outcome = self.command(
            "page.act",
            task_id=task.task_id,
            session_id=opened.session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=snapshot.frames[0].targets[-1].target_id,
            operation="activate",
        )
        self.assertTrue(outcome.checked)
        self.assertEqual(outcome.input_semantics, "chromium_keyboard")
        values = []
        user.runJavaScript(
            "({value:document.getElementById('field').value,checked:document.querySelector('[type=checkbox]').checked,focused:document.hasFocus()})",
            values.append,
        )
        self.wait_for(lambda: bool(values))
        self.assertEqual(
            values[0],
            {"value": "USER_SECRET", "checked": False, "focused": original_focus[0]},
        )
        session = self.service.backend._sessions[opened.session_id]
        self.assertTrue(session.network.frontend_profile.isOffTheRecord())
        self.assertIsNot(session.network.frontend_profile, self.profile)
        frontend, frontend_profile = (
            session.network.frontend,
            session.network.frontend_profile,
        )
        self.service.revoke_grant(task.task_id, grant.grant_id)
        self.assertTrue(sip.isdeleted(frontend))
        self.assertTrue(sip.isdeleted(frontend_profile))
        self.assertFalse(sip.isdeleted(user))
        self.load(user, "/after-native")

    def test_shared_profile_submission_preserves_user_post_and_signed_in_cookie(self):
        self.manager.switch_to_profile("work")
        user = self.page()
        self.load(user, "/cookie")
        type(self).page_fixtures["/form"] = (
            b'<form method="post" action="/task-form">'
            b'<input name="message" value="TASK_SECRET"><button>Send</button></form>'
        )
        type(self).post_responses = {
            "/task-form": (200, None, b"<p>Task result</p>"),
            "/user-form": (200, None, b"<p>User result</p>"),
        }
        task = self.command("task.create", objective="Submit a synthetic form")
        grant = self.request(
            task.task_id,
            profile="existing:work",
            actions=["navigate", "read", "submit"],
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command(
            "session.open", task_id=task.task_id, profile="existing:work"
        )
        self.navigate(task.task_id, opened.session_id, "/form")
        snapshot = self.command(
            "page.inspect", task_id=task.task_id, session_id=opened.session_id
        )
        future = self.service.client(task.task_id).submit(
            "page.submit",
            session_id=opened.session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=snapshot.frames[0].targets[-1].target_id,
        )
        self.wait_for(future.done)
        self.assertEqual(future.result().status, "loaded")
        self.assertIn(
            ("/task-form", b"message=TASK_SECRET", "task_cookie=fixture"), self.posted
        )
        self.assertNotIn("TASK_SECRET", repr(self.service.store.events(task.task_id)))
        request = QWebEngineHttpRequest(QUrl(self.origin + "/user-form"))
        request.setMethod(QWebEngineHttpRequest.Method.Post)
        request.setPostData(b"USER_SECRET")
        finished = []
        user.loadFinished.connect(finished.append)
        user.load(request)
        self.wait_for(lambda: bool(finished))
        self.assertTrue(finished[-1])
        self.assertIn(
            ("/user-form", b"USER_SECRET", "task_cookie=fixture"), self.posted
        )
        self.service.revoke_grant(task.task_id, grant.grant_id)
        self.assertFalse(sip.isdeleted(user))
        self.load(user, "/after-revoke")

    def test_user_and_multiple_tasks_share_a_busy_profile_with_independent_grants(self):
        self.manager.switch_to_profile("work")
        user = self.page()
        self.load(user, "/cookie")
        with patch.object(self.registry.passwords, "busy", return_value=True):
            first_id, first_grant, first_session = self.named_session()
            second_id, _, second_session = self.named_session()
        self.assertEqual(self.registry.task_profiles, {"work": {first_id, second_id}})
        self.assertFalse(self.manager.rename_profile("work", "other"))
        self.assertFalse(self.manager.delete_profile("work"))
        self.navigate(first_id, first_session, "/first-task")
        self.navigate(second_id, second_session, "/second-task")
        self.assertIn(("/first-task", "task_cookie=fixture"), self.requests)
        self.assertIn(("/second-task", "task_cookie=fixture"), self.requests)
        # Ordinary browsing may use destinations and methods outside task scope.
        user.setUrl(QUrl(f"http://localhost:{self.server.server_port}/human-outside"))
        self.wait_for(lambda: user.url().host() == "localhost" and not user.isLoading())
        self.assertTrue(any(path == "/human-outside" for path, _ in self.requests))
        request = QWebEngineHttpRequest(QUrl(self.origin + "/human-post"))
        request.setMethod(QWebEngineHttpRequest.Method.Post)
        user.load(request)
        self.wait_for(
            lambda: any(path == "POST /human-post" for path, _ in self.requests)
        )
        self.service.revoke_grant(first_id, first_grant)
        self.assertEqual(self.registry.task_profiles, {"work": {second_id}})
        self.navigate(second_id, second_session, "/second-still-active")
        self.load(user, "/human-still-active")
        other = ProfileManager(self.config, "work")
        other.close()
        sip.delete(other)

    def test_browser_switch_and_popout_can_join_a_task_profile(self):
        window = BrowserWindow()
        self.windows.append(window)
        task_id, _, _ = self.named_session()
        window._switch_to_profile("work")
        window._pop_out_profile_named("work")
        self.assertEqual(window.profile_manager.current_profile_name, "work")
        self.assertEqual(len(window._child_windows), 1)
        self.assertIs(
            window._child_windows[0].profile_manager.current_profile, self.profile
        )
        self.assertEqual(self.registry.task_profiles, {"work": {task_id}})

    def test_closed_popout_window_is_freed_before_its_opener_closes(self):
        window = BrowserWindow()
        self.windows.append(window)
        window._pop_out_profile_named("work")
        child = window._child_windows[0]
        child.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(child))
        window._pop_out_profile_named("work")
        self.assertEqual(len(window._child_windows), 1)
        window.close()
        self.assertTrue(window._child_windows[0].isHidden())

    def test_unknown_profiles_paths_and_other_profile_sessions_are_denied(self):
        task = self.command("task.create", objective="Invalid profile")
        for selector in ("existing:missing", "existing:search", "existing:../work"):
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

    def test_task_bypasses_existing_workers_without_disabling_user_workers(self):
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
        self.assertEqual(self.service.backend.inspect(session_id).title, "Task fixture")
        self.assertFalse(any(path == "/forbidden-worker" for path, _ in self.requests))
        self.assertTrue(any(path == "/worker-controlled" for path, _ in self.requests))
        self.load(self.page(), "/worker-controlled")
        self.assertEqual(self.pages[-1].title(), "Worker guarded")
        self.assertTrue(any(path == "/forbidden-worker" for path, _ in self.requests))
        self.requests.clear()
        self.navigate(task_id, session_id, "/worker-controlled")
        self.assertFalse(any(path == "/forbidden-worker" for path, _ in self.requests))

    def test_revocation_closes_only_task_pages_and_preserves_user_profile_services(
        self,
    ):
        self.manager.switch_to_profile("work")
        user = self.page()
        snapshot = self.registry.snapshots["work"]
        task_id, grant_id, session_id = self.named_session()
        page = self.service.backend._sessions[session_id].page
        guard = page.task_interceptor
        self.service.revoke_grant(task_id, grant_id)
        self.assertTrue(sip.isdeleted(page))
        self.assertEqual(self.registry.task_profiles, {})
        self.assertEqual(guard.grant.reason(), "revoked")
        self.assertIs(self.registry.snapshots["work"], snapshot)
        self.config.set("global_cookie_sharing_mode", "all_profiles")
        self.registry.refresh_settings()
        self.assertIn("work", self.registry.sharing.enabled)
        self.assertNotIn("work", self.registry.sharing.suspended)
        self.load(user, "/after-revoke-allowed")
        with patch.object(
            self.registry.downloads, "destination_picker", return_value=""
        ) as picker:
            user.setUrl(QUrl(self.origin + "/download"))
            self.wait_for(lambda: picker.called)

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
        self.assertFalse(getattr(self.profile, "task_restricted", False))
        with self.assertRaises(TaskPermissionError):
            self.command("session.open", task_id=task_id, profile="existing:work")

    def test_network_setup_failure_denies_task_navigation_without_affecting_user(self):
        task_id, _, session_id = self.named_session()
        session = self.service.backend._sessions[session_id]
        session.network._fail()
        with self.assertRaisesRegex(RuntimeError, "protection unavailable"):
            self.command(
                "session.navigate",
                task_id=task_id,
                session_id=session_id,
                url=self.origin + "/failed-setup-denied",
            )
        self.assertFalse(
            any(path == "/failed-setup-denied" for path, _ in self.requests)
        )
        self.load(self.page(), "/user-unaffected")

    def test_lost_worker_protection_denies_later_navigation(self):
        task_id, _, session_id = self.named_session()
        self.navigate(task_id, session_id, "/before-loss")
        session = self.service.backend._sessions[session_id]
        session.network._loaded(False)
        with self.assertRaisesRegex(RuntimeError, "protection unavailable"):
            self.command(
                "session.navigate",
                task_id=task_id,
                session_id=session_id,
                url=self.origin + "/after-loss-denied",
            )
        self.assertFalse(any(path == "/after-loss-denied" for path, _ in self.requests))
        self.load(self.page(), "/human-after-loss")

    def test_revocation_journal_failure_freezes_and_releases_the_named_profile(self):
        task_id, grant_id, session_id = self.named_session()
        page = self.service.backend._sessions[session_id].page
        guard = page.task_interceptor
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
        self.assertEqual(guard.grant.reason(), "revoked")
        self.load(self.page(), "/journal-failure-allowed")
        self.assertTrue(
            any(path == "/journal-failure-allowed" for path, _ in self.requests)
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
        self.assertEqual(self.registry.task_profiles, {"work": {task_id}})
        order = []
        page.destroyed.connect(lambda: order.append("page"))
        self.profile.destroyed.connect(lambda: order.append("profile"))
        other.close()
        sip.delete(other)
        self.assertEqual(order, ["page", "profile"])

    def test_approval_discloses_shared_storage_and_concurrent_access(self):
        self.named_request()
        dialog = TaskDialog(self.service)
        try:
            details = dialog.details.toPlainText()
            self.assertIn("existing:work", details)
            self.assertIn("Site changes persist", details)
            self.assertIn("task pages only", details)
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
