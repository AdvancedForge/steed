"""Asking your agent from anywhere: page menu, "@" in the address bar, and
desktop notifications when it replies or needs you."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWidgets import QMenu
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow


class FakeSidebar:
    def __init__(self):
        self._task_id = None
        self.drafts = []
        self.started = 0
        self.attached = []

    def select_task(self, task):
        self._task_id = task

    def set_draft(self, text, version=None):
        self.drafts.append(text)
        return True

    def start_chat(self):
        self.started += 1
        self._task_id = "new-task"

    def isVisible(self):
        return True

    def refresh(self):
        pass

    def attach_tab(self, task_id, tab):
        self.attached.append((task_id, tab))


class Request:
    def __init__(self, selection="", link="", editable=None):
        self.selection, self.link = selection, link
        if editable is not None:
            self.isContentEditable = lambda: editable

    def selectedText(self):
        return self.selection

    def linkUrl(self):
        return QUrl(self.link)


class AskAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()
        self.registry = self.window.profile_manager.registry
        self.service = self.registry.tasks
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.tab.setHtml(
            "<title>Tide pools</title><p>Anemones and crabs.</p>",
            QUrl("https://example.org/tides"),
        )
        self.wait(
            lambda: (
                self.tab.title() == "Tide pools"
                and self.tab.url().toString() == "https://example.org/tides"
            )
        )
        self.sidebar = FakeSidebar()
        self.toggle = patch.object(
            self.window, "_toggle_agent_sidebar", side_effect=self.show_sidebar
        )
        self.toggle.start()

    def show_sidebar(self, visible):
        self.window.agent_sidebar = self.sidebar

    def tearDown(self):
        self.toggle.stop()
        self.window.agent_sidebar = None
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def test_the_page_menu_asks_about_a_selection_link_or_page(self):
        for request, label in (
            (Request(selection="Anemones and crabs."), "Ask Agent About Selection"),
            (Request(link="https://example.org/more"), "Ask Agent About Link"),
            (Request(), "Ask Agent About This Page"),
        ):
            menu = QMenu()
            menu.addAction("Back")
            self.window._add_ask_agent_item(menu, self.tab, request)
            self.assertEqual(menu.actions()[0].text(), label)
        # With no conversation yet: a new chat starts and the text waits as a draft.
        self.window.ask_agent(self.tab, selection="Anemones and crabs.\nAnd kelp.")
        self.assertEqual(self.sidebar.started, 1)
        (draft,) = self.sidebar.drafts
        self.assertEqual(
            draft,
            "On the page “Tide pools” (https://example.org/tides):\n"
            "> Anemones and crabs.\n> And kelp.\n\n",
        )
        self.assertEqual(self.sidebar.attached, [])  # A selection is enough.
        # The page itself: the draft says its text goes along, and it does.
        self.window.ask_agent(self.tab, page="")
        self.assertEqual(
            self.sidebar.drafts[-1],
            "About “Tide pools” (page attached)\n\n",
        )
        self.assertEqual(self.sidebar.attached, [("new-task", self.tab)])

    def test_ask_agent_to_fill_extract_and_compare(self):
        menus = []

        def errands(request):
            menu = QMenu()
            menus.append(menu)  # Kept alive while its actions are used.
            menu.addAction("Back")
            self.window._add_ask_agent_item(menu, self.tab, request)
            found = next(a.menu() for a in menu.actions() if a.text() == "Ask Agent To")
            return {a.text(): a for a in found.actions()}

        plain = errands(Request())
        self.assertEqual(list(plain), ["Extract This"])  # One web tab, no field.
        field = errands(Request(editable=True))
        self.assertIn("Fill This Form", field)
        field["Fill This Form"].trigger()
        self.assertTrue(self.sidebar.drafts[-1].startswith("Fill in the form"))
        self.assertIn("(page attached)", self.sidebar.drafts[-1])
        self.assertEqual(self.sidebar.attached[-1][1], self.tab)
        # Extracting a selection sends at once, with the selection quoted.
        with patch.object(self.window, "agent_message") as message:
            errands(Request(selection="Anemones: 4"))["Extract This"].trigger()
        text = message.call_args.args[0]
        self.assertTrue(text.startswith("Pull the key facts"))
        self.assertIn("> Anemones: 4", text)
        self.assertTrue(message.call_args.kwargs["send"])
        # With another web tab open, Compare is offered.
        self.window._new_tab(url="https://example.org/other")
        other = self.window.tabs[self.window.current_tab_index]
        self.assertIsNot(other, self.tab)
        self.assertIn("Compare with Other Tabs…", errands(Request()))
        self.window._close_tab(self.window.tabs.index(other))

    def test_at_in_the_address_bar_sends_the_question_with_the_page(self):
        self.service.external_tasks.add("connected-task")
        self.sidebar._task_id = "connected-task"
        with patch.object(self.service, "send_message") as send:
            self.window._navigate_tab_to_url(self.tab, "@ summarize this")
            self.wait(lambda: send.called)  # Once the page's text is read.
        send.assert_called_once_with(
            "connected-task",
            "summarize this\n\n[This page: Tide pools — https://example.org/tides]\n"
            "Anemones and crabs.",
        )
        self.assertEqual(self.tab.url().toString(), "https://example.org/tides")
        self.assertEqual(self.sidebar.drafts, [])
        self.service.external_tasks.discard("connected-task")
        # Incognito has no agents.
        with patch.object(
            self.window.profile_manager, "current_profile_name", "search"
        ):
            self.assertIsNone(self.window.ask_agent(self.tab, page=""))

    def test_desktop_notifications_when_the_agent_replies_or_needs_you(self):
        from ui.desktop_notify import DesktopNotifier

        shown = []

        def notify(notifier, summary, body="", on_click=None, replaces=0):
            shown.append((summary, body, on_click))
            return 1

        delivered = patch.object(DesktopNotifier, "notify", notify)
        delivered.start()
        self.addCleanup(delivered.stop)
        self.window._agent_alerts()
        providers = self.registry.agent_providers
        providers.agent_names["t1"] = "Claude Code"
        with patch.object(
            self.service.messages,
            "read",
            return_value={
                "messages": [{"role": "agent", "text": "Done:\n the  tests pass."}]
            },
        ):
            providers.replied.emit("t1")
        self.assertEqual(
            shown[-1][:2], ("Claude Code replied", "Done: the tests pass.")
        )
        providers.requested.emit("t1", "r1", {"agent": "Claude Code"})
        self.assertEqual(shown[-1][0], "Claude Code needs your approval")
        # Clicking brings the conversation up.
        shown[-1][2]()
        self.assertEqual(self.sidebar._task_id, "t1")
        # Not while you are looking at that conversation (or when turned off).
        count = len(shown)
        self.window.config.set("agent_notifications", False)
        providers.requested.emit("t1", "r2", {"agent": "Claude Code"})
        self.assertEqual(len(shown), count)


if __name__ == "__main__":
    unittest.main()
