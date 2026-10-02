"""Fixed asynchronous setup check, cached discovery and trusted retry/lifecycle."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application

from services.agent_tools import TOOLS_BY_NAME
from services.local_worker import WorkerOutcome
from services.task_service import TaskService
from ui.task_dialog import TaskDialog


class WorkerDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.service = TaskService(self.path)
        self.task = self.service._create("Inspect worker support")
        self.client = self.service.client(self.task.task_id)

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def result(self, future):
        end = time.monotonic() + 5
        while not future.done() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.002)
        return future.result(timeout=0)

    def test_actual_sandbox_support_before_any_grant_or_upload(self):
        metadata = self.result(self.client.submit("worker.capabilities"))
        self.assertTrue(metadata["available"], metadata)
        self.assertEqual(metadata["reason"], "")
        self.assertEqual(metadata["execution_approval"], "exact_package")
        self.assertEqual(metadata["limits"]["wall_seconds"], 20)
        self.assertFalse(metadata["direct_network"])
        self.assertFalse(metadata["subprocesses"])
        self.assertEqual(self.service.store.grants(self.task.task_id), [])
        self.assertEqual(self.service.workspace.list(self.task.task_id), [])
        self.assertEqual(self.service.workers.store.list(self.task.task_id), [])
        self.assertEqual(self.service._sessions, {})

    def test_probe_failure_is_sanitized_and_cached_without_host_fallback(self):
        with patch(
            "services.worker_diagnostics.run_worker",
            side_effect=OSError("PRIVATE_HOST_DETAIL"),
        ) as execute:
            first = self.result(self.client.submit("worker.capabilities"))
            second = self.result(self.client.submit("worker.capabilities"))
        self.assertEqual(first, second)
        self.assertFalse(first["available"])
        self.assertEqual(first["reason"], "sandbox_unavailable")
        self.assertNotIn("PRIVATE_HOST_DETAIL", repr(first))
        self.assertEqual(execute.call_count, 1)
        self.assertFalse(self.service.failure)

    def test_concurrent_callers_share_one_background_check(self):
        threads = []
        owner = threading.get_ident()

        def probe(*args, **kwargs):
            threads.append(threading.get_ident())
            time.sleep(0.03)
            return WorkerOutcome("succeeded")

        with patch("services.worker_diagnostics.run_worker", probe):
            first = self.client.submit("worker.capabilities")
            second = self.client.submit("worker.capabilities")
            self.assertEqual(self.result(first), self.result(second))
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], owner)

    def test_trusted_retry_after_fixing_setup_replaces_cached_failure(self):
        with patch(
            "services.worker_diagnostics.run_worker",
            return_value=WorkerOutcome("failed"),
        ):
            self.assertFalse(
                self.result(self.client.submit("worker.capabilities"))["available"]
            )
        self.assertTrue(self.result(self.service.check_worker_setup())["available"])
        self.assertTrue(
            self.result(self.client.submit("worker.capabilities"))["available"]
        )

    def test_agent_cannot_request_retry_or_inject_probe_code(self):
        for arguments in (
            {"recheck": True},
            {"source": "arbitrary"},
            {"task_id": "foreign"},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                TOOLS_BY_NAME["steed_worker_capabilities"].validate(arguments)
        self.assertNotIn("worker.retry_setup", self.service.broker._handlers)

    def test_retry_while_checking_does_not_spawn_more_workers(self):
        gate = threading.Event()
        started = threading.Event()

        def wait(*args, **kwargs):
            started.set()
            gate.wait(2)
            return WorkerOutcome("succeeded")

        with patch(
            "services.worker_diagnostics.run_worker", side_effect=wait
        ) as execute:
            future = self.service.workers.capabilities()
            self.assertTrue(started.wait(1))
            try:
                with self.assertRaises(ValueError):
                    self.service.check_worker_setup()
                self.assertEqual(execute.call_count, 1)
            finally:
                gate.set()
            self.assertTrue(self.result(future)["available"])

    def test_closed_service_stops_pending_check_without_hanging(self):
        started = threading.Event()

        def probe(files, entrypoint, cancelled, request, **kwargs):
            started.set()
            cancelled.wait(2)
            return WorkerOutcome("cancelled")

        with patch("services.worker_diagnostics.run_worker", probe):
            future = self.service.workers.capabilities()
            self.assertTrue(started.wait(1))
            check = self.service.workers.diagnostics
            self.service.close()
            self.assertFalse(check.worker_thread.is_alive())
            self.assertEqual(future.result()["reason"], "setup_check_ended")

    def test_check_uses_only_fixed_source_and_no_task_broker_operations(self):
        def probe(files, entrypoint, cancelled, request, **kwargs):
            self.assertEqual(list(files), ["check.py"])
            self.assertEqual(entrypoint, "check.py")
            self.assertLessEqual(kwargs["timeout"], 3)
            with self.assertRaises(RuntimeError):
                request({"command": "workspace.list", "arguments": {}})
            return WorkerOutcome("succeeded")

        with patch("services.worker_diagnostics.run_worker", probe):
            self.assertTrue(
                self.result(self.client.submit("worker.capabilities"))["available"]
            )
        self.assertEqual(
            [e.kind for e in self.service.store.events(self.task.task_id)],
            ["task.created"],
        )

    def test_setup_is_rechecked_for_actual_execution_after_cached_success(self):
        self.assertTrue(
            self.result(self.client.submit("worker.capabilities"))["available"]
        )
        from services.local_worker import worker_command

        with patch("services.local_worker.Path.is_file", return_value=False):
            with self.assertRaises(RuntimeError):
                worker_command(Path("/package"), Path("/sdk"), "main.py")
        self.assertFalse(self.service._grants)

    def test_ui_check_ready_and_unavailable_without_exposing_paths(self):
        panel = TaskDialog(self.service)
        panel.panels.setCurrentIndex(
            next(
                i
                for i in range(panel.panels.count())
                if panel.panels.tabText(i) == "Workers"
            )
        )
        panel.worker_setup_button.click()
        self.result(self.service.workers.diagnostics.future)
        self.assertIn("ready", panel.worker_setup_status.text())
        panel.show()
        self.app.processEvents()
        panel.grab().save("/tmp/steed-worker-setup.png")
        with patch(
            "services.worker_diagnostics.run_worker",
            side_effect=OSError("PRIVATE_PATH"),
        ):
            panel.worker_setup_button.click()
            self.result(self.service.workers.diagnostics.future)
        self.assertIn("unavailable", panel.worker_setup_status.text())
        self.assertIn("Bubblewrap", panel.worker_setup_status.text())
        self.assertNotIn("PRIVATE_PATH", panel.worker_setup_status.text())
        self.assertTrue(panel.worker_setup_button.isEnabled())
        panel.close()
        sip.delete(panel)

    def test_restart_does_not_reuse_old_machine_support_result(self):
        with patch(
            "services.worker_diagnostics.run_worker",
            return_value=WorkerOutcome("succeeded"),
        ):
            self.assertTrue(
                self.result(self.client.submit("worker.capabilities"))["available"]
            )
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.path)
        task = self.service._create("Inspect fresh setup")
        with patch(
            "services.worker_diagnostics.run_worker",
            return_value=WorkerOutcome("failed"),
        ):
            self.assertFalse(
                self.result(
                    self.service.client(task.task_id).submit("worker.capabilities")
                )["available"]
            )
