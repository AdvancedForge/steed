"""Screenshots: the whole page through Chromium, the visible area, save or copy."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow

TALL = (
    b"<title>tall</title><style>body{margin:0}div{height:3000px;width:600px;"
    b"background:linear-gradient(red,blue)}</style><div></div>"
)


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(TALL)))
        self.end_headers()
        self.wfile.write(TALL)

    def log_message(self, *args):
        pass


class ScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
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
        self.window.resize(900, 600)
        self.window.show()
        self.tab = self.window._new_tab(url=self.origin + "/", return_tab=True)
        self.wait(
            lambda: self.tab.title() == "tall" and not self.tab.page().isLoading()
        )

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

    def test_full_page_is_saved_at_its_whole_height(self):
        path = Path(self.directory.name) / "shot.png"
        capture = self.window.take_screenshot(whole_page=True, path=str(path))
        self.assertIsNotNone(capture)
        self.wait(lambda: path.exists())
        image = QImage(str(path))
        self.assertGreaterEqual(image.height(), 3000)
        self.assertGreater(image.height(), self.tab.height())
        # The DevTools page used for it is gone again.
        self.wait(lambda: self.tab.page().devToolsPage() is None)
        self.assertIn("Saved shot.png", self.window.status_bar.currentMessage())

    def test_visible_area_and_copy(self):
        QApplication.clipboard().clear()
        self.window.take_screenshot(whole_page=False, copy=True)
        image = QApplication.clipboard().image()
        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), self.tab.width() * self.tab.devicePixelRatio())

    def test_editing_crops_highlights_and_hides(self):
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QColor

        from ui.screenshot_editor import ScreenshotEditor, apply_edit

        image = QImage(200, 100, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        hidden = apply_edit(image, "hide", QRect(10, 10, 20, 20))
        self.assertEqual(hidden.pixelColor(15, 15), QColor("black"))
        self.assertEqual(hidden.pixelColor(100, 50), QColor("white"))
        lit = apply_edit(image, "highlight", QRect(50, 50, 40, 30))
        self.assertNotEqual(lit.pixelColor(70, 65), QColor("white"))
        self.assertGreater(lit.pixelColor(70, 65).red(), lit.pixelColor(70, 65).blue())
        self.assertEqual(
            apply_edit(image, "crop", QRect(0, 0, 50, 40)).size().width(), 50
        )
        # A click without a drag changes nothing.
        self.assertIs(apply_edit(image, "crop", QRect(5, 5, 1, 1)), image)
        editor = ScreenshotEditor(image, self.window)
        copied = []
        editor.copied.connect(copied.append)
        editor.choose("crop")
        editor.edit(QRect(0, 0, 100, 50))
        self.assertEqual(editor.image.size().width(), 100)
        editor.undo()
        self.assertEqual(editor.image.size().width(), 200)
        editor.choose("hide")
        editor.edit(QRect(0, 0, 10, 10))
        editor.finish(editor.copied)
        self.assertEqual(copied[0].pixelColor(5, 5), QColor("black"))
        editor.deleteLater()

    def test_edit_from_the_menu_opens_the_editor(self):
        self.window.take_screenshot(whole_page=False, copy="edit")
        editor = self.window.screenshot_editor
        self.assertTrue(editor.isVisible())
        self.assertFalse(editor.image.isNull())
        editor.reject()

    def test_open_developer_tools_is_reported(self):
        from ui import page_screenshot

        holder = self.tab.page().profile()
        tools = __import__("browser.webengine", fromlist=["Page"]).Page(
            holder, self.window
        )
        self.tab.page().setDevToolsPage(tools)
        messages = []
        capture = page_screenshot.FullPageCapture(self.tab.page(), self.window)
        capture.failed.connect(messages.append)
        capture.start()
        self.assertIn("Developer Tools", messages[0])
        self.tab.page().setDevToolsPage(None)
        tools.deleteLater()


if __name__ == "__main__":
    unittest.main()
