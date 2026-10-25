"""Actual HTML controls and synthetic chat documents, without external messages."""

import json
import unittest
from unittest.mock import patch

import test_html_menus as html
import test_web_chat as chats
from test_chatbot_protocol import call, response

from ui.chatbot_relay import ChatbotRelayDialog


class HtmlChatbotRelayTests(unittest.TestCase):
    setUpClass = classmethod(chats.WebChatTests.setUpClass.__func__)
    tearDownClass = classmethod(chats.WebChatTests.tearDownClass.__func__)
    setUp = chats.WebChatTests.setUp
    tearDown = chats.WebChatTests.tearDown
    window = chats.WebChatTests.window
    chat = chats.WebChatTests.chat
    wait_for = chats.WebChatTests.wait_for
    value = html.HtmlMenuTests.value
    ready = html.HtmlMenuTests.ready
    control = html.HtmlMenuTests.control

    def click(self, host, widget):
        self.wait_for(lambda: widget in host.objects.values())
        self.wait_for(
            lambda: self.value(
                host,
                "(()=>{const e=document.querySelector('[data-control=\"' + "
                + json.dumps(self.control(host, widget))
                + " + '\"]');return !!e&&!e.disabled})()",
            )
        )
        html.HtmlMenuTests.click(self, host, widget)

    def dialog(self, target=False):
        window = self.window()
        view = (
            self.chat(
                window,
                "<textarea></textarea><pre id='reply'></pre><script>window.sent=0;document.addEventListener('submit',()=>sent++)</script>",
            )
            if target
            else None
        )
        dialog = ChatbotRelayDialog(window, view)
        dialog.show()
        host = dialog.presentation_host()
        self.ready(host)
        dialog.goal.setPlainText("Read a synthetic page")
        if view is not None:
            dialog.consent.setChecked(True)
        self.click(host, dialog.start)
        self.wait_for(lambda: dialog.runner is not None)
        return window, view, dialog, host

    def submit(self, dialog, host, *calls):
        dialog.response.setPlainText(
            response(*calls, exchange=dialog.runner.exchange_id)
        )
        self.click(host, dialog.review)
        self.wait_for(lambda: dialog._proposal is not None)
        self.click(host, dialog.run)
        self.wait_for(lambda: dialog.runner.exchanges and not dialog.runner.busy)

    def test_manual_bidirectional_exchange_requires_separate_review(self):
        window, _, dialog, host = self.dialog()
        first = json.loads(dialog.outbound.toPlainText())
        self.assertEqual(first["goal"], "Read a synthetic page")
        self.assertTrue(first["available_tools"])
        self.assertFalse(dialog.paste.isEnabled())
        self.assertFalse(dialog.read.isEnabled())
        self.submit(dialog, host, call("steed_task_capabilities"))
        self.assertTrue(dialog.runner.results[0]["ok"])
        self.assertFalse(dialog.outbound.toPlainText())
        self.assertFalse(dialog.copy.isEnabled())
        self.click(host, dialog.prepare)
        self.wait_for(lambda: bool(dialog.outbound.toPlainText()))
        prepared = dialog.outbound.toPlainText()
        self.assertEqual(
            json.loads(prepared)["results_for_exchange"], first["exchange_id"]
        )
        self.click(host, dialog.copy)
        self.wait_for(lambda: "copied" in dialog.status.text())
        from PyQt6.QtWidgets import QApplication

        self.assertEqual(QApplication.clipboard().text(), prepared)
        self.assertEqual(dialog.outbound.toPlainText(), prepared)
        self.assertIsNot(
            host.surface.page.profile(), window.profile_manager.current_profile
        )

    def test_edit_after_review_invalidates_run_and_bad_batch_has_no_effect(self):
        _, _, dialog, host = self.dialog()
        dialog.response.setPlainText(
            response(call(), exchange=dialog.runner.exchange_id)
        )
        self.click(host, dialog.review)
        self.wait_for(lambda: dialog._proposal is not None)
        dialog.response.setPlainText("unstructured chat text")
        self.assertFalse(dialog.run.isEnabled())
        dialog.run_response()
        self.assertEqual(dialog.runner.exchanges, 0)
        self.click(host, dialog.review)
        self.wait_for(lambda: "JSON" in dialog.status.text())
        self.assertFalse(dialog._proposal)
        self.assertFalse(dialog.runner.used_ids)

    def test_tab_mode_reads_only_selected_text(self):
        _, view, dialog, host = self.dialog(True)
        payload = json.dumps(
            json.loads(response(call(), exchange=dialog.runner.exchange_id)), indent=2
        )
        chats.WebChatTests.value(
            self,
            view,
            "document.getElementById('reply').textContent="
            + json.dumps(payload)
            + "; document.body.appendChild(document.createTextNode('PRIVATE_UNSELECTED_HISTORY')); const r=document.createRange();r.selectNodeContents(document.getElementById('reply'));getSelection().removeAllRanges();getSelection().addRange(r);true",
        )
        self.wait_for(lambda: view.page().selectedText() == payload)
        self.click(host, dialog.read)
        self.wait_for(lambda: bool(dialog.response.toPlainText()))
        self.assertEqual(dialog.response.toPlainText(), payload)
        self.assertNotIn("PRIVATE_UNSELECTED_HISTORY", dialog.response.toPlainText())
        self.assertIsNone(view.page().webChannel())
        self.assertEqual(dialog.runner.exchanges, 0)

    def test_initial_conversation_can_be_explicitly_reenabled_then_navigation_stops(
        self,
    ):
        _, view, dialog, host = self.dialog(True)
        nonce = dialog.runner.exchange_id
        chats.WebChatTests.value(
            self, view, "history.pushState({},'', '/c/synthetic');true"
        )
        self.wait_for(lambda: dialog._paused)
        self.assertFalse(dialog.copy.isEnabled())
        self.click(host, dialog.rearm)
        self.wait_for(lambda: not dialog._paused)
        self.assertEqual(dialog.runner.exchange_id, nonce)
        self.assertTrue(dialog.destination.endswith("/c/synthetic"))
        self.submit(dialog, host, call())
        chats.WebChatTests.value(
            self, view, "history.pushState({},'', '/c/another');true"
        )
        self.wait_for(lambda: dialog.runner.stopped)
        self.assertFalse(dialog.outbound.toPlainText())
        self.assertFalse(dialog.results.toPlainText())

    def test_paste_is_exact_and_does_not_send(self):
        window, view, dialog, host = self.dialog(True)
        text = dialog.outbound.toPlainText()
        self.click(host, dialog.paste)
        self.wait_for(lambda: not dialog._pasting and "pasted" in dialog.status.text())
        self.wait_for(
            lambda: (
                chats.WebChatTests.value(
                    self, view, "document.querySelector('textarea').value"
                )
                == text
            )
        )
        self.assertEqual(chats.WebChatTests.value(self, view, "window.sent"), 0)
        self.assertEqual(dialog.runner.exchanges, 0)
        self.assertIsNot(host.surface.page, view.page())
        self.assertIs(window.profile_manager.current_profile, view.page().profile())

    def test_consent_private_profile_and_profile_change(self):
        window = self.window()
        view = self.chat(window)
        dialog = ChatbotRelayDialog(window, view)
        dialog.goal.setPlainText("Synthetic task")
        dialog.start_task()
        self.assertIsNone(dialog.runner)
        self.assertIn("enable", dialog.status.text())
        dialog.consent.setChecked(True)
        dialog.start_task()
        self.assertIsNotNone(dialog.runner)
        dialog.show()
        self.ready(dialog.presentation_host())
        window._switch_to_profile("search")
        self.assertTrue(dialog._closed)
        self.assertTrue(dialog.runner.stopped)
        self.assertEqual(dialog.runner.goal, "")
        self.assertEqual(dialog.goal.toPlainText(), "")
        self.assertEqual(dialog.outbound.toPlainText(), "")
        private = ChatbotRelayDialog(window)
        private.goal.setPlainText("Denied")
        private.start_task()
        self.assertIsNone(private.runner)
        private.reject()

    def test_stop_clears_sensitive_buffers_and_late_commands_cannot_replay(self):
        _, _, dialog, host = self.dialog()
        self.submit(dialog, host, call())
        self.assertTrue(dialog.results.toPlainText())
        self.click(host, dialog.stop)
        self.wait_for(lambda: dialog.runner.stopped)
        self.assertFalse(dialog.results.toPlainText())
        self.assertFalse(dialog.outbound.toPlainText())
        self.assertFalse(dialog.response.toPlainText())
        self.assertFalse(dialog.preview.toPlainText())
        dialog.run_response()
        self.assertEqual(dialog.runner.exchanges, 1)

    def test_sidebar_entry_and_single_dialog_lifetime(self):
        window = self.window()
        view = self.chat(window)
        window._dock_web_chat()
        dock = window.web_chat_dock
        self.ready(dock.presentation)
        with patch.object(window, "_open_chatbot_relay") as opened:
            self.click(dock.presentation, dock.relay)
            self.wait_for(lambda: opened.called)
            opened.assert_called_once_with(view)
        dialog = window._open_chatbot_relay(view)
        self.assertIs(window._open_chatbot_relay(), dialog)
        dialog.reject()
        self.assertIsNone(window.chatbot_relay_dialog)

    def test_ambiguous_composer_does_not_paste_or_send(self):
        _, view, dialog, host = self.dialog(True)
        chats.WebChatTests.value(
            self,
            view,
            "document.body.appendChild(document.createElement('textarea'));true",
        )
        self.click(host, dialog.paste)
        self.wait_for(
            lambda: (
                not dialog._pasting and "one visible composer" in dialog.status.text()
            )
        )
        self.assertEqual(
            chats.WebChatTests.value(
                self, view, "document.querySelector('textarea').value"
            ),
            "",
        )
        self.assertEqual(chats.WebChatTests.value(self, view, "window.sent"), 0)

    def test_pending_paste_cancelled_on_close(self):
        _, view, dialog, _ = self.dialog(True)
        dialog._pasting = True
        dialog.reject()
        dialog._focused(True)
        self.assertEqual(
            chats.WebChatTests.value(
                self, view, "document.querySelector('textarea').value"
            ),
            "",
        )
        self.assertTrue(dialog.runner.stopped)

    def test_unselected_tab_read_explains_the_next_step(self):
        _, _, dialog, host = self.dialog(True)
        self.click(host, dialog.read)
        self.wait_for(lambda: "Select only" in dialog.status.text())
        self.assertFalse(dialog.response.toPlainText())
        self.assertFalse(dialog.runner.used_ids)
