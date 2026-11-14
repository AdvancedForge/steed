"""Working with your agent on more than one page: watches that act when they
fire, Organize Tabs and Compare Tabs (stand-in agents here)."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory
from test_ask_agent import FakeSidebar

from browser_window import BrowserWindow
from services import agent_once
from services.watches import then_prompt
from ui.compare_tabs import CompareDialog, comparison
from ui.desktop_notify import DesktopNotifier
from ui.tab_organizer import OrganizeDialog, organize_prompt, parse_plan

ORGANIZER = [
    sys.executable,
    str(Path(__file__).parent / "fixtures" / "fake_organizer_agent.py"),
]


class PartsTests(unittest.TestCase):
    def test_a_plan_keeps_only_tabs_that_exist_once_each(self):
        reply = (
            "Sure.\n"
            '{"groups": [{"name": " Trip  planning ", "tabs": [1, 2, 9, "3"]},'
            ' {"name": "Again", "tabs": [2]}, {"name": "", "tabs": [3]}],'
            ' "duplicates": [3, 3, 0]}'
        )
        self.assertEqual(
            parse_plan(reply, 3),
            {"groups": [("Trip planning", [0, 1])], "duplicates": [2]},
        )
        for unusable in ("No idea.", "{not json}", "[1, 2]", '{"groups": []}'):
            self.assertIsNone(parse_plan(unusable, 3), unusable)

    def test_prompts_carry_only_what_is_needed(self):
        prompt = organize_prompt([("Flights\nto Oslo", "https://a.test/f")])
        self.assertIn("1. Flights to Oslo — https://a.test/f", prompt)
        message = comparison(
            [("A", "https://a.test/", "one\n\n\ntwo"), ("", "https://b.test/", "")]
        )
        self.assertIn("### 1. A\nhttps://a.test/\n\none\ntwo", message)
        self.assertIn("### 2. https://b.test/", message)
        self.assertTrue(message.endswith("\n\n"))  # Room for your question.
        long = comparison([("A", "u", "x" * 50000)] * 2)
        self.assertLess(len(long), 15000)
        watch = {"title": "Kettle", "url": "https://shop.test/k", "then": "buy it"}
        self.assertIn("I asked you to: buy it", then_prompt(watch, "It is $44."))


class CollabTests(unittest.TestCase):
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

    def pages(self, *pages):
        """Web pages in tabs, without the network: (title, url, text)."""
        tabs = [self.window.tabs[self.window.current_tab_index]]
        while len(tabs) < len(pages):
            tabs.append(self.window._new_tab(url="about:blank", return_tab=True))
        for tab, (title, url, text) in zip(tabs, pages, strict=True):
            tab.setHtml(f"<title>{title}</title><p>{text}</p>", QUrl(url))
        self.wait(
            lambda: all(
                tab.title() == title and tab.url().toString() == url
                for tab, (title, url, _) in zip(tabs, pages, strict=True)
            )
        )
        return tabs

    def test_a_new_chat_gets_the_message_once_it_is_connected(self):
        with patch.object(self.service, "send_message") as send:
            task = self.window.agent_message("Check the kettle.", send=True)
            self.assertEqual(task, "new-task")
            self.assertEqual(self.sidebar.drafts, ["Check the kettle."])
            send.assert_not_called()  # Still starting: it waits as a draft.
            self.service.external_tasks.add("new-task")
            try:
                self.wait(lambda: send.called)
            finally:
                self.service.external_tasks.discard("new-task")
        send.assert_called_once_with("new-task", "Check the kettle.")
        self.assertEqual(self.sidebar.drafts[-1], "")
        # Without send, it is only a draft.
        self.sidebar._task_id = None
        self.window.agent_message("Compare these.")
        self.assertEqual(self.sidebar.drafts[-1], "Compare these.")

    def test_a_watch_that_fires_can_start_its_then_action(self):
        self.window._watch_alerts()
        watch = {
            "id": "w",
            "title": "Kettle",
            "url": "https://shop.test/k",
            "then": "add it to my cart",
            "then_mode": "ask",
        }
        with (
            patch.object(DesktopNotifier, "notify") as notify,
            patch.object(BrowserWindow, "agent_message") as message,
        ):
            # Ask first: the notification says what will happen; a click starts it.
            self.registry.watches.fired.emit(watch, "It is $44.")
            (title, body), options = notify.call_args
            self.assertEqual(title, "Kettle")
            self.assertIn("add it to my cart", body)
            message.assert_not_called()
            options["on_click"]()
            text = message.call_args.args[0]
            self.assertIn("It is $44.", text)
            self.assertIn("I asked you to: add it to my cart", text)
            self.assertTrue(message.call_args.kwargs["send"])
            # At once: started straight away, and you are told.
            message.reset_mock()
            self.registry.watches.fired.emit({**watch, "then_mode": "now"}, "$44.")
            message.assert_called_once()
            self.assertIn("on it", notify.call_args.args[1])
            # No then: just the news, and a click opens the page.
            message.reset_mock()
            self.registry.watches.fired.emit({**watch, "then": ""}, "$44.")
            self.assertEqual(notify.call_args.args[1], "$44.")
            message.assert_not_called()
        with self.assertRaises(ValueError):
            self.registry.watches.add(
                "https://shop.test/k", "global", "K", then="x", then_mode="later"
            )

    def test_organize_tabs_applies_the_agents_plan_when_you_choose(self):
        tabs = self.pages(
            ("Flights to Oslo", "https://air.test/oslo", "Fares"),
            ("Recipes for soup", "https://food.test/soup", "Leeks"),
            ("Flights home", "https://air.test/home", "Fares"),
            ("Recipes again", "https://food.test/soup", "Leeks"),
        )
        with patch.object(
            agent_once, "agent_commands", return_value={"fake": ORGANIZER}
        ):
            dialog = OrganizeDialog(self.window, tabs)
        self.wait(lambda: dialog.apply_button.isEnabled())
        self.assertEqual(dialog.table.rowCount(), 3)
        self.assertEqual(dialog.close_duplicates.text(), "Close 1 duplicate tab")
        self.assertIsNone(tabs[0].group)  # Nothing changes before Apply.
        dialog.apply()
        self.assertEqual(
            [tab.group for tab in tabs[:3]], ["Flights", "Recipes", "Flights"]
        )
        self.assertNotIn(tabs[3], self.window.tabs)

    def test_organize_tabs_without_an_agent_says_so(self):
        tabs = self.pages(("A", "https://a.test/", "a"), ("B", "https://b.test/", "b"))
        with patch.object(agent_once, "agent_commands", return_value={}):
            dialog = OrganizeDialog(self.window, tabs)
        self.assertIn("Install Claude Code or Codex", dialog.status.text())
        self.assertFalse(dialog.apply_button.isEnabled())

    def test_compare_tabs_drafts_their_text_for_your_question(self):
        self.pages(
            ("Kettle A", "https://shop.test/a", "Steel, $40"),
            ("Kettle B", "https://shop.test/b", "Glass, $55"),
            ("Kettle C", "https://shop.test/c", "Copper, $90"),
        )
        dialog = CompareDialog(self.window)
        self.assertEqual(dialog.list.count(), 3)
        dialog.compare()
        self.assertEqual(dialog.status.text(), "Tick at least two tabs.")
        for row in (0, 2):
            dialog.list.item(row).setCheckState(Qt.CheckState.Checked)
        dialog.list.item(1).setCheckState(Qt.CheckState.Unchecked)
        with patch.object(BrowserWindow, "agent_message") as message:
            dialog.compare()
            self.wait(lambda: message.called)
        text = message.call_args.args[0]
        self.assertIn("Steel, $40", text)
        self.assertIn("Copper, $90", text)
        self.assertNotIn("Glass", text)  # Only the tabs you tick.
        self.assertNotIn("send", message.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
