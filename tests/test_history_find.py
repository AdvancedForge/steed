"""Find a Page I Saw: what is sent, what comes back, and opening the match."""

import time
import unittest
from unittest.mock import patch

import test_writing_help as fixture

from browser.browsing_history import HistoryEntry
from ui.history_find import candidates, find_prompt, parse_matches

NOW = time.time()


class FindFunctionTests(unittest.TestCase):
    def test_a_month_of_titles_sites_and_dates_without_sensitive_sites(self):
        entries = [
            HistoryEntry(
                "https://news.example/heat-pumps",
                "Heat pumps, explained",
                NOW - 6 * 86400,
            ),
            HistoryEntry("https://mybank.example/", "Balance", NOW - 30),
            HistoryEntry("https://old.example/", "Old", NOW - 40 * 86400),
            HistoryEntry(
                "https://news.example/heat-pumps",
                "Heat pumps, explained",
                NOW - 7 * 86400,
            ),
        ]
        pages = candidates(entries, NOW)
        self.assertEqual(
            [p[:2] for p in pages],
            [("Heat pumps, explained", "https://news.example/heat-pumps")],
        )
        prompt = find_prompt("that heat pump article last week", pages, NOW)
        self.assertIn("“that heat pump article last week”", prompt)
        self.assertIn("1. Heat pumps, explained — news.example — ", prompt)
        self.assertNotIn("/heat-pumps", prompt)  # Sites, not full addresses.
        self.assertIn("Today is", prompt)

    def test_replies_are_checked(self):
        self.assertEqual(parse_matches('Here: [3, 1, 3, 99, "x"]', 3), [2, 0])
        self.assertEqual(parse_matches("none", 3), [])
        self.assertEqual(parse_matches("[]", 3), [])


class FindDialogTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def test_describing_a_page_finds_and_opens_it(self):
        prompts = []

        class Once(fixture.FakeOnce):
            def __init__(self, command, prompt, parent=None, **_):
                fixture.QObject.__init__(self, parent)
                prompts.append(prompt)
                self.running = True
                fixture.QTimer.singleShot(0, lambda: self.answered.emit("[1]"))

        self.window.history.record(
            "https://news.example/heat-pumps", "Heat pumps, explained"
        )
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
            self.window.find_in_history()
            dialog = shown[0]
            dialog.description.setText("that heat pump article")
            dialog.search()
            self.wait(lambda: dialog.matches)
            self.assertEqual(dialog.table.item(0, 0).text(), "Heat pumps, explained")
            self.assertIn("that heat pump article", prompts[0])
            before = len(self.window.tabs)
            dialog.open_selected()
        self.assertEqual(len(self.window.tabs), before + 1)


if __name__ == "__main__":
    unittest.main()
