"""Saving pages (single file, complete, PDF), reader highlights, e-book export."""

import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ElementTree
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

import epub
import webengine
from browser_window import BrowserWindow
from reader_notes import ReaderNotes

ARTICLE = {
    "title": "A Story",
    "byline": "Ada <Writer>",
    "site": "example.org",
    "minutes": 2,
    "blocks": [
        {"type": "p", "text": "The first paragraph has words & more words."},
        {"type": "h2", "text": "Part two"},
        {"type": "li", "text": "one"},
        {"type": "li", "text": "two"},
        {"type": "p", "text": "The last paragraph."},
    ],
}


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        css = self.path == "/style.css"
        body = (
            b"p { color: red }"
            if css
            else b'<title>Saved</title><link rel="stylesheet" href="/style.css">'
            b"<p>content to keep"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/css" if css else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class BookTests(unittest.TestCase):
    def test_an_epub_readers_accept(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "list.epub"
            entries = [
                {"url": "https://example.org/a", "title": "A", "article": ARTICLE},
                {"url": "https://example.org/b", "title": "No text", "article": None},
            ]
            self.assertEqual(epub.write(entries, path), 1)
            with zipfile.ZipFile(path) as book:
                first = book.infolist()[0]
                self.assertEqual(first.filename, "mimetype")
                self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(book.read("mimetype"), b"application/epub+zip")
                for name in book.namelist():
                    if name.endswith((".xml", ".opf", ".xhtml", ".ncx")):
                        ElementTree.fromstring(book.read(name))  # Well formed.
                chapter = book.read("OEBPS/chapter-1.xhtml").decode()
                self.assertIn("Ada &lt;Writer&gt;", chapter)
                self.assertIn("<ul><li>one</li><li>two</li></ul>", chapter)
                self.assertIn("words &amp; more", chapter)
            with self.assertRaises(ValueError):
                epub.write([entries[1]], path)

    def test_highlights_per_article(self):
        with tempfile.TemporaryDirectory() as directory:
            notes = ReaderNotes(Path(directory))
            first = notes.add("u", 0, 4, 9, "first")
            notes.add("u", 0, 20, 25, "x")
            notes.add("u", 0, 6, 12, "overlaps")  # Replaces the first.
            self.assertEqual(
                [(h["start"], h["end"]) for h in notes.highlights("u")],
                [(6, 12), (20, 25)],
            )
            self.assertNotIn(first["id"], [h["id"] for h in notes.highlights("u")])
            with self.assertRaises(ValueError):
                notes.add("u", 0, 5, 5, "")
            private = ReaderNotes(Path())
            private.add("u", 0, 0, 3, "abc")
            self.assertEqual(len(private.highlights("u")), 1)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.root = Path(self.directory.name)
        self.home = patch("pathlib.Path.home", return_value=self.root)
        self.home.start()
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()

    def tearDown(self):
        from ui.prompts import MessageBox

        # A failed test may leave a save running: do not ask about it.
        with patch.object(
            MessageBox, "question", return_value=MessageBox.StandardButton.Yes
        ):
            self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def wait(self, predicate, seconds=WAIT_SECONDS):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def evaluate(self, page, script):
        found = []
        webengine.run_script(page, script, found.append, world=webengine.PAGE_WORLD)
        self.wait(lambda: found)
        return found[0]

    def test_save_pages_and_highlight_in_reader_view(self):
        tab = self.window._new_tab(url=self.url, return_tab=True)
        self.wait(lambda: tab.title() == "Saved" and not tab.page().isLoading())
        out = self.root / "out"
        out.mkdir()
        for kind, check in (
            (
                ".mhtml",
                lambda data: b"MIME-Version" in data and b"content to keep" in data,
            ),
            (".pdf", lambda data: data.startswith(b"%PDF")),
            (".html", lambda data: b"content to keep" in data),
        ):
            path = Path(self.window.save_page_as(str(out / f"page{kind}"), kind))

            def written(path=path):
                return path.is_file() and path.stat().st_size > 0

            self.wait(written, 20)
            time.sleep(0.2)
            self.app.processEvents()
            self.assertTrue(check(path.read_bytes()), kind)
        # Complete: the page's files too, beside it.
        saved = [p.name for p in out.rglob("*.css")]
        self.assertEqual(saved, ["style.css"])
        # The saves finish (closing would otherwise ask about live downloads).
        self.wait(lambda: not self.window.download_manager.active(), 20)

        # Reader view of a saved article: highlights, notes and removal.
        entry = {
            "url": "https://example.org/story",
            "title": "A Story",
            "article": ARTICLE,
        }
        self.window.read_offline(entry)
        reader = self.window.tabs[self.window.current_tab_index].reader_panel
        self.wait(lambda: reader.surface.ready)

        def send(**command):
            reader.command(
                {"context": reader.context, "sequence": reader.sequence + 1, **command}
            )

        send(action="highlight", block=0, start=4, end=9)
        send(action="highlight", block=99, start=0, end=3)  # Not a block: ignored.
        notes = reader.notes.highlights("https://example.org/story")
        self.assertEqual([h["text"] for h in notes], ["first"])
        send(action="note", id=notes[0]["id"], text="remember this")
        page = reader.surface.page
        self.wait(
            lambda: (
                self.evaluate(
                    page,
                    "[...document.querySelectorAll('mark')].map(m => m.title).join()",
                )
                == "remember this"
            )
        )
        self.assertEqual(
            self.evaluate(page, "document.querySelector('mark').textContent"), "first"
        )
        # Kept for the article itself, so reading it again shows them.
        manager = self.window.tab_manager
        stored = ReaderNotes(manager.profile_dir).highlights(
            "https://example.org/story"
        )
        self.assertEqual(stored[0]["note"], "remember this")
        send(action="unhighlight", id=notes[0]["id"])
        self.wait(
            lambda: self.evaluate(page, "document.querySelectorAll('mark').length") == 0
        )


if __name__ == "__main__":
    unittest.main()
