"""Real-engine watch/handoff, scoped user actions and independent lifetime."""

import json
import sqlite3
import unittest
from concurrent.futures import Future
from unittest.mock import patch

import test_page_inspection as fixtures
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QKeyEvent

from services.page_inspector import INSPECTION_WORLD, PageInspectionError
from services.task_policy import TaskPermissionError
from ui.task_dialog import TaskDialog

HTML = """<html><title>Watch fixture</title><body style="height:3000px">
<a href="/next">Next page</a><input id="field" aria-label="Message" value="OLD_SECRET">
<input type="checkbox" aria-label="Confirm"><input type="password" aria-label="Password">
<form action="/submitted" method="post"><button>Send</button></form>
</body></html>"""


class TaskWatchTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.PageInspectionTests.tearDownClass.__func__)
    setUp = fixtures.PageInspectionTests.setUp
    tearDown = fixtures.PageInspectionTests.tearDown
    session = fixtures.PageInspectionTests.session
    command = fixtures.PageInspectionTests.command
    request = fixtures.PageInspectionTests.request
    load = fixtures.PageInspectionTests.load
    inspect = fixtures.PageInspectionTests.inspect
    wait_for = fixtures.PageInspectionTests.wait_for

    def opened(self, actions=None):
        task, grant, session, page = self.session(
            actions=actions
            or ["navigate", "inspect", "read", "attach", "edit", "scroll", "submit"]
        )
        self.load(session, page, HTML)
        self.command("session.watch", task_id=task, session_id=session)
        return task, grant, session, page, self.service._watchers[session]

    def user(self, window, command, **arguments):
        return self.command(
            "user.command",
            task_id=window.task_id,
            session_id=window.session_id,
            key=window.control_key,
            operation_name=command,
            arguments=arguments,
        )

    def takeover(self, window):
        self.service.take_control(window.task_id, window.session_id, window.control_key)

    def value(self, page):
        replies = []
        page.runJavaScript(
            "document.getElementById('field').value", INSPECTION_WORLD, replies.append
        )
        self.wait_for(lambda: bool(replies))
        return replies[0]

    def test_watch_requires_attach_and_reuses_the_same_page_and_window(self):
        task, _, session, page = self.session(actions=["navigate", "inspect", "read"])
        with self.assertRaises(TaskPermissionError):
            self.command("session.watch", task_id=task, session_id=session)
        self.assertFalse(self.service._watchers)
        self.assertFalse(self.service.backend._sessions[session].views)
        task, _, session, page, window = self.opened()
        self.assertIs(window.view.page(), page)
        self.assertIs(
            window.view.page().profile(),
            self.service.backend._sessions[session].profile,
        )
        self.command("session.watch", task_id=task, session_id=session)
        self.assertIs(self.service._watchers[session], window)

    def test_watch_blocks_direct_input_but_agent_editing_still_works(self):
        task, _, session, page, window = self.opened()
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a"
        )
        QCoreApplication.sendEvent(window.view, event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(self.value(page), "OLD_SECRET")
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Message")
        self.command(
            "page.act",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            operation="enter",
            text="AGENT_TEXT",
        )
        self.assertEqual(self.value(page), "AGENT_TEXT")

    def test_takeover_denies_queued_agent_work_and_all_page_operations(self):
        task, _, session, _, window = self.opened()
        queued = self.service.client(task).submit(
            "session.navigate", session_id=session, url=self.origin + "/not-requested"
        )
        self.takeover(window)
        self.wait_for(queued.done)
        self.assertEqual(queued.exception().code, "user_control_active")
        for command, arguments in (
            ("page.inspect", {}),
            ("session.navigate", {"url": self.origin}),
            ("session.stop", {}),
            ("page.wait", {}),
            (
                "page.act",
                {"snapshot_id": "old", "target_id": "old", "operation": "activate"},
            ),
        ):
            with (
                self.subTest(command=command),
                self.assertRaises(TaskPermissionError) as denied,
            ):
                self.command(command, task_id=task, session_id=session, **arguments)
            self.assertEqual(denied.exception.code, "user_control_active")
        state = self.command("session.inspect", task_id=task, session_id=session)
        self.assertEqual(state.control_owner, "user")
        self.assertFalse(any(path == "/not-requested" for path, _ in self.requests))

    def test_user_edits_returned_page_keeps_identity_and_old_refs_are_stale(self):
        task, _, session, page, window = self.opened()
        old = self.inspect(task, session)
        self.takeover(window)
        snapshot = self.user(window, "page.inspect")
        field = next(t for t in snapshot.frames[0].targets if t.label == "Message")
        self.user(
            window,
            "page.act",
            snapshot_id=snapshot.snapshot_id,
            target_id=field.target_id,
            operation="enter",
            text="USER_SECRET",
        )
        self.assertEqual(self.value(page), "USER_SECRET")
        self.service.return_control(task, session, window.control_key)
        self.assertIs(self.service.backend._sessions[session].page, page)
        self.assertEqual(
            self.command(
                "session.inspect", task_id=task, session_id=session
            ).control_owner,
            "agent",
        )
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.target",
                task_id=task,
                session_id=session,
                snapshot_id=old.snapshot_id,
                target_id=old.frames[0].targets[0].target_id,
            )
        self.assertNotEqual(self.inspect(task, session).snapshot_id, old.snapshot_id)
        events = self.service.store.events(task)
        self.assertNotIn("USER_SECRET", json.dumps([event.payload for event in events]))
        self.assertTrue(
            {"session.control_taken", "session.control_returned"}
            <= {event.kind for event in events}
        )

    def test_user_observed_link_navigation_receipt_survives_async_actor_context(self):
        _, _, session, page, window = self.opened()
        self.takeover(window)
        snapshot = self.user(window, "page.inspect")
        target = next(t for t in snapshot.frames[0].targets if t.label == "Next page")
        outcome = self.user(
            window,
            "page.navigate",
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
        )
        self.assertEqual(outcome.status, "loaded")
        self.assertTrue(page.url().toString().endswith("/next"))
        self.assertTrue(window.owns_control())

    def test_user_controls_do_not_expand_grants_or_allow_foreign_identity(self):
        _, _, _, _, window = self.opened(
            actions=["navigate", "inspect", "read", "attach"]
        )
        self.takeover(window)
        snapshot = self.user(window, "page.inspect")
        target = next(t for t in snapshot.frames[0].targets if t.label == "Message")
        with self.assertRaises(TaskPermissionError):
            self.user(
                window,
                "page.act",
                snapshot_id=snapshot.snapshot_id,
                target_id=target.target_id,
                operation="enter",
                text="NOT_ALLOWED",
            )
        with self.assertRaises(ValueError):
            self.user(window, "page.inspect", task_id="foreign")
        with self.assertRaises(TaskPermissionError):
            self.command(
                "user.command",
                task_id=window.task_id,
                session_id=window.session_id,
                key="foreign",
                operation_name="page.inspect",
                arguments={},
            )
        denied = self.service.client(window.task_id).submit(
            "user.command",
            session_id=window.session_id,
            key=window.control_key,
            operation_name="page.inspect",
            arguments={},
        )
        self.wait_for(denied.done)
        self.assertIsInstance(denied.exception(), TaskPermissionError)

    def test_pending_mutation_prevents_handoff_without_false_evidence(self):
        task, _, session, _, window = self.opened()
        native = self.service.backend._sessions[session]
        native.pending_action = Future()
        try:
            with self.assertRaises(PageInspectionError) as busy:
                self.takeover(window)
            self.assertEqual(busy.exception.code, "session_busy")
            self.assertFalse(self.service._controls)
            self.assertNotIn(
                "session.control_taken",
                [e.kind for e in self.service.store.events(task)],
            )
        finally:
            native.pending_action = None

    def test_closing_idle_user_watch_returns_control_and_keeps_page_alive(self):
        task, _, session, page, window = self.opened()
        self.takeover(window)
        window.close()
        self.assertNotIn(session, self.service._controls)
        self.assertNotIn(session, self.service._watchers)
        self.assertFalse(sip.isdeleted(page))
        self.assertEqual(
            self.command(
                "session.inspect", task_id=task, session_id=session
            ).control_owner,
            "agent",
        )

    def test_escape_and_direct_widget_destruction_return_control(self):
        for operation in ("reject", "delete"):
            with self.subTest(operation=operation):
                task, _, session, page, window = self.opened()
                self.takeover(window)
                if operation == "reject":
                    window.reject()
                else:
                    sip.delete(window)
                self.assertNotIn(session, self.service._controls)
                self.assertNotIn(session, self.service._watchers)
                self.assertFalse(sip.isdeleted(page))

    def test_delayed_old_window_destruction_preserves_reopened_watch(self):
        task, _, session, page, old_window = self.opened()
        self.takeover(old_window)
        old_window.close()
        # Reopening before Qt delivers DeferredDelete must not let the old
        # window's destroyed callback detach the new window or its controller.
        self.service._watch(task, session)
        window = self.service._watchers[session]
        self.assertIsNot(window, old_window)
        self.takeover(window)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(old_window))
        self.assertIs(self.service._watchers[session], window)
        self.assertEqual(self.service._controls[session], window.control_key)
        self.assertIs(window.view.page(), page)
        self.user(window, "page.inspect")

    def test_queued_user_command_with_old_key_is_denied_after_new_handoff(self):
        task, _, session, _, window = self.opened()
        old_key = window.control_key
        self.takeover(window)
        queued = self.service.broker.submit(
            "user.command",
            task_id=task,
            session_id=session,
            key=old_key,
            operation_name="page.inspect",
            arguments={},
        )
        self.service.return_control(task, session, old_key)
        window.control_key = "new-user-ownership"
        self.takeover(window)
        self.wait_for(queued.done)
        self.assertEqual(queued.exception().code, "control_not_owned")

    def test_inflight_agent_read_cannot_return_content_after_user_takes_control(self):
        task, _, session, _, window = self.opened()
        snapshot = self.inspect(task, session)
        source = Future()
        with patch.object(
            self.service.backend, "inspect_page", return_value=source
        ) as inspected:
            result = self.service.client(task).submit(
                "page.inspect", session_id=session
            )
            self.wait_for(lambda: inspected.called)
            self.takeover(window)
            source.set_result(snapshot)
            self.wait_for(result.done)
        self.assertEqual(result.exception().code, "user_control_active")

    def test_closing_during_user_mutation_cancels_task_instead_of_resuming_agent(self):
        task, _, session, page, window = self.opened()
        self.takeover(window)
        self.service.backend._sessions[session].pending_action = Future()
        window.close()
        self.assertEqual(self.service.store.get(task).status, "cancelled")
        self.assertTrue(sip.isdeleted(page))
        self.assertFalse(self.service._controls)

    def test_revocation_closes_watch_and_page_even_during_user_control(self):
        task, grant, session, page, window = self.opened()
        self.takeover(window)
        self.service.revoke_grant(task, grant)
        self.assertTrue(window._ended)
        self.assertTrue(sip.isdeleted(page))
        self.assertNotIn(session, self.service._watchers)
        self.assertFalse(self.service._controls)

    def test_control_journal_failure_freezes_authority_before_transition(self):
        task, _, session, _, window = self.opened()
        with patch.object(
            self.service.store,
            "record_event",
            side_effect=sqlite3.OperationalError("synthetic"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.takeover(window)
        self.assertTrue(self.service.failure)
        self.assertFalse(self.service._controls)

    def test_permissions_dialog_offers_existing_sessions_for_watch(self):
        task, _, session, _, window = self.opened()
        dialog = TaskDialog(self.service)
        try:
            row = next(
                i
                for i, (record, _) in enumerate(dialog._rows)
                if record.task_id == task
            )
            dialog.table.selectRow(row)
            self.assertEqual(dialog.sessions.currentData(), session)
            self.assertTrue(dialog.watch_button.isEnabled())
            dialog.watch_button.click()
            self.assertIs(self.service._watchers[session], window)
        finally:
            dialog.close()
            sip.delete(dialog)
