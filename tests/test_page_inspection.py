"""Read-only inspection, authorization, and stale references in real Chromium."""

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import Future
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import test_task_services as fixtures
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWebEngineCore import QWebEngineSettings
from qt_test_support import keep_directory

from services.page_inspector import INSPECTION_WORLD, PageInspectionError, PageSnapshot
from services.task_policy import TaskPermissionError
from services.task_service import TaskService

FIXTURE = """<!doctype html><html><head><title>Inspection fixture</title></head><body>
<h1>Readable document</h1><p>Useful public text</p>
<p hidden>HIDDEN_SECRET</p><p style="display:none">CSS_SECRET</p>
<script>document.title='Script executed'; SCRIPT_SECRET</script>
<a id="link" href="/next">Next page</a><a href="javascript:alert(1)">Unsupported link</a>
<label for="field">Email address</label><input id="field" value="FORM_SECRET">
<label for="password">Password</label><input id="password" type="password" value="PASSWORD_SECRET">
<textarea aria-label="Message">TEXTAREA_SECRET</textarea>
<div contenteditable>EDITABLE_SECRET</div>
<button id="button" disabled>Disabled button</button><input type="file" aria-label="Upload">
<select aria-label="Choice"><option>OPTION_SECRET</option></select>
<input readonly aria-label="Read only" value="READONLY_SECRET">
<div role="button" aria-label="Custom control">Custom</div><canvas>Canvas fallback</canvas>
<iframe srcdoc="&lt;h2&gt;Child content&lt;/h2&gt;&lt;a href='/child'&gt;Child link&lt;/a&gt;"></iframe>
</body></html>"""


class PageInspectionTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    wait_for = fixtures.TaskServiceTests.wait_for
    command = fixtures.TaskServiceTests.command
    request = fixtures.TaskServiceTests.request

    def setUp(self):
        type(self).page_fixtures = {}
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.service = TaskService(Path(self.directory.name) / "tasks.sqlite")

    def tearDown(self):
        self.service.close()
        sip.delete(self.service)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def session(self, *, read=True, origins=None):
        task = self.command("task.create", objective="Inspect synthetic content")
        actions = ["navigate", "inspect", "attach"] + (["read"] if read else [])
        grant = self.request(
            task.task_id, actions=actions, origins=origins or [self.origin]
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command("session.open", task_id=task.task_id)
        page = self.service.backend._sessions[opened.session_id].page
        return task.task_id, grant.grant_id, opened.session_id, page

    def load(self, session_id, page, html=FIXTURE):
        type(self).page_fixtures["/inspection"] = html.encode()
        self.service.backend.navigate(session_id, self.origin + "/inspection")
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is True
        )

    def inspect(self, task_id, session_id):
        return self.command("page.inspect", task_id=task_id, session_id=session_id)

    def target(self, task_id, session_id, snapshot, descriptor):
        return self.command(
            "page.target",
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=descriptor.target_id,
        )

    def script(self, page, script):
        results = []
        page.runJavaScript(script, INSPECTION_WORLD, results.append)
        self.wait_for(lambda: bool(results))
        self.assertIs(results[0], True)
        return results[0]

    def test_readable_text_control_labels_frames_and_values_omitted_without_a_view(
        self,
    ):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        snapshot = self.inspect(task_id, session_id)
        self.assertIsInstance(snapshot, PageSnapshot)
        self.assertEqual(snapshot.source, "rendered_dom")
        self.assertEqual(snapshot.title, "Inspection fixture")
        self.assertEqual(len(snapshot.frames), 2)
        root, child = snapshot.frames
        self.assertIn("Readable document", root.text)
        self.assertEqual(child.parent_frame_id, root.frame_id)
        self.assertIn("Child content", child.text)
        self.assertEqual(child.path, "main/0")
        self.assertFalse(
            any("SECRET" in str(value) for value in asdict(snapshot).values())
        )
        descriptors = {target.label: target for target in root.targets}
        self.assertEqual(descriptors["Email address"].kind, "input")
        self.assertEqual(descriptors["Next page"].href, self.origin + "/next")
        self.assertFalse(descriptors["Disabled button"].enabled)
        self.assertTrue(descriptors["Read only"].read_only)
        self.assertIn("sensitive_field", descriptors["Password"].constraints)
        self.assertEqual(descriptors["Password"].operations, ())
        self.assertIn("file_actions_unavailable", descriptors["Upload"].constraints)
        self.assertIn(
            "unsupported_link_url", descriptors["Unsupported link"].constraints
        )
        self.assertIn(
            "custom_control_unsupported", descriptors["Custom control"].constraints
        )
        self.assertEqual(self.service.backend._sessions[session_id].views, [])
        self.assertEqual(
            self.target(task_id, session_id, snapshot, descriptors["Next page"]),
            descriptors["Next page"],
        )
        journal = repr(self.service.store.events(task_id))
        self.assertNotIn("Useful public text", journal)
        self.assertNotIn("Email address", journal)
        self.assertIn("page.inspected", journal)

    def test_metadata_permission_does_not_expand_to_content_and_clients_cannot_run_scripts(
        self,
    ):
        task_id, _, session_id, page = self.session(read=False)
        self.load(session_id, page)
        self.command("session.inspect", task_id=task_id, session_id=session_id)
        with patch.object(self.service.backend, "inspect_page") as inspect:
            with self.assertRaises(TaskPermissionError):
                self.inspect(task_id, session_id)
        inspect.assert_not_called()
        client = self.service.client(task_id)
        future = client.submit(
            "page.evaluate", session_id=session_id, script="document.cookie"
        )
        self.wait_for(future.done)
        with self.assertRaises(TaskPermissionError):
            future.result()

    def test_references_are_rejected_after_dom_replacement_attribute_changes_or_new_inspection(
        self,
    ):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        first = self.inspect(task_id, session_id)
        descriptor = first.frames[0].targets[0]
        self.script(
            page,
            "document.getElementById('link').outerHTML='<a id=link href=/other>Replacement</a>'; true",
        )
        with self.assertRaises(PageInspectionError) as error:
            self.target(task_id, session_id, first, descriptor)
        self.assertEqual(error.exception.code, "stale_snapshot")
        second = self.inspect(task_id, session_id)
        self.script(page, "document.getElementById('link').href='/changed'; true")
        with self.assertRaises(PageInspectionError):
            self.target(task_id, session_id, second, second.frames[0].targets[0])
        third = self.inspect(task_id, session_id)
        self.inspect(task_id, session_id)
        with self.assertRaises(PageInspectionError):
            self.target(task_id, session_id, third, third.frames[0].targets[0])

    def test_child_frame_changes_and_main_navigation_invalidate_references(self):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        snapshot = self.inspect(task_id, session_id)
        self.script(
            page,
            "document.querySelector('iframe').contentDocument.body.append('Changed child'); true",
        )
        with self.assertRaises(PageInspectionError):
            self.target(task_id, session_id, snapshot, snapshot.frames[0].targets[0])
        snapshot = self.inspect(task_id, session_id)
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/next",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is True
        )
        with self.assertRaises(PageInspectionError):
            self.target(task_id, session_id, snapshot, snapshot.frames[0].targets[0])

    def test_cross_task_and_forged_target_references_are_rejected(self):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        snapshot = self.inspect(task_id, session_id)
        other_id, _, other_session, other_page = self.session()
        self.load(other_session, other_page)
        with self.assertRaises(TaskPermissionError):
            self.inspect(other_id, session_id)
        with self.assertRaises(PageInspectionError):
            self.target(
                other_id, other_session, snapshot, snapshot.frames[0].targets[0]
            )
        with self.assertRaises(PageInspectionError) as error:
            self.command(
                "page.target",
                task_id=task_id,
                session_id=session_id,
                snapshot_id=snapshot.snapshot_id,
                target_id="client-invented",
            )
        self.assertEqual(error.exception.code, "target_unknown")

    def test_large_pages_report_text_target_and_frame_limits(self):
        task_id, _, session_id, page = self.session()
        html = (
            "<p>"
            + "bounded " * 5000
            + "</p>"
            + "<a href=/next>Link</a>" * 220
            + "<iframe srcdoc='child'></iframe>" * 20
        )
        self.load(session_id, page, html)
        snapshot = self.inspect(task_id, session_id)
        self.assertLessEqual(sum(len(frame.text) for frame in snapshot.frames), 32000)
        self.assertLessEqual(sum(len(frame.targets) for frame in snapshot.frames), 200)
        self.assertLessEqual(len(snapshot.frames), 16)
        for code in ("text_truncated", "targets_truncated", "frame_limit"):
            self.assertIn(code, snapshot.limitations)

    def test_accessible_frames_are_read_and_cross_origin_or_sandboxed_frames_report_limits(
        self,
    ):
        other = f"http://localhost:{self.server.server_port}"
        task_id, _, session_id, page = self.session(origins=[self.origin, other])
        type(self).page_fixtures["/cross"] = b"<p>CROSS_ORIGIN_SECRET</p>"
        type(self).page_fixtures["/same"] = b"<p>Same-origin child text</p>"
        self.load(
            session_id,
            page,
            f'<p>Main text</p><iframe src="{other}/cross"></iframe>'
            '<iframe sandbox srcdoc="&lt;p&gt;SANDBOX_SECRET&lt;/p&gt;"></iframe>'
            '<iframe src="/same"></iframe>',
        )
        snapshot = self.inspect(task_id, session_id)
        self.assertEqual(len(snapshot.frames), 4)
        self.assertIn("Same-origin child text", snapshot.frames[3].text)
        for frame in snapshot.frames[1:3]:
            self.assertEqual(frame.text, "")
            self.assertIn("frame_inaccessible_or_not_ready", frame.limitations)
        self.assertNotIn("SECRET", repr(snapshot))
        self.assertTrue(any(path == "/cross" for path, _ in self.requests))

    def test_inspection_preserves_dom_and_keeps_site_scripts_disabled(self):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        before, after = [], []
        page.runJavaScript("document.body.innerHTML", INSPECTION_WORLD, before.append)
        self.wait_for(lambda: bool(before))
        snapshot = self.inspect(task_id, session_id)
        self.target(task_id, session_id, snapshot, snapshot.frames[0].targets[0])
        page.runJavaScript("document.body.innerHTML", INSPECTION_WORLD, after.append)
        self.wait_for(lambda: bool(after))
        self.assertEqual(before, after)
        self.assertFalse(
            page.settings().testAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled
            )
        )

    def test_superseded_inspection_and_shutdown_settle_pending_futures(self):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        callbacks = []
        with patch.object(
            page,
            "runJavaScript",
            side_effect=lambda script, world, callback: callbacks.append(callback),
        ):
            first = self.service.broker.submit(
                "page.inspect", task_id=task_id, session_id=session_id
            )
            self.wait_for(lambda: len(callbacks) == 1)
            second = self.service.broker.submit(
                "page.inspect", task_id=task_id, session_id=session_id
            )
            self.wait_for(lambda: len(callbacks) == 2)
        with self.assertRaises(PageInspectionError) as error:
            first.result()
        self.assertEqual(error.exception.code, "inspection_superseded")
        self.assertFalse(second.done())
        self.service.close()
        with self.assertRaises(RuntimeError):
            second.result()
        for callback in callbacks:
            callback(None)

    def test_not_ready_timeout_and_late_callbacks_do_not_leave_running_requests(self):
        task_id, _, session_id, page = self.session()
        with self.assertRaises(PageInspectionError) as error:
            self.inspect(task_id, session_id)
        self.assertEqual(error.exception.code, "page_not_ready")
        self.load(session_id, page)
        callbacks = []
        self.service.backend.inspector.timeout_ms = 10
        with patch.object(
            page,
            "runJavaScript",
            side_effect=lambda script, world, callback: callbacks.append(callback),
        ):
            with self.assertRaises(PageInspectionError) as error:
                self.inspect(task_id, session_id)
        self.assertEqual(error.exception.code, "inspection_timeout")
        callbacks[0](None)
        state = self.service.backend.inspector._states[session_id]
        self.assertEqual(state.pending, set())
        self.assertIsNone(state.snapshot)

    def test_pending_read_is_rejected_on_revocation_and_late_native_callback_is_safe(
        self,
    ):
        task_id, grant_id, session_id, page = self.session()
        self.load(session_id, page)
        callbacks = []
        with patch.object(
            page,
            "runJavaScript",
            side_effect=lambda script, world, callback: callbacks.append(callback),
        ):
            future = self.service.broker.submit(
                "page.inspect", task_id=task_id, session_id=session_id
            )
            self.wait_for(lambda: bool(callbacks))
        self.assertFalse(future.done())
        self.service.revoke_grant(task_id, grant_id)
        self.wait_for(future.done)
        with self.assertRaises(PageInspectionError):
            future.result()
        self.assertTrue(sip.isdeleted(page))
        callbacks[0](None)

    def test_journal_failure_does_not_disclose_completed_content(self):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        record = self.service.store.record_event

        def fail_inspected(task_id, kind, payload):
            if kind == "page.inspected":
                raise sqlite3.OperationalError("fixture journal failure")
            return record(task_id, kind, payload)

        with patch.object(
            self.service.store, "record_event", side_effect=fail_inspected
        ):
            with self.assertRaises(sqlite3.Error):
                self.inspect(task_id, session_id)
        self.assertTrue(self.service.failure)

    def test_permission_is_rechecked_before_a_delayed_content_reply_is_disclosed(self):
        now = [100.0]
        self.service._clock = lambda: now[0]
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        native = page.runJavaScript
        replies = []

        def defer(script, world, callback):
            native(script, world, lambda raw: replies.append((callback, raw)))

        with patch.object(page, "runJavaScript", side_effect=defer):
            future = self.service.broker.submit(
                "page.inspect", task_id=task_id, session_id=session_id
            )
            self.wait_for(lambda: bool(replies))
        self.assertFalse(future.done())
        now[0] = 1000.0
        callback, raw = replies[0]
        callback(raw)
        with self.assertRaises(TaskPermissionError) as error:
            future.result()
        self.assertEqual(error.exception.code, "expired")
        self.assertFalse(
            any(
                event.kind == "page.inspected"
                for event in self.service.store.events(task_id)
            )
        )

    def test_broker_flattens_deferred_worker_replies_and_ignores_late_shutdown_results(
        self,
    ):
        sources = []

        def defer():
            source = Future()
            sources.append(source)
            return source

        self.service.broker._handlers["fixture.defer"] = defer
        first = self.service.broker.submit("fixture.defer")
        self.wait_for(lambda: len(sources) == 1)
        worker = threading.Thread(target=lambda: sources[0].set_result("observed"))
        worker.start()
        worker.join()
        self.assertEqual(first.result(), "observed")
        self.assertEqual(self.service.broker._pending, set())
        second = self.service.broker.submit("fixture.defer")
        self.wait_for(lambda: len(sources) == 2)
        self.service.close()
        with self.assertRaises(RuntimeError):
            second.result()
        sources[1].set_result("late result")
        self.assertEqual(self.service.broker._pending, set())
