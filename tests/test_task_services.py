"""A deterministic client drives real Qt task pages without AI or accounts."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QThread
from qt_test_support import application, keep_directory

from browser_window import BrowserWindow
from config import Config
from services.task_service import TaskService


class TaskServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append((self.path, self.headers.get("Cookie", "")))
                payload = b"<html><title>Task fixture</title><body>Independent page</body></html>"
                if self.path == "/slow":
                    time.sleep(0.3)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(payload)))
                if self.path == "/cookie":
                    self.send_header("Set-Cookie", "task_cookie=fixture; Path=/")
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
        snapshot = self.command("session.open", task_id=task.task_id)
        return task.task_id, snapshot.session_id

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
        with self.assertRaises(ValueError):
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
            with self.assertRaises(ValueError):
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
