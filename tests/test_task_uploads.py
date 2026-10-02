"""Exact source/site authority, immutable copies and native multipart requests."""

import unittest
from pathlib import Path
from unittest.mock import patch

import test_task_downloads as fixtures
import test_task_profiles as shared_fixtures
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings

from services.page_inspector import PageInspectionError
from services.task_policy import TaskPermissionError, TaskScope

HTML = '<form method="post" action="/uploaded" enctype="multipart/form-data"><input type="file" name="file" aria-label="Upload source"><button>Send</button></form>'


class TaskUploadTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskDownloadTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskDownloadTests.tearDownClass.__func__)
    setUp = fixtures.TaskDownloadTests.setUp
    tearDown = fixtures.TaskDownloadTests.tearDown
    command = fixtures.TaskDownloadTests.command
    load = fixtures.TaskDownloadTests.load
    inspect = fixtures.TaskDownloadTests.inspect
    wait_for = fixtures.TaskDownloadTests.wait_for

    def opened(self, *, profile="private", size=100000, html=HTML, origins=None):
        type(self).posted = []
        type(self).post_responses = {}
        self.source = Path(self.directory.name) / "source.txt"
        self.source.write_bytes(b"SYNTHETIC_FILE_BYTES")
        task = self.command("task.create", objective="Upload synthetic file")
        grant = self.command(
            "grant.request",
            task_id=task.task_id,
            scope={
                "origins": origins or [self.origin],
                "allow_private_network": True,
                "actions": [
                    "navigate",
                    "inspect",
                    "read",
                    "upload",
                    "submit",
                    "attach",
                ],
                "upload_sources": [str(self.source)],
                "upload_origins": [self.origin],
                "max_upload_bytes": size,
                "profile": profile,
            },
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        session = self.command(
            "session.open", task_id=task.task_id, profile=profile
        ).session_id
        page = self.service.backend._sessions[session].page
        self.load(session, page, html)
        return task.task_id, grant.grant_id, session, page

    def upload(self, task, session, source=None):
        snapshot = self.inspect(task, session)
        field = next(t for t in snapshot.frames[0].targets if "upload" in t.operations)
        return self.command(
            "page.upload",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=field.target_id,
            source=source or str(self.source),
        )

    def submit(self, task, session):
        snapshot = self.inspect(task, session)
        button = next(t for t in snapshot.frames[0].targets if "submit" in t.operations)
        return self.command(
            "page.submit",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=button.target_id,
        )

    def test_native_binding_and_multipart_send_use_an_immutable_source_snapshot(self):
        task, _, session, _ = self.opened()
        result = self.upload(task, session)
        self.assertEqual(result.status, "bound")
        self.assertEqual(result.byte_count, len(b"SYNTHETIC_FILE_BYTES"))
        self.assertFalse(self.posted)
        copy = self.service.backend._sessions[session].uploads[result.upload_id]
        stage = copy.path
        self.source.write_bytes(b"CHANGED_AFTER_SELECTION")
        submitted = self.submit(task, session)
        self.assertTrue(submitted.request_authorized)
        self.assertIn(b"SYNTHETIC_FILE_BYTES", self.posted[-1][1])
        self.assertNotIn(b"CHANGED_AFTER_SELECTION", self.posted[-1][1])
        self.assertNotIn("SYNTHETIC_FILE_BYTES", repr(self.service.store.events(task)))
        self.command("task.finish", task_id=task)
        self.assertFalse(stage.exists())

    def test_remaining_staging_budget_is_enforced_before_copying_more_bytes(self):
        task, _, session, _ = self.opened(size=40)
        self.upload(task, session)
        self.source.write_bytes(b"X" * 22)
        with self.assertRaises(TaskPermissionError) as denied:
            self.upload(task, session)
        self.assertEqual(denied.exception.code, "upload_too_large")
        copies = self.service.backend._sessions[session].uploads.values()
        self.assertEqual(
            sum(copy.size for copy in copies), len(b"SYNTHETIC_FILE_BYTES")
        )

    def test_source_and_upload_destination_authority_remain_exact(self):
        other_origin = self.origin.replace("127.0.0.1", "localhost")
        html = HTML.replace("/uploaded", other_origin + "/forbidden-upload")
        task, _, session, _ = self.opened(
            html=html, origins=[self.origin, other_origin]
        )
        with self.assertRaises(TaskPermissionError):
            self.upload(task, session, str(self.source.parent / "foreign.txt"))
        self.upload(task, session)
        with self.assertRaises(TaskPermissionError) as denied:
            self.submit(task, session)
        self.assertEqual(denied.exception.code, "upload_origin_out_of_scope")
        self.assertFalse(self.posted)

    def test_symlinks_and_oversized_sources_are_not_bound(self):
        task, _, session, _ = self.opened(size=4)
        with self.assertRaises(TaskPermissionError) as size:
            self.upload(task, session)
        self.assertEqual(size.exception.code, "upload_too_large")
        self.source.unlink()
        other = self.source.parent / "private.txt"
        other.write_bytes(b"real")
        self.source.symlink_to(other)
        with self.assertRaises(OSError):
            self.upload(task, session)
        self.assertFalse(self.service.backend._sessions[session].uploads)

    def test_enabling_document_scripts_before_upload_is_denied(self):
        task, _, session, page = self.opened()
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
        )
        with self.assertRaises(PageInspectionError) as denied:
            self.upload(task, session)
        self.assertEqual(denied.exception.code, "scripted_upload_unavailable")
        self.assertFalse(self.service.backend._sessions[session].uploads)

    def test_revocation_during_copy_prevents_native_binding_and_cleans_staging(self):
        import threading

        from services.task_uploads import UploadCopy

        task, grant, session, _ = self.opened()
        gate, entered = threading.Event(), threading.Event()
        original = UploadCopy.copy
        copies = []

        def delayed(copy):
            copies.append(copy)
            entered.set()
            gate.wait(5)
            original(copy)

        with patch.object(UploadCopy, "copy", delayed):
            snapshot = self.inspect(task, session)
            field = next(
                t for t in snapshot.frames[0].targets if "upload" in t.operations
            )
            future = self.service.client(task).submit(
                "page.upload",
                session_id=session,
                snapshot_id=snapshot.snapshot_id,
                target_id=field.target_id,
                source=str(self.source),
            )
            self.wait_for(entered.is_set)
            stage = copies[0].path
            self.service.revoke_grant(task, grant)
            gate.set()
            self.wait_for(future.done)
            self.assertIsNotNone(future.exception())
            self.assertFalse(stage.parent.exists())
        self.assertFalse(self.posted)

    def test_upload_references_are_stale_after_binding_and_multi_file_is_unsupported(
        self,
    ):
        task, _, session, _ = self.opened()
        snapshot = self.inspect(task, session)
        field = next(t for t in snapshot.frames[0].targets if "upload" in t.operations)
        self.upload(task, session)
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.upload",
                task_id=task,
                session_id=session,
                snapshot_id=snapshot.snapshot_id,
                target_id=field.target_id,
                source=str(self.source),
            )
        other, _, second, _ = self.opened(
            html=HTML.replace('type="file"', 'type="file" multiple')
        )
        self.assertFalse(
            any(
                "upload" in t.operations
                for f in self.inspect(other, second).frames
                for t in f.targets
            )
        )

    def test_human_takeover_can_select_an_approved_source(self):
        task, _, session, _ = self.opened()
        self.command("session.watch", task_id=task, session_id=session)
        window = self.service._watchers[session]
        window.take_button.click()
        self.wait_for(lambda: window.snapshot is not None and not window._busy)
        row = next(
            i
            for i in range(window.targets.count())
            if "upload" in window.targets.itemData(i).operations
        )
        window.targets.setCurrentIndex(row)
        self.assertTrue(window.action_buttons["upload"].isEnabled())
        window.action_buttons["upload"].click()
        self.wait_for(lambda: not window._busy)
        self.assertFalse(window.error.text(), window.error.text())
        self.assertTrue(self.service.backend._sessions[session].uploads)
        window.return_button.click()
        self.assertEqual(self.submit(task, session).request_authorized, True)

    def test_submission_cannot_replay_uploaded_body_through_a_redirect(self):
        task, _, session, _ = self.opened()
        type(self).post_responses["/uploaded"] = (307, self.origin + "/replayed", b"")
        self.upload(task, session)
        self.submit(task, session)
        self.assertEqual([path for path, _, _ in self.posted], ["/uploaded"])


