"""Native HTML form dispatch, one-use authority, outcomes and journal privacy."""

import sqlite3
import unittest
from unittest.mock import patch

import test_page_inspection as inspection
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEngineHttpRequest, QWebEngineSettings

from services.form_submission import SubmissionTicket
from services.page_inspector import PageInspectionError
from services.task_policy import LiveGrant, TaskPermissionError, TaskScope

FORM = """<!doctype html><html><body><form action="/submitted" method="post">
<input aria-label="Message" name="message" value="FORM_SECRET">
<input type="hidden" name="csrf" value="CSRF_SECRET">
<button name="decision" value="yes">Send</button></form></body></html>"""


class PageFormTests(unittest.TestCase):
    setUpClass = classmethod(inspection.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(inspection.PageInspectionTests.tearDownClass.__func__)
    tearDown = inspection.PageInspectionTests.tearDown
    session = inspection.PageInspectionTests.session
    load = inspection.PageInspectionTests.load
    inspect = inspection.PageInspectionTests.inspect
    script = inspection.PageInspectionTests.script
    command = inspection.PageInspectionTests.command
    request = inspection.PageInspectionTests.request
    wait_for = inspection.PageInspectionTests.wait_for

    def setUp(self):
        inspection.PageInspectionTests.setUp(self)
        type(self).requests.clear()
        type(self).posted = []
        type(self).post_responses = {
            "/submitted": (200, None, b"<title>FORM_SECRET</title><p>Received</p>"),
        }

    def opened(self, html=FORM, actions=None):
        task_id, grant_id, session_id, page = self.session(
            actions=actions or ["navigate", "inspect", "read", "submit"]
        )
        self.load(session_id, page, html)
        return task_id, grant_id, session_id, page

    def submit(self, task_id, session_id, snapshot=None, label="Send"):
        snapshot = snapshot or self.inspect(task_id, session_id)
        target = next(t for t in snapshot.frames[0].targets if t.label == label)
        return self.command(
            "page.submit",
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
        )

    def test_post_sends_native_controls_and_submitter_once_with_observed_receipt(self):
        task_id, _, session_id, _ = self.opened()
        snapshot = self.inspect(task_id, session_id)
        self.assertIn("submit", snapshot.frames[0].targets[-1].operations)
        self.assertEqual(
            snapshot.frames[0].targets[-1].form_action, self.origin + "/submitted"
        )
        self.assertEqual(snapshot.frames[0].targets[-1].form_method, "POST")
        outcome = self.submit(task_id, session_id, snapshot)
        self.assertEqual(outcome.status, "loaded")
        self.assertTrue(outcome.request_authorized)
        self.assertEqual(outcome.site_effects, "unknown")
        self.assertGreater(outcome.revision, snapshot.revision)
        self.assertEqual(
            self.posted[0][:2],
            ("/submitted", b"message=FORM_SECRET&csrf=CSRF_SECRET&decision=yes"),
        )
        self.assertEqual(len(self.posted), 1)
        journal = repr(self.service.store.events(task_id))
        for secret in ("FORM_SECRET", "CSRF_SECRET", "decision=yes"):
            self.assertNotIn(secret, journal)
            self.assertNotIn(secret, repr(outcome))
        with self.assertRaises(PageInspectionError):
            self.submit(task_id, session_id, snapshot)

    def test_get_submission_replaces_query_without_journaling_values(self):
        task_id, _, session_id, _ = self.opened(
            FORM.replace('method="post"', 'method="get"').replace(
                "/submitted", "/search?discard=OLD_SECRET#section"
            )
        )
        outcome = self.submit(task_id, session_id)
        self.assertEqual(outcome.status, "loaded")
        self.assertTrue(outcome.request_authorized)
        self.assertTrue(
            any(
                path.startswith(
                    "/search?message=FORM_SECRET&csrf=CSRF_SECRET&decision=yes"
                )
                for path, _ in self.requests
            )
        )
        journal = repr(self.service.store.events(task_id))
        for secret in ("FORM_SECRET", "CSRF_SECRET", "OLD_SECRET"):
            self.assertNotIn(secret, journal)
            self.assertNotIn(secret, repr(outcome))

    def test_native_validation_prevents_request_and_novalidate_override_works(self):
        html = FORM.replace('value="FORM_SECRET"', 'required value=""')
        task_id, _, session_id, page = self.opened(html)
        outcome = self.submit(task_id, session_id)
        self.assertEqual(outcome.status, "validation_failed")
        self.assertFalse(outcome.request_authorized)
        self.assertEqual(self.posted, [])
        self.script(page, "document.querySelector('button').formNoValidate=true;true")
        self.assertEqual(self.submit(task_id, session_id).status, "loaded")

    def test_submitter_overrides_are_used_and_out_of_scope_action_is_blocked(self):
        html = FORM.replace(
            "<button ", '<button formaction="/override" formmethod="get" '
        )
        task_id, _, session_id, page = self.opened(html)
        self.assertEqual(self.submit(task_id, session_id).status, "loaded")
        self.assertTrue(any(path.startswith("/override?") for path, _ in self.requests))
        self.load(
            session_id,
            page,
            FORM.replace(
                "/submitted", f"http://localhost:{self.server.server_port}/forbidden"
            ),
        )
        with self.assertRaises(TaskPermissionError):
            self.submit(task_id, session_id)
        self.assertEqual(self.posted, [])

    def test_old_grants_and_missing_read_or_navigation_cannot_dispatch(self):
        task_id, _, session_id, _ = self.opened(actions=["navigate", "read", "edit"])
        snapshot = self.inspect(task_id, session_id)
        with patch.object(self.service.backend, "submit_form") as dispatch:
            with self.assertRaises(TaskPermissionError):
                self.submit(task_id, session_id, snapshot)
        dispatch.assert_not_called()
        for actions in (["submit"], ["read", "submit"]):
            actor, _, owned, _ = self.session(actions=actions)
            with self.assertRaises(TaskPermissionError):
                self.command(
                    "page.submit",
                    task_id=actor,
                    session_id=owned,
                    snapshot_id="unknown",
                    target_id="unknown",
                )

    def test_form_references_go_stale_after_action_or_document_mutation(self):
        task_id, _, session_id, page = self.opened()
        snapshot = self.inspect(task_id, session_id)
        self.script(page, "document.querySelector('form').action='/changed';true")
        with self.assertRaises(PageInspectionError):
            self.submit(task_id, session_id, snapshot)
        self.assertEqual(self.posted, [])

    def test_sensitive_multipart_popup_and_child_frame_forms_are_unavailable(self):
        for html in (
            FORM.replace('name="message"', 'type="password" name="message"'),
            FORM.replace('name="message"', 'type="file" name="message"'),
            FORM.replace(
                'method="post"', 'method="post" enctype="multipart/form-data"'
            ),
            FORM.replace('method="post"', 'method="post" target="_blank"'),
            FORM.replace("<button ", '<button formtarget="_blank" '),
            FORM.replace('method="post"', 'method="dialog"'),
            '<iframe srcdoc="' + FORM.replace('"', "&quot;") + '"></iframe>',
        ):
            with self.subTest(html=html):
                task_id, _, session_id, _ = self.opened(html)
                snapshot = self.inspect(task_id, session_id)
                target = next(
                    t for f in snapshot.frames for t in f.targets if t.label == "Send"
                )
                self.assertNotIn("submit", target.operations)
                with self.assertRaises(PageInspectionError):
                    self.command(
                        "page.submit",
                        task_id=task_id,
                        session_id=session_id,
                        snapshot_id=snapshot.snapshot_id,
                        target_id=target.target_id,
                    )
        self.assertEqual(self.posted, [])

    def test_post_redirects_are_reported_without_being_followed(self):
        # Qt's interceptor cannot reliably stop a 307/308 body replay, and the
        # navigation gate cannot tell those apart from 303, so none are followed.
        for status in (303, 307, 308):
            with self.subTest(status=status):
                self.post_responses["/submitted"] = (
                    status,
                    self.origin + "/after",
                    b"",
                )
                task_id, _, session_id, _ = self.opened()
                outcome = self.submit(task_id, session_id)
                self.assertEqual(outcome.status, "redirect_not_followed")
                self.assertTrue(outcome.request_authorized)
                events = self.service.store.events(task_id)
                self.assertIn("submission_redirect_not_followed", repr(events))
        self.assertFalse(any(path.endswith("/after") for path, _ in self.requests))

    def test_unsolicited_native_forms_and_direct_post_remain_blocked(self):
        task_id, _, session_id, page = self.opened()
        self.script(page, "document.querySelector('form').requestSubmit();true")
        self.wait_for(
            lambda: self.service.backend.inspect(session_id).load_success is False
        )
        self.assertEqual(self.posted, [])
        request = QWebEngineHttpRequest(QUrl(self.origin + "/submitted"))
        request.setMethod(QWebEngineHttpRequest.Method.Post)
        request.setPostData(b"not-approved")
        finished = []
        page.loadFinished.connect(finished.append)
        page.load(request)
        self.wait_for(lambda: bool(finished))
        self.assertFalse(finished[-1])
        self.assertEqual(self.posted, [])

    def test_request_journal_failure_prevents_dispatch(self):
        task_id, _, session_id, _ = self.opened()
        snapshot = self.inspect(task_id, session_id)
        original = self.service.store.record_event

        def record(actor, kind, payload):
            if kind == "page.action_requested":
                raise sqlite3.OperationalError("fixture")
            return original(actor, kind, payload)

        with (
            patch.object(self.service.store, "record_event", side_effect=record),
            patch.object(self.service.backend, "submit_form") as dispatch,
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.submit(task_id, session_id, snapshot)
        dispatch.assert_not_called()
        self.assertEqual(self.posted, [])
        self.assertTrue(self.service.failure)

    def test_receipt_journal_failure_freezes_authority_after_possible_effect(self):
        task_id, _, session_id, _ = self.opened()
        snapshot = self.inspect(task_id, session_id)
        original = self.service.store.record_event

        def record(actor, kind, payload):
            if kind == "page.action_finished":
                raise sqlite3.OperationalError("fixture")
            return original(actor, kind, payload)

        with patch.object(self.service.store, "record_event", side_effect=record):
            with self.assertRaises(sqlite3.OperationalError):
                self.submit(task_id, session_id, snapshot)
        self.assertEqual(len(self.posted), 1)
        self.assertTrue(self.service.failure)

    def test_stop_and_revocation_interrupt_receipts_without_replaying(self):
        for revoke in (False, True):
            with self.subTest(revoke=revoke):
                self.post_responses["/submitted-slow"] = (200, None, b"<p>Done</p>")
                task_id, grant_id, session_id, _ = self.opened(
                    FORM.replace("/submitted", "/submitted-slow")
                )
                snapshot = self.inspect(task_id, session_id)
                target = snapshot.frames[0].targets[-1]
                source = self.service.broker.submit(
                    "page.submit",
                    task_id=task_id,
                    session_id=session_id,
                    snapshot_id=snapshot.snapshot_id,
                    target_id=target.target_id,
                )
                before = len(self.posted)
                self.wait_for(lambda before=before: len(self.posted) > before)
                if revoke:
                    self.service.revoke_grant(task_id, grant_id)
                else:
                    self.command("session.stop", task_id=task_id, session_id=session_id)
                self.wait_for(source.done)
                with self.assertRaises(PageInspectionError):
                    source.result()
                self.assertEqual(len(self.posted), before + 1)

    def test_timeout_returns_uncertainty_and_clears_dispatch_ticket(self):
        task_id, _, session_id, page = self.opened()
        self.service.backend.navigation_timeout_ms = 30
        # Model an acknowledged dispatch for which the engine produces no load.
        with patch.object(
            self.service.backend.inspector,
            "dispatch_submission",
            side_effect=lambda page, request, expected, received: received(
                '{"status":"submitted"}'
            ),
        ):
            outcome = self.submit(task_id, session_id)
        self.assertEqual(outcome.status, "timed_out")
        self.assertFalse(outcome.request_authorized)
        self.assertIsNone(page.submission.destination)
        self.assertEqual(self.posted, [])

    def test_last_moment_form_mutation_and_callback_expiry_cannot_dispatch(self):
        task_id, _, session_id, page = self.opened()
        original = self.service.backend.inspector.dispatch_submission

        def changed(page, request, expected, received):
            page.runJavaScript(
                "document.querySelector('form').action='/changed';true",
                1,
                lambda _: original(page, request, expected, received),
            )

        with patch.object(
            self.service.backend.inspector, "dispatch_submission", side_effect=changed
        ):
            with self.assertRaises(PageInspectionError):
                self.submit(task_id, session_id)
        self.assertEqual(self.posted, [])
        self.assertIsNone(page.submission.destination)
        now = [100.0]
        grant = self.service._grants[task_id][1]
        grant._clock = lambda: now[0]
        grant.deadline = 101.0
        original_prepare = self.service.backend.inspector.prepare_submission

        def expired(*args):
            source = original_prepare(*args)
            source.add_done_callback(lambda _: now.__setitem__(0, 102.0))
            return source

        with patch.object(
            self.service.backend.inspector, "prepare_submission", side_effect=expired
        ):
            with self.assertRaises(TaskPermissionError):
                self.submit(task_id, session_id)
        self.assertEqual(self.posted, [])

    def test_script_enabled_forms_cannot_arm_a_submission_ticket(self):
        task_id, _, session_id, page = self.opened()
        snapshot = self.inspect(task_id, session_id)
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
        )
        with patch.object(
            self.service.backend.inspector, "prepare_submission"
        ) as prepare:
            with self.assertRaises(PageInspectionError) as error:
                self.submit(task_id, session_id, snapshot)
        self.assertEqual(error.exception.code, "scripted_submission_unavailable")
        prepare.assert_not_called()
        self.assertIsNone(page.submission.destination)
        self.assertEqual(self.posted, [])

    def test_scripts_enabled_during_preflight_cannot_dispatch(self):
        task_id, _, session_id, page = self.opened()
        original = self.service.backend.inspector.prepare_submission

        def enable_scripts(*args):
            source = original(*args)
            # Register before backend's dispatch callback, simulating a future
            # settings change between preparation and ticket authorization.
            source.add_done_callback(
                lambda _: page.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.JavascriptEnabled, True
                )
            )
            return source

        with (
            patch.object(
                self.service.backend.inspector,
                "prepare_submission",
                side_effect=enable_scripts,
            ),
            patch.object(
                self.service.backend.inspector, "dispatch_submission"
            ) as dispatch,
        ):
            with self.assertRaises(PageInspectionError) as error:
                self.submit(task_id, session_id)
        self.assertEqual(error.exception.code, "scripted_submission_unavailable")
        dispatch.assert_not_called()
        self.assertIsNone(page.submission.destination)
        self.assertIsNone(self.service.backend._sessions[session_id].pending_action)
        self.assertEqual(self.posted, [])

    def test_named_controls_do_not_shadow_native_form_properties(self):
        html = FORM.replace(
            "<button ",
            '<input name="action" value="SHADOW_SECRET"><input name="method"><input name="elements"><input name="checkValidity"><button ',
        )
        task_id, _, session_id, _ = self.opened(html)
        self.assertEqual(self.submit(task_id, session_id).status, "loaded")
        self.assertNotIn("SHADOW_SECRET", repr(self.service.store.events(task_id)))

    def test_cross_task_reference_and_redirect_destination_are_rejected(self):
        task_id, _, session_id, _ = self.opened()
        snapshot = self.inspect(task_id, session_id)
        other_id, _, other_session, _ = self.opened()
        target = snapshot.frames[0].targets[-1]
        with self.assertRaises(TaskPermissionError):
            self.command(
                "page.submit",
                task_id=other_id,
                session_id=session_id,
                snapshot_id=snapshot.snapshot_id,
                target_id=target.target_id,
            )
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.submit",
                task_id=other_id,
                session_id=other_session,
                snapshot_id=snapshot.snapshot_id,
                target_id=target.target_id,
            )
        self.post_responses["/submitted"] = (
            303,
            f"http://localhost:{self.server.server_port}/forbidden",
            b"",
        )
        self.assertEqual(
            self.submit(task_id, session_id, snapshot).status, "load_failed"
        )
        self.assertFalse(any(path == "/forbidden" for path, _ in self.requests))


