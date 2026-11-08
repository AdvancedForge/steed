"""Tracking parameters, HTTPS first, forgetting sites, fingerprinting resistance."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

import privacy
import webengine
from browser_window import BrowserWindow


class Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class Site(BaseHTTPRequestHandler):
    paths = []

    def do_GET(self):
        Site.paths.append(self.path)
        body = b"<title>Site</title><p>hi"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", "visit=1; Path=/; Max-Age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RuleTests(unittest.TestCase):
    def tearDown(self):
        privacy.http_only.clear()

    def test_addresses_are_cleaned_and_upgraded(self):
        on = Config()
        self.assertEqual(
            privacy.navigation("http://example.com/a?utm_source=x&id=1&fbclid=z", on),
            "https://example.com/a?id=1",
        )
        for local in (
            "http://localhost/",
            "http://127.0.0.1/",
            "http://printer/",
            "http://box.local/",
            "http://example.com:8080/",
            "http://[::1]/",
        ):
            self.assertEqual(privacy.navigation(local, on), local, local)
        off = Config(strip_tracking=False, https_first=False)
        self.assertEqual(
            privacy.navigation("http://example.com/?utm_source=x", off),
            "http://example.com/?utm_source=x",
        )
        self.assertEqual(
            privacy.fallback("https://example.com/a", "http://example.com/a"),
            "http://example.com/a",
        )
        # Remembered: not upgraded again this session.
        self.assertEqual(
            privacy.navigation("http://example.com/b", on), "http://example.com/b"
        )
        self.assertIsNone(privacy.fallback("https://a.com/", "http://b.com/"))
        self.assertEqual(privacy.site_of("www.Example.com"), "example.com")


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
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
        Site.paths = []
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()
        privacy.http_only.clear()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def evaluate(self, tab, script):
        found = []
        webengine.run_script(
            tab.page(), script, found.append, world=webengine.PAGE_WORLD
        )
        self.wait(lambda: found)
        return found[0]

    def open(self, url):
        tab = self.window._new_tab(url=url, return_tab=True)
        self.wait(lambda: tab.title() == "Site" and not tab.page().isLoading())
        return tab

    def test_tracking_https_fallback_forgetting_and_fingerprints(self):
        # Tracking parameters never reach the site.
        tab = self.open(self.origin + "/page?utm_source=mail&id=7&gclid=abc")
        self.assertEqual(Site.paths[-1], "/page?id=7")
        self.assertEqual(tab.url().toString(), self.origin + "/page?id=7")

        # An upgraded address the site cannot serve over HTTPS loads as asked:
        # a failed upgraded load (as the engine reports it) falls back.
        secure = self.origin.replace("http:", "https:") + "/plain"
        tab.page().site_compatibility.upgraded = (secure, self.origin + "/plain")
        self.window._on_load_finished(tab, False)
        self.wait(
            lambda: (
                tab.url().toString() == self.origin + "/plain"
                and tab.title() == "Site"
                and not tab.page().isLoading()
            )
        )
        self.assertIn("/plain", Site.paths)
        self.assertIn("127.0.0.1", privacy.http_only)

        # A site set to be forgotten loses its cookies with its last tab.
        manager = self.window.cookie_manager
        self.wait(lambda: manager.get_cookies_for_domain("127.0.0.1"))
        self.window._site_settings().set("127.0.0.1", "forget", "forget")
        self.window._close_tab(self.window.tabs.index(tab))
        self.wait(lambda: not manager.get_cookies_for_domain("127.0.0.1"))

        # Fingerprinting resistance, once switched on, for new pages.
        tab = self.open(self.origin + "/before")
        cores = "navigator.hardwareConcurrency"
        self.window.config.set("resist_fingerprinting", True)
        self.window.profile_manager.refresh_settings()
        guarded = self.open(self.origin + "/after")
        self.assertEqual(self.evaluate(guarded, cores), 4)
        self.assertEqual(self.evaluate(guarded, "navigator.plugins.length"), 0)
        canvas = (
            "(() => { const c = document.createElement('canvas'); c.width = c.height = 64;"
            " const x = c.getContext('2d'); x.fillStyle = '#808080';"
            " x.fillRect(0, 0, 64, 64); return [...x.getImageData(0, 0, 64, 64).data]"
            ".filter((v, i) => i % 4 === 0 && v !== 128).length; })()"
        )
        self.assertGreater(self.evaluate(guarded, canvas), 0)
        self.assertEqual(self.evaluate(tab, canvas), 0)
        self.window.config.set("resist_fingerprinting", False)
        self.window.profile_manager.refresh_settings()


if __name__ == "__main__":
    unittest.main()
