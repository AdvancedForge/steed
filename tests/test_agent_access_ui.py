"""Local access is opt-in, shared across windows, and closed before profiles."""

import unittest
from unittest.mock import patch

import test_task_profiles as profiles
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QDialog

from browser_window import BrowserWindow


class AgentAccessUITests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for
    command = profiles.TaskProfileTests.command

    def test_context_reports_exact_profile_without_grants_or_pages(self):
        window = BrowserWindow()
        self.windows.append(window)
        self.manager.create_profile("codex test")
        window._switch_to_profile("codex test")
        task = self.command("task.create", objective="Choose the user's profile")
        with patch("profile_manager.QApplication.activeWindow", return_value=window):
            context = self.command("browser.context", task_id=task.task_id)
        self.assertEqual(
            context,
            {
                "window_id": window.browser_window_id,
                "profile": "codex test",
                "profile_selector": "existing:codex test",
                "shared_profile_available": True,
                "source": "active_window",
            },
        )
        self.assertEqual(self.service.store.grants(task.task_id), [])
        self.assertFalse(self.service._sessions)

    def test_context_tracks_profile_switch_without_retargeting_existing_grants(self):
        window = BrowserWindow()
        self.windows.append(window)
        window._switch_to_profile("work")
        task = self.command("task.create", objective="Read work profile")
        grant = self.command(
            "grant.request",
            task_id=task.task_id,
            scope={"origins": ["https://93.184.215.14"], "profile": "existing:work"},
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        window._switch_to_profile("default")
        with patch("profile_manager.QApplication.activeWindow", return_value=window):
            self.assertEqual(self.registry.agent_context()["profile"], "default")
        self.assertEqual(
            self.service.store.grant(task.task_id, grant.grant_id).scope["profile"],
            "existing:work",
        )

    def test_context_selects_active_window_or_its_dialog_then_last_used_window(self):
        first, second = BrowserWindow(), BrowserWindow()
        self.windows.extend((first, second))
        first._switch_to_profile("work")
        second._switch_to_profile("default")
        dialog = QDialog(first)
        with patch("profile_manager.QApplication.activeWindow", return_value=dialog):
            self.assertEqual(
                self.registry.agent_context()["window_id"], first.browser_window_id
            )
        QCoreApplication.sendEvent(second, QEvent(QEvent.Type.WindowActivate))
        with patch("profile_manager.QApplication.activeWindow", return_value=None):
            context = self.registry.agent_context()
        self.assertEqual(context["window_id"], second.browser_window_id)
        self.assertEqual(context["profile_selector"], "existing:default")
        self.assertEqual(context["source"], "last_active_window")
        self.assertNotEqual(first.browser_window_id, second.browser_window_id)

    def test_context_does_not_guess_between_windows_and_drops_closed_owner(self):
        first, second = BrowserWindow(), BrowserWindow()
        self.windows.extend((first, second))
        self.registry.last_active_owner = None
        with patch("profile_manager.QApplication.activeWindow", return_value=None):
            self.assertIsNone(self.registry.agent_context()["window_id"])
            QCoreApplication.sendEvent(second, QEvent(QEvent.Type.WindowActivate))
            second.close()
            self.assertIsNone(self.registry.last_active_owner)
            context = self.registry.agent_context()
        self.assertEqual(context["window_id"], first.browser_window_id)
        self.assertEqual(context["source"], "only_window")

    def test_context_offers_global_but_keeps_incognito_private(self):
        window = BrowserWindow()
        self.windows.append(window)
        with patch("profile_manager.QApplication.activeWindow", return_value=window):
            for name in ("search", "global"):
                with self.subTest(profile=name):
                    window._switch_to_profile(name)
                    context = self.registry.agent_context()
                    self.assertEqual(context["profile"], name)
                    if name == "global":
                        self.assertEqual(context["profile_selector"], "existing:global")
                        self.assertTrue(context["shared_profile_available"])
                    else:
                        self.assertIsNone(context["profile_selector"])
                        self.assertFalse(context["shared_profile_available"])

    def test_access_is_opt_in_and_menu_state_is_shared(self):
        first, second = BrowserWindow(), BrowserWindow()
        self.windows.extend((first, second))
        self.assertIsNone(self.registry.agent_gateway)
        self.assertFalse(first.agent_access_action.isChecked())
        first.agent_access_action.trigger()
        self.assertIsNotNone(self.registry.agent_gateway)
        self.assertTrue(second.agent_access_action.isChecked())
        discovery = self.registry.agent_gateway.discovery
        self.assertTrue(discovery.exists())
        second.agent_access_action.trigger()
        self.assertIsNone(self.registry.agent_gateway)
        self.assertFalse(first.agent_access_action.isChecked())
        self.assertFalse(discovery.exists())

    def test_journal_failure_disables_gateway_in_all_windows(self):
        window = BrowserWindow()
        self.windows.append(window)
        window.agent_access_action.trigger()
        discovery = self.registry.agent_gateway.discovery
        self.service._fatal()
        self.assertIsNone(self.registry.agent_gateway)
        self.assertFalse(window.agent_access_action.isChecked())
        self.assertFalse(discovery.exists())

    def test_last_owner_release_closes_agent_access_before_profile_teardown(self):
        self.registry.set_agent_access(True)
        discovery = self.registry.agent_gateway.discovery
        self.manager.close()
        self.assertFalse(discovery.exists())
        self.assertIsNone(self.registry.agent_gateway)
        self.assertTrue(self.service._closed)