class SubmissionTicketTests(unittest.TestCase):
    def ticket(self, **limits):
        grant = LiveGrant(
            TaskScope.parse(
                {
                    "origins": ["https://example.org"],
                    "allow_private_network": True,
                    "actions": ["navigate", "read", "submit"],
                    **limits,
                }
            )
        )
        ticket = SubmissionTicket(grant)
        ticket.arm("https://example.org/send?token=fixture", "POST")
        return ticket

    def test_ticket_is_exact_single_use_and_rejects_subresources(self):
        ticket = self.ticket()
        for url, method, main in (
            ("https://example.org/other?token=fixture", b"POST", True),
            ("https://example.org/send?token=other", b"POST", True),
            ("https://example.org/send?token=fixture", b"GET", True),
            ("https://example.org/send?token=fixture", b"POST", False),
        ):
            self.assertEqual(
                ticket.request(url, method, main), "submission_not_authorized"
            )
        self.assertEqual(
            ticket.request("https://example.org/send?token=fixture", b"POST", True), ""
        )
        self.assertEqual(
            ticket.request("https://example.org/send?token=fixture", b"POST", True),
            "submission_not_authorized",
        )

    def test_revocation_expiry_and_request_budget_still_apply(self):
        ticket = self.ticket()
        ticket.grant.end("revoked")
        self.assertEqual(
            ticket.request("https://example.org/send?token=fixture", b"POST", True),
            "revoked",
        )
        ticket = self.ticket(max_requests=1)
        ticket.grant.request("https://example.org/")
        self.assertEqual(
            ticket.request("https://example.org/send?token=fixture", b"POST", True),
            "request_budget_exhausted",
        )
        ticket = self.ticket()
        ticket.deadline = 0
        self.assertEqual(
            ticket.request("https://example.org/send?token=fixture", b"POST", True),
            "submission_not_authorized",
        )
        ticket = self.ticket()
        ticket.clear()
        self.assertEqual(
            ticket.navigation("https://example.org/send?token=fixture", True),
            "submission_not_authorized",
        )
