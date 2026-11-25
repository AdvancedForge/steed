"""An agent handing one step to the person: the page comes to them, the
agent waits, and control goes back when they say so."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_live_tabs as live
import test_task_watch as watch
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent

from services.agent_tools import TOOLS
from services.page_inspector import PageInspectionError
from services.task_client import CLIENT_COMMANDS
from services.task_policy import TaskPermissionError
from ui.handoff_prompt import HandoffPrompt


class AdoptedTabHandoffTests(live.LiveTabsTests):
    """The agent works in one of the person's own tabs."""

    def ask(self, task, session, request="h1", reason="captcha", **extra):
        return self.command(
            "browser.ask_person",
            task_id=task,
            session_id=session,
            request_id=request,
            reason=reason,
            **extra,
        )

    def status(self, task, request="h1"):
        return self.command(
            "browser.ask_person_status", task_id=task, request_id=request
        )

    def test_the_tab_comes_forward_with_a_bar_and_the_agent_pauses(self):
        window, view = self.window()
        task, _, session = self.share(window)
        window._new_tab()
        self.assertIsNot(window.tabs[window.current_tab_index], view)
        with patch("ui.desktop_notify.DesktopNotifier") as notifier:
            receipt = self.ask(
                task, session, message="Please tick the  I'm not a robot\nbox"
            )
        self.assertEqual(receipt["state"], "waiting_for_person")
        self.assertIs(window.tabs[window.current_tab_index], view)
        self.assertTrue(window.handoff_bar.isVisible())
        self.assertIn(
            "solve a check that you are a person", window.handoff_bar.label.text()
        )
        self.assertIn(
            "“Please tick the I'm not a robot box”", window.handoff_bar.label.text()
        )
        del notifier
        # The person can use their page; the agent's commands are refused.
        shield = self.service.backend._sessions[session].shield
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a"
        )
        self.assertFalse(shield.eventFilter(view, event))
        with self.assertRaises(Exception) as refused:
            self.command("page.inspect", task_id=task, session_id=session)
        self.assertEqual(getattr(refused.exception, "code", ""), "user_control_active")
        # Asking twice for the same page while waiting is refused too.
        with self.assertRaises(TaskPermissionError):
            self.ask(task, session, request="h2")
        # Another tab hides the bar; coming back shows it again.
        window._switch_tab(0 if window.tabs[0] is not view else 1)
        self.assertFalse(window.handoff_bar.isVisible())
        window._switch_tab(window.tabs.index(view))
        self.assertTrue(window.handoff_bar.isVisible())
        window.handoff_bar.done.click()
        self.assertFalse(window.handoff_bar.isVisible())
        self.assertEqual(self.status(task)["state"], "done")
        self.assertIn("Inspect it again", self.status(task)["note"])
        self.assertTrue(shield.eventFilter(view, event))
        self.inspect(task, session)  # The agent has the page again.
        kinds = [e.kind for e in self.service.store.recent_events(task, limit=50)]
        self.assertIn("handoff.requested", kinds)
        self.assertIn("handoff.done", kinds)

    def test_pointing_at_the_thing_rings_it_on_the_page(self):
        window, view = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(
            t for f in snapshot.frames for t in f.targets if t.label == "Confirm"
        )
        with self.assertRaises(PageInspectionError):
            self.ask(task, session, snapshot_id=snapshot.snapshot_id, target_id="nope")
        self.assertFalse(window.handoff_bar.isVisible())  # Nothing handed over.
        self.ask(
            task,
            session,
            reason="confirm",
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
        )
        self.assertIn("ringed on the page (Confirm)", window.handoff_bar.label.text())
        self.wait_for(
            lambda: self.value(
                view.page(),
                "[...document.querySelectorAll('div[aria-hidden=true]')]"
                ".some(d => d.style.borderColor || d.style.border)",
            )
        )
        window.handoff_bar.done.click()
        self.assertEqual(self.status(task)["state"], "done")

    def test_i_cant_reports_not_done_and_returns_control(self):
        window, view = self.window()
        task, _, session = self.share(window)
        self.ask(task, session, reason="payment")
        self.assertIn("confirm a payment", window.handoff_bar.label.text())
        window.handoff_bar.cannot.click()
        receipt = self.status(task)
        self.assertEqual(receipt["state"], "not_done")
        self.assertIn("asking them in the conversation", receipt["note"])
        self.inspect(task, session)

    def test_cancelling_the_task_ends_the_request_and_hides_the_bar(self):
        window, view = self.window()
        task, _, session = self.share(window)
        self.ask(task, session)
        self.service.cancel_task(task)
        self.assertFalse(window.handoff_bar.isVisible())
        self.assertEqual(window.handoff_bar.entries, [])

    def test_bad_requests_are_refused(self):
        window, view = self.window()
        task, _, session = self.share(window)
        with self.assertRaises(ValueError):
            self.ask(task, session, reason="anything")
        with self.assertRaises((KeyError, ValueError, TaskPermissionError)):
            self.ask(task, "not-a-session")
        self.assertFalse(window.handoff_bar.isVisible())


# Keep only this file's own tests in the subclass above.
for _name in [n for n in dir(live.LiveTabsTests) if n.startswith("test_")]:
    if _name not in AdoptedTabHandoffTests.__dict__:
        setattr(AdoptedTabHandoffTests, _name, None)


class WatchedPageHandoffTests(watch.TaskWatchTests):
    """A task page that is not one of the person's tabs opens in the watch
    window, already in their hands."""

    def test_watch_window_opens_in_their_control_and_return_counts_as_done(self):
        task, _, session, page = self.session(
            actions=["navigate", "inspect", "read", "attach"]
        )
        self.load(session, page, watch.HTML)
        # This fixture's service has no browser windows: show it as Steed would.
        registry = SimpleNamespace(
            config={"agent_notifications": False},
            owners=[],
            agent_context=lambda: {},
        )
        HandoffPrompt(registry, self.service)
        receipt = self.command(
            "browser.ask_person",
            task_id=task,
            session_id=session,
            request_id="h1",
            reason="code",
            message="Type the code from your phone",
        )
        self.assertEqual(receipt["state"], "waiting_for_person")
        dialog = self.service._watchers[session]
        self.assertTrue(dialog.owns_control())
        self.assertIn("Type the code from your phone", dialog.notice.text())
        dialog.return_control()
        self.assertEqual(
            self.command("browser.ask_person_status", task_id=task, request_id="h1")[
                "state"
            ],
            "done",
        )

    def test_a_page_the_person_cannot_see_needs_attach_first(self):
        task, _, session, page = self.session(actions=["navigate", "inspect", "read"])
        with self.assertRaises(Exception) as refused:
            self.command(
                "browser.ask_person",
                task_id=task,
                session_id=session,
                request_id="h1",
                reason="captcha",
            )
        self.assertEqual(getattr(refused.exception, "code", ""), "attach_needed")
        self.assertFalse(self.service._watchers)


for _name in [n for n in dir(watch.TaskWatchTests) if n.startswith("test_")]:
    if _name not in WatchedPageHandoffTests.__dict__:
        setattr(WatchedPageHandoffTests, _name, None)


class WiringTests(unittest.TestCase):
    def test_agents_can_call_it(self):
        names = {tool.command for tool in TOOLS}
        for name in ("browser.ask_person", "browser.ask_person_status"):
            self.assertIn(name, names)
            self.assertIn(name, CLIENT_COMMANDS)


if __name__ == "__main__":
    unittest.main()