class SharedTaskUploadTests(unittest.TestCase):
    setUpClass = classmethod(shared_fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(shared_fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = shared_fixtures.TaskProfileTests.setUp
    tearDown = shared_fixtures.TaskProfileTests.tearDown
    command = TaskUploadTests.command
    load = TaskUploadTests.load
    inspect = TaskUploadTests.inspect
    wait_for = TaskUploadTests.wait_for
    opened = TaskUploadTests.opened
    upload = TaskUploadTests.upload
    submit = TaskUploadTests.submit

    def test_shared_profile_file_submission_keeps_signed_in_cookies(self):
        user = QWebEnginePage(self.profile, self.manager)
        self.pages.append(user)
        user.setUrl(QUrl(self.origin + "/cookie"))
        self.wait_for(lambda: bool(user.title()))
        task, _, session, page = self.opened(profile="existing:work")
        self.assertIs(page.profile(), self.profile)
        self.upload(task, session)
        outcome = self.submit(task, session)
        self.assertTrue(outcome.request_authorized)
        self.assertIn("task_cookie=fixture", self.posted[-1][2])
        self.assertIn(b"SYNTHETIC_FILE_BYTES", self.posted[-1][1])


class UploadScopeTests(unittest.TestCase):
    def test_sources_and_origins_are_explicit_and_subset_of_task_origins(self):
        for values in (
            {"actions": ["upload"]},
            {"upload_sources": ["/tmp/source"]},
            {
                "upload_sources": ["/tmp/source"],
                "upload_origins": ["https://8.8.8.8"],
                "actions": ["upload"],
            },
            {"max_upload_bytes": 0},
            {"max_upload_bytes": True},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TaskScope.parse(values)
