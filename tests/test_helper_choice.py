"""Which assistant helps with Steed's small jobs: asked once when there are
several, remembered, and changed in Agents → Helper Assistant."""

import unittest
from unittest.mock import patch

import test_writing_help as fixture

from services import agent_once
from ui.assistant_choice import HelperDialog, ensure

MODELS = [
    {"name": "Home model", "base_url": "http://127.0.0.1:9/v1", "model": "a"},
    {"name": "Work model", "base_url": "http://127.0.0.1:9/v1", "model": "b"},
]


class HelperChoiceTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def test_several_assistants_ask_once_then_remember(self):
        config = self.window.config
        config.set("models", MODELS)
        used = []
        dialog = ensure(self.window, used.append)
        self.assertIsInstance(dialog, HelperDialog)
        self.assertEqual(used, [])  # Nothing is sent before you choose.
        dialog.list.setCurrentRow(1)
        dialog.use()
        self.assertEqual(config.get("watch_agent"), "model:Work model")
        self.assertEqual(used[0]["name"], "Work model")
        self.assertIsNone(ensure(self.window, used.append))  # Not asked again.
        self.assertEqual(used[1]["name"], "Work model")
        # The menu shows it and changes it.
        self.window._fill_helper_menu()
        actions = self.window.helper_menu.actions()
        self.assertEqual([a.text() for a in actions], ["Home model", "Work model"])
        self.assertTrue(actions[1].isChecked())
        actions[0].trigger()
        self.assertEqual(agent_once.agent_command(config)["name"], "Home model")

    def test_cancel_sends_nothing_and_one_assistant_needs_no_question(self):
        config = self.window.config
        config.set("models", MODELS)
        used = []
        dialog = ensure(self.window, used.append)
        dialog.reject()
        self.assertEqual(used, [])
        self.assertEqual(config.get("watch_agent", ""), "")
        config.set("models", MODELS[:1])
        self.assertIsNone(ensure(self.window, used.append))
        self.assertEqual(used[0]["name"], "Home model")
        self.assertEqual(config.get("watch_agent"), "model:Home model")

    def test_installed_agents_count_and_none_says_so(self):
        with patch.object(agent_once, "installed_agents", lambda: ["codex", "claude"]):
            self.assertEqual(
                agent_once.helper_choices(self.window.config),
                [("codex", "Codex"), ("claude", "Claude Code")],
            )
        self.window._fill_helper_menu()
        (none,) = self.window.helper_menu.actions()
        self.assertFalse(none.isEnabled())


if __name__ == "__main__":
    unittest.main()
