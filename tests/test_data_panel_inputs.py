"""Native form consent, task-bound delivery, retries and literal table rendering."""

import json
import sqlite3
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_data_panels as panels
from PyQt6 import sip
from PyQt6.QtWidgets import QDialogButtonBox, QLineEdit, QPlainTextEdit, QPushButton

from browser.data_panel_format import (
    FORM_EXAMPLE,
    form_values,
    parse_panel,
    widget_content,
)
from ui.data_panels import DataPanelReviewDialog


class InputFormatTests(unittest.TestCase):
    def test_versions_and_strict_form_fields(self):
        definition = parse_panel(json.dumps(FORM_EXAMPLE))
        self.assertEqual(definition.artifact_widgets, {})
        for change in (
            lambda d: d.update(version=1),
            lambda d: d["widgets"][0].update(callback="send"),
            lambda d: d["widgets"][0].update(artifact_id="foreign"),
            lambda d: d["widgets"][0].update(fields=[]),
            lambda d: d["widgets"][0]["fields"][0].update(required=1),
            lambda d: d["widgets"][0]["fields"][0].update(default="private"),
            lambda d: d["widgets"][0]["fields"][1].update(options=["same", "same"]),
            lambda d: d["widgets"][0]["fields"][1].update(options=["\u202e"]),
        ):
            document = deepcopy(FORM_EXAMPLE)
            change(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                parse_panel(json.dumps(document))

    def test_explicit_values_and_table_bounds(self):
        form = FORM_EXAMPLE["widgets"][0]
        self.assertEqual(
            form_values(form, {"focus": "Research", "depth": ""})["depth"], ""
        )
        for values in (
            {"focus": "", "depth": "Brief"},
            {"focus": "x", "depth": "Unreviewed"},
            {"focus": "x" * 513, "depth": ""},
            {"focus": "\u202e", "depth": ""},
            {"focus": "x", "depth": "", "extra": "private"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                form_values(form, values)
        good = {"columns": ["Option", "Notes"], "rows": [["A", "<b>Literal</b>"]]}
        self.assertEqual(
            widget_content("table", json.dumps(good))["rows"][0][1], "<b>Literal</b>"
        )
        for value in (
            {"columns": ["same", "same"], "rows": []},
            {"columns": ["A"], "rows": [[1]]},
            {"columns": ["A"], "rows": [["x", "extra"]]},
            {"columns": ["A"], "rows": [["x"]] * 65},
            {"columns": ["A"], "rows": [["x" * 513]]},
            {"columns": ["A"], "rows": [], "callback": "execute"},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                widget_content("table", json.dumps(value))


class PanelInputTests(unittest.TestCase):
    setUpClass = classmethod(panels.DataPanelTests.setUpClass.__func__)
    tearDownClass = classmethod(panels.DataPanelTests.tearDownClass.__func__)
    setUp = panels.DataPanelTests.setUp
    tearDown = panels.DataPanelTests.tearDown
    command = panels.DataPanelTests.command
    request = panels.DataPanelTests.request
    wait_for = panels.DataPanelTests.wait_for
    task = panels.DataPanelTests.task
    artifact = panels.DataPanelTests.artifact
    window = panels.DataPanelTests.window
    approve = panels.DataPanelTests.approve

    def proposal(self, task, artifact=None):
        document = deepcopy(FORM_EXAMPLE)
        if artifact:
            document["widgets"].insert(
                0,
                {
                    "id": "summary",
                    "kind": "text",
                    "title": "Summary",
                    "artifact_id": artifact.artifact_id,
                },
            )
        return self.command(
            "panel.propose",
            task_id=task.task_id,
            request_id="inputs",
            source=json.dumps(document),
        )

    def enabled(self):
        task = self.task(grant=False)
        receipt = self.proposal(task)
        self.assertEqual(self.service.messages.summary(task.task_id)["pending"], 1)
        self.approve(receipt)
        self.service.external_connection(task.task_id, True)
        return task, self.service.data_panels.profile_key("work")

    def submit(self, task, key, *, submission="first", values=None):
        return self.service.data_panels.submit_form(
            task.task_id,
            "inputs",
            "preferences",
            submission,
            values or {"focus": "PRIVATE_FORM_VALUE", "depth": "Brief"},
            profile_key=key,
        )

    def test_forms_need_no_data_grant_and_review_never_sends(self):
        window = self.window()
        task = self.task(grant=False)
        receipt = self.proposal(task)
        review = DataPanelReviewDialog(self.service, receipt, window)
        review.show()
        self.app.processEvents()
        form = review.preview.widget().displays["preferences"]
        self.assertFalse(form.send.isEnabled())
        self.assertFalse(form.inputs["focus"].isEnabled())
        self.assertFalse(form.send.autoDefault())
        self.assertTrue(review.deny_button.isDefault())
        review.approve_button.click()
        self.assertEqual(self.service.messages.summary(task.task_id)["pending"], 0)
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])
        self.assertEqual(self.service.store.grants(task.task_id), [])

    def test_submission_is_exact_task_scoped_and_not_a_broker_command(self):
        task, key = self.enabled()
        other = self.task(grant=False)
        notices = []
        self.service.user_message_sent.connect(lambda *args: notices.append(args))
        receipt = self.submit(task, key)
        self.assertEqual(receipt["state"], "queued")
        self.assertEqual(len(notices), 1)
        page = self.command("conversation.read", task_id=task.task_id)
        payload = json.loads(page["messages"][0]["text"])
        self.assertEqual(payload["fields"][0]["value"], "PRIVATE_FORM_VALUE")
        self.assertEqual(self.service.messages.read(other.task_id)["messages"], [])
        self.assertNotIn(
            "PRIVATE_FORM_VALUE", str(self.service.store.events(task.task_id))
        )
        denied = self.service.client(task.task_id).submit("panel.submit_form")
        self.app.processEvents()
        with self.assertRaises(PermissionError):
            denied.result()

    def test_retries_do_not_renotify_even_after_clear_and_task_end(self):
        task, key = self.enabled()
        notices = []
        self.service.user_message_sent.connect(lambda *args: notices.append(args))
        receipt = self.submit(task, key)
        self.assertEqual(self.submit(task, key), receipt)
        with self.assertRaises(ValueError):
            self.submit(task, "foreign")
        with self.assertRaises(ValueError):
            self.submit(task, key, values={"focus": "Changed", "depth": "Brief"})
        self.service.clear_messages(task.task_id)
        self.command("task.finish", task_id=task.task_id)
        self.assertEqual(self.submit(task, key), receipt)
        self.assertEqual(len(notices), 1)
        self.assertEqual(
            self.service.messages.read(task.task_id)["messages"][0]["text"], ""
        )
        with self.assertRaises(OSError):
            self.submit(task, key, submission="another")

    def test_unapproved_foreign_profile_disconnected_removed_and_ended_refuse_delivery(
        self,
    ):
        task = self.task(grant=False)
        receipt = self.proposal(task)
        key = self.service.data_panels.profile_key("work", create=True)
        self.service.external_connection(task.task_id, True)
        with self.assertRaises(ValueError):
            self.submit(task, key)
        self.approve(receipt)
        with self.assertRaises(ValueError):
            self.submit(task, "foreign")
        self.service.external_connection(task.task_id, False)
        with self.assertRaises(ValueError):
            self.submit(task, key)
        self.service.external_connection(task.task_id, True)
        self.service.data_panels.remove(task.task_id, "inputs")
        with self.assertRaises(ValueError):
            self.submit(task, key)
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])

    def test_form_receipt_failure_rolls_back_message_and_notifies_nobody(self):
        task, key = self.enabled()
        notices = []
        self.service.user_message_sent.connect(lambda *args: notices.append(args))
        original = self.service.store._append

        def fail(task_id, kind, payload):
            if kind == "panel.form_queued":
                raise sqlite3.OperationalError("synthetic")
            return original(task_id, kind, payload)

        with patch.object(self.service.store, "_append", side_effect=fail):
            with self.assertRaises(sqlite3.Error):
                self.submit(task, key)
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])
        self.assertFalse(notices)
        self.assertTrue(self.service.failure)

    def test_form_payload_aggregate_byte_limit(self):
        task = self.task(grant=False)
        document = deepcopy(FORM_EXAMPLE)
        document["widgets"][0]["fields"] = [
            {
                "id": f"field{i}",
                "kind": "text",
                "title": f"Field {i}",
                "required": False,
            }
            for i in range(8)
        ]
        self.command(
            "panel.propose",
            task_id=task.task_id,
            request_id="large",
            source=json.dumps(document),
        )
        with self.assertRaises(ValueError):
            self.service.data_panels.form_payload(
                task.task_id,
                "large",
                "preferences",
                {f"field{i}": "🙂" * 512 for i in range(8)},
            )

    def test_values_focus_and_preview_survive_artifact_update_and_disconnect(self):
        window = self.window()
        task = self.task()
        artifact = self.artifact(task)
        self.approve(self.proposal(task, artifact))
        self.service.external_connection(task.task_id, True)
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        form = dock.scroll.widget().displays["preferences"]
        form.inputs["focus"].setText("UNSENT_PRIVATE_VALUE")
        form.inputs["focus"].setFocus()
        form.review()
        self.assertIsNotNone(form.preview)
        preview = form.preview
        replacement = self.artifact(task, "UPDATED_DRAFT")
        self.command(
            "panel.publish",
            task_id=task.task_id,
            request_id="inputs",
            update_id="update",
            expected_revision=0,
            bindings=[{"id": "summary", "artifact_id": replacement.artifact_id}],
        )
        self.app.processEvents()
        self.assertIs(dock.scroll.widget().displays["preferences"], form)
        self.assertIs(form.preview, preview)
        self.assertEqual(form.inputs["focus"].text(), "UNSENT_PRIVATE_VALUE")
        self.service.external_connection(task.task_id, False)
        self.app.processEvents()
        self.assertFalse(form.preview_send.isEnabled())
        self.service.external_connection(task.task_id, True)
        self.app.processEvents()
        self.assertTrue(form.preview_send.isEnabled())
        preview.reject()
        self.assertTrue(form.inputs["focus"].isEnabled())
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])

    def test_explicit_ui_preview_send_and_double_click(self):
        window = self.window()
        task, key = self.enabled()
        window._toggle_data_panels(True)
        form = window.data_panels_dock.scroll.widget().displays["preferences"]
        form.send.click()
        self.assertIsNone(form.preview)
        self.assertIn("Focus:", form.notice.text())
        form.inputs["focus"].setText("EXPLICIT_VALUES")
        form.send.click()
        self.assertIsNotNone(form.preview)
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])
        content = form.preview.findChild(QPlainTextEdit)
        self.assertIn("EXPLICIT_VALUES", content.toPlainText())
        self.assertNotIn("steed_panel_form", content.toPlainText())
        details = next(
            b
            for b in form.preview.findChildren(QPushButton)
            if b.text() == "Show message details"
        )
        details.click()
        self.assertEqual(
            json.loads(content.toPlainText())["fields"][0]["value"], "EXPLICIT_VALUES"
        )
        buttons = form.preview.findChild(QDialogButtonBox)
        self.assertTrue(
            buttons.button(QDialogButtonBox.StandardButton.Cancel).isDefault()
        )
        send = next(b for b in buttons.buttons() if b.text() == "Send to agent")
        send.click()
        self.assertIsNone(form.preview)
        self.assertEqual(len(self.service.messages.read(task.task_id)["messages"]), 1)
        form.send.click()
        self.assertIsNone(form.preview)
        self.assertEqual(len(self.service.messages.read(task.task_id)["messages"]), 1)

    def test_profile_switch_scrubs_unsent_values_and_closes_preview(self):
        window = self.window()
        task, key = self.enabled()
        window._toggle_data_panels(True)
        form = window.data_panels_dock.scroll.widget().displays["preferences"]
        form.inputs["focus"].setText("PRIVATE_UNSENT")
        form.review()
        preview = form.preview
        window._switch_to_profile("search")
        if not sip.isdeleted(form):
            self.assertEqual(form.inputs["focus"].text(), "")
        if not sip.isdeleted(preview):
            self.assertFalse(preview.isVisible())
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])
        self.assertFalse(
            any(e.text() for e in window.data_panels_dock.findChildren(QLineEdit))
        )

    def test_native_table_is_literal_read_only_and_owned(self):
        window = self.window()
        task = self.task()
        artifact = self.artifact(
            task,
            json.dumps(
                {
                    "columns": ["Option", "Notes"],
                    "rows": [["A", "<b>Plain</b>"], ["B", "https://example.test/"]],
                }
            ),
        )
        source = json.dumps(
            {
                "version": 2,
                "name": "Compare",
                "widgets": [
                    {
                        "id": "comparison",
                        "kind": "table",
                        "title": "Options",
                        "artifact_id": artifact.artifact_id,
                    }
                ],
            }
        )
        receipt = self.command(
            "panel.propose", task_id=task.task_id, request_id="table", source=source
        )
        self.approve(receipt)
        window._toggle_data_panels(True)
        table = window.data_panels_dock.scroll.widget().displays["comparison"]
        self.assertEqual(table.item(0, 1).text(), "<b>Plain</b>")
        self.assertEqual(table.rowCount(), 2)
        self.assertFalse(table.editTriggers())
        window._switch_to_profile("search")
        if not sip.isdeleted(table):
            self.assertEqual(table.rowCount(), 0)
            self.assertEqual(table.columnCount(), 0)

    def test_closing_window_clears_input_and_closes_unsent_preview(self):
        window = self.window()
        task, key = self.enabled()
        window._toggle_data_panels(True)
        form = window.data_panels_dock.scroll.widget().displays["preferences"]
        form.inputs["focus"].setText("UNSENT_AT_CLOSE")
        form.review()
        preview = form.preview
        window.close()
        if not sip.isdeleted(form):
            self.assertEqual(form.inputs["focus"].text(), "")
        if not sip.isdeleted(preview):
            self.assertFalse(preview.isVisible())
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])
