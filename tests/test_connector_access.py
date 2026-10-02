"""Standing account switches stay bounded, revocable and separate from execution."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_connector_support import MemorySecrets, SyntheticGoogle, install_account
from PyQt6 import sip
from PyQt6.QtCore import Qt
from qt_test_support import application

from services.app_accounts import AccountStore
from services.app_connections import AppConnections, AppProvider
from services.connectors import ConnectorOutcome, ConnectorTool
from services.google_connector import GoogleConnector
from services.task_service import TaskService
from ui.app_connections import AppConnectionsDialog


class ConnectorAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = AccountStore(self.root / "accounts.json", MemorySecrets())
        self.account = install_account(self.store)
        self.http = SyntheticGoogle()
        self.providers = [
            AppProvider(
                "google",
                "Google fixture",
                (("documents", "Docs"), ("email", "Gmail")),
                lambda *args: None,
                lambda account, store: GoogleConnector(
                    account, store, http=self.http
                ).tools(),
            )
        ]
        self.manager = AppConnections(
            self.store.path, store=self.store, providers=self.providers
        )
        self.service = TaskService(
            self.root / "tasks.sqlite", connectors=self.manager.connectors
        )
        self.manager.task_service = self.service
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            sip.delete(dialog)
        self.manager.close()
        self.service.close()
        sip.delete(self.manager)
        sip.delete(self.service)
        self.temp.cleanup()

    def request(
        self,
        tool="create_document",
        effect="write",
        destination="drive:root",
        account=None,
        **extra,
    ):
        task = self.service._create("Synthetic account delegation")
        grant = self.service._request_grant(
            task.task_id,
            {
                "actions": ["app", "artifact"],
                "connector_permissions": [
                    {
                        "connector": (account or self.account).connector,
                        "tool": tool,
                        "effect": effect,
                        "destination": destination,
                    }
                ],
                **extra,
            },
        )
        return task, grant

    def switch(self, group, enabled=True):
        self.manager.set_access(self.account.account_id, group, enabled)

    def test_sign_in_alone_does_not_approve_and_toggle_covers_future_destinations(self):
        pending_task, pending = self.request()
        self.assertEqual(pending.status, "requested")
        self.switch("write")
        self.assertEqual(
            self.service.store.grant(pending_task.task_id, pending.grant_id).status,
            "approved",
        )
        task, grant = self.request(destination="drive:folder:next-folder")
        self.assertEqual(grant.status, "approved")
        self.assertEqual(
            grant.scope["connector_permissions"][0]["destination"],
            "drive:folder:next-folder",
        )
        self.assertTrue(
            any(
                e.kind == "permission.account_access"
                for e in self.service.store.events(task.task_id)
            )
        )
        self.assertEqual(self.service.store.approval_rules(), [])

    def test_reads_sends_and_direct_edits_are_separate(self):
        self.switch("write")
        _, direct = self.request(
            "replace_document_direct", destination="doc:example:tab:t.0"
        )
        self.assertEqual(direct.status, "requested")
        _, read = self.request("read_document", "read", "doc:example:tab:t.0")
        self.assertEqual(read.status, "requested")
        _, send = self.request("send_email", "send", "email:person@example.test")
        self.assertEqual(send.status, "requested")
        self.switch("read")
        _, read = self.request("read_document", "read", "doc:other:tab:t.0")
        self.assertEqual(read.status, "approved")
        self.switch("direct")
        _, direct = self.request(
            "replace_document_direct", destination="doc:other:tab:t.0"
        )
        self.assertEqual(direct.status, "approved")

    def test_turning_off_revokes_active_and_forgets_matching_saved_rules(self):
        task, pending = self.request()
        self.service.approve_grant(task.task_id, pending.grant_id, remember="global")
        self.switch("write")
        other, approved = self.request()
        self.assertEqual(approved.status, "approved")
        self.switch("write", False)
        self.assertEqual(self.service.store.get(task.task_id).status, "cancelled")
        self.assertEqual(self.service.store.get(other.task_id).status, "cancelled")
        self.assertEqual(self.service.store.approval_rules(), [])
        self.assertEqual(self.request()[1].status, "requested")

    def test_limits_and_file_permissions_do_not_inherit_account_access(self):
        self.switch("write")
        for extra in [
            {"duration_seconds": 601},
            {"max_operations": 1001},
            {"max_requests": 1001},
            {"max_sessions": 2},
            {"actions": ["app", "artifact", "workspace"]},
            {"actions": ["app", "artifact", "read"]},
        ]:
            with self.subTest(extra=extra):
                self.assertEqual(self.request(**extra)[1].status, "requested")

    def test_new_tools_do_not_inherit_prior_toggle_and_ui_shows_partial(self):
        self.switch("write")
        self.manager.connectors.register(
            ConnectorTool(
                self.account.connector,
                "new_write",
                "write",
                "A new trusted tool",
                False,
                lambda request, cancelled: ConnectorOutcome(),
            )
        )
        self.assertEqual(
            self.service.account_access.state(self.account.connector, "write"), "some"
        )
        self.assertEqual(
            self.request("new_write", destination="new-target", actions=["app"])[
                1
            ].status,
            "requested",
        )
        dialog = AppConnectionsDialog(self.manager)
        self.dialogs.append(dialog)
        self.assertEqual(
            dialog.access_choices["write"].checkState(), Qt.CheckState.PartiallyChecked
        )
        self.switch("write")
        self.assertEqual(
            self.request("new_write", destination="new-target", actions=["app"])[
                1
            ].status,
            "approved",
        )

    def test_account_identity_and_restart_are_preserved_without_old_authority(self):
        self.switch("write")
        other = install_account(self.store)
        self.manager._register(other)
        self.assertEqual(self.request(account=other)[1].status, "requested")
        task, _ = self.request()
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(
            self.root / "tasks.sqlite", connectors=self.manager.connectors
        )
        self.manager.task_service = self.service
        self.assertEqual(self.service.store.get(task.task_id).status, "interrupted")
        self.assertEqual(self.request()[1].status, "approved")

    def test_user_switches_are_not_exposed_to_agents(self):
        from services.agent_tools import TOOLS
        from services.task_client import CLIENT_COMMANDS

        self.assertNotIn("app.set_access", CLIENT_COMMANDS)
        self.assertFalse(any("set_access" in tool.command for tool in TOOLS))
        dialog = AppConnectionsDialog(self.manager)
        self.dialogs.append(dialog)
        self.assertFalse(dialog.access_choices["send"].isChecked())
        dialog.access_choices["send"].click()
        self.assertTrue(
            self.service.account_access.enabled(self.account.connector, "send")
        )
        self.assertEqual(
            self.request("send_email", "send", "email:person@example.test")[1].status,
            "approved",
        )
        dialog.access_choices["send"].click()
        self.assertFalse(
            self.service.account_access.enabled(self.account.connector, "send")
        )

    def test_storage_failure_prevents_settings_change_and_freezes_authority(self):
        self.switch("write")
        with patch.object(
            self.service.account_access,
            "set",
            side_effect=sqlite3.OperationalError("synthetic disk failure"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.switch("write", False)
        self.assertTrue(self.service.failure)

    def test_auto_approval_journal_failure_leaves_no_live_grant(self):
        self.switch("write")
        original = self.service.store._append

        def fail_approval(task_id, kind, payload):
            if kind == "permission.account_access":
                raise sqlite3.OperationalError("synthetic journal failure")
            return original(task_id, kind, payload)

        with patch.object(self.service.store, "_append", side_effect=fail_approval):
            with self.assertRaises(sqlite3.Error):
                self.request()
        self.assertFalse(self.service._grants)
        self.assertTrue(self.service.failure)
