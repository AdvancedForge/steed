"""Lumen, Steed's look: the page as a card in light coloured by the site, the
motion that goes with it, and the Motion and light setting that turns it down."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtGui import QColor, QFontDatabase, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from ui import lumen
from webengine import UI_FONT


def solid_icon(color):
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


class LightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def colors(self, dark=True):
        return {"toolbar": "#2b2f3a", "accent": "#8caaff", "dark": dark}

    def test_the_field_is_lit_by_the_accent_and_the_site(self):
        light = lumen.Light.of(self.colors(), "#e0302a")
        self.assertEqual(len(light.glows), 3)
        accent, site, below = (glow[3] for glow in light.glows)
        self.assertGreater(site.red(), site.blue())  # The site's red.
        self.assertGreater(accent.blue(), accent.red())  # The accent's blue.
        self.assertTrue(all(0 < glow[3].alpha() < 255 for glow in light.glows))
        # Darker than the toolbar in dark mode: the card stands out.
        self.assertLess(light.base.lightness(), QColor("#2b2f3a").lightness())
        # Without a site, the second glow turns the accent's hue a little.
        plain = lumen.Light.of(self.colors())
        self.assertNotEqual(plain.glows[1][3].hue(), plain.glows[0][3].hue())

    def test_light_flows_between_fields(self):
        start = lumen.Light.of(self.colors(), "#e0302a")
        end = lumen.Light.of(self.colors(), "#20a040")
        self.assertTrue(start.blend(end, 0).same(start))
        self.assertTrue(start.blend(end, 1).same(end))
        middle = start.blend(end, 0.5).glows[1][3]
        self.assertTrue(
            min(start.glows[1][3].green(), end.glows[1][3].green())
            <= middle.green()
            <= max(start.glows[1][3].green(), end.glows[1][3].green())
        )
        flat = lumen.Light.flat(self.colors())
        self.assertTrue(all(glow[3].alpha() == 0 for glow in flat.glows))
        css = start.css()
        self.assertRegex(css["base"], r"^#[0-9a-f]{6}$")
        self.assertRegex(css["glows"][0][3], r"^#[0-9a-f]{8}$")

    def test_a_site_is_known_by_its_icon_colour(self):
        self.assertEqual(QColor(lumen.site_color(solid_icon("#d02020"))).hue(), 0)
        self.assertIsNone(lumen.site_color(solid_icon("#808080")))  # Grey.
        self.assertIsNone(lumen.site_color(QIcon()))

    def test_steeds_typeface_is_bundled_and_used(self):
        self.assertIn(UI_FONT, QFontDatabase.families())
        self.assertEqual(QApplication.font().family(), UI_FONT)
        for family in ("Mona Sans SemiExpanded", "Mona Sans Expanded"):
            self.assertIn(family, QFontDatabase.families())

    def test_the_effects_setting_is_checked(self):
        self.assertEqual(lumen.level({"visual_effects": "minimal"}), "minimal")
        self.assertEqual(lumen.level({"visual_effects": "wild"}), "full")
        self.assertEqual(lumen.level({}), "full")
        from ui.settings_dialog import DEFAULTS, SettingsDialog

        self.assertEqual(DEFAULTS["visual_effects"], "full")
        with self.assertRaises(ValueError):
            SettingsDialog._validate_values({**DEFAULTS, "visual_effects": "wild"})


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
        self.window.resize(1000, 700)
        self.window.show()
        self.wait(lambda: self.window.isVisible())

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

    def margins(self):
        return self.window.content_area.contentsMargins().left()

    def test_the_page_floats_as_a_card_in_the_light(self):
        window = self.window
        self.assertTrue(window._lumen)
        self.assertEqual(self.margins(), lumen.CARD_MARGIN)
        self.assertIsNotNone(window.light)
        self.assertTrue(all(c.isVisible() for c in window._card_corners))
        # The corners sit on the card's corners, above the page.
        card = window._card_rect()
        top_left = window._card_corners[0]
        self.assertEqual(top_left.pos(), card.topLeft())
        # The bars let the light through; the tab strip paints the same field.
        self.assertIn("background: transparent", window.address_bar.styleSheet())
        light = window.tab_bar.chrome.get("light")
        self.assertEqual(light["base"], window._light_animator.goal.css()["base"])
        self.assertEqual(light["size"], [window.width(), window.height()])
        # The window paints it (outside the card).
        picture = window.grab().toImage()
        self.assertEqual(picture.size().isEmpty(), False)

    def test_the_light_takes_the_sites_colour(self):
        window = self.window
        tab = window.tabs[window.current_tab_index]
        tab.setHtml("<title>Red</title>", QUrl("https://red.example/"))
        self.wait(lambda: tab.url().host() == "red.example")
        with patch.object(type(tab), "icon", return_value=solid_icon("#d02020")):
            window._refresh_light(animate=False)
        site = window._light_animator.goal.glows[1][3]
        self.assertEqual(site.hue(), 0)
        self.assertGreater(site.red(), 150)

    def test_switching_tabs_dissolves_and_minimal_does_not(self):
        window = self.window
        window._new_tab(url="about:blank")
        self.wait(lambda: len(window.tabs) == 2)
        window._switch_tab(0)
        self.assertTrue(window.tab_container.findChildren(lumen.Dissolve))
        self.wait(lambda: not window.tab_container.findChildren(lumen.Dissolve))
        window.config.set("visual_effects", "minimal")
        window.apply_dark_mode()
        window._switch_tab(1)
        self.assertFalse(window.tab_container.findChildren(lumen.Dissolve))
        # Flat colours, no hover motion, but still the card.
        self.assertTrue(
            all(glow[3].alpha() == 0 for glow in window._light_animator.goal.glows)
        )
        self.assertFalse(window.address_bar.colors["glide"])
        self.assertFalse(window.tab_bar.chrome["light"]["motion"])
        self.assertEqual(self.margins(), lumen.CARD_MARGIN)

    def test_toolbar_buttons_glide_and_the_field_glows(self):
        bar = self.window.address_bar
        glide = bar.glide
        self.assertIn(bar.reload_btn, glide.levels)
        self.assertIs(glide.field, bar.address_input)
        QApplication.sendEvent(bar.reload_btn, QEvent(QEvent.Type.Enter))
        self.wait(lambda: glide.levels[bar.reload_btn][0] > 0.99)
        QApplication.sendEvent(bar.reload_btn, QEvent(QEvent.Type.Leave))
        self.wait(lambda: glide.levels[bar.reload_btn][0] < 0.01)
        QApplication.sendEvent(bar.address_input, QEvent(QEvent.Type.FocusIn))
        self.wait(lambda: glide.glow.blurRadius() > 20)

    def test_glass_keeps_its_own_look_and_fullscreen_is_edge_to_edge(self):
        window = self.window
        window._enter_fullscreen()
        self.assertEqual(self.margins(), 0)
        self.assertFalse(any(c.isVisible() for c in window._card_corners))
        window._exit_fullscreen()
        self.assertEqual(self.margins(), lumen.CARD_MARGIN)
        styles = window.profile_manager.registry.page_style
        profile = window.profile_manager.current_profile_name
        styles.save(profile, {**styles.get(profile), "glass": True})
        window.apply_dark_mode()
        self.assertFalse(window._lumen)
        self.assertEqual(self.margins(), 0)
        self.assertIsNone(window.light)
        self.assertNotIn("light", window.tab_bar.chrome)

    def test_pages_know_how_much_motion_to_use(self):
        pages = self.window.profile_manager.registry.start_pages
        profile = self.window.profile_manager.current_profile_name
        self.assertIn(b'data-effects="full"', pages.render(profile, "welcome"))
        self.window.config.set("visual_effects", "balanced")
        page = pages.render(profile, "newtab")
        self.assertIn(b'data-effects="balanced"', page)
        self.assertIn(b"Mona Sans Expanded", page)


if __name__ == "__main__":
    unittest.main()
