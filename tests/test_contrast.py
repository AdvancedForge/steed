"""Colours from a photo (or a toolbar colour) stay readable: text 4.5:1,
icons and large text 3:1, on Steed's pages and in the browser's bars, for
pale, dark, vivid and grey photos alike."""

import colorsys
import re
import unittest
from pathlib import Path

from qt_test_support import application

from ui.chrome_style import browser_colors, page_colors, tint_colors
from ui.contrast import (
    DARK_TEXT,
    LARGE,
    LIGHT_TEXT,
    TEXT,
    contrast,
    hsl,
    luminance,
    readable,
    text_on,
)

ROOT = Path(__file__).resolve().parents[1]


def tints():
    """Photo colours across hues, lightness and colourfulness, and greys."""
    found = []
    for hue in range(0, 360, 40):
        for lightness in (0.12, 0.35, 0.55, 0.75, 0.92):
            for saturation in (0.15, 0.6, 1.0):
                red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
                found.append(
                    "#" + "".join(f"{round(c * 255):02x}" for c in (red, green, blue))
                )
    return found + ["#101010", "#808080", "#f0f0f0", "#ffffcc", "#00004c"]


class MeasureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_wcag_numbers(self):
        self.assertAlmostEqual(contrast("#000000", "#ffffff"), 21, places=1)
        self.assertAlmostEqual(contrast("#777777", "#ffffff"), 4.48, places=2)
        self.assertEqual(luminance("#000000"), 0)
        self.assertEqual(contrast("#355ed6", "#355ed6"), 1)

    def test_readable_moves_lightness_only_as_far_as_needed(self):
        pale = "#f0e68c"  # Khaki on white: about 1.2:1.
        found = readable(pale, "#ffffff", TEXT)
        self.assertGreaterEqual(contrast(found, "#ffffff"), TEXT)
        self.assertLess(contrast(found, "#ffffff"), TEXT + 0.3)
        self.assertAlmostEqual(hsl(found)[0], hsl(pale)[0], delta=0.02)
        self.assertEqual(readable("#202b40", "#ffffff"), "#202b40")  # Already fine.
        light = readable("#1a2a6c", "#101418", TEXT)  # Dark on dark: lighter.
        self.assertGreater(luminance(light), luminance("#1a2a6c"))

    def test_text_on_picks_the_one_that_reads(self):
        self.assertEqual(text_on("#355ed6"), LIGHT_TEXT)
        self.assertEqual(text_on("#8caaff"), DARK_TEXT)
        self.assertEqual(text_on("#ffd23f"), DARK_TEXT)


class PaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_steeds_pages_read_whatever_the_photo(self):
        failures = []
        for dark in (False, True):
            ink = "#e8edf6" if dark else "#202b40"
            for tint in tints():
                colors = page_colors(tint, dark)
                checks = [
                    (ink, "bg", 7),
                    (ink, "surface", 7),
                    (ink, "soft", TEXT),
                    (colors["muted"], "bg", TEXT),
                    (colors["muted"], "surface", TEXT),
                    (colors["accent"], "bg", TEXT),
                    (colors["accent"], "surface", TEXT),
                    (colors["accent"], "soft", LARGE),
                    (colors["on-accent"], "accent", TEXT),
                ]
                for color, against, need in checks:
                    ratio = contrast(color, colors[against])
                    if ratio < need:
                        failures.append((dark, tint, color, against, round(ratio, 2)))
        self.assertEqual(failures, [])

    def test_the_browser_bars_read_whatever_the_photo(self):
        failures = []
        for dark in (False, True):
            for tint in tints():
                toolbar, accent = tint_colors(tint, dark)
                colors = browser_colors(toolbar, dark, accent)
                for name, against, need in (
                    ("ink", "toolbar", 7),
                    ("ink", "field", 7),
                    ("muted", "toolbar", TEXT),
                    ("accent", "toolbar", LARGE),
                    ("accent", "field", LARGE),
                    ("on_accent", "accent", TEXT),
                ):
                    ratio = contrast(colors[name], colors[against])
                    if ratio < need:
                        failures.append((dark, tint, name, against, round(ratio, 2)))
        self.assertEqual(failures, [])

    def test_dark_pages_stay_dark_with_a_bright_photo(self):
        for tint in ("#ffd23f", "#e0901c", "#ffffcc", "#80ffff"):
            colors = page_colors(tint, True)
            for name in ("bg", "surface", "soft"):
                self.assertLess(luminance(colors[name]), 0.09, (tint, name))
            # Still the photo's colour, not grey.
            self.assertGreater(hsl(colors["bg"])[1], 0.2, tint)

    def test_a_chosen_accent_is_kept_with_readable_text_on_it(self):
        colors = page_colors("#3366cc", False, accent="#ffd23f")
        self.assertEqual(colors["accent"], "#ffd23f")
        self.assertEqual(colors["on-accent"], DARK_TEXT)
        chosen = browser_colors("#eef1f6", False, chosen={"accent": "#ffd23f"})
        self.assertEqual(chosen["accent"], "#ffd23f")
        self.assertEqual(chosen["on_accent"], DARK_TEXT)


class StylesheetTests(unittest.TestCase):
    def test_text_on_the_accent_uses_on_accent(self):
        root = (ROOT / "ui" / "web" / "root.css").read_text()
        self.assertEqual(root.count("--on-accent:"), 2)  # Light and dark.
        sources = [
            *(ROOT / "ui" / "web").glob("*.css"),
            ROOT / "ui" / "assets" / "start_page.css",
            ROOT / "services" / "start_pages.py",
        ]
        wrong = re.compile(
            r"background(?:-color)?: ?var\(--accent\)[^};]*;[^}]*?"
            r"(?<![-\w])color: ?(#fff(?:fff)?|white|var\(--(?:bg|surface)\))"
        )
        found = [
            (path.name, match.group(0)[:80])
            for path in sources
            for match in wrong.finditer(path.read_text())
        ]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
