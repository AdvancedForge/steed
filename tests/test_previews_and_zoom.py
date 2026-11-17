"""Tab hover previews and enlarging pages whose text is small."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow

TEXT = "This is ordinary page text that goes on long enough to count as text. "


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        size = "10px" if self.path.startswith("/small") else "16px"
        body = (
            f"<title>{self.path.strip('/')}</title><style>p{{font-size:{size}}}</style>"
            + "".join(f"<p>{TEXT}</p>" for _ in range(6))
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
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

    def settle(self, seconds=0.5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def open(self, path):
        tab = self.window._new_tab(url=self.origin + path, return_tab=True)
        self.wait(lambda: tab.title() == path.strip("/") and not tab.page().isLoading())
        return tab

    def test_leaving_a_tab_keeps_a_preview_shown_on_hover(self):
        first = self.open("/normal-one")
        self.settle()
        second = self.open("/normal-two")
        self.assertTrue(first.preview.startswith(b"\xff\xd8"))
        self.assertLess(len(first.preview), 200_000)
        index = self.window.tabs.index(first)
        self.window.tab_bar.tab_hovered.emit(index, QPoint(10, 10))
        preview = self.window.tab_preview
        self.assertTrue(preview.isVisible())
        self.assertEqual(preview.title.text(), "normal-one")
        self.assertEqual(preview.site.text(), "127.0.0.1")
        self.assertTrue(preview.image.isVisible())
        # In Steed's colours, not the system's tooltip colours.
        self.assertIn(self.window.address_bar.colors["field"], preview.styleSheet())
        self.window.tab_bar.hover_ended.emit()
        self.assertFalse(preview.isVisible())
        # The tab you are looking at: a live snapshot of it as it is now.
        self.window.tab_bar.tab_hovered.emit(
            self.window.tabs.index(second), QPoint(10, 10)
        )
        self.assertTrue(preview.isVisible())
        self.assertEqual(preview.title.text(), "normal-two")
        self.assertTrue(preview.image.isVisible())
        self.window.tab_bar.hover_ended.emit()
        # With previews off: still the card (the one description), no snapshot.
        self.window.config.set("tab_previews", False)
        self.window._switch_tab(index)
        self.assertIsNone(getattr(second, "preview", None))
        self.window._set_keep_loaded(second, True)
        self.window.tab_bar.tab_hovered.emit(
            self.window.tabs.index(second), QPoint(10, 10)
        )
        self.assertTrue(preview.isVisible())
        self.assertFalse(preview.image.isVisible())
        self.assertEqual(preview.notes.text(), "Kept loaded")

    def test_native_strip_reports_a_hovered_tab(self):
        from ui.native_tab_strip import NativeTabStrip

        strip = self.window.tab_bar
        if not isinstance(strip, NativeTabStrip):
            strip = NativeTabStrip()
            strip.insert_tab(0, "One")
            strip.insert_tab(1, "Two")
            strip.show()
            self.settle(0.2)
        got = []
        strip.tab_hovered.connect(lambda index, point: got.append(index))
        strip._hover_index = 1
        strip._hover_shown()
        self.assertEqual(got, [1])

    def test_small_text_is_enlarged_until_reset_for_the_site(self):
        normal = self.open("/normal")
        self.settle()
        self.assertAlmostEqual(normal.zoomFactor(), 1.0)
        small = self.open("/small")
        self.wait(lambda: small.zoomFactor() > 1.0)
        self.assertAlmostEqual(small.zoomFactor(), 1.5)
        self.assertIn("text is small", self.window.status_bar.currentMessage())
        self.window._zoom(0)
        self.assertAlmostEqual(small.zoomFactor(), 1.0)
        store = self.window._site_settings()
        self.assertEqual(store.get("127.0.0.1")["auto_zoom"], "block")
        small.reload()
        self.wait(lambda: not small.page().isLoading())
        self.settle()
        self.assertAlmostEqual(small.zoomFactor(), 1.0)
        store.clear("127.0.0.1")
        self.window.config.set("auto_zoom_small_text", False)
        other = self.open("/small-again")
        self.settle()
        self.assertAlmostEqual(other.zoomFactor(), 1.0)


if __name__ == "__main__":
    unittest.main()
