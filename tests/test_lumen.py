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
        self.assertIsNone(lumen.site_color(solid_icon("#8a8078")))  # Nearly grey.
        self.assertIsNone(lumen.site_color(QIcon()))

    def test_tabs_without_icons_get_steeds_glyphs_not_letters(self):
        from ui.tab_strip import steed_page

        self.assertEqual(steed_page("steed:history"), "history")
        self.assertEqual(steed_page("steed://newtab/"), "newtab")
        self.assertEqual(steed_page("https://steed.example/"), "")
        self.assertEqual(steed_page("steed://<script>/"), "")
        script = (Path(__file__).parents[1] / "ui" / "web" / "tabs.js").read_text()
        self.assertNotIn('classList.add("letter")', script)
        for name in ("newtab", "steed", "settings", "history", "globe"):
            self.assertIn(f"  {name}: '<", script)

    def test_steeds_typeface_is_bundled_and_used(self):
        self.assertIn(UI_FONT, QFontDatabase.families())
        self.assertEqual(QApplication.font().family(), UI_FONT)
        for family in ("Mona Sans SemiExpanded", "Mona Sans Expanded"):
            self.assertIn(family, QFontDatabase.families())

    def test_a_sites_declared_colour_lights_it_unless_it_is_grey(self):
        self.assertEqual(lumen.theme_color("#1652F0"), "#1652f0")
        self.assertEqual(lumen.theme_color("rgb(220, 40, 40)"), "#dc2828")
        for grey in ("#ffffff", "#000000", "#f5f5f5", "#777777", "transparent"):
            self.assertIsNone(lumen.theme_color(grey), grey)
        for junk in (None, "", "not a colour", "#" + "1" * 80):
            self.assertIsNone(lumen.theme_color(junk))

    def test_the_effects_setting_is_checked(self):
        self.assertEqual(lumen.level({"visual_effects": "minimal"}), "minimal")
        self.assertEqual(lumen.level({"visual_effects": "wild"}), "full")
        self.assertEqual(lumen.level({}), "full")
        from ui.settings_dialog import DEFAULTS, SettingsDialog

        self.assertEqual(DEFAULTS["visual_effects"], "full")
        with self.assertRaises(ValueError):
            SettingsDialog._validate_values({**DEFAULTS, "visual_effects": "wild"})


class EditableTests(unittest.TestCase):
    """Every part of Lumen can be changed: by the person on the Style page,
    or by their agent through a reviewed style proposal."""

    def test_lumen_settings_are_checked_and_only_changes_are_kept(self):
        from services.page_style import DEFAULTS, LUMEN_DEFAULTS, clean_style

        style = clean_style({**DEFAULTS, "lumen": {"light": 150, "card_radius": 12}})
        self.assertEqual(style["lumen"], {"light": 150})  # 12 is the default.
        for bad in (
            {"light": 201},
            {"card_margin": -1},
            {"card_radius": 3.5},
            {"font": "comic"},
            {"titles": "tall"},
            {"follow_site": 1},
            {"light_colors": {"left": "red"}},
            {"light_colors": {"middle": "#ffffff"}},
            {"glow": 100},
        ):
            with self.assertRaises(ValueError, msg=bad):
                clean_style({**DEFAULTS, "lumen": bad})
        self.assertEqual(set(LUMEN_DEFAULTS), set(clean_style_fields()))

    def test_the_typeface_is_a_token_every_page_uses(self):
        from services.page_style import DEFAULTS, lumen_css

        css = lumen_css(DEFAULTS)
        self.assertIn('--steed-font: "Mona Sans"', css)
        self.assertIn('--steed-display-font: "Mona Sans Expanded"', css)
        system = lumen_css({**DEFAULTS, "lumen": {"font": "system"}})
        self.assertIn("--steed-font: system-ui", system)
        self.assertNotIn("Mona", system)
        root = Path(__file__).parents[1]
        for name in ("ui/web/lumen.css", "ui/web/tabs.css", "ui/web/menu.css"):
            self.assertIn("var(--steed-font", (root / name).read_text())

    def test_an_agent_patches_only_the_lumen_keys_it_names(self):
        import json

        from services.page_style import DEFAULTS, clean_style
        from start_page_format import parse_page

        current = clean_style(
            {**DEFAULTS, "lumen": {"card_radius": 20, "font": "system"}}
        )
        proposal = parse_page(
            json.dumps(
                {
                    "version": 1,
                    "name": "Brighter",
                    "page": "style",
                    "mode": "theme",
                    "style": {
                        "lumen": {"light": 160, "light_colors": {"base": "#102030"}}
                    },
                }
            )
        )
        result = proposal.candidate(current)["lumen"]
        self.assertEqual(result["card_radius"], 20)
        self.assertEqual(result["font"], "system")
        self.assertEqual(result["light"], 160)
        self.assertEqual(result["light_colors"], {"base": "#102030"})
        with self.assertRaises(ValueError):
            parse_page(
                json.dumps(
                    {
                        "version": 1,
                        "name": "Too bright",
                        "page": "style",
                        "mode": "theme",
                        "style": {"lumen": {"light": 999}},
                    }
                )
            )

    def test_the_style_guide_tells_agents_about_lumen(self):
        from services.page_style import LUMEN_FIELDS, style_guide

        guide = style_guide()
        self.assertEqual(set(guide["lumen"]), set(LUMEN_FIELDS))
        for token in ("--steed-font", "--stride", "--lift"):
            self.assertIn(token, guide["tokens"])
        self.assertIn("#lit", guide["selectors"])


