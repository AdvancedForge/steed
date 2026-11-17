"""A website's permission question appears and answers, without hanging."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from ui.site_permissions import SitePermissionDialog

COPY = b"""<title>copy</title><button id=b style="width:300px;height:100px">Copy</button>
<script>b.onclick = () => navigator.clipboard.writeText('COPIED').then(
  () => document.title = 'copied', () => document.title = 'refused');</script>"""


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(COPY)))
        self.end_headers()
        self.wfile.write(COPY)

    def log_message(self, *args):
        pass


class PermissionPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Page)
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
            time.sleep(0.02)
        self.fail("Timed out")

    def click_copy(self, answer):
        tab = self.window.tabs[0]
        tab.setUrl(QUrl(self.origin + "/copy"))
        self.wait(lambda: tab.title() == "copy" and not tab.page().isLoading())
        asked = []

        def exec_(dialog):
            asked.append(dialog)
            source = getattr(dialog, "source", dialog)
            source.remember.setCurrentIndex(source.remember.findData("once"))
            return answer

        with patch.object(SitePermissionDialog, "exec", exec_):
            QTest.mouseClick(
                tab.focusProxy() or tab, Qt.MouseButton.LeftButton, pos=QPoint(100, 40)
            )
            self.wait(lambda: tab.title() in ("copied", "refused"))
        return asked, tab.title()

    def test_clipboard_question_is_answered_and_the_copy_completes(self):
        asked, title = self.click_copy(QDialog.DialogCode.Accepted)
        self.assertEqual(len(asked), 1)
        self.assertEqual(title, "copied")

    def test_declining_refuses_the_copy_without_hanging(self):
        asked, title = self.click_copy(QDialog.DialogCode.Rejected)
        self.assertEqual(len(asked), 1)
        self.assertEqual(title, "refused")


if __name__ == "__main__":
    unittest.main()
