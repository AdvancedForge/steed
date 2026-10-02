"""Selected file copies are atomic data disclosure, never restored authority."""

import base64
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from qt_test_support import application

from services.agent_tools import TOOLS_BY_NAME
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.task_dialog import TaskDialog
from ui.task_recovery import TaskRecoveryDialog


class WorkspaceRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite"
        self.service = TaskService(self.path)
        self.source = self.service._create("Original generated attachments")
        self.binary = self.file("report.zip", bytes(range(256)) * 150)
        self.hidden = self.file("unselected-private.txt", b"PRIVATE_UNSELECTED")
        self.draft = self.service.store.store_artifact(
            self.source.task_id, "Selected draft", "PRIVATE_DRAFT"
        )
        self.service._finish_task(self.source.task_id)
        self.target = self.service._create("Continue selected files")
        self.service._request_recovery(self.target.task_id)

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def file(self, name, data, source=None, sealed=True):
        owner = source or self.source
        item = self.service.workspace.begin(
            owner.task_id,
            name,
            "application/octet-stream",
            len(data),
            hashlib.sha256(data).hexdigest(),
        )
        for offset in range(0, len(data), 32768):
            self.service.workspace.write(
                owner.task_id,
                item.file_id,
                offset,
                base64.b64encode(data[offset : offset + 32768]).decode(),
            )
        return (
            self.service.workspace.seal(owner.task_id, item.file_id) if sealed else item
        )

    def share(self, ids=None, **extra):
        return self.service.approve_recovery(
            self.target.task_id,
            self.source.task_id,
            [],
            evidence=False,
            file_ids=ids if ids is not None else [self.binary.file_id],
            **extra,
        )

    def approve(self, actions=None, **extra):
        grant = self.service._request_grant(
            self.target.task_id,
            {
                "actions": actions or ["workspace"],
                "max_operations": 1000,
                **extra,
            },
        )
        self.assertEqual(grant.status, "requested")
        self.service.approve_grant(self.target.task_id, grant.grant_id)

    def call(self, command, **args):
        future = self.service.client(self.target.task_id).submit(command, **args)
        deadline = time.monotonic() + 3
        while not future.done() and time.monotonic() < deadline:
            self.app.processEvents()
        return future.result(timeout=0)

    def test_selected_binary_round_trip_fresh_ids_metadata_only_and_authority(self):
        result = self.share()
        context = result["context"]
        self.assertEqual(len(context["imported_files"]), 1)
        imported = context["imported_files"][0]
        self.assertNotEqual(imported["file_id"], self.binary.file_id)
        self.assertEqual(imported["source_file_id"], self.binary.file_id)
        self.assertEqual(imported["sha256"], self.binary.sha256)
        self.assertNotIn("PRIVATE_UNSELECTED", repr(context))
        self.assertNotIn("unselected-private", repr(context))
        self.assertNotIn("data_base64", repr(context))
        with self.assertRaises(TaskPermissionError):
            self.call("workspace.read", file_id=imported["file_id"])
        self.approve()
        listed = self.call("workspace.list")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].source, "user_shared")
        observed = []
        for offset in range(0, self.binary.byte_count, 32768):
            part = self.call(
                "workspace.read", file_id=imported["file_id"], offset=offset
            )
            observed.append(base64.b64decode(part["data_base64"]))
        self.assertEqual(b"".join(observed), bytes(range(256)) * 150)
        with self.assertRaises(KeyError):
            self.call("workspace.read", file_id=self.binary.file_id)
        journal = repr(self.service.store.events(self.target.task_id))
        self.assertNotIn(self.binary.name, journal)
        self.assertNotIn("PRIVATE_UNSELECTED", journal)

    def test_artifact_permission_does_not_read_imported_file_bytes(self):
        imported = self.share()["context"]["imported_files"][0]
        self.approve(["artifact"])
        with self.assertRaises(TaskPermissionError):
            self.call("workspace.read", file_id=imported["file_id"])

    def test_copies_survive_original_deletion_and_restart(self):
        imported = self.share()["context"]["imported_files"][0]
        self.service.delete_workspace_file(self.source.task_id, self.binary.file_id)
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.path)
        self.assertEqual(
            self.service.workspace.bytes(self.target.task_id, imported["file_id"]),
            bytes(range(256)) * 150,
        )
        self.assertFalse(self.service._grants)
        self.assertFalse(self.service.workers.jobs)
        self.assertEqual(
            self.service.store.recovery(self.target.task_id)["context"][
                "imported_files"
            ][0],
            imported,
        )

    def test_denial_and_empty_selection_share_nothing(self):
        with self.assertRaises(ValueError):
            self.share([])
        self.service.deny_recovery(self.target.task_id)
        self.assertEqual(self.service.workspace.list(self.target.task_id), [])
        self.assertEqual(
            self.service._recovery_info(self.target.task_id)["context"], {}
        )

    def test_duplicate_foreign_unsealed_deleted_and_missing_files_are_rejected(self):
        foreign_task = self.service._create("Foreign files")
        foreign = self.file("foreign", b"FOREIGN", source=foreign_task)
        building = self.file("building", b"INCOMPLETE", sealed=False)
        deleted = self.file("deleted", b"DELETED")
        self.service.delete_workspace_file(self.source.task_id, deleted.file_id)
        for ids in (
            [self.binary.file_id] * 2,
            [foreign.file_id],
            [building.file_id],
            [deleted.file_id],
            ["missing"],
            "not a selection",
            [True],
        ):
            with self.subTest(ids=ids), self.assertRaises((ValueError, KeyError)):
                self.share(ids)
            self.assertEqual(self.service.workspace.list(self.target.task_id), [])
            self.assertEqual(
                self.service.store.recovery(self.target.task_id)["status"], "requested"
            )
        self.assertFalse(self.service.failure)

    def test_count_and_reserved_byte_limits(self):
        items = [self.file(f"file-{n}", b"x") for n in range(11)]
        with self.assertRaises(ValueError):
            self.share([item.file_id for item in items])
        with self.service.store._db:
            for item in items[:5]:
                self.service.store._db.execute(
                    "UPDATE workspace_files SET expected_bytes=? WHERE file_id=?",
                    (8 * 1024 * 1024, item.file_id),
                )
        with self.assertRaisesRegex(ValueError, "32 MiB"):
            self.share([item.file_id for item in items[:5]])
        self.assertFalse(self.service.workspace.list(self.target.task_id))

    def test_corrupt_metadata_or_bytes_fail_before_disclosure(self):
        for column, value in [
            ("data", b"CHANGED"),
            ("name", "../traversal"),
            ("expected_bytes", 9 * 1024 * 1024),
            ("sha256", "invalid"),
        ]:
            with self.subTest(column=column):
                original = self.service.store._db.execute(
                    f"SELECT {column} FROM workspace_files WHERE file_id=?",
                    (self.binary.file_id,),
                ).fetchone()[0]
                with self.service.store._db:
                    self.service.store._db.execute(
                        f"UPDATE workspace_files SET {column}=? WHERE file_id=?",
                        (value, self.binary.file_id),
                    )
                with self.assertRaises(ValueError):
                    self.share()
                self.assertFalse(self.service.workspace.list(self.target.task_id))
                with self.service.store._db:
                    self.service.store._db.execute(
                        f"UPDATE workspace_files SET {column}=? WHERE file_id=?",
                        (original, self.binary.file_id),
                    )

    def test_journal_failure_rolls_back_files_drafts_and_selection(self):
        draft = self.draft
        append = self.service.store._append

        def fail_final_event(task_id, kind, payload):
            if kind == "recovery.approved":
                raise sqlite3.OperationalError("synthetic failure")
            return append(task_id, kind, payload)

        with patch.object(self.service.store, "_append", fail_final_event):
            with self.assertRaises(sqlite3.Error):
                self.service.approve_recovery(
                    self.target.task_id,
                    self.source.task_id,
                    [draft.artifact_id],
                    evidence=False,
                    file_ids=[self.binary.file_id],
                )
        self.assertTrue(self.service.failure)
        self.assertFalse(self.service.workspace.list(self.target.task_id))
        self.assertFalse(self.service.store.artifacts(self.target.task_id))
        self.assertEqual(
            self.service.store.recovery(self.target.task_id)["status"], "requested"
        )

    def test_source_code_copies_neither_staged_packages_nor_execution_approval(self):
        main = self.file("main.py", b"pass")
        manifest = self.file(
            "extension.json",
            json.dumps(
                {
                    "version": 1,
                    "name": "helper",
                    "entrypoint": "main.py",
                    "files": ["main.py"],
                    "capabilities": [],
                }
            ).encode(),
        )
        original = self.service.workspace.stage_extension(
            self.source.task_id, manifest.file_id
        )
        result = self.share([main.file_id, manifest.file_id])
        self.assertEqual(self.service.workspace.extensions(self.target.task_id), [])
        self.assertEqual(self.service.workers.store.list(self.target.task_id), [])
        self.approve(["workspace", "worker"])
        fresh_manifest = next(
            f
            for f in result["context"]["imported_files"]
            if f["name"] == "extension.json"
        )
        staged = self.call(
            "workspace.stage_extension", manifest_id=fresh_manifest["file_id"]
        )
        self.assertNotEqual(staged["package_sha256"], original["package_sha256"])
        run = self.call(
            "worker.run", extension_id=staged["extension_id"], request_id="run-copied"
        )
        self.assertEqual(run.state, "awaiting_approval")
        self.assertFalse(self.service.workers.jobs)

    def test_saved_workspace_rule_never_approves_recovered_files(self):
        owner = self.service._create("Remember workspace")
        grant = self.service._request_grant(owner.task_id, {"actions": ["workspace"]})
        self.service.approve_grant(owner.task_id, grant.grant_id, remember="global")
        self.share()
        self.assertEqual(
            self.service._request_grant(
                self.target.task_id, {"actions": ["workspace"]}
            ).status,
            "requested",
        )

    def test_recovery_cannot_be_added_to_existing_workspace(self):
        existing = self.service._create("Existing shared file")
        self.file("existing.bin", b"EXISTING", source=existing)
        with self.assertRaises(ValueError):
            self.service._request_recovery(existing.task_id)
        self.file("added.bin", b"ADDED", source=self.target)
        with self.assertRaises(ValueError):
            self.share()

    def test_ui_files_are_unchecked_and_selection_is_explicit(self):
        dialog = TaskRecoveryDialog(self.service, self.target)
        dialog.source.setCurrentIndex(dialog.source.findData(self.source.task_id))
        self.assertEqual(dialog.files.count(), 2)
        self.assertTrue(
            all(
                dialog.files.item(i).checkState() == Qt.CheckState.Unchecked
                for i in range(dialog.files.count())
            )
        )
        for i in range(dialog.files.count()):
            item = dialog.files.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == self.binary.file_id:
                item.setCheckState(Qt.CheckState.Checked)
                dialog.files.setCurrentRow(i)
        self.assertIn(self.binary.sha256, dialog.file_preview.toPlainText())
        dialog.evidence.setChecked(False)
        dialog.materials.setCurrentIndex(1)
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-workspace-recovery.png")
        dialog.share.click()
        self.assertEqual(len(self.service.workspace.list(self.target.task_id)), 1)
        self.assertEqual(
            self.service.store.recovery(self.target.task_id)["status"], "approved"
        )

    def test_agent_cannot_choose_or_approve_recovery_files(self):
        with self.assertRaises(ValueError):
            TOOLS_BY_NAME["steed_task_recover"].validate(
                {"file_ids": [self.binary.file_id]}
            )
        self.assertNotIn("recovery.approve", self.service.broker._handlers)

    def test_workspace_preview_without_recovery_keeps_task_service_available(self):
        panel = TaskDialog(self.service)
        row = next(
            i
            for i, (task, _) in enumerate(panel._rows)
            if task.task_id == self.source.task_id
        )
        panel.table.selectRow(row)
        panel.refresh_activity()
        self.assertFalse(self.service.failure)
        self.assertIn("File:", panel.workspace_preview.toPlainText())
        panel.close()
        sip.delete(panel)

    def test_oversized_context_rolls_back_already_copied_files(self):
        with patch(
            "services.task_service.recovery_snapshot",
            return_value={
                "evidence_included": False,
                "large": "x" * (128 * 1024),
            },
        ):
            with self.assertRaises(ValueError):
                self.share()
        self.assertFalse(self.service.workspace.list(self.target.task_id))
        self.assertFalse(self.service.failure)
        self.assertEqual(
            self.service.store.recovery(self.target.task_id)["status"], "requested"
        )

    def test_later_corrupt_file_rolls_back_an_earlier_good_copy(self):
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE workspace_files SET data=? WHERE file_id=?",
                (b"CHANGED", self.hidden.file_id),
            )
        with self.assertRaises(ValueError):
            self.share([self.binary.file_id, self.hidden.file_id])
        self.assertFalse(self.service.workspace.list(self.target.task_id))
        self.assertFalse(
            any(
                e.kind == "workspace.file_imported"
                for e in self.service.store.events(self.target.task_id)
            )
        )

    def test_followup_file_provenance_preserves_each_selected_hop(self):
        first = self.share()["context"]["imported_files"][0]
        self.service._finish_task(self.target.task_id)
        third = self.service._create("Continue once more")
        self.service._request_recovery(third.task_id)
        result = self.service.approve_recovery(
            third.task_id,
            self.target.task_id,
            [],
            evidence=False,
            file_ids=[first["file_id"]],
        )
        final = result["context"]["imported_files"][0]
        self.assertEqual(final["source_file_id"], first["file_id"])
        self.assertNotEqual(final["file_id"], first["file_id"])
        self.service.delete_workspace_file(self.target.task_id, first["file_id"])
        self.assertEqual(
            self.service.workspace.bytes(third.task_id, final["file_id"]),
            bytes(range(256)) * 150,
        )
