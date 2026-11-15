"""Dangerous sites: pages on phishing and malware lists are stopped with a
warning (you can still go on), downloads from them are refused, and files
that run programs are kept only when you say so."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

import download_manager
from browser_window import BrowserWindow
from services import safe_browsing


class ListTests(unittest.TestCase):
    def test_lists_match_a_host_and_its_subdomains(self):
        class Config(dict):
            pass

        with tempfile.TemporaryDirectory() as directory:
            where = Path(directory)
            (where / "phishing.txt").write_text("# Title\nbank-login.test\n")
            (where / "malware.txt").write_text("payload.test\n")
            service = safe_browsing.SafeBrowsing(Config(), where=where)
            try:
                check = service.check
                self.assertEqual(check(QUrl("https://bank-login.test/x")), "phishing")
                self.assertEqual(
                    check(QUrl("https://www.bank-login.test/")), "phishing"
                )
                self.assertEqual(check(QUrl("http://a.payload.test/f.exe")), "malware")
                self.assertIsNone(check(QUrl("https://login.test/")))
                self.assertIsNone(check(QUrl("https://example.org/")))
                service.allow(QUrl("https://bank-login.test/"))
                self.assertIsNone(check(QUrl("https://bank-login.test/x")))
                service.config["safe_browsing"] = False
                self.assertIsNone(check(QUrl("http://payload.test/")))
            finally:
                service.close()
        self.assertEqual(
            safe_browsing.parse("# c\n! c\nA.test\nwww.b.test\n\n"),
            {"a.test", "b.test"},
        )
        self.assertTrue(download_manager.runs_programs("setup.AppImage"))
        self.assertFalse(download_manager.runs_programs("report.pdf"))


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.safety = self.window.profile_manager.registry.safe_browsing
        self.safety.hosts["phishing"] = {"bank-login.test"}

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

    def click(self, href):
        self.tab.page().runJavaScript(
            f"document.querySelector('a[href^=\"{href}\"]').click()"
        )

    def test_a_listed_page_is_stopped_with_a_way_back_and_a_way_on(self):
        self.tab.setUrl(QUrl("https://bank-login.test/signin"))
        self.wait(
            lambda: (
                self.tab.title() == "Dangerous site"
                and self.tab.url().toString() == "steed://safety/warning"
            )
        )
        html = []
        self.tab.page().toHtml(html.append)
        self.wait(lambda: html)
        self.assertIn("bank-login.test", html[0])
        self.assertIn("phishing site", html[0])
        # A made-up "proceed" link (no token) does nothing.
        self.window._safety_action(
            self.tab, QUrl("steed://safety/proceed?token=guess&url=x")
        )
        self.assertEqual(self.tab.url().toString(), "steed://safety/warning")
        # Visit anyway: allowed for this run.
        with patch.object(type(self.tab), "setUrl") as went:
            self.click("steed://safety/proceed")
            self.wait(lambda: went.called)
        went.assert_called_once_with(QUrl("https://bank-login.test/signin"))
        self.assertIsNone(self.safety.check(QUrl("https://bank-login.test/")))

    def test_downloads_from_listed_sites_and_programs(self):
        manager = self.window.download_manager

        class Request:
            cancelled = False

            def __init__(self, url, name):
                self._url, self._name = QUrl(url), name

            def url(self):
                return self._url

            def suggestedFileName(self):
                return self._name

            def isSavePageDownload(self):
                return False

            def page(self):
                return None

            def cancel(self):
                Request.cancelled = True

            def deleteLater(self):
                pass

        self.safety.hosts["malware"] = {"payload.test"}
        stopped = []
        manager.blocked.connect(lambda name, why: stopped.append((name, why)))
        with patch.object(self.window, "_download_blocked"):
            manager.handle(
                Request("http://payload.test/tool.zip", "tool.zip"), "global"
            )
        self.assertTrue(Request.cancelled)
        self.assertEqual(stopped, [("tool.zip", "malware")])
        # A program: kept only if you agree.
        Request.cancelled = False
        asked = []
        manager.confirm_program = lambda name, parent: asked.append(name) or False
        manager.handle(
            Request("https://good.test/setup.AppImage", "setup.AppImage"), "global"
        )
        self.assertEqual(asked, ["setup.AppImage"])
        self.assertTrue(Request.cancelled)


if __name__ == "__main__":
    unittest.main()
