"""Starting out calm: a three-step Welcome, one-time tips, a Customize panel
that shows little until asked, every Steed page reachable from Settings,
and and a New Tab menu whose sections open on hover."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWidgets import QLabel
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
        # Every way to bring an assistant, and none needed.
        for kind in ("local", "key", "coding", "website", "cloud"):
            self.assertIn(f"steed://action/assistant?kind={kind}", page)
        self.assertIn("works fully without one", page)
        ready = welcome_steps("photo", glass=False, ready=["Codex"])
        self.assertIn("Already on this computer: Codex", ready)
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
                page in WORKSPACES or page in ("welcome", "dictation", "read-aloud"),
                page,
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

    def test_a_page_tip_shows_only_on_its_page(self):
        from ui import tips

        window = self.window
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: tab.url().host() == "newtab")
        on_newtab = lambda: tab.url().host() == "newtab"  # noqa: E731
        window._new_tab(url="about:blank")  # Another page is in front now.
        self.assertIsNone(tips.offer(window, "customize", when=lambda: False))
        window._switch_tab(window.tabs.index(tab))
        bubble = tips.offer(window, "customize", when=on_newtab)
        self.assertIsNotNone(bubble)
        tab.setUrl(QUrl("data:text/html,<title>away</title>"))
        self.wait(lambda: window._tip is None)
        # The first websites bring no dictation tip.
        self.assertLess(getattr(window, "_web_loads", 0), 25)

    def test_welcome_reads_as_one_page_on_every_step(self):
        from services.start_pages import assistant_cards, display_url

        self.assertEqual(display_url(QUrl("steed://welcome/#agent")), "steed:welcome")
        self.assertEqual(display_url(QUrl("steed://dashboard/#x")), "steed:dashboard#x")
        self.assertIn("Choose one", assistant_cards(("Codex", "Claude Code")))
        self.assertIn("Choose it", assistant_cards(("Codex",)))
        page = self.window.profile_manager.registry.start_pages.render(
            "global", "welcome"
        ).decode()
        self.assertNotIn('<span class="profile">Global', page)

    def test_tips_fade_in_whole_say_where_and_sit_clear(self):
        from ui import tips

        window = self.window
        window._new_tab(url="about:blank")  # Not over the Welcome tour.
        bubble = tips.offer(window, "customize", text=tips.CUSTOMIZE_GEAR)
        self.assertIn("gear", bubble.findChild(QLabel).text())
        # Words and bubble fade together (no words without their bubble).
        self.assertIsNotNone(bubble.graphicsEffect())
        self.assertLess(bubble.graphicsEffect().opacity(), 1.0)
        # Opening Customize is finding it: the tip goes.
        tab = window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: tab.url().host() == "newtab")
        window._start_page_action(tab, QUrl("steed://action/newtab-set?glass=1"))
        self.wait(lambda: window._tip is None)
        # Beside a tall panel, near its top rather than over its bottom.
        window._toggle_agent_sidebar(True)
        sidebar = window.agent_sidebar
        self.wait(lambda: sidebar.isVisible())
        bubble = tips.offer(window, "agent", sidebar)
        top = sidebar.mapTo(window, sidebar.rect().topLeft()).y()
        self.assertLess(bubble.y(), top + sidebar.height() // 2)

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
        shown = []
        with patch.object(
            type(window),
            "_present_dialog",
            lambda s, d, **_: (shown.append(d), MagicMock())[1],
        ):
            window._start_page_action(tab, QUrl("steed://action/assistant?kind=key"))
            window._start_page_action(
                tab, QUrl("steed://action/assistant?kind=nonsense")
            )
        self.assertEqual([type(d).__name__ for d in shown], ["ModelsDialog"])
        self.assertEqual(shown[0].service.currentData()[1], "keyring")
        for dialog in shown:
            dialog.deleteLater()
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
