"""A deterministic client drives real Qt task pages without AI or accounts."""

import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QThread, QUrl
from PyQt6.QtWebEngineCore import QWebEngineHttpRequest
from PyQt6.QtWidgets import QMessageBox
from qt_test_support import application, keep_directory

from browser_window import BrowserWindow
from config import Config
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.task_dialog import TaskDialog


class TaskServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append((self.path, self.headers.get("Cookie", "")))
                payload = b"<html><title>Task fixture</title><body>Independent page</body></html>"
                payload = getattr(cls, "page_fixtures", {}).get(self.path, payload)
                denied = f"http://localhost:{cls.server.server_port}"
                if self.path in ("/redirect-out", "/redirect-resource"):
                    self.send_response(302)
                    self.send_header("Location", denied + "/forbidden-redirect")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/scope-page":
                    payload = (
                        f'<html><title>Scoped page</title><img src="{denied}/forbidden-image">'
                        f'<iframe src="{denied}/forbidden-frame"></iframe>'
                        '<img src="/redirect-resource">'
                        f'<script>document.title="Script ran"; fetch("{denied}/forbidden-script")</script>'
                        "</html>"
                    ).encode()
                if self.path == "/service-worker.js":
                    payload = (
                        'self.addEventListener("install", e => e.waitUntil(self.skipWaiting()));'
                        'self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));'
                        'self.addEventListener("fetch", e => {'
                        'if (new URL(e.request.url).pathname !== "/worker-controlled") return;'
                        "e.respondWith((async () => {"
                        f'try {{ await fetch("{denied}/forbidden-worker", {{mode:"no-cors"}}); }} catch (_) {{}}'
                        'return new Response("<html><title>Worker guarded</title></html>",'
                        '{headers: {"Content-Type":"text/html"}});})());});'
                    ).encode()
                if self.path == "/slow":
                    time.sleep(0.3)
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/javascript"
                    if self.path == "/service-worker.js"
                    else "text/html",
                )
                self.send_header("Content-Length", str(len(payload)))
                if self.path == "/cookie":
                    self.send_header("Set-Cookie", "task_cookie=fixture; Path=/")
                if self.path == "/download":
                    self.send_header(
                        "Content-Disposition", 'attachment; filename="task-fixture.bin"'
                    )
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):
                cls.requests.append(("POST " + self.path, ""))
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if hasattr(cls, "posted"):
                    cls.posted.append((self.path, body, self.headers.get("Cookie", "")))
                if self.path == "/submitted-slow":
                    time.sleep(0.3)
                status, location, payload = getattr(cls, "post_responses", {}).get(
                    self.path, (204, None, b"")
                )
                self.send_response(status)
                if location is not None:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

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
        keep_directory(self.directory)
        self.service = TaskService(Path(self.directory.name) / "tasks.sqlite")

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for task fixture")

    def command(self, name, **arguments):
        future = self.service.broker.submit(name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def task_session(self):
        task = self.command("task.create", objective="Read the local fixture")
        requested = self.command(
            "grant.request",
            task_id=task.task_id,
            scope={"origins": [self.origin], "allow_private_network": True},
        )
        self.service.approve_grant(task.task_id, requested.grant_id)
        snapshot = self.command("session.open", task_id=task.task_id)
        return task.task_id, snapshot.session_id

    def request(self, task_id, **changes):
        scope = {"origins": [self.origin], "allow_private_network": True}
        scope.update(changes)
        return self.command("grant.request", task_id=task_id, scope=scope)

    def test_client_cannot_execute_without_approval_or_approve_its_own_request(self):
        task = self.command("task.create", objective="Pending approval")
        requested = self.request(task.task_id)
        with patch.object(self.service.backend, "open_session") as opened:
            with self.assertRaises(TaskPermissionError) as error:
                self.command("session.open", task_id=task.task_id)
            self.assertEqual(error.exception.code, "approval_required")
            opened.assert_not_called()
        with self.assertRaises(ValueError):
            self.command(
                "grant.approve", task_id=task.task_id, grant_id=requested.grant_id
            )
        self.assertEqual(
            self.service.store.grant(task.task_id, requested.grant_id).status,
            "requested",
        )
        self.service.deny_grant(task.task_id, requested.grant_id)
        with self.assertRaises(TaskPermissionError):
            self.command("session.open", task_id=task.task_id)

    def test_scope_violations_are_journaled_before_backend_dispatch(self):
        task_id, session_id = self.task_session()
        with patch.object(self.service.backend, "navigate") as navigate:
            with self.assertRaises(TaskPermissionError) as error:
                self.command(
                    "session.navigate",
                    task_id=task_id,
                    session_id=session_id,
                    url=f"http://localhost:{self.server.server_port}/forbidden?secret=fixture",
                )
            self.assertEqual(error.exception.code, "origin_out_of_scope")
            navigate.assert_not_called()
        with patch.object(self.service.backend, "open_session") as opened:
            with self.assertRaises(TaskPermissionError):
                self.command("session.open", task_id=task_id, profile="global")
            opened.assert_not_called()
        with self.assertRaises(ValueError):
            self.request(
                self.command("task.create", objective="File attempt").task_id,
                file_destinations=[str(Path(self.directory.name) / "forbidden")],
            )
        events = self.service.store.events(task_id)
        denied = [event for event in events if event.kind == "permission.denied"]
        self.assertEqual(
            [e.payload["reason"] for e in denied],
            ["origin_out_of_scope", "profile_out_of_scope"],
        )
        self.assertNotIn("secret", repr(events))
        self.assertFalse((Path(self.directory.name) / "forbidden").exists())

    def test_engine_blocks_redirects_frames_resources_and_post_from_attached_view(self):
        task_id, session_id = self.task_session()
        before = len(self.requests)
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/scope-page",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is True
        )
        self.wait_for(
            lambda: any(
                event.kind == "permission.denied"
                for event in self.service.store.events(task_id)
            )
        )
        self.assertEqual(self.service.backend.inspect(session_id).title, "Scoped page")
        self.assertTrue(
            any(path == "/redirect-resource" for path, _ in self.requests[before:])
        )
        self.assertFalse(any("forbidden" in path for path, _ in self.requests[before:]))
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/redirect-out",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is False
        )
        view = self.command("session.attach", task_id=task_id, session_id=session_id)
        request = QWebEngineHttpRequest(QUrl(self.origin + "/forbidden-post"))
        request.setMethod(QWebEngineHttpRequest.Method.Post)
        request.setPostData(b"fixture=value")
        view.page().load(request)
        self.wait_for(
            lambda: any(
                event.payload.get("reason") == "method_out_of_scope"
                for event in self.service.store.events(task_id)
            )
        )
        self.assertFalse(any("forbidden" in path for path, _ in self.requests[before:]))

    def test_ui_approves_exact_scope_and_revokes_before_queued_navigation(self):
        task = self.command("task.create", objective="UI approval")
        requested = self.request(task.task_id)
        dialog = TaskDialog(self.service)
        try:
            self.assertTrue(dialog.approve_button.isEnabled())
            self.assertIn(self.origin, dialog.details.toPlainText())
            self.assertIn("DNS routes are not pinned", dialog.details.toPlainText())
            dialog.approve_button.click()
            self.assertEqual(
                self.service.store.grant(task.task_id, requested.grant_id).status,
                "approved",
            )
            opened = self.command("session.open", task_id=task.task_id)
            page = self.service.backend._sessions[opened.session_id].page
            queued = self.service.broker.submit(
                "session.navigate",
                task_id=task.task_id,
                session_id=opened.session_id,
                url=self.origin,
            )
            dialog.revoke_button.click()
            self.wait_for(queued.done)
            with self.assertRaises(TaskPermissionError):
                queued.result()
            self.assertTrue(sip.isdeleted(page))
            self.assertEqual(self.service.store.get(task.task_id).status, "cancelled")
            self.assertEqual(
                self.service.store.grant(task.task_id, requested.grant_id).status,
                "revoked",
            )
        finally:
            dialog.reject()
            sip.delete(dialog)

    def test_expiry_cancels_task_and_closes_page_without_another_command(self):
        now = [100.0]
        self.service._clock = lambda: now[0]
        task = self.command("task.create", objective="Expires")
        requested = self.request(task.task_id, duration_seconds=1)
        self.service.approve_grant(task.task_id, requested.grant_id)
        opened = self.command("session.open", task_id=task.task_id)
        page = self.service.backend._sessions[opened.session_id].page
        now[0] = 101.0
        self.wait_for(lambda: sip.isdeleted(page))
        self.assertEqual(self.service.store.get(task.task_id).status, "cancelled")
        self.assertEqual(
            self.service.store.grant(task.task_id, requested.grant_id).status, "expired"
        )

    def test_operation_and_session_budgets_deny_before_execution(self):
        task = self.command("task.create", objective="Bounded task")
        requested = self.request(task.task_id, max_operations=2)
        self.service.approve_grant(task.task_id, requested.grant_id)
        opened = self.command("session.open", task_id=task.task_id)
        with patch.object(self.service.backend, "open_session") as create:
            with self.assertRaises(TaskPermissionError):
                self.command("session.open", task_id=task.task_id)
            create.assert_not_called()
        with patch.object(self.service.backend, "navigate") as navigate:
            with self.assertRaises(TaskPermissionError):
                self.command(
                    "session.navigate",
                    task_id=task.task_id,
                    session_id=opened.session_id,
                    url=self.origin,
                )
            navigate.assert_not_called()
        self.assertEqual(self.service.store.get(task.task_id).status, "cancelled")
        self.assertEqual(
            self.service.store.grant(task.task_id, requested.grant_id).status,
            "exhausted",
        )

    def test_grant_failure_cannot_install_live_authority_and_revocation_failure_stops_engine(
        self,
    ):
        task = self.command("task.create", objective="Approval write failure")
        requested = self.request(task.task_id)
        with patch.object(
            self.service.store, "_append", side_effect=OSError("fixture disk")
        ):
            with self.assertRaises(OSError):
                self.service.approve_grant(task.task_id, requested.grant_id)
        self.assertNotIn(task.task_id, self.service._grants)
        self.assertEqual(
            self.service.store.grant(task.task_id, requested.grant_id).status,
            "requested",
        )
        self.service.approve_grant(task.task_id, requested.grant_id)
        opened = self.command("session.open", task_id=task.task_id)
        page = self.service.backend._sessions[opened.session_id].page
        with patch.object(
            self.service.store, "resolve_grant", side_effect=OSError("fixture disk")
        ):
            with self.assertRaises(OSError):
                self.service.revoke_grant(task.task_id, requested.grant_id)
        self.assertTrue(sip.isdeleted(page))
        self.assertTrue(self.service.failure)

    def test_task_cancel_rejects_queued_work_and_keeps_other_task_alive(self):
        first, session_id = self.task_session()
        second, other_id = self.task_session()
        first_page = self.service.backend._sessions[session_id].page
        second_page = self.service.backend._sessions[other_id].page
        queued = self.service.broker.submit(
            "session.navigate", task_id=first, session_id=session_id, url=self.origin
        )
        self.service.cancel_task(first)
        self.wait_for(queued.done)
        with self.assertRaises(TaskPermissionError):
            queued.result()
        self.assertTrue(sip.isdeleted(first_page))
        self.assertFalse(sip.isdeleted(second_page))
        self.command(
            "session.navigate", task_id=second, session_id=other_id, url=self.origin
        )
        self.wait_for(
            lambda: self.service.backend.inspect(other_id).load_success is True
        )

    def test_bound_client_cannot_impersonate_or_enumerate_other_tasks(self):
        first, session_id = self.task_session()
        second, other_id = self.task_session()
        client = self.service.client(first)
        requests = [
            client.submit("session.inspect", task_id=second, session_id=other_id),
            client.submit("session.inspect", session_id=other_id),
            client.submit("task.list"),
            client.submit("grant.approve", grant_id="unused"),
        ]
        for future in requests:
            self.wait_for(future.done)
            with self.assertRaises(TaskPermissionError):
                future.result()
        status = client.submit("task.status")
        self.wait_for(status.done)
        self.assertEqual(status.result().task_id, first)
        inspected = client.submit("session.inspect", session_id=session_id)
        self.wait_for(inspected.done)
        self.assertEqual(inspected.result().session_id, session_id)

    def test_network_request_budget_closes_page_and_cancels_task(self):
        task = self.command("task.create", objective="Network budget")
        requested = self.request(task.task_id, max_requests=1)
        self.service.approve_grant(task.task_id, requested.grant_id)
        opened = self.command("session.open", task_id=task.task_id)
        page = self.service.backend._sessions[opened.session_id].page
        self.command(
            "session.navigate",
            task_id=task.task_id,
            session_id=opened.session_id,
            url=self.origin + "/scope-page",
        )
        self.wait_for(lambda: sip.isdeleted(page))
        self.assertEqual(self.service.store.get(task.task_id).status, "cancelled")
        self.assertEqual(
            self.service.store.grant(task.task_id, requested.grant_id).status,
            "exhausted",
        )
        events = self.service.store.events(task.task_id)
        self.assertTrue(
            any(e.payload.get("reason") == "request_budget_exhausted" for e in events)
        )

    def test_ui_handles_sqlite_approval_failure_without_granting_authority(self):
        task = self.command("task.create", objective="UI disk failure")
        requested = self.request(task.task_id)
        dialog = TaskDialog(self.service)
        try:
            with (
                patch.object(
                    self.service.store,
                    "_append",
                    side_effect=sqlite3.OperationalError("fixture disk"),
                ),
                patch.object(QMessageBox, "warning") as warning,
            ):
                dialog.approve_button.click()
            warning.assert_called_once()
            self.assertEqual(
                self.service.store.grant(task.task_id, requested.grant_id).status,
                "requested",
            )
            self.assertNotIn(task.task_id, self.service._grants)
            self.assertTrue(dialog.approve_button.isEnabled())
        finally:
            dialog.reject()
            sip.delete(dialog)

    def test_task_download_is_cancelled_without_destination_or_file_write(self):
        task_id, session_id = self.task_session()
        states = []
        profile = self.service.backend._sessions[session_id].profile
        profile.downloadRequested.connect(
            lambda request: states.append(request.state().name)
        )
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/download",
        )
        self.wait_for(lambda: bool(states))
        self.assertEqual(states, ["DownloadCancelled"])
        self.assertFalse((Path(self.directory.name) / "task-fixture.bin").exists())

    def test_client_navigates_without_a_view_attaches_same_page_and_finishes(self):
        task_id, session_id = self.task_session()
        self.command(
            "session.navigate", task_id=task_id, session_id=session_id, url=self.origin
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is True
        )
        snapshot = self.command(
            "session.inspect", task_id=task_id, session_id=session_id
        )
        self.assertEqual(snapshot.title, "Task fixture")
        self.assertTrue(snapshot.url.startswith(self.origin))
        session = self.service.backend._sessions[session_id]
        page, profile = session.page, session.profile
        self.assertTrue(profile.isOffTheRecord())
        self.assertEqual(session.views, [])
        view = self.command("session.attach", task_id=task_id, session_id=session_id)
        self.assertIs(view.page(), page)
        self.assertEqual(view.page().title(), "Task fixture")
        # Destroying a view must not destroy its independent session/page.
        sip.delete(view)
        view = self.command("session.attach", task_id=task_id, session_id=session_id)
        self.assertIs(view.page(), page)
        completed = self.command("task.finish", task_id=task_id)
        self.assertEqual(completed.status, "completed")
        self.assertTrue(sip.isdeleted(page))
        self.assertTrue(sip.isdeleted(profile))
        self.assertTrue(sip.isdeleted(view))
        events = self.command("task.events", task_id=task_id)
        kinds = [event.kind for event in events]
        for expected in (
            "task.created",
            "task.running",
            "session.opened",
            "session.navigation_requested",
            "session.updated",
            "session.closed",
            "task.completed",
        ):
            self.assertIn(expected, kinds)
        with self.assertRaises(PermissionError):
            self.command("session.open", task_id=task_id)

    def test_worker_commands_run_on_qt_thread_and_queued_cancellation_has_no_effect(
        self,
    ):
        observed = []
        original = self.service.broker._handlers["task.create"]

        def create(**arguments):
            observed.append(QThread.currentThread() == self.app.thread())
            return original(**arguments)

        self.service.broker._handlers["task.create"] = create
        futures = []
        worker = threading.Thread(
            target=lambda: futures.append(
                self.service.broker.submit("task.create", objective="Worker request")
            )
        )
        worker.start()
        worker.join()
        self.wait_for(futures[0].done)
        self.assertEqual(futures[0].result().objective, "Worker request")
        self.assertEqual(observed, [True])
        cancelled = self.service.broker.submit("task.create", objective="Never created")
        self.assertTrue(cancelled.cancel())
        self.app.processEvents()
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        unknown = self.service.broker.submit("shell.execute", command_line="unused")
        self.wait_for(unknown.done)
        with self.assertRaises(ValueError):
            unknown.result()

    def test_task_ownership_url_validation_and_profile_isolation(self):
        first, session_id = self.task_session()
        second, other_id = self.task_session()
        for operation in (
            "session.navigate",
            "session.inspect",
            "session.stop",
            "session.attach",
            "session.close",
        ):
            arguments = {"task_id": second, "session_id": session_id}
            if operation == "session.navigate":
                arguments["url"] = self.origin
            with self.assertRaises(PermissionError):
                self.command(operation, **arguments)
        for url in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "https://user:secret@example.org",
            "https://",
            "data:text/html,fixture",
        ):
            with self.assertRaises(ValueError):
                self.command(
                    "session.navigate", task_id=first, session_id=session_id, url=url
                )
        self.command(
            "session.navigate",
            task_id=first,
            session_id=session_id,
            url=self.origin + "/cookie",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is True
        )
        self.command(
            "session.navigate",
            task_id=first,
            session_id=session_id,
            url=self.origin + "/check-first",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is True
        )
        self.assertIn(("/check-first", "task_cookie=fixture"), self.requests)
        cookies = []
        first_profile = self.service.backend._sessions[session_id].profile
        second_profile = self.service.backend._sessions[other_id].profile
        self.assertIsNot(first_profile, second_profile)
        second_profile.cookieStore().cookieAdded.connect(cookies.append)
        self.command(
            "session.navigate",
            task_id=second,
            session_id=other_id,
            url=self.origin + "/check-second",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(other_id).load_success is True
        )
        second_profile.cookieStore().loadAllCookies()
        self.app.processEvents()
        self.assertEqual(cookies, [])
        self.assertIn(("/check-second", ""), self.requests)

    def test_shutdown_interrupts_tasks_cancels_queued_work_and_rejects_late_events(
        self,
    ):
        task_id, session_id = self.task_session()
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/slow",
        )
        page = self.service.backend._sessions[session_id].page
        queued = self.service.broker.submit(
            "task.create", objective="Must not survive shutdown"
        )
        self.service.close()
        self.assertTrue(queued.cancelled())
        self.assertTrue(sip.isdeleted(page))
        self.app.processEvents()
        restored = TaskService(Path(self.directory.name) / "tasks.sqlite")
        try:
            self.assertEqual(restored.store.get(task_id).status, "interrupted")
            self.assertEqual(len(restored.store.list_tasks()), 1)
            self.assertEqual(restored.backend._sessions, {})
            self.assertEqual(
                restored.store.events(task_id)[-1].kind, "task.interrupted"
            )
        finally:
            restored.close()
            sip.delete(restored)
        denied = self.service.broker.submit("task.list")
        with self.assertRaises(RuntimeError):
            denied.result()

    def test_journal_failure_prevents_navigation_and_async_failure_disables_commands(
        self,
    ):
        task_id, session_id = self.task_session()
        page = self.service.backend._sessions[session_id].page
        with (
            patch.object(
                self.service.store,
                "record_event",
                side_effect=OSError("Fixture disk failure"),
            ),
            patch.object(self.service.backend, "navigate") as navigate,
        ):
            with self.assertRaises(OSError):
                self.command(
                    "session.navigate",
                    task_id=task_id,
                    session_id=session_id,
                    url=self.origin,
                )
            navigate.assert_not_called()
            queued = self.service.broker.submit(
                "task.create", objective="Unjournaled task"
            )
            snapshot = self.service.backend.inspect(session_id)
            self.service.backend.changed.emit(snapshot)
            self.assertTrue(queued.cancelled())
            self.assertTrue(self.service.failure)
        with self.assertRaises(RuntimeError):
            self.service.broker.submit("task.list").result()
        # Cleanup still destroys engine resources even if shutdown cannot write.
        with patch.object(
            self.service.store,
            "record_event",
            side_effect=OSError("Fixture disk failure"),
        ):
            with self.assertRaises(OSError):
                self.service.close()
        self.assertTrue(sip.isdeleted(page))

    def test_windows_share_service_until_last_browser_owner_closes(self):
        with patch("pathlib.Path.home", return_value=Path(self.directory.name)):
            config = Config()
            config.set("home_page", "about:blank")
            first, second = BrowserWindow(), BrowserWindow()
            try:
                self.assertIsNone(first.profile_manager.registry._tasks)
                shared = first.profile_manager.registry.tasks
                self.assertIs(shared, second.profile_manager.registry.tasks)
                task = shared.broker.submit("task.create", objective="Shared lifetime")
                self.wait_for(task.done)
                requested = shared.broker.submit(
                    "grant.request",
                    task_id=task.result().task_id,
                    scope={"origins": []},
                )
                self.wait_for(requested.done)
                shared.approve_grant(task.result().task_id, requested.result().grant_id)
                opened = shared.broker.submit(
                    "session.open", task_id=task.result().task_id
                )
                self.wait_for(opened.done)
                page = shared.backend._sessions[opened.result().session_id].page
                first.close()
                self.assertFalse(sip.isdeleted(page))
                second.close()
                self.assertTrue(sip.isdeleted(page))
            finally:
                if not first._closing:
                    first.close()
                if not second._closing:
                    second.close()
