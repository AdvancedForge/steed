"""Reviewed, exact-once continuation into a new task or another external agent."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_html_agents as agents
import test_html_menus as menus
from PyQt6.QtWidgets import QApplication

from services.codex_provider import CodexProvider
from ui.conversation_recovery import recent_context


class ConversationRecoveryTests(unittest.TestCase):
    setUpClass = classmethod(agents.HtmlAgentTests.setUpClass.__func__)
    tearDownClass = classmethod(agents.HtmlAgentTests.tearDownClass.__func__)
    setUp = agents.HtmlAgentTests.setUp
    tearDown = agents.HtmlAgentTests.tearDown
    sidebar = agents.HtmlAgentTests.sidebar
    loaded = agents.HtmlAgentTests.loaded
    value = agents.HtmlAgentTests.value
    wait_for = agents.HtmlAgentTests.wait_for
    command = agents.HtmlAgentTests.command
    connect_task = agents.HtmlAgentTests.connect_task
    control = menus.HtmlMenuTests.control
    ready = menus.HtmlMenuTests.ready

    def click(self, host, widget):
        token = self.control(host, widget)
        self.wait_for(
            lambda: self.value(
                host, f"!document.querySelector('[data-control=\"{token}\"]').disabled"
            )
        )
        menus.HtmlMenuTests.click(self, host, widget)

    def prepare(self):
        window, sidebar = self.loaded()
        task = self.connect_task(sidebar, "Interrupted work")
        self.service.send_message(task, "PRIVATE_PREVIOUS_INSTRUCTION")
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="partial",
            text="PARTIAL_REPLY",
            state="streaming",
        )
        sidebar.set_draft("Verify previous outcomes and report what remains")
        self.service.cancel_task(task)
        return window, sidebar, task

    def open_recovery(self, sidebar):
        self.wait_for(lambda: self.value(sidebar, "state.usable"))
        self.value(sidebar, "document.getElementById('recover').click(); true")
        self.wait_for(lambda: sidebar._recovery_dialog is not None)
        dialog = sidebar._recovery_dialog
        host = dialog.presentation_host()
        self.ready(host)
        return dialog, host

    def runtime(self, mode="normal"):
        self.registry.agent_providers.factory = lambda parent: CodexProvider(
            parent,
            command=[
                sys.executable,
                str(Path(__file__).parent / "fixtures" / "fake_codex_server.py"),
                mode,
            ],
        )

    def payload(self, text):
        return json.loads(text.split("\n\n", 1)[1])

    def test_review_shares_nothing_and_copy_includes_history_only_when_checked(self):
        window, sidebar, task = self.prepare()
        self.service.send_message(
            self.connect_task(sidebar, "Other agent"), "Separate conversation"
        )
        sidebar.select_task(task)
        dialog, host = self.open_recovery(sidebar)
        self.assertNotIn("PRIVATE_PREVIOUS_INSTRUCTION", dialog.preview.toPlainText())
        self.assertFalse(dialog.include_history.isChecked())
        self.assertFalse(dialog.send.isEnabled())
        original = sidebar.draft
        before = len(self.service.messages.read(task)["messages"])
        self.click(host, dialog.include_history)
        self.wait_for(lambda: dialog.include_history.isChecked())
        expected = dialog.preview.toPlainText()
        self.assertIn("PARTIAL_REPLY", expected)
        self.assertIn("interrupted", expected)
        self.click(host, dialog.copy)
        self.wait_for(lambda: sidebar._recovery_dialog is None)
        self.assertTrue(host.surface.disposed)
        self.assertEqual(QApplication.clipboard().text(), expected)
        self.assertEqual(sidebar.draft, original)
        self.assertEqual(len(self.service.messages.read(task)["messages"]), before)

    def test_handoff_sends_only_reviewed_new_instruction_and_does_not_replay(self):
        _, sidebar, source = self.prepare()
        target = self.connect_task(sidebar, "Connected external agent")
        sidebar.select_task(source)
        dialog, host = self.open_recovery(sidebar)
        dialog.recipient.setCurrentIndex(dialog.recipient.findData(target))
        expected = dialog.preview.toPlainText()
        self.click(host, dialog.send)
        self.wait_for(lambda: sidebar._task_id == target)
        messages = self.service.messages.read(target)["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["text"], expected)
        self.assertEqual(self.payload(expected)["reviewed_context"], "")
        self.assertNotIn("PRIVATE_PREVIOUS_INSTRUCTION", expected)
        self.service.changed.emit()
        self.assertEqual(len(self.service.messages.read(target)["messages"]), 1)
        self.assertEqual(self.service.store.grants(target), [])
        sidebar.select_task(source)
        self.assertEqual(
            sidebar.draft, "Verify previous outcomes and report what remains"
        )
        self.assertEqual(len(self.service.messages.read(source)["messages"]), 2)

    def test_fresh_runtime_gets_new_task_no_old_grants_and_exactly_one_message(self):
        _, sidebar, source = self.prepare()
        self.runtime()
        dialog, host = self.open_recovery(sidebar)
        dialog.recipient.setCurrentIndex(dialog.recipient.findData("new_codex"))
        expected = dialog.preview.toPlainText()
        self.click(host, dialog.send)
        self.wait_for(lambda: sidebar._task_id != source)
        target = sidebar._task_id
        self.wait_for(
            lambda: any(
                m["role"] == "agent"
                for m in self.service.messages.read(target)["messages"]
            )
        )
        self.assertNotEqual(source, target)
        self.assertEqual(self.service.store.grants(target), [])
        user = [
            m
            for m in self.service.messages.read(target)["messages"]
            if m["role"] == "user"
        ]
        self.assertEqual([m["text"] for m in user], [expected])
        self.registry.agent_providers.changed.emit()
        self.service.changed.emit()
        self.assertEqual(
            len(
                [
                    m
                    for m in self.service.messages.read(target)["messages"]
                    if m["role"] == "user"
                ]
            ),
            1,
        )
        self.assertEqual(self.service.store.get(source).status, "cancelled")

    def test_cancelling_connecting_runtime_sends_nothing_and_cancels_only_new_task(
        self,
    ):
        _, sidebar, source = self.prepare()
        self.runtime("startup-hang")
        dialog, host = self.open_recovery(sidebar)
        dialog.recipient.setCurrentIndex(dialog.recipient.findData("new_codex"))
        self.click(host, dialog.send)
        self.wait_for(lambda: dialog._pending is not None)
        target = dialog._pending[0]
        self.assertEqual(self.service.messages.read(target)["messages"], [])
        dialog.reject()
        self.wait_for(lambda: target not in self.registry.agent_providers.chats)
        self.assertEqual(self.service.store.get(target).status, "cancelled")
        self.assertEqual(self.service.store.get(source).status, "cancelled")
        self.assertEqual(len(self.service.messages.read(source)["messages"]), 2)
        self.assertTrue(sidebar.draft)

    def test_failed_start_does_not_send_or_retry_and_explains_signin(self):
        _, sidebar, _ = self.prepare()
        self.runtime("signin")
        dialog, host = self.open_recovery(sidebar)
        dialog.recipient.setCurrentIndex(dialog.recipient.findData("new_codex"))
        self.click(host, dialog.send)
        self.wait_for(lambda: "codex login" in dialog.notice.text())
        self.assertEqual(len(self.service.store.list_tasks()), 2)
        self.assertEqual(self.registry.agent_providers.chats, {})
        self.assertTrue(sidebar.draft)
        dialog.reject()

    def test_selection_and_private_transition_close_and_scrub_review(self):
        window, sidebar, source = self.prepare()
        target = self.connect_task(sidebar, "Other")
        sidebar.select_task(source)
        dialog, host = self.open_recovery(sidebar)
        sidebar.select_task(target)
        self.assertTrue(host.surface.disposed)
        self.assertEqual(host.surface._state, {})
        sidebar.select_task(source)
        dialog, host = self.open_recovery(sidebar)
        stale = {
            "context": host.context,
            "sequence": host.sequence + 1,
            "action": "click",
            "id": self.control(host, dialog.copy),
        }
        window._switch_to_profile("search")
        self.assertTrue(host.surface.disposed)
        host.command(stale)
        self.assertEqual(host.surface._state, {})
        self.assertEqual(self.service.messages.read(target)["messages"], [])

    def test_oversized_preview_and_uncertain_delivery_never_implicitly_resend(self):
        _, sidebar, source = self.prepare()
        target = self.connect_task(sidebar, "Other")
        sidebar.select_task(source)
        dialog, host = self.open_recovery(sidebar)
        dialog.recipient.setCurrentIndex(dialog.recipient.findData(target))
        dialog.instructions.setPlainText("🙂" * 3000)
        self.assertFalse(dialog.send.isEnabled())
        dialog.instructions.setPlainText("Check earlier effects; report only")
        with patch.object(
            self.service, "send_message", side_effect=ValueError("PRIVATE_EXCEPTION")
        ) as send:
            self.click(host, dialog.send)
            self.wait_for(lambda: "could not be confirmed" in dialog.notice.text())
            dialog.send_message()
            self.assertEqual(send.call_count, 1)
        self.assertNotIn("PRIVATE_EXCEPTION", str(host.surface._state))
        self.assertEqual(self.service.messages.read(target)["messages"], [])
        self.assertTrue(sidebar.draft)
        dialog.reject()

    def test_recent_context_is_bounded_utf8_and_marks_omission(self):
        text = recent_context(
            [
                {
                    "role": "agent",
                    "state": "interrupted",
                    "text": "🙂" * 5000,
                    "created_sequence": 1,
                }
            ]
        )
        self.assertTrue(text.startswith("[Earlier conversation omitted"))
        self.assertLess(len(text.encode("utf-8")), 5100)

    def test_storage_failure_disables_recovery_without_escaping_qt_callback(self):
        _, sidebar, source = self.prepare()
        dialog, host = self.open_recovery(sidebar)
        with patch.object(
            self.service.store,
            "list_tasks",
            side_effect=sqlite3.OperationalError("PRIVATE_STORAGE_PATH"),
        ):
            dialog.refresh_targets()
        self.assertTrue(self.service.failure)
        self.assertTrue(host.surface.disposed)
        self.assertNotIn("PRIVATE_STORAGE_PATH", str(sidebar.state))
        self.assertTrue(sidebar._drafts[source])
