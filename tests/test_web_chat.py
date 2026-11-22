"""Tab/sidebar identity, explicit context sharing and bounded native paste."""

import unittest
from unittest.mock import patch

import test_task_profiles as fixtures
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

from browser.browser_window import BrowserWindow
from ui.web_chat import WebChatPromptDialog


class WebChatTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = fixtures.TaskProfileTests.setUp
    tearDown = fixtures.TaskProfileTests.tearDown
    wait_for = fixtures.TaskProfileTests.wait_for
    command = fixtures.TaskProfileTests.command

    def window(self):
        window = BrowserWindow()
        self.windows.append(window)
        window.show()
        return window

    def chat(self, window, html="<textarea></textarea>"):
        view = window._new_tab(url="about:blank", return_tab=True)
        loaded = []
        view.loadFinished.connect(loaded.append)
        view.setHtml(html, QUrl("https://chatgpt.com/"))
        self.wait_for(lambda: loaded and view.url().host() == "chatgpt.com")
        return view

    def value(self, view, script):
        result = []
        view.page().runJavaScript(script, result.append)
        self.wait_for(lambda: bool(result))
        return result[0]

    def test_native_chat_moves_without_losing_unsent_draft_or_task(self):
        window = self.window()
        window._toggle_agent_sidebar(True)
        sidebar = window.agent_sidebar
        sidebar.set_draft("Unsent draft")
        tab = window._open_agent_tab()
        self.assertIs(window.agent_sidebar, sidebar)
        self.assertIs(sidebar.parentWidget(), tab)
        self.assertEqual(sidebar.draft, "Unsent draft")
        self.assertIs(window._open_agent_tab(), tab)
        self.assertEqual(sum(getattr(t, "agent_panel", False) for t in window.tabs), 1)
        window._toggle_agent_sidebar(True)
        self.assertIsNone(window.agent_tab)
        self.assertIs(sidebar.parentWidget(), window)
        self.assertEqual(sidebar.draft, "Unsent draft")
        self.assertNotIn(tab, window.tabs)

    def test_short_agent_tab_keeps_all_controls_reachable_by_scrolling(self):
        window = self.window()
        window.resize(900, 350)
        window._open_agent_tab()
        surface = window.agent_sidebar.surface
        self.wait_for(lambda: surface.ready)
        self.value(surface.view, "document.getElementById('options').open = true")
        self.wait_for(
            lambda: self.value(
                surface.view, "document.documentElement.scrollHeight > innerHeight"
            )
        )
        self.assertTrue(window.agent_sidebar.state["can_library"])

    def test_agent_tab_persists_and_reopens_as_native_ui(self):
        window = self.window()
        tab = window._open_agent_tab()
        window._save_current_tabs()
        self.assertIn(
            "steed:agents", [s.url for s in window.tab_manager.get_saved_tabs()]
        )
        window._close_tab(window.tabs.index(tab))
        self.assertIsNone(window.agent_tab)
        window._restore_closed_tab()
        self.assertTrue(window.agent_tab.agent_panel)
        self.assertEqual(
            window.agent_tab.page().profile(), window.profile_manager.current_profile
        )

    def test_agent_tab_profile_switch_clears_private_ui_and_navigation_releases_panel(
        self,
    ):
        window = self.window()
        window._open_agent_tab()
        window.agent_sidebar.set_draft("Private unsent draft")
        window._switch_to_profile("search")
        self.assertIsNone(window.agent_tab)
        self.assertEqual(window.agent_sidebar.draft, "")
        self.assertFalse(window.agent_sidebar.state["active"])
        tab = window._open_agent_tab()
        window._navigate_tab_to_url(tab, "about:blank")
        self.assertIsNone(window.agent_tab)
        self.assertIsNone(tab.native_panel)

    def test_website_moves_same_page_and_state_between_sidebar_and_tab(self):
        window = self.window()
        view = self.chat(
            window, "<textarea>Draft</textarea><script>window.chatState=42</script>"
        )
        page = view.page()
        window._dock_web_chat()
        self.assertIs(window.web_chat_dock.view, view)
        self.assertIs(view.page(), page)
        self.assertIsNot(view.parentWidget(), window.tab_container)
        self.assertTrue(view.isVisible())
        window._switch_tab(window.tabs.index(view))
        self.assertIsNone(window.web_chat_dock.view)
        self.assertIs(view.parentWidget(), window.tab_container)
        self.assertEqual(self.value(view, "window.chatState"), 42)
        self.assertEqual(
            self.value(view, "document.querySelector('textarea').value"), "Draft"
        )

    def test_the_website_chats_menu_opens_them_in_the_chat_sidebar(self):
        from unittest.mock import patch as patched

        from services.website_chats import WEB_CHATS

        window = self.window()
        agents = window._layout_menus["agents"]
        chats = next(a.menu() for a in agents.actions() if a.text() == "Website Chats")
        label, url = next(iter(WEB_CHATS.items()))
        action = next(a for a in chats.actions() if a.text() == label)
        with patched.object(type(window), "_open_website_assistant") as opened:
            action.trigger()
        opened.assert_called_once_with(url)

    def test_closing_docked_current_index_and_switching_profile_releases_view(self):
        window = self.window()
        view = self.chat(window)
        window._dock_web_chat()
        window._close_tab(window.tabs.index(view))
        self.assertIsNone(window.web_chat_dock.view)
        view = self.chat(window)
        window._dock_web_chat()
        window._switch_to_profile("work")
        self.assertIsNone(window.web_chat_dock.view)
        self.assertTrue(
            all(
                t.page().profile() is window.profile_manager.current_profile
                for t in window.tabs
            )
        )

    def test_context_is_opt_in_exact_preview_and_strips_url_secrets(self):
        window = self.window()
        source = window.tabs[0]
        source.setHtml(
            "<title>Source page</title><p>Website content</p>",
            QUrl("https://example.test/path?token=secret#fragment"),
        )
        self.wait_for(lambda: source.url().host() == "example.test")
        target = self.chat(window)
        window._switch_tab(0)
        dialog = WebChatPromptDialog(window, target)
        dialog.instructions.setPlainText("Summarize my selection")
        self.assertEqual(dialog.preview.toPlainText(), "Summarize my selection")
        dialog.include_context.setChecked(True)
        preview = dialog.preview.toPlainText()
        self.assertIn("https://example.test/path", preview)
        self.assertNotIn("secret", preview)
        self.assertNotIn("fragment", preview)
        self.assertIn("untrusted website content", preview)
        self.assertNotIn("Website content", preview)
        dialog.copy_prompt()
        self.assertEqual(QApplication.clipboard().text(), preview)
        self.assertFalse(self.service._sessions)
        self.assertEqual(self.service.store.list_tasks(), [])

    def test_paste_dispatches_native_input_without_submitting_form(self):
        window = self.window()
        target = self.chat(
            window,
            "<form onsubmit='window.sent=true;return false'><textarea></textarea><button>Send</button></form><script>window.sent=false;window.edited=false;document.querySelector('textarea').oninput=()=>window.edited=true</script>",
        )
        dialog = WebChatPromptDialog(window, target)
        dialog.instructions.setPlainText("Hello website chat")
        dialog.show()
        dialog.paste_prompt()
        self.wait_for(lambda: dialog.result() == dialog.DialogCode.Accepted)
        self.wait_for(
            lambda: (
                self.value(target, "document.querySelector('textarea').value")
                == "Hello website chat"
            )
        )
        self.assertTrue(self.value(target, "window.edited"))
        self.assertFalse(self.value(target, "window.sent"))

    def test_ambiguous_composer_fails_without_clipboard_or_dom_change(self):
        window = self.window()
        target = self.chat(window, "<textarea></textarea><textarea></textarea>")
        dialog = WebChatPromptDialog(window, target)
        dialog.instructions.setPlainText("Don't choose an editor for me")
        QApplication.clipboard().setText("Previous clipboard")
        dialog.paste_prompt()
        self.wait_for(lambda: "Couldn't identify" in dialog.error.text())
        self.assertEqual(QApplication.clipboard().text(), "Previous clipboard")
        self.assertEqual(
            self.value(target, "document.querySelector('textarea').value"), ""
        )
        dialog.reject()

    def test_navigation_or_cancel_blocks_late_paste_and_size_limit_is_utf8(self):
        window = self.window()
        target = self.chat(window)
        for cancel in (False, True):
            dialog = WebChatPromptDialog(window, target)
            dialog.instructions.setPlainText("é" * 4097)
            self.assertFalse(dialog.copy.isEnabled())
            dialog.instructions.setPlainText("Reviewed text")
            QApplication.clipboard().setText("Original")
            callbacks = []
            with patch.object(
                target.page(),
                "runJavaScript",
                side_effect=lambda code, world, cb, callbacks=callbacks: (
                    callbacks.append(cb)
                ),
            ):
                dialog.paste_prompt()
            if cancel:
                dialog.reject()
            else:
                target.loadStarted.emit()
            callbacks[0](True)
            self.assertEqual(QApplication.clipboard().text(), "Original")
            if not cancel:
                self.assertIn("Couldn't identify", dialog.error.text())
                dialog.reject()

    def test_private_web_chat_uses_private_profile_without_native_chat_authority(self):
        window = self.window()
        window._switch_to_profile("search")
        target = self.chat(window)
        window._dock_web_chat()
        self.assertTrue(target.page().profile().isOffTheRecord())
        dialog = WebChatPromptDialog(window, target)
        dialog.instructions.setPlainText("Private website message")
        dialog.copy_prompt()
        self.assertIsNone(self.registry.agent_gateway)
        self.assertEqual(self.service.store.list_tasks(), [])

    def test_profile_switch_scrubs_prompt_and_blocks_copy_to_new_profile(self):
        window = self.window()
        target = self.chat(window)
        dialog = WebChatPromptDialog(window, target)
        dialog.instructions.setPlainText("Regular profile draft")
        dialog.show()
        QApplication.clipboard().setText("Keep this")
        window._switch_to_profile("search")
        self.assertEqual(dialog.instructions.toPlainText(), "")
        self.assertEqual(dialog.preview.toPlainText(), "")
        self.assertFalse(dialog.isVisible())
        dialog.copy_prompt()
        self.assertEqual(QApplication.clipboard().text(), "Keep this")

    def test_delegation_from_native_chat_targets_browsing_tab_not_blank_panel(self):
        window = self.window()
        source = self.chat(window)
        panel = window._open_agent_tab()
        self.assertIs(panel.agent_source, source)
        task = self.command("task.create", objective="Delegate from chat")
        self.service.external_connection(task.task_id, True)
        sidebar = window.agent_sidebar
        sidebar.select_task(task.task_id)
        self.assertTrue(sidebar.state["can_delegate"])
        with patch.object(self.service.live_tabs, "request") as request:
            window.delegate_tabs(task.task_id)
            self.assertEqual(request.call_args.args[1], [source])
            window.delegate_tabs(task.task_id, whole_profile=True)
            self.assertEqual(request.call_args.args[1], [source])
