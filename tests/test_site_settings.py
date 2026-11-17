"""Per-site zoom, JavaScript, autoplay and pop-ups."""

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
from browser.site_settings import SiteSettings, site_key

PAGE = b"<title>static</title><script>document.title = 'scripted'</script>"


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


class StoreTests(unittest.TestCase):
    def test_values_defaults_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site_settings.json"
            store = SiteSettings(path)
            self.assertEqual(store.get("a.org")["javascript"], "allow")
            store.set("a.org", "javascript", "block")
            store.set("a.org", "zoom", 1.25)
            for key, value in (("zoom", 9), ("zoom", True), ("popups", "maybe")):
                with self.assertRaises(ValueError):
                    store.set("a.org", key, value)
            with self.assertRaises(ValueError):
                store.set(None, "zoom", 1.1)
            again = SiteSettings(path)
            self.assertEqual(
                again.changed_keys("a.org"), {"javascript": "block", "zoom": 1.25}
            )
            # Back to the default removes it; an empty site disappears.
            again.set("a.org", "javascript", "allow")
            again.set("a.org", "zoom", 1.0)
            self.assertEqual(json.loads(path.read_text())["sites"], {})
            path.write_text(
                '{"version": 1, "sites": {"b.org": {"zoom": "x", "popups": "block"}}}'
            )
            self.assertEqual(
                SiteSettings(path).changed_keys("b.org"), {"popups": "block"}
            )
            path.write_text("not json")
            self.assertEqual(SiteSettings(path).sites, {})
            memory = SiteSettings(None)
            memory.set("c.org", "zoom", 1.5)
            self.assertEqual(memory.get("c.org")["zoom"], 1.5)

    def test_site_key(self):
        self.assertEqual(site_key(QUrl("https://Example.org/x")), "example.org")
        self.assertEqual(site_key("http://127.0.0.1:8000/"), "127.0.0.1")
        for url in ("steed:memory", "file:///tmp/a.html", "about:blank", ""):
            self.assertIsNone(site_key(url))


class WindowTests(unittest.TestCase):
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
        self.store = self.window._site_settings()

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

    def open(self, host="127.0.0.1"):
        tab = self.window._new_tab(url=f"http://{host}:{self.port}/", return_tab=True)
        self.wait(
            lambda: not tab.page().isLoading() and tab.title() in ("static", "scripted")
        )
        return tab

    def test_blocking_javascript_applies_to_the_site_only(self):
        tab = self.open()
        self.assertEqual(tab.title(), "scripted")
        self.window._set_site_setting(tab, "127.0.0.1", "javascript", "block")
        self.wait(lambda: tab.title() == "static" and not tab.page().isLoading())
        other = self.open("localhost")
        self.assertEqual(other.title(), "scripted")
        settings = tab.page().settings()
        self.assertFalse(webengine.option(settings, "javascript"))
        self.window._clear_site_settings(tab, "127.0.0.1")
        self.wait(lambda: tab.title() == "scripted")

    def test_zoom_is_remembered_per_site(self):
        tab = self.open()
        self.window._switch_tab(self.window.tabs.index(tab))
        self.window._zoom(1)
        zoomed = tab.zoomFactor()
        self.assertGreater(zoomed, 1.0)
        self.assertEqual(self.store.get("127.0.0.1")["zoom"], round(zoomed, 3))
        second = self.open()
        self.wait(lambda: abs(second.zoomFactor() - zoomed) < 0.01)
        tab.setUrl(QUrl(f"http://localhost:{self.port}/"))
        self.wait(lambda: abs(tab.zoomFactor() - 1.0) < 0.01)
        # Resetting from another place updates open tabs of the site.
        self.store.set("127.0.0.1", "zoom", 1.0)
        self.wait(lambda: abs(second.zoomFactor() - 1.0) < 0.01)

    def test_autoplay_popups_and_the_menu(self):
        tab = self.open()
        self.window._switch_tab(self.window.tabs.index(tab))
        self.window._show_site_menu()
        menu = self.window.site_menu
        actions = {a.text(): a for a in menu.actions()}
        self.assertTrue(actions["Allow pop-ups and new windows"].isChecked())
        self.assertFalse(actions["Clear settings for this site"].isEnabled())
        actions["Allow autoplay with sound"].setChecked(False)
        actions["Allow pop-ups and new windows"].setChecked(False)
        menu.close()
        self.wait(lambda: not tab.page().isLoading())
        settings = tab.page().settings()
        self.assertTrue(webengine.option(settings, "media_needs_gesture"))
        self.assertFalse(webengine.option(settings, "scripts_open_windows"))
        self.assertEqual(
            self.store.changed_keys("127.0.0.1"),
            {"autoplay": "block", "popups": "block"},
        )
        # Internal pages have no site menu.
        self.window._new_tab(url="steed:memory")
        self.window.site_menu = None
        self.window._show_site_menu()
        self.assertIsNone(self.window.site_menu)

    def test_permissions_page_lists_and_resets_sites(self):
        from ui.site_permissions import SitePermissionsDialog, describe_site_settings

        self.store.set("a.example", "javascript", "block")
        self.store.set("a.example", "zoom", 1.25)
        self.store.set("b.example", "popups", "block")
        self.assertEqual(
            describe_site_settings(self.store.sites["a.example"]),
            "JavaScript blocked · zoom 125%",
        )
        tab = self.window._new_tab(url="steed:permissions", return_tab=True)
        self.wait(lambda: tab.workspace_controller is not None)
        dialog = tab.workspace_controller.source
        self.assertIsInstance(dialog, SitePermissionsDialog)
        sites = dialog.sites
        self.assertEqual(
            [sites.item(row, 0).text() for row in range(sites.rowCount())],
            ["a.example", "b.example"],
        )
        sites.selectRow(0)
        dialog.reset_site()
        self.assertNotIn("a.example", self.store.sites)
        self.assertEqual(sites.rowCount(), 1)

    def test_incognito_settings_stay_in_memory(self):
        registry = self.window.profile_manager.registry
        private = registry.site_settings("search")
        self.assertIsNone(private.path)
        private.set("127.0.0.1", "zoom", 1.5)
        self.assertFalse(
            any(Path(self.directory.name).rglob("site_settings.json"))
            and json.loads(
                next(Path(self.directory.name).rglob("site_settings.json")).read_text()
            )["sites"]
        )
        registry._create_search_profile()
        self.assertEqual(registry.site_settings("search").get("127.0.0.1")["zoom"], 1.0)


if __name__ == "__main__":
    unittest.main()
