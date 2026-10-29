"""Read-only inspection, authorization, and stale references in real Chromium."""

import json
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

from services.agent_tools import RESPONSE_LIMIT
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

    def session(self, *, read=True, origins=None, actions=None):
        task = self.command("task.create", objective="Inspect synthetic content")
        if actions is None:
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

    def follow(self, task_id, session_id, snapshot, target):
        return self.command(
            "page.navigate",
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
        )

    def test_form_help_errors_and_required_markers_preserve_values_and_authority(self):
        task, _, session, page = self.session()
        self.load(
            session,
            page,
            """<label for="email">Email</label>
            <input id="email" type="email" required value="VALUE_SECRET"
                aria-describedby="hint" aria-description="Fallback hint"
                aria-invalid="true" aria-errormessage="error">
            <p id="hint">Use your school email.</p><p id="error">Check the domain.</p>
            <textarea aria-label="Essay" aria-required="true">ESSAY_SECRET</textarea>
            <select aria-label="Program" required><option>OPTION_SECRET</option></select>
            <input type="checkbox" aria-label="Confirm" required>
            <button aria-label="Continue" required title="Review your details">Go</button>""",
        )
        snapshot = self.inspect(task, session)
        targets = {target.label: target for target in snapshot.frames[0].targets}
        email = targets["Email"]
        self.assertEqual(email.description, "Use your school email.")
        self.assertEqual(email.error_message, "Check the domain.")
        self.assertTrue(email.required)
        self.assertTrue(email.invalid)
        self.assertEqual(email.operations, ("enter",))
        for name in ("Essay", "Program", "Confirm"):
            self.assertTrue(targets[name].required)
            self.assertFalse(targets[name].invalid)
        self.assertFalse(targets["Continue"].required)
        self.assertEqual(targets["Continue"].description, "Review your details")
        self.assertNotIn("SECRET", str(asdict(snapshot)))
        self.assertIn("accessibility_tree_not_inspected", snapshot.limitations)
        self.assertIn("hidden_accessible_references_omitted", snapshot.limitations)

    def test_outline_reports_structure_in_order_without_values(self):
        task, _, session, page = self.session()
        self.load(
            session,
            page,
            """<header><h1>Store</h1></header>
            <nav aria-label="Primary"><ul><li><a href="/a">A</a></li>
              <li><a href="/b" aria-current="page">B</a></li></ul></nav>
            <main>
              <h2>Orders</h2>
              <div role="tablist" aria-label="Views">
                <span role="tab" aria-selected="true">Open</span>
                <span role="tab" aria-selected="false">Closed</span></div>
              <table><caption>Recent orders</caption>
                <tr><th>Order</th><th>Total</th></tr>
                <tr><td>1</td><td>$5</td></tr><tr><td>2</td><td>$9</td></tr></table>
              <img src="data:," alt="Delivery map"><img src="data:,">
              <ol><li>Pick</li><li>Pack</li><li>Ship</li></ol>
              <details open><summary>Shipping rules</summary>Within 3 days</details>
              <button aria-expanded="false" aria-label="More">…</button>
              <div role="alert">Payment failed</div>
              <section><h3 aria-level="3">Unnamed section</h3></section>
              <section aria-label="Help"><input aria-label="Note" value="VALUE_SECRET"></section>
              <div role="heading" aria-level="4">Custom heading</div>
              <h5 hidden>Hidden heading</h5>
            </main>
            <footer>© Store</footer>""",
        )
        snapshot = self.inspect(task, session)
        outline = snapshot.frames[0].outline
        summary = [
            (item.kind, item.role or item.level or "", item.text) for item in outline
        ]
        self.assertEqual(
            summary[:4],
            [
                ("landmark", "banner", ""),
                ("heading", 1, "Store"),
                ("landmark", "navigation", "Primary"),
                ("list", "", ""),
            ],
        )
        kinds = {item.kind: item for item in outline}
        self.assertEqual(kinds["table"].text, "Recent orders")
        self.assertEqual(kinds["table"].columns, ("Order", "Total"))
        self.assertEqual(kinds["table"].count, 3)
        self.assertEqual(kinds["image"].text, "Delivery map")
        self.assertIn("unlabelled_images_present", snapshot.limitations)
        lists = [item for item in outline if item.kind == "list"]
        self.assertEqual([(i.ordered, i.count) for i in lists], [(False, 2), (True, 3)])
        self.assertEqual(kinds["disclosure"].text, "Shipping rules")
        self.assertIs(kinds["disclosure"].expanded, True)
        self.assertEqual(kinds["alert"].text, "Payment failed")
        self.assertEqual(kinds["tablist"].text, "Views")
        headings = [(i.level, i.text) for i in outline if i.kind == "heading"]
        self.assertIn((4, "Custom heading"), headings)
        self.assertNotIn("Hidden heading", str(headings))
        regions = [
            i.text for i in outline if i.kind == "landmark" and i.role == "region"
        ]
        self.assertEqual(regions, ["Help"])
        self.assertIn(("landmark", "contentinfo", ""), summary)
        targets = {t.label: t for t in snapshot.frames[0].targets}
        self.assertEqual(targets["More"].states, ("collapsed",))
        self.assertEqual(targets["B"].states, ("current",))
        self.assertEqual(targets["Open"].states, ("selected",))
        self.assertIn("custom_control_unsupported", targets["Open"].constraints)
        self.assertNotIn("SECRET", str(asdict(snapshot)))
        self.assertNotIn("text_truncated", snapshot.limitations)

    def test_description_fallbacks_and_invalid_tokens_do_not_run_validation(self):
        task, _, session, page = self.session()
        self.load(
            session,
            page,
            """<input aria-label="Missing" aria-describedby="absent" aria-description="Direct help">
            <input aria-label="Title" title="Title help">
            <input title="Same name">
            <input aria-label="Unvalidated" required aria-invalid="false" aria-errormessage="error">
            <input aria-label="Grammar" aria-invalid="grammar" aria-errormessage="error">
            <input aria-label="Unknown token" aria-invalid="unexpected" aria-errormessage="error">
            <p id="error">Author error</p>""",
        )
        snapshot = self.inspect(task, session)
        targets = {target.label: target for target in snapshot.frames[0].targets}
        self.assertEqual(targets["Missing"].description, "Direct help")
        self.assertEqual(targets["Title"].description, "Title help")
        self.assertEqual(targets["Same name"].description, "")
        self.assertFalse(targets["Unvalidated"].invalid)
        self.assertEqual(targets["Unvalidated"].error_message, "")
        for name in ("Grammar", "Unknown token"):
            self.assertTrue(targets[name].invalid)
            self.assertEqual(targets[name].error_message, "Author error")

    def test_referenced_help_and_labels_exclude_hidden_and_editable_ancestors(self):
        task, _, session, page = self.session()
        self.load(
            session,
            page,
            """<input aria-label="Safe field" aria-labelledby="hidden_label"
                aria-describedby="hidden css aria editable value area select safe"
                aria-invalid="true" aria-errormessage="hidden">
            <div hidden><span id="hidden">HIDDEN_SECRET</span>
                <span id="hidden_label">LABEL_SECRET</span></div>
            <div style="display:none"><span id="css">CSS_SECRET</span></div>
            <div aria-hidden="true"><span id="aria">ARIA_SECRET</span></div>
            <div contenteditable><span id="editable">EDITABLE_SECRET</span></div>
            <input id="value" value="VALUE_SECRET">
            <textarea id="area">AREA_SECRET</textarea>
            <select id="select"><option>SELECT_SECRET</option></select>
            <p id="safe">Visible help <span hidden>INLINE_SECRET</span></p>""",
        )
        snapshot = self.inspect(task, session)
        field = next(t for t in snapshot.frames[0].targets if t.label == "Safe field")
        self.assertEqual(field.description, "Visible help")
        self.assertEqual(field.error_message, "")
        self.assertNotIn("SECRET", str(asdict(snapshot)))

    def test_shadow_help_uses_own_root_and_remains_observation_only(self):
        task, _, session, page = self.session()
        self.load(session, page, '<p id="hint">Outer hint</p><div id="host"></div>')
        self.script(
            page,
            """const root = document.querySelector('#host').attachShadow({mode:'open'});
            root.innerHTML='<p id="hint">Component hint</p><p id="error">Component error</p>' +
                '<input aria-label="Component field" aria-describedby="hint" required ' +
                'aria-invalid="true" aria-errormessage="error" value="SHADOW_SECRET">'; true;""",
        )
        snapshot = self.inspect(task, session)
        field = next(
            t for t in snapshot.frames[0].targets if t.label == "Component field"
        )
        self.assertEqual(field.description, "Component hint")
        self.assertEqual(field.error_message, "Component error")
        self.assertTrue(field.required)
        self.assertEqual(field.operations, ())
        self.assertNotIn("SECRET", str(asdict(snapshot)))

    def test_form_metadata_is_bounded_without_removing_snapshot_limit_signals(self):
        task, _, session, page = self.session()
        self.load(
            session,
            page,
            '<input aria-label="Bounded" aria-describedby="one" aria-invalid="true" '
            'aria-errormessage="one"><p id="one">'
            + "a" * 5000
            + "</p>"
            + '<input aria-label="References" aria-describedby="'
            + " ".join(f"r{i}" for i in range(9))
            + '">'
            + "".join(f'<p id="r{i}">Hint{i}</p>' for i in range(9)),
        )
        snapshot = self.inspect(task, session)
        targets = {target.label: target for target in snapshot.frames[0].targets}
        self.assertEqual(targets["Bounded"].description, "a" * 256)
        self.assertEqual(targets["Bounded"].error_message, "a" * 256)
        self.assertIn("Hint7", targets["References"].description)
        self.assertNotIn("Hint8", targets["References"].description)
        self.assertIn("text_truncated", snapshot.limitations)

    def test_large_unicode_targets_fit_the_mcp_response_with_explicit_truncation(self):
        task, _, session, page = self.session()
        destination = self.origin + "/" + "a" * (2048 - len(self.origin) - 1)
        hint = "語" * 256
        self.load(
            session,
            page,
            "<p>"
            + "語" * 32000
            + '</p><p id="error">'
            + hint
            + "</p>"
            + 200
            * (
                f'<a href="{destination}" role="{hint[:64]}" '
                f'aria-label="{hint}" aria-description="{hint}" '
                'aria-invalid="true" aria-errormessage="error">Link</a>'
            ),
        )
        snapshot = self.inspect(task, session)
        targets = snapshot.frames[0].targets
        self.assertEqual(len(targets), 200)
        observed = sum(
            len(t.label) + len(t.description) + len(t.error_message) for t in targets
        )
        self.assertLessEqual(observed, 16000)
        self.assertIn("target_text_truncated", snapshot.limitations)
        packet = (
            json.dumps({"id": 1, "result": asdict(snapshot)}, allow_nan=False).encode()
            + b"\n"
        )
        self.assertLess(len(packet), RESPONSE_LIMIT - 4096)
        self.assertTrue(targets[0].description)
        self.assertTrue(targets[0].error_message)

    def test_form_metadata_still_requires_read_permission(self):
        task, _, session, page = self.session(read=False)
        self.load(
            session,
            page,
            '<input aria-label="Protected" aria-description="Help" required>',
        )
        with self.assertRaises(TaskPermissionError):
            self.inspect(task, session)

    def test_open_shadow_reads_nested_content_names_and_omits_private_values(self):
        task, _, session, page = self.session()
        self.load(
            session,
            page,
            '<div id="host"></div><button><img alt="Save report"></button>',
        )
        self.script(
            page,
            """
            const root = document.querySelector('#host').attachShadow({mode:'open'});
            root.innerHTML = '<h2>Component report</h2><span id="name">Component name</span>' +
                '<input aria-labelledby="name" value="SHADOW_VALUE_SECRET">' +
                '<input type="password" value="SHADOW_PASSWORD_SECRET">' +
                '<div contenteditable>SHADOW_EDITABLE_SECRET</div><div id="nested"></div>';
            root.querySelector('#nested').attachShadow({mode:'open'}).innerHTML =
                '<p>Nested report details</p><img alt="Revenue chart">'; true;
        """,
        )
        snapshot = self.inspect(task, session)
        self.assertIn("Component report", snapshot.frames[0].text)
        self.assertIn("Nested report details", snapshot.frames[0].text)
        self.assertIn("Revenue chart", snapshot.frames[0].text)
        self.assertNotIn("SHADOW_VALUE_SECRET", repr(snapshot))
        self.assertNotIn("SHADOW_PASSWORD_SECRET", repr(snapshot))
        self.assertNotIn("SHADOW_EDITABLE_SECRET", repr(snapshot))
        field = next(
            t for t in snapshot.frames[0].targets if t.label == "Component name"
        )
        self.assertEqual(field.operations, ())
        self.assertIn("shadow_control_actions_unavailable", field.constraints)
        self.assertEqual(self.target(task, session, snapshot, field), field)
        self.assertIn("Save report", [t.label for t in snapshot.frames[0].targets])
        self.assertIn("closed_shadow_roots_not_inspected", snapshot.limitations)
        self.assertNotIn("shadow_dom_not_inspected", snapshot.limitations)

    def test_shadow_mutation_invalidates_restricted_snapshot(self):
        task, _, session, page = self.session()
        self.load(session, page, '<div id="host"></div>')
        self.script(
            page,
            """
            const root = document.querySelector('#host').attachShadow({mode:'open'});
            root.innerHTML = '<button>Component action</button><p id="status">Before</p>'; true;
        """,
        )
        snapshot = self.inspect(task, session)
        button = snapshot.frames[0].targets[0]
        self.script(
            page,
            "document.querySelector('#host').shadowRoot.querySelector('#status').textContent='After'; true",
        )
        with self.assertRaises(PageInspectionError):
            self.target(task, session, snapshot, button)
        self.assertIn("After", self.inspect(task, session).frames[0].text)

    def test_shadow_root_budget_and_hidden_closed_content_are_reported(self):
        task, _, session, page = self.session()
        self.load(
            session, page, '<div id="hidden" hidden></div><div id="closed"></div>'
        )
        self.script(
            page,
            """
            document.querySelector('#hidden').attachShadow({mode:'open'}).innerHTML='<p>HIDDEN_SHADOW_SECRET</p>';
            document.querySelector('#closed').attachShadow({mode:'closed'}).innerHTML='<p>CLOSED_SHADOW_SECRET</p>';
            for (let i=0; i<40; i++) {
                const host = document.createElement('div'); document.body.append(host);
                host.attachShadow({mode:'open'}).innerHTML='<p>Public component '+i+'</p><button>Action '+i+'</button>';
            } true;
        """,
        )
        snapshot = self.inspect(task, session)
        self.assertNotIn("HIDDEN_SHADOW_SECRET", repr(snapshot))
        self.assertNotIn("CLOSED_SHADOW_SECRET", repr(snapshot))
        self.assertIn("shadow_root_limit", snapshot.limitations)
        self.assertLessEqual(len(snapshot.frames[0].targets), 32)
        self.assertIn("Public component 0", snapshot.frames[0].text)
        self.assertNotIn("Public component 39", snapshot.frames[0].text)

    def test_shadow_root_depth_limit_stops_nested_component_traversal(self):
        task, _, session, page = self.session()
        self.load(session, page, '<div id="host"></div>')
        self.script(
            page,
            """
            let host = document.querySelector('#host');
            for(let i=0; i<12; i++) {
                const root=host.attachShadow({mode:'open'});
                root.innerHTML='<p>Depth '+i+'</p><div></div>';
                host=root.querySelector('div');
            } true;
        """,
        )
        snapshot = self.inspect(task, session)
        self.assertIn("Depth 7", snapshot.frames[0].text)
        self.assertNotIn("Depth 8", snapshot.frames[0].text)
        self.assertIn("shadow_root_limit", snapshot.limitations)

    def test_target_navigation_reports_a_loaded_document_and_invalidates_old_targets(
        self,
    ):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        snapshot = self.inspect(task_id, session_id)
        target = snapshot.frames[0].targets[0]
        outcome = self.follow(task_id, session_id, snapshot, target)
        self.assertEqual(outcome.status, "loaded")
        self.assertEqual(outcome.url, self.origin + "/next")
        self.assertGreater(outcome.revision, snapshot.revision)
        with self.assertRaises(PageInspectionError):
            self.follow(task_id, session_id, snapshot, target)
        journal = self.service.store.events(task_id)
        self.assertTrue(
            any(event.kind == "page.navigation_requested" for event in journal)
        )
        self.assertTrue(
            any(event.kind == "page.navigation_finished" for event in journal)
        )
        self.assertNotIn("Next page", repr(journal))

    def test_long_link_destinations_are_not_silently_truncated_and_executed(self):
        task_id, _, session_id, page = self.session()
        self.load(
            session_id, page, '<a href="/next?query=' + "x" * 2100 + '">Long link</a>'
        )
        snapshot = self.inspect(task_id, session_id)
        target = snapshot.frames[0].targets[0]
        self.assertIsNone(target.href)
        self.assertIn("link_url_too_long", target.constraints)
        with self.assertRaises(PageInspectionError):
            self.follow(task_id, session_id, snapshot, target)

    def test_fragment_navigation_reports_the_observed_destination(self):
        task_id, _, session_id, page = self.session()
        self.load(
            session_id,
            page,
            '<a href="#section">Section</a><h2 id="section">Section</h2>',
        )
        snapshot = self.inspect(task_id, session_id)
        self.service.backend.navigation_timeout_ms = 500
        outcome = self.follow(
            task_id, session_id, snapshot, snapshot.frames[0].targets[0]
        )
        self.assertEqual(outcome.status, "same_document")
        self.assertEqual(outcome.url, self.origin + "/inspection#section")
        self.assertFalse(self.service.backend.inspect(session_id).loading)
        self.assertTrue(self.service.backend.inspect(session_id).load_success)
        self.assertIn("Section", self.inspect(task_id, session_id).frames[0].text)

    def test_target_navigation_denies_unapproved_origins_controls_and_stale_dom(self):
        task_id, _, session_id, page = self.session()
        self.load(
            session_id,
            page,
            '<a id="link" href="https://unapproved.example/">Other</a><button>Button</button>',
        )
        snapshot = self.inspect(task_id, session_id)
        with patch.object(self.service.backend, "navigate_observed") as dispatch:
            with self.assertRaises(TaskPermissionError):
                self.follow(
                    task_id, session_id, snapshot, snapshot.frames[0].targets[0]
                )
            with self.assertRaises(PageInspectionError):
                self.follow(
                    task_id, session_id, snapshot, snapshot.frames[0].targets[1]
                )
            self.script(page, "document.getElementById('link').href='/next'; true")
            with self.assertRaises(PageInspectionError):
                self.follow(
                    task_id, session_id, snapshot, snapshot.frames[0].targets[0]
                )
        dispatch.assert_not_called()

    def test_target_navigation_needs_read_and_navigate_and_rejects_cross_task(self):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        snapshot = self.inspect(task_id, session_id)
        other_id, _, _, _ = self.session()
        with self.assertRaises(TaskPermissionError):
            self.follow(other_id, session_id, snapshot, snapshot.frames[0].targets[0])
        reader_id, _, reader_session, _ = self.session(actions=["read"])
        with patch.object(self.service.backend, "inspect_target") as inspect:
            with self.assertRaises(TaskPermissionError):
                self.follow(
                    reader_id, reader_session, snapshot, snapshot.frames[0].targets[0]
                )
        inspect.assert_not_called()

    def test_target_navigation_reports_blocked_redirects_and_timeout_as_unsuccessful(
        self,
    ):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page, '<a href="/redirect-out">Redirect</a>')
        snapshot = self.inspect(task_id, session_id)
        outcome = self.follow(
            task_id, session_id, snapshot, snapshot.frames[0].targets[0]
        )
        self.assertEqual(outcome.status, "load_failed")
        self.assertFalse(any("forbidden" in path for path, _ in self.requests))
        self.load(session_id, page, '<a href="/slow">Slow</a>')
        snapshot = self.inspect(task_id, session_id)
        self.service.backend.navigation_timeout_ms = 20
        outcome = self.follow(
            task_id, session_id, snapshot, snapshot.frames[0].targets[0]
        )
        self.assertEqual(outcome.status, "timed_out")
        self.assertIsNone(self.service.backend._sessions[session_id].pending_action)

    def test_revocation_settles_pending_target_navigation(
        self,
    ):
        task_id, grant_id, session_id, page = self.session()
        self.load(session_id, page, '<a href="/slow">Slow</a>')
        snapshot = self.inspect(task_id, session_id)
        future = self.service.broker.submit(
            "page.navigate",
            task_id=task_id,
            session_id=session_id,
            snapshot_id=snapshot.snapshot_id,
            target_id=snapshot.frames[0].targets[0].target_id,
        )
        self.wait_for(
            lambda: (
                self.service.backend._sessions[session_id].pending_action is not None
            )
        )
        self.service.revoke_grant(task_id, grant_id)
        self.wait_for(future.done)
        with self.assertRaises((TaskPermissionError, PageInspectionError)):
            future.result()

    def test_target_navigation_never_dispatches_when_request_evidence_cannot_be_saved(
        self,
    ):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        snapshot = self.inspect(task_id, session_id)
        record = self.service.store.record_event

        def fail_request(task_id, kind, payload):
            if kind == "page.navigation_requested":
                raise sqlite3.OperationalError("fixture")
            return record(task_id, kind, payload)

        with (
            patch.object(self.service.store, "record_event", side_effect=fail_request),
            patch.object(self.service.backend, "navigate_observed") as dispatch,
        ):
            with self.assertRaises(sqlite3.Error):
                self.follow(
                    task_id, session_id, snapshot, snapshot.frames[0].targets[0]
                )
        dispatch.assert_not_called()
        self.assertTrue(self.service.failure)

    def test_target_navigation_receipt_failure_rejects_an_already_loaded_page(self):
        task_id, _, session_id, page = self.session()
        self.load(session_id, page)
        snapshot = self.inspect(task_id, session_id)
        record = self.service.store.record_event

        def fail_receipt(task_id, kind, payload):
            if kind == "page.navigation_finished":
                raise sqlite3.OperationalError("fixture")
            return record(task_id, kind, payload)

        with patch.object(self.service.store, "record_event", side_effect=fail_receipt):
            with self.assertRaises(sqlite3.Error):
                self.follow(
                    task_id, session_id, snapshot, snapshot.frames[0].targets[0]
                )
        self.assertTrue(self.service.failure)
        self.assertTrue(any(path == "/next" for path, _ in self.requests))

    def test_stop_and_superseding_navigation_reject_the_old_action_without_success(
        self,
    ):
        task_id, _, session_id, page = self.session()
        for command in ("session.stop", "session.navigate"):
            with self.subTest(command=command):
                self.load(session_id, page, '<a href="/slow">Slow</a>')
                snapshot = self.inspect(task_id, session_id)
                future = self.service.broker.submit(
                    "page.navigate",
                    task_id=task_id,
                    session_id=session_id,
                    snapshot_id=snapshot.snapshot_id,
                    target_id=snapshot.frames[0].targets[0].target_id,
                )
                self.wait_for(
                    lambda: (
                        self.service.backend._sessions[session_id].pending_action
                        is not None
                    )
                )
                arguments = {"task_id": task_id, "session_id": session_id}
                if command == "session.navigate":
                    arguments["url"] = self.origin + "/replacement"
                self.command(command, **arguments)
                self.wait_for(future.done)
                with self.assertRaises(PageInspectionError):
                    future.result()
                self.assertIsNone(
                    self.service.backend._sessions[session_id].pending_action
                )
                if command == "session.navigate":
                    self.wait_for(
                        lambda: (
                            self.service.backend.inspect(session_id).load_success
                            is True
                        )
                    )

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
