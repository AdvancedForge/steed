"""Scriptless control edits, scroll receipts, bounded waits, and live authority."""

import sqlite3
import unittest
from unittest.mock import patch

import test_page_inspection as inspection
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings

from services.page_inspector import INSPECTION_WORLD, PageInspectionError
from services.task_policy import TaskPermissionError

FIXTURE = """<!doctype html><html><body style="height:5000px">
<p>Action fixture</p><input id="text" aria-label="Text" value="OLD_SECRET">
<textarea aria-label="Message">OLD_TEXTAREA_SECRET</textarea>
<input aria-label="Limited" maxlength="5">
<input aria-label="Check" type="checkbox"><input aria-label="Radio" type="radio">
<select aria-label="Choice"><option>OPTION_SECRET</option><option>Second</option><option disabled>Disabled</option></select>
<input aria-label="Password" type="password" value="PASSWORD_SECRET">
<input aria-label="Read only" readonly><input aria-label="Disabled" disabled>
<input aria-label="Number" type="number"><input aria-label="Upload" type="file">
<select aria-label="Multiple" multiple><option>One</option><option>Two</option></select>
<form action="/must-not-submit" method="post"><button>Submit</button></form>
<div role="button">Custom</div>
<script>document.title='Scripts executed'</script>
</body></html>"""


