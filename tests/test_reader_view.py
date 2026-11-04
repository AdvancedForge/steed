"""Reader view: article text only, shown in a sealed page, closed on navigation."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

import webengine
from browser_window import BrowserWindow
from ui.reader_view import MIN_ARTICLE, article

PARAGRAPH = (
    "Steed keeps the reading calm. This sentence is long enough to count as "
    "article text, and it repeats so the article is clearly the main content."
)
ARTICLE = f"""<!doctype html><title>Story</title>
<meta property="og:site_name" content="Example News"><meta name="author" content="Ada">
<nav class="menu"><a href="/a">Home</a> <a href="/b">World</a> <a href="/c">Sports</a></nav>
<aside class="sidebar related"><p>Related: something else entirely that is not the story at all.</p></aside>
<article><h1>The headline</h1>
{"".join(f"<p>{PARAGRAPH} ({n})</p>" for n in range(8))}
<h2>A section</h2><ul><li>First point that is reasonably descriptive.</li><li>Second point.</li></ul>
<p>&lt;img src=x onerror="document.title='pwned'"&gt; shown as text</p>
<div class="comments"><p>A comment that should not appear in the reader at all, really.</p></div>
</article>""".encode()
SHORT = b"<title>Short</title><p>Hello.</p><a href='/x'>Link</a>"


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        body = SHORT if self.path == "/short" else ARTICLE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ArticleTests(unittest.TestCase):
    def test_rejects_short_or_malformed_answers_and_cleans_blocks(self):
        for raw in (
            None,
            "x",
            "[]",
            '{"ok": false}',
            json.dumps({"ok": True, "blocks": []}),
        ):
            self.assertIsNone(article(raw))
        text = "word " * (MIN_ARTICLE // 4)
        content = article(
            json.dumps(
                {
                    "ok": True,
                    "title": "T" * 400,
                    "site": 5,
                    "blocks": [
                        {"type": "p", "text": text},
                        {"type": "script", "text": "bad"},
                        {"type": "p", "text": 7},
                        {"type": "h2", "text": "   "},
                    ],
                }
            )
        )
        self.assertEqual([b["type"] for b in content["blocks"]], ["p"])
        self.assertEqual(len(content["title"]), 300)
        self.assertEqual(content["site"], "")
        self.assertGreaterEqual(content["minutes"], 1)


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Pages)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def js(self, page, source):
        out = []
        webengine.run_script(page, source, out.append, world=webengine.PAGE_WORLD)
        self.wait(lambda: out)
        return out[0]

    def open(self, path="/story"):
        tab = self.window._new_tab(url=self.origin + path, return_tab=True)
        self.wait(lambda: not tab.page().isLoading() and tab.title())
        return tab

    def reader(self, tab):
        self.window._toggle_reader()
        self.wait(lambda: tab.reader_panel is not None)
        panel = tab.reader_panel
        self.wait(lambda: panel.surface.ready)
        self.wait(
            lambda: self.js(
                panel.surface.page, "document.getElementById('title').textContent"
            )
        )
        return panel

    def test_article_text_only_and_nothing_from_the_site_runs(self):
        tab = self.open()
        panel = self.reader(tab)
        page = panel.surface.page
        text = self.js(page, "document.body.innerText")
        self.assertIn("The headline", text)
        self.assertIn("A section", text)
        self.assertIn("Second point.", text)
        self.assertIn("Example News".upper(), text.upper())
        self.assertIn("Ada", text)
        for absent in ("Sports", "Related:", "A comment that should not"):
            self.assertNotIn(absent, text)
        self.assertIn('<img src=x onerror="document.title', text)
        self.assertEqual(self.js(page, "document.querySelectorAll('img').length"), 0)
        self.assertNotEqual(tab.title(), "pwned")
        self.assertEqual(self.js(page, "document.querySelectorAll('ul li').length"), 2)

    def test_controls_persist_and_f9_or_navigation_closes(self):
        tab = self.open()
        panel = self.reader(tab)

        def send(**command):
            panel.command(
                {"context": panel.context, "sequence": panel.sequence + 1, **command}
            )

        send(action="theme", theme="sepia")
        send(action="size", step=1)
        self.assertEqual(self.window.config.get("reader_theme"), "sepia")
        self.assertEqual(self.window.config.get("reader_font_size"), 20)
        send(action="theme", theme="neon")
        self.assertEqual(self.window.config.get("reader_theme"), "sepia")
        self.window._toggle_reader()
        self.assertIsNone(tab.reader_panel)
        panel = self.reader(tab)
        tab.setUrl(QUrl(self.origin + "/next"))
        self.wait(lambda: tab.reader_panel is None)
        self.assertTrue(panel.disposed)

    def test_pages_without_an_article_say_so(self):
        tab = self.open("/short")
        self.window._toggle_reader()
        self.wait(lambda: "no article" in self.window.status_bar.currentMessage())
        self.assertIsNone(tab.reader_panel)
        self.window._new_tab(url="steed:memory")
        self.window._toggle_reader()
        self.assertIn("web pages", self.window.status_bar.currentMessage())


if __name__ == "__main__":
    unittest.main()
