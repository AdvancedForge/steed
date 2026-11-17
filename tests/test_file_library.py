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
from qt_test_support import WAIT_SECONDS, application

from browser.browser_window import BrowserWindow
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

    def test_content_search_finds_text_inside_files_with_snippets(self):
        self.library.enable(self.folder, True)
        self.library.import_files(
            self.folder,
            [
                (
                    "notes/meeting.txt",
                    b"Agenda\n\nThe Quarterly budget is\n  due Friday.",
                ),
                ("budget.csv", b"item,amount"),
                ("image.bin", b"\x00\x01quarterly budget"),
                ("other.txt", b"nothing relevant"),
            ],
        )
        found = self.library.search("quarterly BUDGET", content=True)["entries"]
        self.assertEqual([e["path"] for e in found], ["notes/meeting.txt"])
        self.assertEqual(found[0]["match"], "content")
        self.assertIn("Quarterly budget is due Friday.", found[0]["snippet"])
        # Path matches are still reported, without a snippet.
        both = self.library.search("budget", content=True)["entries"]
        self.assertEqual(
            {(e["path"], e["match"]) for e in both},
            {("budget.csv", "path"), ("notes/meeting.txt", "content")},
        )
        self.assertNotIn("snippet", next(e for e in both if e["match"] == "path"))
        # Without content=true only names match.
        names = self.library.search("Friday")["entries"]
        self.assertEqual(names, [])
        with self.assertRaises(ValueError):
            self.library.search("", content=True)
        with self.assertRaises(ValueError):
            self.library.search("x", content=1)
        from services.agent_tools import TOOLS_BY_NAME

        tool = TOOLS_BY_NAME["steed_library_search"]
        tool.validate({"query": "x", "content": True, "order": "recent"})
        for bad in ({"content": 1}, {"content": "yes"}, {"order": "size"}):
            with self.assertRaises(ValueError):
                tool.validate(bad)

    def test_recent_order_lists_newest_changes_first(self):
        self.library.enable(self.folder, True)
        for name in ("a.txt", "b.txt", "c.txt"):
            self.library.import_files(self.folder, [(name, name.encode())])
            time.sleep(0.01)
        self.write("a.txt", b"edited", expected=1)
        found = self.library.search(order="recent", limit=2)
        self.assertEqual([e["path"] for e in found["entries"]], ["a.txt", "c.txt"])
        self.assertTrue(found["has_more"])
        self.assertGreater(found["entries"][0]["modified"], 1_700_000_000)
        with self.assertRaises(ValueError):
            self.library.search(order="newest")
        cursor = self.library.search(limit=1)["cursor"]
        with self.assertRaises(ValueError):
            self.library.search(order="recent", after=cursor)

    def test_version_one_library_gains_modification_times(self):
        self.library.import_files(self.folder, [("old.txt", b"before upgrade")])
        self.library.close()
        db = sqlite3.connect(self.path)
        db.executescript(
            "CREATE TABLE copy AS SELECT folder,path,kind,revision,data,sha256,"
            "deleted,trash_id FROM entries; DROP TABLE entries;"
            "ALTER TABLE copy RENAME TO entries; PRAGMA user_version=1;"
        )
        db.close()
        self.library = FileLibrary(self.path)
        self.library.enable(self.folder, True)
        (old,) = self.library.search("old")["entries"]
        self.assertEqual(old["modified"], 0)
        self.library.import_files(self.folder, [("new.txt", b"after upgrade")])
        newest = self.library.search(order="recent")["entries"][0]
        self.assertEqual(newest["path"], "new.txt")

    def test_available_path_numbers_taken_names(self):
        self.library.import_files(self.folder, [("report.pdf", b"one")])
        self.assertEqual(
            self.library.available_path(self.folder, "report.pdf"), "report (2).pdf"
        )
        self.library.import_files(self.folder, [("report (2).pdf", b"two")])
        self.assertEqual(
            self.library.available_path(self.folder, "report.pdf"), "report (3).pdf"
        )
        self.assertEqual(self.library.available_path(self.folder, "new.pdf"), "new.pdf")

    def test_search_returns_metadata_across_roots_with_literal_queries(self):
        other = self.library.create_folder("Other")
        hidden = self.library.create_folder("Hidden")
        self.library.import_files(
            self.folder,
            [
                ("project/report.txt", b"first"),
                ("naïve 100%_done.md", b"unicode"),
                ("project/report.txt.bak", b"stale"),
            ],
        )
        self.library.import_files(
            other, [("project/report.txt", b"second"), ("project/trash.txt", b"gone")]
        )
        self.library.import_files(hidden, [("secret.txt", b"hidden")])
        for folder in (self.folder, other):
            self.library.enable(folder, True)

        # The same file name in two roots stays distinguishable.
        found = self.library.search("report.txt")["entries"]
        self.assertEqual(
            sorted((e["folder_id"], e["folder"], e["path"]) for e in found),
            sorted(
                [
                    (self.folder, "Research", "project/report.txt"),
                    (other, "Other", "project/report.txt"),
                    (self.folder, "Research", "project/report.txt.bak"),
                ]
            ),
        )
        # Metadata is enough to read the right file with the existing interface.
        self.assertEqual({e["revision"] for e in found}, {1})
        self.assertEqual({e["kind"] for e in found}, {"file"})
        for entry in found:
            data = self.library.read(
                entry["folder_id"], entry["path"], entry["revision"]
            )
            expected = {other: b"second", self.folder: b"first"}.get(entry["folder_id"])
            if entry["path"].endswith(".bak"):
                expected = b"stale"
            self.assertEqual(base64.b64decode(data["data_base64"]), expected)

        # Query punctuation is literal, never a wildcard.
        self.assertEqual(
            [e["path"] for e in self.library.search("100%_done")["entries"]],
            ["naïve 100%_done.md"],
        )
        self.assertEqual(
            sorted(e["path"] for e in self.library.search("%")["entries"]),
            ["naïve 100%_done.md"],
        )
        self.assertEqual(self.library.search("100X_done")["entries"], [])

        # Disabled roots, Trash and recovery data never appear.
        self.assertEqual(self.library.search("secret")["entries"], [])
        self.assertEqual(
            self.library.search("trash.txt")["entries"][0]["folder_id"], other
        )
        self.library.remove(other, "project/trash.txt", 1)
        self.assertEqual(self.library.search("trash.txt")["entries"], [])
        # Browsing groups each root in folder-name order, then path order.
        browse = self.library.search("")["entries"]
        self.assertEqual(
            [e["folder"] for e in browse], sorted(e["folder"] for e in browse)
        )
        self.assertEqual(
            [e["path"] for e in browse if e["folder"] == "Research"],
            sorted(e["path"] for e in browse if e["folder"] == "Research"),
        )

    def test_search_paging_is_deterministic_bounded_and_validates_cursors(self):
        self.library.enable(self.folder, True)
        self.library.import_files(
            self.folder, [(f"page/file{index:02d}.txt", b"x") for index in range(12)]
        )
        seen, cursor, pages = [], "", 0
        while True:
            page = self.library.search("page/file", after=cursor, limit=5)
            self.assertLessEqual(len(page["entries"]), 5)
            self.assertEqual(page["truncated"], page["has_more"])
            seen += [(e["folder_id"], e["path"]) for e in page["entries"]]
            cursor, pages = page["cursor"], pages + 1
            if not page["has_more"] or pages > 10:
                break
        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12)
        # Paging visits exactly what one uncapped call returns, in the same order.
        self.assertEqual(
            seen,
            [
                (e["folder_id"], e["path"])
                for e in self.library.search("page/file", limit=200)["entries"]
            ],
        )
        # An empty result set is explicit rather than an error.
        empty = self.library.search("nothing-matches-this")
        self.assertEqual((empty["entries"], empty["has_more"]), ([], False))
        for bad in ("garbage", "[1]", "[]", '["a"]', "[1,2]", "[1,2,3,4]", '{"a":1}'):
            with self.assertRaises(ValueError):
                self.library.search("page", after=bad)
        with self.assertRaises(ValueError):
            self.library.search("page", limit=0)
        # A stale revision from search still cannot be read after an edit.
        entry = self.library.search("page/file00")["entries"][0]
        self.write("page/file00.txt", b"changed", expected=entry["revision"])
        with self.assertRaises(ValueError):
            self.library.read(entry["folder_id"], entry["path"], entry["revision"])

    def test_search_unicode_cursor_can_be_used_for_the_next_page(self):
        folder = self.library.create_folder("研" * 128)
        self.library.enable(folder, True)
        self.library.import_files(
            folder, [("文" * 100 + suffix, b"x") for suffix in ("a", "b")]
        )
        first = self.library.search("文", limit=1)
        self.assertLessEqual(len(first["cursor"]), 1024)
        second = self.library.search("文", limit=1, after=first["cursor"])
        self.assertFalse(second["has_more"])
        self.assertEqual(second["entries"][0]["path"], "文" * 100 + "b")


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
        deadline = time.monotonic() + WAIT_SECONDS
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

    def test_agent_search_discovers_shared_files_from_a_new_task(self):
        self.service.library.enable(self.folder, True)
        found = self.call("library.search", query="data.bin")
        self.assertEqual([e["path"] for e in found["entries"]], ["inputs/data.bin"])
        entry = found["entries"][0]
        self.assertEqual((entry["folder_id"], entry["folder"]), (self.folder, "Shared"))
        self.assertEqual(entry["revision"], 1)

        # A brand new task locates and reuses the file with no per-file/task grant.
        new_task = self.task()
        reused = self.call("library.search", client=new_task, query="data.bin")
        self.assertEqual(reused["entries"][0]["revision"], entry["revision"])
        data = self.call(
            "library.read",
            client=new_task,
            folder_id=entry["folder_id"],
            path=entry["path"],
            revision=entry["revision"],
        )
        self.assertEqual(base64.b64decode(data["data_base64"]), b"input\x00\xff")
        self.assertEqual(self.service.store.grants(new_task.task_id), [])

        # Scoping to a disabled folder is refused, and disabling hides it at once.
        other = self.service.library.create_folder("Off")
        with self.assertRaises(ValueError):
            self.call("library.search", query="data.bin", folder_id=other)
        self.service.library.enable(self.folder, False)
        self.assertEqual(self.call("library.search", query="data.bin")["entries"], [])

    def test_agent_search_folder_scope_does_not_return_another_enabled_root(self):
        other = self.service.library.create_folder("Other shared folder")
        for folder in (self.folder, other):
            self.service.library.enable(folder, True)
        self.service.library.import_files(other, [("inputs/data.bin", b"other")])
        result = self.call("library.search", folder_id=self.folder, query="data.bin")
        self.assertEqual([e["folder_id"] for e in result["entries"]], [self.folder])
        result = self.call("library.search", folder_id=other, query="data.bin")
        self.assertEqual([e["folder_id"] for e in result["entries"]], [other])

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
        deadline = time.monotonic() + WAIT_SECONDS
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

    def test_dialog_filter_is_literal_bounded_and_cleared_on_close(self):
        dialog = FileLibraryDialog(self.service)
        dialog.enabled.setChecked(True)
        for name, data in (
            ("notes/report.txt", b"first"),
            ("notes/100%_done.md", b"literal"),
            ("deep/summary.txt", b"second"),
        ):
            self.service.library.import_files(self.folder, [(name, data)])

        def labels():
            dialog.refresh()
            found = []
            stack = [
                dialog.files.topLevelItem(i)
                for i in range(dialog.files.topLevelItemCount())
            ]
            while stack:
                item = stack.pop()
                found.append(item.text(0))
                stack.extend(item.child(i) for i in range(item.childCount()))
            return sorted(found)

        self.assertEqual(
            labels(),
            [
                "100%_done.md",
                "data.bin",
                "deep",
                "inputs",
                "notes",
                "report.txt",
                "summary.txt",
            ],
        )
        dialog.filter.setText("report")
        self.assertEqual(labels(), ["notes/report.txt"])
        # Punctuation is literal, so % never stands in for any character.
        dialog.filter.setText("%")
        self.assertEqual(labels(), ["notes/100%_done.md"])
        dialog.filter.setText("100X_done")
        self.assertEqual(labels(), [])
        # Filter state does not survive closing the dialog.
        dialog.filter.setText("report")
        dialog.reject()
        self.assertEqual(dialog.filter.text(), "")
        sip.delete(dialog)

    def test_dialog_paging_reaches_files_beyond_the_old_ceiling_and_resets(self):
        count = 2103
        for start in range(0, count, 500):
            self.service.library.import_files(
                self.folder,
                [
                    (f"bulk/report-{i:04}.txt", b"x")
                    for i in range(start, min(start + 500, count))
                ],
            )
        dialog = FileLibraryDialog(self.service)
        dialog.filter.setText("report-")
        seen = []
        while True:
            self.assertLessEqual(dialog.files.topLevelItemCount(), 200)
            seen.extend(
                dialog.files.topLevelItem(i).text(0)
                for i in range(dialog.files.topLevelItemCount())
            )
            if not dialog.next_page.isEnabled():
                break
            dialog.next_page.click()
        self.assertEqual(seen, [f"bulk/report-{i:04}.txt" for i in range(count)])
        self.assertTrue(dialog.previous_page.isEnabled())
        dialog.previous_page.click()
        self.assertEqual(dialog.files.topLevelItem(0).text(0), "bulk/report-1800.txt")
        dialog.filter.setText("report-2102")
        self.assertFalse(dialog.previous_page.isEnabled())
        self.assertFalse(dialog.next_page.isEnabled())
        self.assertEqual(dialog.files.topLevelItem(0).text(0), "bulk/report-2102.txt")
        dialog.reject()
        self.assertEqual(dialog._page_history, [])
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
