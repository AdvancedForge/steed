"""Persistent folder access, binary edits, recovery and task isolation."""

import base64
import hashlib
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_task_profiles as profile_fixtures
from PyQt6 import sip
from qt_test_support import application

from browser_window import BrowserWindow
from services.file_library import CHUNK_BYTES, FileLibrary, read_import
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.file_library import FileLibraryDialog


class FileLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "library.sqlite"
        self.library = FileLibrary(self.path)
        self.folder = self.library.create_folder("Research")

    def tearDown(self):
        self.library.close()
        self.temp.cleanup()

    def write(self, path, data, *, task="agent-a", request="write", expected=0):
        item = self.library.begin(
            task,
            self.folder,
            path,
            request,
            len(data),
            hashlib.sha256(data).hexdigest(),
            expected,
        )
        for offset in range(0, len(data), CHUNK_BYTES):
            self.library.chunk(
                task,
                item["id"],
                offset,
                base64.b64encode(data[offset : offset + CHUNK_BYTES]).decode(),
            )
        return self.library.commit(task, item["id"])

    def test_default_off_and_disabled_folders_are_not_discoverable(self):
        self.assertEqual(self.library.folders(enabled_only=True), [])
        with self.assertRaises(ValueError):
            self.library.folder(self.folder, agent=True)
        self.library.enable(self.folder, True)
        self.assertEqual(self.library.folders(enabled_only=True)[0]["id"], self.folder)
        other = self.library.create_folder("Other")
        self.assertNotIn(
            other, {r["id"] for r in self.library.folders(enabled_only=True)}
        )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_binary_chunks_repeat_without_replay_and_are_revision_consistent(self):
        data = bytes(range(256)) * 300
        receipt = self.write("project/report.bin", data)
        self.assertEqual(self.library.commit("agent-a", receipt["id"]), receipt)
        chunks = [
            self.library.read(
                self.folder, receipt["path"], receipt["revision"], offset=i
            )
            for i in range(0, len(data), CHUNK_BYTES)
        ]
        self.assertEqual(
            b"".join(base64.b64decode(c["data_base64"]) for c in chunks), data
        )
        self.assertTrue(chunks[-1]["eof"])
        self.assertNotIn(
            base64.b64encode(data).decode(),
            repr(self.library.db.execute("SELECT * FROM activity").fetchall()),
        )
        with self.assertRaises(ValueError):
            self.library.read(self.folder, receipt["path"], True)

    def test_edit_retains_previous_version_and_stale_writer_cannot_overwrite(self):
        first = self.write("notes.txt", b"Original")
        self.write(
            "notes.txt", b"Updated", request="update", expected=first["revision"]
        )
        with self.assertRaises(ValueError):
            self.write(
                "notes.txt",
                b"Stale",
                request="stale",
                task="agent-b",
                expected=first["revision"],
            )
        self.assertEqual(self.library.row(self.folder, "notes.txt")["data"], b"Updated")
        restored = self.library.restore_version(
            self.folder, "notes.txt", first["revision"], 2
        )
        self.assertEqual(restored["revision"], 3)
        self.assertEqual(
            self.library.row(self.folder, "notes.txt")["data"], b"Original"
        )
        self.assertEqual(self.library.commit("agent-a", first["id"])["revision"], 1)
        self.assertEqual(self.library.row(self.folder, "notes.txt")["revision"], 3)

    def test_chunk_ownership_offsets_hash_and_retry(self):
        item = self.library.begin(
            "a", self.folder, "new.bin", "stable", 3, hashlib.sha256(b"abc").hexdigest()
        )
        self.library.chunk("a", item["id"], 0, "YWI=")
        self.library.chunk("a", item["id"], 0, "YWI=")
        for task, offset, data in (
            ("b", 2, "Yw=="),
            ("a", 1, "Yw=="),
            ("a", 0, "YWM="),
            ("a", 2, "ZA=="),
        ):
            if data == "ZA==":
                self.library.chunk(task, item["id"], offset, data)
                with self.assertRaises(ValueError):
                    self.library.commit("a", item["id"])
            else:
                with self.assertRaises(ValueError):
                    self.library.chunk(task, item["id"], offset, data)
        self.assertIsNone(self.library.row(self.folder, "new.bin"))
        with self.assertRaises(ValueError):
            self.library.begin(
                "a", self.folder, "other.bin", "stable", 3, item["sha256"]
            )

    def test_disabled_folder_cancels_partial_write_even_after_reenable(self):
        item = self.library.begin(
            "a", self.folder, "new", "request", 3, hashlib.sha256(b"abc").hexdigest()
        )
        self.library.chunk("a", item["id"], 0, "YWJj")
        self.library.enable(self.folder, False)
        self.library.enable(self.folder, True)
        self.assertEqual(self.library.write_info("a", item["id"])["state"], "cancelled")
        with self.assertRaises(ValueError):
            self.library.commit("a", item["id"])
        self.assertIsNone(self.library.row(self.folder, "new"))

    def test_directory_revision_protects_changed_descendants_and_trash_restores_tree(
        self,
    ):
        self.write("project/first.bin", b"one")
        old = self.library.row(self.folder, "project")["revision"]
        self.write("project/nested/second.bin", b"two", request="second")
        with self.assertRaises(ValueError):
            self.library.remove(self.folder, "project", old)
        now = self.library.row(self.folder, "project")["revision"]
        removed = self.library.remove(self.folder, "project", now)
        self.assertEqual(removed["removed"], 4)
        self.assertEqual(self.library.list(self.folder)["entries"], [])
        self.assertEqual(len(self.library.list(self.folder, trash=True)["entries"]), 4)
        self.library.restore(self.folder, "project")
        self.assertEqual(
            self.library.row(self.folder, "project/nested/second.bin")["data"], b"two"
        )
        self.assertEqual(self.library.list(self.folder, trash=True)["entries"], [])

    def test_recreated_name_retains_old_history_without_restoring_over_new_file(self):
        first = self.write("file", b"old")
        self.library.remove(self.folder, "file", first["revision"])
        self.write("file", b"new", request="new")
        with self.assertRaises(ValueError):
            self.library.restore(self.folder, "file")
        self.assertEqual(self.library.row(self.folder, "file")["data"], b"new")
        self.assertTrue(self.library.history(self.folder, "file"))

    def test_paging_literal_wildcard_names_and_direct_children(self):
        self.library.import_files(
            self.folder, [("%/a.txt", b"a"), ("_/b.txt", b"b"), ("top.txt", b"c")]
        )
        first = self.library.list(self.folder, limit=1)
        second = self.library.list(self.folder, after=first["cursor"], limit=1)
        self.assertTrue(first["has_more"])
        self.assertNotEqual(first["entries"], second["entries"])
        self.assertEqual(
            [i["path"] for i in self.library.list(self.folder, "%")["entries"]],
            ["%/a.txt"],
        )

    def test_traversal_absolute_and_ambiguous_names_never_touch_host_files(self):
        for name in (
            "../escape",
            "/tmp/escape",
            "a//b",
            "a/./b",
            "a/../b",
            "a\\b",
            "a\nfile",
            "",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.write(name, b"no")
        self.assertFalse((self.path.parent / "escape").exists())
        self.assertEqual(self.library.list(self.folder)["entries"], [])

    def test_import_is_atomic_when_directory_precedes_conflict(self):
        self.library.import_files(self.folder, [("existing", b"old")])
        with self.assertRaises(ValueError):
            self.library.import_files(
                self.folder,
                [("new", None), ("new/valid", b"okay"), ("existing", b"conflict")],
            )
        self.assertIsNone(self.library.row(self.folder, "new"))
        self.assertEqual(self.library.row(self.folder, "existing")["data"], b"old")

    def test_quota_counts_versions_and_recovery_copies(self):
        self.write("file", b"1234")
        with patch("services.file_library.LIBRARY_BYTES", 7):
            with self.assertRaises(ValueError):
                self.write("file", b"5678", request="edit", expected=1)
        self.library.remove(self.folder, "file", 1)
        with patch("services.file_library.LIBRARY_BYTES", 7):
            with self.assertRaises(ValueError):
                self.library.restore(self.folder, "file")
        self.assertTrue(self.library.row(self.folder, "file")["deleted"])
        self.library.clear_recovery(self.folder)
        self.assertIsNone(self.library.row(self.folder, "file"))

    def test_restart_retains_files_switches_and_cancels_uncommitted_write(self):
        self.write("kept", b"persistent")
        self.library.enable(self.folder, True)
        pending = self.library.begin(
            "a", self.folder, "partial", "pending", 1, hashlib.sha256(b"x").hexdigest()
        )
        self.library.close()
        self.library = FileLibrary(self.path)
        self.assertTrue(self.library.folder(self.folder)["enabled"])
        self.assertEqual(self.library.row(self.folder, "kept")["data"], b"persistent")
        self.assertEqual(
            self.library.write_info("a", pending["id"])["state"], "cancelled"
        )

    def test_picker_copies_subfolders_empty_directories_and_rejects_links_special_files(
        self,
    ):
        root = self.path.parent / "input"
        (root / "empty").mkdir(parents=True)
        (root / "nested").mkdir()
        (root / "nested/file.bin").write_bytes(b"binary\x00\xff")
        imported = read_import([root])
        self.library.import_files(self.folder, imported)
        self.assertEqual(
            self.library.row(self.folder, "input/empty")["kind"], "directory"
        )
        self.assertEqual(
            self.library.row(self.folder, "input/nested/file.bin")["data"],
            b"binary\x00\xff",
        )
        (root / "link").symlink_to(self.path)
        with self.assertRaises((ValueError, OSError)):
            read_import([root])
        (root / "link").unlink()
        os.mkfifo(root / "pipe")
        with self.assertRaises(ValueError):
            read_import([root])

    def test_library_storage_symlink_and_wrong_thread_are_rejected(self):
        link = self.path.parent / "link.sqlite"
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            FileLibrary(link)
        errors = []

        def wrong_thread():
            try:
                self.library.folders()
            except RuntimeError:
                errors.append(True)

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join()
        self.assertEqual(errors, [True])

    def test_corrupt_recovery_copy_rolls_back_entire_restore(self):
        self.write("project/file", b"keep")
        self.library.remove(
            self.folder, "project", self.library.row(self.folder, "project")["revision"]
        )
        with self.library.db:
            self.library.db.execute(
                "UPDATE versions SET data=? WHERE folder=? AND path=?",
                (b"tampered", self.folder, "project/file"),
            )
        with self.assertRaises(ValueError):
            self.library.restore(self.folder, "project")
        self.assertTrue(self.library.row(self.folder, "project")["deleted"])
        self.assertTrue(self.library.row(self.folder, "project/file")["deleted"])

    def test_missing_recovery_copy_leaves_entire_subtree_in_trash(self):
        self.write("project/file", b"keep")
        self.library.remove(
            self.folder, "project", self.library.row(self.folder, "project")["revision"]
        )
        with self.library.db:
            self.library.db.execute(
                "DELETE FROM versions WHERE folder=? AND path=?",
                (self.folder, "project/file"),
            )
        with self.assertRaisesRegex(ValueError, "Recovery copy is unavailable"):
            self.library.restore(self.folder, "project")
        self.assertTrue(self.library.row(self.folder, "project")["deleted"])
        self.assertTrue(self.library.row(self.folder, "project/file")["deleted"])


class FileLibraryServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = TaskService(Path(self.temp.name) / "tasks.sqlite")
        self.client = self.task()
        self.folder = self.service.library.create_folder("Shared")
        self.service.library.import_files(
            self.folder, [("inputs/data.bin", b"input\x00\xff")]
        )

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        self.temp.cleanup()

    def task(self):
        return self.service.client(self.service._create("Reuse library files").task_id)

    def call(self, name, *, client=None, **args):
        future = (client or self.client).submit(name, **args)
        deadline = time.monotonic() + 5
        while not future.done() and time.monotonic() < deadline:
            self.app.processEvents()
        return future.result(timeout=0)

    def test_folder_enable_is_sufficient_across_tasks_without_task_grants(self):
        self.assertEqual(self.call("library.folders"), [])
        with self.assertRaises(ValueError):
            self.call("library.list", folder_id=self.folder)
        self.service.library.enable(self.folder, True)
        for client in (self.client, self.task()):
            result = self.call(
                "library.read",
                client=client,
                folder_id=self.folder,
                path="inputs/data.bin",
                revision=1,
            )
            self.assertEqual(base64.b64decode(result["data_base64"]), b"input\x00\xff")
            self.assertEqual(self.service.store.grants(client.task_id), [])
        self.service.library.enable(self.folder, False)
        with self.assertRaises(ValueError):
            self.call(
                "library.read",
                folder_id=self.folder,
                path="inputs/data.bin",
                revision=1,
            )

    def test_agent_cannot_enable_folders_clear_recovery_or_supply_task_identity(self):
        self.service.library.enable(self.folder, True)
        for command in (
            "library.enable",
            "library.import_host",
            "library.clear_recovery",
            "library.execute",
        ):
            with self.assertRaises(TaskPermissionError):
                self.call(command)
        with self.assertRaises(TaskPermissionError):
            self.call("library.folders", task_id=self.task().task_id)

    def test_write_chunks_are_task_owned_and_access_rechecked(self):
        self.service.library.enable(self.folder, True)
        item = self.call(
            "library.write_begin",
            folder_id=self.folder,
            path="output.txt",
            request_id="new",
            byte_count=3,
            sha256=hashlib.sha256(b"abc").hexdigest(),
        )
        with self.assertRaises(ValueError):
            self.call(
                "library.write_chunk",
                client=self.task(),
                write_id=item["id"],
                offset=0,
                data_base64="YWJj",
            )
        self.call(
            "library.write_chunk", write_id=item["id"], offset=0, data_base64="YWJj"
        )
        self.service.library.enable(self.folder, False)
        with self.assertRaises(ValueError):
            self.call("library.write_commit", write_id=item["id"])

    def test_cancelled_task_cannot_use_persistent_access_but_next_task_can(self):
        self.service.library.enable(self.folder, True)
        item = self.call(
            "library.write_begin",
            folder_id=self.folder,
            path="unfinished",
            request_id="pending",
            byte_count=1,
            sha256=hashlib.sha256(b"x").hexdigest(),
        )
        with self.assertRaises(ValueError):
            self.call("task.finish", status="invalid")
        self.assertEqual(
            self.service.library.write_info(self.client.task_id, item["id"])["state"],
            "building",
        )
        self.service.cancel_task(self.client.task_id)
        self.assertEqual(
            self.service.library.write_info(self.client.task_id, item["id"])["state"],
            "cancelled",
        )
        with self.assertRaises(TaskPermissionError):
            self.call("library.list", folder_id=self.folder)
        self.assertTrue(
            self.call("library.list", client=self.task(), folder_id=self.folder)[
                "entries"
            ]
        )

    def test_workspace_bridge_preserves_user_shared_source_and_reuses_outputs(self):
        self.service.library.enable(self.folder, True)
        with self.assertRaises(TaskPermissionError):
            self.call(
                "library.import_workspace",
                folder_id=self.folder,
                path="inputs/data.bin",
                revision=1,
            )
        grant = self.service._request_grant(
            self.client.task_id, {"actions": ["workspace"], "max_operations": 100}
        )
        self.service.approve_grant(self.client.task_id, grant.grant_id)
        item = self.call(
            "library.import_workspace",
            folder_id=self.folder,
            path="inputs/data.bin",
            revision=1,
        )
        self.assertEqual(item.source, "user_shared")
        self.assertEqual(
            self.service.workspace.bytes(self.client.task_id, item.file_id),
            b"input\x00\xff",
        )
        receipt = self.call(
            "library.save_workspace",
            folder_id=self.folder,
            path="outputs/copy.bin",
            file_id=item.file_id,
        )
        self.assertEqual(receipt["state"], "committed")
        self.assertEqual(
            self.call(
                "library.save_workspace",
                folder_id=self.folder,
                path="outputs/copy.bin",
                file_id=item.file_id,
            ),
            receipt,
        )
        other = self.task()
        data = self.call(
            "library.read",
            client=other,
            folder_id=self.folder,
            path="outputs/copy.bin",
            revision=receipt["revision"],
        )
        self.assertEqual(base64.b64decode(data["data_base64"]), b"input\x00\xff")

    def test_sql_failure_stops_service_instead_of_publishing(self):
        self.service.library.enable(self.folder, True)
        with patch.object(
            self.service.library,
            "mkdir",
            side_effect=sqlite3.OperationalError("synthetic failure"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.call("library.mkdir", folder_id=self.folder, path="never")
        self.assertTrue(self.service.failure)
        self.assertIsNone(self.service._library.row(self.folder, "never"))

    def test_dialog_toggle_and_async_import_are_shared_and_disabling_is_immediate(self):
        dialog = FileLibraryDialog(self.service)
        self.assertFalse(dialog.enabled.isChecked())
        dialog.enabled.setChecked(True)
        self.assertTrue(self.call("library.folders"))
        source = Path(self.temp.name) / "picked.txt"
        source.write_text("Reuse this input")
        dialog.import_selection([str(source)])
        deadline = time.monotonic() + 5
        while dialog._job and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNone(dialog._job)
        self.assertEqual(
            self.service.library.row(self.folder, "picked.txt")["data"],
            b"Reuse this input",
        )
        dialog.enabled.setChecked(False)
        self.assertEqual(self.call("library.folders"), [])
        dialog.reject()
        sip.delete(dialog)

    def test_closing_dialog_during_import_never_publishes_late_files(self):
        from concurrent.futures import Future

        dialog = FileLibraryDialog(self.service)
        future = Future()
        future.set_running_or_notify_cancel()
        dialog._job = (self.folder, future)
        dialog.reject()
        future.set_result([("late.txt", b"must not be imported")])
        dialog.poll_import()
        self.assertIsNone(self.service.library.row(self.folder, "late.txt"))
        sip.delete(dialog)

    def test_export_is_atomic_private_and_does_not_overwrite(self):
        dialog = FileLibraryDialog(self.service)
        node = dialog.files.topLevelItem(0).child(0)
        dialog.files.setCurrentItem(node)
        destination = Path(self.temp.name) / "export.bin"
        with patch(
            "ui.file_library.QFileDialog.getSaveFileName",
            return_value=(str(destination), ""),
        ):
            dialog.export()
            self.assertEqual(destination.read_bytes(), b"input\x00\xff")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            destination.write_bytes(b"Never replace this")
            dialog.export()
        self.assertEqual(destination.read_bytes(), b"Never replace this")
        dialog.reject()
        sip.delete(dialog)


class FileLibraryBrowserTests(unittest.TestCase):
    setUpClass = classmethod(profile_fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(
        profile_fixtures.TaskProfileTests.tearDownClass.__func__
    )
    setUp = profile_fixtures.TaskProfileTests.setUp
    tearDown = profile_fixtures.TaskProfileTests.tearDown

    def window(self):
        window = BrowserWindow()
        self.windows.append(window)
        window.show()
        window._open_file_library()
        return window

    def test_private_profile_closes_library_ui_and_cannot_reopen_it(self):
        window = self.window()
        folder = self.service.library.create_folder("Explicit shared folder")
        self.service.library.enable(folder, True)
        self.service.changed.emit()
        self.assertIsNotNone(window.file_library_dialog)
        window._switch_to_profile("search")
        self.assertIsNone(window.file_library_dialog)
        self.assertFalse(window.file_library_action.isEnabled())
        window._open_file_library()
        self.assertIsNone(window.file_library_dialog)
        # Incognito browsing doesn't revoke a separate, explicit global folder switch.
        self.assertTrue(self.service.library.folder(folder)["enabled"])

    def test_two_windows_share_library_changes_and_one_close_does_not_close_storage(
        self,
    ):
        first, second = self.window(), self.window()
        folder = self.service.library.create_folder("Reusable")
        self.service.changed.emit()
        self.assertEqual(first.file_library_dialog.folders.currentData(), folder)
        self.assertEqual(second.file_library_dialog.folders.currentData(), folder)
        first.file_library_dialog.enabled.setChecked(True)
        self.assertTrue(second.file_library_dialog.enabled.isChecked())
        first.close()
        self.assertTrue(second.file_library_dialog.enabled.isChecked())
        self.service.library.import_files(folder, [("kept.txt", b"still available")])
        self.service.changed.emit()
        self.assertEqual(second.file_library_dialog.files.topLevelItemCount(), 1)
