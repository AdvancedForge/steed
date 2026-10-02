"""Provider-neutral setup, trusted UI, task grants and retained Google receipts."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from app_connector_support import MemorySecrets, SyntheticGoogle, install_account
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application

from services.app_accounts import AccountError, AccountStore
from services.app_connections import AppConnections, AppProvider
from services.connectors import (
    ConnectorCancellation,
    ConnectorOutcome,
    ConnectorRequest,
    ConnectorTool,
)
from services.google_connector import GoogleConnector
from services.task_policy import LiveGrant, TaskScope
from services.task_service import TaskService
from ui.app_connections import AppConnectionsDialog
from ui.task_approval import TaskApprovalDialog


class AppConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.secrets = MemorySecrets()
        self.store = AccountStore(self.path / "accounts.json", self.secrets)
        self.http = SyntheticGoogle()
        self.account = install_account(self.store)
        self.gate = threading.Event()
        self.gate.set()
        self.setup_thread = None

        def connect(store, client_file, capabilities, cancelled):
            self.setup_thread = threading.get_ident()
            self.gate.wait(5)
            return (
                install_account(store, capabilities=capabilities)
                if not cancelled.is_set()
                else self._cancelled()
            )

        self.providers = (
            AppProvider(
                "google",
                "Google fixture",
                (("documents", "Docs"), ("email", "Email")),
                connect,
                lambda account, store: GoogleConnector(
                    account, store, http=self.http
                ).tools(),
            ),
            AppProvider(
                "microsoft",
                "Microsoft fixture",
                (("documents", "Documents"),),
                lambda store, file, capabilities, cancel: install_account(
                    store, provider="microsoft", capabilities=capabilities
                ),
                lambda account, store: [
                    ConnectorTool(
                        account.connector,
                        "create",
                        "write",
                        "Synthetic Microsoft document",
                        True,
                        lambda request, cancel: ConnectorOutcome(
                            reference="synthetic-microsoft-doc"
                        ),
                    )
                ],
            ),
        )
        self.manager = AppConnections(
            self.store.path, store=self.store, providers=self.providers
        )
        self.service = TaskService(
            self.path / "tasks.sqlite", connectors=self.manager.connectors
        )
        self.manager.task_service = self.service
        self.dialogs = []

    def _cancelled(self):
        raise AccountError("Connection cancelled.")

    def tearDown(self):
        self.gate.set()
        for dialog in self.dialogs:
            dialog.close()
            sip.delete(dialog)
        self.wait_for(lambda: not self.manager.busy)
        self.wait_for(lambda: not self.service._connector_jobs)
        self.manager.close()
        self.service.close()
        sip.delete(self.manager)
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("App connection fixture timed out")

    def resolve(self, future):
        self.wait_for(future.done)
        return future.result()

    def grant(
        self,
        tool="create_document",
        destination="drive:root",
        effect="write",
        account=None,
        approve=True,
    ):
        account = account or self.account
        task = self.service._create("Synthetic provider task")
        record = self.service._request_grant(
            task.task_id,
            {
                "actions": ["app", "artifact"],
                "connector_permissions": [
                    {
                        "connector": account.connector,
                        "tool": tool,
                        "effect": effect,
                        "destination": destination,
                    }
                ],
            },
        )
        if approve:
            self.service.approve_grant(task.task_id, record.grant_id)
        return self.service.client(task.task_id), record

    def artifact(self, client):
        return self.resolve(
            client.submit(
                "artifact.store", title="Synthetic draft", content="SYNTHETIC_BODY"
            )
        )

    def call(
        self,
        client,
        artifact,
        *,
        tool="create_document",
        destination="drive:root",
        request_id="doc-1",
        account=None,
    ):
        return self.resolve(
            client.submit(
                "app.call_tool",
                connector=(account or self.account).connector,
                tool=tool,
                destination=destination,
                request_id=request_id,
                artifact_id=artifact.artifact_id,
            )
        )

    def settled(self, client, request_id="doc-1"):
        self.wait_for(
            lambda: (client.task_id, request_id) not in self.service._connector_jobs
        )
        return self.resolve(client.submit("app.status", request_id=request_id))

    def test_google_writes_use_gateway_and_uncertain_doc_lookup_does_not_replay(self):
        self.http.lose_document = True
        client, _ = self.grant()
        artifact = self.artifact(client)
        requested = self.call(client, artifact)
        self.assertEqual(requested.state, "requested")
        uncertain = self.settled(client)
        self.assertEqual(uncertain.state, "uncertain")
        self.assertEqual(self.call(client, artifact).state, "uncertain")
        self.resolve(client.submit("app.reconcile", request_id="doc-1"))
        self.assertEqual(self.settled(client).state, "succeeded")
        self.assertEqual(len(self.http.documents), 1)
        events = str(self.service.store.events(client.task_id))
        self.assertNotIn("SYNTHETIC_BODY", events)
        self.assertNotIn("SYNTHETIC_NEW_ACCESS", events)

    def test_uncertain_gmail_has_no_automatic_reconciliation_or_retry(self):
        self.http.lose_email = True
        client, _ = self.grant("send_email", "email:recipient@example.test", "send")
        artifact = self.artifact(client)
        self.call(
            client,
            artifact,
            tool="send_email",
            destination="email:recipient@example.test",
        )
        self.assertEqual(self.settled(client).state, "uncertain")
        with self.assertRaises(ValueError):
            self.resolve(client.submit("app.reconcile", request_id="doc-1"))
        self.call(
            client,
            artifact,
            tool="send_email",
            destination="email:recipient@example.test",
        )
        self.assertEqual(len(self.http.messages), 1)

    def test_provider_destination_validation_precedes_approval(self):
        for tool, destination, effect in (
            ("create_document", "drive:arbitrary-folder", "write"),
            ("send_email", "email:a@example.test,b@example.test", "send"),
        ):
            with self.assertRaises(ValueError):
                self.grant(tool, destination, effect)
        self.assertFalse(self.service.store.grants())
        self.assertFalse(self.http.calls)

    def test_disconnect_denies_pending_revokes_active_and_blocks_old_account(self):
        active, record = self.grant()
        pending, pending_record = self.grant(approve=False)
        self.manager.disconnect(self.account.account_id)
        self.wait_for(lambda: not self.manager.busy)
        self.assertEqual(
            self.service.store.grant(active.task_id, record.grant_id).status, "revoked"
        )
        self.assertEqual(
            self.service.store.grant(pending.task_id, pending_record.grant_id).status,
            "denied",
        )
        self.assertEqual(self.manager.connectors.describe(), [])
        self.assertNotIn(self.account.account_id, self.secrets.values)
        with self.assertRaises(ValueError):
            self.service.approve_grant(pending.task_id, pending_record.grant_id)

    def test_disconnect_before_queued_write_never_calls_provider(self):
        client, _ = self.grant()
        artifact = self.artifact(client)
        receipt = self.service._app_call(
            client.task_id,
            self.account.connector,
            "create_document",
            "queued",
            "drive:root",
            artifact.artifact_id,
        )
        self.manager.disconnect(self.account.account_id)
        self.wait_for(
            lambda: not self.manager.busy and not self.service._connector_jobs
        )
        self.assertEqual(
            self.service.connector_store.get(client.task_id, receipt.request_id).state,
            "cancelled",
        )
        self.assertFalse(self.http.documents)

    def test_second_provider_runs_without_changes_to_google_or_task_authority(self):
        self.manager.connect_account(
            "microsoft", self.path / "synthetic-client", ("documents",)
        )
        self.wait_for(lambda: not self.manager.busy)
        account = self.store.list()[-1]
        self.assertEqual(account.provider, "microsoft")
        client, _ = self.grant("create", "workspace-a", "write", account)
        self.call(
            client,
            self.artifact(client),
            tool="create",
            destination="workspace-a",
            account=account,
        )
        self.assertEqual(self.settled(client).reference, "synthetic-microsoft-doc")
        self.assertFalse(self.http.calls)

    def test_setup_is_off_qt_thread_and_never_approves_a_task(self):
        self.manager.connect_account(
            "google", self.path / "synthetic-client", ("email",)
        )
        self.wait_for(lambda: not self.manager.busy)
        self.assertNotEqual(self.setup_thread, threading.get_ident())
        new_account = self.store.list()[-1]
        self.assertNotEqual(new_account.connector, self.account.connector)
        tools = [
            t
            for t in self.manager.connectors.describe()
            if t["connector"] == new_account.connector
        ]
        self.assertEqual([t["tool"] for t in tools], ["send_email"])
        self.assertFalse(self.service.store.grants())

    def test_cancelling_setup_preserves_existing_accounts(self):
        initial = self.store.list()
        self.gate.clear()
        self.manager.connect_account(
            "google", self.path / "synthetic-client", ("email",)
        )
        self.manager.cancel()
        self.gate.set()
        self.wait_for(lambda: not self.manager.busy)
        self.assertEqual(self.store.list(), initial)
        self.assertIn("cancelled", self.manager.message)

    def test_ui_shows_account_and_scope_description_as_plain_text(self):
        dialog = AppConnectionsDialog(self.manager)
        self.dialogs.append(dialog)
        self.assertEqual(dialog.accounts.item(0, 1).text(), "fixture@example.test")
        dialog.accounts.selectRow(0)
        self.assertTrue(dialog.disconnect_button.isEnabled())
        client, record = self.grant(approve=False)
        approval = TaskApprovalDialog(
            self.service, self.service.store.get(client.task_id), record
        )
        self.dialogs.append(approval)
        from PyQt6.QtWidgets import QPlainTextEdit

        text = "\n".join(w.toPlainText() for w in approval.findChildren(QPlainTextEdit))
        self.assertIn("Google Docs (fixture@example.test)", text)
        self.assertIn("drive:root", text)
        self.assertNotIn("SYNTHETIC_REFRESH", text)

    def test_authority_cancellation_detects_expiry_between_provider_requests(self):
        clock = [0]
        scope = TaskScope.parse(
            {
                "actions": ["app", "artifact"],
                "duration_seconds": 1,
                "connector_permissions": [
                    {
                        "connector": self.account.connector,
                        "tool": "create_document",
                        "effect": "write",
                        "destination": "drive:root",
                    }
                ],
            }
        )
        grant = LiveGrant(scope, clock=lambda: clock[0])
        request = ConnectorRequest(
            "request",
            self.account.connector,
            "create_document",
            "write",
            "drive:root",
            None,
            "a" * 32,
        )
        cancel = ConnectorCancellation(grant, request, artifact=True)
        self.assertFalse(cancel.is_set())
        clock[0] = 2
        self.assertTrue(cancel.is_set())
