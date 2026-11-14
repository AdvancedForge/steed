"""New Tabs change in place (a new photo, its colours) instead of reloading;
Steed reloads its own pages only when needed, never mid-load, and never in a
loop; closing a tab leaves Steed's pages alone unless they list closed tabs."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QColor, QImage
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow


def jpeg(color, folder):
    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    path = Path(folder) / f"{color.strip('#')}.jpg"
    image.save(str(path), "JPEG")
    return path.read_bytes()


class LiveNewTabTests(unittest.TestCase):
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
        self.pages = self.window.profile_manager.registry.start_pages
        self.navigations = []
        original = self.window.navigation.navigate
        self.navigate = patch.object(
            self.window.navigation,
            "navigate",
            side_effect=lambda tab, url: (
                self.navigations.append(url.toString()),
                original(tab, url),
            ),
        )
        self.navigate.start()

    def tearDown(self):
        self.navigate.stop()
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

    def js(self, tab, script):
        out = []
        tab.page().runJavaScript(script, out.append)
        self.wait(lambda: out)
        return out[0]

    def newtab(self):
        tab = self.window._new_tab(return_tab=True)
        self.wait(
            lambda: tab.url().toString() == "steed://newtab/" and not tab.is_loading
        )
        return tab

    def settle(self):
        """Until no Steed reload is due or running, and it stays so."""

        def quiet():
            timer = getattr(self.window, "_steed_pages_timer", None)
            return (timer is None or not timer.isActive()) and not any(
                tab.is_loading or getattr(tab, "reload_when_loaded", None)
                for tab in self.window.tabs
            )

        for _ in range(3):
            self.wait(quiet)
            end = time.monotonic() + 0.25
            while time.monotonic() < end:
                self.app.processEvents()
                time.sleep(0.01)

    def use_simple(self):
        settings = {
            **self.pages.state("global")["settings"],
            "layout": "simple",
            "background": "photo",
        }
        self.pages.configure("global", settings)

    def test_a_new_photo_changes_open_new_tabs_in_place(self):
        self.use_simple()
        first, second = self.newtab(), self.newtab()
        # The layout change's own reload (coalesced) finishes first.
        self.settle()
        for tab in (first, second):
            self.assertTrue(self.js(tab, "!!document.getElementById('steed-live')"))
        self.js(first, "document.getElementById('query').value = 'half typed'; true")
        self.navigations.clear()
        started = []
        for tab in (first, second):
            tab.loadStarted.connect(lambda: started.append(1))
        photos = self.pages.photos
        photos._saved(
            jpeg("#2266cc", self.directory.name),
            {"id": "t1", "author": "Tess", "url": "https://photos.example/t1"},
        )
        credit = "document.getElementById('steed-credit').textContent"
        live = "document.getElementById('steed-live').textContent"
        for tab in (first, second):
            self.wait(lambda tab=tab: "Tess" in (self.js(tab, credit) or ""))
            self.assertIn("--newtab-image", self.js(tab, live))
        # Nothing reloaded: what was typed is still there.
        self.assertEqual(self.navigations, [])
        self.assertEqual(started, [])
        self.assertEqual(
            self.js(first, "document.getElementById('query').value"), "half typed"
        )

    def test_steed_reloads_wait_for_a_load_and_stop_a_loop(self):
        window = self.window
        tab = self.newtab()
        self.navigations.clear()
        # Mid-load: the reload waits for the load to end, then happens once.
        tab.is_loading = True
        window._reload_steed_page(tab, "test")
        window._reload_steed_page(tab, "test")
        self.assertEqual(self.navigations, [])
        tab.is_loading = False
        window._on_load_finished(tab, True)
        self.wait(lambda: len(self.navigations) == 1)
        # A runaway is cut off after a few and written down.
        for _ in range(10):
            window._reload_steed_page(tab, "runaway")
            tab.is_loading = False
        self.assertEqual(len(self.navigations), window.STEED_RELOADS[0])
        log = window.config.config_dir / "reload_loops.log"
        self.assertIn("runaway", log.read_text())

    def test_closing_a_tab_reloads_only_pages_listing_closed_tabs(self):
        window = self.window
        self.newtab()
        extra = window._new_tab(url="about:blank", return_tab=True)
        self.navigations.clear()
        window._close_tab(window.tabs.index(extra))
        self.app.processEvents()
        time.sleep(0.2)
        self.app.processEvents()
        self.assertEqual(self.navigations, [])  # The simple New Tab lists none.
        settings = {**self.pages.state("global")["settings"], "layout": "classic"}
        settings["modules"] = ["search", "recent"]
        self.pages.configure("global", settings)
        self.assertTrue(window._closed_tabs_shown("global"))

    def test_open_steed_pages_follow_motion_changes_live(self):
        window = self.window
        window.config.set("visual_effects", "minimal")
        window.apply_dark_mode()
        strip = window.tab_bar.surface
        self.wait(lambda: '"effects": "minimal"' in (strip._last_payload or ""))

    def test_settings_and_agents_show_steeds_mark_not_a_letter(self):
        root = Path(__file__).parents[1] / "ui" / "web"
        for name in ("settings.html", "agents.html"):
            page = (root / name).read_text()
            self.assertNotIn('aria-hidden="true">s</span>', page)
            self.assertIn('<span class="brand-mark" aria-hidden="true"><svg', page)


if __name__ == "__main__":
    unittest.main()
