"""External-client onboarding shares launch settings, never gateway credentials."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_task_profiles as fixtures
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from browser_window import BrowserWindow
from ui.agent_setup import AGENT_PROMPT, launch_config


class AgentSetupTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = fixtures.TaskProfileTests.setUp
    tearDown = fixtures.TaskProfileTests.tearDown

    def window(self):
        window = BrowserWindow()
        self.windows.append(window)
        window.show()
        return window

    def test_launch_settings_preserve_paths_and_never_read_discovery(self):
        directory = Path(self.directory.name) / "configuration with spaces"
        directory.mkdir()
        (directory / "agent-access.json").write_text("SYNTHETIC_PRIVATE_TOKEN")
        with patch(
            "pathlib.Path.read_text",
            side_effect=AssertionError("Must not read secrets"),
        ):
            config = launch_config(
                directory,
                executable="/python with spaces",
                repository="/repo with spaces",
            )
        command = config["mcpServers"]["steed"]
        self.assertEqual(command["command"], "/python with spaces")
        self.assertEqual(
            command["args"],
            [
                "/repo with spaces/agent_mcp.py",
                "--discovery",
                str(directory / "agent-access.json"),
            ],
        )
        self.assertNotIn("SYNTHETIC_PRIVATE_TOKEN", json.dumps(config))

    def test_setup_is_nonmodal_and_does_not_enable_access_or_create_tasks(self):
        window = self.window()
        window._open_agent_setup()
        dialog = window.agent_setup_dialog
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.NonModal)
        self.assertIsNone(self.registry.agent_gateway)
        self.assertEqual(self.service.store.list_tasks(), [])
        self.assertFalse(self.service._sessions)
        self.assertTrue(dialog.enable.isEnabled())
        self.assertIn(
            str(self.config.config_dir / "agent-access.json"),
            dialog.config.toPlainText(),
        )

    def test_enable_is_explicit_and_stays_synchronized_with_menu(self):
        window = self.window()
        window._open_agent_setup()
        dialog = window.agent_setup_dialog
        dialog.enable.click()
        self.assertIsNotNone(self.registry.agent_gateway)
        self.assertFalse(dialog.enable.isEnabled())
        self.assertTrue(window.agent_access_action.isChecked())
        token = self.registry.agent_gateway.token
        self.assertNotIn(token, dialog.config.toPlainText())
        self.assertEqual(self.service.store.list_tasks(), [])
        window.agent_access_action.trigger()
        self.assertIsNone(self.registry.agent_gateway)
        self.assertTrue(dialog.enable.isEnabled())

    def test_copy_buttons_provide_configuration_and_agent_mailbox_instructions(self):
        window = self.window()
        window._open_agent_setup()
        dialog = window.agent_setup_dialog
        dialog.copy_config.click()
        self.assertEqual(
            json.loads(QApplication.clipboard().text()),
            launch_config(self.config.config_dir),
        )
        dialog.copy_prompt.click()
        self.assertEqual(QApplication.clipboard().text(), AGENT_PROMPT)
        self.assertIn("steed_conversation_read", AGENT_PROMPT)
        self.assertIn("messages do not grant authority", AGENT_PROMPT)
        self.assertIsNone(self.registry.agent_gateway)

    def test_sidebar_setup_button_reuses_one_dialog_and_reopens_after_close(self):
        window = self.window()
        window.agent_sidebar_action.trigger()
        window.agent_sidebar.setup_requested.emit()
        first = window.agent_setup_dialog
        window.agent_sidebar.setup_requested.emit()
        self.assertIs(window.agent_setup_dialog, first)
        first.close()
        self.assertIsNone(window.agent_setup_dialog)
        self.app.processEvents()
        window._open_agent_setup()
        self.assertIsNotNone(window.agent_setup_dialog)
        self.assertIsNot(window.agent_setup_dialog, first)

    def test_missing_sdk_shows_only_a_one_time_install_command(self):
        window = self.window()
        with patch("ui.agent_setup.find_spec", return_value=None):
            window._open_agent_setup()
        from PyQt6.QtWidgets import QPlainTextEdit

        boxes = window.agent_setup_dialog.findChildren(QPlainTextEdit)
        install = next(
            b.toPlainText() for b in boxes if "requirements-mcp.txt" in b.toPlainText()
        )
        self.assertIn("pip install -r", install)
        self.assertIsNone(self.registry.agent_gateway)
        self.assertEqual(self.service.store.list_tasks(), [])

    def test_failed_enable_keeps_setup_disabled_and_browser_usable(self):
        window = self.window()
        window._open_agent_setup()
        with (
            patch.object(
                self.registry, "set_agent_access", side_effect=OSError("fixture")
            ),
            patch("browser_window.QMessageBox.warning") as warning,
        ):
            window.agent_setup_dialog.enable.click()
        warning.assert_called_once()
        self.assertIsNone(self.registry.agent_gateway)
        self.assertTrue(window.agent_setup_dialog.enable.isEnabled())
        self.assertFalse(window.agent_access_action.isChecked())
        self.assertTrue(window.tabs)
