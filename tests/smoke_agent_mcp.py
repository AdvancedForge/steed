"""Two external stdio clients drive a task beside a synthetic signed-in user.

The fixture alone clicks the approval popup and cancels the task. No production
approval bypass, accounts, real profiles, or external website requests are used.
"""

import asyncio
import json
import select
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEngineHttpRequest, QWebEngineSettings
from test_task_profiles import TaskProfileTests

from browser_window import BrowserWindow


async def sdk_client(discovery, origin, shared, revoke, revoked):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(Path(__file__).resolve().parents[1] / "agent_mcp.py"),
            "--discovery",
            str(discovery),
        ],
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            initialized = await client.initialize()
            shared["protocol_version"] = initialized.protocolVersion
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "steed_page_submit" in names
            assert "steed_grant_approve" not in names

            async def call(command, **arguments):
                outcome = await client.call_tool(
                    "steed_" + command.replace(".", "_"), arguments
                )
                assert not outcome.isError, outcome
                assert outcome.structuredContent == json.loads(outcome.content[0].text)
                return outcome.structuredContent["result"]

            task = await call(
                "task.start", objective="Complete a synthetic form beside the user"
            )
            shared["task_id"] = task["task_id"]
            assert not (await call("task.capabilities"))["website_scripts"]
            context = await call("browser.context")
            assert context["profile_selector"] == "existing:work"
            assert context["shared_profile_available"] and context["window_id"]
            shared["profile_discovery"] = True
            denied = await client.call_tool("steed_session_open", {})
            assert (
                denied.isError
                and denied.structuredContent["error"]["code"] == "approval_required"
            )
            await call(
                "grant.request",
                scope={
                    "origins": [origin],
                    "allow_private_network": True,
                    "profile": context["profile_selector"],
                    "actions": [
                        "navigate",
                        "inspect",
                        "read",
                        "edit",
                        "submit",
                        "scroll",
                    ],
                    "duration_seconds": 120,
                },
            )
            deadline = time.monotonic() + 15
            while (await call("grant.list"))[0]["status"] != "approved":
                assert time.monotonic() < deadline, "Approval fixture timed out"
                await asyncio.sleep(0.02)
            session = await call("session.open", profile=context["profile_selector"])
            session_id = session["session_id"]
            shared["session_id"] = session_id
            await call(
                "session.navigate", session_id=session_id, url=origin + "/mcp-start"
            )
            await call("page.wait", session_id=session_id)

            async def target(label):
                snapshot = await call("page.inspect", session_id=session_id)
                assert "FORM_PRIVATE" not in json.dumps(snapshot)
                found = next(
                    target
                    for frame in snapshot["frames"]
                    for target in frame["targets"]
                    if target["label"] == label
                )
                return {
                    "session_id": session_id,
                    "snapshot_id": snapshot["snapshot_id"],
                    "target_id": found["target_id"],
                }

            link = await target("Open form")
            await call("page.navigate", **link)
            field = await target("Message")
            edited = await call(
                "page.act", **field, operation="enter", text="SYNTHETIC_MCP_VALUE"
            )
            assert edited["text_matches"]
            stale = await client.call_tool("steed_page_target", field)
            assert stale.isError
            checkbox = await target("Confirm")
            activated = await call("page.act", **checkbox, operation="activate")
            assert activated["checked"]
            submitter = await target("Send")
            submitted = await call("page.submit", **submitter)
            assert submitted["status"] == "loaded" and submitted["request_authorized"]
            events = await call("task.events")
            assert "SYNTHETIC_MCP_VALUE" not in json.dumps(events)
            assert "FORM_PRIVATE" not in json.dumps(events)
            assert any(event["kind"] == "page.action_finished" for event in events)
            shared["form_completed"] = True
            revoke.set()
            deadline = time.monotonic() + 15
            while not revoked.is_set():
                assert time.monotonic() < deadline, "Cancellation fixture timed out"
                await asyncio.sleep(0.02)
            assert (await call("task.status"))["status"] == "cancelled"
            denied = await client.call_tool(
                "steed_session_inspect", {"session_id": session_id}
            )
            assert (
                denied.isError
                and denied.structuredContent["error"]["code"] == "task_inactive"
            )
            shared["cancellation_denied_further_access"] = True


