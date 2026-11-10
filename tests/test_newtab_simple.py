"""The simple new tab page, its photos, hidden bars and the tab hover card."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import (
    QBuffer,
    QByteArray,
    QCoreApplication,
    QEvent,
    QIODevice,
    QPoint,
    Qt,
)
from PyQt6.QtGui import QImage
from qt_test_support import WAIT_SECONDS, application, keep_directory

import services.newtab_photo as photos
import webengine
from browser_window import BrowserWindow


def jpeg():
    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(0x3366AA)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG")
    return bytes(data)


class Picsum(BaseHTTPRequestHandler):
    """Stands in for Lorem Picsum's list and image addresses."""

    asked = []

    def do_GET(self):
        Picsum.asked.append(self.path)
        if self.path.startswith("/list"):
            body = json.dumps(
                [
                    {
                        "id": "7",
                        "author": "Ada <B>",
                        "url": "https://unsplash.com/photos/x",
                    }
                ]
            ).encode()
            kind = "application/json"
        else:
            body, kind = jpeg(), "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SimpleNewTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Picsum)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.patches = [
            patch("pathlib.Path.home", return_value=Path(self.directory.name)),
            patch.object(photos, "LIST_URL", self.origin + "/list?page={page}"),
            patch.object(photos, "IMAGE_URL", self.origin + "/id/{id}.jpg"),
        ]
        for item in self.patches:
            item.start()
        Picsum.asked = []
        self.window = BrowserWindow()
        self.window.resize(1100, 700)
        self.window.show()
        self.pages = self.window.profile_manager.registry.start_pages

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def js(self, tab, script):
        found = []
        webengine.run_script(
            tab.page(), script, found.append, world=webengine.PAGE_WORLD
        )
        self.wait(lambda: found)
        return found[0]

    def test_the_name_an_empty_search_box_and_a_photo_that_changes(self):
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: tab.title() == "New Tab" and not tab.page().isLoading())
        self.assertEqual(
            self.js(tab, "document.querySelector('.wordmark').textContent"), "Steed"
        )
        self.assertEqual(
            self.js(tab, "document.querySelector('#query').placeholder"), ""
        )
        self.assertTrue(
            self.js(tab, "!!document.querySelector('.corner a[href*=customize]')")
        )
        self.assertEqual(
            self.js(tab, "document.querySelectorAll('header, footer, .card').length"), 0
        )
        # The first photo arrives in the background; the open new tab shows it.
        self.wait(lambda: self.pages.photos.current() is not None)
        self.wait(
            lambda: (
                not tab.page().isLoading()
                and self.js(tab, "!!document.querySelector('.credit')")
            )
        )
        credit = self.js(tab, "document.querySelector('.credit').textContent")
        self.assertEqual(credit, "Photo: Ada <B> · Unsplash")
        self.assertTrue(self.js(tab, "document.body.classList.contains('scrim')"))
        html = self.pages.render("global", "newtab").decode()
        self.assertIn("data:image/jpeg;base64,", html)
        self.assertNotIn("Ada <B>", html)  # Escaped.
        # Not due again for 6 hours; with "every new tab", always due.
        self.assertFalse(self.pages.photos.due("6h"))
        self.assertTrue(self.pages.photos.due("new_tab"))
        asked = len(Picsum.asked)
        self.pages.photos.refresh("6h")
        self.assertEqual(len(Picsum.asked), asked)

    def test_a_colour_instead_and_the_classic_page(self):
        settings = self.pages.state("global")["settings"]
        self.pages.configure(
            "global", {**settings, "background": "color", "color": "#112233"}
        )
        html = self.pages.render("global", "newtab").decode()
        self.assertIn("--newtab-color: #112233", html)
        self.assertIn('class="scrim"', html)  # Light text on a dark colour.
        self.assertEqual(Picsum.asked, [])  # No photo asked for.
        self.pages.configure(
            "global", {**settings, "background": "color", "color": "#f0f0f0"}
        )
        self.assertIn('<body class="">', self.pages.render("global", "newtab").decode())
        with self.assertRaises(ValueError):
            self.pages.configure("global", {**settings, "color": "red"})
        self.pages.configure("global", {**settings, "layout": "classic"})
        self.assertIn("Your space.", self.pages.render("global", "newtab").decode())

    def test_bars_hidden_by_default_and_the_tab_card(self):
        window = self.window
        self.assertTrue(window.menuBar().isHidden())
        self.assertTrue(window.status_bar.isHidden())
        window.status_bar.showMessage("Saved the page.")
        label = window.status_overlay.label
        self.assertTrue(label.isVisible())
        self.assertEqual(label.text(), "Saved the page.")
        window.status_bar.clearMessage()
        self.assertFalse(label.isVisible())
        window.status_bar.showMessage("Loading... 40%")  # Routine: not shown.
        self.assertFalse(label.isVisible())
        window.status_bar.showMessage("Saved again.")
        window.status_overlay.timer.timeout.emit()  # Gone after a while.
        self.assertFalse(label.isVisible())
        window._set_bar("status_bar", True)
        window.status_bar.showMessage("Shown in the bar.")
        self.assertFalse(label.isVisible())

        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: not tab.page().isLoading())
        window._show_tab_preview(window.tabs.index(tab), QPoint(10, 10))
        card = window.tab_preview
        self.assertTrue(card.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        from ui.tab_preview import WIDTH

        self.assertEqual(card.card.width(), WIDTH + 20)
        window.config.set("tab_card_fixed_width", False)
        window._show_tab_preview(window.tabs.index(tab), QPoint(10, 10))
        self.assertLess(card.card.width(), WIDTH + 20)


if __name__ == "__main__":
    unittest.main()
