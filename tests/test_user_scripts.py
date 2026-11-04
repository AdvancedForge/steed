"""User scripts and styles: listed sites only, errors contained, not in incognito."""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

import webengine
from browser_window import BrowserWindow
from user_scripts import PREFIX, clean, matches, parse_sites

PAGE = b"<title>site</title><p>text</p><script>window.pageReady = true</script>"


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


class RuleTests(unittest.TestCase):
    def test_sites_and_matching(self):
        self.assertEqual(
            parse_sites("https://Example.org/path, news.site.org\n*.wiki.org *"),
            ["example.org", "news.site.org", "*.wiki.org", "*"],
        )
        for bad in ("", "exa mple", "http://", "a_b.org", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                parse_sites(bad)
        self.assertTrue(matches("www.example.org", ["example.org"]))
        self.assertTrue(matches("example.org", ["*.example.org"]))
        self.assertFalse(matches("badexample.org", ["example.org"]))
        self.assertTrue(matches("anything.net", ["*"]))

    def test_entries_are_validated(self):
        entry = clean({"name": "x", "sites": "a.org", "css": "p{}", "id": "bad"})
        self.assertEqual(len(entry["id"]), 32)
        self.assertEqual(entry["when"], "ready")
        for bad in (
            {"name": "", "sites": "a.org", "css": "p{}"},
            {"name": "x", "sites": "a.org"},
            {"name": "x", "sites": "a.org", "js": "1", "when": "later"},
            {"name": "x", "sites": "a.org", "js": "x" * 200_001},
        ):
            with self.assertRaises(ValueError):
                clean(bad)


class InjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_port

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
        self.window.show()
        registry = self.window.profile_manager.registry
        self.store = registry.user_scripts(
            self.window.profile_manager.current_profile_name
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

    def js(self, tab, source):
        out = []
        webengine.run_script(tab.page(), source, out.append, world=webengine.PAGE_WORLD)
        self.wait(lambda: out)
        return out[0]

    def open(self, host):
        tab = self.window._new_tab(url=f"http://{host}:{self.port}/", return_tab=True)
        self.wait(lambda: not tab.page().isLoading() and tab.title() == "site")
        self.wait(lambda: self.js(tab, "document.readyState") == "complete")
        return tab

    def test_listed_site_only_errors_contained_and_switch_off(self):
        self.store.save(
            {
                "name": "Tweak",
                "sites": "127.0.0.1",
                "css": "p { color: rgb(1, 2, 3) !important; }",
                "js": "document.body.dataset.tweaked = typeof window.pageReady;",
            }
        )
        broken = self.store.save(
            {"name": "Broken", "sites": "*", "js": "throw new Error('oops')"}
        )
        tab = self.open("127.0.0.1")
        color = "getComputedStyle(document.querySelector('p')).color"
        self.assertEqual(self.js(tab, color), "rgb(1, 2, 3)")
        # The script ran in the page's world, after the page's own script.
        self.assertEqual(self.js(tab, "document.body.dataset.tweaked"), "boolean")
        self.assertTrue(self.js(tab, "window.pageReady"))
        other = self.open("localhost")
        self.assertNotEqual(self.js(other, color), "rgb(1, 2, 3)")
        self.assertIsNone(self.js(other, "document.body.dataset.tweaked || null"))
        self.store.remove(broken)
        first = self.store.entries[0]["id"]
        self.store.set_enabled(first, False)
        tab.reload()
        self.wait(lambda: not tab.page().isLoading())
        self.wait(lambda: self.js(tab, "document.readyState") == "complete")
        self.assertNotEqual(self.js(tab, color), "rgb(1, 2, 3)")
        names = [s.name() for s in tab.page().profile().scripts().toList()]
        self.assertFalse(any(name.startswith(PREFIX) for name in names))

    def test_not_in_incognito_and_kept_per_profile(self):
        registry = self.window.profile_manager.registry
        self.assertIsNone(registry.user_scripts("search"))
        self.store.save({"name": "Mine", "sites": "*", "css": "p{}"})
        scripts = [s.name() for s in registry.search_profile.scripts().toList()]
        self.assertFalse(any(name.startswith(PREFIX) for name in scripts))
        saved = self.store.path.read_text()
        self.assertIn('"Mine"', saved)

    def test_page_saves_toggles_and_reports_errors(self):
        tab = self.window._new_tab(url="steed:userscripts", return_tab=True)
        page = tab.workspace_controller
        self.wait(lambda: page.surface.ready)

        def send(**command):
            page.command(
                {"context": page.context, "sequence": page.sequence + 1, **command}
            )

        send(
            action="save",
            name="Big text",
            sites="example.org",
            css="body{}",
            js="",
            when="ready",
        )
        (entry,) = self.store.entries
        self.assertIn("Saved", page.notice)
        send(action="toggle", id=entry["id"], enabled=False)
        self.assertFalse(self.store.entries[0]["enabled"])
        send(action="save", name="Bad", sites="not a site!", css="p{}")
        self.assertIn("not a site", page.error)


if __name__ == "__main__":
    unittest.main()
