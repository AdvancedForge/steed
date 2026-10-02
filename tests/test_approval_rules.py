"""Reusable approvals stay bounded, profile-isolated, removable and journaled."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application

from services.approval_rules import ApprovalRule
from services.connectors import ConnectorOutcome, ConnectorRegistry, ConnectorTool
from services.task_policy import TaskPermissionError, TaskScope
from services.task_service import TaskService
from ui.task_approval import TaskApprovalDialog
from ui.task_dialog import TaskDialog


class Profiles:
    def __init__(self):
        self.keys = {"work": "a" * 32, "personal": "b" * 32}
        self.reserved = {}

    def validate_task_profile(self, selector):
        name = selector.removeprefix("existing:")
        if name not in self.keys:
            raise ValueError("Unknown profile")
        return name

    def approval_profile_key(self, selector, *, create=False):
        return self.keys[self.validate_task_profile(selector)]

    def reserve_task_profile(self, task_id, selector):
        self.validate_task_profile(selector)
        self.reserved[task_id] = selector

    def task_profile(self, task_id, selector):
        raise AssertionError("No engine pages in this fixture")

    def release_task_profiles(self, task_id=None):
        if task_id is None:
            self.reserved.clear()
        else:
            self.reserved.pop(task_id, None)


class ApprovalRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.profiles = Profiles()
        self.registry = ConnectorRegistry()
        self.registry.register(
            ConnectorTool(
                "fixture",
                "send",
                "send",
                "Synthetic email",
                True,
                lambda request, cancel: ConnectorOutcome(),
            )
        )
        self.service = TaskService(
            self.path, profiles=self.profiles, connectors=self.registry
        )
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.close()
                sip.delete(dialog)
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def request(self, **changes):
        task = self.service._create("Synthetic permission request")
        scope = {
            "origins": ["https://93.184.215.14"],
            "actions": ["navigate", "inspect", "read"],
            **changes,
        }
        return task, self.service._request_grant(task.task_id, scope)

    def remember(self, mode="global", **changes):
        task, grant = self.request(**changes)
        self.service.approve_grant(task.task_id, grant.grant_id, remember=mode)
        return task, grant, self.service.store.approval_rules()[-1]

    def test_allow_once_never_creates_a_rule(self):
        task, grant = self.request()
        self.service.approve_grant(task.task_id, grant.grant_id)
        self.assertFalse(self.service.store.approval_rules())
        self.assertEqual(self.request()[1].status, "requested")

    def test_global_reuses_same_or_narrower_scope_and_journals_source(self):
        _, _, rule = self.remember()
        task, grant = self.request(
            actions=["inspect"], max_operations=50, duration_seconds=60
        )
        self.assertEqual(grant.status, "approved")
        self.assertEqual(self.service.store.grant_rule_id(grant.grant_id), rule.rule_id)
        self.assertIn(
            "saved_approval:" + rule.rule_id,
            str(self.service.store.events(task.task_id)),
        )
        self.assertEqual(
            self.request(profile="existing:personal")[1].status, "approved"
        )

    def test_profile_rule_never_applies_to_another_profile_or_private(self):
        self.remember("profile", profile="existing:work")
        self.assertEqual(self.request(profile="existing:work")[1].status, "approved")
        self.assertEqual(
            self.request(profile="existing:personal")[1].status, "requested"
        )
        self.assertEqual(self.request()[1].status, "requested")

    def test_profile_identity_survives_rename_but_not_recreation(self):
        self.remember("profile", profile="existing:work")
        self.profiles.keys["daily"] = self.profiles.keys.pop("work")
        self.assertEqual(self.request(profile="existing:daily")[1].status, "approved")
        self.profiles.keys["work"] = "c" * 32
        self.assertEqual(self.request(profile="existing:work")[1].status, "requested")

    def test_expanded_sites_actions_or_budgets_require_another_prompt(self):
        self.remember()
        for changes in (
            {"origins": ["https://1.1.1.1"]},
            {"actions": ["navigate", "inspect", "read", "edit"]},
            {"max_operations": 101},
            {"max_requests": 1001},
            {"max_sessions": 2},
            {"duration_seconds": 601},
            {"allow_private_network": True},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.request(**changes)[1].status, "requested")

    def test_app_account_tool_effect_and_recipient_remain_exact(self):
        permission = {
            "connector": "fixture",
            "tool": "send",
            "effect": "send",
            "destination": "recipient@example.test",
        }
        self.remember(actions=["artifact", "app"], connector_permissions=[permission])
        self.assertEqual(
            self.request(
                actions=["artifact", "app"], connector_permissions=[permission]
            )[1].status,
            "approved",
        )
        self.assertEqual(
            self.request(
                actions=["artifact", "app"],
                connector_permissions=[
                    {**permission, "destination": "other@example.test"}
                ],
            )[1].status,
            "requested",
        )
        self.registry.register(
            ConnectorTool(
                "other",
                "send",
                "send",
                "Other account",
                True,
                lambda request, cancel: ConnectorOutcome(),
            )
        )
        self.assertEqual(
            self.request(
                actions=["artifact", "app"],
                connector_permissions=[{**permission, "connector": "other"}],
            )[1].status,
            "requested",
        )
        self.service.disconnect_connector("fixture")
        self.assertFalse(self.service.store.approval_rules())

    def test_file_sources_destinations_and_sizes_are_not_broadened(self):
        scope = TaskScope.parse(
            {
                "actions": ["navigate", "upload", "download"],
                "origins": ["https://93.184.215.14"],
                "upload_origins": ["https://93.184.215.14"],
                "upload_sources": ["/tmp/source.txt"],
                "download_destinations": ["/tmp/destination.txt"],
            }
        )
        rule = ApprovalRule("fixture", "global", "", scope.payload(), "fixture-time")
        self.assertTrue(rule.covers(scope))
        for field, value in (
            ("upload_sources", ["/tmp/other.txt"]),
            ("download_destinations", ["/tmp/other.txt"]),
            ("max_upload_bytes", 6 * 1024 * 1024),
            ("max_download_bytes", 26 * 1024 * 1024),
        ):
            with self.subTest(field=field):
                self.assertFalse(
                    rule.covers(TaskScope.parse({**scope.payload(), field: value}))
                )

    def test_separate_rules_are_not_unioned_to_authorize_a_new_combination(self):
        self.remember(actions=["read"])
        self.remember(actions=["edit"])
        self.assertEqual(self.request(actions=["read", "edit"])[1].status, "requested")

    def test_restart_retains_rules_but_never_restores_task_grants(self):
        task, _, _ = self.remember()
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(
            self.path, profiles=self.profiles, connectors=self.registry
        )
        self.assertFalse(self.service._grants)
        self.assertEqual(self.service.store.get(task.task_id).status, "interrupted")
        self.assertEqual(self.request()[1].status, "approved")

    def test_forget_revokes_all_linked_tasks_and_new_requests_prompt(self):
        original, _, rule = self.remember()
        reused, _ = self.request()
        unrelated, grant = self.request(origins=["https://1.1.1.1"])
        self.service.approve_grant(unrelated.task_id, grant.grant_id)
        self.service.forget_approval_rule(rule.rule_id)
        self.assertEqual(self.service.store.get(original.task_id).status, "cancelled")
        self.assertEqual(self.service.store.get(reused.task_id).status, "cancelled")
        self.assertIn(unrelated.task_id, self.service._grants)
        self.assertEqual(self.request()[1].status, "requested")

    def test_failed_rule_journal_cannot_persist_either_rule_or_grant(self):
        task, grant = self.request()
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.service.approve_grant(
                    task.task_id, grant.grant_id, remember="global"
                )
        self.assertFalse(self.service.store.approval_rules())
        self.assertEqual(
            self.service.store.grant(task.task_id, grant.grant_id).status, "requested"
        )
        self.assertFalse(self.service._grants)

    def test_failed_forget_journal_freezes_authority_and_rolls_back_removal(self):
        _, _, rule = self.remember()
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.service.forget_approval_rule(rule.rule_id)
        self.assertTrue(self.service.failure)
        self.assertEqual(len(self.service.store.approval_rules()), 1)
        self.assertTrue(all(live.reason() for _, live in self.service._grants.values()))

    def test_private_profile_cannot_create_profile_specific_rule(self):
        task, grant = self.request()
        with self.assertRaises(ValueError):
            self.service.approve_grant(task.task_id, grant.grant_id, remember="profile")
        self.assertFalse(self.service.store.approval_rules())

    def test_agent_surface_cannot_create_delete_or_change_remembered_choices(self):
        self.assertNotIn(
            "approve_grant",
            self.service.broker._handlers
            if hasattr(self.service.broker, "_handlers")
            else {},
        )
        with self.assertRaises(ValueError):
            self.request(remember="global")
        self.assertFalse(self.service.store.approval_rules())

    def test_malformed_saved_scope_freezes_authority_without_autoapproval(self):
        task, _, _ = self.remember()
        live = self.service._grants[task.task_id][1]
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE approval_rules SET scope = ?",
                ('{"actions":["new_authority"]}',),
            )
        with self.assertRaises(ValueError):
            self.request()
        self.assertTrue(self.service.failure)
        with self.assertRaises(TaskPermissionError):
            live.check("read")

    def test_popup_default_and_saved_approval_management(self):
        task, grant = self.request(profile="existing:work")
        popup = TaskApprovalDialog(self.service, task, grant)
        self.dialogs.append(popup)
        self.assertEqual(popup.remember.currentData(), "once")
        popup.remember.setCurrentIndex(2)
        self.assertIn("this named profile only", popup.remember_notice.text())
        popup.decide(True)
        self.assertEqual(self.service.store.approval_rules()[0].mode, "profile")
        panel = TaskDialog(self.service)
        self.dialogs.append(panel)
        self.assertEqual(panel.saved_rules.rowCount(), 1)
        panel.saved_rules.selectRow(0)
        self.assertIn("Sites: https://93.184.215.14", panel.saved_details.toPlainText())
        self.assertIn("Read page text", panel.saved_details.toPlainText())
        self.assertTrue(panel.forget_rule_button.isEnabled())
        panel.forget_rule()
        self.assertEqual(panel.saved_rules.rowCount(), 0)

    def test_private_popup_disables_profile_choice_but_allows_global(self):
        task, grant = self.request()
        popup = TaskApprovalDialog(self.service, task, grant)
        self.dialogs.append(popup)
        self.assertFalse(popup.remember.model().item(2).isEnabled())
        popup.remember.setCurrentIndex(1)
        self.assertIn("all profiles, including private", popup.remember_notice.text())
