"""Cookie banners: rejected, accepted or left alone as you chose, with your
own rules and sites to skip; the page can't forge Steed's messages."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from qt_test_support import WAIT_SECONDS, application

from browser import cookie_banners, webengine
from browser.cookie_banners import (
    check_rules_file,
    mode_for,
    optin_site_rules,
    own_rules,
    parse,
    site_rules,
    skipped,
    start_reply,
    wanted,
)

# A page with Cookiebot, which autoconsent recognises through the page's own
# JavaScript (Steed runs those checks).
COOKIEBOT = """<title>start</title>
<script>
window.Cookiebot = {hasResponse: false, dialog: {visible: true}, declined: false,
  withdraw() { return true; }, hide() { return true; }};
</script>
<div id=CybotCookiebotDialog
  style="position:fixed;bottom:0;left:0;right:0;padding:20px;background:#eee">
We use cookies.
<button id=CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll
  onclick="Cookiebot.declined=true;Cookiebot.hasResponse=true;
  document.title='rejected';this.parentNode.remove()">Decline</button>
<button id=CybotCookiebotDialogBodyLevelButtonAccept
  onclick="Cookiebot.hasResponse=true;document.title='accepted';
  this.parentNode.remove()">Allow all</button>
</div><p>Article</p>"""
# A banner no rule knows, with your own rule for it.
OWN = """<title>start</title><p>Article</p>
<div id=steed-banner style="position:fixed;bottom:0;padding:20px;background:#eee">
Biscuits?
<button id=steed-no onclick="document.title='rejected'">No thanks</button>
<button id=steed-yes onclick="document.title='accepted'">Sure</button></div>"""
OWN_RULES = {
    "autoconsent": [
        {
            "name": "steed-test",
            "runContext": {"urlPattern": "^https://own\\.test/"},
            "detectCmp": [{"exists": "#steed-banner"}],
            "detectPopup": [{"visible": "#steed-banner"}],
            "optIn": [{"waitForThenClick": "#steed-yes"}],
            "optOut": [{"waitForThenClick": "#steed-no"}],
        }
    ]
}


class Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class ChoiceTests(unittest.TestCase):
    def test_modes_for_you_and_for_agents(self):
        self.assertEqual(mode_for(Config()), "reject")  # The default.
        self.assertEqual(mode_for(None), "leave")
        self.assertEqual(mode_for(Config(cookie_banners="nonsense")), "reject")
        config = Config(cookie_banners="leave")
        self.assertEqual(mode_for(config, agent=True), "leave")  # Same as yours.
        self.assertFalse(wanted(config))
        config["cookie_banners_agents"] = "accept"
        self.assertEqual(mode_for(config, agent=True), "accept")
        self.assertEqual(mode_for(config), "leave")
        self.assertTrue(wanted(config))

    def test_sites_to_skip_include_their_subdomains(self):
        config = Config(cookie_banner_skip="example.com,  www.news.test/")
        self.assertTrue(skipped(config, "www.example.com"))
        self.assertTrue(skipped(config, "shop.example.com"))
        self.assertTrue(skipped(config, "news.test"))
        self.assertFalse(skipped(config, "notexample.com"))
        self.assertFalse(skipped(Config(), "example.com"))


class RuleTests(unittest.TestCase):
    def test_generic_rules_everywhere_and_a_sites_own_where_they_apply(self):
        generic = site_rules("https://nothing-special.example/")
        rules = cookie_banners.compact_rules()
        index = rules["index"]
        self.assertEqual(len(generic["r"]), index["genericRuleRange"][1])
        self.assertEqual(len(generic["s"]), index["genericStringEnd"])
        specific = [
            rule
            for rule in rules["r"][slice(*index["specificRuleRange"])]
            if rule[3] and rule[4] != 1
        ]
        pattern = specific[0][3]
        url = next(
            u
            for u in (
                "https://www.bbc.com/",
                "https://www.bing.com/",
                "https://posthog.com/",
            )
            if any(cookie_banners._matches(r[3], u) for r in specific)
        )
        here = site_rules(url)
        self.assertGreater(len(here["r"]), len(generic["r"]))
        self.assertIn("", here["s"])  # Strings no chosen rule uses are dropped.
        self.assertTrue(pattern)

    def test_accepting_uses_rules_that_run_in_this_page(self):
        everywhere = optin_site_rules("https://nothing-special.example/")
        bbc = optin_site_rules("https://www.bbc.com/")
        self.assertGreater(len(bbc), len(everywhere))
        self.assertTrue(all("optIn" in rule for rule in bbc))
        self.assertFalse(
            any((r.get("runContext") or {}).get("main") is False for r in everywhere)
        )

    def test_your_own_rules_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(OWN_RULES))
            config = Config(cookie_banner_rules_file=str(path))
            self.assertEqual(check_rules_file(str(path)), 1)
            self.assertEqual(
                own_rules(config, "https://own.test/a")[0]["name"], "steed-test"
            )
            self.assertEqual(own_rules(config, "https://other.test/"), [])
            path.write_text("not json")
            self.assertEqual(own_rules(config, "https://own.test/"), [])
            with self.assertRaisesRegex(ValueError, "not JSON"):
                check_rules_file(str(path))
            path.write_text(json.dumps([{"no": "name"}]))
            with self.assertRaisesRegex(ValueError, "each with a name"):
                check_rules_file(str(path))
        with self.assertRaisesRegex(ValueError, "could not be read"):
            check_rules_file("/nonexistent/rules.json")

    def test_what_autoconsent_is_told_at_the_start(self):
        config = Config()
        reject = start_reply("reject", config, "https://site.test/")
        self.assertEqual(reject["config"]["autoAction"], "optOut")
        self.assertIn("compact", reject["rules"])
        accept = start_reply("accept", config, "https://site.test/")
        self.assertEqual(accept["config"]["autoAction"], "optIn")
        self.assertNotIn("compact", accept["rules"])
        self.assertTrue(accept["rules"]["autoconsent"])
        for mode, url, settings in (
            ("leave", "https://site.test/", config),
            ("reject", "file:///home/you/page.html", config),
            ("reject", "https://site.test/", Config(cookie_banner_skip="site.test")),
        ):
            answer = start_reply(mode, settings, url)
            self.assertFalse(answer["config"]["enabled"], (mode, url))

    def test_only_messages_with_the_token_count(self):
        good = cookie_banners.PREFIX + json.dumps(
            {"token": cookie_banners.TOKEN, "message": {"type": "init"}}
        )
        self.assertEqual(parse(good), {"type": "init"})
        for text in (
            cookie_banners.PREFIX
            + json.dumps({"token": "guess", "message": {"type": "init"}}),
            cookie_banners.PREFIX + "not json",
            cookie_banners.PREFIX
            + json.dumps({"token": cookie_banners.TOKEN, "message": "init"}),
            good + " " * cookie_banners.MAX_MESSAGE,
        ):
            self.assertIsNone(parse(text))


class Page(webengine.Page):
    def __init__(self, profile, parent, config):
        super().__init__(profile, parent)
        self.config = config
        self.answered = []

    def cookie_banner_settings(self):
        return mode_for(self.config), self.config

    def cookie_banner_answered(self, done):
        self.answered.append(done)


class LivePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.profile = QWebEngineProfile()
        cookie_banners.install(cls.profile, True)

    def load(self, html, url, **settings):
        view = QWebEngineView()
        page = Page(self.profile, view, Config(settings))
        view.setPage(page)
        view.resize(900, 600)
        view.show()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        page.setHtml(html, QUrl(url))
        return page

    def wait_for_answer(self, page, seconds=WAIT_SECONDS):
        """The page's title once a button was clicked and autoconsent said
        so (it reports after checking it worked)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if page.title() == "pwned" or (
                page.title() in ("rejected", "accepted") and page.answered
            ):
                break
            time.sleep(0.01)
        return page.title()

    def test_installed_once_in_top_frames_only(self):
        found = [
            s
            for s in self.profile.scripts().toList()
            if s.name() == cookie_banners.SCRIPT_NAME
        ]
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].runsOnSubFrames())
        self.assertEqual(found[0].worldId(), cookie_banners.WORLD)
        self.assertEqual(
            found[0].injectionPoint(), found[0].InjectionPoint.DocumentCreation
        )
        cookie_banners.install(self.profile, True)
        self.assertEqual(
            sum(
                s.name() == cookie_banners.SCRIPT_NAME
                for s in self.profile.scripts().toList()
            ),
            1,
        )

    def test_rejected_by_default_with_the_pages_own_checks(self):
        page = self.load(COOKIEBOT, "https://cookiebot.test/")
        self.assertEqual(self.wait_for_answer(page), "rejected")
        self.assertEqual(
            page.answered, [{"tool": "Cybotcookiebot", "answer": "reject"}]
        )

    def test_accepted_when_you_chose_accept(self):
        page = self.load(COOKIEBOT, "https://cookiebot.test/", cookie_banners="accept")
        self.assertEqual(self.wait_for_answer(page), "accepted")

    def test_left_alone(self):
        for settings in (
            {"cookie_banners": "leave"},
            {"cookie_banner_skip": "cookiebot.test"},
        ):
            page = self.load(COOKIEBOT, "https://cookiebot.test/", **settings)
            self.assertEqual(self.wait_for_answer(page, 3), "start", settings)
            self.assertEqual(page.answered, [])

    def test_your_own_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(OWN_RULES))
            page = self.load(
                OWN, "https://own.test/", cookie_banner_rules_file=str(path)
            )
            self.assertEqual(self.wait_for_answer(page), "rejected")
            self.assertEqual(page.answered[0]["tool"], "steed-test")

    def test_the_page_cannot_forge_steeds_messages(self):
        forged = json.dumps(
            {
                "token": "guess",
                "message": {
                    "type": "eval",
                    "id": "1",
                    "code": "document.title='pwned'",
                },
            }
        )
        html = (
            "<title>start</title><script>console.debug('\\u0001steedconsent ' + "
            + json.dumps(forged)
            + ")</script>"
        )
        page = self.load(html, "https://forged.test/")
        self.assertEqual(self.wait_for_answer(page, 2), "start")


