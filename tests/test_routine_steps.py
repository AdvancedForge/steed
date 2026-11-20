"""Saved routine steps: what they may say, and recording them from a chat."""

import unittest

import test_live_tabs as fixtures

from services.routine_steps import check_steps, describe_step


class CheckTests(unittest.TestCase):
    def test_steps_start_by_opening_and_say_one_thing_each(self):
        steps = check_steps(
            [
                {"open": "https://shop.example/"},
                {"type": "Search", "text": "kettle "},
                {"choose": "Size", "option": "Large"},
                {"click": "Go"},
                {"wait": "Results"},
                {"read": True},
            ]
        )
        self.assertEqual(steps[1], {"type": "Search", "text": "kettle "})
        self.assertEqual(
            [describe_step(s) for s in steps],
            [
                "Open https://shop.example/",
                "Type “kettle ” into “Search”",
                "Choose “Large” in “Size”",
                "Click “Go”",
                "Wait for “Results”",
                "Read the page",
            ],
        )
        self.assertEqual(check_steps(None), [])
        for bad in (
            [{"click": "Go"}],  # Must open a page first.
            [{"open": "file:///etc/passwd"}],
            [{"open": "https://a.test/"}, {"click": "Go", "text": "x"}],
            [{"open": "https://a.test/"}, {"type": "Search"}],
            [{"open": "https://a.test/"}, {"run": "rm -rf"}],
            [{"open": "https://a.test/"}] * 21,
            "open https://a.test",
        ):
            with self.assertRaises(ValueError, msg=bad):
                check_steps(bad)


class RecordingTests(unittest.TestCase):
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

    def test_what_worked_in_a_chat_becomes_steps_and_nothing_else_does(self):
        window, view = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        targets = {t.label: t for f in snapshot.frames for t in f.targets}
        self.act(task, session, snapshot, targets["Message"], "enter", text="hello")
        snapshot = self.inspect(task, session)  # References last one attempt.
        targets = {t.label: t for f in snapshot.frames for t in f.targets}
        self.act(task, session, snapshot, targets["Run action"])
        self.wait_for(lambda: len(self.service.step_logs.get(task, [])) == 3)
        log = self.service.step_logs[task]
        self.assertEqual(log[0], {"open": self.origin + "/live"})
        self.assertEqual(
            log[1:], [{"type": "Message", "text": "hello"}, {"click": "Run action"}]
        )
        # Never in the journal: it holds labels and typed text.
        journal = repr(self.service.store.events(task))
        self.assertNotIn("hello", journal)
        if not self.service.routines.available:
            self.skipTest("Routines need the browser's registry")
        proposal = self.service.routines.propose(
            task,
            "save-steps",
            name="Run it",
            ask="Did it run?",
            steps_from_this_chat=True,
        )
        self.assertEqual(proposal.routine["steps"], log + [{"read": True}])
        other = self.task()
        with self.assertRaises(ValueError):
            self.service.routines.propose(
                other, "none", name="X", ask="Y", steps_from_this_chat=True
            )


if __name__ == "__main__":
    unittest.main()
