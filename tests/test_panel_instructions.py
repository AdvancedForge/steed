"""Reusable literal instructions, explicit recipient review and atomic delivery."""

import json
import sqlite3
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_data_panels as panels
from PyQt6 import sip
from PyQt6.QtWidgets import QDialogButtonBox, QPlainTextEdit

from data_panel_format import panel_capabilities, parse_panel
from ui.data_panels import DataPanelReviewDialog

EXAMPLE = {
    "version": 6,
    "name": "Daily research",
    "widgets": [
        {
            "id": "research",
            "kind": "instruction",
            "title": "Research roles",
            "instruction": "Find suitable roles. Ask for access to my enabled files as needed.",
        }
    ],
}


class InstructionFormatTests(unittest.TestCase):
    def test_literal_bounded_instruction_and_old_grammar(self):
        published = panel_capabilities()["instruction_example"]
        self.assertEqual(
            parse_panel(json.dumps(published)).widgets[0]["kind"], "instruction"
        )
        source = json.dumps(EXAMPLE)
        self.assertEqual(parse_panel(source).source, source)
        for change in (
            lambda d: d.update(version=5),
            lambda d: d["widgets"][0].update(instruction=""),
            lambda d: d["widgets"][0].update(instruction="🙂" * 1025),
            lambda d: d["widgets"][0].update(instruction="\u202e"),
            lambda d: d["widgets"][0].update(instruction=["callback"]),
            lambda d: d["widgets"][0].update(recipient="automatic"),
            lambda d: d["widgets"][0].update(
                library={"folder_id": "folder", "path": "private"}
            ),
            lambda d: d["widgets"][0].update(action="app.call_tool"),
        ):
            document = deepcopy(EXAMPLE)
            change(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                parse_panel(json.dumps(document))

    def test_library_definition_keeps_its_original_source_string(self):
        source = json.dumps(
            {
                "version": 4,
                "name": "Notes",
                "widgets": [
                    {
                        "id": "notes",
                        "kind": "text",
                        "title": "Notes",
                        "library": {"folder_id": "folder", "path": "notes.txt"},
                    },
                ],
            }
        )
        self.assertEqual(parse_panel(source).source, source)


class PanelInstructionTests(unittest.TestCase):
    setUpClass = classmethod(panels.DataPanelTests.setUpClass.__func__)
    tearDownClass = classmethod(panels.DataPanelTests.tearDownClass.__func__)
    setUp = panels.DataPanelTests.setUp
    tearDown = panels.DataPanelTests.tearDown
    command = panels.DataPanelTests.command
    window = panels.DataPanelTests.window
    wait_for = panels.DataPanelTests.wait_for

    def setup_panel(self, *, approve=True, finish=True):
        self.owner = self.command("task.create", objective="Panel creator")
        self.receipt = self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="daily",
            source=json.dumps(EXAMPLE),
        )
        self.key = self.service.data_panels.profile_key("work", create=True)
        if approve:
            self.service.data_panels.resolve(
                self.owner.task_id, "daily", approve=True, profile="work"
            )
        if finish:
            self.command("task.finish", task_id=self.owner.task_id)
        self.target = self.command(
            "task.create", objective="Selected external conversation"
        )
        self.service.external_connection(self.target.task_id, True)

    def submit(self, submission="first", recipient=None, key=None):
        return self.service.data_panels.submit_instruction(
            self.owner.task_id,
            "daily",
            "research",
            submission,
            recipient or self.target.task_id,
            profile_key=key or self.key,
        )

    def test_finished_owner_reusable_button_delivers_only_literal_to_selected_task(
        self,
    ):
        self.setup_panel()
        notifications = []
        self.service.user_message_sent.connect(lambda *args: notifications.append(args))
        receipt = self.submit()
        self.assertEqual(receipt["state"], "queued")
        self.assertEqual(notifications[0][0], self.target.task_id)
        page = self.service.messages.read(self.target.task_id)
        message = json.loads(page["messages"][0]["text"])
        self.assertEqual(message["instruction"], EXAMPLE["widgets"][0]["instruction"])
        self.assertEqual(self.service.messages.read(self.owner.task_id)["messages"], [])
        self.assertEqual(self.service.store.grants(self.target.task_id), [])
        self.assertNotIn(
            message["instruction"], str(self.service.store.events(self.owner.task_id))
        )
        denied = self.service.client(self.target.task_id).submit(
            "panel.submit_instruction"
        )
        self.app.processEvents()
        with self.assertRaises(PermissionError):
            denied.result()

    def test_retry_after_clear_and_recipient_end_never_renotifies_or_retargets(self):
        self.setup_panel()
        notifications = []
        self.service.user_message_sent.connect(lambda *args: notifications.append(args))
        receipt = self.submit()
        self.service.clear_messages(self.target.task_id)
        self.command("task.finish", task_id=self.target.task_id)
        self.assertEqual(self.submit(), receipt)
        other = self.command("task.create", objective="Other conversation")
        self.service.external_connection(other.task_id, True)
        with self.assertRaises(ValueError):
            self.submit(recipient=other.task_id)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(self.service.messages.read(other.task_id)["messages"], [])
        self.assertEqual(
            self.service.messages.read(self.target.task_id)["messages"][0]["text"], ""
        )
        self.submit("second", recipient=other.task_id)
        self.assertEqual(len(notifications), 2)

    def test_unapproved_removed_wrong_profile_and_disconnected_targets_are_refused(
        self,
    ):
        self.setup_panel(approve=False, finish=False)
        with self.assertRaises(ValueError):
            self.submit()
        self.service.data_panels.resolve(
            self.owner.task_id, "daily", approve=True, profile="work"
        )
        with self.assertRaises(ValueError):
            self.submit(key="foreign")
        self.service.external_connection(self.target.task_id, False)
        with self.assertRaises(ValueError):
            self.submit()
        self.service.external_connection(self.target.task_id, True)
        self.service.data_panels.remove(self.owner.task_id, "daily")
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(
            self.service.messages.read(self.target.task_id)["messages"], []
        )

    def test_audit_failure_rolls_back_delivery_and_freezes_service(self):
        self.setup_panel()
        notifications = []
        self.service.user_message_sent.connect(lambda *args: notifications.append(args))
        append = self.service.store._append

        def fail(task, kind, payload):
            if kind == "panel.instruction_queued":
                raise sqlite3.OperationalError("synthetic")
            return append(task, kind, payload)

        with patch.object(self.service.store, "_append", side_effect=fail):
            with self.assertRaises(sqlite3.Error):
                self.submit()
        self.assertTrue(self.service.failure)
        self.assertEqual(
            self.service.messages.read(self.target.task_id)["messages"], []
        )
        self.assertFalse(notifications)

    def test_linked_contents_are_not_attached_and_read_access_is_not_send_authority(
        self,
    ):
        self.setup_panel(finish=False)
        folder = self.service.library.create_folder("Selected notes")
        self.service.library.import_files(
            folder, [("note.txt", b"PRIVATE_LINKED_CONTENT")]
        )
        self.service.library.enable(folder, True)
        document = deepcopy(EXAMPLE)
        document["widgets"].append(
            {
                "id": "note",
                "kind": "text",
                "title": "Note",
                "library": {"folder_id": folder, "path": "note.txt"},
            }
        )
        self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="mixed",
            source=json.dumps(document),
        )
        self.service.data_panels.resolve(
            self.owner.task_id,
            "mixed",
            approve=True,
            profile="work",
            library_access=True,
        )
        self.assertEqual(
            self.service.data_panels.view(self.owner.task_id, "mixed")[1]["content"],
            "PRIVATE_LINKED_CONTENT",
        )
        self.command("task.finish", task_id=self.owner.task_id)
        self.service.library.enable(folder, False)
        self.service.data_panels.submit_instruction(
            self.owner.task_id,
            "mixed",
            "research",
            "explicit",
            self.target.task_id,
            profile_key=self.key,
        )
        message = self.service.messages.read(self.target.task_id)["messages"][0]["text"]
        self.assertNotIn("PRIVATE_LINKED_CONTENT", message)
        self.assertNotIn(folder, message)
        self.assertEqual(self.service.store.grants(self.target.task_id), [])

    def test_profile_identity_survives_rename_but_not_recreation(self):
        self.setup_panel()
        self.assertTrue(self.manager.rename_profile("work", "renamed"))
        self.submit()
        self.assertTrue(self.manager.delete_profile("renamed"))
        self.assertTrue(self.manager.create_profile("work"))
        with self.assertRaises(ValueError):
            self.submit("new")
        self.assertEqual(
            len(self.service.messages.read(self.target.task_id)["messages"]), 1
        )

    def test_review_is_inert_and_native_send_requires_explicit_recipient(self):
        self.setup_panel(approve=False, finish=False)
        review = DataPanelReviewDialog(self.service, self.receipt)
        review.show()
        self.assertFalse(review.preview.widget().displays["research"].send.isEnabled())
        review.reject()
        self.service.data_panels.resolve(
            self.owner.task_id, "daily", approve=True, profile="work"
        )
        window = self.window()
        window._toggle_data_panels(True)
        control = window.data_panels_dock.scroll.widget().displays["research"]
        control.send.click()
        dialog, send = control.preview, control.preview_send
        self.assertFalse(send.isEnabled())
        self.assertEqual(
            self.service.messages.read(self.target.task_id)["messages"], []
        )
        self.assertIn(
            EXAMPLE["widgets"][0]["instruction"],
            dialog.findChild(QPlainTextEdit).toPlainText(),
        )
        self.assertTrue(
            dialog.findChild(QDialogButtonBox)
            .button(QDialogButtonBox.StandardButton.Cancel)
            .isDefault()
        )
        control.recipient.setCurrentIndex(
            control.recipient.findData(self.target.task_id)
        )
        self.assertTrue(send.isEnabled())
        send.click()
        send.click()
        self.assertIsNone(control.preview)
        self.assertEqual(
            len(self.service.messages.read(self.target.task_id)["messages"]), 1
        )
        self.assertIn("Queued", control.notice.text())
        # A deliberate later review may send the same reusable instruction again.
        control.send.click()
        control.recipient.setCurrentIndex(
            control.recipient.findData(self.target.task_id)
        )
        control.preview_send.click()
        self.assertEqual(
            len(self.service.messages.read(self.target.task_id)["messages"]), 2
        )

    def test_disconnect_invalidates_selection_and_profile_switch_closes_preview(self):
        self.setup_panel()
        window = self.window()
        window._toggle_data_panels(True)
        control = window.data_panels_dock.scroll.widget().displays["research"]
        control.send.click()
        control.recipient.setCurrentIndex(
            control.recipient.findData(self.target.task_id)
        )
        self.service.external_connection(self.target.task_id, False)
        self.assertFalse(control.preview_send.isEnabled())
        self.service.external_connection(self.target.task_id, True)
        self.assertIsNone(control.recipient.currentData())
        preview = control.preview
        window._switch_to_profile("search")
        if not sip.isdeleted(preview):
            self.assertFalse(preview.isVisible())
        self.assertEqual(
            self.service.messages.read(self.target.task_id)["messages"], []
        )

    def test_restarted_panel_remains_reusable_without_resurrecting_recipient(self):
        self.setup_panel()
        self.service.close()
        from services.task_service import TaskService

        restarted = TaskService(
            self.config.config_dir / "tasks.sqlite", profiles=self.registry
        )
        try:
            self.assertEqual(restarted.data_panels.instruction_recipients(), [])
            target = restarted._create("New connected agent")
            restarted.external_connection(target.task_id, True)
            restarted.data_panels.submit_instruction(
                self.owner.task_id,
                "daily",
                "research",
                "after-restart",
                target.task_id,
                profile_key=self.key,
            )
            self.assertEqual(
                len(restarted.messages.read(target.task_id)["messages"]), 1
            )
        finally:
            restarted.close()
