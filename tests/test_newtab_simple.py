"""The simple new tab page, its photos, hidden bars and the tab hover card."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import (
    QBuffer,
    QByteArray,
    QCoreApplication,
    QEvent,
    QIODevice,
    QPoint,
    Qt,
    QUrl,
)
from PyQt6.QtGui import QColor, QImage
from qt_test_support import WAIT_SECONDS, application, keep_directory

import services.newtab_photo as photos
import webengine
from browser_window import BrowserWindow


def jpeg():
    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(0x3366AA)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG")
    return bytes(data)


class Picsum(BaseHTTPRequestHandler):
    """Stands in for Lorem Picsum's list and image addresses."""

    asked = []

    def do_GET(self):
        Picsum.asked.append(self.path)
        if self.path.startswith("/list"):
            body = json.dumps(
                [
                    {
                        "id": "7",
                        "author": "Ada <B>",
                        "url": "https://unsplash.com/photos/x",
                    }
                ]
            ).encode()
            kind = "application/json"
        else:
            body, kind = jpeg(), "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SimpleNewTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Picsum)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.patches = [
            patch("pathlib.Path.home", return_value=Path(self.directory.name)),
            patch.object(photos, "LIST_URL", self.origin + "/list?page={page}"),
            patch.object(photos, "IMAGE_URL", self.origin + "/id/{id}.jpg"),
        ]
        for item in self.patches:
            item.start()
        Picsum.asked = []
        self.window = BrowserWindow()
        self.window.resize(1100, 700)
        self.window.show()
        self.pages = self.window.profile_manager.registry.start_pages

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def loaded(self, tab):
        """Wait until the tab's page has committed and finished loading."""
        self.wait(
            lambda: (
                tab.history().count() > 0
                and tab.title() == "New Tab"
                and not tab.page().isLoading()
            )
        )

    def settle(self, seconds=0.5):
        """Let the new window finish showing (activating it closes popups)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def run_in(self, page, script):
        found = []
        webengine.run_script(page, script, found.append, world=webengine.PAGE_WORLD)
        self.wait(lambda: found)
        return found[0]

    def js(self, tab, script):
        found = []
        webengine.run_script(
            tab.page(), script, found.append, world=webengine.PAGE_WORLD
        )
        self.wait(lambda: found)
        return found[0]

    def test_the_name_an_empty_search_box_and_a_photo_that_changes(self):
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: tab.title() == "New Tab" and not tab.page().isLoading())
        self.assertEqual(
            self.js(tab, "document.querySelector('.wordmark').textContent"), "Steed"
        )
        self.assertEqual(
            self.js(tab, "document.querySelector('#query').placeholder"), ""
        )
        self.assertTrue(
            self.js(tab, "!!document.querySelector('.corner a[href*=customize]')")
        )
        self.assertEqual(
            self.js(tab, "document.querySelectorAll('header, footer, .card').length"), 0
        )
        # The first photo arrives in the background; the open new tab shows it.
        self.wait(lambda: self.pages.photos.current() is not None)
        self.wait(
            lambda: (
                not tab.page().isLoading()
                and self.js(tab, "!!document.querySelector('.credit')")
            )
        )
        credit = self.js(tab, "document.querySelector('.credit').textContent")
        self.assertEqual(credit, "Photo: Ada <B> · Unsplash")
        self.assertTrue(self.js(tab, "document.body.classList.contains('scrim')"))
        html = self.pages.render("global", "newtab").decode()
        self.assertIn("data:image/jpeg;base64,", html)
        self.assertNotIn("Ada <B>", html)  # Escaped.
        # Not due again for 6 hours; with "every new tab", always due.
        self.assertFalse(self.pages.photos.due("6h"))
        self.assertTrue(self.pages.photos.due("new_tab"))
        asked = len(Picsum.asked)
        self.pages.photos.refresh("6h")
        self.assertEqual(len(Picsum.asked), asked)

    def test_a_colour_instead_and_the_classic_page(self):
        settings = self.pages.state("global")["settings"]
        self.pages.configure(
            "global", {**settings, "background": "color", "color": "#112233"}
        )
        html = self.pages.render("global", "newtab").decode()
        self.assertIn("--newtab-color: #112233", html)
        self.assertIn('class="scrim"', html)  # Light text on a dark colour.
        self.assertEqual(Picsum.asked, [])  # No photo asked for.
        self.pages.configure(
            "global", {**settings, "background": "color", "color": "#f0f0f0"}
        )
        self.assertIn('<body class="">', self.pages.render("global", "newtab").decode())
        with self.assertRaises(ValueError):
            self.pages.configure("global", {**settings, "color": "red"})
        self.pages.configure("global", {**settings, "layout": "classic"})
        self.assertIn("Your space.", self.pages.render("global", "newtab").decode())

    def test_bars_hidden_by_default_and_the_tab_card(self):
        window = self.window
        self.assertTrue(window.menuBar().isHidden())
        self.assertTrue(window.status_bar.isHidden())
        window.status_bar.showMessage("Saved the page.")
        label = window.status_overlay.label
        self.assertTrue(label.isVisible())
        self.assertEqual(label.text(), "Saved the page.")
        window.status_bar.clearMessage()
        self.assertFalse(label.isVisible())
        window.status_bar.showMessage("Loading... 40%")  # Routine: not shown.
        self.assertFalse(label.isVisible())
        window.status_bar.showMessage("Saved again.")
        window.status_overlay.timer.timeout.emit()  # Gone after a while.
        self.assertFalse(label.isVisible())
        window._set_bar("status_bar", True)
        window.status_bar.showMessage("Shown in the bar.")
        self.assertFalse(label.isVisible())

        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: not tab.page().isLoading())
        window._show_tab_preview(window.tabs.index(tab), QPoint(10, 10))
        card = window.tab_preview
        self.assertTrue(card.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        from ui.tab_preview import WIDTH

        self.assertEqual(card.card.width(), WIDTH + 20)
        window.config.set("tab_card_fixed_width", False)
        window._show_tab_preview(window.tabs.index(tab), QPoint(10, 10))
        self.assertLess(card.card.width(), WIDTH + 20)

    def test_the_corner_panel_changes_the_page_and_stays_open(self):
        pages, window = self.pages, self.window
        html = pages.render("global", "newtab").decode()
        self.assertIn('<details class="customize">', html)
        self.assertIn("steed://action/newtab-set?background=color", html)
        self.assertIn("steed://action/newtab-photo", html)
        self.assertIn("steed://action/customize", html)
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.loaded(tab)
        window._start_page_action(
            tab, QUrl("steed://action/newtab-set?background=color&color=%231f3a5f")
        )
        settings = pages.state("global")["settings"]
        self.assertEqual(
            (settings["background"], settings["color"]), ("color", "#1f3a5f")
        )
        # The page that comes back after a choice keeps the panel open, once.
        html = pages.render("global", "newtab").decode()
        self.assertIn('<details class="customize" open>', html)
        self.assertIn('class="swatch on" style="background:#1f3a5f"', html)
        self.assertIn(
            '<details class="customize">', pages.render("global", "newtab").decode()
        )
        # Anything the panel does not offer is refused and changes nothing.
        for bad in (
            "background=file",
            "color=red",
            "image=data:x",
            "html=%3Cscript%3E",
            "match_theme=yes",
        ):
            window._start_page_action(tab, QUrl("steed://action/newtab-set?" + bad))
            self.assertEqual(pages.state("global")["settings"], settings, bad)
        with self.assertRaises(ValueError):
            pages.set_from_page("global", [])
        # Custom HTML pages cannot use it.
        pages.configure("global", settings, "<p>mine</p>")
        window._start_page_action(tab, QUrl("steed://action/newtab-set?layout=classic"))
        self.assertEqual(pages.state("global")["settings"]["layout"], "simple")

    def test_browser_colours_follow_the_photo(self):
        self.assertEqual(photos.theme_color(QImage()), None)
        blue = photos.theme_color(QImage.fromData(jpeg()))
        self.assertTrue(QColor(blue).blue() > QColor(blue).red() + 60, blue)
        grey = QImage(20, 20, QImage.Format.Format_RGB32)
        grey.fill(0x808080)
        self.assertEqual(photos.theme_color(grey), "#808080")

        window = self.window
        plain = window.address_bar.colors["toolbar"]
        tab = window._new_tab(url="steed:newtab", return_tab=True)  # Asks for one.
        self.wait(lambda: self.pages.photos.current() is not None)
        self.app.processEvents()
        tint = self.pages.theme_tint("global")
        self.assertIsNotNone(tint)
        toolbar = QColor(window.address_bar.colors["toolbar"])
        self.assertNotEqual(toolbar.name(), plain)
        self.assertGreater(toolbar.blue(), toolbar.red())
        self.assertNotEqual(window.address_bar.colors["accent"], "#355ed6")
        # Turned off in the panel: the usual colours come back.
        self.loaded(tab)
        window._start_page_action(tab, QUrl("steed://action/newtab-set?match_theme=0"))
        self.assertIsNone(self.pages.theme_tint("global"))
        self.assertEqual(window.address_bar.colors["toolbar"], plain)
        # A plain colour background never tints the browser.
        settings = self.pages.state("global")["settings"]
        self.pages.configure(
            "global", {**settings, "match_theme": True, "background": "color"}
        )
        self.assertIsNone(self.pages.theme_tint("global"))

    def test_new_photo_now_reloads_the_open_new_tab(self):
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: self.pages.photos.current() is not None)
        fetched = self.pages.photos.current()["fetched"]
        self.loaded(tab)
        self.wait(lambda: self.js(tab, "!!document.querySelector('.credit')"))
        asked = len(Picsum.asked)
        self.js(tab, "window.steedMark = 1")
        self.window._start_page_action(tab, QUrl("steed://action/newtab-photo"))
        self.wait(lambda: len(Picsum.asked) >= asked + 2)
        self.wait(
            lambda: (
                not tab.page().isLoading()
                and self.js(tab, "window.steedMark === undefined")
            )
        )
        self.assertGreater(self.pages.photos.current()["fetched"], fetched)
        # The reloaded page keeps the panel open where the choice was made.
        self.loaded(tab)
        self.assertTrue(self.js(tab, "document.querySelector('.customize').open"))

    def test_liquid_glass_mode(self):
        window, pages = self.window, self.pages
        styles = window.profile_manager.registry.page_style
        self.assertFalse(styles.get("global")["glass"])
        self.assertFalse(window.address_bar.colors["glass"])
        self.assertNotIn("qlineargradient", window.address_bar.styleSheet())
        html = pages.render("global", "newtab").decode()
        self.assertNotIn("steed-refract", html)
        self.assertIn("steed://action/newtab-set?glass=1", html)

        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.loaded(tab)
        window._start_page_action(tab, QUrl("steed://action/newtab-set?glass=1"))
        self.assertTrue(styles.get("global")["glass"])
        self.assertTrue(window.glass_action.isChecked())
        self.assertTrue(window.address_bar.colors["glass"])
        self.assertIn("qlineargradient", window.address_bar.styleSheet())
        self.assertTrue(window.tab_bar.chrome["glass"])
        self.assertRegex(window.tab_bar.chrome["glow"], "^#[0-9a-f]{6}$")
        page = window.tab_bar.surface.page
        found = []
        self.wait(
            lambda: (
                webengine.run_script(
                    page,
                    "document.documentElement.classList.contains('glass')",
                    found.append,
                    world=webengine.PAGE_WORLD,
                )
                or (found and found[-1])
            )
        )
        window._show_tab_preview(window.tabs.index(tab), QPoint(10, 10))
        self.assertIn("rgba(", window.tab_preview.styleSheet())
        # The new tab comes back in glass, with the panel still open.
        self.loaded(tab)
        self.wait(lambda: self.js(tab, "document.body.classList.contains('glass')"))
        self.assertTrue(self.js(tab, "document.querySelector('.customize').open"))
        self.assertTrue(self.js(tab, "!!document.getElementById('steed-refract')"))
        self.assertIn(
            "blur(",
            self.js(
                tab,
                "getComputedStyle(document.querySelector('.search')).backdropFilter",
            ),
        )
        # The View menu turns it off again.
        window.glass_action.trigger()
        self.assertFalse(styles.get("global")["glass"])
        self.assertFalse(window.tab_bar.chrome["glass"])
        self.assertIn('<body class="', pages.render("global", "newtab").decode())
        self.assertNotIn(
            "glass", pages.render("global", "newtab").decode().split("<body")[1][:30]
        )

    def test_dark_mode_tint_reaches_the_page_and_is_visible(self):
        styles = self.window.profile_manager.registry.page_style
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: self.pages.photos.current() is not None)
        styles.save("global", {**styles.get("global"), "theme": "dark"})
        self.loaded(tab)
        toolbar = QColor(self.window.address_bar.colors["toolbar"])
        self.assertGreater(toolbar.blue() - toolbar.red(), 20)  # Not just grey.
        html = self.pages.render("global", "newtab").decode()
        self.assertIn("html:root[data-theme] { --surface:", html)
        self.wait(
            lambda: (
                self.js(
                    tab,
                    "getComputedStyle(document.documentElement)"
                    ".getPropertyValue('--surface').trim()",
                )
                == self.window.address_bar.colors["toolbar"]
            )
        )

    def test_customize_can_live_inside_the_menu(self):
        pages = self.pages
        html = pages.render("global", "newtab").decode()
        self.assertIn('<summary class="icon" title="Customize New Tab"', html)
        self.assertIn("steed://action/newtab-set?gear_in_menu=1", html)
        pages.set_from_page("global", [("gear_in_menu", "1")])
        self.assertTrue(pages.state("global")["settings"]["gear_in_menu"])
        html = pages.render("global", "newtab").decode()  # Opened by the choice.
        menu = html.split('<details class="browser-menu" open>', 1)[1]
        self.assertTrue(menu.startswith("<summary>☰ Browser menu</summary><div>"))
        self.assertIn('<details class="customize" open><summary class="row">', menu)
        self.assertNotIn('class="icon" title="Customize New Tab"', html)
        with self.assertRaises(ValueError):
            pages.set_from_page("global", [("gear_in_menu", "maybe")])
        # Without the ☰ menu the gear stays its own button.
        styles = self.window.profile_manager.registry.page_style
        styles.save("global", {**styles.get("global"), "newtab_menu": False})
        html = pages.render("global", "newtab").decode()
        self.assertIn('<summary class="icon" title="Customize New Tab"', html)
        self.assertNotIn("gear_in_menu=", html)

    def test_menus_are_compact_and_in_the_browser_colours(self):
        from ui.html_menu import MAX_HEIGHT, WIDTH

        self.settle()

        window = self.window
        colors = window.address_bar.colors
        self.assertIn(colors["toolbar"], window.browser_menu.styleSheet())
        self.assertIn(colors["toolbar"], window._layout_menus["general"].styleSheet())
        popup = window._show_html_menu(
            window._layout_menus["tabs"], window.mapToGlobal(QPoint(300, 120))
        )
        self.assertEqual(popup.width(), WIDTH)
        self.assertLessEqual(popup.height(), MAX_HEIGHT)
        self.assertEqual(popup._colors()["menu"], colors["menu"])
        script = (
            "getComputedStyle(document.documentElement)"
            ".getPropertyValue('--surface').trim()"
        )
        self.wait(lambda: self.run_in(popup.surface.page, script) == colors["menu"])
        popup.close()

    def test_a_photo_arriving_leaves_a_busy_new_tab_alone(self):
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: self.pages.photos.current() is not None)
        self.loaded(tab)
        self.wait(lambda: self.js(tab, "!!document.querySelector('.credit')"))
        self.js(
            tab,
            "window.steedMark = 1; document.querySelector('.browser-menu').open = true; true",
        )
        self.pages.photos.changed.emit(True)
        end = time.monotonic() + 1
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.js(tab, "window.steedMark"), 1)  # Not reloaded.
        self.js(tab, "document.querySelector('.browser-menu').open = false; true")
        self.pages.photos.changed.emit(True)
        self.wait(lambda: self.js(tab, "window.steedMark === undefined"))

    def test_every_browser_colour_can_be_fixed_and_kept(self):
        window, pages = self.window, self.pages
        styles = window.profile_manager.registry.page_style
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: pages.photos.current() is not None)
        self.loaded(tab)
        automatic = dict(window.automatic_colors)
        from services.page_style import CHROME_FIELDS

        self.assertLessEqual(set(CHROME_FIELDS), set(automatic))
        # Fix a few: they apply everywhere; the rest still follow the photo.
        chosen = {
            "strip": "#102030",
            "accent": "#ff7a59",
            "menu": "#203040",
            "card": "#304050",
        }
        styles.save("global", {**styles.get("global"), "chrome": chosen})
        colors = window.address_bar.colors
        self.assertEqual(colors["accent"], "#ff7a59")
        self.assertEqual(colors["toolbar"], automatic["toolbar"])
        self.assertEqual(window.automatic_colors["accent"], automatic["accent"])
        chrome = window.tab_bar.chrome
        self.assertEqual(chrome["palette"]["strip"], "#102030")
        self.assertEqual(chrome["chosen"], sorted(chosen))
        self.assertIn("#203040", window.browser_menu.styleSheet())
        window._show_tab_preview(window.tabs.index(tab), QPoint(10, 10))
        self.assertIn("#304050", window.tab_preview.styleSheet())
        popup = window._show_html_menu(
            window._layout_menus["tabs"], window.mapToGlobal(QPoint(300, 120))
        )
        self.assertEqual(popup._colors()["menu"], "#203040")
        popup.close()
        self.assertIn("--accent: #ff7a59", pages.render("global", "newtab").decode())
        # Keep these colours: all of them fixed, so the photo no longer moves them.
        kept = {key: window.automatic_colors[key] for key in CHROME_FIELDS}
        styles.save("global", {**styles.get("global"), "chrome": kept})
        before = dict(window.address_bar.colors)
        pages.set_from_page("global", [("match_theme", "0")])
        self.assertNotEqual(window.automatic_colors["toolbar"], kept["toolbar"])
        for key in CHROME_FIELDS:
            self.assertEqual(window.address_bar.colors.get(key), before.get(key), key)
        from services.page_style import clean_style

        with self.assertRaises(ValueError):
            clean_style({**styles.get("global"), "chrome": {"toolbar": "red"}})
        with self.assertRaises(ValueError):
            clean_style({**styles.get("global"), "chrome": {"page": "#000000"}})

    def test_menu_panels_open_beside_and_in_glass(self):
        from PyQt6.QtCore import QRect

        self.settle()

        window = self.window
        screen = window.screen().availableGeometry()
        # A dropdown at the right edge: the panel opens to its left.
        beside = QRect(screen.right() - 220, screen.top() + 60, 200, 300)
        popup = window._show_html_menu(
            window._layout_menus["tabs"],
            QPoint(beside.right(), beside.top() + 30),
            beside=beside,
        )
        self.assertFalse(popup.geometry().intersects(beside))
        self.assertLess(popup.x(), beside.left())
        self.assertFalse(popup.glass)
        popup.close()
        window._set_bar("glass", True)
        self.settle()  # Switching reloads Steed's pages, which closes popups.
        popup = window._show_html_menu(
            window._layout_menus["tabs"], window.mapToGlobal(QPoint(300, 120))
        )
        self.assertTrue(popup.glass)
        self.assertTrue(
            popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        )
        self.assertTrue(popup.surface.transparent)
        self.wait(lambda: popup.surface._last_payload)
        self.assertIn('"glass": true', popup.surface._last_payload)
        popup.close()
        self.assertTrue(
            window.browser_menu.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground
            )
        )
        self.assertIn("border-radius: 12px", window.browser_menu.styleSheet())

    def test_steed_pages_follow_the_photo_unless_turned_off(self):
        styles = self.window.profile_manager.registry.page_style
        self.assertNotIn("--bg:", styles.css("global"))  # No photo yet.
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: self.pages.photos.current() is not None)
        self.loaded(tab)
        css = styles.css("global")
        self.assertIn("--bg:", css)
        self.assertIn("--surface:", css)
        self.assertIn("--accent:", css)
        # Hand-picked page colours and custom CSS still come after (win).
        styles.save(
            "global",
            {**styles.get("global"), "colors": {"bg": "#123456"}, "css": "p{}"},
        )
        css = styles.css("global")
        self.assertLess(css.index("--bg: #"), css.index("--bg: #123456"))
        self.assertTrue(css.endswith("p{}"))
        styles.save("global", {**styles.get("global"), "pages_follow_photo": False})
        self.assertNotIn("--surface:", styles.css("global"))

    def test_glass_bars_are_frosted_over_the_picture(self):
        window = self.window
        styles = window.profile_manager.registry.page_style
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: self.pages.photos.current() is not None)
        self.loaded(tab)
        self.assertNotIn("frost", window.tab_bar.chrome)  # Not without glass.
        self.assertNotIn("border-image", window.address_bar.styleSheet())
        window._set_bar("glass", True)
        frost = window.tab_bar.chrome["frost"]
        self.assertTrue(frost.startswith("data:image/jpeg;base64,"))
        sheet = window.address_bar.styleSheet()
        self.assertIn("border-image", sheet)
        path = sheet.split('url("', 1)[1].split('")', 1)[0]
        self.assertTrue(Path(path).is_file())
        self.assertEqual(Path(path).parent, window.config.config_dir / "frost")
        # A strip or toolbar colour chosen by hand wins over the picture.
        styles.save(
            "global", {**styles.get("global"), "chrome": {"toolbar": "#334455"}}
        )
        self.assertNotIn("frost", window.tab_bar.chrome)
        self.assertNotIn("border-image", window.address_bar.styleSheet())

    def test_sub_menus_open_beside_their_panel(self):
        window = self.window
        self.settle()
        panel = window._show_html_menu(
            window._layout_menus["general"], window.mapToGlobal(QPoint(40, 60))
        )
        self.wait(lambda: panel.surface._last_payload)
        token = next(
            key for key, action in panel.actions.items() if action.menu() is not None
        )
        sub = panel.actions[token].menu()
        panel.command(
            {
                "context": panel.context,
                "sequence": 1,
                "action": "hover",
                "id": token,
                "x": 10,
                "y": 40,
            }
        )
        child = panel.child
        self.assertIsNotNone(child)
        self.assertIs(child.stack[0], sub)
        self.assertIs(child.parent_menu, panel)
        self.assertFalse(panel.closed)
        self.assertFalse(child.geometry().intersects(panel.geometry()))
        panel.close()  # Closing the panel closes its sub-menu too.
        self.assertTrue(child.closed)


if __name__ == "__main__":
    unittest.main()
