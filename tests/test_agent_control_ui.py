"""Takeover review, pinned tabs, Take Back and explicit shared YOLO controls."""

import json
import unittest
from unittest.mock import patch

import test_live_tabs as live_fixture
from PyQt6.QtCore import QUrl

from layout_format import LAYOUT_EXAMPLE
from services.live_control import resolve
from services.task_policy import TaskPermissionError
from start_page_format import EXAMPLE
from ui.live_control_review import LiveControlDialog
from ui.prompts import MessageBox as QMessageBox


class AgentControlUITests(unittest.TestCase):
    setUpClass = classmethod(live_fixture.LiveTabsTests.setUpClass.__func__)
    tearDownClass = classmethod(live_fixture.LiveTabsTests.tearDownClass.__func__)
    setUp = live_fixture.LiveTabsTests.setUp
    tearDown = live_fixture.LiveTabsTests.tearDown
    wait_for = live_fixture.LiveTabsTests.wait_for
    command = live_fixture.LiveTabsTests.command
    window = live_fixture.LiveTabsTests.window
    task = live_fixture.LiveTabsTests.task

    def test_takeover_requests_one_popup_and_take_back_cannot_be_replayed(self):
        window, view = self.window()
        task = self.task()
        receipt = self.command(
            "browser.request_control", task_id=task, request_id="handoff"
        )
        self.assertEqual(receipt["state"], "requested")
        self.assertNotIn("Live fixture", json.dumps(receipt))
        self.assertNotIn(self.origin, json.dumps(receipt))
        queue = self.registry.task_approvals
        self.wait_for(lambda: isinstance(queue.dialog, LiveControlDialog))
        queue.dialog.allow_button.click()
        self.assertEqual(
            self.command("browser.control_status", task_id=task, request_id="handoff")[
                "state"
            ],
            "approved",
        )
        tabs = self.command("browser.tabs", task_id=task)["tabs"]
        self.assertEqual(len(tabs), 1)
        self.assertTrue(tabs[0]["session_id"])
        window._take_back_tab(view)
        self.assertIsNone(self.service.backend.live_session(view))
        repeat = self.command(
            "browser.request_control", task_id=task, request_id="handoff"
        )
        self.assertEqual(repeat["state"], "returned")
        self.assertIsNone(self.service.backend.live_session(view))

    def test_profile_change_makes_pinned_takeover_stale(self):
        window, view = self.window()
        task = self.task()
        self.command("browser.request_control", task_id=task, request_id="stale")
        window._switch_to_profile("global")
        receipt = resolve(
            self.service, self.service.control_requests[task, "stale"], approve=True
        )
        self.assertEqual(receipt["state"], "stale")
        self.assertFalse(self.service._grants)
        self.assertFalse(self.service.live_tabs.offers)

    def test_navigation_to_another_site_makes_pinned_takeover_stale(self):
        _, view = self.window()
        task = self.task()
        self.command("browser.request_control", task_id=task, request_id="origin-pin")
        view.setUrl(QUrl(self.origin.replace("127.0.0.1", "localhost") + "/live"))
        self.wait_for(lambda: view.url().host() == "localhost")
        receipt = resolve(
            self.service,
            self.service.control_requests[task, "origin-pin"],
            approve=True,
        )
        self.assertEqual(receipt["state"], "stale")
        self.assertFalse(self.service._grants)

    def test_enabling_auto_mode_approves_an_already_visible_takeover_popup(self):
        _, view = self.window()
        task = self.task()
        self.command("browser.request_control", task_id=task, request_id="pending-auto")
        self.wait_for(
            lambda: isinstance(self.registry.task_approvals.dialog, LiveControlDialog)
        )
        self.service.set_auto_approval(True)
        self.wait_for(
            lambda: (
                self.service.control_requests[task, "pending-auto"].state == "approved"
            )
        )
        self.assertIsNotNone(self.service.backend.live_session(view))

    def test_auto_mode_applies_layout_and_start_page_reviews(self):
        self.window()
        self.service.set_auto_approval(True)
        task = self.task()
        self.command(
            "layout.propose",
            task_id=task,
            request_id="auto-layout",
            source=json.dumps(LAYOUT_EXAMPLE),
        )
        self.wait_for(
            lambda: (
                self.service.layouts.get(task, "auto-layout").state
                != "awaiting_approval"
            )
        )
        self.assertEqual(self.service.layouts.get(task, "auto-layout").state, "applied")
        self.command(
            "start_page.propose",
            task_id=task,
            request_id="auto-page",
            source=json.dumps(EXAMPLE),
        )
        self.wait_for(
            lambda: (
                self.service.start_pages.get(task, "auto-page").state
                != "awaiting_approval"
            )
        )
        self.assertEqual(
            self.service.start_pages.get(task, "auto-page").state, "applied"
        )

    def test_auto_mode_takes_over_exact_selection_without_a_popup(self):
        _, view = self.window()
        self.service.set_auto_approval(True)
        task = self.task()
        receipt = self.command(
            "browser.request_control", task_id=task, request_id="automatic"
        )
        self.assertEqual(receipt["state"], "approved")
        self.assertIsNotNone(self.service.backend.live_session(view))
        self.app.processEvents()
        self.assertIsNone(self.registry.task_approvals.dialog)

    def test_auto_mode_applies_panel_proposals_without_an_extra_review(self):
        self.window()
        self.service.set_auto_approval(True)
        task = self.task()
        source = {
            "version": 1,
            "name": "Synthetic task panel",
            "widgets": [{"id": "task", "kind": "task", "title": "Task"}],
        }
        self.command(
            "panel.propose",
            task_id=task,
            request_id="automatic-panel",
            source=json.dumps(source),
        )
        self.wait_for(
            lambda: (
                self.service.data_panels.get(task, "automatic-panel").state
                != "awaiting_approval"
            )
        )
        self.assertEqual(
            self.service.data_panels.get(task, "automatic-panel").state, "applied"
        )
        self.assertIsNone(self.registry.task_approvals.dialog)

    def test_mode_requires_desktop_confirmation_and_agents_cannot_enable_it(self):
        window, _ = self.window()
        with patch(
            "browser_window.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.No,
        ):
            window.auto_approval_action.setChecked(True)
        self.assertFalse(self.service.auto_approve_requests)
        self.assertFalse(window.auto_approval_action.isChecked())
        with patch(
            "browser_window.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window.auto_approval_action.setChecked(True)
        self.assertTrue(self.service.auto_approve_requests)
        self.assertTrue(window.auto_approval_notice.isVisible())
        window.auto_approval_action.setChecked(False)
        task = self.task()
        future = self.service.client(task).submit("set_auto_approval", enabled=True)
        self.wait_for(future.done)
        with self.assertRaises(TaskPermissionError):
            future.result()
        self.assertFalse(self.service.auto_approve_requests)
