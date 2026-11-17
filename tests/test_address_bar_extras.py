"""Search keywords and inline address completion."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from qt_test_support import application, keep_directory

from browser import search_keywords
from browser.browser_window import BrowserWindow
from browser.browsing_history import HistoryEntry


class Config(dict):
    def set(self, key, value):
        self[key] = value


class KeywordTests(unittest.TestCase):
    def test_built_in_custom_and_invalid_keywords(self):
        config = Config()
        self.assertEqual(
            search_keywords.search_url("w steed browser", config),
            "https://en.wikipedia.org/w/index.php?search=steed%20browser",
        )
        for text in ("w", "wsteed", "unknown thing", "", "example.org"):
            self.assertIsNone(search_keywords.search_url(text, config))
        config["search_keywords"] = {
            "mdn": ["MDN", "https://developer.mozilla.org/search?q={q}"],
            "w": [
                "German Wikipedia",
                "https://de.wikipedia.org/w/index.php?search={q}",
            ],
            "bad": ["No query", "https://example.org/"],
            "x y": ["Space", "https://example.org/?q={q}"],
        }
        found = search_keywords.keywords(config)
        self.assertEqual(found["mdn"][0], "MDN")
        self.assertEqual(found["w"][0], "German Wikipedia")
        self.assertNotIn("bad", found)
        self.assertEqual(
            search_keywords.match("MDN fetch api", config), ("mdn", "MDN", "fetch api")
        )
        for bad in (
            ("", "N", "https://a/?q={q}"),
            ("k", "", "https://a/?q={q}"),
            ("k", "N", "ftp://a/{q}"),
        ):
            with self.assertRaises(ValueError):
                search_keywords.clean(*bad)


class AddressBarTests(unittest.TestCase):
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
        self.bar = self.window.address_bar
        self.field = self.bar.address_input

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def type(self, text, clear=True):
        self.field.setFocus()
        if clear:
            self.field.clear()
            self.field._typed = ""
        QTest.keyClicks(self.field, text)
        self.app.processEvents()

    def test_keywords_turn_into_searches_and_lead_the_suggestions(self):
        url = bytes(self.window._text_to_qurl("yt cat videos").toEncoded()).decode()
        self.assertEqual(
            url, "https://www.youtube.com/results?search_query=cat%20videos"
        )
        self.assertEqual(self.window._text_to_qurl("example.org").host(), "example.org")
        self.bar._suggest("w steed")
        first = self.bar.history_model.item(0)
        self.assertEqual(first.text(), "Search Wikipedia for “steed”")

    def test_typing_the_start_of_a_visited_site_completes_it(self):
        self.bar.set_history(
            [
                HistoryEntry("https://www.example.org/a", "A"),
                HistoryEntry("https://example.org/b", "B"),
                HistoryEntry("https://exact.example/", "Exact"),
                HistoryEntry("file:///tmp/x.html", "Local"),
            ]
        )
        self.assertEqual(self.field.hosts, ["example.org", "exact.example"])
        self.type("exa")
        self.assertEqual(self.field.text(), "example.org")
        self.assertEqual(self.field.selectedText(), "mple.org")
        # Backspace removes the suggestion and does not bring it back.
        QTest.keyClick(self.field, Qt.Key.Key_Backspace)
        self.app.processEvents()
        self.assertEqual(self.field.text(), "exa")
        self.type("www.exac")
        self.assertEqual(self.field.text(), "www.exact.example")
        self.type("exa mple")
        self.assertEqual(self.field.text(), "exa mple")
        self.field.autocomplete = False
        self.type("exa")
        self.assertEqual(self.field.text(), "exa")

    def test_keywords_dialog_adds_and_removes_your_own(self):
        from ui.search_keywords_dialog import SearchKeywordsDialog

        dialog = SearchKeywordsDialog(self.window.config, self.window)
        try:
            dialog.keyword.setText("mdn")
            dialog.name.setText("MDN")
            dialog.url.setText("https://developer.mozilla.org/search?q={q}")
            dialog.add()
            self.assertIn("mdn", self.window.config.get("search_keywords"))
            self.assertIn(
                "developer.mozilla.org",
                self.window._text_to_qurl("mdn fetch").toString(),
            )
            row = next(
                r
                for r in range(dialog.table.rowCount())
                if dialog.table.item(r, 0).text() == "mdn"
            )
            dialog.table.selectRow(row)
            dialog.remove()
            self.assertEqual(self.window.config.get("search_keywords"), {})
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
