"""Two external stdio or HTTP clients drive a task beside a synthetic signed-in user.

The fixture alone clicks the approval popup and cancels the task. No production
approval bypass, accounts, real profiles, or external website requests are used.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import select
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_connector_support import MemorySecrets
from mock_app_connector import register_mock_apps
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWebEngineCore import QWebEngineHttpRequest, QWebEngineSettings
from recipe_support import (
    TOKEN,
    SyntheticRecipeHttp,
    WriteRecipeHttp,
    recipe,
    write_recipe,
)
from test_recipe_oauth import OAuthHttp
from test_task_profiles import TaskProfileTests

from browser_window import BrowserWindow
from services.connector_recipes import (
    RECIPE_NESTED_EXAMPLE,
    RECIPE_OAUTH_EXAMPLE,
    RECIPE_PAGINATED_EXAMPLE,
    RECIPE_PLAN_EXAMPLE,
    parse_recipe,
)
from ui.app_review import AppReviewDialog
from ui.recipe_review import RecipeReviewDialog
from ui.task_dialog import TaskDialog
from ui.task_recovery import TaskRecoveryDialog
from ui.worker_approval import WorkerApprovalDialog


@asynccontextmanager
async def sdk_transport(discovery, endpoint):
    if endpoint is not None:
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + endpoint["tokens"]["sdk"]}
        ) as http:
            async with streamable_http_client(
                endpoint["url"], http_client=http
            ) as streams:
                yield streams
    else:
        from mcp import StdioServerParameters
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
            yield reader, writer, None


async def recipe_workflow(
    call,
    shared,
    label,
    *,
    oauth=False,
    pagination=False,
    writing=False,
    nested=False,
    plan=False,
):
    """External clients propose source; only the fixture installs/connects it."""
    task = await call("task.start", objective="Design a synthetic connection recipe")
    shared[label + "_recipe_task"] = task["task_id"]
    capabilities = await call("recipe.capabilities")
    assert (
        capabilities["available"]
        and capabilities["installation_approval"] == "exact_recipe"
    )
    source = json.dumps(
        capabilities["plan_example"]
        if plan
        else capabilities["nested_example"]
        if nested
        else capabilities["write_example"]
        if writing
        else capabilities["pagination_example"]
        if pagination
        else capabilities["oauth_example"]
        if oauth
        else capabilities["example"]
    ).encode()
    provider = parse_recipe(source).provider
    await call("grant.request", scope={"actions": ["workspace", "connector"]})

    async def approved():
        deadline = time.monotonic() + 15
        while (await call("grant.list"))[-1]["status"] != "approved":
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)

    await approved()
    item = await call(
        "workspace.begin",
        name="recipe.json",
        media_type="application/json",
        byte_count=len(source),
        sha256=hashlib.sha256(source).hexdigest(),
    )
    await call(
        "workspace.write",
        file_id=item["file_id"],
        offset=0,
        data_base64=base64.b64encode(source).decode(),
    )
    await call("workspace.seal", file_id=item["file_id"])
    preflight = await call("recipe.validate", file_id=item["file_id"])
    assert preflight["valid"] and preflight["installation_required"]
    assert preflight["recipe"]["provider"] == provider
    assert not preflight["provider_semantics_verified"]
    arguments = {"file_id": item["file_id"], "request_id": "fixture-recipe"}
    receipt = await call("recipe.propose", **arguments)
    deadline = time.monotonic() + 15
    while receipt["state"] == "awaiting_approval":
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
        receipt = await call("recipe.status", request_id="fixture-recipe")
    assert receipt["state"] == "installed"
    assert (await call("recipe.propose", **arguments)) == receipt
    tools = []
    while not tools:
        assert time.monotonic() < deadline
        tools = [
            t
            for t in await call("app.list_tools")
            if t["tool"]
            == ("create_note" if writing else "list_notes" if oauth else "read_note")
            and t["connector"].startswith(provider + "_")
        ]
        await asyncio.sleep(0.02)
    assert len(tools) == 1
    await call("task.finish")
    if writing:
        task = await call(
            "task.start", objective="Create and send reviewed synthetic drafts"
        )
        shared[label + "_recipe_task"] = task["task_id"]
        connector = tools[0]["connector"]
        await call(
            "grant.request",
            scope={
                "actions": ["app", "artifact"],
                "connector_permissions": [
                    {
                        "connector": connector,
                        "tool": "create_note",
                        "effect": "write",
                        "destination": "account",
                    },
                    *(
                        []
                        if plan
                        else [
                            {
                                "connector": connector,
                                "tool": "create_note",
                                "effect": "read",
                                "destination": "account",
                            }
                        ]
                    ),
                    *(
                        []
                        if nested or plan
                        else [
                            {
                                "connector": connector,
                                "tool": "send_message",
                                "effect": "send",
                                "destination": "email:recipient@example.test",
                            }
                        ]
                    ),
                ],
            },
        )
        await approved()
        artifact = await call(
            "artifact.store",
            title="Synthetic recipe payload",
            content='SYNTHETIC_REVIEWED_DRAFT with "quotes" and\nnewlines',
        )
        for request_id, tool, destination in (
            ("create", "create_note", "account"),
            ("send", "send_message", "email:recipient@example.test"),
            ("uncertain-create", "create_note", "account"),
        ):
            if (nested or plan) and tool == "send_message":
                continue
            arguments = {
                "connector": connector,
                "tool": tool,
                "destination": destination,
                "request_id": request_id,
                "artifact_id": artifact["artifact_id"],
            }
            result = await call("app.call_tool", **arguments)
            deadline = time.monotonic() + 15
            while result["state"] in {"requested", "dispatched"}:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)
                result = await call("app.status", request_id=request_id)
            assert result["state"] == (
                "uncertain" if request_id == "uncertain-create" else "succeeded"
            ), result
            assert await call("app.call_tool", **arguments) == result
            if request_id == "uncertain-create" and not plan:
                await call("app.reconcile", request_id=request_id)
                while (await call("app.status", request_id=request_id))[
                    "state"
                ] != "succeeded":
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.02)
        shared[
            label
            + (
                "_reviewed_nested_recipe_writes"
                if nested
                else "_reviewed_plan_recipe_writes"
                if plan
                else "_reviewed_recipe_writes"
            )
        ] = True
        await call("task.finish")
        return
    task = await call("task.start", objective="Read one note from the reviewed service")
    shared[label + "_recipe_task"] = task["task_id"]
    arguments = {
        "connector": tools[0]["connector"],
        "tool": "list_notes" if oauth else "read_note",
        "destination": "account" if oauth else "note-1",
    }
    await call(
        "grant.request",
        scope={
            "actions": ["app"],
            "connector_permissions": [{**arguments, "effect": "read"}],
        },
    )
    await approved()
    arguments["request_id"] = "recipe-note"
    result = await call("app.call_tool", **arguments)
    while result["state"] in {"requested", "dispatched"}:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
        result = await call("app.status", request_id="recipe-note")
    assert result["state"] == "succeeded", result
    assert json.loads(result["text"]) == (
        {
            "notes": [
                {"id": "note-1", "title": "Synthetic note"},
                {"id": "note-2", "title": "Next synthetic note"},
            ],
            "_pagination": {"pages": 2, "more_available": False},
        }
        if pagination
        else (
            {"notes": [{"id": "note-1", "title": "Synthetic note"}]}
            if oauth
            else {"title": "Fixture note", "body": "Shared task data"}
        )
    )
    assert (await call("app.call_tool", **arguments)) == result
    shared[
        label
        + (
            "_paginated_recipe_read"
            if pagination
            else "_oauth_recipe_read"
            if oauth
            else "_reviewed_recipe_read"
        )
    ] = True
    await call("task.finish")


async def workspace_workflow(call, destination, require_documents=False):
    """Same typed workflow over independently implemented protocol clients."""

    async def upload(name, data):
        item = await call(
            "workspace.begin",
            name=name,
            media_type="application/octet-stream",
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        for offset in range(0, len(data), 32768):
            arguments = {
                "file_id": item["file_id"],
                "offset": offset,
                "data_base64": base64.b64encode(data[offset : offset + 32768]).decode(),
            }
            await call("workspace.write", **arguments)
            await call("workspace.write", **arguments)
        return await call("workspace.seal", file_id=item["file_id"])

    data = bytes(range(256)) * 140
    item = await upload("attachment.bin", data)
    observed = []
    for offset in range(0, len(data), 32768):
        chunk = await call("workspace.read", file_id=item["file_id"], offset=offset)
        observed.append(base64.b64decode(chunk["data_base64"]))
    assert b"".join(observed) == data
    await call("workspace.export", file_id=item["file_id"], destination=destination)
    assert Path(destination).read_bytes() == data
    await upload("main.py", b"raise RuntimeError('staged source must never run')")
    manifest = await upload(
        "extension.json",
        json.dumps(
            {
                "version": 1,
                "name": "fixture-helper",
                "entrypoint": "main.py",
                "files": ["main.py"],
                "capabilities": [
                    {
                        "connector": "email",
                        "tool": "send",
                        "effect": "send",
                        "destination": "unapproved@example.test",
                    }
                ],
            }
        ).encode(),
    )
    package = await call("workspace.stage_extension", manifest_id=manifest["file_id"])
    assert package["status"] == "staged" and not package["executable"]
    assert len(await call("workspace.list")) == 3
    await upload(
        "generator.py",
        b"from steed_worker import store_file\n"
        b"import io, zipfile\n"
        b"out = io.BytesIO()\n"
        b"with zipfile.ZipFile(out, 'w') as archive:\n"
        b"    archive.writestr('report.csv', 'name,value\\nSteed,1\\n')\n"
        b"store_file('generated.zip', out.getvalue(), 'application/zip')\n",
    )
    runnable_manifest = await upload(
        "generator.json",
        json.dumps(
            {
                "version": 1,
                "name": "fixture-generator",
                "entrypoint": "generator.py",
                "files": ["generator.py"],
                "capabilities": [],
            }
        ).encode(),
    )
    runnable = await call(
        "workspace.stage_extension", manifest_id=runnable_manifest["file_id"]
    )
    receipt = await call(
        "worker.run", extension_id=runnable["extension_id"], request_id="generate-zip"
    )
    deadline = time.monotonic() + 10
    while receipt["state"] in {"awaiting_approval", "running", "cancelling"}:
        assert time.monotonic() < deadline, receipt
        await asyncio.sleep(0.02)
        receipt = await call("worker.status", request_id="generate-zip")
    assert receipt["state"] == "succeeded", receipt
    output = next(
        f for f in await call("workspace.list") if f["name"] == "generated.zip"
    )
    content = await call("workspace.read", file_id=output["file_id"])
    import io
    import zipfile

    with zipfile.ZipFile(
        io.BytesIO(base64.b64decode(content["data_base64"]))
    ) as archive:
        assert archive.read("report.csv") == b"name,value\nSteed,1\n"
    assert (
        await call(
            "worker.run",
            extension_id=runnable["extension_id"],
            request_id="generate-zip",
        )
    )["state"] == "succeeded"
    if require_documents:
        setup = await call("worker.capabilities")
        assert any(
            e["runtime"] == "documents" and e["available"]
            for e in setup["environments"]
        ), setup
        await upload(
            "documents.py",
            b"""
