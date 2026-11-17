"""History persistence, URL eligibility, and private-session regressions."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser.browsing_history import BrowsingHistory
from browser.utils import save_json


class HistoryTests(unittest.TestCase):
    def history(self, path, limit=1000):
        # These checks drive flushing explicitly and do not need a Qt event loop.
        with patch("browser.browsing_history.QTimer"):
            return BrowsingHistory(path, limit=limit)

    def test_recent_unique_visits_are_bounded_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = self.history(path, limit=2)
            history.record("https://example.org/first", "First")
            history.record("https://example.org/second", "Second")
            history.record("https://example.org/first", "Updated first")
            history.record("https://example.org/third", "Third")
            history.close()
            restored = self.history(path, limit=2)
            self.assertEqual(
                [(entry.url, entry.title) for entry in restored.entries()],
                [
                    ("https://example.org/third", "Third"),
                    ("https://example.org/first", "Updated first"),
                ],
            )

    def test_clear_is_persistent_and_closed_history_ignores_late_visits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = self.history(path)
            history.record("https://example.org/", "Example")
            history.clear()
            self.assertEqual(self.history(path).entries(), [])
            history.close()
            history.record("https://example.org/late", "Late")
            self.assertEqual(history.entries(), [])

    def test_private_history_never_schedules_disk_writes(self):
        history = self.history(None)
        history.record("https://example.org/private", "Private")
        self.assertEqual(len(history.entries()), 1)
        history._save_timer.start.assert_not_called()
        with patch("browser.browsing_history.save_json") as save:
            history.close()
        save.assert_not_called()

    def test_malformed_entries_and_non_web_or_credential_urls_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            save_json(
                path,
                [
                    None,
                    {"url": 42},
                    {"url": "javascript:alert(1)"},
                    {"url": "file:///tmp/private.txt"},
                    {"url": "https://name:secret@example.org/"},
                    {"url": "https://example.org/", "title": "One\nTwo"},
                ],
            )
            history = self.history(path)
            self.assertEqual(len(history.entries()), 1)
            self.assertEqual(history.entries()[0].title, "One Two")
            history.record("about:blank")
            history.record("https://name:secret@example.org/")
            self.assertEqual(len(history.entries()), 1)


if __name__ == "__main__":
    unittest.main()
