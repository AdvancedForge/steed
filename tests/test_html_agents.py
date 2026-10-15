"""Real HTML/bridge acceptance with synthetic conversations and local pages."""

import json
import unittest
from unittest.mock import patch

import test_agent_sidebar as fixtures
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtTest import QTest


class HtmlAgentTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AgentSidebarTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.AgentSidebarTests.tearDownClass.__func__)
    setUp = fixtures.AgentSidebarTests.setUp
    tearDown = fixtures.AgentSidebarTests.tearDown
    wait_for = fixtures.AgentSidebarTests.wait_for
    command = fixtures.AgentSidebarTests.command
    sidebar = fixtures.AgentSidebarTests.sidebar
    connect_task = fixtures.AgentSidebarTests.connect_task

    def value(self, sidebar, script):
        results = []
        sidebar.surface.page.runJavaScript(script, results.append)
        self.wait_for(lambda: bool(results))
        return results[0]

    def loaded(self):
        window, sidebar = self.sidebar()
        self.wait_for(lambda: sidebar.surface.ready)
        self.wait_for(lambda: self.value(sidebar, "!!state"))
        return window, sidebar

    def input(self, sidebar, text):
        self.value(
            sidebar,
            f"composer.value = {json.dumps(text)}; composer.dispatchEvent(new Event('input')); true",
        )
        self.wait_for(lambda: sidebar.draft == text)

    def click(self, sidebar, identifier):
        self.value(
            sidebar, f"document.getElementById({json.dumps(identifier)}).click(); true"
        )

    def test_onboarding_real_bridge_and_collapsed_settings(self):
        _, sidebar = self.loaded()
        self.assertTrue(
            self.value(sidebar, "!document.getElementById('welcome').hidden")
        )
        self.assertTrue(
            self.value(sidebar, "document.getElementById('conversation').hidden")
        )
        self.assertFalse(self.value(sidebar, "document.getElementById('options').open"))
        with patch.object(sidebar, "start_chat") as start:
            self.click(sidebar, "welcome-start")
            self.wait_for(lambda: start.called)
            self.assertEqual(start.call_count, 1)
        with patch.object(sidebar.browser_window, "_open_file_library") as library:
            self.value(sidebar, "document.getElementById('options').open = true")
            self.click(sidebar, "library")
            self.wait_for(lambda: library.called)

    def test_start_failure_keeps_existing_connection_guidance_visible(self):
        _, sidebar = self.loaded()
        with patch.object(
            sidebar.providers,
            "new_chat",
            side_effect=RuntimeError("Install the optional MCP dependencies first."),
        ):
            self.click(sidebar, "welcome-start")
            self.wait_for(
                lambda: self.value(
                    sidebar,
                    "document.getElementById('error').textContent.includes('MCP dependencies')",
                )
            )
        self.assertFalse(sidebar.state["usable"])
        self.assertEqual(self.service.store.list_tasks(), [])

    def test_real_bridge_send_shortcut_stream_literal_text_and_theme(self):
        window, sidebar = self.loaded()
        task = self.connect_task(sidebar)
        self.wait_for(lambda: self.value(sidebar, "state.active"))
        self.input(sidebar, "Hello from HTML")
        self.click(sidebar, "send")
        self.wait_for(lambda: sidebar.draft == "")
        self.wait_for(lambda: self.value(sidebar, "composer.value === ''"))
        payload = '<img src="https://example.invalid/leak" onerror="window.injected=true"><script>window.injected=true</script>'
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="literal",
            text=payload,
            state="streaming",
        )
        self.wait_for(
            lambda: self.value(
                sidebar, "document.querySelectorAll('.message').length === 2"
            )
        )
        self.assertEqual(
            self.value(
                sidebar,
                "document.querySelector('.message.agent .message-body').textContent",
            ),
            payload,
        )
        self.assertEqual(
            self.value(
                sidebar,
                "document.querySelectorAll('#messages img, #messages script').length",
            ),
            0,
        )
        self.assertFalse(self.value(sidebar, "!!window.injected"))
        self.command(
            "conversation.reply",
            task_id=task,
            message_id="literal",
            text=payload + " Finished",
            revision=1,
        )
        self.wait_for(
            lambda: self.value(
                sidebar,
                "document.querySelector('.message.agent .message-body').textContent.endsWith(' Finished')",
            )
        )
        self.assertEqual(
            self.value(sidebar, "document.querySelectorAll('.message.agent').length"), 1
        )
        self.input(sidebar, "Keyboard follow-up")
        self.value(sidebar, "composer.focus(); true")
        sidebar.surface.view.setFocus()
        QTest.keyClick(
            sidebar.surface.view.focusProxy(),
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.wait_for(lambda: len(self.service.messages.read(task)["messages"]) == 3)
        window.config.dark_mode = True
        window.apply_dark_mode()
        self.wait_for(
            lambda: self.value(
                sidebar, "document.documentElement.dataset.theme === 'dark'"
            )
        )
        self.assertEqual(self.service.store.grants(task), [])

    def test_real_selection_preserves_drafts_and_stale_commands_cannot_send(self):
        _, sidebar = self.loaded()
        first = self.connect_task(sidebar, "First task")
        second = self.connect_task(sidebar, "Second task")
        sidebar.select_task(first)
        self.wait_for(
            lambda: self.value(sidebar, f"state.task_id === {json.dumps(first)}")
        )
        self.input(sidebar, "First unsent draft")
        stale = {
            "context": sidebar.state["context"],
            "action": "send",
            "value": "Stale message",
            "version": 9,
        }
        self.value(
            sidebar,
            f"document.getElementById('tasks').value = {json.dumps(second)}; document.getElementById('tasks').dispatchEvent(new Event('change')); true",
        )
        self.wait_for(lambda: sidebar.state["task_id"] == second)
        self.wait_for(
            lambda: self.value(sidebar, f"state.task_id === {json.dumps(second)}")
        )
        sidebar._on_command(stale)
        self.assertEqual(self.service.messages.read(second)["messages"], [])
        self.input(sidebar, "Second unsent draft")
        sidebar.select_task(first)
        self.wait_for(
            lambda: self.value(sidebar, "composer.value === 'First unsent draft'")
        )
        self.assertEqual(self.service.messages.read(first)["messages"], [])

    def test_late_state_does_not_overwrite_newer_typing(self):
        _, sidebar = self.loaded()
        self.connect_task(sidebar)
        self.wait_for(lambda: self.value(sidebar, "state.active"))
        self.input(sidebar, "Original")
        self.value(
            sidebar,
            "const old = structuredClone(state); composer.value = 'Newer typing'; draftVersion += 2; window.steedUI.render(old); true",
        )
        self.assertEqual(self.value(sidebar, "composer.value"), "Newer typing")

    def test_activity_model_and_unsafe_mode_are_visible_in_html(self):
        _, sidebar = self.loaded()
        task = self.connect_task(sidebar)
        with (
            patch.object(
                sidebar.providers,
                "model_info",
                return_value={
                    "model": "confirmed-runtime-model",
                    "provider": "Synthetic",
                    "live": True,
                    "source": "thread",
                },
            ),
            patch.object(
                sidebar.providers,
                "activity",
                return_value={"state": "working", "queued": 1},
            ),
        ):
            sidebar.refresh()
            self.wait_for(
                lambda: self.value(
                    sidebar,
                    "document.getElementById('running-model').textContent.includes('confirmed-runtime-model') && !document.getElementById('progress').hidden",
                )
            )
            self.assertIn(
                "1 follow-up",
                self.value(
                    sidebar, "document.getElementById('activity-hint').textContent"
                ),
            )
        self.command("grant.request", task_id=task, scope={"actions": ["artifact"]})
        self.wait_for(
            lambda: self.value(
                sidebar,
                "!document.getElementById('pending').hidden && !document.getElementById('options').open",
            )
        )
        self.service.set_auto_approval(True)
        self.wait_for(
            lambda: self.value(sidebar, "!document.getElementById('unsafe').hidden")
        )
        self.service.set_auto_approval(False)
        self.wait_for(
            lambda: self.value(sidebar, "document.getElementById('unsafe').hidden")
        )

    def test_send_is_once_until_acknowledged_and_failure_keeps_text(self):
        _, sidebar = self.loaded()
        task = self.connect_task(sidebar)
        self.wait_for(lambda: self.value(sidebar, "state.active"))
        self.input(sidebar, "One intentional message")
        self.value(sidebar, "send(); send(); true")
        self.wait_for(lambda: sidebar.draft == "")
        self.wait_for(lambda: self.value(sidebar, "!pendingSend"))
        self.assertEqual(len(self.service.messages.read(task)["messages"]), 1)
        self.input(sidebar, "é" * 4097)
        self.click(sidebar, "send")
        self.wait_for(
            lambda: self.value(
                sidebar, "!document.getElementById('error').hidden && !pendingSend"
            )
        )
        self.assertEqual(self.value(sidebar, "composer.value"), "é" * 4097)
        self.assertEqual(len(self.service.messages.read(task)["messages"]), 1)

    def test_private_transition_covers_pixels_then_scrubs_html_and_commands(self):
        window, sidebar = self.loaded()
        task = self.connect_task(sidebar)
        self.service.send_message(task, "Private-transition canary")
        self.wait_for(
            lambda: self.value(
                sidebar,
                "document.getElementById('messages').textContent.includes('canary')",
            )
        )
        self.input(sidebar, "Unsent canary")
        old_context = sidebar.state["context"]
        window._switch_to_profile("search")
        self.assertIs(sidebar.surface.stack.currentWidget(), sidebar.surface.fallback)
        self.assertEqual(sidebar.draft, "")
        self.wait_for(
            lambda: self.value(
                sidebar,
                "state.private && !document.body.textContent.includes('canary') && composer.value === ''",
            )
        )
        sidebar._on_command(
            {
                "context": old_context,
                "action": "send",
                "value": "Never send",
                "version": 100,
            }
        )
        self.assertEqual(len(self.service.messages.read(task)["messages"]), 1)
        self.assertEqual(sidebar.state["tasks"], [])

    def test_dedicated_profile_no_navigation_network_or_arbitrary_commands(self):
        _, sidebar = self.loaded()
        self.assertTrue(sidebar.surface.profile.isOffTheRecord())
        self.assertIsNot(sidebar.surface.profile, self.profile)
        for url in (
            "https://example.invalid/",
            "file:///etc/passwd",
            "data:text/html,untrusted",
            "steed:newtab",
        ):
            self.assertFalse(
                sidebar.surface.page.acceptNavigationRequest(QUrl(url), 0, True)
            )
        with patch.object(sidebar, "start_chat") as start:
            for raw in (
                "not json",
                "[]",
                json.dumps(
                    {
                        "generation": "old",
                        "context": sidebar.state["context"],
                        "action": "new_chat",
                    }
                ),
                json.dumps(
                    {
                        "generation": sidebar.surface.generation,
                        "context": sidebar.state["context"],
                        "action": "new_chat",
                        "extra": "reject",
                    }
                ),
                json.dumps(
                    {
                        "generation": sidebar.surface.generation,
                        "context": sidebar.state["context"],
                        "action": ["new_chat"],
                    }
                ),
            ):
                sidebar.surface._dispatch(raw)
            start.assert_not_called()
        self.value(
            sidebar,
            "fetch('http://127.0.0.1:1/forbidden').then(() => window.fetchResult = 'allowed').catch(() => window.fetchResult = 'blocked'); true",
        )
        self.wait_for(lambda: self.value(sidebar, "window.fetchResult === 'blocked'"))

    def test_interface_failure_reload_preserves_draft_without_replaying_send(self):
        _, sidebar = self.loaded()
        task = self.connect_task(sidebar)
        self.wait_for(lambda: self.value(sidebar, "state.active"))
        self.input(sidebar, "Recoverable unsent text")
        old_generation = sidebar.surface.generation
        sidebar.surface._failed()
        self.assertFalse(sidebar.surface.ready)
        self.assertIs(sidebar.surface.stack.currentWidget(), sidebar.surface.fallback)
        sidebar.surface.reload()
        self.wait_for(lambda: sidebar.surface.ready)
        self.wait_for(
            lambda: self.value(sidebar, "composer.value === 'Recoverable unsent text'")
        )
        self.assertNotEqual(sidebar.surface.generation, old_generation)
        self.assertEqual(self.service.messages.read(task)["messages"], [])
        self.assertEqual(self.service.store.grants(task), [])

    def test_same_html_page_moves_between_sidebar_and_tab(self):
        window, sidebar = self.loaded()
        page = sidebar.surface.page
        task = self.connect_task(sidebar)
        self.wait_for(lambda: self.value(sidebar, "state.active"))
        self.input(sidebar, "Keep this draft")
        self.value(sidebar, "document.getElementById('options').open = true")
        self.click(sidebar, "open-tab")
        self.wait_for(lambda: window.agent_tab is not None)
        self.assertIs(sidebar.surface.page, page)
        self.assertTrue(self.value(sidebar, "document.getElementById('options').open"))
        self.click(sidebar, "show-sidebar")
        self.wait_for(lambda: window.agent_tab is None)
        self.assertIs(sidebar.surface.page, page)
        self.assertEqual(sidebar.draft, "Keep this draft")
        self.assertEqual(sidebar.state["task_id"], task)
