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

from browser import webengine
from browser.browser_window import BrowserWindow
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
<img src="/pic.png" alt="A picture" width="300" height="200">
<img src="/fake.png" alt="Not a picture" width="300" height="200">
<img src="/big.png" alt="Too big" width="300" height="200">
<img src="/pic.png" alt="icon" width="16" height="16">
<h2>A section</h2><ul><li>First point that is reasonably descriptive.</li><li>Second point.</li></ul>
<p>&lt;img src=x onerror="document.title='pwned'"&gt; shown as text</p>
<div class="comments"><p>A comment that should not appear in the reader at all, really.</p></div>
</article>""".encode()
SHORT = b"<title>Short</title><p>Hello.</p><a href='/x'>Link</a>"


def png_bytes():
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage

    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


IMAGES = {}


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        body = SHORT if self.path == "/short" else IMAGES.get(self.path, ARTICLE)
        self.send_response(200)
        self.send_header(
            "Content-Type", "image/png" if self.path in IMAGES else "text/html"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ArticleTests(unittest.TestCase):
    def test_image_types_come_from_the_bytes(self):
        from ui.reader_view import image_type

        self.assertEqual(image_type(b"\x89PNG\r\n\x1a\nrest"), "image/png")
        self.assertEqual(image_type(b"\xff\xd8\xff\xe0"), "image/jpeg")
        self.assertEqual(image_type(b"RIFF1234WEBPVP8 "), "image/webp")
        self.assertEqual(image_type(b"\x00\x00\x00\x1cftypavif"), "image/avif")
        for other in (b"<svg xmlns=", b"<script>", b"", b"GIF8"):
            self.assertIsNone(image_type(other))

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
        IMAGES.update(
            {
                "/pic.png": png_bytes(),
                "/fake.png": b"<script>alert(1)</script>",
                "/big.png": b"\x89PNG\r\n\x1a\n" + b"0" * 2_100_000,
            }
        )
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
        # The only images are the reader's own, holding inline data at most.
        sources = self.js(
            page, "[...document.images].map(i => i.getAttribute('src') || '')"
        )
        self.assertTrue(all(s == "" or s.startswith("data:image/") for s in sources))
        self.assertNotEqual(tab.title(), "pwned")
        self.assertEqual(self.js(page, "document.querySelectorAll('ul li').length"), 2)

    def test_images_are_fetched_checked_and_can_be_hidden(self):
        tab = self.open()
        panel = self.reader(tab)
        page = panel.surface.page
        images = [b for b in panel.content["blocks"] if b["type"] == "img"]
        # Images the page could not show, and the 16-pixel icon, are left out.
        self.assertEqual([b["text"] for b in images], ["A picture"])
        shown = (
            "[...document.querySelectorAll('figure:not([hidden]) img')].map(i => i.alt)"
        )
        self.wait(lambda: self.js(page, shown) == ["A picture"])
        # Steed's own checks: real image bytes only, and a size limit.
        for path in ("/fake.png", "/big.png"):
            panel.content["blocks"].append(
                {"type": "img", "text": path, "src": self.origin + path}
            )
        panel.fetch_images()
        self.wait(lambda: not panel.replies)
        self.assertTrue(
            self.js(
                page, "document.querySelector('figure:not([hidden]) img').src"
            ).startswith("data:image/png;base64,")
        )
        self.assertEqual(len(panel.images), 1)
        panel.command(
            {
                "context": panel.context,
                "sequence": panel.sequence + 1,
                "action": "images",
                "show": False,
            }
        )
        self.assertFalse(self.window.config.get("reader_images"))
        self.wait(lambda: self.js(page, shown) == [])

    def test_photo_theme_takes_the_new_tab_photos_colours(self):
        from PyQt6.QtGui import QColor, QImage

        pages = self.window.profile_manager.registry.start_pages
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#2a6f97"))
        with (
            patch.object(pages, "theme_tint", return_value="#2a6f97"),
            patch.object(pages, "background_image", return_value=("k", image)),
        ):
            tab = self.open()
            panel = self.reader(tab)
            self.assertEqual(panel.options()[0], "photo")  # The default.
            look = panel.photo_look()
            self.assertTrue(look["backdrop"].startswith("data:image/jpeg;base64,"))
            background = QColor(look["palette"]["bg"])
            self.assertGreater(background.blue(), background.red())  # Its hue.
            page = panel.surface.page
            self.wait(
                lambda: self.js(
                    page, "document.documentElement.classList.contains('reader-photo')"
                )
            )
            self.assertEqual(
                self.js(
                    page,
                    "getComputedStyle(document.documentElement)"
                    ".getPropertyValue('--reader-bg').trim()",
                ),
                look["palette"]["bg"],
            )
        # Other themes keep their own colours and no photo.
        panel.command(
            {
                "context": panel.context,
                "sequence": panel.sequence + 1,
                "action": "theme",
                "theme": "sepia",
            }
        )
        self.wait(
            lambda: (
                not self.js(
                    page, "document.documentElement.classList.contains('reader-photo')"
                )
            )
        )

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
