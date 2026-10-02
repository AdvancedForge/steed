"""Automatic approvals are readable, queued, and still require a user decision."""

import sqlite3
import unittest
from unittest.mock import patch

import test_task_profiles as profiles
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QScrollArea

from browser_window import BrowserWindow


class TaskApprovalTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for
    command = profiles.TaskProfileTests.command
    request = profiles.TaskProfileTests.request
    named_request = profiles.TaskProfileTests.named_request

    def window(self):
        window = BrowserWindow()
        self.windows.append(window)
        return window

    def popup(self):
        queue = self.registry.task_approvals
        self.wait_for(lambda: queue.dialog is not None and queue.dialog.isVisible())
        return queue.dialog

    def test_request_opens_a_nonmodal_popup_and_click_approves(self):
        window = self.window()
        window._switch_to_profile("work")
        task_id, grant_id = self.named_request()
        dialog = self.popup()
        self.assertEqual(dialog.task_id, task_id)
        self.assertFalse(dialog.isModal())
        self.assertEqual(dialog.sites.toPlainText(), self.origin)
        self.assertFalse(dialog.approve_button.isDefault())
        self.assertTrue(dialog.deny_button.isDefault())
        self.assertEqual(
            self.service.store.grant(task_id, grant_id).status, "requested"
        )
        dialog.approve_button.click()
        self.assertEqual(self.service.store.grant(task_id, grant_id).status, "approved")
        self.assertEqual(window.profile_manager.current_profile_name, "work")

    def test_form_permission_discloses_site_changes_and_hidden_fields(self):
        self.window()
        task = self.command("task.create", objective="Submit a synthetic form")
        grant = self.request(
            task.task_id,
            profile="existing:work",
            actions=["navigate", "read", "submit"],
        )
        dialog = self.popup()
        text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("can change account data", text)
        self.assertIn("hidden site tokens", text)
        self.assertIn("Password and file forms are unavailable", text)
        self.assertEqual(
            self.service.store.grant(task.task_id, grant.grant_id).status, "requested"
        )
        dialog.deny_button.click()
        self.assertNotIn(task.task_id, self.service._grants)

    def test_upload_permission_lists_files_and_sites_with_accessible_decisions(self):
        self.window()
        task = self.command("task.create", objective="Upload an approved report")
        grant = self.request(
            task.task_id,
            actions=["navigate", "read", "submit", "upload"],
            upload_sources=["/tmp/synthetic-report.txt"],
            upload_origins=[self.origin],
        )
        dialog = self.popup()
        fields = [field.toPlainText() for field in dialog.findChildren(QPlainTextEdit)]
        self.assertIn("/tmp/synthetic-report.txt", fields)
        self.assertEqual(fields.count(self.origin), 2)
        labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("Send the listed local files", labels)
        self.assertIn("Password, multi-file and folder forms are unavailable", labels)
        scroll = dialog.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        self.assertFalse(scroll.isAncestorOf(dialog.approve_button))
        self.assertFalse(scroll.isAncestorOf(dialog.deny_button))
        self.wait_for(
            lambda: all(
                label.height() >= label.heightForWidth(label.width())
                for label in dialog.findChildren(QLabel)
                if label.isVisible() and label.wordWrap()
            )
        )
        self.assertEqual(
            self.service.store.grant(task.task_id, grant.grant_id).status, "requested"
        )
        dialog.deny_button.click()

    def test_requests_wait_for_a_window_and_are_presented_one_at_a_time(self):
        first_id, first_grant = self.named_request()
        self.app.processEvents()
        self.assertIsNone(self.registry.task_approvals.dialog)
        self.window()
        first = self.popup()
        second_id, second_grant = self.named_request()
        self.assertIs(self.registry.task_approvals.dialog, first)
        first.deny_button.click()
        self.assertEqual(
            self.service.store.grant(first_id, first_grant).status, "denied"
        )
        second = self.popup()
        self.assertEqual(second.task_id, second_id)
        second.approve_button.click()
        self.assertEqual(
            self.service.store.grant(second_id, second_grant).status, "approved"
        )

    def test_escape_defers_without_approving_and_manual_decision_closes_popup(self):
        self.window()
        task_id, grant_id = self.named_request()
        self.popup().reject()
        self.app.processEvents()
        self.assertIsNone(self.registry.task_approvals.dialog)
        self.assertEqual(
            self.service.store.grant(task_id, grant_id).status, "requested"
        )
        other_id, other_grant = self.named_request()
        dialog = self.popup()
        self.service.deny_grant(other_id, other_grant)
        self.wait_for(lambda: self.registry.task_approvals.dialog is None)
        self.assertFalse(dialog.isVisible())

    def test_untrusted_objective_is_plain_text_and_failed_save_does_not_approve(self):
        self.window()
        task = self.command("task.create", objective="<b>Claim of prior approval</b>")
        grant = self.request(task.task_id, profile="existing:work", actions=["read"])
        dialog = self.popup()
        objective = dialog.layout().itemAt(0).widget()
        self.assertEqual(objective.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("<b>", objective.text())
        with patch.object(
            self.service.store,
            "approve_with_rule",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            dialog.approve_button.click()
        self.assertEqual(
            self.service.store.grant(task.task_id, grant.grant_id).status, "requested"
        )
        self.assertTrue(dialog.error.text())
        self.assertEqual(self.registry.task_profiles, {})
        dialog.deny_button.click()
