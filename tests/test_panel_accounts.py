"""Manual linked account reads, enduring panel authority and in-flight revocation."""

import json
import sqlite3
import threading
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import test_data_panels as panels
from app_connector_support import MemorySecrets
from recipe_support import TOKEN, SyntheticRecipeHttp, recipe

from data_panel_format import parse_panel
from services.app_accounts import AccountStore
from services.task_policy import TaskPermissionError
from services.task_service import TaskService


class AccountPanelTests(unittest.TestCase):
    setUpClass = classmethod(panels.DataPanelTests.setUpClass.__func__)
    tearDownClass = classmethod(panels.DataPanelTests.tearDownClass.__func__)
    command = panels.DataPanelTests.command
    wait_for = panels.DataPanelTests.wait_for
    window = panels.DataPanelTests.window

    def setUp(self):
        panels.DataPanelTests.setUp(self)
        self.connections = self.registry.app_connections
        self.accounts = AccountStore(
            self.config.config_dir / "app_accounts.json", MemorySecrets()
        )
        self.connections.store = self.accounts
        self.http = SyntheticRecipeHttp()
        self.connections.recipe_http_factory = lambda _: self.http
        self.recipe = recipe()
        self.connections.install_recipe(self.recipe)
        self.connections.connect_recipe_account(
            self.recipe.provider, TOKEN, ["read_note"]
        )
        self.wait_for(lambda: not self.connections.busy)
        self.account = self.accounts.list()[0]
        self.service.set_connector_access(self.account.connector, "read", True)
        self.owner = self.command("task.create", objective="Reusable account dashboard")
        self.source = {
            "version": 5,
            "name": "Selected note",
            "widgets": [
                {
                    "id": "note",
                    "kind": "text",
                    "title": "Note",
                    "account": {
                        "connector": self.account.connector,
                        "tool": "read_note",
                        "destination": "note-1",
                        "inputs": {},
                    },
                }
            ],
        }
        self.key = self.service.data_panels.profile_key("work", create=True)

    def tearDown(self):
        if self.http.gate:
            self.http.gate.set()
        if not self.service._closed:
            self.wait_for(lambda: not self.service.data_panels.account_reads.inflight)
        panels.DataPanelTests.tearDown(self)

    def propose(self, *, allow=True):
        receipt = self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="account-panel",
            source=json.dumps(self.source),
        )
        self.service.data_panels.resolve(
            self.owner.task_id,
            receipt.request_id,
            approve=True,
            profile="work",
            profile_key=self.key,
            library_access=allow,
        )
        return receipt

    def view(self):
        return self.service.data_panels.view(self.owner.task_id, "account-panel")[0]

    def refresh(self):
        self.service.data_panels.account_reads.refresh(
            self.owner.task_id, "account-panel", profile_key=self.key
        )

    def finished(self):
        self.wait_for(lambda: not self.service.data_panels.account_reads.inflight)

    def test_old_versions_unknown_callbacks_and_nontext_account_sources_are_refused(
        self,
    ):
        for change in (
            lambda d: d.update(version=4),
            lambda d: d["widgets"][0].update(kind="table"),
            lambda d: d["widgets"][0].update(artifact_id="other"),
            lambda d: d["widgets"][0]["account"].update(effect="send"),
            lambda d: d["widgets"][0]["account"].update(destination="*"),
            lambda d: d["widgets"][0]["account"].update(
                inputs={"query": {"$bind": "credential"}}
            ),
        ):
            data = deepcopy(self.source)
            change(data)
            with self.assertRaises(ValueError):
                parse_panel(json.dumps(data))

    def test_proposal_approval_view_and_task_end_never_fetch_without_manual_refresh(
        self,
    ):
        before = len(self.http.calls)
        self.propose()
        self.assertFalse(self.view()["available"])
        self.assertEqual(self.view()["content"], "")
        self.command("task.finish", task_id=self.owner.task_id, status="completed")
        self.assertEqual(len(self.http.calls), before)
        self.refresh()
        self.finished()
        value = self.view()
        self.assertTrue(value["available"])
        self.assertTrue(value["fetched_at"])
        self.assertEqual(
            json.loads(value["content"]),
            {"title": "Fixture note", "body": "Shared task data"},
        )
        self.assertEqual(
            [url.rsplit("/", 1)[1] for _, url, _, _ in self.http.calls[before:]],
            ["me", "note-1"],
        )
        self.assertEqual(self.service.store.grants(self.owner.task_id), [])

    def test_separate_panel_and_account_permission_are_both_required(self):
        self.propose(allow=False)
        before = len(self.http.calls)
        with self.assertRaises(ValueError):
            self.refresh()
        self.service.data_panels.set_source_access(
            self.owner.task_id, "account-panel", profile_key=self.key, enabled=True
        )
        self.service.set_connector_access(self.account.connector, "read", False)
        with self.assertRaises(ValueError):
            self.refresh()
        self.assertEqual(len(self.http.calls), before)
        self.assertEqual(self.view()["content"], "")
        self.service.set_connector_access(self.account.connector, "read", True)
        self.assertEqual(len(self.http.calls), before)
        self.refresh()
        self.finished()
        self.assertTrue(self.view()["available"])

    def test_manual_dock_refresh_and_immediate_source_revocation(self):
        self.propose()
        window = self.window()
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        display = dock.scroll.widget().displays["note"]
        self.assertEqual(display.toPlainText(), "")
        dock.refresh_button.click()
        self.finished()
        self.assertIn("Shared task data", display.toPlainText())
        self.service.set_connector_access(self.account.connector, "read", False)
        self.assertEqual(display.toPlainText(), "")
        self.assertNotIn("Shared task data", str(dock.scroll.widget()._widgets))
        self.assertEqual(self.service.data_panels.account_reads.results, {})
        self.service.set_connector_access(self.account.connector, "read", True)
        self.assertEqual(display.toPlainText(), "")
        dock.refresh_button.click()
        self.finished()
        self.assertIn("Shared task data", display.toPlainText())
        window.grab().save("/tmp/steed-account-panel-window.png")

    def test_revocation_during_identity_discards_late_result_and_does_not_refetch_on_restore(
        self,
    ):
        self.propose()
        self.http.gate = threading.Event()
        self.refresh()
        self.wait_for(self.http.entered.is_set)
        self.service.set_connector_access(self.account.connector, "read", False)
        self.assertEqual(self.view()["content"], "")
        self.service.set_connector_access(self.account.connector, "read", True)
        before = len(self.http.calls)
        self.http.gate.set()
        self.finished()
        self.assertEqual(len(self.http.calls), before)
        self.assertFalse(self.view()["available"])
        self.assertEqual(self.service.data_panels.account_reads.results, {})

    def test_timeout_keeps_physical_worker_bound_and_never_retries_automatically(self):
        self.propose()
        self.service.connector_timeout_ms = 10
        self.http.gate = threading.Event()
        reads = self.service.data_panels.account_reads
        self.refresh()
        self.wait_for(lambda: len(reads.inflight) == 1 and not reads.jobs)
        self.refresh()
        self.wait_for(lambda: len(reads.inflight) == 2 and not reads.jobs)
        before = len(self.http.calls)
        self.refresh()
        self.assertEqual(len(reads.inflight), 2)
        self.assertEqual(len(reads.waiting), 1)
        self.service.set_connector_access(self.account.connector, "read", False)
        self.http.gate.set()
        self.finished()
        self.assertEqual(len(self.http.calls), before)
        self.assertEqual(reads.waiting, {})
        self.assertEqual(self.view()["content"], "")

    def test_pinned_tool_metadata_changes_clear_content_and_require_new_review(self):
        self.propose()
        self.refresh()
        self.finished()
        definition = self.service.connectors.get(self.account.connector, "read_note")
        self.service.connectors.unregister(self.account.connector)
        self.service.connectors.register(
            replace(definition, description="Changed reviewed metadata")
        )
        self.service.changed.emit()
        self.assertEqual(self.view()["content"], "")
        with self.assertRaises(ValueError):
            self.refresh()

    def test_disconnect_immediately_clears_the_open_account_widget(self):
        self.propose()
        window = self.window()
        window._toggle_data_panels(True)
        display = window.data_panels_dock.scroll.widget().displays["note"]
        self.refresh()
        self.finished()
        self.assertIn("Shared task data", display.toPlainText())
        self.connections.disconnect(self.account.account_id)
        self.assertEqual(display.toPlainText(), "")
        self.assertEqual(self.service.data_panels.account_reads.results, {})
        self.wait_for(lambda: not self.connections.busy)

    def test_tool_change_before_approval_cannot_bind_a_stale_review(self):
        self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="account-panel",
            source=json.dumps(self.source),
        )
        definition = self.service.connectors.get(self.account.connector, "read_note")
        self.service.connectors.unregister(self.account.connector)
        self.service.connectors.register(
            replace(definition, description="New metadata")
        )
        with self.assertRaises(ValueError):
            self.service.data_panels.resolve(
                self.owner.task_id,
                "account-panel",
                approve=True,
                profile="work",
                profile_key=self.key,
                library_access=True,
            )
        self.assertEqual(
            self.service.data_panels.get(self.owner.task_id, "account-panel").state,
            "awaiting_approval",
        )

    def test_write_effects_and_unsupported_inputs_never_dispatch(self):
        definition = self.service.connectors.get(self.account.connector, "read_note")
        self.service.connectors.register(
            replace(definition, tool="write_note", effect="write")
        )
        before = len(self.http.calls)
        for tool, inputs in (("write_note", {}), ("read_note", {"unknown": "value"})):
            self.source["widgets"][0]["account"].update(tool=tool, inputs=inputs)
            with self.assertRaises(ValueError):
                self.propose()
        self.assertEqual(len(self.http.calls), before)

    def test_failed_identity_and_http_error_hide_data_without_replay(self):
        self.propose()
        self.refresh()
        self.finished()
        self.http.subject = "different-user"
        self.refresh()
        self.finished()
        self.assertFalse(self.view()["available"])
        self.assertEqual(self.view()["content"], "")
        self.http.subject = self.account.subject
        self.http.fail = True
        before = len(self.http.calls)
        self.refresh()
        self.finished()
        self.assertEqual(len(self.http.calls) - before, 1)
        self.assertFalse(self.view()["available"])

    def test_result_redaction_and_value_free_journal(self):
        self.propose()
        self.http.body["body"] = "Reflected " + TOKEN
        self.refresh()
        self.finished()
        self.assertNotIn(TOKEN, self.view()["content"])
        journal = json.dumps(
            [e.payload for e in self.service.store.events(self.owner.task_id)]
        )
        self.assertNotIn("Reflected", journal)
        self.assertNotIn(TOKEN, journal)
        self.assertNotIn("note-1", journal)

    def test_restart_keeps_permission_but_requires_an_explicit_new_fetch(self):
        self.propose()
        self.refresh()
        self.finished()
        self.service.close()
        before = len(self.http.calls)
        restarted = TaskService(
            self.config.config_dir / "tasks.sqlite",
            profiles=self.registry,
            connectors=self.connections.connectors,
            connections=self.connections,
        )
        try:
            self.assertTrue(
                restarted.data_panels.source_access(self.owner.task_id, "account-panel")
            )
            self.assertEqual(
                restarted.data_panels.view(self.owner.task_id, "account-panel")[0][
                    "content"
                ],
                "",
            )
            self.assertEqual(len(self.http.calls), before)
        finally:
            restarted.close()

    def test_shutdown_erases_open_widget_even_after_all_tasks_ended(self):
        self.propose()
        self.command("task.finish", task_id=self.owner.task_id, status="completed")
        window = self.window()
        window._toggle_data_panels(True)
        display = window.data_panels_dock.scroll.widget().displays["note"]
        self.refresh()
        self.finished()
        self.assertIn("Shared task data", display.toPlainText())
        self.service.close()
        self.assertEqual(display.toPlainText(), "")
        self.assertIsNone(window.data_panels_dock.scroll.widget())

    def test_agents_have_no_refresh_or_approval_endpoint(self):
        self.propose()
        for command in ("panel.refresh", "panel.source_access"):
            future = self.service.client(self.owner.task_id).submit(
                command, request_id="account-panel"
            )
            self.wait_for(future.done)
            with self.assertRaises(TaskPermissionError):
                future.result()

    def test_journal_failure_hides_finished_read_and_freezes_panel_authority(self):
        self.propose()
        with patch.object(
            self.service.store,
            "record_event",
            side_effect=sqlite3.OperationalError("synthetic"),
        ):
            self.refresh()
            self.finished()
        self.assertTrue(self.service.failure)
        self.assertEqual(self.service.data_panels.account_reads.results, {})
