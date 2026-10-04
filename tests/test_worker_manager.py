"""Exact package approval, owned broker authority, receipts and lifecycle."""

import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application

from services import worker_bundle
from services.connectors import ConnectorOutcome, ConnectorRegistry, ConnectorTool
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.task_dialog import TaskDialog
from ui.worker_approval import WorkerApprovalDialog


class WorkerManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.registry = ConnectorRegistry()
        self.provider_calls = []
        self.registry.register(
            ConnectorTool(
                "docs",
                "read",
                "read",
                "Read the selected synthetic document",
                False,
                self.provider,
            )
        )
        self.service = TaskService(self.path, connectors=self.registry)
        self.client = self.task()

    def provider(self, request, cancel):
        self.provider_calls.append(request)
        return ConnectorOutcome(text="SHARED_DOCUMENT")

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("Worker fixture did not settle")

    def task(self, actions=None, **scope):
        task = self.service._create("Generate a synthetic attachment")
        record = self.service._request_grant(
            task.task_id,
            {
                "actions": actions or ["workspace", "worker"],
                "max_operations": 1000,
                **scope,
            },
        )
        self.service.approve_grant(task.task_id, record.grant_id)
        return self.service.client(task.task_id)

    def call(self, command, client=None, **args):
        future = (client or self.client).submit(command, **args)
        self.wait_for(future.done)
        return future.result()

    def file(self, name, data, client=None):
        item = self.call(
            "workspace.begin",
            client=client,
            name=name,
            media_type="application/octet-stream",
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        if data:
            self.call(
                "workspace.write",
                client=client,
                file_id=item.file_id,
                offset=0,
                data_base64=base64.b64encode(data).decode(),
            )
        return self.call("workspace.seal", client=client, file_id=item.file_id)

    def package(self, source=None, *, capabilities=None, name="helper", client=None):
        source = (
            source
            or "from steed_worker import store_file; store_file('report.csv', b'name,value\\nSteed,1\\n', 'text/csv')"
        )
        self.file(name + ".py", source.encode(), client)
        manifest = self.file(
            name + ".json",
            json.dumps(
                {
                    "version": 1,
                    "name": name,
                    "entrypoint": name + ".py",
                    "files": [name + ".py"],
                    "capabilities": capabilities or [],
                }
            ).encode(),
            client,
        )
        return self.call(
            "workspace.stage_extension", client=client, manifest_id=manifest.file_id
        )

    def request(self, package, request_id="run-1", client=None):
        return self.call(
            "worker.run",
            client=client,
            extension_id=package["extension_id"],
            request_id=request_id,
        )

    def receipt(self, request_id="run-1", client=None):
        return self.service.workers.store.get(
            (client or self.client).task_id, request_id
        )

    def settle(self, request_id="run-1", client=None):
        self.wait_for(
            lambda: (
                self.receipt(request_id, client).state
                not in {"awaiting_approval", "running", "cancelling"}
            )
        )
        return self.receipt(request_id, client)

    def approve(self, request_id="run-1", remember=False):
        return self.service.resolve_worker(
            self.client.task_id, request_id, approve=True, remember=remember
        )

    def test_exact_ui_approval_generates_retained_attachment_and_never_replays(self):
        package = self.package()
        pending = self.request(package)
        self.assertEqual(pending.state, "awaiting_approval")
        self.assertFalse(self.service.workers.jobs)
        dialog = WorkerApprovalDialog(
            self.service, self.service.store.get(self.client.task_id), pending
        )
        self.assertTrue(dialog.deny_button.isDefault())
        self.assertFalse(dialog.remember.isChecked())
        self.assertIn("store_file", dialog.source.toPlainText())
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-worker-approval.png")
        dialog.allow_button.click()
        self.assertEqual(self.settle().state, "succeeded")
        report = next(
            f
            for f in self.service.workspace.list(self.client.task_id)
            if f.name == "report.csv"
        )
        self.assertEqual(
            self.service.workspace.bytes(self.client.task_id, report.file_id),
            b"name,value\nSteed,1\n",
        )
        with patch("services.worker_manager.LocalWorkerJob.start") as start:
            self.assertEqual(self.request(package), self.receipt())
            start.assert_not_called()
        self.assertNotIn(
            "name,value", repr(self.service.store.events(self.client.task_id))
        )
        panel = TaskDialog(self.service)
        panel.table.selectRow(0)
        panel.refresh_activity()
        self.assertEqual(panel.worker_runs.rowCount(), 1)
        panel.panels.setCurrentIndex(
            next(
                i
                for i in range(panel.panels.count())
                if panel.panels.tabText(i) == "Workers"
            )
        )
        panel.show()
        self.app.processEvents()
        panel.grab().save("/tmp/steed-worker-results.png")
        panel.close()

    def test_denied_package_cannot_execute_or_be_approved_by_agent(self):
        package = self.package()
        self.request(package)
        with self.assertRaises(TaskPermissionError):
            self.call("worker.approve", request_id="run-1")
        denied = self.service.resolve_worker(self.client.task_id, "run-1")
        self.assertEqual(denied.state, "cancelled")
        self.assertEqual(self.request(package), denied)
        with self.assertRaises(ValueError):
            self.approve()
        self.assertFalse(self.service.workers.jobs)

    def test_permission_and_cross_task_ownership(self):
        other = self.task(["workspace"])
        foreign = self.package(client=other)
        with self.assertRaises(TaskPermissionError):
            self.request(foreign, client=other)
        with self.assertRaises(KeyError):
            self.request(foreign)
        own = self.package()
        self.request(own)
        with self.assertRaises(KeyError):
            self.call("worker.status", client=other, request_id="run-1")

    def test_remembered_hash_is_bound_to_task_grant_and_changed_code_needs_review(self):
        package = self.package()
        self.request(package)
        self.approve(remember=True)
        self.assertEqual(self.settle().state, "succeeded")
        self.assertEqual(self.request(package, "run-2").state, "running")
        self.assertEqual(self.settle("run-2").state, "succeeded")
        changed = self.package("pass", name="changed")
        self.assertEqual(self.request(changed, "run-3").state, "awaiting_approval")
        with self.assertRaises(ValueError):
            self.request(changed, "run-1")
        other = self.task()
        new = self.package(client=other)
        self.assertEqual(self.request(new, client=other).state, "awaiting_approval")

    def test_tampering_or_deletion_prevents_pending_execution(self):
        package = self.package()
        self.request(package)
        source = next(
            f
            for f in self.service.workspace.list(self.client.task_id)
            if f.name.endswith(".py")
        )
        self.service.store._db.execute(
            "UPDATE workspace_files SET data=? WHERE file_id=?",
            (b"changed", source.file_id),
        )
        self.service.store._db.commit()
        with self.assertRaises(ValueError):
            self.approve()
        self.assertFalse(self.service.workers.jobs)
        self.service.delete_workspace_file(self.client.task_id, source.file_id)
        self.assertEqual(self.receipt().state, "cancelled")

    def test_declared_capability_does_not_bypass_task_permission(self):
        permission = dict(
            connector="docs", tool="read", effect="read", destination="doc-a"
        )
        package = self.package("pass", capabilities=[permission])
        with self.assertRaises(TaskPermissionError):
            self.request(package)
        self.assertEqual(self.provider_calls, [])

    def test_declared_app_read_uses_broker_without_credentials_or_extra_authority(self):
        permission = dict(
            connector="docs", tool="read", effect="read", destination="doc-a"
        )
        self.client = self.task(
            ["workspace", "worker", "app"], connector_permissions=[permission]
        )
        package = self.package(
            """
import time
from steed_worker import call, store_file
assert len(call('app.list_tools')) == 1
receipt = call('app.call_tool', connector='docs', tool='read', destination='doc-a', request_id='read-doc')
while receipt['state'] in ('requested', 'dispatched'):
    time.sleep(0.01)
    receipt = call('app.status', request_id='read-doc')
assert receipt['state'] == 'succeeded'
store_file('document.txt', receipt['text'].encode())
try:
    call('app.call_tool', connector='docs', tool='read', destination='doc-b', request_id='foreign')
except RuntimeError as e:
    assert str(e) == 'worker_capability_undeclared'
else:
    raise AssertionError('destination escaped')
try:
    call('app.status', request_id='outside-worker')
except RuntimeError as e:
    assert str(e) == 'worker_receipt_not_owned'
else:
    raise AssertionError('foreign receipt disclosed')
""",
            capabilities=[permission],
        )
        self.request(package)
        self.approve()
        self.assertEqual(self.settle().state, "succeeded")
        self.assertEqual(len(self.provider_calls), 1)

    def test_undeclared_app_and_host_management_commands_are_denied(self):
        package = self.package("""
from steed_worker import call
for command, args in [
    ('task.finish', {}), ('worker.run', {}),
    ('workspace.export', {'file_id':'x','destination':'/tmp/escape'}),
    ('workspace.begin', {'task_id':'other','name':'x'}),
    ('app.call_tool', {'connector':'docs','tool':'read','destination':'doc-a','request_id':'x'}),
]:
    try:
        call(command, **args)
    except RuntimeError:
        pass
    else:
        raise AssertionError('unexpected authority')
""")
        self.request(package)
        self.approve()
        self.assertEqual(self.settle().state, "succeeded")
        self.assertEqual(self.provider_calls, [])
        self.assertFalse(self.service.failure)

    def test_cancellation_between_rpc_enqueue_and_dispatch_stops_mutation(self):
        package = self.package()
        self.request(package)
        original = self.service.broker.submit_guarded

        def cancel_after_enqueue(command, guard, **args):
            future = original(command, guard, **args)
            self.service.workers.jobs[(self.client.task_id, "run-1")].cancelled.set()
            return future

        with patch.object(self.service.broker, "submit_guarded", cancel_after_enqueue):
            self.approve()
            self.assertEqual(self.settle().state, "cancelled")
        self.assertEqual(len(self.service.workspace.list(self.client.task_id)), 2)

    def test_cancel_revoke_expiry_and_source_removal_kill_running_worker(self):
        for action in ["cancel", "revoke", "expire", "delete"]:
            with self.subTest(action=action):
                self.client = self.task()
                package = self.package("import time; time.sleep(10)")
                self.request(package)
                self.approve()
                job = self.service.workers.jobs[(self.client.task_id, "run-1")]
                if action == "cancel":
                    self.call("worker.cancel", request_id="run-1")
                elif action == "revoke":
                    self.service.revoke_grant(
                        self.client.task_id,
                        self.service._grants[self.client.task_id][0],
                    )
                elif action == "expire":
                    grant = self.service._grants[self.client.task_id][1]
                    grant.deadline = -1
                    self.service._expire_grants()
                else:
                    source = self.service.workspace.list(self.client.task_id)[0]
                    self.service.delete_workspace_file(
                        self.client.task_id, source.file_id
                    )
                self.assertEqual(self.settle().state, "cancelled")
                self.assertFalse(job.thread.is_alive())

    def test_restart_interrupts_requests_clears_remembered_packages_and_never_replays(
        self,
    ):
        package = self.package("pass")
        self.request(package)
        self.approve(remember=True)
        self.settle()
        self.request(self.package("pass", name="pending"), "pending")
        with patch.object(self.service.workers, "cancel_task"):
            self.service.close()  # Simulate persisted pending code at interrupted shutdown.
        sip.delete(self.service)
        self.service = TaskService(self.path)
        self.assertEqual(self.receipt("pending").state, "interrupted")
        self.assertEqual(self.receipt().state, "succeeded")
        self.assertFalse(self.service.workers.jobs)
        self.assertEqual(
            self.service.store._db.execute(
                "SELECT COUNT(*) FROM worker_package_approvals"
            ).fetchone()[0],
            0,
        )

    def test_failed_approval_journal_prevents_execution_and_disables_authority(self):
        package = self.package("pass")
        self.request(package)
        with (
            patch.object(
                self.service.store, "_append", side_effect=sqlite3.OperationalError
            ),
            patch("services.worker_manager.LocalWorkerJob.start") as start,
        ):
            with self.assertRaises(sqlite3.Error):
                self.approve(remember=True)
            start.assert_not_called()
        self.assertTrue(self.service.failure)
        self.assertEqual(self.receipt().state, "awaiting_approval")
        self.assertEqual(
            self.service.store._db.execute(
                "SELECT COUNT(*) FROM worker_package_approvals"
            ).fetchone()[0],
            0,
        )

    def test_shutdown_joins_running_worker(self):
        self.request(self.package("import time; time.sleep(10)"))
        self.approve()
        job = self.service.workers.jobs[(self.client.task_id, "run-1")]
        self.service.close()
        self.assertFalse(job.thread.is_alive())
        with sqlite3.connect(self.path) as db:
            state = db.execute(
                "SELECT state FROM worker_runs WHERE task_id=? AND request_id=?",
                (self.client.task_id, "run-1"),
            ).fetchone()[0]
        self.assertIn(state, {"cancelled", "interrupted"})

    def test_capacity_limits_leave_other_requests_pending(self):
        clients = [self.client, self.task(), self.task()]
        packages = [
            self.package("import time; time.sleep(10)", client=c) for c in clients
        ]
        for client, package in zip(clients, packages, strict=True):
            self.request(package, client=client)
        for client in clients[:2]:
            self.service.resolve_worker(client.task_id, "run-1", approve=True)
        with self.assertRaises(TaskPermissionError):
            self.service.resolve_worker(clients[2].task_id, "run-1", approve=True)
        self.assertEqual(self.receipt(client=clients[2]).state, "awaiting_approval")
        self.request(packages[0], "same-task", client=clients[0])
        with self.assertRaises(TaskPermissionError):
            self.approve("same-task")
        for client in clients[:2]:
            self.service.cancel_worker(client.task_id, "run-1")
            self.settle(client=client)
        self.service.resolve_worker(clients[2].task_id, "run-1")

    def test_failed_run_is_retained_and_never_replayed(self):
        package = self.package("raise RuntimeError('PRIVATE_SCRIPT_ERROR')")
        self.request(package)
        self.approve()
        result = self.settle()
        self.assertEqual(result.state, "failed")
        self.assertNotIn("PRIVATE_SCRIPT_ERROR", repr(result))
        with patch("services.worker_manager.LocalWorkerJob.start") as start:
            self.assertEqual(self.request(package), result)
            start.assert_not_called()

    def test_worker_file_rpc_obeys_operation_budget(self):
        self.client = self.task(max_operations=9)
        package = self.package()
        self.request(package)  # Upload/stage/run spends eight operations.
        self.approve()
        self.assertIn(self.settle().state, {"cancelled", "failed"})
        self.assertFalse(
            any(
                f.name == "report.csv" and f.status == "sealed"
                for f in self.service.workspace.list(self.client.task_id)
            )
        )

    def test_file_permission_cannot_be_assumed_from_worker_permission(self):
        package = self.package()
        live = self.service._grants[self.client.task_id][1]
        from dataclasses import replace

        live.scope = replace(live.scope, actions=frozenset({"worker"}))
        self.request(package)
        self.approve()
        self.assertEqual(self.settle().state, "failed")
        self.assertFalse(
            any(
                f.name == "report.csv"
                for f in self.service.workspace.list(self.client.task_id)
            )
        )
        self.assertFalse(self.service.failure)

    def test_failed_completion_journal_stops_authority_without_false_success(self):
        package = self.package("pass")
        self.request(package)
        self.approve()
        original = self.service.store._append

        def fail_completion(task_id, kind, data):
            if kind == "worker.succeeded":
                raise sqlite3.OperationalError("synthetic failure")
            return original(task_id, kind, data)

        with patch.object(self.service.store, "_append", fail_completion):
            self.wait_for(lambda: bool(self.service.failure))
        self.assertEqual(self.receipt().state, "running")
        self.assertFalse(self.service.workers.jobs)

    def test_schema_11_migration_preserves_workspace(self):
        self.package("pass")
        self.service.close()
        sip.delete(self.service)
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE worker_package_approvals")
            db.execute("DROP TABLE worker_runs")
            db.execute("PRAGMA user_version=11")
        self.service = TaskService(self.path)
        self.assertEqual(
            self.service.store._db.execute("PRAGMA user_version").fetchone()[0], 18
        )
        self.assertEqual(len(self.service.workspace.list(self.client.task_id)), 2)
        self.assertEqual(self.service.workers.store.list(self.client.task_id), [])

    def test_missing_bundle_rejects_request_without_journal_failure_or_fallback(self):
        package = self.package("pass")
        with patch.object(
            worker_bundle, "BUNDLE_ROOT", Path(self.directory.name) / "absent"
        ):
            with self.assertRaisesRegex(ValueError, "not_installed"):
                self.call(
                    "worker.run",
                    extension_id=package["extension_id"],
                    request_id="docs",
                    runtime="documents",
                )
        self.assertFalse(self.service.failure)
        self.assertFalse(self.service.workers.jobs)
        self.assertEqual(self.service.workers.store.list(self.client.task_id), [])

    def test_changed_environment_needs_new_approval_and_does_not_inherit_remembered_choice(
        self,
    ):
        package = self.package("pass")
        first = self.request(package)
        self.approve(remember=True)
        self.assertEqual(self.settle().state, "succeeded")
        changed = replace(worker_bundle.environment(), sha256="0" * 64)
        with patch("services.worker_manager.environment", return_value=changed):
            second = self.request(package, "changed")
        self.assertEqual(second.state, "awaiting_approval")
        self.assertNotEqual(first.environment_sha256, second.environment_sha256)
        with self.assertRaisesRegex(ValueError, "environment changed"):
            self.approve("changed")
        self.assertFalse(self.service.workers.jobs)

    def test_document_runtime_requires_distinct_popup_and_stable_id_binds_runtime(self):
        try:
            worker_bundle.require_installed(worker_bundle.environment("documents"))
        except worker_bundle.BundleError:
            self.skipTest("Optional document bundle not installed")
        package = self.package("pass")
        self.request(package)
        self.approve(remember=True)
        self.assertEqual(self.settle().state, "succeeded")
        with self.assertRaisesRegex(ValueError, "runtime"):
            self.call(
                "worker.run",
                extension_id=package["extension_id"],
                request_id="run-1",
                runtime="documents",
            )
        pending = self.call(
            "worker.run",
            extension_id=package["extension_id"],
            request_id="docs",
            runtime="documents",
        )
        self.assertEqual(pending.state, "awaiting_approval")
        dialog = WorkerApprovalDialog(
            self.service, self.service.store.get(self.client.task_id), pending
        )
        self.assertIn("environment", dialog.remember.text())
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-document-worker-approval.png")
        dialog.allow_button.click()
        self.assertEqual(self.settle("docs").state, "succeeded")

    def test_real_document_outputs_are_retained_with_environment_bound_receipt(self):
        try:
            worker_bundle.require_installed(worker_bundle.environment("documents"))
        except worker_bundle.BundleError:
            self.skipTest("Optional document bundle not installed")
        package = self.package("""
from io import BytesIO
from reportlab.pdfgen import canvas
from openpyxl import Workbook
from PIL import Image
from steed_worker import store_file
out = BytesIO()
pdf = canvas.Canvas(out)
pdf.drawString(72,720,'Synthetic Steed report')
pdf.save()
store_file('report.pdf', out.getvalue(), 'application/pdf')
out = BytesIO()
sheet = Workbook()
sheet.active.append(['Steed',42])
sheet.save(out)
store_file('report.xlsx', out.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
out = BytesIO()
Image.new('RGB',(16,16),'blue').save(out,format='PNG')
store_file('report.png', out.getvalue(), 'image/png')
""")
        pending = self.call(
            "worker.run",
            extension_id=package["extension_id"],
            request_id="docs",
            runtime="documents",
        )
        self.assertEqual(pending.state, "awaiting_approval")
        self.approve("docs")
        receipt = self.settle("docs")
        self.assertEqual(receipt.state, "succeeded", receipt)
        self.assertEqual(receipt.runtime, "documents")
        self.assertEqual(
            receipt.environment_sha256, worker_bundle.environment("documents").sha256
        )
        files = {
            f.name: self.service.workspace.bytes(self.client.task_id, f.file_id)
            for f in self.service.workspace.list(self.client.task_id)
            if f.name.startswith("report.")
        }
        self.assertTrue(files["report.pdf"].startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", files["report.pdf"])
        self.assertTrue(files["report.png"].startswith(b"\x89PNG\r\n\x1a\n"))
        with zipfile.ZipFile(io.BytesIO(files["report.xlsx"])) as archive:
            xml = archive.read("xl/worksheets/sheet1.xml")
            self.assertIn(b"Steed", xml)
            self.assertIn(b"<v>42</v>", xml)
        self.assertNotIn(
            "Synthetic Steed report",
            repr(self.service.store.events(self.client.task_id)),
        )

    def test_wheel_tampering_after_approval_fails_before_generated_code_or_rpc(self):
        env = worker_bundle.environment("documents")
        root = Path(self.directory.name) / "bundles"
        target = root / env.name
        target.mkdir(parents=True)
        for wheel in env.wheels:
            (target / wheel["filename"]).write_bytes(b"x" * wheel["size"])
        package = self.package("raise RuntimeError('must not execute')")
        with patch.object(worker_bundle, "BUNDLE_ROOT", root):
            self.call(
                "worker.run",
                extension_id=package["extension_id"],
                request_id="docs",
                runtime="documents",
            )
            self.approve("docs")
            receipt = self.settle("docs")
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(receipt.reason, "worker_bundle_invalid")
        self.assertEqual(receipt.rpc_count, 0)
        self.assertFalse(self.service.failure)

    def test_schema_12_migration_keeps_legacy_receipts_and_discards_legacy_approvals(
        self,
    ):
        package = self.package("pass")
        receipt = self.request(package)
        self.approve(remember=True)
        self.assertEqual(self.settle().state, "succeeded")
        grant_id = self.service._grants[self.client.task_id][0]
        events = self.service.store.events(self.client.task_id)
        self.service.close()
        sip.delete(self.service)
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE worker_runs DROP COLUMN runtime")
            db.execute("ALTER TABLE worker_runs DROP COLUMN environment_sha256")
            db.execute("DROP TABLE worker_package_approvals")
            db.execute(
                "CREATE TABLE worker_package_approvals(task_id TEXT,grant_id TEXT,package_sha256 TEXT,PRIMARY KEY(task_id,grant_id,package_sha256))"
            )
            db.execute(
                "INSERT INTO worker_package_approvals VALUES (?,?,?)",
                (self.client.task_id, grant_id, receipt.package_sha256),
            )
            db.execute("PRAGMA user_version=12")
        self.service = TaskService(self.path)
        retained = self.service.workers.store.get(self.client.task_id, "run-1")
        self.assertEqual(retained.state, "succeeded")
        self.assertEqual(retained.package_sha256, receipt.package_sha256)
        self.assertEqual(retained.runtime, "stdlib")
        self.assertEqual(retained.environment_sha256, "")
        self.assertEqual(
            self.service.store.events(self.client.task_id)[: len(events)], events
        )
        self.assertEqual(
            self.service.store._db.execute(
                "SELECT count(*) FROM worker_package_approvals"
            ).fetchone()[0],
            0,
        )