class PageActionTests(unittest.TestCase):
    setUpClass = classmethod(inspection.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(inspection.PageInspectionTests.tearDownClass.__func__)
    setUp = inspection.PageInspectionTests.setUp
    tearDown = inspection.PageInspectionTests.tearDown
    session = inspection.PageInspectionTests.session
    load = inspection.PageInspectionTests.load
    inspect = inspection.PageInspectionTests.inspect
    script = inspection.PageInspectionTests.script
    command = inspection.PageInspectionTests.command
    request = inspection.PageInspectionTests.request
    wait_for = inspection.PageInspectionTests.wait_for

    def opened(self, *, actions=None):
        task_id, grant_id, session_id, page = self.session(
            actions=actions or ["navigate", "inspect", "read", "edit", "scroll"]
        )
        self.load(session_id, page, FIXTURE)
        return task_id, grant_id, session_id, page

    def act(self, task_id, session_id, label, operation, **arguments):
        snapshot = self.inspect(task_id, session_id)
        target = next(t for t in snapshot.frames[0].targets if t.label == label)
        return self.command(
            "page.act",
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            operation=operation,
            **arguments,
        )

    def evaluate(self, page, expression):
        replies = []
        page.runJavaScript(expression, INSPECTION_WORLD, replies.append)
        self.wait_for(lambda: bool(replies))
        return replies[0]

    def test_text_uses_native_editing_events_and_undo_without_leaking_values(self):
        task_id, _, session_id, page = self.opened()
        self.script(
            page,
            "window.events=[];document.getElementById('text').addEventListener('input',e=>events.push({trusted:e.isTrusted,type:e.inputType}));true",
        )
        text = "ENTERED_SECRET ' </script> ${literal} ☃"
        outcome = self.act(task_id, session_id, "Text", "enter", text=text)
        self.assertEqual(outcome.status, "applied")
        self.assertTrue(outcome.text_matches)
        self.assertEqual(outcome.input_semantics, "chromium_editing_command")
        self.assertEqual(
            self.evaluate(page, "document.getElementById('text').value"), text
        )
        self.assertEqual(
            self.evaluate(page, "events"), [{"trusted": True, "type": "insertText"}]
        )
        page.triggerAction(QWebEnginePage.WebAction.Undo)
        self.assertEqual(
            self.evaluate(page, "document.getElementById('text').value"), "OLD_SECRET"
        )
        self.assertNotIn("SECRET", repr(self.service.store.events(task_id)))
        snapshot = self.inspect(task_id, session_id)
        self.assertNotIn("SECRET", repr(snapshot))
        self.assertFalse(
            page.settings().testAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled
            )
        )

    def test_entry_reports_length_constraints_multiline_and_clearing(self):
        task_id, _, session_id, page = self.opened()
        outcome = self.act(task_id, session_id, "Limited", "enter", text="longer")
        self.assertEqual(outcome.status, "constrained")
        self.assertFalse(outcome.text_matches)
        outcome = self.act(
            task_id, session_id, "Message", "enter", text="first\nsecond"
        )
        self.assertTrue(outcome.text_matches)
        self.assertEqual(
            self.evaluate(page, "document.querySelector('textarea').value"),
            "first\nsecond",
        )
        self.assertTrue(
            self.act(task_id, session_id, "Text", "enter", text="").text_matches
        )

    def test_checkbox_radio_and_selection_report_observed_state(self):
        task_id, _, session_id, _ = self.opened()
        self.assertTrue(self.act(task_id, session_id, "Check", "activate").checked)
        self.assertFalse(self.act(task_id, session_id, "Check", "activate").checked)
        self.assertTrue(self.act(task_id, session_id, "Radio", "activate").checked)
        self.assertEqual(
            self.act(task_id, session_id, "Radio", "activate").status, "no_change"
        )
        self.assertEqual(
            self.act(task_id, session_id, "Choice", "select", index=1).selected_index, 1
        )
        for index in (2, 20):
            with self.assertRaises(PageInspectionError):
                self.act(task_id, session_id, "Choice", "select", index=index)

    def test_sensitive_disabled_special_and_submit_controls_remain_unsupported(self):
        task_id, _, session_id, _ = self.opened()
        for label, operation, arguments in (
            ("Password", "enter", {"text": "fixture"}),
            ("Read only", "enter", {"text": "fixture"}),
            ("Disabled", "enter", {"text": "fixture"}),
            ("Number", "enter", {"text": "4"}),
            ("Upload", "enter", {"text": "/tmp/fixture"}),
            ("Submit", "activate", {}),
            ("Custom", "activate", {}),
            ("Multiple", "select", {"index": 1}),
        ):
            with self.subTest(label=label), self.assertRaises(PageInspectionError):
                self.act(task_id, session_id, label, operation, **arguments)
        self.assertFalse(any("must-not-submit" in path for path, _ in self.requests))

    def test_old_grants_do_not_gain_edit_or_scroll_authority(self):
        task_id, _, session_id, _ = self.opened(actions=["navigate", "read"])
        snapshot = self.inspect(task_id, session_id)
        with patch.object(self.service.backend, "act") as act:
            with self.assertRaises(TaskPermissionError):
                self.command(
                    "page.act",
                    task_id=task_id,
                    session_id=session_id,
                    snapshot_id=snapshot.snapshot_id,
                    target_id=snapshot.frames[0].targets[0].target_id,
                    operation="enter",
                    text="fixture",
                )
        act.assert_not_called()
        with self.assertRaises(TaskPermissionError):
            self.command(
                "page.scroll",
                task_id=task_id,
                session_id=session_id,
                snapshot_id=snapshot.snapshot_id,
                dy=100,
            )

    def test_actions_require_read_and_correct_task_identity(self):
        task_id, _, session_id, _ = self.opened()
        snapshot = self.inspect(task_id, session_id)
        other_id, _, other_session, _ = self.session(actions=["navigate", "edit"])
        for actor, owned_session in ((other_id, session_id), (other_id, other_session)):
            with self.assertRaises(TaskPermissionError):
                self.command(
                    "page.act",
                    task_id=actor,
                    session_id=owned_session,
                    snapshot_id=snapshot.snapshot_id,
                    target_id=snapshot.frames[0].targets[0].target_id,
                    operation="enter",
                    text="fixture",
                )

    def test_mutation_and_completed_edits_invalidate_old_references(self):
        task_id, _, session_id, page = self.opened()
        snapshot = self.inspect(task_id, session_id)
        target = snapshot.frames[0].targets[0]
        arguments = dict(
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            operation="enter",
            text="fixture",
        )
        self.command("page.act", **arguments)
        with self.assertRaises(PageInspectionError):
            self.command("page.act", **arguments)
        snapshot = self.inspect(task_id, session_id)
        target = snapshot.frames[0].targets[0]
        arguments.update(snapshot_id=snapshot.snapshot_id, target_id=target.target_id)
        self.script(page, "document.getElementById('text').type='password';true")
        with self.assertRaises(PageInspectionError):
            self.command("page.act", **arguments)

    def test_scroll_has_observed_offsets_and_invalidates_the_snapshot(self):
        task_id, _, session_id, _ = self.opened()
        snapshot = self.inspect(task_id, session_id)
        outcome = self.command(
            "page.scroll",
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            dy=400,
        )
        self.assertEqual(outcome.status, "applied")
        self.assertGreater(outcome.scroll_position[1], 0)
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.scroll",
                task_id=task_id,
                session_id=session_id,
                snapshot_id=snapshot.snapshot_id,
                dy=100,
            )

    def test_wait_for_text_matches_changes_without_persisting_the_query(self):
        task_id, _, session_id, page = self.opened()
        future = self.service.broker.submit(
            "page.wait",
            task_id=task_id,
            session_id=session_id,
            condition="text",
            text="WAIT_SECRET",
            timeout_ms=1000,
        )
        self.wait_for(lambda: bool(self.service.backend._sessions[session_id].waits))
        page.runJavaScript("document.body.append('WAIT_SECRET')", INSPECTION_WORLD)
        self.wait_for(future.done)
        self.assertEqual(future.result().status, "matched")
        self.assertNotIn("WAIT_SECRET", repr(self.service.store.events(task_id)))

    def test_wait_is_bounded_and_does_not_search_control_values(self):
        task_id, _, session_id, _ = self.opened()
        outcome = self.command(
            "page.wait",
            task_id=task_id,
            session_id=session_id,
            condition="text",
            text="OLD_SECRET",
            timeout_ms=20,
        )
        self.assertEqual(outcome.status, "timed_out")
        self.assertIn("form_values_omitted", outcome.limitations)
        self.assertEqual(self.service.backend._sessions[session_id].waits, set())

    def test_ready_wait_observes_load_completion_and_failure(self):
        task_id, _, session_id, _ = self.opened()
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/slow",
        )
        outcome = self.command(
            "page.wait", task_id=task_id, session_id=session_id, condition="ready"
        )
        self.assertEqual(outcome.status, "matched")
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/redirect-out",
        )
        outcome = self.command(
            "page.wait", task_id=task_id, session_id=session_id, condition="ready"
        )
        self.assertEqual(outcome.status, "load_failed")

    def test_ready_wait_after_superseding_a_load_reports_the_current_document(self):
        task_id, _, session_id, _ = self.opened()
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/slow",
        )
        self.command(
            "session.navigate",
            task_id=task_id,
            session_id=session_id,
            url=self.origin + "/replacement",
        )
        outcome = self.command("page.wait", task_id=task_id, session_id=session_id)
        self.assertEqual(outcome.status, "matched")
        self.assertEqual(outcome.url, self.origin + "/replacement")

    def test_expiry_rejects_a_delayed_action_receipt_after_native_dispatch(self):
        now = [100.0]
        self.service._clock = lambda: now[0]
        task_id, _, session_id, page = self.opened()
        snapshot = self.inspect(task_id, session_id)
        native = page.runJavaScript
        replies = []

        def defer(script, world, callback):
            native(script, world, lambda raw: replies.append((callback, raw)))

        with patch.object(page, "runJavaScript", side_effect=defer):
            future = self.service.broker.submit(
                "page.act",
                task_id=task_id,
                session_id=session_id,
                snapshot_id=snapshot.snapshot_id,
                target_id=snapshot.frames[0].targets[0].target_id,
                operation="enter",
                text="fixture",
            )
            self.wait_for(lambda: bool(replies))
        now[0] = 1000.0
        callback, raw = replies[0]
        callback(raw)
        with self.assertRaises(TaskPermissionError):
            future.result()
        self.assertFalse(
            any(
                event.kind == "page.action_finished"
                for event in self.service.store.events(task_id)
            )
        )

    def test_wait_is_cancelled_by_navigation_stop_and_revocation(self):
        for interrupt in ("navigate", "stop", "revoke"):
            with self.subTest(interrupt=interrupt):
                task_id, grant_id, session_id, _ = self.opened()
                future = self.service.broker.submit(
                    "page.wait",
                    task_id=task_id,
                    session_id=session_id,
                    condition="text",
                    text="Never appears",
                )
                self.wait_for(
                    lambda sid=session_id: bool(
                        self.service.backend._sessions[sid].waits
                    )
                )
                if interrupt == "revoke":
                    self.service.revoke_grant(task_id, grant_id)
                else:
                    arguments = dict(task_id=task_id, session_id=session_id)
                    if interrupt == "navigate":
                        arguments["url"] = self.origin + "/next"
                    self.command("session." + interrupt, **arguments)
                self.wait_for(future.done)
                with self.assertRaises(PageInspectionError):
                    future.result()

    def test_queued_action_receipts_recheck_live_permission_and_ignore_late_callbacks(
        self,
    ):
        task_id, grant_id, session_id, page = self.opened()
        snapshot = self.inspect(task_id, session_id)
        callbacks = []
        with patch.object(
            page,
            "runJavaScript",
            side_effect=lambda script, world, callback: callbacks.append(callback),
        ):
            future = self.service.broker.submit(
                "page.act",
                task_id=task_id,
                session_id=session_id,
                snapshot_id=snapshot.snapshot_id,
                target_id=snapshot.frames[0].targets[0].target_id,
                operation="enter",
                text="fixture",
            )
            self.wait_for(lambda: bool(callbacks))
        self.service.revoke_grant(task_id, grant_id)
        self.wait_for(future.done)
        with self.assertRaises(PageInspectionError):
            future.result()
        callbacks[0](None)

    def fail_evidence(self, failure_kind):
        record = self.service.store.record_event

        def fail(task_id, kind, payload):
            if kind == failure_kind:
                raise sqlite3.OperationalError("fixture")
            return record(task_id, kind, payload)

        return patch.object(self.service.store, "record_event", side_effect=fail)

    def test_request_evidence_failure_prevents_edit(self):
        task_id, _, session_id, _ = self.opened()
        with (
            self.fail_evidence("page.action_requested"),
            patch.object(self.service.backend, "act") as dispatch,
        ):
            with self.assertRaises(sqlite3.Error):
                self.act(task_id, session_id, "Text", "enter", text="fixture")
        dispatch.assert_not_called()
        self.assertTrue(self.service.failure)

    def test_receipt_failure_does_not_report_a_dispatched_edit_as_successful(self):
        task_id, _, session_id, _ = self.opened()
        with (
            self.fail_evidence("page.action_finished"),
            patch.object(
                self.service.backend, "act", wraps=self.service.backend.act
            ) as dispatch,
        ):
            with self.assertRaises(sqlite3.Error):
                self.act(task_id, session_id, "Text", "enter", text="fixture")
        dispatch.assert_called_once()
        self.assertTrue(self.service.failure)

    def test_invalid_action_and_wait_arguments_never_reach_the_engine(self):
        task_id, _, session_id, _ = self.opened()
        for operation, arguments in (
            ("enter", {"text": "x" * 4097}),
            ("enter", {"text": "\x00"}),
            ("select", {"index": True}),
            ("activate", {"text": "invalid"}),
            ("submit", {}),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.act(task_id, session_id, "Text", operation, **arguments)
        with self.assertRaises(ValueError):
            self.command(
                "page.wait",
                task_id=task_id,
                session_id=session_id,
                condition="script",
                timeout_ms=10001,
            )

    def test_actions_in_readable_child_frames_use_the_correct_document(self):
        task_id, _, session_id, page = self.opened()
        self.load(
            session_id,
            page,
            '<input value="PARENT_SECRET"><iframe srcdoc="&lt;input aria-label=Child&gt;"></iframe>',
        )
        snapshot = self.inspect(task_id, session_id)
        child = snapshot.frames[1].targets[0]
        outcome = self.command(
            "page.act",
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=child.target_id,
            operation="enter",
            text="fixture",
        )
        self.assertTrue(outcome.text_matches)
        self.assertEqual(
            self.evaluate(page, "document.querySelector('input').value"),
            "PARENT_SECRET",
        )
        self.assertEqual(
            self.evaluate(
                page,
                "document.querySelector('iframe').contentDocument.querySelector('input').value",
            ),
            "fixture",
        )
