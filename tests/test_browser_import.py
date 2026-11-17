"""Importing bookmarks, history and open tabs from synthetic Chromium and Firefox profiles."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application, keep_directory

from browser import browser_import


def literal_block(data: bytes) -> bytes:
    """One LZ4 sequence holding only literals (valid for the decoder)."""
    length = len(data)
    if length < 15:
        return bytes([length << 4]) + data
    rest, extra = length - 15, bytearray()
    while rest >= 255:
        extra.append(255)
        rest -= 255
    extra.append(rest)
    return bytes([0xF0]) + bytes(extra) + data


def make_chromium(home: Path):
    root = home / ".config/chromium"
    profile = root / "Default"
    profile.mkdir(parents=True)
    (root / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Default": {"name": "Personal"}}}})
    )
    (profile / "Bookmarks").write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "children": [
                            {
                                "type": "url",
                                "name": "Docs",
                                "url": "https://docs.example/",
                            },
                            {
                                "type": "folder",
                                "name": "Work",
                                "children": [
                                    {
                                        "type": "url",
                                        "name": "Wiki",
                                        "url": "https://wiki.example/",
                                    },
                                    {
                                        "type": "url",
                                        "name": "Local",
                                        "url": "file:///etc/x",
                                    },
                                ],
                            },
                        ]
                    },
                    "other": {"children": []},
                }
            }
        )
    )
    db = sqlite3.connect(profile / "History")
    db.execute(
        "CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INT, hidden INT)"
    )
    db.executemany(
        "INSERT INTO urls VALUES (?,?,?,0)",
        [
            ("https://old.example/", "Old", 1),
            ("https://new.example/", "New", 2),
            ("chrome://settings", "Settings", 3),
        ],
    )
    db.commit()
    db.close()


def make_firefox(home: Path):
    root = home / ".mozilla/firefox"
    profile = root / "abc.default-release"
    (profile / "sessionstore-backups").mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Profile0]\nName=default-release\nIsRelative=1\nPath=abc.default-release\n"
    )
    db = sqlite3.connect(profile / "places.sqlite")
    db.execute(
        "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT,"
        " visit_count INT, hidden INT, last_visit_date INT)"
    )
    db.execute(
        "CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INT, fk INT,"
        " parent INT, title TEXT, position INT)"
    )
    db.executemany(
        "INSERT INTO moz_places VALUES (?,?,?,?,0,?)",
        [
            (1, "https://fox.example/", "Fox", 3, 10),
            (2, "https://news.example/", "News", 1, 20),
        ],
    )
    db.executemany(
        "INSERT INTO moz_bookmarks VALUES (?,?,?,?,?,?)",
        [
            (1, 2, None, 0, "", 0),
            (2, 2, None, 1, "toolbar", 0),
            (3, 2, None, 2, "Reading", 0),
            (4, 1, 1, 3, "Fox site", 0),
            (5, 1, 2, 2, "News site", 1),
        ],
    )
    db.commit()
    db.close()
    session = {
        "windows": [
            {
                "tabs": [
                    {
                        "index": 2,
                        "entries": [
                            {"url": "https://a.example/", "title": "A"},
                            {"url": "https://b.example/", "title": "B"},
                        ],
                    },
                    {"index": 1, "entries": [{"url": "about:blank"}]},
                ]
            }
        ]
    }
    data = json.dumps(session).encode()
    (profile / "sessionstore-backups" / "recovery.jsonlz4").write_bytes(
        b"mozLz40\0" + len(data).to_bytes(4, "little") + literal_block(data)
    )


class ReaderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        make_chromium(self.home)
        make_firefox(self.home)

    def test_profiles_are_found_and_read(self):
        sources = {s["browser"]: s for s in browser_import.discover(self.home)}
        self.assertEqual(sources["Chromium"]["name"], "Personal")
        self.assertEqual(sources["Firefox"]["name"], "default-release")
        chromium = browser_import.read_source(sources["Chromium"])
        self.assertEqual(
            chromium["bookmarks"],
            [
                {"url": "https://docs.example/", "title": "Docs", "folder": ""},
                {"url": "https://wiki.example/", "title": "Wiki", "folder": "Work"},
            ],
        )
        self.assertEqual([h["title"] for h in chromium["history"]], ["New", "Old"])
        self.assertEqual(chromium["tabs"], [])
        firefox = browser_import.read_source(sources["Firefox"])
        self.assertEqual(
            {(b["title"], b["folder"]) for b in firefox["bookmarks"]},
            {("Fox site", "Reading"), ("News site", "")},
        )
        self.assertEqual([h["title"] for h in firefox["history"]], ["News", "Fox"])
        self.assertEqual(firefox["tabs"], [{"url": "https://b.example/", "title": "B"}])

    def test_lz4_with_back_references_and_damaged_input(self):
        # "abc" literals, then a 6-byte match 3 back: "abcabcabc".
        block = bytes([0x32]) + b"abc" + (3).to_bytes(2, "little")
        self.assertEqual(browser_import.lz4_block(block, 9), b"abcabcabc")
        with self.assertRaises(ValueError):
            browser_import.lz4_block(
                bytes([0x10]) + b"a" + (5).to_bytes(2, "little"), 9
            )

    def test_missing_or_broken_files_give_nothing(self):
        (self.home / ".config/chromium/Default/History").write_bytes(b"not a database")
        source = next(
            s for s in browser_import.discover(self.home) if s["browser"] == "Chromium"
        )
        self.assertEqual(browser_import.read_source(source)["history"], [])
        self.assertEqual(browser_import.discover(self.home / "nothing"), [])


class WindowImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.root = Path(self.directory.name)
        self.home = patch("pathlib.Path.home", return_value=self.root)
        self.home.start()
        make_firefox(self.root)
        from browser.browser_window import BrowserWindow

        self.window = BrowserWindow()
        self.window.show()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def test_import_into_this_profile_and_again(self):
        from ui.import_dialog import ImportDialog

        dialog = ImportDialog(self.window)
        try:
            self.assertEqual(dialog.source.count(), 1)
            self.assertTrue(dialog.tabs.isEnabled())
            summary = dialog.run()
        finally:
            dialog.deleteLater()
        self.assertIn("2 bookmarks added", summary)
        self.assertIn("2 history pages", summary)
        self.assertIn("1 open tabs saved", summary)
        profile = self.window.profile_manager.current_profile_name
        urls = [
            b["url"]
            for b in self.window.profile_manager.registry.start_pages.state(profile)[
                "bookmarks"
            ]
        ]
        self.assertIn("https://fox.example/", urls)
        self.assertEqual(
            [e.url for e in self.window.history.entries()][:2],
            ["https://news.example/", "https://fox.example/"],
        )
        (group,) = self.window.tab_manager.saved_groups()
        self.assertEqual(group["name"], "Open tabs from Firefox")
        source = browser_import.discover(self.root)[0]
        again = self.window.import_from_browser(source, history=False, tabs=False)
        self.assertIn("0 bookmarks added, 2 already there", again)


if __name__ == "__main__":
    unittest.main()
