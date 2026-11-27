"""Welcome's three quick questions: optional, each answer sets a few defaults
(and says which), all changeable later."""

import unittest

import test_writing_help as fixture
from PyQt6.QtCore import QUrl

from browser import memory_modes
from browser.layout_preferences import preferences_from_config
from services import welcome_questions
from services.start_pages import welcome_questions as rendered
from services.start_pages import welcome_steps
from services.welcome_questions import EFFECTS, QUESTIONS, answers, menu_order


class Config(dict):
    def set(self, key, value):
        self[key] = value


class PieceTests(unittest.TestCase):
    def test_every_answer_says_what_it_sets(self):
        for key, _, choices in QUESTIONS:
            self.assertEqual({value for value, _ in choices}, set(EFFECTS[key]))
            for value, _ in choices:
                self.assertTrue(EFFECTS[key][value][4].endswith("."))

    def test_the_page_offers_them_and_marks_what_you_chose(self):
        page = welcome_steps("photo", glass=False)
        self.assertIn('id="you"', page)
        self.assertIn("steed://action/welcome-answer?q=ai&amp;a=daily", page)
        self.assertNotIn('class="answer on"', page)
        # The note under each question is there before it is answered (empty),
        # so answering never moves the questions below.
        self.assertEqual(page.count('<p class="sets"></p>'), 3)
        chosen = rendered({"weight": "light"})
        self.assertEqual(chosen.count('class="answer on"'), 1)
        self.assertIn(
            'aria-pressed="true" href="steed://action/welcome-answer?q=weight&amp;a=light"',
            chosen,
        )
        self.assertIn("no tab previews", chosen)
        self.assertNotIn("<script", page)

    def test_answers_are_checked(self):
        config = Config(welcome_answers={"ai": "daily", "ai2": "x", "weight": "huge"})
        self.assertEqual(answers(config), {"ai": "daily"})
        self.assertEqual(answers(Config(welcome_answers="daily")), {})
        with self.assertRaises(ValueError):
            welcome_questions.apply(config, "ai", "always")
        self.assertEqual(
            menu_order(["general", "agents", "tabs"], True),
            ["agents", "general", "tabs"],
        )
        self.assertEqual(
            menu_order(["agents", "general"], False), ["general", "agents"]
        )


class WindowTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def answer(self, question, value):
        tab = self.window.tabs[self.window.current_tab_index]
        tab.setUrl(QUrl("steed://welcome/#you"))
        self.wait(lambda: tab.url().host() == "welcome")
        self.window._start_page_action(
            tab, QUrl(f"steed://action/welcome-answer?q={question}&a={value}")
        )

    def errands(self):
        pages = self.window.profile_manager.registry.start_pages
        profile = self.window.profile_manager.current_profile_name
        return pages.state(profile)["settings"]["errands"]

    def test_answers_set_their_defaults(self):
        config = self.window.config
        self.answer("weight", "light")
        self.assertEqual(memory_modes.mode(config), "saver")
        self.assertEqual(config.get("visual_effects"), "minimal")
        self.assertFalse(config.get("tab_previews"))
        self.answer("ai", "daily")
        self.assertEqual(preferences_from_config(config).menu_order[0], "agents")
        self.assertTrue(self.errands())
        self.assertTrue(config.get("agent_notifications"))
        self.answer("ai", "rarely")
        self.assertEqual(preferences_from_config(config).menu_order[-1], "agents")
        self.assertFalse(self.errands())
        self.assertFalse(config.get("agent_notifications"))
        self.answer("notify", "needs_me")
        self.assertEqual(
            (config.get("notifications_where"), config.get("notifications_often")),
            ("desktop", "needs_me"),
        )
        self.answer("weight", "nonsense")  # Ignored.
        self.assertEqual(
            config.get("welcome_answers"),
            {"weight": "light", "ai": "rarely", "notify": "needs_me"},
        )
        html = self.window.profile_manager.registry.start_pages.render(
            self.window.profile_manager.current_profile_name, "welcome"
        )
        html = html.decode() if isinstance(html, bytes) else str(html)
        self.assertEqual(html.count('class="answer on"'), 3)

    def test_clicking_an_answer_shows_it_on_the_same_step(self):
        tab = self.window.tabs[self.window.current_tab_index]
        loaded = []
        tab.loadFinished.connect(loaded.append)
        tab.setUrl(QUrl("steed://welcome/#you"))
        self.wait(lambda: loaded)

        def chosen():
            found = []
            tab.page().runJavaScript(
                "document.querySelectorAll('.answer.on').length", found.append
            )
            self.wait(lambda: found)
            return found[0]

        self.assertEqual(chosen(), 0)
        tab.page().runJavaScript(
            "document.querySelector('a[href*=\"q=notify&a=steed\"]').click()"
        )
        self.wait(lambda: chosen() == 1)
        self.assertEqual(tab.url().fragment(), "you")
        self.assertEqual(self.window.config.get("notifications_where"), "steed")

    def test_only_from_welcome(self):
        tab = self.window.tabs[self.window.current_tab_index]
        tab.setUrl(QUrl("steed://newtab/"))
        self.wait(lambda: tab.url().host() == "newtab")
        self.window._start_page_action(
            tab, QUrl("steed://action/welcome-answer?q=weight&a=light")
        )
        self.assertIsNone(self.window.config.get("welcome_answers"))


if __name__ == "__main__":
    unittest.main()
