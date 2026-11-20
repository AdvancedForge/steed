"""Recent history in topics: what is sent, what comes back, what you can do."""

import time
import unittest
from unittest.mock import patch

import test_writing_help as fixture

from browser.browsing_history import HistoryEntry
from ui.history_topics import parse_topics, recent_pages, topics_prompt

NOW = time.time()


class TopicFunctionTests(unittest.TestCase):
    def test_only_the_past_week_without_sensitive_sites(self):
        entries = [
            HistoryEntry("https://flights.example/lis", "Flights to Lisbon", NOW - 60),
            HistoryEntry("https://mybank.example/", "Balance", NOW - 30),
            HistoryEntry("https://old.example/", "Old", NOW - 30 * 86400),
            HistoryEntry("file:///home/me/x", "Local", NOW),
            HistoryEntry("https://flights.example/lis", "Flights to Lisbon", NOW - 90),
        ]
        pages = recent_pages(entries, NOW)
        self.assertEqual(pages, [("Flights to Lisbon", "https://flights.example/lis")])
        prompt = topics_prompt(pages)
        self.assertIn("1. Flights to Lisbon — flights.example", prompt)
        self.assertNotIn("/lis", prompt)  # Sites, not full addresses.

    def test_replies_are_checked(self):
        reply = 'Sure! [{"topic": "Trip  to Lisbon", "pages": [1, 2, 99, "x"]}, {"topic": 5}]'
        self.assertEqual(parse_topics(reply, 2), [("Trip to Lisbon", [0, 1])])
        self.assertEqual(parse_topics("no json here", 2), [])


class TopicDialogTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def test_topics_reopen_their_pages(self):
        class Once(fixture.FakeOnce):
            def __init__(self, command, prompt, parent=None, **_):
                fixture.QObject.__init__(self, parent)
                self.running = True
                fixture.QTimer.singleShot(
                    0,
                    lambda: self.answered.emit(
                        '[{"topic": "Lisbon trip", "pages": [1]}]'
                    ),
                )

        self.window.history.record("https://flights.example/lis", "Flights to Lisbon")
        shown = []
        with (
            patch("services.agent_once.agent_command", return_value=["fake"]),
            patch("services.agent_once.AgentOnce", Once),
            patch.object(
                fixture.BrowserWindow,
                "_present_dialog",
                lambda self, dialog, **_: shown.append(dialog),
            ),
        ):
            self.window.history_topics()
            dialog = shown[0]
            self.wait(lambda: dialog.topics)
            self.assertEqual(dialog.list.item(0).text(), "Lisbon trip · 1 page")
            before = len(self.window.tabs)
            dialog.reopen_topic()
        self.assertEqual(len(self.window.tabs), before + 1)


if __name__ == "__main__":
    unittest.main()