def raw_client(discovery, foreign_session):
    """Independent JSON-RPC client: no MCP SDK on this side of the protocol."""
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "agent_mcp.py"),
            "--discovery",
            str(discovery),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    sequence = 0

    def request(method, params):
        nonlocal sequence
        sequence += 1
        process.stdin.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}
            ).encode()
            + b"\n"
        )
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 15)[0], (
            "Raw MCP response timed out"
        )
        response = json.loads(process.stdout.readline())
        assert response["id"] == sequence
        return response["result"]

    try:
        request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "steed-raw-fixture", "version": "1"},
            },
        )
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        assert len(request("tools/list", {})["tools"]) > 10
        result = request(
            "tools/call",
            {
                "name": "steed_task_start",
                "arguments": {"objective": "Independent client isolation"},
            },
        )
        assert not result["isError"]
        denied = request(
            "tools/call",
            {
                "name": "steed_session_inspect",
                "arguments": {"session_id": foreign_session},
            },
        )
        assert (
            denied["isError"]
            and denied["structuredContent"]["error"]["code"] == "session_not_owned"
        )
        denied = request("tools/call", {"name": "steed_grant_approve", "arguments": {}})
        assert denied["isError"]
        result = request("tools/call", {"name": "steed_task_finish", "arguments": {}})
        assert not result["isError"]
        result = request(
            "tools/call",
            {
                "name": "steed_task_start",
                "arguments": {"objective": "Reuse connection with fresh permissions"},
            },
        )
        assert not result["isError"]
        denied = request("tools/call", {"name": "steed_session_open", "arguments": {}})
        assert (
            denied["isError"]
            and denied["structuredContent"]["error"]["code"] == "approval_required"
        )
        result = request("tools/call", {"name": "steed_task_finish", "arguments": {}})
        assert not result["isError"]
        return True
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def check():
    case = TaskProfileTests("runTest")
    case.setUpClass()
    case.setUp()
    worker = None
    shared, errors = {}, []
    revoke, revoked = threading.Event(), threading.Event()
    try:
        window = BrowserWindow()
        case.windows.append(window)
        window._switch_to_profile("work")
        window.show()
        user = window.tabs[window.current_tab_index].page()
        loaded = []
        user.loadFinished.connect(loaded.append)
        user.setUrl(QUrl(case.origin + "/cookie"))
        case.wait_for(lambda: bool(loaded) and loaded[-1])
        user.loadFinished.disconnect(loaded.append)
        type(case).page_fixtures = {
            "/mcp-start": b'<p>Delegated workflow</p><a href="/mcp-form">Open form</a>',
            "/mcp-form": b'<form method="post" action="/mcp-submitted"><input aria-label="Message" name="message" value="FORM_PRIVATE"><input type="hidden" name="token" value="SYNTHETIC_TOKEN"><input aria-label="Confirm" type="checkbox" name="confirm" value="yes"><button name="decision" value="send">Send</button></form>',
        }
        type(case).post_responses = {
            "/mcp-submitted": (200, None, b"<p>Received</p>"),
            "/mcp-user-post": (200, None, b"<p>User browsing continues</p>"),
        }
        window.agent_access_action.trigger()
        discovery = case.registry.agent_gateway.discovery

        def run():
            try:
                asyncio.run(sdk_client(discovery, case.origin, shared, revoke, revoked))
                shared["independent_raw_client"] = raw_client(
                    discovery, shared["session_id"]
                )
            except BaseException:
                errors.append(traceback.format_exc())

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 60
        user_posted = False
        while worker.is_alive() and time.monotonic() < deadline:
            case.app.processEvents()
            dialog = case.registry.task_approvals.dialog
            if dialog is not None and dialog.isVisible():
                assert not dialog.isModal()
                assert dialog.task_id == shared["task_id"]
                dialog.approve_button.click()  # Trusted synthetic fixture only.
                shared["approval_popup"] = True
            session_id = shared.get("session_id")
            if session_id in case.service.backend._sessions and not user_posted:
                task_page = case.service.backend._sessions[session_id].page
                assert task_page.profile() is user.profile() is case.profile
                assert user.settings().testAttribute(
                    QWebEngineSettings.WebAttribute.JavascriptEnabled
                )
                assert not task_page.settings().testAttribute(
                    QWebEngineSettings.WebAttribute.JavascriptEnabled
                )
                request = QWebEngineHttpRequest(QUrl(case.origin + "/mcp-user-post"))
                request.setMethod(QWebEngineHttpRequest.Method.Post)
                request.setPostData(b"user=synthetic")
                user.load(request)
                user_posted = True
                shared["actual_shared_profile"] = True
            if revoke.is_set() and not revoked.is_set():
                case.service.cancel_task(shared["task_id"])
                revoked.set()
            time.sleep(0.005)
        assert not worker.is_alive(), "External workflow timed out"
        assert not errors, errors
        case.wait_for(
            lambda: any(path == "/mcp-user-post" for path, _, _ in case.posted)
        )
        form = next(body for path, body, _ in case.posted if path == "/mcp-submitted")
        assert b"message=SYNTHETIC_MCP_VALUE" in form and b"confirm=yes" in form
        assert any(
            path == "/mcp-form" and "task_cookie=fixture" in cookie
            for path, cookie in case.requests
        )
        assert user.profile() is case.profile
        assert not case.service._sessions
        shared["shared_cookie"] = True
        shared["ordinary_user_post"] = True
        shared["task_pages_disposed"] = True
        print(json.dumps(shared, indent=2))
    finally:
        case.registry.set_agent_access(False)
        if worker is not None:
            case.wait_for(lambda: not worker.is_alive())
            worker.join()
        case.tearDown()
        case.tearDownClass()


if __name__ == "__main__":
    check()
