"""Portable layout validation and real task/user-review authority boundaries."""

import json
import sqlite3
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_task_profiles as profiles
from PyQt6 import sip
from PyQt6.QtNetwork import QLocalSocket

from browser_window import BrowserWindow
from layout_format import LAYOUT_EXAMPLE, export_layout, parse_layout
from layout_preferences import LayoutPreferences, preferences_from_config
from services.agent_gateway import AgentGateway
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.layout_review import LayoutReviewDialog
from ui.task_dialog import TaskDialog


class PortableLayoutTests(unittest.TestCase):
    def source(self, edit=None):
        document = deepcopy(LAYOUT_EXAMPLE)
        if edit:
            edit(document)
        return json.dumps(document)

    def test_export_round_trip_preserves_appearance_but_not_private_configuration(self):
        original = LayoutPreferences()
        original.set_density("compact")
        original.save_named("Personal preset")
        source = export_layout(original)
        definition = parse_layout(source)
        applied = definition.preferences(LayoutPreferences())
        self.assertEqual(applied.to_dict()["layout"], original.to_dict()["layout"])
        self.assertNotIn("Personal preset", source)
        self.assertEqual(applied.named_layouts, [])

    def test_definition_preserves_existing_named_layouts(self):
        current = LayoutPreferences()
        current.save_named("Existing")
        applied = parse_layout(self.source()).preferences(current)
        self.assertEqual(applied.named_layouts, ["Existing"])
        self.assertEqual(current.active_layout, "Existing")

    def test_unknown_executable_fields_and_missing_controls_are_refused(self):
        mutations = [
            lambda d: d.update(callback="exec('anything')"),
            lambda d: d.update(version=True),
            lambda d: d.update(version=99),
            lambda d: d["layout"].update(script="alert(1)"),
            lambda d: d["layout"]["toolbar"]["hidden"].append("address"),
            lambda d: d["layout"]["toolbar"]["order"].remove("address"),
            lambda d: d["layout"]["menus"]["order"].remove("view"),
            lambda d: d["layout"]["toolbar"]["order"].append("back"),
            lambda d: d["layout"]["sidebar"].update(width=260),
            lambda d: d["layout"]["sidebar"].update(width=True),
            lambda d: d["layout"].update(density="script"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parse_layout(self.source(mutation))

    def test_malformed_duplicate_oversized_and_deep_source_is_refused(self):
        for source in (
            "",
            "[]",
            '{"version":1,"version":1}',
            " " * 16385,
            "[" * 2000 + "]" * 2000,
            "\ud800",
        ):
            with self.subTest(source=source[:30]), self.assertRaises(ValueError):
                parse_layout(source)


class LayoutProposalTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for

    def command(self, name, *, client=None, **arguments):
        submitter = client if client is not None else self.service.broker
        future = submitter.submit(name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def task(self):
        return self.command("task.create", objective="Synthetic UI design")

    def source(self, density="compact"):
        document = deepcopy(LAYOUT_EXAMPLE)
        document["layout"]["density"] = density
        return json.dumps(document)

    def propose(self, task, request_id="layout-one", source=None):
        return self.command(
            "layout.propose",
            task_id=task.task_id,
            request_id=request_id,
            source=source or self.source(),
        )

    def test_discovery_and_proposal_need_no_data_permission_or_immediate_effect(self):
        task = self.task()
        client = self.service.client(task.task_id)
        capabilities = self.command(
            "layout.capabilities", task_id=task.task_id, client=client
        )
        self.assertTrue(capabilities["available"])
        proposal = self.command(
            "layout.propose", client=client, request_id="one", source=self.source()
        )
        self.assertEqual(proposal.state, "awaiting_approval")
        self.assertEqual(preferences_from_config(self.config).density, "comfortable")
        self.assertEqual(self.service.store.grants(task.task_id), [])
        denied = client.submit("layout.resolve", request_id="one", approve=True)
        self.app.processEvents()
        with self.assertRaises(TaskPermissionError):
            denied.result()

    def test_authenticated_gateway_can_propose_and_poll_but_cannot_approve(self):
        gateway = AgentGateway(self.service, self.config.config_dir)
        socket = QLocalSocket()
        try:
            socket.connectToServer(gateway.socket_path)
            self.wait_for(
                lambda: socket.state() == QLocalSocket.LocalSocketState.ConnectedState
            )

            def exchange(packet):
                socket.write(json.dumps(packet).encode() + b"\n")
                self.wait_for(socket.canReadLine)
                return json.loads(bytes(socket.readLine()))

            self.assertEqual(exchange({"token": gateway.token}), {"connected": True})
            started = exchange(
                {
                    "id": 1,
                    "tool": "steed_task_start",
                    "arguments": {"objective": "Synthetic layout via MCP gateway"},
                }
            )
            task_id = started["result"]["task_id"]
            response = exchange(
                {
                    "id": 2,
                    "tool": "steed_layout_propose",
                    "arguments": {"source": self.source(), "request_id": "wire-one"},
                }
            )
            self.assertEqual(response["result"]["state"], "awaiting_approval")
            denied = exchange(
                {
                    "id": 3,
                    "tool": "steed_layout_resolve",
                    "arguments": {"request_id": "wire-one", "approve": True},
                }
            )
            self.assertIn("error", denied)
            self.service.layouts.resolve(
                task_id,
                "wire-one",
                approve=True,
                base=preferences_from_config(self.config),
            )
            status = exchange(
                {
                    "id": 4,
                    "tool": "steed_layout_status",
                    "arguments": {"request_id": "wire-one"},
                }
            )
            self.assertEqual(status["result"]["state"], "applied")
            self.assertEqual(self.service.store.grants(task_id), [])
        finally:
            socket.abort()
            gateway.close()
            sip.delete(gateway)

    def test_exact_retries_and_completed_receipts_never_apply_again(self):
        task = self.task()
        proposal = self.propose(task)
        self.assertEqual(self.propose(task), proposal)
        with self.assertRaises(ValueError):
            self.propose(task, source=self.source("comfortable"))
        self.service.layouts.resolve(
            task.task_id,
            proposal.request_id,
            approve=True,
            base=preferences_from_config(self.config),
        )
        self.assertEqual(self.propose(task).state, "applied")
        self.assertEqual(preferences_from_config(self.config).density, "compact")
        self.assertEqual(self.service.store.grants(task.task_id), [])
        with self.assertRaises(ValueError):
            self.service.layouts.resolve(
                task.task_id,
                proposal.request_id,
                approve=True,
                base=preferences_from_config(self.config),
            )

    def test_foreign_task_receipt_lookup_and_identity_override_are_denied(self):
        first, second = self.task(), self.task()
        proposal = self.propose(first)
        with self.assertRaises(KeyError):
            self.command(
                "layout.status", task_id=second.task_id, request_id=proposal.request_id
            )
        client = self.service.client(second.task_id)
        with self.assertRaises(TaskPermissionError):
            self.command(
                "layout.status",
                client=client,
                task_id=first.task_id,
                request_id=proposal.request_id,
            )

    def test_pending_limit_and_cancel_preserve_current_layout(self):
        task = self.task()
        for number in range(3):
            self.propose(task, request_id=str(number))
        with self.assertRaises(TaskPermissionError):
            self.propose(task, request_id="four")
        self.assertFalse(self.service.failure)
        self.service.cancel_task(task.task_id)
        self.assertTrue(
            all(p.state == "cancelled" for p in self.service.layouts.list(task.task_id))
        )
        self.assertEqual(preferences_from_config(self.config).density, "comfortable")

    def test_applied_receipt_failure_never_replays_layout_publication(self):
        task = self.task()
        proposal = self.propose(task)
        original = self.service.store._append

        def fail_final_receipt(task_id, kind, payload):
            if kind == "layout.applied":
                raise OSError("Synthetic receipt failure")
            return original(task_id, kind, payload)

        with patch.object(
            self.service.store, "_append", side_effect=fail_final_receipt
        ):
            with self.assertRaises(OSError):
                self.service.layouts.resolve(
                    task.task_id,
                    proposal.request_id,
                    approve=True,
                    base=preferences_from_config(self.config),
                )
        self.assertEqual(preferences_from_config(self.config).density, "compact")
        self.assertEqual(self.propose(task).state, "applying")
        revision = self.config.revision
        with self.assertRaises(ValueError):
            self.service.layouts.resolve(
                task.task_id,
                proposal.request_id,
                approve=True,
                base=preferences_from_config(self.config),
            )
        self.assertEqual(self.config.revision, revision)

    def test_stale_review_cannot_apply_before_user_sees_updated_preview(self):
        task = self.task()
        proposal = self.propose(task)
        base = preferences_from_config(self.config)
        external = base.copy()
        external.set_sidebar("left", 400)
        self.registry.commit_layout(external)
        with self.assertRaises(ValueError):
            self.service.layouts.resolve(
                task.task_id, proposal.request_id, approve=True, base=base
            )
        self.assertEqual(
            self.service.layouts.get(task.task_id, proposal.request_id).state,
            "awaiting_approval",
        )

    def test_import_review_refreshes_after_other_window_saves(self):
        dialog = LayoutReviewDialog(self.registry, parse_layout(self.source()))
        external = preferences_from_config(self.config)
        external.set_sidebar("left", 400)
        self.registry.commit_layout(external)
        dialog.apply_layout()
        self.assertIn("changed", dialog.status.text())
        self.assertEqual(preferences_from_config(self.config).density, "comfortable")
        dialog.apply_layout()
        self.assertEqual(preferences_from_config(self.config).density, "compact")
        sip.delete(dialog)

    def test_automatic_native_popup_applies_to_all_windows_without_data_access(self):
        first, second = BrowserWindow(), BrowserWindow()
        self.windows.extend((first, second))
        task = self.task()
        proposal = self.propose(task)
        self.app.processEvents()
        dialog = self.registry.task_approvals.dialog
        self.assertIsInstance(dialog, LayoutReviewDialog)
        self.assertIn("Control spacing: compact", dialog.changes.toPlainText())
        dialog.apply_button.click()
        self.app.processEvents()
        self.assertEqual(first.layout_preferences.density, "compact")
        self.assertEqual(second.layout_preferences.density, "compact")
        self.assertEqual(
            self.service.layouts.get(task.task_id, proposal.request_id).state, "applied"
        )
        self.assertEqual(self.service.store.grants(task.task_id), [])

    def test_failed_config_publication_restores_memory_and_marks_receipt_failed(self):
        task = self.task()
        proposal = self.propose(task)
        before = self.config.get("layout")
        revision = self.config.revision
        with patch.object(
            self.config, "save_config", side_effect=OSError("synthetic disk failure")
        ):
            with self.assertRaises(OSError):
                self.service.layouts.resolve(
                    task.task_id,
                    proposal.request_id,
                    approve=True,
                    base=preferences_from_config(self.config),
                )
        self.assertEqual(self.config.get("layout"), before)
        self.assertEqual(self.config.revision, revision)
        self.assertEqual(
            self.service.layouts.get(task.task_id, proposal.request_id).state, "failed"
        )
        self.assertEqual(self.propose(task).state, "failed")

    def test_journal_excludes_source_and_preview_text(self):
        task = self.task()
        proposal = self.propose(task)
        self.service.layouts.resolve(task.task_id, proposal.request_id)
        events = [
            e
            for e in self.service.store.events(task.task_id)
            if e.kind.startswith("layout.")
        ]
        self.assertEqual(
            [e.kind for e in events], ["layout.requested", "layout.denied"]
        )
        self.assertTrue(all(set(e.payload) == {"request_id", "sha256"} for e in events))

    def test_previous_layout_recovery_retains_presets_and_updates_all_windows(self):
        first, second = BrowserWindow(), BrowserWindow()
        self.windows.extend((first, second))
        preferences = preferences_from_config(self.config)
        preferences.set_density("compact")
        self.registry.commit_layout(preferences)
        preferences.save_named("Keep this")
        self.registry.commit_layout(preferences)
        first._recover_layout()
        self.assertEqual(second.layout_preferences.density, "comfortable")
        self.assertEqual(second.layout_preferences.named_layouts, ["Keep this"])

    def test_restart_interrupts_pending_and_inflight_without_replay(self):
        task = self.task()
        first = self.propose(task)
        second = self.propose(task, request_id="inflight")
        self.service.layouts.update(task.task_id, second.request_id, "applying")
        self.service.close()
        path = self.config.config_dir / "tasks.sqlite"
        # Restore a synthetic crash snapshot instead of the normal shutdown receipt.
        with sqlite3.connect(path) as database:
            database.execute(
                "UPDATE layout_proposals SET state='awaiting_approval' WHERE request_id=?",
                (first.request_id,),
            )
            database.execute("PRAGMA user_version=16")
        restarted = TaskService(path, profiles=self.registry)
        try:
            self.assertEqual(
                restarted.store._db.execute("PRAGMA user_version").fetchone()[0], 21
            )
            self.assertTrue(
                all(
                    p.state == "interrupted"
                    for p in restarted.layouts.list(task.task_id)
                )
            )
            self.assertEqual(
                preferences_from_config(self.config).density, "comfortable"
            )
        finally:
            restarted.close()
            sip.delete(restarted)

    def test_closed_popup_remains_reviewable_in_task_management(self):
        window = BrowserWindow()
        self.windows.append(window)
        task = self.task()
        proposal = self.propose(task)
        self.app.processEvents()
        self.registry.task_approvals.dialog.reject()
        self.app.processEvents()
        manager = TaskDialog(self.service, window)
        manager.table.selectRow(0)
        manager.layout_requests.selectRow(0)
        self.assertTrue(manager.review_layout_button.isEnabled())
        self.assertEqual(manager._layout_rows[0].request_id, proposal.request_id)
        manager.review_layout()
        reviews = manager.findChildren(LayoutReviewDialog)
        self.assertEqual(len(reviews), 1)
        reviews[0].decline()
        self.app.processEvents()
        self.assertEqual(
            self.service.layouts.get(task.task_id, proposal.request_id).state, "denied"
        )
        manager.close()
        sip.delete(manager)


if __name__ == "__main__":
    unittest.main()
