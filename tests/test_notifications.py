"""Notifications where and as often as you chose: on the desktop, only in
Steed or none; every time, gathered into one, or only when needed."""

import unittest
from unittest.mock import patch

import test_writing_help as fixture
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtTest import QTest
from qt_test_support import application

from ui.desktop_notify import DesktopNotifier
from ui.notifications import GAPS, Notifications, notifications_for, settings


class Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class Registry(QObject):
    def __init__(self, **settings):
        super().__init__()
        self.config = Config(settings)
        self.last_active_owner = None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.sent = []

        def notify(notifier, summary, body="", on_click=None, replaces=0):
            self.sent.append((summary, body, on_click))
            return len(self.sent)

        delivered = patch.object(DesktopNotifier, "notify", notify)
        delivered.start()
        self.addCleanup(delivered.stop)
        self.clock = Clock()

    def notifications(self, **settings):
        registry = Registry(**settings)
        self.addCleanup(registry.deleteLater)
        return Notifications(registry, clock=self.clock)

    def test_every_time_on_the_desktop_by_default(self):
        self.assertEqual(settings(Config()), ("desktop", "every"))
        self.assertEqual(
            settings(Config(notifications_where="x")), ("desktop", "every")
        )
        notes = self.notifications()
        notes.notify("One")
        notes.notify("Two", "body", kind="needs_me")
        self.assertEqual([s[0] for s in self.sent], ["One", "Two"])

    def test_none(self):
        notes = self.notifications(notifications_where="off")
        self.assertEqual(notes.notify("One", kind="needs_me"), 0)
        self.assertEqual(self.sent, [])

    def test_only_when_something_needs_you(self):
        notes = self.notifications(notifications_often="needs_me")
        notes.notify("Watch fired")
        notes.notify("Approve this?", kind="needs_me")
        self.assertEqual([s[0] for s in self.sent], ["Approve this?"])

    def test_at_most_one_in_15_minutes_with_the_rest_gathered(self):
        notes = self.notifications(notifications_often="15min")
        notes.notify("Kettle", "$45")
        notes.notify("News", "a headline")
        notes.notify("Approve this?", kind="needs_me")  # Never held back.
        notes.notify("Routine ran")
        self.assertEqual([s[0] for s in self.sent], ["Kettle", "Approve this?"])
        self.assertTrue(notes.timer.isActive())
        self.assertGreater(notes.timer.interval(), 14 * 60 * 1000)
        self.clock.now += GAPS["15min"]
        notes.flush()  # What the timer does.
        self.assertEqual(self.sent[-1][0], "2 updates from Steed")
        self.assertEqual(self.sent[-1][1], "News; Routine ran")
        # A quiet spell later, one comes through at once again.
        self.clock.now += GAPS["15min"] + 1
        notes.notify("Later")
        self.assertEqual(self.sent[-1][0], "Later")

    def test_one_held_comes_as_itself(self):
        notes = self.notifications(notifications_often="hourly")
        notes.notify("First")
        clicked = []
        notes.notify("Second", "body", on_click=lambda: clicked.append(1))
        notes.flush()
        self.assertEqual(self.sent[-1][:2], ("Second", "body"))
        self.sent[-1][2]()
        self.assertEqual(clicked, [1])

    def test_turned_off_while_held(self):
        notes = self.notifications(notifications_often="15min")
        notes.notify("First")
        notes.notify("Second")
        notes.config["notifications_where"] = "off"
        notes.flush()
        self.assertEqual([s[0] for s in self.sent], ["First"])


class InSteedTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def test_only_inside_steed_shows_a_note_in_the_window(self):
        registry = self.window.profile_manager.registry
        self.window.config.set("notifications_where", "steed")
        clicked = []
        with patch.object(DesktopNotifier, "notify") as desktop:
            notes = notifications_for(registry)
            self.assertIs(notifications_for(registry), notes)
            notes.notify("Kettle", "$45", on_click=lambda: clicked.append(1))
        desktop.assert_not_called()
        toasts = self.window._toasts
        self.assertEqual(len(toasts), 1)
        toast = toasts[0]
        self.assertTrue(toast.isVisible())
        self.assertEqual(toast.title.text(), "Kettle")
        self.assertEqual(toast.body.text(), "$45")
        QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
        self.assertEqual(clicked, [1])
        self.assertEqual(self.window._toasts, [])
        for n in range(5):
            notes.notify(f"Note {n}")
        self.assertEqual(
            [t.title.text() for t in self.window._toasts],
            ["Note 2", "Note 3", "Note 4"],
        )
        newest = self.window._toasts[-1]
        self.assertLess(newest.y(), self.window._toasts[0].y())  # Newest on top.

    def test_notes_wait_until_you_are_back(self):
        from ui.notifications import Toast

        toast = Toast(self.window, "Hi", "", None)
        self.addCleanup(toast.deleteLater)
        with patch.object(self.window, "isActiveWindow", return_value=False):
            for _ in range(20):
                toast._tick()
        self.assertEqual(toast.left, 10)
        with patch.object(self.window, "isActiveWindow", return_value=True):
            toast._tick()
        self.assertEqual(toast.left, 9)

    def test_settings_are_checked(self):
        from ui.settings_dialog import DEFAULTS, SettingsDialog

        for key in ("notifications_where", "notifications_often"):
            with self.assertRaises(ValueError):
                SettingsDialog._validate_values({**DEFAULTS, key: "sometimes"})
        SettingsDialog._validate_values(
            {**DEFAULTS, "notifications_where": "steed", "notifications_often": "15min"}
        )


if __name__ == "__main__":
    unittest.main()
