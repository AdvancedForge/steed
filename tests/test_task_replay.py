"""Replaying what an agent did: plain steps, and pictures when turned on."""

import stat
import time
import unittest
from types import SimpleNamespace

import test_live_tabs as fixtures

from services.task_replay import TaskReplay, describe
from ui.task_steps import TaskSteps


def event(sequence, kind, **payload):
    return SimpleNamespace(
        sequence=sequence,
        kind=kind,
        payload=payload,
        created_at=f"2026-11-19T10:00:{sequence:02d}+00:00",
    )


class DescribeTests(unittest.TestCase):
    def test_steps_read_as_sentences_and_bookkeeping_is_left_out(self):
        events = [
            event(1, "session.opened", session_id="s"),
            event(2, "page.action_requested", action_id="a", operation="enter"),
            event(3, "page.action_finished", action_id="a"),
            event(4, "page.action_requested", action_id="b", operation="submit"),
            event(5, "page.action_finished", action_id="b"),
            event(6, "page.action_failed", reason="target_obscured"),
            event(7, "session.navigation_requested", url="https://example.com"),
            event(8, "grant.requested"),
            event(9, "agents.paused", paused=True),
        ]
        store = SimpleNamespace(recent_events=lambda task_id, limit: events)
        replay = TaskReplay(SimpleNamespace(store=store, profiles=None))
        self.assertEqual(
            [sentence for _, sentence, _ in replay.steps("task")],
            [
                "Opened a page",
                "Typed into a field",
                "Sent a form",
                "Tried something that did not work (target obscured)",
                "Went to https://example.com",
                "Agents were paused",
            ],
        )
        self.assertEqual(replay.steps("task")[0][0], "10:00:01")
        self.assertEqual(
            describe(event(1, "page.action_finished", action_id="x"), {}),
            "Did something on the page",
        )


class ReplayTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.LiveTabsTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LiveTabsTests.tearDownClass.__func__)
    setUp = fixtures.LiveTabsTests.setUp
    tearDown = fixtures.LiveTabsTests.tearDown
    wait_for = fixtures.LiveTabsTests.wait_for
    command = fixtures.LiveTabsTests.command
    window = fixtures.LiveTabsTests.window
    share = fixtures.LiveTabsTests.share
    task = fixtures.LiveTabsTests.task
    value = fixtures.LiveTabsTests.value
    inspect = fixtures.LiveTabsTests.inspect
    act = fixtures.LiveTabsTests.act

    def click(self, task, session):
        snapshot = self.inspect(task, session)
        target = next(
            t for f in snapshot.frames for t in f.targets if t.label == "Run action"
        )
        self.act(task, session, snapshot, target)

    def test_pictures_only_when_turned_on_and_deleted_on_request(self):
        window, view = self.window()
        task, _, session = self.share(window)
        replay = self.service.replay
        self.click(task, session)
        self.wait_for(lambda: view.title() == "Clicked")
        later = time.monotonic() + 0.8  # Past the moment a picture is taken.
        self.wait_for(lambda: time.monotonic() > later)
        self.assertEqual(replay.pictures(task), {})  # Off by default.
        self.service.profiles.config.set("agent_replay", True)
        self.click(task, session)
        self.wait_for(lambda: replay.pictures(task))
        sequence, path = next(iter(replay.pictures(task).items()))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        events = {e.sequence: e.kind for e in self.service.store.events(task)}
        self.assertEqual(events[sequence], "page.action_finished")
        steps = replay.steps(task)
        self.assertIn(("Clicked", path), [(s, p) for _, s, p in steps])
        # The journal holds no picture, and page text never.
        self.assertNotIn(str(path), repr(self.service.store.events(task)))
        steps_tab = TaskSteps(self.service)
        steps_tab.show_task(task)
        pictured = [
            steps_tab.list.item(row)
            for row in range(steps_tab.list.count())
            if not steps_tab.list.item(row).icon().isNull()
        ]
        self.assertEqual(len(pictured), 1)
        steps_tab.list.setCurrentItem(pictured[0])
        self.assertFalse(steps_tab.picture.pixmap().isNull())
        steps_tab._delete()
        self.assertEqual(steps_tab.list.count(), len(steps))
        steps_tab.deleteLater()
        replay.forget(task)
        self.assertEqual(replay.pictures(task), {})
        self.assertFalse(path.parent.exists())


if __name__ == "__main__":
    unittest.main()
