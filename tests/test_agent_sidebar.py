"""Sidebar interaction leaves page authority and task lifecycle explicit."""

import sqlite3
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        sidebar.select_task(task.task_id)
        return task.task_id

    def test_first_open_is_available_without_a_conversation_or_grants(self):
        _, sidebar = self.sidebar()
        self.assertTrue(sidebar.state["available"])
        self.assertFalse(sidebar.state["usable"])
        self.assertEqual(sidebar.state["messages"], [])
        self.assertEqual(sidebar.state["pending"], 0)
        self.assertTrue(sidebar.state["can_library"])

    def test_connected_chat_keeps_its_draft_local(self):
        _, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        self.assertTrue(sidebar.state["usable"])
        sidebar.set_draft("Draft stays local")
        self.assertEqual(sidebar.draft, "Draft stays local")
        self.assertEqual(self.service.store.grants(task), [])
        self.assertTrue(sidebar.state["active"])

    def test_pending_permissions_stay_visible_with_options_collapsed(self):
        _, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        self.command("grant.request", task_id=task, scope={"actions": ["artifact"]})
        self.assertEqual(sidebar.state["pending"], 1)
        sidebar.open_details("Permissions")
        dialog = sidebar.findChildren(TaskDialog)[-1]
        self.assertEqual(dialog.selection()[0].task_id, task)
        self.assertEqual(
            dialog.panels.tabText(dialog.panels.currentIndex()), "Permissions"
        )
        dialog.close()

    def test_pending_agent_questions_follow_conversation_selection(self):
        _, sidebar = self.sidebar()
        first = self.connect_task(sidebar, "Needs an answer")
        second = self.connect_task(sidebar, "No question")
        provider = SimpleNamespace(
            chats={first: {"requests": {"question": {}}}},
            can_send=lambda task: True,
            status=lambda task: "",
        )
        with patch.object(sidebar, "providers", provider):
            sidebar.refresh()
            self.assertEqual(sidebar.state["requests"], 0)
            sidebar.select_task(first)
            self.assertEqual(sidebar.state["requests"], 1)
            sidebar.select_task(second)
            self.assertEqual(sidebar.state["requests"], 0)

    def test_light_dark_sections_and_speaker_headers_preserve_plain_text(self):
        window, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        self.service.send_message(task, "Hello 🌍")
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="styled",
            text="<b>Literal reply</b>",
        )
        self.assertIn("<b>Literal reply</b>", sidebar.transcript_text)
        self.assertEqual(len(sidebar.state["messages"]), 2)
        self.assertIn("External agent connected", sidebar.state["activity"]["title"])
        self.assertFalse(sidebar.state["activity"]["busy"])
        self.assertFalse(sidebar.state["dark"])
        window.config.dark_mode = True
        window.apply_dark_mode()
        self.assertTrue(sidebar.state["dark"])
        self.assertEqual(len(sidebar.state["messages"]), 2)
        self.assertEqual(self.service.store.grants(task), [])
        sidebar.set_private(True)
        self.assertFalse(sidebar.state["usable"])
        self.assertEqual(sidebar.transcript_text, "")

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
        first.start_chat()
        task = next(iter(providers.chats))
        self.wait_for(lambda: providers.status(task) == "Codex ready")
        second.select_task(task)
        first.set_draft("First window")
        first.send_message()
        second.set_draft("Second window")
        second.send_message()
        self.wait_for(
            lambda: "Synthetic reply: Second window" in second.transcript_text
        )
        self.assertIn("Synthetic reply: Second window", first.transcript_text)
        self.assertEqual(self.service.store.grants(task), [])
        first_window.close()
        self.assertIn(task, providers.chats)
        self.assertTrue(second.state["active"])
        self.assertTrue(second_window.agent_access_action.isChecked())
        second_window.agent_access_action.trigger()
        self.assertIsNone(self.registry.agent_gateway)
        self.assertEqual(providers.chats, {})
        # Steed closed the chat (access off); the person did not cancel it.
        self.assertEqual(self.service.store.get(task).status, "interrupted")
        self.assertFalse(second.state["active"])

    def test_send_and_stream_are_plain_text_without_grants(self):
        _, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        sidebar.set_draft("Only explicitly shared text")
        sidebar.send_message()
        self.assertEqual(sidebar.draft, "")
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="reply",
            text="<script>literal</script>",
            state="streaming",
        )
        self.assertIn("<script>literal</script>", sidebar.transcript_text)
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="reply",
            text="<script>literal</script> finished",
            revision=1,
        )
        self.assertEqual(sidebar.transcript_text.count("Agent\n"), 1)
        self.assertNotIn("streaming", sidebar.transcript_text)
        self.assertEqual(self.service.store.grants(task), [])
        self.assertFalse(self.service._sessions)
        self.assertIn("waiting for the agent", sidebar.state["status"])
        self.command("conversation.read", task_id=task)
        self.assertIn("Agent fetched", sidebar.state["status"])

    def test_hide_show_keeps_task_and_draft_cancel_is_explicit(self):
        window, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        sidebar.set_draft("Unsent draft")
        window.agent_sidebar_action.trigger()
        self.assertFalse(sidebar.isVisible())
        self.assertIn(self.service.store.get(task).status, ("created", "running"))
        window.agent_sidebar_action.trigger()
        self.assertEqual(sidebar.draft, "Unsent draft")
        sidebar.cancel_task()
        self.assertEqual(self.service.store.get(task).status, "cancelled")
        self.assertFalse(sidebar.state["active"])

    def test_task_switch_retains_drafts_without_sharing_them(self):
        _, sidebar = self.sidebar()
        first = self.connect_task(sidebar, "First")
        sidebar.set_draft("Draft for first")
        second = self.connect_task(sidebar, "Second")
        self.assertEqual(sidebar.draft, "")
        sidebar.set_draft("Message for second")
        sidebar.send_message()
        sidebar.select_task(first)
        self.assertEqual(sidebar.draft, "Draft for first")
        self.assertNotIn("Message for second", sidebar.transcript_text)
        self.assertEqual(self.service.messages.read(first)["messages"], [])
        self.assertEqual(
            self.service.messages.read(second)["messages"][0]["text"],
            "Message for second",
        )

    def test_incognito_erases_visible_content_and_disables_conversation(self):
        window, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        self.service.send_message(task, "Regular profile text")
        sidebar.set_draft("Unsent regular draft")
        window._switch_to_profile("search")
        self.assertEqual(sidebar.transcript_text, "")
        self.assertEqual(sidebar.draft, "")
        self.assertFalse(sidebar.state["active"])
        self.assertEqual(sidebar.state["tasks"], [])
        window._switch_to_profile("default")
        self.assertEqual(sidebar.transcript_text, "")
        self.assertEqual(
            self.service.messages.read(task)["messages"][0]["text"],
            "Regular profile text",
        )

    def test_two_windows_share_only_sent_messages_and_profile_grant_stays_pinned(self):
        first_window, first = self.sidebar()
        _, second = self.sidebar()
        task = self.connect_task(first)
        second.select_task(task)
        grant = self.command(
            "grant.request",
            task_id=task,
            scope={"profile": "existing:work", "actions": ["artifact"]},
        )
        self.service.approve_grant(task, grant.grant_id)
        first_window._switch_to_profile("default")
        self.assertIn("existing:work", first.state["status"])
        self.assertIn("0 pending", first.state["summary"])
        first.set_draft("Shared conversation")
        first.send_message()
        self.assertIn("Shared conversation", second.transcript_text)
        first.set_draft("Unsent private draft")
        self.assertEqual(second.draft, "")
        first.clear_messages()
        self.assertEqual(first.transcript_text, "")
        self.assertEqual(second.transcript_text, "")

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
        sidebar.set_draft("é" * 4097)
        sidebar.send_message()
        self.assertTrue(sidebar.state["error"])
        self.assertEqual(sidebar.draft, "é" * 4097)
        self.service.external_connection(task, False)
        self.assertFalse(sidebar.state["active"])
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
            sidebar.select_task(task.task_id)
        self.assertFalse(sidebar.state["active"])
        self.assertFalse(sidebar.state["usable"])
        self.assertEqual(sidebar.transcript_text, "")
        self.assertTrue(sidebar.state["error"])
        self.assertTrue(window.tabs)

    def test_recovering_storage_read_restores_already_seen_messages_and_draft(self):
        _, sidebar = self.sidebar()
        task = self.connect_task(sidebar)
        self.service.send_message(task, "Already seen message")
        sidebar.set_draft("Unsent text survives")
        with patch.object(
            self.service.messages,
            "read",
            side_effect=sqlite3.OperationalError("fixture"),
        ):
            sidebar.refresh()
        self.assertFalse(sidebar.state["usable"])
        self.assertEqual(sidebar.transcript_text, "")
        sidebar.refresh()
        self.assertTrue(sidebar.state["usable"])
        self.assertIn("Already seen message", sidebar.transcript_text)
        self.assertEqual(sidebar.draft, "Unsent text survives")
        self.assertEqual(sidebar.state["error"], "")
