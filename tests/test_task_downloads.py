"""Download authority, native networking, safe publication and cleanup."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_page_inspection as fixtures
import test_task_profiles as shared_fixtures
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage

from services.task_downloads import DownloadDestination
from services.task_policy import LiveGrant, TaskPermissionError, TaskScope


class DownloadDestinationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / "file.bin"

    def output(self):
        output = DownloadDestination(str(self.target))
        self.addCleanup(output.close)
        return output

    def test_cleanup_retries_while_a_cancelled_payload_is_still_written(self):
        import os
        import time

        from qt_test_support import WAIT_SECONDS, application

        app = application()
        output = self.output()
        stage = output.stage
        real_cleanup = stage.cleanup
        attempts = []

        def busy_then_clean():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError(39, "Directory not empty")
            real_cleanup()

        stage.cleanup = busy_then_clean
        stage_fd = output.stage_fd
        output.close()  # Must not raise into the Qt slot that cancelled it.
        self.assertIsNotNone(output.stage_fd)
        deadline = time.monotonic() + WAIT_SECONDS
        while output.stage_fd is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        self.assertEqual(len(attempts), 2)
        self.assertIsNone(output.stage_fd)
        self.assertIsNone(output.parent_fd)
        with self.assertRaises(OSError):
            os.fstat(stage_fd)
        self.assertEqual(
            [p.name for p in self.root.iterdir() if p.name.startswith(".steed")], []
        )

    def test_explicit_destinations_and_size_limit_are_required(self):
        for values in (
            {"actions": ["download"]},
            {"download_destinations": [str(self.target)]},
            {"actions": ["download"], "download_destinations": ["../file"]},
            {"actions": ["download"], "download_destinations": ["/tmp/../file"]},
            {"actions": ["download"], "download_destinations": ["/tmp/a\nfile"]},
            {"max_download_bytes": True},
            {"max_download_bytes": 104857601},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TaskScope.parse(values)
        grant = LiveGrant(
            TaskScope.parse(
                {"actions": ["download"], "download_destinations": [str(self.target)]}
            )
        )
        grant.check("download", destination=str(self.target))
        with self.assertRaises(TaskPermissionError):
            grant.check("download", destination=str(self.root / "other"))

    def test_publish_is_atomic_private_and_never_overwrites(self):
        output = self.output()
        stage = Path(output.staging_directory) / "payload"
        stage.write_bytes(b"synthetic")
        self.assertFalse(self.target.exists())
        output.publish(9, 100)
        self.assertEqual(self.target.read_bytes(), b"synthetic")
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            output.publish(9, 100)

    def test_existing_files_and_symlinks_are_rejected(self):
        self.target.write_bytes(b"existing")
        with self.assertRaises(TaskPermissionError):
            self.output()
        self.target.unlink()
        self.target.symlink_to(self.root / "missing")
        with self.assertRaises(TaskPermissionError):
            self.output()
        folder = self.root / "alias"
        folder.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            DownloadDestination(str(folder / "other"))

    def test_parent_substitution_cannot_redirect_staging_or_publication(self):
        parent = self.root / "folder"
        parent.mkdir()
        self.target = parent / "file.bin"
        output = self.output()
        staging = Path(output.staging_directory)
        renamed = self.root / "renamed"
        parent.rename(renamed)
        parent.mkdir()
        (staging / "payload").write_bytes(b"safe")
        self.assertFalse(list(parent.iterdir()))
        with self.assertRaises(TaskPermissionError):
            output.publish(4, 100)
        self.assertFalse(self.target.exists())

    def test_late_file_symlink_and_size_mismatch_fail_closed(self):
        output = self.output()
        (Path(output.staging_directory) / "payload").write_bytes(b"data")
        with self.assertRaises(TaskPermissionError):
            output.publish(3, 100)
        self.target.symlink_to(self.root / "protected")
        with self.assertRaises(FileExistsError):
            output.publish(4, 100)
        self.assertFalse((self.root / "protected").exists())


class TaskDownloadTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.PageInspectionTests.tearDownClass.__func__)
    setUp = fixtures.PageInspectionTests.setUp
    tearDown = fixtures.PageInspectionTests.tearDown
    command = fixtures.PageInspectionTests.command
    request = fixtures.PageInspectionTests.request
    inspect = fixtures.PageInspectionTests.inspect
    wait_for = fixtures.PageInspectionTests.wait_for

    def load(self, session, page, html):
        type(self).page_fixtures["/inspection"] = html.encode()
        self.command(
            "session.navigate",
            task_id=self.service._sessions[session],
            session_id=session,
            url=self.origin + "/inspection",
        )
        self.wait_for(
            lambda: (
                self.service.backend.inspect(session).load_success is True
                and page.url().toString() == self.origin + "/inspection"
            )
        )

    def opened(self, *, actions=None, size=100000, link="/download", profile="private"):
        self.target = Path(self.directory.name) / "result.bin"
        task = self.command("task.create", objective="Download synthetic fixture")
        scope = {
            "actions": actions or ["navigate", "inspect", "read", "download"],
            "download_destinations": [str(self.target)],
            "max_download_bytes": size,
            "origins": [self.origin],
            "allow_private_network": True,
            "max_operations": 100,
            "profile": profile,
        }
        grant = self.command("grant.request", task_id=task.task_id, scope=scope)
        self.service.approve_grant(task.task_id, grant.grant_id)
        session = self.command(
            "session.open", task_id=task.task_id, profile=profile
        ).session_id
        page = self.service.backend._sessions[session].page
        self.load(session, page, f'<a href="{link}">Download fixture</a>')
        return task.task_id, grant.grant_id, session, page

    def start(self, task, session, destination=None):
        snapshot = self.inspect(task, session)
        target = snapshot.frames[0].targets[0]
        return self.command(
            "page.download",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            destination=destination or str(self.target),
        )

    def finished(self, session, started):
        transfer = self.service.backend._sessions[session].downloads[
            started.download_id
        ]
        self.wait_for(transfer.result.done, timeout=35)
        return transfer.result.result()

    def test_native_download_publishes_observed_bytes_and_receipt(self):
        task, _, session, _ = self.opened()
        started = self.start(task, session)
        result = self.finished(session, started)
        self.assertEqual(result.state, "completed", result)
        self.assertIn(b"Independent page", self.target.read_bytes())
        self.assertEqual(result.received_bytes, self.target.stat().st_size)
        status = self.command(
            "download.status",
            task_id=task,
            session_id=session,
            download_id=started.download_id,
        )
        self.assertEqual(status, result)
        events = self.service.store.events(task)
        self.assertIn("download.publish_requested", [e.kind for e in events])
        self.assertIn("download.finished", [e.kind for e in events])
        self.assertNotIn("Independent page", repr(events))
        self.assertFalse(list(self.target.parent.glob(".steed-download-*")))

    def test_html_response_does_not_claim_a_download(self):
        task, _, session, _ = self.opened(link="/next")
        result = self.finished(session, self.start(task, session))
        self.assertEqual(result.error, "not_a_download")
        self.assertFalse(self.target.exists())

    def test_oversized_response_and_out_of_scope_destination_are_denied(self):
        task, _, session, _ = self.opened(size=5)
        with self.assertRaises(TaskPermissionError):
            self.start(task, session, str(self.target.parent / "other"))
        result = self.finished(session, self.start(task, session))
        self.assertEqual(result.error, "download_too_large", result)
        self.assertFalse(self.target.exists())

    def test_forbidden_redirect_never_reaches_the_other_origin(self):
        self.requests.clear()
        task, _, session, _ = self.opened(link="/redirect-out")
        result = self.finished(session, self.start(task, session))
        self.assertNotEqual(result.state, "completed")
        self.assertFalse(any("forbidden" in p for p, _ in self.requests))
        self.assertFalse(self.target.exists())

    def test_close_and_revoke_discard_pending_transfer(self):
        for close in (False, True):
            with self.subTest(close=close):
                task, grant, session, _ = self.opened(link="/slow")
                started = self.start(task, session)
                transfer = self.service.backend._sessions[session].downloads[
                    started.download_id
                ]
                if close:
                    self.command("session.close", task_id=task, session_id=session)
                else:
                    self.service.revoke_grant(task, grant)
                self.assertTrue(transfer.result.done())
                self.assertFalse(self.target.exists())
                self.assertFalse(list(self.target.parent.glob(".steed-download-*")))

    def test_publish_journal_failure_prevents_file_publication(self):
        task, _, session, _ = self.opened()
        original = self.service.store.record_event

        def recorded(task_id, kind, payload):
            if kind == "download.publish_requested":
                raise OSError("synthetic journal failure")
            return original(task_id, kind, payload)

        with patch.object(self.service.store, "record_event", side_effect=recorded):
            self.start(task, session)
            self.wait_for(
                lambda: bool(self.service.failure) and not self.service._sessions
            )
        self.assertFalse(self.target.exists())
        self.assertFalse(self.service._sessions)

    def test_cancel_active_stream_discards_bytes_and_keeps_session_usable(self):
        task, _, session, _ = self.opened(link="/download-slow", size=1024 * 1024)
        type(self).page_fixtures["/download-slow"] = b"d" * (512 * 1024)
        started = self.start(task, session)
        transfer = self.service.backend._sessions[session].downloads[
            started.download_id
        ]
        self.wait_for(lambda: transfer.received > 0)
        result = self.command(
            "download.cancel",
            task_id=task,
            session_id=session,
            download_id=started.download_id,
        )
        self.assertEqual(result.state, "cancelled")
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob(".steed-download-*")))
        self.command(
            "session.navigate",
            task_id=task,
            session_id=session,
            url=self.origin + "/next",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session).load_success is True
        )

    def test_unknown_length_is_measured_before_publication(self):
        task, _, session, _ = self.opened(link="/download-unknown", size=5)
        result = self.finished(session, self.start(task, session))
        self.assertEqual(result.error, "download_too_large", result)
        self.assertFalse(self.target.exists())

    def test_expiry_during_active_transfer_discards_bytes(self):
        now = [0.0]
        self.service._clock = lambda: now[0]
        task, _, session, _ = self.opened(link="/download-slow", size=1024 * 1024)
        type(self).page_fixtures["/download-slow"] = b"d" * (512 * 1024)
        started = self.start(task, session)
        transfer = self.service.backend._sessions[session].downloads[
            started.download_id
        ]
        self.wait_for(lambda: transfer.received > 0)
        now[0] = 601.0
        self.wait_for(lambda: not self.service._sessions)
        self.assertTrue(transfer.result.done())
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob(".steed-download-*")))

    def test_foreign_task_cannot_read_or_cancel_an_owned_download(self):
        task, _, session, _ = self.opened(link="/download-slow", size=1024 * 1024)
        type(self).page_fixtures["/download-slow"] = b"d" * (64 * 1024)
        started = self.start(task, session)
        foreign = self.command("task.create", objective="Different task")
        for command in ("download.status", "download.cancel"):
            with self.subTest(command=command), self.assertRaises(TaskPermissionError):
                self.command(
                    command,
                    task_id=foreign.task_id,
                    session_id=session,
                    download_id=started.download_id,
                )
        self.assertEqual(self.finished(session, started).state, "completed")

    def test_receipt_journal_failure_disables_access_after_publication(self):
        task, _, session, _ = self.opened()
        original = self.service.store.record_event

        def recorded(task_id, kind, payload):
            if kind == "download.finished":
                raise OSError("synthetic receipt failure")
            return original(task_id, kind, payload)

        with patch.object(self.service.store, "record_event", side_effect=recorded):
            self.start(task, session)
            self.wait_for(
                lambda: bool(self.service.failure) and not self.service._sessions
            )
        self.assertTrue(self.target.exists())
        self.assertNotIn(
            "download.finished", [e.kind for e in self.service.store.events(task)]
        )

    def test_late_destination_creation_is_preserved(self):
        task, _, session, _ = self.opened(link="/download-slow", size=1024 * 1024)
        type(self).page_fixtures["/download-slow"] = b"d" * (64 * 1024)
        started = self.start(task, session)
        self.target.write_bytes(b"user's existing file")
        result = self.finished(session, started)
        self.assertNotEqual(result.state, "completed")
        self.assertEqual(self.target.read_bytes(), b"user's existing file")


class SharedTaskDownloadTests(unittest.TestCase):
    setUpClass = classmethod(shared_fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(shared_fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = shared_fixtures.TaskProfileTests.setUp
    tearDown = shared_fixtures.TaskProfileTests.tearDown
    command = TaskDownloadTests.command
    load = TaskDownloadTests.load
    inspect = TaskDownloadTests.inspect
    wait_for = TaskDownloadTests.wait_for
    opened = TaskDownloadTests.opened
    start = TaskDownloadTests.start
    finished = TaskDownloadTests.finished

    def test_shared_cookie_download_and_ordinary_downloads_are_independent(self):
        user = QWebEnginePage(self.profile, self.manager)
        self.pages.append(user)
        user.setUrl(QUrl(self.origin + "/cookie"))
        self.wait_for(lambda: bool(user.title()))
        task, _, session, page = self.opened(profile="existing:work")
        self.assertIs(page.profile(), self.profile)
        result = self.finished(session, self.start(task, session))
        self.assertEqual(result.state, "completed", result)
        self.assertTrue(
            any(
                p == "/download" and "task_cookie=fixture" in c
                for p, c in self.requests
            )
        )
        self.assertFalse(self.registry.downloads.records)
        ordinary = self.target.parent / "user.bin"
        with patch.object(
            self.registry.downloads, "destination_picker", return_value=str(ordinary)
        ) as picker:
            user.setUrl(QUrl(self.origin + "/download"))
            self.wait_for(
                lambda: any(
                    r.state == "DownloadCompleted"
                    for r in self.registry.downloads.records.values()
                )
            )
        picker.assert_called_once()
        self.assertTrue(ordinary.exists())
        self.assertEqual(self.target.read_bytes(), ordinary.read_bytes())

    def test_shared_download_redirect_remains_scoped(self):
        self.requests.clear()
        task, _, session, _ = self.opened(profile="existing:work", link="/redirect-out")
        result = self.finished(session, self.start(task, session))
        self.assertNotEqual(result.state, "completed")
        self.assertFalse(any("forbidden" in p for p, _ in self.requests))
        self.assertFalse(self.target.exists())
