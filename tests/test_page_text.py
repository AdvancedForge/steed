"""What an agent gets from a tab: a PDF's own text, not the viewer's nothing."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QUrl
from PyQt6.QtGui import QPageSize, QPainter, QPdfWriter
from qt_test_support import WAIT_SECONDS, application, keep_directory

WORDS = "Quarterly report: revenue rose eleven percent."


def pdf_bytes():
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    painter = QPainter(writer)
    painter.drawText(200, 400, WORDS)
    writer.newPage()
    painter.drawText(200, 400, "Second page, appendix.")
    painter.end()
    return bytes(buffer.data())


class Site(BaseHTTPRequestHandler):
    PDF = b""

    def do_GET(self):
        pdf = self.path.endswith(".pdf")
        body = self.PDF if pdf else b"<title>Plain</title><p>Plain page words.</p>"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf" if pdf else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class PageTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        Site.PDF = pdf_bytes()
        cls.server = HTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        from browser.browser_window import BrowserWindow

        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
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

    def read(self, path):
        from ui.page_text import read_page_text

        tab = self.window._new_tab(return_tab=True)
        loaded = []
        tab.loadFinished.connect(loaded.append)
        tab.setUrl(QUrl(self.origin + path))
        self.wait(lambda: loaded and tab.url().path() == path)
        got = []
        read_page_text(tab.page(), self.window.config, got.append)
        self.wait(lambda: got)
        return got[0]

    def test_a_pdf_tab_gives_its_text(self):
        text = self.read("/report.pdf")
        self.assertIn("revenue rose eleven percent", text)
        self.assertIn("Second page", text)

    def test_a_web_page_gives_its_text(self):
        self.assertIn("Plain page words.", self.read("/plain"))

    def test_unreadable_pdf_bytes_give_nothing(self):
        from ui.page_text import pdf_text

        self.assertEqual(pdf_text(b"not a pdf"), "")
        self.assertIn("eleven percent", pdf_text(Site.PDF))


if __name__ == "__main__":
    unittest.main()