def clean_style_fields():
    from services.page_style import LUMEN_FIELDS

    return LUMEN_FIELDS


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
        # The window paints the light outside the card: the margin beside
        # the card shows the field's base colour, not the old flat toolbar.
        picture = window.grab().toImage()
        ratio = picture.devicePixelRatio()
        x, y = card.left() - 3, card.center().y()
        painted = picture.pixelColor(int(x * ratio), int(y * ratio))
        field = QColor(window.light.base)
        self.assertLess(
            abs(painted.lightness() - field.lightness()), 40, painted.name()
        )

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
        window.config.set("visual_effects", "balanced")
        window.apply_dark_mode()
        window._switch_tab(1)
        self.assertFalse(window.tab_container.findChildren(lumen.Dissolve))
        self.assertTrue(window.address_bar.colors["glide"])  # Still motion.
        window.config.set("visual_effects", "minimal")
        window.apply_dark_mode()
        window._switch_tab(0)
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

    def test_glass_floats_the_card_and_fullscreen_and_compact_are_edge_to_edge(self):
        window = self.window
        window._enter_fullscreen()
        self.assertEqual(self.margins(), 0)
        self.assertFalse(any(c.isVisible() for c in window._card_corners))
        window._exit_fullscreen()
        self.assertEqual(self.margins(), lumen.CARD_MARGIN)
        styles = window.profile_manager.registry.page_style
        profile = window.profile_manager.current_profile_name
        # Liquid glass: the card floats over the frosted photo (the strip has
        # its own frost), not over the light.
        styles.save(profile, {**styles.get(profile), "glass": True})
        window.apply_dark_mode()
        self.assertTrue(window._lumen)
        self.assertEqual(self.margins(), lumen.CARD_MARGIN)
        self.assertTrue(all(g[3].alpha() == 0 for g in window.light.glows))
        self.assertNotIn("light", window.tab_bar.chrome)
        # Compact mode is all page: edge to edge.
        window._set_bar("compact", True)
        self.wait(lambda: self.margins() == 0)

    def test_the_window_follows_the_lumen_settings(self):
        window = self.window
        styles = window.profile_manager.registry.page_style
        profile = window.profile_manager.current_profile_name

        def use(**lumen_values):
            styles.save(profile, {**styles.get(profile), "lumen": lumen_values})
            window.apply_dark_mode()

        use(card_margin=14, card_radius=22, light=0)
        self.assertEqual(self.margins(), 14)
        self.assertEqual(window._card_corners[0].width(), 22)
        self.assertTrue(
            all(g[3].alpha() == 0 for g in window._light_animator.goal.glows)
        )
        use(light_colors={"base": "#102030", "right": "#ff0000"}, follow_site=False)
        goal = window._light_animator.goal
        self.assertEqual(goal.base.name(), "#102030")
        self.assertEqual(goal.glows[1][3].hue(), 0)
        tab = window.tabs[window.current_tab_index]
        with patch.object(type(tab), "icon", return_value=solid_icon("#20c040")):
            window._refresh_light(animate=False)
        self.assertEqual(window._light_animator.goal.glows[1][3].hue(), 0)
        use(card_radius=0)
        self.assertFalse(any(c.isVisible() for c in window._card_corners))
        use(font="system")
        self.assertNotEqual(QApplication.font().family(), UI_FONT)
        use()
        self.assertEqual(QApplication.font().family(), UI_FONT)
        self.assertEqual(self.margins(), lumen.CARD_MARGIN)

    def test_the_light_takes_the_colour_a_site_declares(self):
        window = self.window
        tab = window.tabs[window.current_tab_index]
        tab.setHtml(
            '<title>Blue</title><meta name="theme-color" content="#1652f0">'
            '<meta name="theme-color" media="(min-width: 99999px)" content="#ff0000">',
            QUrl("https://blue.example/"),
        )
        self.wait(lambda: getattr(tab, "theme_color", None) == "#1652f0")
        self.wait(lambda: window._light_animator.goal.glows[1][3].blue() > 150)
        # Leaving for a page without one: back to its icon (none here).
        tab.setHtml("<title>Plain</title>", QUrl("https://plain.example/"))
        self.wait(lambda: tab.title() == "Plain" and tab.theme_color is None)

    def test_split_view_and_docked_panels_are_cards_too(self):
        window = self.window
        first = window.tabs[window.current_tab_index]
        second = window._new_tab(url="about:blank", return_tab=True)
        window._switch_tab(window.tabs.index(first))
        window.show_beside(second)
        self.wait(lambda: second.isVisible() and len(window._cards()) == 2)
        left, right = window._cards()
        self.assertGreaterEqual(right.left() - left.right(), lumen.CARD_MARGIN)
        self.assertEqual(sum(c.isVisible() for c in window._card_corners), 8)
        window.close_split_view()
        self.wait(lambda: len(window._cards()) == 1)
        self.assertEqual(sum(c.isVisible() for c in window._card_corners), 4)
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QDockWidget, QLabel

        dock = QDockWidget("Panel", window)
        dock.setWidget(QLabel("panel"))
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.show()
        self.wait(lambda: len(window._cards()) == 2)
        self.assertEqual(dock.widget().contentsMargins().right(), lumen.CARD_MARGIN)
        dock.setFloating(True)
        self.wait(lambda: len(window._cards()) == 1)

    def test_steed_links_on_its_pages_are_not_load_errors(self):
        from PyQt6.QtWebEngineCore import QWebEnginePage

        window = self.window
        tab = window._new_tab(return_tab=True)
        self.wait(
            lambda: tab.url().toString() == "steed://newtab/" and not tab.is_loading
        )
        with patch.object(window, "_start_page_action") as action:
            accepted = tab.page().acceptNavigationRequest(
                QUrl("steed://action/newtab-photo"),
                QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
                True,
            )
            self.wait(lambda: action.called)
        self.assertFalse(accepted)
        # The engine reports the stopped load as failed: not an error here…
        window._on_load_finished(tab, False)
        self.assertNotEqual(window.status_bar.currentMessage(), "Error loading page")
        # …but the next real failure is reported.
        window._on_load_finished(tab, False)
        self.assertEqual(window.status_bar.currentMessage(), "Error loading page")

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
