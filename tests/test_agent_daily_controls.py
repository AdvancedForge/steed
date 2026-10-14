"""Real private live pages, screenshot input and actionable grant diagnostics."""

import json
import unittest

import test_agent_gateway as gateway_fixture
import test_page_inspection as page_fixture
from PyQt6 import sip

from services.page_inspector import PageInspectionError
from services.task_policy import TaskPermissionError
from services.task_service import TaskService

HTML = """<!doctype html><title>Interactive private page</title>
<body style='margin:0'><canvas id='canvas' width='160' height='100'></canvas>
<script>window.canvasClicks=0; canvas.onclick=()=>window.canvasClicks++;
canvas.getContext('2d').fillRect(20,20,80,40); document.title='Scripts running';</script>
</body>"""


class PrivateComputerTests(unittest.TestCase):
    setUpClass = classmethod(page_fixture.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(page_fixture.PageInspectionTests.tearDownClass.__func__)
    setUp = page_fixture.PageInspectionTests.setUp
    tearDown = page_fixture.PageInspectionTests.tearDown
    wait_for = page_fixture.PageInspectionTests.wait_for
    command = page_fixture.PageInspectionTests.command
    request = page_fixture.PageInspectionTests.request
    load = page_fixture.PageInspectionTests.load

    def session(self, *, computer=True, html=HTML):
        task = self.command("task.create", objective="Use a normal private website")
        actions = ["navigate", "inspect", "read", "attach", "live", "visual"]
        if computer:
            actions.append("computer")
        grant = self.request(task.task_id, actions=actions, max_operations=200)
        if grant.status == "requested":
            self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command("session.open", task_id=task.task_id, mode="live")
        page = self.service.backend._sessions[opened.session_id].page
        self.load(opened.session_id, page, html)
        self.command(
            "session.watch", task_id=task.task_id, session_id=opened.session_id
        )
        self.app.processEvents()
        return task.task_id, grant, opened.session_id, page

    def capture(self, task, session):
        return self.command("page.capture", task_id=task, session_id=session)

    def value(self, page, code):
        results = []
        page.runJavaScript(code, results.append)
        self.wait_for(lambda: bool(results))
        return results[0]

    def test_private_live_page_runs_scripts_and_canvas_works_without_dom_targets(self):
        task, _, session, page = self.session()
        self.assertTrue(page.profile().isOffTheRecord())
        self.assertEqual(page.title(), "Scripts running")
        self.assertEqual(self.service.backend.inspect(session).mode, "live")
        snapshot = self.command("page.inspect", task_id=task, session_id=session)
        self.assertFalse(any(f.targets for f in snapshot.frames))
        self.assertTrue(snapshot.guidance)
        image = self.capture(task, session)
        result = self.command(
            "page.click",
            task_id=task,
            session_id=session,
            capture_id=image["capture_id"],
            x=50,
            y=40,
        )
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(self.value(page, "window.canvasClicks"), 1)
        with self.assertRaises(PageInspectionError) as rejected:
            self.command(
                "page.click",
                task_id=task,
                session_id=session,
                capture_id=image["capture_id"],
                x=50,
                y=40,
            )
        self.assertEqual(rejected.exception.code, "stale_capture")
        self.assertEqual(self.value(page, "window.canvasClicks"), 1)

    def test_live_embeds_load_normally_but_dom_reads_still_check_frame_origin(self):
        frame_origin = self.origin.replace("127.0.0.1", "localhost")
        type(self).page_fixtures["/private-frame"] = (
            b"<p>CROSS_FRAME_CONTENT</p><script>parent.postMessage('frame loaded','*')</script>"
        )
        html = f"<script>window.frameLoaded=false;addEventListener('message',e=>window.frameLoaded=e.data==='frame loaded')</script><iframe src='{frame_origin}/private-frame'></iframe>"
        task, _, session, page = self.session(html=html)
        self.wait_for(lambda: self.value(page, "window.frameLoaded") is True)
        snapshot = self.command("page.inspect", task_id=task, session_id=session)
        self.assertNotIn("CROSS_FRAME_CONTENT", str(snapshot.frames))
        self.assertTrue(
            any(
                "frame_origin_out_of_scope" in f.limitations
                or "frame_inaccessible_or_not_ready" in f.limitations
                for f in snapshot.frames
            )
        )

    def test_native_type_and_select_all_keep_entered_text_out_of_journal(self):
        task, _, session, page = self.session(
            html=HTML
            + "<input id='field' style='position:absolute;left:20px;top:130px'>"
        )
        image = self.capture(task, session)
        self.command(
            "page.click",
            task_id=task,
            session_id=session,
            capture_id=image["capture_id"],
            x=40,
            y=140,
        )
        image = self.capture(task, session)
        self.command(
            "page.type",
            task_id=task,
            session_id=session,
            capture_id=image["capture_id"],
            text="SYNTHETIC_TYPED_SECRET",
        )
        self.assertEqual(self.value(page, "field.value"), "SYNTHETIC_TYPED_SECRET")
        image = self.capture(task, session)
        self.command(
            "page.key",
            task_id=task,
            session_id=session,
            capture_id=image["capture_id"],
            key="SelectAll",
        )
        image = self.capture(task, session)
        self.command(
            "page.key",
            task_id=task,
            session_id=session,
            capture_id=image["capture_id"],
            key="Backspace",
        )
        self.assertEqual(self.value(page, "field.value"), "")
        self.assertNotIn("SYNTHETIC_TYPED_SECRET", str(self.service.store.events(task)))

    def test_resize_invalidates_pixels_before_click_and_bounds_are_checked(self):
        task, _, session, page = self.session()
        image = self.capture(task, session)
        with self.assertRaises(PageInspectionError) as outside:
            self.command(
                "page.click",
                task_id=task,
                session_id=session,
                capture_id=image["capture_id"],
                x=image["width"],
                y=0,
            )
        self.assertEqual(outside.exception.code, "invalid_coordinates")
        window = self.service._watchers[session]
        window.resize(window.width() + 100, window.height() + 100)
        self.app.processEvents()
        with self.assertRaises(PageInspectionError) as stale:
            self.command(
                "page.click",
                task_id=task,
                session_id=session,
                capture_id=image["capture_id"],
                x=50,
                y=40,
            )
        self.assertEqual(stale.exception.code, "stale_capture")
        self.assertEqual(self.value(page, "window.canvasClicks"), 0)

    def test_screenshot_permission_does_not_imply_computer_input(self):
        task, _, session, page = self.session(computer=False)
        image = self.capture(task, session)
        with self.assertRaises(TaskPermissionError):
            self.command(
                "page.click",
                task_id=task,
                session_id=session,
                capture_id=image["capture_id"],
                x=50,
                y=40,
            )
        self.assertEqual(self.value(page, "window.canvasClicks"), 0)

    def test_minimal_computer_scope_opens_distinct_private_live_profiles_by_default(
        self,
    ):
        task = self.command("task.create", objective="Open isolated interactive pages")
        grant = self.request(task.task_id, actions=["computer"], max_sessions=2)
        self.assertTrue(
            {"read", "visual", "live", "navigate", "inspect", "attach"}
            <= set(grant.scope["actions"])
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        first = self.command("session.open", task_id=task.task_id)
        second = self.command("session.open", task_id=task.task_id)
        a = self.service.backend._sessions[first.session_id].profile
        b = self.service.backend._sessions[second.session_id].profile
        self.assertEqual(first.mode, "live")
        self.assertTrue(a.isOffTheRecord() and b.isOffTheRecord())
        self.assertIsNot(a, b)

    def test_user_control_pauses_computer_input_and_unblocks_live_page_interaction(
        self,
    ):
        task, _, session, _ = self.session()
        image = self.capture(task, session)
        watch = self.service._watchers[session]
        watch.take_control()
        view = self.service.backend._sessions[session].views[0]
        self.assertFalse(view.input_shield.enabled)
        self.assertEqual(self.service.backend.inspect(session).control_owner, "user")
        with self.assertRaises(TaskPermissionError) as paused:
            self.command(
                "page.click",
                task_id=task,
                session_id=session,
                capture_id=image["capture_id"],
                x=50,
                y=40,
            )
        self.assertEqual(paused.exception.code, "user_control_active")
        watch.return_control()
        self.assertTrue(view.input_shield.enabled)
        self.assertEqual(self.service.backend.inspect(session).control_owner, "agent")

    def test_repeated_grants_reuse_pending_and_approved_receipts(self):
        task = self.command("task.create", objective="Repeat a minimal permission")
        scope = {"origins": ["https://example.com"], "actions": ["read"]}
        first = self.command("grant.request", task_id=task.task_id, scope=scope)
        self.assertTrue(first.scope["allow_private_network"])
        self.assertEqual(
            self.command("grant.request", task_id=task.task_id, scope=scope).grant_id,
            first.grant_id,
        )
        self.service.approve_grant(task.task_id, first.grant_id)
        self.assertEqual(
            self.command("grant.request", task_id=task.task_id, scope=scope).grant_id,
            first.grant_id,
        )
        self.assertEqual(len(self.service.store.grants(task.task_id)), 1)

    def test_replacement_waits_for_review_then_ends_old_sessions(self):
        task, old, session, _ = self.session()
        scope = dict(old.scope, max_sessions=2)
        replacement = self.command("grant.request", task_id=task, scope=scope)
        self.assertEqual(replacement.status, "requested")
        self.assertIn(session, self.service._sessions)
        self.service.approve_grant(task, replacement.grant_id)
        self.assertNotIn(session, self.service._sessions)
        self.assertEqual(self.service.store.grant(task, old.grant_id).status, "ended")

    def test_auto_approval_is_explicit_and_not_restored_on_restart(self):
        self.assertFalse(self.service.auto_approve_requests)
        self.service.set_auto_approval(True)
        task = self.command("task.create", objective="Synthetic auto-approved task")
        grant = self.command(
            "grant.request", task_id=task.task_id, scope={"actions": ["artifact"]}
        )
        self.assertEqual(grant.status, "approved")
        self.service.set_auto_approval(False)
        second = self.command("task.create", objective="Manual approvals restored")
        self.assertEqual(
            self.command(
                "grant.request", task_id=second.task_id, scope={"actions": ["artifact"]}
            ).status,
            "requested",
        )
        self.service.set_auto_approval(True)
        self.service.close()
        sip.delete(self.service)
        self.service = TaskService(self.service_path())
        self.assertFalse(self.service.auto_approve_requests)

    def service_path(self):
        from pathlib import Path

        return Path(self.directory.name) / "tasks.sqlite"


class GrantDiagnosticTests(unittest.TestCase):
    setUpClass = classmethod(gateway_fixture.AgentGatewayTests.setUpClass.__func__)
    tearDownClass = classmethod(
        gateway_fixture.AgentGatewayTests.tearDownClass.__func__
    )
    setUp = gateway_fixture.AgentGatewayTests.setUp
    tearDown = gateway_fixture.AgentGatewayTests.tearDown
    wait_for = gateway_fixture.AgentGatewayTests.wait_for
    receive = gateway_fixture.AgentGatewayTests.receive
    connect = gateway_fixture.AgentGatewayTests.connect
    call = gateway_fixture.AgentGatewayTests.call
    start = gateway_fixture.AgentGatewayTests.start

    def test_invalid_origin_names_field_and_fix_without_echoing_sensitive_value(self):
        socket = self.connect()
        self.start(socket)
        result = self.call(
            socket,
            "grant.request",
            scope={"origins": ["https://example.com/path?SYNTHETIC_SECRET"]},
        )
        self.assertEqual(result["error"]["code"], "invalid_scope")
        self.assertEqual(result["error"]["field"], "origins")
        self.assertIn("without paths", result["error"]["message"])
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_minimal_hostname_request_and_repeat_succeed_over_real_gateway(self):
        socket = self.connect()
        self.start(socket)
        scope = {"origins": ["https://example.com"], "actions": ["read"]}
        first = self.call(socket, "grant.request", scope=scope)["result"]
        second = self.call(socket, "grant.request", scope=scope)["result"]
        self.assertEqual(first["grant_id"], second["grant_id"])