from io import BytesIO
from reportlab.pdfgen import canvas
from openpyxl import Workbook
from PIL import Image
from steed_worker import store_file
out = BytesIO()
pdf = canvas.Canvas(out)
pdf.drawString(72,720,'Steed protocol fixture')
pdf.save()
store_file('report.pdf',out.getvalue(),'application/pdf')
out = BytesIO()
sheet = Workbook()
sheet.active.append(['Steed',42])
sheet.save(out)
store_file('report.xlsx',out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
out = BytesIO()
Image.new('RGB',(16,16),'blue').save(out,format='PNG')
store_file('report.png',out.getvalue(),'image/png')
""",
        )
        manifest = await upload(
            "documents.json",
            json.dumps(
                {
                    "version": 1,
                    "name": "document-fixture",
                    "entrypoint": "documents.py",
                    "files": ["documents.py"],
                    "capabilities": [],
                }
            ).encode(),
        )
        package = await call(
            "workspace.stage_extension", manifest_id=manifest["file_id"]
        )
        receipt = await call(
            "worker.run",
            extension_id=package["extension_id"],
            request_id="documents",
            runtime="documents",
        )
        deadline = time.monotonic() + 10
        while receipt["state"] in {"awaiting_approval", "running", "cancelling"}:
            assert time.monotonic() < deadline, receipt
            await asyncio.sleep(0.02)
            receipt = await call("worker.status", request_id="documents")
        assert receipt["state"] == "succeeded" and receipt["runtime"] == "documents", (
            receipt
        )
        assert len(receipt["environment_sha256"]) == 64
        files = {f["name"]: f for f in await call("workspace.list")}
        outputs = {}
        for name in ("report.pdf", "report.xlsx", "report.png"):
            result = await call("workspace.read", file_id=files[name]["file_id"])
            outputs[name] = base64.b64decode(result["data_base64"])
        assert (
            outputs["report.pdf"].startswith(b"%PDF-")
            and b"%%EOF" in outputs["report.pdf"]
        )
        assert outputs["report.png"].startswith(b"\x89PNG\r\n\x1a\n")
        with zipfile.ZipFile(io.BytesIO(outputs["report.xlsx"])) as archive:
            assert b"<v>42</v>" in archive.read("xl/worksheets/sheet1.xml")


async def sdk_client(
    discovery, origin, shared, revoke, revoked, destination, source, endpoint=None
):
    from mcp import ClientSession

    async with sdk_transport(discovery, endpoint) as (reader, writer, get_session_id):
        async with ClientSession(reader, writer) as client:
            initialized = await client.initialize()
            shared["protocol_version"] = initialized.protocolVersion
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "steed_page_submit" in names
            assert "steed_workspace_stage_extension" in names
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
            await conversation_workflow(call, shared, "sdk", task["task_id"])
            worker_support = await call("worker.capabilities")
            assert (
                worker_support["available"]
                and worker_support["execution_approval"] == "exact_package"
            )
            shared["sdk_worker_setup_discovery"] = True
            app_tools = await call("app.list_tools")
            assert {(t["connector"], t["effect"]) for t in app_tools} == {
                ("documents", "write"),
                ("email", "send"),
            }
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
                        "attach",
                        "edit",
                        "submit",
                        "scroll",
                        "download",
                        "upload",
                        "artifact",
                        "workspace",
                        "worker",
                        "app",
                    ],
                    "duration_seconds": 120,
                    "max_operations": 500,
                    "download_destinations": [destination, destination + "-workspace"],
                    "max_download_bytes": 1024 * 1024,
                    "upload_sources": [source],
                    "upload_origins": [origin],
                    "max_upload_bytes": 1024 * 1024,
                    "connector_permissions": [
                        {
                            "connector": "documents",
                            "tool": "create",
                            "effect": "write",
                            "destination": "synthetic-workspace",
                        },
                        {
                            "connector": "email",
                            "tool": "send",
                            "effect": "send",
                            "destination": "fixture@example.test",
                        },
                    ],
                },
            )
            deadline = time.monotonic() + 15
            while (await call("grant.list"))[0]["status"] != "approved":
                assert time.monotonic() < deadline, "Approval fixture timed out"
                await asyncio.sleep(0.02)
            await workspace_workflow(
                call, destination + "-workspace", shared["require_documents"]
            )
            shared["sdk_binary_export_and_staged_extension"] = True
            session = await call("session.open", profile=context["profile_selector"])
            session_id = session["session_id"]
            shared["session_id"] = session_id
            if endpoint is not None:
                shared["independent_raw_client"] = await asyncio.to_thread(
                    raw_http_client,
                    endpoint,
                    session_id,
                    get_session_id(),
                    origin,
                    shared,
                )
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
            await call("session.watch", session_id=session_id)
            old_field = await target("Message")
            shared["takeover_requested"] = True
            deadline = time.monotonic() + 15
            while not shared.get("user_control_taken"):
                assert time.monotonic() < deadline, "User takeover fixture timed out"
                await asyncio.sleep(0.02)
            assert (await call("session.inspect", session_id=session_id))[
                "control_owner"
            ] == "user"
            paused = await client.call_tool(
                "steed_page_inspect", {"session_id": session_id}
            )
            assert (
                paused.isError
                and paused.structuredContent["error"]["code"] == "user_control_active"
            )
            shared["agent_blocked_during_takeover"] = True
            shared["return_requested"] = True
            while not shared.get("user_control_returned"):
                assert time.monotonic() < deadline, "User return fixture timed out"
                await asyncio.sleep(0.02)
            stale_handoff = await client.call_tool("steed_page_target", old_field)
            assert (
                stale_handoff.isError
                and stale_handoff.structuredContent["error"]["code"] == "stale_snapshot"
            )
            shared["handoff_stale_refs_denied"] = True
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
            assert "SYNTHETIC_USER_HANDOFF" not in json.dumps(events)
            assert any(event["kind"] == "page.action_finished" for event in events)
            shared["form_completed"] = True
            attachment = await target("Download receipt")
            download = await call(
                "page.download", **attachment, destination=destination
            )
            deadline = time.monotonic() + 15
            while download["state"] in ("requested", "in_progress"):
                assert time.monotonic() < deadline, "Download fixture timed out"
                await asyncio.sleep(0.1)
                download = await call(
                    "download.status",
                    session_id=session_id,
                    download_id=download["download_id"],
                )
            assert download["state"] == "completed", download
            assert Path(destination).read_bytes() == b"SYNTHETIC_DOWNLOAD_BYTES"
            shared["download_completed"] = True
            await call(
                "session.navigate",
                session_id=session_id,
                url=origin + "/mcp-upload-form",
            )
            assert (await call("page.wait", session_id=session_id))[
                "status"
            ] == "matched"
            selected = await call(
                "page.upload", **(await target("Upload source")), source=source
            )
            assert selected["status"] == "bound"
            assert selected["byte_count"] == len(b"SYNTHETIC_MCP_UPLOAD_BYTES")
            Path(source).write_bytes(b"SYNTHETIC_CHANGED_AFTER_COPY")
            upload = await call("page.submit", **(await target("Send file")))
            assert upload["request_authorized"] and upload["status"] == "loaded"
            events = await call("task.events", limit=1000)
            assert "SYNTHETIC_MCP_UPLOAD_BYTES" not in json.dumps(events)
            shared["upload_completed"] = True
            sources = [e["sequence"] for e in events if e["kind"] == "page.inspected"][
                -2:
            ]
            artifact = await call(
                "artifact.store",
                title="Synthetic draft",
                content="SYNTHETIC_RETAINED_DRAFT",
                source_events=sources,
            )
            assert "content" not in artifact
            assert (await call("artifact.read", artifact_id=artifact["artifact_id"]))[
                "content"
            ] == "SYNTHETIC_RETAINED_DRAFT"
            assert (await call("artifact.list"))[0]["artifact_id"] == artifact[
                "artifact_id"
            ]
            assert "SYNTHETIC_RETAINED_DRAFT" not in json.dumps(
                await call("task.events", limit=1000)
            )
            shared["artifact_completed"] = True

            async def app_result(request_id):
                deadline = time.monotonic() + 20
                while True:
                    receipt = await call("app.status", request_id=request_id)
                    if receipt["state"] not in {
                        "requested",
                        "dispatched",
                        "reconciling",
                    }:
                        return receipt
                    assert time.monotonic() < deadline, "Mock app workflow timed out"
                    await asyncio.sleep(0.1)

            document_args = {
                "connector": "documents",
                "tool": "create",
                "request_id": "document-1",
                "destination": "synthetic-workspace",
                "artifact_id": artifact["artifact_id"],
            }
            await call("app.call_tool", **document_args)
            document = await app_result("document-1")
            assert document["state"] == "succeeded" and document[
                "reference"
            ].startswith("document-")
            assert (await call("app.call_tool", **document_args))[
                "reference"
            ] == document["reference"]
            email = await call(
                "artifact.store",
                title="Synthetic email",
                content="Synthetic document reference: " + document["reference"],
                source_events=sources,
            )
            denied = await client.call_tool(
                "steed_app_call_tool",
                {
                    "connector": "email",
                    "tool": "send",
                    "request_id": "foreign-recipient",
                    "destination": "outside@example.test",
                    "artifact_id": email["artifact_id"],
                },
            )
            assert denied.isError
            email_args = {
                "connector": "email",
                "tool": "send",
                "request_id": "uncertain-email",
                "destination": "fixture@example.test",
                "artifact_id": email["artifact_id"],
            }
            await call("app.call_tool", **email_args)
            assert (await app_result("uncertain-email"))["state"] == "uncertain"
            assert (await call("app.call_tool", **email_args))["state"] == "uncertain"
            shared["mock_document_email_completed"] = True
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
            followup = await call(
                "task.start", objective="Continue explicitly selected drafts"
            )
            shared["sdk_recovery_task"] = followup["task_id"]
            await call("task.recover")
            deadline = time.monotonic() + 15
            while (await call("task.recovery"))["status"] == "requested":
                assert time.monotonic() < deadline, "Recovery picker timed out"
                await asyncio.sleep(0.02)
            recovery = await call("task.recovery")
            assert (
                recovery["status"] == "approved"
                and recovery["source_task_id"] == task["task_id"]
            )
            assert len(recovery["context"]["imported_files"]) == 1
            assert recovery["context"]["imported_files"][0]["name"] == "generated.zip"
            await call(
                "grant.request",
                scope={
                    "actions": ["artifact", "app", "workspace"],
                    "connector_permissions": [
                        {
                            "connector": "documents",
                            "tool": "create",
                            "effect": "write",
                            "destination": "synthetic-workspace",
                        },
                        {
                            "connector": "email",
                            "tool": "send",
                            "effect": "read",
                            "destination": "fixture@example.test",
                        },
                    ],
                },
            )
            while (await call("grant.list"))[0]["status"] != "approved":
                assert time.monotonic() < deadline, "Fresh recovery approval timed out"
                await asyncio.sleep(0.02)
            copied = next(
                item
                for item in await call("artifact.list")
                if item["sha256"] == artifact["sha256"]
            )
            recovered_file = recovery["context"]["imported_files"][0]
            chunk = await call("workspace.read", file_id=recovered_file["file_id"])
            assert (
                hashlib.sha256(base64.b64decode(chunk["data_base64"])).hexdigest()
                == recovered_file["sha256"]
            )
            shared["sdk_recovered_generated_attachment"] = True
            assert copied["artifact_id"] != artifact["artifact_id"]
            assert (await call("artifact.read", artifact_id=copied["artifact_id"]))[
                "content"
            ] == "SYNTHETIC_RETAINED_DRAFT"
            await call(
                "app.reconcile_history",
                source_request_id="uncertain-email",
                request_id="archived-email",
            )
            checked = await app_result("archived-email")
            assert checked["state"] == "succeeded" and checked["effect"] == "read"
            assert (
                await call(
                    "app.reconcile_history",
                    source_request_id="uncertain-email",
                    request_id="archived-email",
                )
            ) == checked
            shared["uncertain_email_reconciled_without_replay"] = True
            shared["sdk_archived_read_only_probe"] = True
            shared["sdk_retention_task"] = followup["task_id"]
            deadline = time.monotonic() + 15
            while not shared.get("sdk_results_cleared"):
                assert time.monotonic() < deadline, "Trusted result clearing timed out"
                await asyncio.sleep(0.02)
            cleared = await call("app.status", request_id="archived-email")
            assert cleared["state"] == "succeeded" and cleared["result_cleared_at"]
            assert not cleared["text"] and not cleared["reference"]
            assert all(
                not r["text"] and not r["reference"]
                for r in (await call("task.recovery"))["context"]["app_receipts"]
            )
            assert (
                await call(
                    "app.reconcile_history",
                    source_request_id="uncertain-email",
                    request_id="archived-email",
                )
            ) == cleared
            replay = await client.call_tool(
                "steed_app_call_tool",
                {
                    **document_args,
                    "request_id": "recovery-document",
                    "artifact_id": copied["artifact_id"],
                },
            )
            assert (
                replay.isError
                and replay.structuredContent["error"]["code"]
                == "recovery_write_already_recorded"
            )
            await call("task.finish")
            shared["sdk_user_selected_recovery"] = True
            shared["sdk_recovery_write_not_replayed"] = True
            if shared["require_recipes"]:
                await recipe_workflow(call, shared, "sdk")
                await recipe_workflow(call, shared, "sdk", oauth=True)
                await recipe_workflow(call, shared, "sdk", oauth=True, pagination=True)
                await recipe_workflow(call, shared, "sdk", writing=True)
                await recipe_workflow(call, shared, "sdk", writing=True, nested=True)
                await recipe_workflow(call, shared, "sdk", writing=True, plan=True)


async def conversation_workflow(call, shared, label, task_id):
    """The desktop fixture sends text; an independent external client replies."""
    shared[label + "_conversation_task"] = task_id
    deadline = time.monotonic() + 15
    page = await call("conversation.read")
    while not page["messages"]:
        assert time.monotonic() < deadline, "Sidebar prompt did not arrive"
        await asyncio.sleep(0.02)
        page = await call("conversation.read")
    assert [m["text"] for m in page["messages"]] == [label + " explicit sidebar prompt"]
    assert (await call("grant.list")) == []
    partial = await call(
        "conversation.reply",
        message_id="sidebar-reply",
        text="External reply",
        state="streaming",
    )
    final = await call(
        "conversation.reply",
        message_id="sidebar-reply",
        text="External reply complete",
        revision=1,
    )
    assert final["created_sequence"] == partial["created_sequence"]
    assert (
        await call(
            "conversation.reply",
            message_id="sidebar-reply",
            text="External reply complete",
            revision=1,
        )
        == final
    )
    update = await call("conversation.read", after=page["cursor"])
    assert update["messages"] == [final]
    assert (await call("conversation.read", after=update["cursor"]))["messages"] == []
    shared[label + "_conversation_verified"] = True


def raw_workflow(request, notify, foreign_session, origin, shared):
    request(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "steed-raw-fixture", "version": "1"},
        },
    )
    notify()
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
    shared["raw_approval_task"] = result["structuredContent"]["result"]["task_id"]
    support = request(
        "tools/call", {"name": "steed_worker_capabilities", "arguments": {}}
    )
    assert (
        not support["isError"] and support["structuredContent"]["result"]["available"]
    )
    shared["raw_worker_setup_discovery"] = True
    denied = request("tools/call", {"name": "steed_session_open", "arguments": {}})
    assert (
        denied["isError"]
        and denied["structuredContent"]["error"]["code"] == "approval_required"
    )

    def call(command, **arguments):
        result = request(
            "tools/call",
            {"name": "steed_" + command.replace(".", "_"), "arguments": arguments},
        )
        assert not result["isError"], result
        return result["structuredContent"]["result"]

    async def conversation_call(command, **arguments):
        return call(command, **arguments)

    asyncio.run(
        conversation_workflow(
            conversation_call, shared, "raw", shared["raw_approval_task"]
        )
    )

    call(
        "grant.request",
        scope={
            "origins": [origin],
            "allow_private_network": True,
            "actions": [
                "navigate",
                "inspect",
                "read",
                "artifact",
                "app",
                "workspace",
                "worker",
                "download",
            ],
            "download_destinations": [shared["raw_workspace_destination"]],
            "max_operations": 500,
            "connector_permissions": [
                {
                    "connector": "documents",
                    "tool": "create",
                    "effect": "write",
                    "destination": "synthetic-workspace",
                },
                {
                    "connector": "email",
                    "tool": "send",
                    "effect": "send",
                    "destination": "fixture@example.test",
                },
            ],
        },
    )
    deadline = time.monotonic() + 15
    while call("grant.list")[0]["status"] != "approved":
        assert time.monotonic() < deadline
        time.sleep(0.02)

    async def workspace_call(command, **arguments):
        return call(command, **arguments)

    asyncio.run(
        workspace_workflow(
            workspace_call,
            shared["raw_workspace_destination"],
            shared["require_documents"],
        )
    )
    shared["raw_binary_export_and_staged_extension"] = True
    session = call("session.open")
    call(
        "session.navigate",
        session_id=session["session_id"],
        url=origin + "/raw-research",
    )
    call("page.wait", session_id=session["session_id"])
    snapshot = call("page.inspect", session_id=session["session_id"])
    assert snapshot["frames"][0]["text"]
    sources = [
        e["sequence"]
        for e in call("task.events", limit=1000)
        if e["kind"] == "page.inspected"
    ][-1:]
    draft = call(
        "artifact.store",
        title="Independent draft",
        content="RAW_SYNTHETIC_DRAFT",
        source_events=sources,
    )

    def app_result(request_id):
        deadline = time.monotonic() + 20
        while True:
            result = call("app.status", request_id=request_id)
            if result["state"] not in {"requested", "dispatched", "reconciling"}:
                return result
            assert time.monotonic() < deadline
            time.sleep(0.1)

    document_args = {
        "connector": "documents",
        "tool": "create",
        "request_id": "document-1",
        "destination": "synthetic-workspace",
        "artifact_id": draft["artifact_id"],
    }
    call("app.call_tool", **document_args)
    document = app_result("document-1")
    assert document["state"] == "succeeded"
    assert call("app.call_tool", **document_args)["reference"] == document["reference"]
    email = call(
        "artifact.store",
        title="Independent email",
        content="Synthetic document reference: " + document["reference"],
        source_events=sources,
    )
    email_args = {
        "connector": "email",
        "tool": "send",
        "request_id": "uncertain-email",
        "destination": "fixture@example.test",
        "artifact_id": email["artifact_id"],
    }
    call("app.call_tool", **email_args)
    assert app_result("uncertain-email")["state"] == "uncertain"
    assert call("app.call_tool", **email_args)["state"] == "uncertain"
    shared["independent_raw_app_workflow"] = True
    result = request("tools/call", {"name": "steed_task_finish", "arguments": {}})
    assert not result["isError"]
    followup = call("task.start", objective="Independent archived-task follow-up")
    shared["raw_recovery_task"] = followup["task_id"]
    call("task.recover")
    deadline = time.monotonic() + 15
    while call("task.recovery")["status"] == "requested":
        assert time.monotonic() < deadline, "Independent recovery picker timed out"
        time.sleep(0.02)
    recovery = call("task.recovery")
    assert recovery["source_task_id"] == shared["raw_approval_task"]
    assert len(recovery["context"]["imported_files"]) == 1
    call(
        "grant.request",
        scope={
            "actions": ["artifact", "app", "workspace"],
            "connector_permissions": [
                {
                    "connector": "email",
                    "tool": "send",
                    "effect": "send",
                    "destination": "fixture@example.test",
                },
                {
                    "connector": "email",
                    "tool": "send",
                    "effect": "read",
                    "destination": "fixture@example.test",
                },
            ],
        },
    )
    while call("grant.list")[0]["status"] != "approved":
        assert time.monotonic() < deadline, "Independent fresh approval timed out"
        time.sleep(0.02)
    copied = next(
        item for item in call("artifact.list") if item["sha256"] == email["sha256"]
    )
    recovered_file = recovery["context"]["imported_files"][0]
    chunk = call("workspace.read", file_id=recovered_file["file_id"])
    assert (
        hashlib.sha256(base64.b64decode(chunk["data_base64"])).hexdigest()
        == recovered_file["sha256"]
    )
    shared["raw_recovered_generated_attachment"] = True
    assert copied["artifact_id"] != email["artifact_id"]
    assert (
        "Synthetic document reference"
        in call("artifact.read", artifact_id=copied["artifact_id"])["content"]
    )
    call(
        "app.reconcile_history",
        source_request_id="uncertain-email",
        request_id="archived-email",
    )
    checked = app_result("archived-email")
    assert checked["state"] == "succeeded" and checked["effect"] == "read"
    shared["raw_archived_read_only_probe"] = True
    shared["raw_retention_task"] = followup["task_id"]
    deadline = time.monotonic() + 15
    while not shared.get("raw_results_cleared"):
        assert time.monotonic() < deadline, (
            "Independent trusted result clearing timed out"
        )
        time.sleep(0.02)
    cleared = call("app.status", request_id="archived-email")
    assert cleared["state"] == "succeeded" and cleared["result_cleared_at"]
    assert not cleared["text"] and not cleared["reference"]
    assert all(
        not r["text"] and not r["reference"]
        for r in call("task.recovery")["context"]["app_receipts"]
    )
    replay = request(
        "tools/call",
        {
            "name": "steed_app_call_tool",
            "arguments": {
                **email_args,
                "request_id": "recovery-email",
                "artifact_id": copied["artifact_id"],
            },
        },
    )
    assert (
        replay["isError"]
        and replay["structuredContent"]["error"]["code"]
        == "recovery_write_already_recorded"
    )
    call("task.finish")
    shared["raw_user_selected_recovery"] = True
    shared["raw_recovery_write_not_replayed"] = True
    if shared["require_recipes"]:
        asyncio.run(recipe_workflow(workspace_call, shared, "raw"))
        asyncio.run(recipe_workflow(workspace_call, shared, "raw", oauth=True))
        asyncio.run(
            recipe_workflow(workspace_call, shared, "raw", oauth=True, pagination=True)
        )
        asyncio.run(recipe_workflow(workspace_call, shared, "raw", writing=True))
        asyncio.run(
            recipe_workflow(workspace_call, shared, "raw", writing=True, nested=True)
        )
        asyncio.run(
            recipe_workflow(workspace_call, shared, "raw", writing=True, plan=True)
        )
    return True


def raw_client(discovery, foreign_session, origin, shared):
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

    def notify():
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()

    try:
        return raw_workflow(request, notify, foreign_session, origin, shared)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def raw_http_client(endpoint, foreign_session, foreign_http_session, origin, shared):
    """Independent HTTP JSON-RPC client, including credential-owner denial."""
    session_id = None
    sequence = 0
    headers = {
        "Authorization": "Bearer " + endpoint["tokens"]["raw"],
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }

    def request(method, params, notification=False):
        nonlocal sequence, session_id
        sequence += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = sequence
        current = dict(headers)
        if session_id:
            current["MCP-Session-Id"] = session_id
        with urlopen(
            Request(
                endpoint["url"], data=json.dumps(payload).encode(), headers=current
            ),
            timeout=15,
        ) as response:
            session_id = response.headers.get("MCP-Session-Id", session_id)
            if notification:
                assert response.status == 202
                return None
            value = json.load(response)
            assert value["id"] == sequence
            return value["result"]

    try:
        stolen = dict(headers, **{"MCP-Session-Id": foreign_http_session})
        for method in ("POST", "DELETE"):
            body = (
                b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
                if method == "POST"
                else None
            )
            try:
                urlopen(
                    Request(endpoint["url"], data=body, headers=stolen, method=method),
                    timeout=15,
                )
                raise AssertionError("Foreign HTTP session was accepted")
            except HTTPError as error:
                assert error.code == 404
                error.close()
        assert raw_workflow(
            request,
            lambda: request("notifications/initialized", {}, notification=True),
            foreign_session,
            origin,
            shared,
        )

        def call(name, **arguments):
            result = request(
                "tools/call",
                {"name": "steed_" + name.replace(".", "_"), "arguments": arguments},
            )
            assert not result["isError"], result
            return result["structuredContent"]["result"]

        for outcome in ("complete", "delete"):
            task = call(
                "task.start", objective="Independent HTTP read/termination fixture"
            )
            shared["raw_approval_task"] = task["task_id"]
            call(
                "grant.request",
                scope={
                    "origins": [origin],
                    "allow_private_network": True,
                    "duration_seconds": 120,
                    "actions": ["navigate", "inspect", "read"],
                },
            )
            deadline = time.monotonic() + 15
            while call("grant.list")[0]["status"] != "approved":
                assert time.monotonic() < deadline
                time.sleep(0.02)
            page = call("session.open")
            call(
                "session.navigate",
                session_id=page["session_id"],
                url=origin + "/mcp-start",
            )
            call("page.wait", session_id=page["session_id"])
            snapshot = call("page.inspect", session_id=page["session_id"])
            assert any(
                "Delegated workflow" in frame["text"] for frame in snapshot["frames"]
            )
            if outcome == "complete":
                call("task.finish", status="completed")
                shared["independent_raw_read_completed"] = True
            else:
                shared["http_delete_task"] = task["task_id"]
        return True
    finally:
        if session_id:
            with urlopen(
                Request(
                    endpoint["url"],
                    headers=dict(headers, **{"MCP-Session-Id": session_id}),
                    method="DELETE",
                ),
                timeout=15,
            ):
                pass


def check(transport="stdio", require_documents=False, require_recipes=False):
    case = TaskProfileTests("runTest")
    case.setUpClass()
    case.setUp()
    worker = None
    http_process = None
    oauth_patch = None
    shared, errors = (
        {"require_documents": require_documents, "require_recipes": require_recipes},
        [],
    )
    revoke, revoked = threading.Event(), threading.Event()
    try:
        window = BrowserWindow()
        case.windows.append(window)
        window._switch_to_profile("work")
        window.show()
        window.agent_sidebar_action.trigger()
        user = window.tabs[window.current_tab_index].page()
        loaded = []
        user.loadFinished.connect(loaded.append)
        user.setUrl(QUrl(case.origin + "/cookie"))
        case.wait_for(lambda: bool(loaded) and loaded[-1])
        user.loadFinished.disconnect(loaded.append)
        type(case).page_fixtures = {
            "/mcp-start": b'<p>Delegated workflow</p><a href="/mcp-form">Open form</a>',
            "/mcp-form": b'<form method="post" action="/mcp-submitted"><input aria-label="Message" name="message" value="FORM_PRIVATE"><input type="hidden" name="token" value="SYNTHETIC_TOKEN"><input aria-label="Confirm" type="checkbox" name="confirm" value="yes"><button name="decision" value="send">Send</button></form>',
            "/download-mcp": b"SYNTHETIC_DOWNLOAD_BYTES",
            "/mcp-upload-form": b'<form method="post" action="/mcp-upload-submitted" enctype="multipart/form-data"><input aria-label="Upload source" type="file" name="file"><button>Send file</button></form>',
        }
        type(case).post_responses = {
            "/mcp-submitted": (
                200,
                None,
                b'<p>Received</p><a href="/download-mcp">Download receipt</a>',
            ),
            "/mcp-user-post": (200, None, b"<p>User browsing continues</p>"),
            "/mcp-upload-submitted": (200, None, b"<p>Upload received</p>"),
        }
        window.agent_access_action.trigger()
        app_state = Path(case.directory.name) / "mock-apps.sqlite"
        register_mock_apps(case.service.connectors, app_state)
        if require_recipes:
            manager = case.registry.app_connections
            manager.store.secrets = MemorySecrets()
            oauth_http = OAuthHttp()
            write_http = WriteRecipeHttp()
            manager.recipe_http_factory = lambda selected: (
                write_http
                if selected.version >= 2
                else oauth_http
                if selected.authentication.kind == "oauth_pkce"
                else SyntheticRecipeHttp()
            )
            oauth_patch = patch(
                "services.recipe_oauth.desktop_authorization",
                return_value=(
                    "SYNTHETIC_CODE",
                    "SYNTHETIC_VERIFIER",
                    "http://127.0.0.1:12345/callback",
                ),
            )
            oauth_patch.start()
            recipe_client = Path(case.directory.name) / "recipe-client.json"
            recipe_client.write_text('{"client_id":"SYNTHETIC_CLIENT"}')
            oauth_source = parse_recipe(json.dumps(RECIPE_OAUTH_EXAMPLE).encode())
            paginated_source = parse_recipe(
                json.dumps(RECIPE_PAGINATED_EXAMPLE).encode()
            )
        upload_source = Path(case.directory.name) / "mcp-upload.txt"
        upload_source.write_bytes(b"SYNTHETIC_MCP_UPLOAD_BYTES")
        shared["raw_workspace_destination"] = str(
            Path(case.directory.name) / "raw-workspace.bin"
        )
        discovery = case.registry.agent_gateway.discovery
        endpoint = None
        if transport == "http":
            endpoint_file = discovery.with_name("agent-http.json")
            http_process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "agent_mcp.py"),
                    "--transport",
                    "http",
                    "--discovery",
                    str(discovery),
                    "--http-client",
                    "sdk",
                    "--http-client",
                    "raw",
                ],
                stdout=subprocess.DEVNULL,
            )
            case.wait_for(endpoint_file.exists)
            endpoint = json.loads(endpoint_file.read_text())
            shared["transport"] = "http"

        def run():
            try:
                asyncio.run(
                    sdk_client(
                        discovery,
                        case.origin,
                        shared,
                        revoke,
                        revoked,
                        str(Path(case.directory.name) / "mcp-result.bin"),
                        str(upload_source),
                        endpoint,
                    )
                )
                if endpoint is None:
                    shared["independent_raw_client"] = raw_client(
                        discovery, shared["session_id"], case.origin, shared
                    )

            except BaseException:
                errors.append(traceback.format_exc())

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 60
        user_posted = False
        user_edit_started = False
        while worker.is_alive() and time.monotonic() < deadline:
            case.app.processEvents()
            for label in ("sdk", "raw"):
                task_id = shared.get(label + "_conversation_task")
                if task_id and not shared.get(label + "_conversation_sent"):
                    sidebar = window.agent_sidebar
                    sidebar.tasks.setCurrentIndex(sidebar.tasks.findData(task_id))
                    assert sidebar.send.isEnabled()
                    sidebar.composer.setPlainText(label + " explicit sidebar prompt")
                    sidebar.send.click()
                    assert sidebar.composer.toPlainText() == ""
                    shared[label + "_conversation_sent"] = True
            for label, source in (
                ("sdk", shared.get("task_id")),
                ("raw", shared.get("raw_approval_task")),
            ):
                owner = shared.get(label + "_retention_task")
                if owner and not shared.get(label + "_results_cleared"):
                    case.service.clear_app_results(owner)
                    case.service.clear_app_results(source)
                    shared[label + "_results_cleared"] = True
            dialog = case.registry.task_approvals.dialog
            if dialog is not None and dialog.isVisible():
                assert not dialog.isModal()
                assert dialog.task_id in (
                    shared["task_id"],
                    shared.get("raw_approval_task"),
                    shared.get("sdk_recovery_task"),
                    shared.get("raw_recovery_task"),
                    shared.get("sdk_recipe_task"),
                    shared.get("raw_recipe_task"),
                )
                if isinstance(dialog, RecipeReviewDialog):
                    assert dialog.recipe in (
                        recipe(),
                        oauth_source,
                        paginated_source,
                        write_recipe(),
                        parse_recipe(json.dumps(RECIPE_NESTED_EXAMPLE).encode()),
                        parse_recipe(json.dumps(RECIPE_PLAN_EXAMPLE).encode()),
                    )
                    assert dialog.deny_button.isDefault()
                    dialog.grab().save(f"/tmp/steed-recipe-{transport}-preview.png")
                    dialog.allow_button.click()  # Trusted synthetic user only.
                    shared["recipe_review_popup"] = True
                elif isinstance(dialog, AppReviewDialog):
                    assert dialog.deny_button.isDefault()
                    assert dialog.windowTitle() == "Review App Action"
                    context = json.loads(dialog.review.context)
                    payload = json.loads(dialog.review.before_text)
                    nested = (
                        context["source_sha256"]
                        == parse_recipe(
                            json.dumps(RECIPE_NESTED_EXAMPLE).encode()
                        ).sha256
                    )
                    plan = (
                        context["source_sha256"]
                        == parse_recipe(json.dumps(RECIPE_PLAN_EXAMPLE).encode()).sha256
                    )
                    assert (
                        nested
                        or plan
                        or context["source_sha256"] == write_recipe().sha256
                    )
                    assert (
                        context["url"]
                        in (
                            "https://api.fixture.example/notes",
                            "https://api.fixture.example/messages",
                        )
                        or plan
                        and context["url"].startswith(
                            "https://api.fixture.example/notes/created-"
                        )
                        and context["url"].endswith("/comments")
                    )
                    nested = nested or plan and context["step"] == 1
                    assert (
                        payload["data"]["operation_id"]
                        if nested
                        else payload["operation_id"]
                    ) == context["operation_id"]
                    assert (
                        payload["data"]["body"]["text"]
                        if nested
                        else payload["comment"]
                        if plan
                        else payload["body"]
                    ) == dialog.review.after_text
                    if context["effect"] == "send":
                        assert payload["to"] == "recipient@example.test"
                    if (
                        dialog.review.request_id == "uncertain-create"
                        or context.get("plan_request_id") == "uncertain-create"
                    ):
                        write_http.fail_once = True
                    dialog.grab().save(
                        f"/tmp/steed-recipe-write-{transport}-preview.png"
                    )
                    dialog.approve_button.click()  # Trusted synthetic user only.
                    shared["recipe_payload_review_popup"] = True
                elif isinstance(dialog, WorkerApprovalDialog):
                    assert (
                        "generated.zip" in dialog.source.toPlainText()
                        or require_documents
                        and "report.pdf" in dialog.source.toPlainText()
                    )
                    assert dialog.deny_button.isDefault()
                    dialog.grab().save(f"/tmp/steed-worker-{transport}-preview.png")
                    dialog.allow_button.click()  # Only the trusted synthetic fixture.
                    shared["worker_package_popup"] = True
                elif isinstance(dialog, TaskRecoveryDialog):
                    original = (
                        shared["task_id"]
                        if dialog.task_id == shared.get("sdk_recovery_task")
                        else shared["raw_approval_task"]
                    )
                    dialog.source.setCurrentIndex(dialog.source.findData(original))
                    for index in range(dialog.artifacts.count()):
                        dialog.artifacts.item(index).setCheckState(
                            Qt.CheckState.Checked
                        )
                    for index in range(dialog.files.count()):
                        item = dialog.files.item(index)
                        if item.text().startswith("generated.zip "):
                            item.setCheckState(Qt.CheckState.Checked)
                    dialog.grab().save(f"/tmp/steed-recovery-{transport}-preview.png")
                    dialog.share.click()  # Trusted synthetic user selection only.
                else:
                    if dialog.task_id == shared["task_id"]:
                        dialog.remember.setCurrentIndex(1)
                    dialog.approve_button.click()  # Trusted synthetic fixture only.
                shared["approval_popup"] = True
            if require_recipes:
                manager = case.registry.app_connections
                for selected in (
                    recipe(),
                    oauth_source,
                    paginated_source,
                    write_recipe(),
                    parse_recipe(json.dumps(RECIPE_NESTED_EXAMPLE).encode()),
                    parse_recipe(json.dumps(RECIPE_PLAN_EXAMPLE).encode()),
                ):
                    if selected.provider not in manager.providers or manager.busy:
                        continue
                    accounts = [
                        a
                        for a in manager.store.list()
                        if a.provider == selected.provider and a.enabled
                    ]
                    if not accounts:
                        if selected.authentication.kind == "oauth_pkce":
                            manager.connect_account(
                                selected.provider,
                                recipe_client,
                                [t.name for t in selected.tools],
                            )
                        else:
                            manager.connect_recipe_account(
                                selected.provider,
                                TOKEN,
                                [t.name for t in selected.tools],
                            )
                    elif not shared.get(selected.provider + "_access_enabled"):
                        if any(
                            t.effect == "read" or t.reconciliation
                            for t in selected.tools
                        ):
                            manager.set_access(accounts[0].account_id, "read", True)
                        shared[selected.provider + "_access_enabled"] = True
            session_id = shared.get("session_id")
            watcher = case.service._watchers.get(session_id)
            if (
                shared.get("takeover_requested")
                and not shared.get("user_control_taken")
                and watcher is not None
            ):
                if not watcher.owns_control():
                    watcher.take_button.click()  # Trusted synthetic user only.
                elif not watcher._busy and not user_edit_started:
                    index = next(
                        i
                        for i in range(watcher.targets.count())
                        if watcher.targets.itemData(i).label == "Message"
                    )
                    watcher.targets.setCurrentIndex(index)
                    watcher.text.setText("SYNTHETIC_USER_HANDOFF")
                    watcher.action_buttons["enter"].click()
                    user_edit_started = True
                elif not watcher._busy and user_edit_started:
                    assert not watcher.error.text(), watcher.error.text()
                    assert (
                        watcher.view.page()
                        is case.service.backend._sessions[session_id].page
                    )
                    watcher.grab().save(f"/tmp/steed-watch-{transport}-preview.png")
                    shared["user_control_taken"] = True
            if (
                shared.get("return_requested")
                and not shared.get("user_control_returned")
                and watcher is not None
            ):
                watcher.return_button.click()
                assert not watcher.owns_control()
                shared["user_control_returned"] = True
            if shared.get("download_completed") and not shared.get(
                "receipt_ui_preview"
            ):
                panel = TaskDialog(case.service, window)
                row = next(
                    i
                    for i, (t, _) in enumerate(panel._rows)
                    if t.task_id == shared["task_id"]
                )
                panel.table.selectRow(row)
                panel.panels.setCurrentWidget(panel.downloads)
                panel.show()
                case.app.processEvents()
                assert panel.downloads.item(0, 1).text() == "completed"
                panel.grab().save(f"/tmp/steed-activity-{transport}-preview.png")
                panel.close()
                shared["receipt_ui_preview"] = True
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
        assert (
            shared["sdk_conversation_verified"] and shared["raw_conversation_verified"]
        )
        if require_recipes:
            assert shared["sdk_reviewed_recipe_writes"]
            assert shared["raw_reviewed_recipe_writes"]
            assert shared["sdk_reviewed_nested_recipe_writes"]
            assert shared["raw_reviewed_nested_recipe_writes"]
            assert shared["sdk_reviewed_plan_recipe_writes"]
            assert shared["raw_reviewed_plan_recipe_writes"]
            assert shared["recipe_payload_review_popup"]
            assert len(write_http.payloads) == 16
            assert len(write_http.operations) == 16
            assert sum("to" in payload for payload in write_http.payloads) == 2
            assert sum("data" in payload for payload in write_http.payloads) == 8
            assert sum("comment" in payload for payload in write_http.payloads) == 2
        case.wait_for(
            lambda: any(path == "/mcp-user-post" for path, _, _ in case.posted)
        )
        form = next(body for path, body, _ in case.posted if path == "/mcp-submitted")
        assert b"message=SYNTHETIC_MCP_VALUE" in form and b"confirm=yes" in form
        uploaded = next(
            body for path, body, _ in case.posted if path == "/mcp-upload-submitted"
        )
        assert b"SYNTHETIC_MCP_UPLOAD_BYTES" in uploaded
        assert b"SYNTHETIC_CHANGED_AFTER_COPY" not in uploaded
        assert any(
            path == "/mcp-form" and "task_cookie=fixture" in cookie
            for path, cookie in case.requests
        )
        assert user.profile() is case.profile
        if endpoint is not None:
            case.wait_for(
                lambda: (
                    case.service.store.get(shared["http_delete_task"]).status
                    == "cancelled"
                )
            )
            shared["http_delete_cancelled_owned_task"] = True
        assert not case.service._sessions
        shared["shared_cookie"] = True
        shared["ordinary_user_post"] = True
        shared["task_pages_disposed"] = True
        with sqlite3.connect(app_state) as state:
            results = state.execute(
                "SELECT tool, destination, body FROM results ORDER BY tool"
            ).fetchall()
        assert len(results) == 4, results
        documents = [row for row in results if row[0] == "document_create"]
        emails = [row for row in results if row[0] == "email_send"]
        assert len(documents) == len(emails) == 2
        assert {row[1] for row in documents} == {"synthetic-workspace"}
        assert {row[2] for row in documents} == {
            "SYNTHETIC_RETAINED_DRAFT",
            "RAW_SYNTHETIC_DRAFT",
        }
        assert {row[1] for row in emails} == {"fixture@example.test"}
        assert all(
            row[2].startswith("Synthetic document reference: document-")
            for row in emails
        )
        print(json.dumps(shared, indent=2))
    finally:
        case.registry.set_agent_access(False)
        if http_process is not None:
            try:
                http_process.wait(timeout=6)
                assert http_process.returncode == 0
                assert not endpoint_file.exists()
            finally:
                if http_process.poll() is None:
                    http_process.kill()
                    http_process.wait(timeout=5)
        if worker is not None:
            case.wait_for(lambda: not worker.is_alive())
            worker.join()
        case.tearDown()
        case.tearDownClass()
        if oauth_patch is not None:
            oauth_patch.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument(
        "--recipes",
        action="store_true",
        help="Review synthetic recipes, read their APIs, approve creates/sends and reconcile without replay through both protocol clients",
    )
    parser.add_argument(
        "--documents",
        action="store_true",
        help="Require the installed document bundle and generate PDF/XLSX/PNG through both protocol clients",
    )
    args = parser.parse_args()
    check(args.transport, args.documents, args.recipes)
