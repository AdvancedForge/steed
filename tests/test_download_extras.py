"""Retrying failed downloads, toolbar progress, and showing a file in its folder."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from browser.download_manager import DownloadRecord
from ui.download_dialog import reveal_file

BODY = bytes(range(256)) * 400  # 100 KB


class Files(BaseHTTPRequestHandler):
    attempts = 0
    # Chromium quietly retries a dropped connection a few times by itself, so
    # the flaky address keeps failing until a test lets it succeed.
    broken = True

    def do_GET(self):
        if self.path == "/page":
            body = b"<title>page</title><p>files"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        type(self).attempts += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="data.bin"')
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        if self.path == "/flaky" and type(self).broken:
            # The first attempt breaks off partway, as a dropped connection does.
            self.wfile.write(BODY[:5000])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


class DownloadExtrasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Files)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Files.attempts = 0
        Files.broken = True
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()
        self.manager = self.window.download_manager
        self.asked = []
        self.target = Path(self.directory.name) / "saved.bin"

        def picker(suggested, parent=None):
            self.asked.append(suggested)
            return str(self.target)

        self.original_picker = self.manager.destination_picker
        self.manager.destination_picker = picker

    def tearDown(self):
        self.manager.destination_picker = self.original_picker
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

    def record(self):
        return next(iter(self.manager.records.values()), None)

    def state(self):
        record = self.record()
        return record.state if record else None

    def web_page(self):
        tab = self.window._new_tab(url=self.origin + "/page", return_tab=True)
        self.wait(lambda: tab.title() == "page")
        return tab.page()

    def test_a_failed_download_is_retried_to_the_same_file_without_asking(self):
        page = self.web_page()
        page.download(QUrl(self.origin + "/flaky"), "data.bin")
        self.wait(lambda: self.manager.records and not self.manager.active())
        key, record = next(iter(self.manager.records.items()))
        self.assertIn(record.state, ("DownloadInterrupted", "Interrupted"))
        self.assertEqual(record.url, self.origin + "/flaky")
        self.assertTrue(self.manager.can_retry(key))
        self.assertEqual(len(self.asked), 1)
        Files.broken = False
        # The hidden fallback page, as when no web tab of the profile is open.
        self.manager.retry(key, self.window._blank_download_page())
        self.wait(lambda: self.state() == "DownloadCompleted")
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.record().path, str(self.target))
        self.assertEqual(self.target.read_bytes(), BODY)
        self.assertFalse(self.manager.can_retry(next(iter(self.manager.records))))

    def test_retry_uses_a_web_tab_or_a_hidden_page_never_a_steed_page(self):
        profile = self.window.profile_manager.current_profile_name
        fallback = self.window._download_page(profile)
        self.assertNotIn(fallback, [tab.page() for tab in self.window.tabs])
        self.assertIs(fallback, self.window._download_page(profile))
        page = self.web_page()
        self.assertIs(self.window._download_page(profile), page)
        self.assertIsNone(self.window._download_page("no-such-profile"))

    def local_page(self, body):
        path = Path(self.directory.name) / "local.html"
        path.write_text("<title>local</title>" + body)
        tab = self.window._new_tab(url=path.as_uri(), return_tab=True)
        self.wait(lambda: tab.title() == "local" and not tab.page().isLoading())
        return tab

    def test_links_on_local_pages_download_and_still_navigate(self):
        tab = self.local_page(
            f"<a id=file href='{self.origin}/file'>file</a>"
            f"<a id=page href='{self.origin}/page'>page</a>"
        )
        tab.page().runJavaScript("document.getElementById('file').click()")
        self.wait(lambda: self.state() == "DownloadCompleted")
        self.assertEqual(self.target.read_bytes(), BODY)
        tab.page().runJavaScript("document.getElementById('page').click()")
        self.wait(lambda: tab.title() == "page")
        self.assertEqual(tab.url().toString(), self.origin + "/page")

    def test_local_pages_still_cannot_read_the_web(self):
        from browser import webengine

        tab = self.local_page(
            "<script>window.got = 'waiting'; fetch('"
            + self.origin
            + "/page').then(r => r.text()).then(t => window.got = t,"
            " () => window.got = 'blocked');</script>"
        )
        self.assertFalse(
            webengine.option(tab.page().settings(), "local_files_reach_web")
        )
        out = []

        def ask():
            webengine.run_script(
                tab.page(), "window.got", out.append, world=webengine.PAGE_WORLD
            )

        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end and (not out or out[-1] == "waiting"):
            ask()
            self.wait(lambda: out)
            if out[-1] == "waiting":
                out.clear()
                time.sleep(0.05)
        self.assertEqual(out[-1], "blocked")

    def test_save_as_on_a_local_page_is_started_by_steed(self):
        from types import SimpleNamespace

        from PyQt6.QtWidgets import QMenu

        from browser import webengine

        tab = self.local_page("<p>x")
        menu = QMenu()
        original = webengine.menu_action(tab.page(), "download_link")
        menu.addAction(original)
        request = SimpleNamespace(
            linkUrl=lambda: QUrl(self.origin + "/file"), mediaUrl=lambda: QUrl()
        )
        self.window._local_download_actions(menu, tab.page(), request)
        (replacement,) = menu.actions()
        self.assertIsNot(replacement, original)
        self.assertEqual(replacement.text(), original.text())
        replacement.trigger()
        self.wait(lambda: self.state() == "DownloadCompleted")
        self.assertEqual(self.target.read_bytes(), BODY)
        self.assertFalse(self.window._download_url(QUrl("file:///etc/passwd")))

    def test_the_toolbar_shows_progress_then_done(self):
        bar = self.window.address_bar
        self.assertTrue(bar.downloads_btn.isHidden())
        page = self.web_page()
        page.download(QUrl(self.origin + "/file"), "data.bin")
        self.wait(lambda: self.state() == "DownloadCompleted")
        self.wait(lambda: bar.downloads_btn.text() == "↓ Done")
        self.assertFalse(bar.downloads_btn.isHidden())
        self.assertIn("data.bin", self.window.status_bar.currentMessage() + "data.bin")
        self.window._downloads_recent.stop()
        self.window._downloads_changed()
        self.assertTrue(bar.downloads_btn.isHidden())

    def test_progress_and_retry_rules(self):
        manager = self.manager
        manager.records = {
            "web": DownloadRecord(
                "web", "a", "/tmp/a", "default", "Interrupted", url="https://x/a"
            ),
            "file": DownloadRecord(
                "file", "b", "/tmp/b", "default", "Interrupted", url="file:///b"
            ),
            "done": DownloadRecord(
                "done", "c", "/tmp/c", "default", "DownloadCompleted", url="https://x/c"
            ),
        }
        self.assertTrue(manager.can_retry("web"))
        self.assertFalse(manager.can_retry("file"))
        self.assertFalse(manager.can_retry("done"))
        with self.assertRaises(ValueError):
            manager.retry("done", None)
        self.assertEqual(manager.progress(), (0, None))

    def test_reveal_selects_the_file_or_opens_its_folder(self):
        path = Path(self.directory.name) / "here.txt"
        path.write_text("x")
        with (
            patch("PyQt6.QtDBus.QDBusInterface.isValid", return_value=False),
            patch(
                "ui.download_dialog.QDesktopServices.openUrl", return_value=True
            ) as opened,
        ):
            self.assertTrue(reveal_file(path))
        opened.assert_called_once_with(QUrl.fromLocalFile(str(path.parent)))
        self.assertFalse(reveal_file(Path(self.directory.name) / "missing" / "x"))


if __name__ == "__main__":
    unittest.main()


class FakeRequest(__import__("PyQt6.QtCore", fromlist=["QObject"]).QObject):
    from PyQt6.QtCore import pyqtSignal as _signal

    receivedBytesChanged = _signal()
    totalBytesChanged = _signal()
    stateChanged = _signal()
    isPausedChanged = _signal()

    def __init__(self, url):
        super().__init__()
        self._url = QUrl(url)
        self.accepted = False

    def page(self):
        return None

    def url(self):
        return self._url

    def suggestedFileName(self):
        return "secret-report.pdf"

    def isSavePageDownload(self):
        return False

    def setDownloadDirectory(self, path):
        self.directory = path

    def setDownloadFileName(self, name):
        self.name = name

    def accept(self):
        self.accepted = True

    def receivedBytes(self):
        return 10

    def totalBytes(self):
        return 10

    def state(self):
        from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest

        return QWebEngineDownloadRequest.DownloadState.DownloadCompleted

    def isPaused(self):
        return False

    def interruptReasonString(self):
        return ""


class PrivateDownloadTests(unittest.TestCase):
    """Incognito downloads are listed while the session lasts and never
    written to the download history on disk."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_incognito_downloads_never_reach_the_history_file(self):
        import json

        from browser.download_manager import DownloadManager

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name)

        class Config:
            config_dir = folder

        manager = DownloadManager(
            Config(), destination_picker=lambda name, parent=None: str(folder / name)
        )
        requests = [
            FakeRequest("https://private.example/secret-report.pdf"),
            FakeRequest("https://public.example/notes.pdf"),
        ]
        # The stand-ins outlive the manager here; their end means nothing.
        self.addCleanup(lambda: [r.destroyed.disconnect() for r in requests])
        manager.handle(requests[0], "search")
        manager.handle(requests[1], "global")
        self.assertEqual(len(manager.records), 2)  # Both shown this session.
        saved = json.loads((folder / "downloads.json").read_text())
        self.assertEqual(
            [r["url"] for r in saved], ["https://public.example/notes.pdf"]
        )
        self.assertNotIn("private.example", (folder / "downloads.json").read_text())
        # A fresh start (next session) knows only the regular one.
        again = DownloadManager(Config())
        self.assertEqual(
            [r.url for r in again.records.values()],
            ["https://public.example/notes.pdf"],
        )
