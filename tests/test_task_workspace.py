"""Binary fidelity, exact authority, user sharing and inert custom packages."""

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from qt_test_support import application

from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from services.task_workspace import CHUNK_BYTES, MAX_FILE_BYTES
from ui.task_dialog import TaskDialog


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = TaskService(self.root / "tasks.sqlite")
        self.client = self.task()

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        self.temp.cleanup()

    def task(self, actions=None, **scope):
        task = self.service._create("Synthetic file task")
        grant = self.service._request_grant(
            task.task_id,
            {"actions": actions or ["workspace"], "max_operations": 1000, **scope},
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        return self.service.client(task.task_id)

    def call(self, command, client=None, **args):
        future = (client or self.client).submit(command, **args)
        deadline = time.monotonic() + 3
        while not future.done() and time.monotonic() < deadline:
            self.app.processEvents()
        return future.result(timeout=0)

    def begin(self, name, data, client=None):
        return self.call(
            "workspace.begin",
            client=client,
            name=name,
            media_type="application/octet-stream",
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def file(self, name="report.bin", data=b"PRIVATE\x00\xff", client=None):
        item = self.begin(name, data, client)
        for offset in range(0, len(data), CHUNK_BYTES):
            self.call(
                "workspace.write",
                client=client,
                file_id=item.file_id,
                offset=offset,
                data_base64=base64.b64encode(
                    data[offset : offset + CHUNK_BYTES]
                ).decode(),
            )
        return self.call("workspace.seal", client=client, file_id=item.file_id)

    def package(self, **changes):
        self.file("main.py", b"raise RuntimeError('must never execute')")
        manifest = {
            "version": 1,
            "name": "custom-helper",
            "entrypoint": "main.py",
            "files": ["main.py"],
            "capabilities": [
                {
                    "connector": "google",
                    "tool": "send",
                    "effect": "send",
                    "destination": "person@example.test",
                }
            ],
            **changes,
        }
        return self.file("extension.json", json.dumps(manifest).encode())

    def test_binary_round_trip_multiple_chunks_no_content_in_events(self):
        data = bytes(range(256)) * 300
        item = self.file("report.xlsx", data)
        self.assertEqual(
            self.service.workspace.bytes(self.client.task_id, item.file_id), data
        )
        chunks = []
        for offset in range(0, len(data), CHUNK_BYTES):
            result = self.call("workspace.read", file_id=item.file_id, offset=offset)
            chunks.append(base64.b64decode(result["data_base64"]))
        self.assertEqual(b"".join(chunks), data)
        self.assertTrue(result["eof"])
        self.assertEqual(self.call("workspace.list"), [item])
        events = repr(self.service.store.events(self.client.task_id))
        self.assertNotIn("report.xlsx", events)
        self.assertNotIn(base64.b64encode(data).decode(), events)

    def test_chunk_retry_is_identical_and_sealed_content_is_immutable(self):
        item = self.begin("retry.bin", b"abc\x00\xff")
        args = {"file_id": item.file_id, "offset": 0, "data_base64": "YWJjAP8="}
        first = self.call("workspace.write", **args)
        self.assertEqual(self.call("workspace.write", **args), first)
        self.assertEqual(
            len(
                [
                    e
                    for e in self.service.store.events(self.client.task_id)
                    if e.kind == "workspace.chunk_written"
                ]
            ),
            1,
        )
        self.call("workspace.seal", file_id=item.file_id)
        with self.assertRaises(ValueError):
            self.call("workspace.write", **args)
        self.assertEqual(self.begin("retry.bin", b"abc\x00\xff").file_id, item.file_id)
        with self.assertRaises(ValueError):
            self.begin("retry.bin", b"changed")

    def test_bad_chunks_ranges_hashes_and_empty_file(self):
        item = self.begin("bounded.bin", b"abcd")
        for offset, data in [
            (1, "YQ=="),
            (0, "@@"),
            (0, "YQ==\n"),
            (0, "YR=="),
            (0, ""),
            (True, "YQ=="),
            (0, "YWJjZGU="),
        ]:
            with self.subTest(offset=offset, data=data), self.assertRaises(ValueError):
                self.call(
                    "workspace.write",
                    file_id=item.file_id,
                    offset=offset,
                    data_base64=data,
                )
        with self.assertRaises(ValueError):
            self.call("workspace.seal", file_id=item.file_id)
        self.call(
            "workspace.write", file_id=item.file_id, offset=0, data_base64="YWJjZQ=="
        )
        with self.assertRaises(ValueError):
            self.call("workspace.seal", file_id=item.file_id)
        empty = self.file("empty", b"")
        self.assertTrue(self.call("workspace.read", file_id=empty.file_id)["eof"])
        for args in (
            {"offset": -1},
            {"offset": 1},
            {"limit": CHUNK_BYTES + 1},
            {"limit": 0},
        ):
            with self.assertRaises(ValueError):
                self.call("workspace.read", file_id=empty.file_id, **args)

    def test_names_mime_and_declarations_are_bounded(self):
        for name in [
            "../bad",
            "/tmp/bad",
            "a/../b",
            "a//b",
            "./a",
            "a\\b",
            "bad\nname",
            ".",
            "",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.begin(name, b"a")
        for extra in [
            {"byte_count": True},
            {"byte_count": MAX_FILE_BYTES + 1},
            {"sha256": "a"},
            {"media_type": "text/html\r\nInjected: yes"},
        ]:
            with self.assertRaises(ValueError):
                self.call(
                    "workspace.begin",
                    **{
                        "name": "bad",
                        "byte_count": 1,
                        "sha256": "a" * 64,
                        "media_type": "text/plain",
                        **extra,
                    },
                )

    def test_reserved_size_and_file_count_limits(self):
        for i in range(4):
            self.call(
                "workspace.begin",
                name=str(i),
                media_type="text/plain",
                byte_count=MAX_FILE_BYTES,
                sha256="a" * 64,
            )
        with self.assertRaises(ValueError):
            self.begin("overflow", b"a")
        other = self.task()
        for i in range(50):
            self.begin(str(i), b"", other)
        with self.assertRaises(ValueError):
            self.begin("overflow", b"", other)

    def test_task_ownership_and_workspace_permission_are_required(self):
        item = self.file()
        other = self.task()
        for command in ["workspace.read", "workspace.seal"]:
            with self.assertRaises(KeyError):
                self.call(command, client=other, file_id=item.file_id)
        denied = self.task(["artifact"])
        with self.assertRaises(TaskPermissionError):
            self.call("workspace.list", client=denied)
        grant = self.service.store.grants(self.client.task_id)[0]
        self.service.revoke_grant(self.client.task_id, grant.grant_id)
        with self.assertRaises(TaskPermissionError):
            self.call("workspace.read", file_id=item.file_id)
        self.assertFalse(self.service.failure)

    def test_agents_cannot_import_delete_execute_or_approve_packages(self):
        for command in [
            "workspace.import",
            "workspace.delete",
            "workspace.execute",
            "workspace.approve_extension",
        ]:
            with self.assertRaises(TaskPermissionError):
                self.call(command)

    def test_staged_package_is_inert_and_caps_grant_nothing(self):
        manifest = self.package()
        result = self.call("workspace.stage_extension", manifest_id=manifest.file_id)
        self.assertFalse(result["executable"])
        self.assertEqual(result["requested_capabilities"][0]["effect"], "send")
        self.assertEqual(
            self.call("workspace.stage_extension", manifest_id=manifest.file_id), result
        )
        self.assertEqual(self.service.connectors.describe(), [])
        self.assertEqual(
            self.service.store.grants(self.client.task_id)[0].scope[
                "connector_permissions"
            ],
            [],
        )
        with self.assertRaises(ValueError):
            self.call(
                "app.call_tool",
                connector="google",
                tool="send",
                destination="person@example.test",
                request_id="attempt",
            )
        self.assertEqual(
            self.service.workspace.extensions(self.client.task_id)[0]["status"],
            "staged",
        )
        self.service.delete_workspace_file(self.client.task_id, manifest.file_id)
        self.assertEqual(
            self.service.workspace.extensions(self.client.task_id)[0]["status"],
            "unavailable",
        )

    def test_package_rejects_unowned_missing_unsealed_and_duplicate_keys(self):
        manifest = self.package(files=["missing.py"], entrypoint="missing.py")
        with self.assertRaises(ValueError):
            self.call("workspace.stage_extension", manifest_id=manifest.file_id)
        foreign = self.task()
        with self.assertRaises(KeyError):
            self.call(
                "workspace.stage_extension",
                client=foreign,
                manifest_id=manifest.file_id,
            )
        duplicate = self.file("duplicate.json", b'{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            self.call("workspace.stage_extension", manifest_id=duplicate.file_id)
        utf16 = self.file("utf16.json", b"\xff\xfe{\x00}\x00")
        with self.assertRaises(ValueError):
            self.call("workspace.stage_extension", manifest_id=utf16.file_id)
        original = json.loads(
            self.service.workspace.bytes(self.client.task_id, manifest.file_id)
        )
        original["files"] = ["main.py"]
        original["entrypoint"] = "main.py"
        original["capabilities"] *= 2
        repeated = self.file("repeated-capability.json", json.dumps(original).encode())
        with self.assertRaises(ValueError):
            self.call("workspace.stage_extension", manifest_id=repeated.file_id)
        unsealed = self.begin("unfinished.json", b"{}")
        with self.assertRaises(ValueError):
            self.call("workspace.stage_extension", manifest_id=unsealed.file_id)

    def test_tampered_sealed_bytes_cannot_be_read_exported_or_staged(self):
        manifest = self.package()
        source = next(
            f
            for f in self.service.workspace.list(self.client.task_id)
            if f.name == "main.py"
        )
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE workspace_files SET data=? WHERE file_id=?",
                (b"tampered", source.file_id),
            )
        with self.assertRaises(ValueError):
            self.call("workspace.read", file_id=source.file_id)
        with self.assertRaises(ValueError):
            self.service.export_workspace_file(
                self.client.task_id, source.file_id, str(self.root / "no-export")
            )
        with self.assertRaises(ValueError):
            self.call("workspace.stage_extension", manifest_id=manifest.file_id)

    def test_export_requires_exact_download_permission_and_never_overwrites(self):
        dest = str(self.root / "result.pdf")
        client = self.task(["workspace", "download"], download_destinations=[dest])
        item = self.file("report.pdf", b"%PDF\x00\xff", client)
        with self.assertRaises(TaskPermissionError):
            self.call(
                "workspace.export",
                client=client,
                file_id=item.file_id,
                destination=str(self.root / "other"),
            )
        result = self.call(
            "workspace.export", client=client, file_id=item.file_id, destination=dest
        )
        self.assertEqual(Path(dest).read_bytes(), b"%PDF\x00\xff")
        self.assertEqual(Path(dest).stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["sha256"], item.sha256)
        with self.assertRaises(TaskPermissionError):
            self.call(
                "workspace.export",
                client=client,
                file_id=item.file_id,
                destination=dest,
            )
        self.assertFalse(self.service.failure)
        self.assertEqual(list(self.root.glob(".steed-download-*")), [])

    def test_export_checks_size_and_revocation_at_publication(self):
        dest = str(self.root / "output")
        client = self.task(
            ["workspace", "download"],
            download_destinations=[dest],
            max_download_bytes=1,
        )
        item = self.file("large", b"ab", client)
        with self.assertRaises(ValueError):
            self.call(
                "workspace.export",
                client=client,
                file_id=item.file_id,
                destination=dest,
            )
        item = self.file("small", b"a", client)
        original = self.service._workspace_event

        def revoke(task_id, kind, payload):
            original(task_id, kind, payload)
            if kind == "workspace.export_requested":
                self.service._grants[task_id][1].end("revoked")

        with patch.object(self.service, "_workspace_event", side_effect=revoke):
            with self.assertRaises(TaskPermissionError):
                self.call(
                    "workspace.export",
                    client=client,
                    file_id=item.file_id,
                    destination=dest,
                )
        self.assertFalse(Path(dest).exists())
        self.assertFalse(self.service.failure)

    def test_symlink_destination_and_parent_are_rejected(self):
        item = self.file()
        (self.root / "link").symlink_to(self.root / "absent")
        with self.assertRaises(TaskPermissionError):
            self.service.export_workspace_file(
                self.client.task_id, item.file_id, str(self.root / "link")
            )
        (self.root / "parent").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.service.export_workspace_file(
                self.client.task_id, item.file_id, str(self.root / "parent" / "out")
            )

    def test_user_sharing_is_owned_snapshot_with_provenance(self):
        path = self.root / "private.csv"
        path.write_bytes(b"SYNTHETIC_USER_DATA\x00\xff")
        item = self.service.import_workspace_file(self.client.task_id, str(path))
        self.assertEqual(item.source, "user_shared")
        path.write_bytes(b"changed on disk")
        read = self.call("workspace.read", file_id=item.file_id)
        self.assertEqual(
            base64.b64decode(read["data_base64"]), b"SYNTHETIC_USER_DATA\x00\xff"
        )
        events = repr(self.service.store.events(self.client.task_id))
        self.assertNotIn(str(path), events)
        self.assertNotIn("SYNTHETIC_USER_DATA", events)

    def test_user_sharing_rejects_symlinks_fifo_and_oversize(self):
        path = self.root / "big"
        with path.open("wb") as stream:
            stream.truncate(MAX_FILE_BYTES + 1)
        (self.root / "link").symlink_to(path)
        os.mkfifo(self.root / "pipe")
        for name, error in [
            ("big", ValueError),
            ("link", OSError),
            ("pipe", ValueError),
        ]:
            with self.assertRaises(error):
                self.service.import_workspace_file(
                    self.client.task_id, str(self.root / name)
                )
        self.assertFalse(self.service.failure)

    def test_restart_preserves_bytes_user_can_export_delete_but_old_grants_end(self):
        item = self.file()
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.root / "tasks.sqlite")
        dest = str(self.root / "retained")
        self.service.export_workspace_file(self.client.task_id, item.file_id, dest)
        self.assertEqual(Path(dest).read_bytes(), b"PRIVATE\x00\xff")
        with self.assertRaises(TaskPermissionError):
            self.service.client(self.client.task_id)
        with self.assertRaises(TaskPermissionError):
            self.service._workspace_read(self.client.task_id, item.file_id)
        self.service.delete_workspace_file(self.client.task_id, item.file_id)
        row = self.service.store._db.execute(
            "SELECT data,status FROM workspace_files WHERE file_id=?", (item.file_id,)
        ).fetchone()
        self.assertEqual(tuple(row), (b"", "deleted"))
        self.assertTrue(Path(dest).exists())

    def test_schema_nine_upgrade_preserves_drafts_and_journal(self):
        draft = self.service.store.store_artifact(
            self.client.task_id, "Old draft", "original"
        )
        with self.service.store._db:
            self.service.store._db.execute("DROP TABLE workspace_extensions")
            self.service.store._db.execute("DROP TABLE workspace_files")
            self.service.store._db.execute("PRAGMA user_version=9")
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.root / "tasks.sqlite")
        self.assertEqual(
            self.service.store._db.execute("PRAGMA user_version").fetchone()[0], 18
        )
        self.assertEqual(
            self.service.store.artifact(self.client.task_id, draft.artifact_id).content,
            "original",
        )

    def test_storage_failure_rolls_back_and_ends_authority(self):
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("fixture disk failure"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.begin("failed", b"x")
        self.assertTrue(self.service.failure)
        self.assertEqual(self.service.workspace.list(self.client.task_id), [])

    def test_export_journal_failure_prevents_publication(self):
        item = self.file()
        dest = str(self.root / "failed")
        with patch.object(
            self.service.store,
            "record_event",
            side_effect=sqlite3.OperationalError("fixture disk failure"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.service.export_workspace_file(
                    self.client.task_id, item.file_id, dest
                )
        self.assertTrue(self.service.failure)
        self.assertFalse(Path(dest).exists())

    def test_workspace_ui_retention_and_delete_defaults_to_no(self):
        item = self.file()
        dialog = TaskDialog(self.service)
        try:
            dialog.table.selectRow(0)
            dialog.refresh_activity()
            self.assertTrue(dialog.share_workspace_button.isEnabled())
            self.assertTrue(dialog.export_workspace_button.isEnabled())
            self.assertIn(item.sha256, dialog.workspace_preview.toPlainText())
            self.assertNotIn("PRIVATE", dialog.workspace_preview.toPlainText())
            from PyQt6.QtWidgets import QMessageBox

            with patch(
                "ui.task_dialog.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as prompt:
                dialog.delete_workspace_file()
                self.assertEqual(
                    prompt.call_args.args[-1], QMessageBox.StandardButton.No
                )
            self.service._finish_task(self.client.task_id)
            dialog.refresh_activity()
            self.assertFalse(dialog.share_workspace_button.isEnabled())
            self.assertTrue(dialog.export_workspace_button.isEnabled())
            with patch.object(
                self.service.workspace,
                "get",
                side_effect=sqlite3.OperationalError("fixture storage failure"),
            ):
                dialog.show_workspace_file()
            self.assertTrue(self.service.failure)
            self.assertFalse(dialog.export_workspace_button.isEnabled())
            self.assertFalse(dialog.delete_workspace_button.isEnabled())
        finally:
            dialog.close()
            sip.delete(dialog)

    def test_export_completion_journal_failure_leaves_file_and_ends_authority(self):
        item = self.file()
        destination = str(self.root / "published-without-receipt")
        original = self.service.store.record_event

        def fail_completion(task_id, kind, payload):
            if kind == "workspace.exported":
                raise sqlite3.OperationalError("fixture completion failure")
            return original(task_id, kind, payload)

        with patch.object(
            self.service.store, "record_event", side_effect=fail_completion
        ):
            with self.assertRaises(sqlite3.Error):
                self.service.export_workspace_file(
                    self.client.task_id, item.file_id, destination
                )
        self.assertTrue(self.service.failure)
        self.assertEqual(Path(destination).read_bytes(), b"PRIVATE\x00\xff")
        with self.assertRaises(RuntimeError):
            self.service.export_workspace_file(
                self.client.task_id, item.file_id, destination
            )
