"""Sidebar interaction leaves page authority and task lifecycle explicit."""

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_task_profiles as fixtures
from PyQt6.QtCore import Qt

from browser_window import BrowserWindow
from services.codex_provider import CodexProvider
from ui.task_dialog import TaskDialog


class AgentSidebarTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = fixtures.TaskProfileTests.setUp
    tearDown = fixtures.TaskProfileTests.tearDown
    wait_for = fixtures.TaskProfileTests.wait_for
    command = fixtures.TaskProfileTests.command

    def sidebar(self):
        window = BrowserWindow()
        self.windows.append(window)
        window.show()
        self.assertIsNone(window.agent_sidebar)
        window.agent_sidebar_action.trigger()
        self.app.processEvents()
        return window, window.agent_sidebar

    def connect_task(self, sidebar, objective="Synthetic external agent"):
        task = self.command("task.create", objective=objective)
        self.service.external_connection(task.task_id, True)
        sidebar.tasks.setCurrentIndex(sidebar.tasks.findData(task.task_id))
        return task.task_id

    def test_managed_codex_chat_is_shared_and_access_toggle_stops_it(self):
        first_window, first = self.sidebar()
        second_window, second = self.sidebar()
        providers = self.registry.agent_providers
        self.assertEqual(providers.chats, {})
        self.assertIsNone(self.registry.agent_gateway)
        providers.factory = lambda parent: CodexProvider(
            parent,
            command=[
                sys.executable,
                str(Path(__file__).parent / "fixtures" / "fake_codex_server.py"),
                "normal",
            ],
        )
        first.new_chat.click()
        task = next(iter(providers.chats))
        self.wait_for(lambda: providers.status(task) == "Codex ready")
        second.tasks.setCurrentIndex(second.tasks.findData(task))
        first.composer.setPlainText("First window")
        first.send.click()
        second.composer.setPlainText("Second window")
        second.send.click()
        self.wait_for(
            lambda: "Synthetic reply: Second window" in second.transcript.toPlainText()
        )
        self.assertIn("Synthetic reply: Second window", first.transcript.toPlainText())
        self.assertEqual(self.service.store.grants(task), [])
        first_window.close()
        self.assertIn(task, providers.chats)
        self.assertTrue(second.send.isEnabled())
        self.assertTrue(second_window.agent_access_action.isChecked())
        second_window.agent_access_action.trigger()
        self.assertIsNone(self.registry.agent_gateway)
        self.assertEqual(providers.chats, {})
        self.assertEqual(self.service.store.get(task).status, "cancelled")
        self.assertFalse(second.send.isEnabled())

    def test_send_and_stream_are_plain_text_without_grants(self):
        _, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        sidebar.composer.setPlainText("Only explicitly shared text")
        sidebar.send.click()
        self.assertEqual(sidebar.composer.toPlainText(), "")
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="reply",
            text="<script>literal</script>",
            state="streaming",
        )
        self.assertIn("<script>literal</script>", sidebar.transcript.toPlainText())
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="reply",
            text="<script>literal</script> finished",
            revision=1,
        )
        self.assertEqual(sidebar.transcript.toPlainText().count("Agent\n"), 1)
        self.assertNotIn("streaming", sidebar.transcript.toPlainText())
        self.assertEqual(self.service.store.grants(task), [])
        self.assertFalse(self.service._sessions)
        self.assertIn("waiting for the agent", sidebar.status.text())
        self.command("conversation.read", task_id=task)
        self.assertIn("Agent fetched", sidebar.status.text())

    def test_hide_show_keeps_task_and_draft_cancel_is_explicit(self):
        window, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        sidebar.composer.setPlainText("Unsent draft")
        window.agent_sidebar_action.trigger()
        self.assertFalse(sidebar.isVisible())
        self.assertIn(self.service.store.get(task).status, ("created", "running"))
        window.agent_sidebar_action.trigger()
        self.assertEqual(sidebar.composer.toPlainText(), "Unsent draft")
        sidebar.cancel.click()
        self.assertEqual(self.service.store.get(task).status, "cancelled")
        self.assertFalse(sidebar.send.isEnabled())

    def test_task_switch_retains_drafts_without_sharing_them(self):
        _, sidebar = self.sidebar()
        first = self.connect_task(sidebar, "First")
        sidebar.composer.setPlainText("Draft for first")
        second = self.connect_task(sidebar, "Second")
        self.assertEqual(sidebar.composer.toPlainText(), "")
        sidebar.composer.setPlainText("Message for second")
        sidebar.send.click()
        sidebar.tasks.setCurrentIndex(sidebar.tasks.findData(first))
        self.assertEqual(sidebar.composer.toPlainText(), "Draft for first")
        self.assertNotIn("Message for second", sidebar.transcript.toPlainText())
        self.assertEqual(self.service.messages.read(first)["messages"], [])
        self.assertEqual(
            self.service.messages.read(second)["messages"][0]["text"],
            "Message for second",
        )

    def test_incognito_erases_visible_content_and_disables_conversation(self):
        window, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        self.service.send_message(task, "Regular profile text")
        sidebar.composer.setPlainText("Unsent regular draft")
        window._switch_to_profile("search")
        self.assertEqual(sidebar.transcript.toPlainText(), "")
        self.assertEqual(sidebar.composer.toPlainText(), "")
        self.assertFalse(sidebar.send.isEnabled())
        self.assertEqual(sidebar.tasks.count(), 1)
        window._switch_to_profile("default")
        self.assertEqual(sidebar.transcript.toPlainText(), "")
        self.assertEqual(
            self.service.messages.read(task)["messages"][0]["text"],
            "Regular profile text",
        )

    def test_two_windows_share_only_sent_messages_and_profile_grant_stays_pinned(self):
        first_window, first = self.sidebar()
        _, second = self.sidebar()
        task = self.connect_task(first)
        second.tasks.setCurrentIndex(second.tasks.findData(task))
        grant = self.command(
            "grant.request",
            task_id=task,
            scope={"profile": "existing:work", "actions": ["artifact"]},
        )
        self.service.approve_grant(task, grant.grant_id)
        first_window._switch_to_profile("default")
        self.assertIn("existing:work", first.status.text())
        self.assertIn("0 pending", first.summary.text())
        first.composer.setPlainText("Shared conversation")
        first.send.click()
        self.assertIn("Shared conversation", second.transcript.toPlainText())
        first.composer.setPlainText("Unsent private draft")
        self.assertEqual(second.composer.toPlainText(), "")
        first.clear.click()
        self.assertEqual(first.transcript.toPlainText(), "")
        self.assertEqual(second.transcript.toPlainText(), "")

    def test_permissions_files_and_receipts_select_the_same_task_nonmodally(self):
        _, sidebar = self.sidebar()
        self.connect_task(sidebar, "First")
        task = self.connect_task(sidebar, "Second")
        for panel in ("Permissions", "Workspace", "Apps"):
            sidebar.open_details(panel)
            dialog = sidebar.findChildren(TaskDialog)[-1]
            self.assertEqual(dialog.selection()[0].task_id, task)
            self.assertEqual(dialog.panels.tabText(dialog.panels.currentIndex()), panel)
            self.assertEqual(dialog.windowModality(), Qt.WindowModality.NonModal)
            dialog.close()
            self.app.processEvents()

    def test_disconnected_or_oversized_send_preserves_composer(self):
        _, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        sidebar.composer.setPlainText("é" * 4097)
        sidebar.send.click()
        self.assertTrue(sidebar.error.text())
        self.assertEqual(sidebar.composer.toPlainText(), "é" * 4097)
        self.service.external_connection(task, False)
        self.assertFalse(sidebar.send.isEnabled())
        self.assertEqual(self.service.messages.read(task)["messages"], [])

    def test_sidebar_startup_failure_leaves_browsing_available(self):
        window = BrowserWindow()
        self.windows.append(window)
        with (
            patch("ui.agent_sidebar.AgentSidebar", side_effect=RuntimeError("fixture")),
            patch("browser_window.QMessageBox.warning"),
        ):
            window.agent_sidebar_action.trigger()
        self.assertIsNone(window.agent_sidebar)
        self.assertFalse(window.agent_sidebar_action.isChecked())
        self.assertTrue(window.tabs)

    def test_selection_storage_failure_disables_sidebar_without_closing_page(self):
        window, sidebar = self.sidebar()
        self.connect_task(sidebar, "First")
        task = self.command("task.create", objective="Second")
        self.service.external_connection(task.task_id, True)
        with patch.object(
            self.service.messages,
            "read",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            sidebar.tasks.setCurrentIndex(sidebar.tasks.findData(task.task_id))
        self.assertFalse(sidebar.send.isEnabled())
        self.assertFalse(sidebar.clear.isEnabled())
        self.assertEqual(sidebar.transcript.toPlainText(), "")
        self.assertTrue(sidebar.error.text())
        self.assertTrue(window.tabs)
