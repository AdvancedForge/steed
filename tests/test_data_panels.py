"""Native panel ownership, immutable sources, profile identity and user-only review."""

import json
import sqlite3
import unittest
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch

import test_task_profiles as profiles
from PyQt6 import sip
from PyQt6.QtWidgets import QPlainTextEdit

from browser_window import BrowserWindow
from data_panel_format import PANEL_EXAMPLE, parse_panel, widget_content
from layout_format import export_layout
from layout_preferences import preferences_from_config
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.data_panels import DataPanelReviewDialog
from ui.task_dialog import TaskDialog


class PanelFormatTests(unittest.TestCase):
    def source(self, change=None):
        document = deepcopy(PANEL_EXAMPLE)
        if change:
            change(document)
        return json.dumps(document)

    def test_valid_native_widgets_and_malformed_executable_declarations(self):
        self.assertEqual(parse_panel(self.source()).name, "Task dashboard")
        for change in (
            lambda d: d.update(version=True),
            lambda d: d.update(version=8),
            lambda d: d.update(callback="exec"),
            lambda d: d.update(profile="existing:work"),
            lambda d: d.update(widgets=[]),
            lambda d: d.update(widgets=d["widgets"] * 5),
            lambda d: d["widgets"][0].update(kind="html"),
            lambda d: d["widgets"][0].update(title="Misleading\u202e"),
            lambda d: d["widgets"][1].update(artifact_id="../../foreign"),
            lambda d: d["widgets"][1].update(id=d["widgets"][0]["id"]),
            lambda d: d["widgets"][1].update(action="app.call_tool"),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_panel(self.source(change))
        for source in (
            "",
            "[]",
            "[" * 2000,
            " " * 16385,
            "\ud800",
            '{"version":1,"version":1}',
        ):
            with self.subTest(source=source[:25]), self.assertRaises(ValueError):
                parse_panel(source)

    def test_list_contract_and_limits_do_not_turn_content_into_actions(self):
        self.assertEqual(
            widget_content("list", '["<a>Plain item</a>","second"]'),
            ("<a>Plain item</a>", "second"),
        )
        self.assertEqual(widget_content("list", "[]"), ())
        for content in (
            '{"items":[]}',
            "[1]",
            '[" "]',
            "invalid",
            json.dumps(["x"] * 65),
            json.dumps(["x" * 513]),
        ):
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                widget_content("list", content)


class DataPanelTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for
    command = profiles.TaskProfileTests.command
    request = profiles.TaskProfileTests.request

    def task(self, *, grant=True):
        task = self.command("task.create", objective="Synthetic profile dashboard")
        if grant:
            requested = self.request(
                task.task_id, actions=["artifact"], max_operations=1000
            )
            self.service.approve_grant(task.task_id, requested.grant_id)
        return task

    def artifact(self, task, content="SYNTHETIC_DRAFT"):
        return self.command(
            "artifact.store",
            task_id=task.task_id,
            title="Synthetic source",
            content=content,
            format="text",
        )

    def source(self, artifact=None):
        document = deepcopy(PANEL_EXAMPLE)
        if artifact:
            document["widgets"][1]["artifact_id"] = artifact.artifact_id
        else:
            document["widgets"] = document["widgets"][:1]
        return json.dumps(document)

    def propose(self, task, artifact=None, request="dashboard"):
        return self.command(
            "panel.propose",
            task_id=task.task_id,
            request_id=request,
            source=self.source(artifact),
        )

    def approve(self, receipt, profile="work"):
        return self.service.data_panels.resolve(
            receipt.task_id, receipt.request_id, approve=True, profile=profile
        )

    def publish(self, task, artifact, *, update="update-one", revision=0):
        return self.command(
            "panel.publish",
            task_id=task.task_id,
            request_id="dashboard",
            update_id=update,
            expected_revision=revision,
            bindings=[{"id": "summary", "artifact_id": artifact.artifact_id}],
        )

    def window(self, profile="work"):
        window = BrowserWindow()
        self.windows.append(window)
        window._switch_to_profile(profile)
        window.show()
        return window

    def test_discovery_and_task_summary_need_no_grant_and_never_approve(self):
        task = self.task(grant=False)
        capabilities = self.command("panel.capabilities", task_id=task.task_id)
        self.assertTrue(capabilities["available"])
        receipt = self.propose(task)
        self.assertEqual(receipt.state, "awaiting_approval")
        self.assertEqual(self.service.store.grants(task.task_id), [])
        client = self.service.client(task.task_id)
        denied = client.submit(
            "panel.resolve", request_id=receipt.request_id, approve=True
        )
        self.app.processEvents()
        with self.assertRaises(TaskPermissionError):
            denied.result()
        self.approve(receipt)
        self.assertEqual(len(self.service.data_panels.for_profile("work")), 1)

    def test_artifact_permission_foreign_sources_and_list_validation(self):
        first, second = self.task(), self.task()
        source = self.artifact(first)
        with self.assertRaises(KeyError):
            self.propose(second, source)
        no_access = self.task(grant=False)
        with self.assertRaises(TaskPermissionError):
            self.propose(no_access, source)
        malformed = self.artifact(first, "not a list")
        document = json.loads(self.source(malformed))
        document["widgets"][1]["kind"] = "list"
        with self.assertRaises(ValueError):
            self.command(
                "panel.propose",
                task_id=first.task_id,
                request_id="bad-list",
                source=json.dumps(document),
            )
        self.assertEqual(self.service.data_panels.list(first.task_id), [])

    def test_duplicate_requests_are_pinned_and_proposals_do_not_install(self):
        task = self.task()
        artifact = self.artifact(task)
        first = self.propose(task, artifact)
        remaining = self.service._grants[task.task_id][1].operations
        self.assertEqual(self.propose(task, artifact), first)
        self.assertEqual(self.service._grants[task.task_id][1].operations, remaining)
        with self.assertRaises(ValueError):
            self.propose(task)
        self.assertEqual(self.service.data_panels.for_profile("work"), [])
        self.assertNotIn("SYNTHETIC_DRAFT", json.dumps(asdict(first)))

    def test_publication_revisions_idempotency_and_component_bounds(self):
        task = self.task()
        original = self.artifact(task)
        receipt = self.propose(task, original)
        replacement = self.artifact(task, "UPDATED_DRAFT")
        with self.assertRaises(ValueError):
            self.publish(task, replacement)
        self.approve(receipt)
        update = self.publish(task, replacement)
        self.assertEqual(update.revision, 1)
        self.assertEqual(self.publish(task, replacement), update)
        with self.assertRaises(ValueError):
            self.publish(task, original)
        with self.assertRaises(ValueError):
            self.publish(task, original, update="next", revision=0)
        with self.assertRaises(ValueError):
            self.command(
                "panel.publish",
                task_id=task.task_id,
                request_id="dashboard",
                update_id="unknown-widget",
                expected_revision=1,
                bindings=[{"id": "progress", "artifact_id": replacement.artifact_id}],
            )
        self.assertEqual(
            self.service.data_panels.view(task.task_id, receipt.request_id)[1][
                "content"
            ],
            "UPDATED_DRAFT",
        )
        events = str(self.service.store.events(task.task_id))
        self.assertNotIn("UPDATED_DRAFT", events)
        self.assertNotIn("SYNTHETIC_DRAFT", events)

    def test_cross_task_status_publication_and_user_removal(self):
        task = self.task()
        artifact = self.artifact(task)
        receipt = self.propose(task, artifact)
        self.approve(receipt)
        other = self.task()
        with self.assertRaises(KeyError):
            self.command(
                "panel.status", task_id=other.task_id, request_id=receipt.request_id
            )
        foreign = self.artifact(other)
        with self.assertRaises(KeyError):
            self.publish(task, foreign)
        self.service.data_panels.remove(task.task_id, receipt.request_id)
        with self.assertRaises(ValueError):
            self.publish(task, artifact)
        self.assertEqual(self.service.data_panels.for_profile("work"), [])
        self.assertEqual(
            self.service.store.artifact(task.task_id, artifact.artifact_id).content,
            "SYNTHETIC_DRAFT",
        )

    def test_ending_task_freezes_approved_drafts_and_cancels_pending(self):
        task = self.task()
        artifact = self.artifact(task)
        approved = self.propose(task, artifact)
        self.approve(approved)
        pending = self.propose(task, artifact, request="pending")
        self.command("task.finish", task_id=task.task_id, status="completed")
        self.assertEqual(
            self.service.data_panels.get(task.task_id, pending.request_id).state,
            "cancelled",
        )
        self.assertEqual(
            self.service.data_panels.get(task.task_id, approved.request_id).state,
            "applied",
        )
        self.assertEqual(
            self.service.data_panels.view(task.task_id, approved.request_id)[1][
                "content"
            ],
            "SYNTHETIC_DRAFT",
        )
        with self.assertRaises(TaskPermissionError):
            self.publish(task, artifact)

    def test_expired_artifact_authority_cannot_approve_or_publish(self):
        task = self.task()
        artifact = self.artifact(task)
        receipt = self.propose(task, artifact)
        self.service._grants[task.task_id][1].end("expired")
        with self.assertRaises(TaskPermissionError):
            self.approve(receipt)
        self.assertEqual(
            self.service.data_panels.get(task.task_id, receipt.request_id).state,
            "awaiting_approval",
        )

    def test_renamed_profile_keeps_panel_and_recreated_profile_does_not(self):
        task = self.task()
        artifact = self.artifact(task)
        self.approve(self.propose(task, artifact))
        self.assertTrue(self.manager.rename_profile("work", "renamed"))
        self.assertEqual(len(self.service.data_panels.for_profile("renamed")), 1)
        self.publish(task, artifact)
        self.assertTrue(self.manager.delete_profile("renamed"))
        self.manager.create_profile("work")
        self.assertEqual(self.service.data_panels.for_profile("work"), [])
        with self.assertRaises(ValueError):
            self.publish(task, artifact, update="deleted-profile", revision=1)
        self.assertEqual(self.service.data_panels.for_profile("search"), [])

    def test_stale_profile_preview_and_missing_or_changed_artifacts_refuse_apply(self):
        task = self.task()
        artifact = self.artifact(task)
        receipt = self.propose(task, artifact)
        key = self.service.data_panels.profile_key("work", create=True)
        self.manager.delete_profile("work")
        self.manager.create_profile("work")
        with self.assertRaises(ValueError):
            self.service.data_panels.resolve(
                task.task_id,
                receipt.request_id,
                approve=True,
                profile="work",
                profile_key=key,
            )
        self.service.store.delete_artifact(task.task_id, artifact.artifact_id)
        with self.assertRaises(KeyError):
            self.approve(receipt)
        self.service.data_panels.resolve(task.task_id, receipt.request_id)
        self.assertEqual(
            self.service.data_panels.get(task.task_id, receipt.request_id).state,
            "denied",
        )

    def test_profile_widgets_sync_and_clear_immediately_when_profile_changes(self):
        task = self.task()
        artifact = self.artifact(task, "PROFILE_DRAFT <b>plain</b>\u202e")
        self.approve(self.propose(task, artifact))
        first, second, other = self.window(), self.window(), self.window("default")
        for window in (first, second, other):
            window._toggle_data_panels(True)
        self.assertEqual(other.data_panels_dock.selector.count(), 0)
        display = first.data_panels_dock.scroll.widget().displays["summary"]
        self.assertIn("<b>plain</b>\\u202e", display.toPlainText())
        self.assertTrue(display.isReadOnly())
        new = self.artifact(task, "NEW_PROFILE_DRAFT")
        self.publish(task, new)
        self.wait_for(
            lambda: (
                first.data_panels_dock.scroll.widget().displays["summary"].toPlainText()
                == "NEW_PROFILE_DRAFT"
            )
        )
        self.wait_for(
            lambda: (
                second.data_panels_dock.scroll.widget()
                .displays["summary"]
                .toPlainText()
                == "NEW_PROFILE_DRAFT"
            )
        )
        first._switch_to_profile("search")
        self.assertFalse(first.data_panels_action.isEnabled())
        self.assertFalse(first.data_panels_dock.isVisible())
        self.assertIsNone(first.data_panels_dock.scroll.widget())
        self.assertFalse(
            any(
                "NEW_PROFILE_DRAFT" in text.toPlainText()
                for text in first.data_panels_dock.findChildren(QPlainTextEdit)
            )
        )
        first._switch_to_profile("work")
        first._toggle_data_panels(True)
        self.assertEqual(
            first.data_panels_dock.scroll.widget().displays["summary"].toPlainText(),
            "NEW_PROFILE_DRAFT",
        )
        first.data_panels_dock.remove_button.click()
        self.wait_for(lambda: second.data_panels_dock.selector.count() == 0)

    def test_popup_review_is_inert_and_deferred_request_is_manageable(self):
        window = self.window()
        task = self.task()
        artifact = self.artifact(task)
        receipt = self.propose(task, artifact)
        self.wait_for(
            lambda: (
                self.registry.task_approvals.dialog is not None
                and self.registry.task_approvals.dialog.request_key
                == receipt.request_key
            )
        )
        review = self.registry.task_approvals.dialog
        self.assertIsInstance(review, DataPanelReviewDialog)
        self.assertIsNone(window.data_panels_dock)
        review.reject()
        self.wait_for(lambda: self.registry.task_approvals.dialog is None)
        manager = TaskDialog(self.service, window)
        manager.show()
        try:
            for row in range(manager.table.rowCount()):
                if manager.table.item(row, 0).data(256) == task.task_id:
                    manager.table.selectRow(row)
                    break
            manager.refresh_data_panels()
            self.assertEqual(len(manager._data_panel_rows), 1)
            second = DataPanelReviewDialog(self.service, receipt, window)
            second.show()
            second.profiles.setCurrentText("work")
            second.approve_button.click()
            self.wait_for(lambda: window.data_panels_dock is not None)
            self.assertEqual(
                self.service.data_panels.get(task.task_id, receipt.request_id).state,
                "applied",
            )
        finally:
            manager.close()
            sip.delete(manager)

    def test_shown_panel_preserves_selection_and_text_across_refresh(self):
        window = self.window()
        task = self.task()
        artifact = self.artifact(task)
        for request in ("first", "second"):
            self.approve(self.propose(task, artifact, request=request))
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        self.wait_for(lambda: dock.isVisible() and dock.selector.count() == 2)
        dock.selector.setCurrentIndex(1)
        selected = dock.selector.currentData()
        display = dock.scroll.widget().displays["summary"]
        self.assertFalse(display.isVisible())
        self.assertTrue(dock.presentation.isVisible())
        cursor = display.textCursor()
        cursor.select(cursor.SelectionType.Document)
        display.setTextCursor(cursor)
        dock.refresh()
        self.assertEqual(dock.selector.currentData(), selected)
        self.assertIs(dock.scroll.widget().displays["summary"], display)
        self.assertEqual(display.textCursor().selectedText(), "SYNTHETIC_DRAFT")
        self.service.changed.emit()
        self.app.processEvents()
        self.assertEqual(dock.selector.currentData(), selected)
        self.assertFalse(display.isVisible())
        self.assertTrue(dock.presentation.isVisible())

    def test_panels_share_dock_space_without_disrupting_native_chat_tab_ownership(self):
        window = self.window()
        window._toggle_agent_sidebar(True)
        task = self.task()
        artifact = self.artifact(task)
        self.approve(self.propose(task, artifact))
        window._toggle_data_panels(True)
        self.assertIn(
            window.agent_sidebar, window.tabifiedDockWidgets(window.data_panels_dock)
        )
        chat = window._open_agent_tab()
        self.assertIs(window.agent_tab, chat)
        self.assertEqual(
            window.data_panels_dock.scroll.widget().displays["summary"].toPlainText(),
            "SYNTHETIC_DRAFT",
        )
        window._toggle_agent_sidebar(True)
        self.assertIsNone(window.agent_tab)
        window._toggle_data_panels(True)
        self.assertTrue(window.data_panels_dock.isVisible())
        self.assertEqual(window.data_panels_dock.selector.count(), 1)

    def test_saved_drafts_do_not_enter_appearance_exports_or_named_layouts(self):
        task = self.task()
        artifact = self.artifact(task)
        self.approve(self.propose(task, artifact))
        before = self.config.get("browser_layout")
        source = export_layout(preferences_from_config(self.config))
        self.assertNotIn(artifact.artifact_id, source)
        self.assertNotIn("SYNTHETIC_DRAFT", source)
        self.assertEqual(self.config.get("browser_layout"), before)

    def test_publication_storage_failure_rolls_back_all_bindings_and_receipts(self):
        task = self.task()
        original = self.artifact(task)
        self.approve(self.propose(task, original))
        new = self.artifact(task, "NEW_UNCOMMITTED")
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("synthetic"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.publish(task, new)
        row = self.service.store._db.execute(
            "SELECT bindings,revision FROM data_panels WHERE task_id=?", (task.task_id,)
        ).fetchone()
        self.assertEqual(row[1], 0)
        self.assertNotIn(new.artifact_id, row[0])
        self.assertEqual(
            self.service.store._db.execute(
                "SELECT COUNT(*) FROM data_panel_updates"
            ).fetchone()[0],
            0,
        )
        self.assertTrue(self.service.failure)

    def test_user_approval_journal_failure_cannot_install_a_panel(self):
        task = self.task()
        artifact = self.artifact(task)
        receipt = self.propose(task, artifact)
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("synthetic"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.approve(receipt)
        row = self.service.store._db.execute(
            "SELECT state,profile_key FROM data_panels WHERE task_id=?", (task.task_id,)
        ).fetchone()
        self.assertEqual(tuple(row), ("awaiting_approval", ""))
        self.assertTrue(self.service.failure)

    def test_receipt_and_profile_limits_leave_the_service_usable(self):
        task = self.task(grant=False)
        for index in range(3):
            self.propose(task, request=f"pending-{index}")
        with self.assertRaises(TaskPermissionError) as error:
            self.propose(task, request="fourth")
        self.assertEqual(error.exception.code, "panel_proposal_limit")
        for receipt in self.service.data_panels.pending(task.task_id):
            self.approve(receipt)
        for index in range(5):
            self.approve(self.propose(task, request=f"installed-{index}"))
        ninth = self.propose(task, request="ninth")
        with self.assertRaises(TaskPermissionError) as error:
            self.approve(ninth)
        self.assertEqual(error.exception.code, "profile_panel_limit")
        self.assertFalse(self.service.failure)
        self.assertEqual(len(self.service.data_panels.for_profile("work")), 8)

    def test_old_publication_retry_retains_original_revision_and_update_limit(self):
        task = self.task()
        first = self.artifact(task)
        self.approve(self.propose(task, first))
        one = self.publish(task, first)
        second = self.artifact(task, "SECOND_PUBLICATION")
        self.publish(task, second, update="two", revision=1)
        self.assertEqual(self.publish(task, first), one)
        self.assertEqual(
            self.service.data_panels.get(task.task_id, "dashboard").revision, 2
        )
        self.service.store._db.execute(
            "UPDATE data_panels SET revision=256 WHERE task_id=?", (task.task_id,)
        )
        self.service.store._db.commit()
        with self.assertRaises(TaskPermissionError) as error:
            self.publish(task, second, update="limit", revision=256)
        self.assertEqual(error.exception.code, "panel_update_limit")
        self.assertFalse(self.service.failure)

    def test_native_list_global_profile_and_missing_source_have_no_fallback(self):
        window = self.window("global")
        task = self.task()
        artifact = self.artifact(
            task, '["<b>Literal item</b>","https://example.test/"]'
        )
        document = json.loads(self.source(artifact))
        document["widgets"][1]["kind"] = "list"
        receipt = self.command(
            "panel.propose",
            task_id=task.task_id,
            request_id="dashboard",
            source=json.dumps(document),
        )
        self.approve(receipt, "global")
        window._toggle_data_panels(True)
        items = window.data_panels_dock.scroll.widget().displays["summary"]
        self.assertEqual(items.item(0).text(), "<b>Literal item</b>")
        self.assertEqual(items.item(1).text(), "https://example.test/")
        self.assertEqual(self.service.data_panels.for_profile("work"), [])
        self.service.store.delete_artifact(task.task_id, artifact.artifact_id)
        self.service.changed.emit()
        self.wait_for(lambda: window.data_panels_dock.scroll.widget() is None)
        self.assertIn("unavailable", window.data_panels_dock.status.text())
        self.assertTrue(window.data_panels_dock.remove_button.isEnabled())
        window.data_panels_dock.remove_button.click()
        self.assertEqual(self.service.data_panels.for_profile("global"), [])

    def test_revocation_stops_updates_and_preserves_disclosed_saved_drafts(self):
        window = self.window()
        task = self.task()
        artifact = self.artifact(task)
        self.approve(self.propose(task, artifact))
        window._toggle_data_panels(True)
        grant_id = self.service._grants[task.task_id][0]
        self.service.revoke_grant(task.task_id, grant_id)
        self.wait_for(
            lambda: "updates stopped" in window.data_panels_dock.status.text()
        )
        self.assertEqual(
            window.data_panels_dock.scroll.widget().displays["summary"].toPlainText(),
            "SYNTHETIC_DRAFT",
        )
        with self.assertRaises(TaskPermissionError):
            self.publish(task, artifact)

    def test_tampered_source_and_artifact_cannot_change_the_reviewed_panel(self):
        task = self.task()
        artifact = self.artifact(task)
        receipt = self.propose(task, artifact)
        self.service.store._db.execute(
            "UPDATE artifacts SET content='CHANGED' WHERE artifact_id=?",
            (artifact.artifact_id,),
        )
        self.service.store._db.commit()
        with self.assertRaises(ValueError):
            self.approve(receipt)
        self.assertEqual(
            self.service.data_panels.get(task.task_id, receipt.request_id).state,
            "awaiting_approval",
        )
        self.service.store._db.execute(
            "UPDATE data_panels SET source=? WHERE task_id=?",
            (self.source(), task.task_id),
        )
        self.service.store._db.commit()
        with self.assertRaises(ValueError):
            self.service.data_panels.definition(task.task_id, receipt.request_id)

    def test_restart_interrupts_requests_and_keeps_profile_bound_saved_drafts(self):
        task = self.task()
        artifact = self.artifact(task)
        receipt = self.propose(task, artifact)
        self.approve(receipt)
        pending = self.propose(task, artifact, request="pending")
        path = self.config.config_dir / "tasks.sqlite"
        self.service.close()
        restarted = TaskService(path, profiles=self.registry)
        try:
            self.assertEqual(
                restarted.store._db.execute("PRAGMA user_version").fetchone()[0], 21
            )
            self.assertEqual(
                restarted.data_panels.get(task.task_id, pending.request_id).state,
                "interrupted",
            )
            self.assertEqual(len(restarted.data_panels.for_profile("work")), 1)
            self.assertEqual(
                restarted.data_panels.view(task.task_id, receipt.request_id)[1][
                    "content"
                ],
                "SYNTHETIC_DRAFT",
            )
            with self.assertRaises(TaskPermissionError):
                restarted.data_panels.publish(
                    task.task_id,
                    receipt.request_id,
                    "new",
                    0,
                    [{"id": "summary", "artifact_id": artifact.artifact_id}],
                )
        finally:
            restarted.close()
            sip.delete(restarted)
