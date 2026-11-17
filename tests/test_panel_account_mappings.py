"""Reviewed JSON projections, visible bounds, native revocation and cache hygiene."""

import json
import threading
import unittest
from copy import deepcopy

import test_panel_accounts as accounts_fixture
from PyQt6.QtWidgets import QListWidget, QTableWidget

from browser.data_panel_format import parse_panel
from services.panel_mapping import project_result
from ui.data_panels import DataPanelReviewDialog, source_caption

MAPPING = {
    "items": "/body",
    "columns": [
        {"title": "Title", "value": "/title"},
        {"title": "Score", "value": "/score"},
    ],
}


class MappingFormatTests(unittest.TestCase):
    def widget(self, kind="table"):
        return {
            "kind": kind,
            "mapping": deepcopy(MAPPING)
            if kind == "table"
            else {"items": "", "label": "/title"},
        }

    def test_empty_root_and_escaped_pointer_scalars_are_literal(self):
        widget = self.widget("list")
        result = project_result(widget, '[{"title":"<b>Literal</b>"}]')
        self.assertEqual(result["content"], ("<b>Literal</b>",))
        widget["mapping"]["label"] = "/a~1b~0c"
        self.assertEqual(
            project_result(widget, '[{"a/b~c":true}]')["content"], ("true",)
        )
        widget["mapping"]["label"] = ""
        self.assertEqual(
            project_result(widget, "[null,5,false]")["content"], ("null", "5", "false")
        )

    def test_missing_nonscalar_duplicate_or_nonfinite_fields_fail_without_partial_output(
        self,
    ):
        widget = self.widget()
        for text in (
            "not json",
            '{"body":{}}',
            '{"body":[{"title":"Only title"}]}',
            '{"body":[{"title":[],"score":1}]}',
            '{"body":[{"title":"One","score":NaN}]}',
            '{"body":[],"body":[]}',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                project_result(widget, text)
        with self.assertRaises(ValueError):
            project_result(widget, "[" * 2000)

    def test_row_cell_bounds_are_explicit_and_unselected_fields_do_not_survive(self):
        widget = self.widget()
        rows = [{"title": "A" * 600, "score": 1, "hidden": "PRIVATE_UNSELECTED"}] + [
            {"title": "One", "score": 2}
        ] * 69
        result = project_result(widget, json.dumps({"body": rows}))
        self.assertEqual(
            (result["shown_count"], result["source_count"], result["clipped_cells"]),
            (64, 70, 1),
        )
        self.assertEqual(len(result["content"]["rows"][0][0]), 512)
        self.assertTrue(result["content"]["rows"][0][0].endswith("…"))
        self.assertNotIn("PRIVATE_UNSELECTED", str(result))


class AccountMappingTests(unittest.TestCase):
    setUpClass = classmethod(accounts_fixture.AccountPanelTests.setUpClass.__func__)
    tearDownClass = classmethod(
        accounts_fixture.AccountPanelTests.tearDownClass.__func__
    )
    tearDown = accounts_fixture.AccountPanelTests.tearDown
    command = accounts_fixture.AccountPanelTests.command
    wait_for = accounts_fixture.AccountPanelTests.wait_for
    window = accounts_fixture.AccountPanelTests.window
    propose = accounts_fixture.AccountPanelTests.propose
    view = accounts_fixture.AccountPanelTests.view
    refresh = accounts_fixture.AccountPanelTests.refresh
    finished = accounts_fixture.AccountPanelTests.finished

    def setUp(self):
        accounts_fixture.AccountPanelTests.setUp(self)
        self.source["version"] = 7
        self.source["widgets"][0].update(kind="table", mapping=deepcopy(MAPPING))
        self.http.body["body"] = [
            {"title": "One", "score": 7, "hidden": "PRIVATE_UNSELECTED"}
        ]

    def test_grammar_requires_v7_exact_columns_pointers_and_read_sources(self):
        for change in (
            lambda d: d.update(version=6),
            lambda d: d["widgets"][0]["mapping"].update(items="/body/*"),
            lambda d: d["widgets"][0]["mapping"].update(items="/bad~x"),
            lambda d: d["widgets"][0]["mapping"].update(callback="run"),
            lambda d: d["widgets"][0]["mapping"].update(columns=[]),
            lambda d: d["widgets"][0]["mapping"].update(columns=MAPPING["columns"] * 5),
            lambda d: d["widgets"][0]["mapping"]["columns"][1].update(title="Title"),
            lambda d: d["widgets"][0].update(kind="text"),
        ):
            document = deepcopy(self.source)
            change(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                parse_panel(json.dumps(document))
        parse_panel(json.dumps(self.source))

    def test_manual_after_task_end_keeps_only_projected_values_and_no_model_message(
        self,
    ):
        before = len(self.http.calls)
        self.propose()
        self.assertEqual(len(self.http.calls), before)
        self.command("task.finish", task_id=self.owner.task_id)
        self.assertEqual(len(self.http.calls), before)
        self.refresh()
        self.finished()
        value = self.view()
        self.assertEqual(
            value["content"], {"columns": ("Title", "Score"), "rows": (("One", "7"),)}
        )
        self.assertIn("showing 1 of 1 rows", source_caption(value))
        self.assertNotIn(
            "PRIVATE_UNSELECTED", str(self.service.data_panels.account_reads.results)
        )
        self.assertEqual(self.service.messages.read(self.owner.task_id)["messages"], [])
        self.assertNotIn(
            "PRIVATE_UNSELECTED", str(self.service.store.events(self.owner.task_id))
        )

    def test_bad_refresh_clears_previous_table_and_never_falls_back_to_raw_json(self):
        self.propose()
        self.refresh()
        self.finished()
        self.assertTrue(self.view()["available"])
        self.http.body["body"] = [{"hidden": "PRIVATE_UNSELECTED"}]
        self.refresh()
        self.finished()
        value = self.view()
        self.assertFalse(value["available"])
        self.assertEqual(value["content"]["rows"], ())
        self.assertNotIn("PRIVATE_UNSELECTED", str(value))
        self.assertNotIn("One", str(value))

    def test_native_table_and_pending_review_scrub_on_account_revocation(self):
        self.propose()
        self.refresh()
        self.finished()
        window = self.window()
        window._toggle_data_panels(True)
        content = window.data_panels_dock.scroll.widget()
        table = content.displays["note"]
        self.assertIsInstance(table, QTableWidget)
        self.assertEqual(table.item(0, 0).text(), "One")
        pending = self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="projection-review",
            source=json.dumps(self.source),
        )
        review = DataPanelReviewDialog(self.service, pending, window)
        try:
            self.assertIn("Display projection", review.linked_sources.toPlainText())
            self.assertIn("/score", review.linked_sources.toPlainText())
            self.service.set_connector_access(self.account.connector, "read", False)
            self.assertEqual(table.rowCount(), 0)
            self.assertFalse(table.isEnabled())
            self.assertEqual(content._widgets["note"]["content"]["rows"], ())
            self.service.set_connector_access(self.account.connector, "read", True)
            self.assertFalse(self.view()["available"])
            self.assertEqual(table.rowCount(), 0)
        finally:
            review.close()

    def test_native_list_bounds_caption_and_source_disable_preserve_inert_labels(self):
        self.source["widgets"][0].update(
            kind="list", mapping={"items": "/body", "label": "/title"}
        )
        self.http.body["body"] = [{"title": "<b>Literal</b>"}] * 70
        self.propose()
        self.refresh()
        self.finished()
        window = self.window()
        window._toggle_data_panels(True)
        content = window.data_panels_dock.scroll.widget()
        listing = content.displays["note"]
        self.assertIsInstance(listing, QListWidget)
        self.assertEqual(listing.count(), 64)
        self.assertEqual(listing.item(0).text(), "<b>Literal</b>")
        self.assertIn("showing 64 of 70 rows", content.captions["note"].text())
        self.service.data_panels.set_source_access(
            self.owner.task_id, "account-panel", profile_key=self.key, enabled=False
        )
        self.assertEqual(listing.count(), 0)
        self.assertEqual(content._widgets["note"]["content"], ())

    def test_table_timeout_keeps_native_empty_shape_and_late_reply_stays_hidden(self):
        self.propose()
        window = self.window()
        window._toggle_data_panels(True)
        self.service.connector_timeout_ms = 25
        self.http.gate = threading.Event()
        self.refresh()
        self.wait_for(lambda: self.http.entered.is_set())
        self.wait_for(lambda: not self.service.data_panels.account_reads.jobs)
        self.assertFalse(self.view()["available"])
        self.assertEqual(self.view()["content"]["rows"], ())
        table = window.data_panels_dock.scroll.widget().displays["note"]
        self.assertEqual(table.rowCount(), 0)
        self.service.data_panels.set_source_access(
            self.owner.task_id, "account-panel", profile_key=self.key, enabled=False
        )
        self.http.gate.set()
        self.finished()
        self.assertFalse(self.view()["available"])
        self.assertEqual(table.rowCount(), 0)
