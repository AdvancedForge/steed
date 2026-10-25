"""HTML chat controls cannot replace, inspect or replay into live websites."""

import unittest
from unittest.mock import patch

import test_html_menus as html
import test_web_chat as chats


class HtmlWebChatTests(unittest.TestCase):
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
    click = html.HtmlMenuTests.click

    def docked(self):
        window = self.window()
        view = self.chat(window, "<textarea>UNSENT_WEBSITE_DRAFT</textarea>")
        window._dock_web_chat()
        dock = window.web_chat_dock
        self.ready(dock.presentation)
        return window, view, dock, dock.presentation

    def test_controls_are_isolated_and_html_return_preserves_page_and_draft(self):
        window, view, dock, host = self.docked()
        page = view.page()
        self.assertIsNot(host.surface.page, page)
        self.assertTrue(host.surface.profile.isOffTheRecord())
        self.assertIsNone(page.webChannel())
        self.assertNotIn("UNSENT_WEBSITE_DRAFT", str(host.surface._state))
        with patch.object(window, "_share_web_chat") as share:
            self.click(host, dock.prompt)
            self.wait_for(lambda: share.called)
            share.assert_called_once_with(view)
        self.click(host, dock.restore)
        self.wait_for(lambda: dock.view is None)
        self.assertIs(view.page(), page)
        self.assertEqual(
            chats.WebChatTests.value(
                self, view, "document.querySelector('textarea').value"
            ),
            "UNSENT_WEBSITE_DRAFT",
        )
        self.assertNotIn("chatgpt.com", str(host.surface._state))

    def test_old_controls_cannot_share_with_replacement_and_reload_never_replays(self):
        window, view, dock, host = self.docked()
        stale = {
            "context": host.context,
            "sequence": host.sequence + 1,
            "action": "click",
            "id": self.control(host, dock.prompt),
        }
        replacement = self.chat(window)
        dock.adopt(replacement)
        self.ready(host)
        with patch.object(window, "_share_web_chat") as share:
            host.command(stale)
            share.assert_not_called()
            host.surface._failed()
            self.assertTrue(host.surface.retry.isVisible())
            host.surface.reload()
            self.ready(host)
            self.assertIs(dock.view, replacement)
            share.assert_not_called()
            self.click(host, dock.prompt)
            self.wait_for(lambda: share.called)
            share.assert_called_once_with(replacement)
        self.assertEqual(
            chats.WebChatTests.value(
                self, view, "document.querySelector('textarea').value"
            ),
            "UNSENT_WEBSITE_DRAFT",
        )

    def test_loading_status_profile_cleanup_and_window_disposal(self):
        window, view, dock, host = self.docked()
        view.loadStarted.emit()
        view.loadProgress.emit(42)
        self.wait_for(
            lambda: self.value(host, "document.body.textContent.includes('42%')")
        )
        self.assertTrue(self.value(host, "!!document.querySelector('[role=status]')"))
        view.loadFinished.emit(False)
        self.wait_for(
            lambda: self.value(
                host, "document.body.textContent.includes('could not load')"
            )
        )
        dock.release()
        dock.adopt(view)
        self.ready(host)
        self.assertTrue(
            self.value(host, "document.body.textContent.includes('could not load')")
        )
        window._switch_to_profile("work")
        self.assertIsNone(dock.view)
        self.assertNotIn("chatgpt.com", str(host.surface._state))
        window.close()
        self.assertTrue(host.surface.disposed)