class SettingsTests(unittest.TestCase):
    def test_choices_and_your_rules_file_are_checked(self):
        from ui.settings_dialog import DEFAULTS, SettingsDialog

        values = dict(DEFAULTS)
        SettingsDialog._validate_values(values)
        for key, value, message in (
            ("cookie_banners", "maybe", "cookie banners"),
            ("cookie_banners_agents", "maybe", "agents' pages"),
            (
                "cookie_banner_rules_file",
                "/nonexistent/rules.json",
                "could not be read",
            ),
        ):
            with self.assertRaisesRegex(ValueError, message):
                SettingsDialog._validate_values({**values, key: value})

    def test_accepting_or_skipping_is_marked_as_weakening(self):
        from services.settings_proposals import weakens

        self.assertTrue(weakens("cookie_banners", "accept"))
        self.assertTrue(weakens("cookie_banners_agents", "accept"))
        self.assertTrue(weakens("cookie_banner_skip", "example.com"))
        self.assertFalse(weakens("cookie_banners", "reject"))
        self.assertFalse(weakens("cookie_banners", "leave"))


class WindowTests(unittest.TestCase):
    """Every profile, incognito too, follows the setting when it changes."""

    def setUp(self):
        import test_writing_help as fixture

        self.fixture = fixture.WritingHelpTests("setUp")
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.window = self.fixture.window

    def installed(self):
        registry = self.window.profile_manager.registry
        return [
            any(s.name() == cookie_banners.SCRIPT_NAME for s in p.scripts().toList())
            for p in (*registry.profiles.values(), registry.search_profile)
        ]

    def test_profiles_follow_the_setting(self):
        self.assertTrue(all(self.installed()))
        tab = self.window.tabs[self.window.current_tab_index]
        self.assertEqual(tab.page().cookie_banner_settings()[0], "reject")
        config = self.window.config
        config.set("cookie_banners", "leave")
        self.window.profile_manager.refresh_settings()
        self.assertFalse(any(self.installed()))
        config.set("cookie_banners_agents", "reject")  # Agents' pages still need it.
        self.window.profile_manager.refresh_settings()
        self.assertTrue(all(self.installed()))
        self.assertEqual(tab.page().cookie_banner_settings()[0], "leave")


if __name__ == "__main__":
    unittest.main()
