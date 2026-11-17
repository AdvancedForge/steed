"""Starting out calm: a three-step Welcome, one-time tips, a Customize panel
that shows little until asked, every Steed page reachable from Settings,
and and a New Tab menu whose sections open on hover."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from services.start_pages import WORKSPACES, customize_panel, welcome_steps


class PieceTests(unittest.TestCase):
    def test_welcome_is_three_steps_with_a_way_out(self):
        page = welcome_steps("photo", glass=False)
        for step in ('id="look"', 'id="bring"', 'id="agent"'):
            self.assertIn(step, page)
        self.assertIn("steed://action/import", page)
        self.assertIn("steed://action/agents", page)
        self.assertEqual(page.count("steed://action/start"), 2)  # Done, or skip.
        self.assertIn("newtab-set?glass=1", page)  # Glass is off: turns it on.
        self.assertIn("newtab-set?glass=0", welcome_steps("photo", glass=True))
        self.assertNotIn("<script", page)

    def test_welcome_shows_what_is_chosen(self):
        page = welcome_steps("color", glass=True)
        chosen = page.split('class="tile colour on"')[1].split("</a>")[0]
        self.assertIn("Chosen", chosen)
        self.assertEqual(page.count("Chosen"), 1)
        self.assertIn('class="switch on" role="switch" aria-checked="true"', page)
        self.assertIn('aria-checked="false"', welcome_steps("photo", glass=False))

    def test_actions_come_from_welcome_on_any_step(self):
        from PyQt6.QtCore import QUrl

        from services.start_pages import action_page

        self.assertEqual(action_page(QUrl("steed://welcome/#bring")), "welcome")
        self.assertEqual(action_page(QUrl("steed://newtab/")), "newtab")
        self.assertIsNone(action_page(QUrl("https://example.com/#bring")))

    def test_customize_shows_little_until_more_options(self):
        settings = {
            "background": "photo",
            "photo_source": "online",
            "photo_folder": "",
            "photo_style": "color",
            "photo_every": "6h",
            "color": "",
            "match_theme": True,
            "layout": "simple",
        }
        panel = customize_panel(settings)
        up_front = panel.split('<details class="more-options"')[0]
        self.assertIn('class="tiles"', up_front)
        self.assertIn("New photo", up_front)
        for later in ("Photos from", "Black &amp; white", "Black & white", "Every tab"):
            self.assertNotIn(later, up_front)
        self.assertIn('<details class="more-options">', panel)  # Closed.
        self.assertIn(
            '<details class="more-options" open>',
            customize_panel(settings, more_open=True),
        )

    def test_every_settings_directory_entry_is_a_real_page(self):
        from ui.settings_dialog import DIRECTORY

        html = (Path(__file__).parents[1] / "ui" / "web" / "settings.html").read_text()
        for page in DIRECTORY:
            self.assertIn(f'data-page="{page}"', html)
            self.assertTrue(
                page in WORKSPACES or page in ("welcome", "dictation"), page
            )
        # And every maintained page is listed (Settings itself and New Tab aside).
        listed = {page for page in WORKSPACES if f'data-page="{page}"' in html}
        self.assertEqual(set(WORKSPACES) - listed, {"settings"})


class TipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.tips_off = os.environ.pop("STEED_NO_TIPS", None)
        self.window = BrowserWindow()
        self.window.show()

    def tearDown(self):
        if self.tips_off is not None:
            os.environ["STEED_NO_TIPS"] = self.tips_off
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

    def test_a_tip_shows_once_one_at_a_time(self):
        from ui import tips

        window = self.window
        window._new_tab(url="about:blank")
        first = tips.offer(window, "address", window.address_bar)
        self.assertIsNotNone(first)
        self.assertIsNone(tips.offer(window, "dictation"))  # One at a time.
        first.dismiss()
        self.app.processEvents()
        self.assertIn("address", window.config.get("tips_seen"))
        self.assertIsNone(tips.offer(window, "address"))  # Never again.
        self.assertIsNotNone(tips.offer(window, "dictation"))
        self.assertIsNone(tips.offer(window, "no-such-tip"))

    def test_welcome_choices_and_links_work_on_every_step(self):
        from PyQt6.QtCore import QUrl

        window = self.window
        tab = window._new_tab(url="steed:welcome", return_tab=True)
        self.wait(lambda: tab.url().host() == "welcome")
        tab.setUrl(QUrl("steed://welcome/#bring"))  # "Next", within the page.
        self.wait(lambda: tab.url().fragment() == "bring")
        pages = window.profile_manager.registry.start_pages
        profile = window.profile_manager.current_profile_name
        window._start_page_action(
            tab, QUrl("steed://action/newtab-set?background=color")
        )
        self.assertEqual(pages.state(profile)["settings"]["background"], "color")
        window._start_page_action(tab, QUrl("steed://action/agents"))
        self.wait(lambda: window.agent_sidebar is not None)
        window._start_page_action(tab, QUrl("steed://action/start"))
        self.assertTrue(window.config.get("welcome_completed"))

    def test_no_tips_over_the_welcome_tour_or_in_tests(self):
        from ui import tips

        window = self.window
        tab = window._new_tab(url="steed:welcome", return_tab=True)
        self.wait(lambda: tab.url().host() == "welcome")
        self.assertIsNone(tips.offer(window, "address"))
        os.environ["STEED_NO_TIPS"] = "1"
        window._new_tab(url="about:blank")
        self.assertIsNone(tips.offer(window, "address"))
        del os.environ["STEED_NO_TIPS"]


if __name__ == "__main__":
    unittest.main()
