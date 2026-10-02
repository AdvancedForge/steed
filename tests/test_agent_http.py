"""HTTP authentication, ownership and lifecycle without real browser state."""

import asyncio
import importlib.util
import json
import stat
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agent_mcp import BrowserConnection
from services.agent_http import create_http_app, publish_endpoint
from services.agent_tools import REQUEST_LIMIT


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP SDK not installed")
class AgentHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.discovery = Path(self.directory.name) / "browser.json"
        self.discovery.write_text(
            json.dumps({"version": 1, "socket": "/unused", "token": "synthetic"})
        )
        self.discovery.chmod(0o600)
        self.connections = []
        self.pending = asyncio.Event()

    def tearDown(self):
        self.directory.cleanup()

    @asynccontextmanager
    async def running(self, idle_timeout=60):
        import httpx

        def connection(_):
            index = len(self.connections)
            result = BrowserConnection(self.discovery)
            result.writer = Mock()
            result.writer.drain = AsyncMock()
            result.writer.wait_closed = AsyncMock()
            result.connect = AsyncMock()
            writer = result.writer

            async def reply():
                payload = json.loads(writer.write.call_args.args[0])
                if payload["tool"] == "steed_page_wait":
                    self.pending.set()
                    await asyncio.Event().wait()
                return (
                    json.dumps(
                        {"id": payload["id"], "result": {"connection": index}}
                    ).encode()
                    + b"\n"
                )

            result.reader = Mock()
            result.reader.readline = AsyncMock(side_effect=reply)
            self.connections.append((result, writer))
            return result

        with patch("agent_mcp.BrowserConnection", side_effect=connection):
            app = create_http_app(
                self.discovery,
                {"one": "synthetic-one", "two": "synthetic-two"},
                "127.0.0.1:8765",
                idle_timeout=idle_timeout,
            )
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8765",
                ) as client:
                    yield client

    async def rpc(
        self, client, method, params, session=None, token="synthetic-one", request_id=1
    ):
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }
        if session:
            headers["MCP-Session-Id"] = session
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        return await client.post("/mcp", headers=headers, json=payload)

    async def initialize(self, client, token="synthetic-one"):
        response = await self.rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
            token=token,
        )
        self.assertEqual(response.status_code, 200, response.text)
        session = response.headers["mcp-session-id"]
        notified = await self.rpc(
            client, "notifications/initialized", {}, session, token, request_id=None
        )
        self.assertEqual(notified.status_code, 202)
        return session

    async def test_all_methods_require_credentials_and_reject_origins_and_hosts(self):
        async with self.running() as client:
            for method in ("POST", "GET", "DELETE"):
                for headers, expected in (
                    ({}, 401),
                    ({"Authorization": "Bearer wrong"}, 401),
                    (
                        {
                            "Authorization": "Bearer synthetic-one",
                            "Origin": "https://evil.invalid",
                        },
                        403,
                    ),
                    ({"Authorization": "Bearer synthetic-one", "Origin": ""}, 403),
                    (
                        {
                            "Authorization": "Bearer synthetic-one",
                            "Host": "evil.invalid",
                        },
                        421,
                    ),
                ):
                    with self.subTest(method=method, headers=headers):
                        response = await client.request(method, "/mcp", headers=headers)
                        self.assertEqual(response.status_code, expected)
            self.assertEqual(self.connections, [])

    async def test_each_session_has_its_own_connection_and_credential_owner(self):
        async with self.running() as client:
            first = await self.initialize(client)
            second = await self.initialize(client, "synthetic-two")
            params = {
                "name": "steed_task_start",
                "arguments": {"objective": "Synthetic task"},
            }
            self.assertEqual(
                (await self.rpc(client, "tools/call", params, first)).json()["result"][
                    "structuredContent"
                ]["result"]["connection"],
                0,
            )
            self.assertEqual(
                (
                    await self.rpc(
                        client, "tools/call", params, second, "synthetic-two"
                    )
                ).json()["result"]["structuredContent"]["result"]["connection"],
                1,
            )
            stolen = await self.rpc(client, "tools/list", {}, first, "synthetic-two")
            self.assertEqual(stolen.status_code, 404)
            deleted = await client.delete(
                "/mcp",
                headers={
                    "Authorization": "Bearer synthetic-two",
                    "MCP-Session-Id": first,
                },
            )
            self.assertEqual(deleted.status_code, 404)
            self.assertFalse(self.connections[0][1].close.called)

    async def test_delete_closes_only_its_connection_and_old_id_is_rejected(self):
        async with self.running() as client:
            first, second = await self.initialize(client), await self.initialize(client)
            deleted = await client.delete(
                "/mcp",
                headers={
                    "Authorization": "Bearer synthetic-one",
                    "MCP-Session-Id": first,
                },
            )
            self.assertEqual(deleted.status_code, 200)
            for _ in range(20):
                if self.connections[0][1].close.called:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(self.connections[0][1].close.called)
            self.assertFalse(self.connections[1][1].close.called)
            self.assertEqual(
                (await self.rpc(client, "tools/list", {}, first)).status_code, 404
            )
            self.assertEqual(
                (await self.rpc(client, "tools/list", {}, second)).status_code, 200
            )
        self.assertTrue(self.connections[1][1].close.called)

    async def test_idle_session_closes_connection_and_cannot_resume(self):
        async with self.running(idle_timeout=0.08) as client:
            session = await self.initialize(client)
            await asyncio.sleep(0.16)
            self.assertEqual(
                (await self.rpc(client, "tools/list", {}, session)).status_code, 404
            )
            self.assertTrue(self.connections[0][1].close.called)

    async def test_get_stream_keeps_session_alive_and_disconnect_does_not_cancel_it(
        self,
    ):
        async with self.running(idle_timeout=0.08) as client:
            session = await self.initialize(client)
            stream = asyncio.create_task(
                client.get(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer synthetic-one",
                        "MCP-Session-Id": session,
                        "Accept": "text/event-stream",
                    },
                )
            )
            try:
                await asyncio.sleep(0.16)
                self.assertFalse(self.connections[0][1].close.called)
            finally:
                stream.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await stream
            self.assertEqual(
                (await self.rpc(client, "tools/list", {}, session)).status_code, 200
            )
            self.assertFalse(self.connections[0][1].close.called)

    async def test_cancel_notification_closes_uncertain_browser_operation(self):
        async with self.running() as client:
            session = await self.initialize(client)
            operation = asyncio.create_task(
                self.rpc(
                    client,
                    "tools/call",
                    {
                        "name": "steed_page_wait",
                        "arguments": {"session_id": "synthetic-page"},
                    },
                    session,
                    request_id=9,
                )
            )
            await asyncio.wait_for(self.pending.wait(), 2)
            cancellation = await self.rpc(
                client,
                "notifications/cancelled",
                {"requestId": 9, "reason": "Fixture cancellation"},
                session,
                request_id=None,
            )
            self.assertEqual(cancellation.status_code, 202)
            await asyncio.wait_for(operation, 2)
            self.assertTrue(self.connections[0][1].close.called)
            self.assertIsNone(self.connections[0][0].writer)

    async def test_bounds_and_browser_access_rotation_are_enforced(self):
        async with self.running() as client:
            headers = {
                "Authorization": "Bearer synthetic-one",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            response = await client.post(
                "/mcp", headers=headers, content=b"x" * (REQUEST_LIMIT + 1)
            )
            self.assertEqual(response.status_code, 413)
            for _ in range(4):
                await self.initialize(client)
            self.assertEqual(
                (await self.rpc(client, "initialize", {})).status_code, 503
            )
            config = json.loads(self.discovery.read_text())
            config["token"] = "new-browser-run"
            self.discovery.write_text(json.dumps(config))
            self.assertEqual(
                (await self.rpc(client, "tools/list", {})).status_code, 503
            )
            self.assertEqual(len(self.connections), 4)

    def test_endpoint_credentials_are_private_and_existing_files_are_preserved(self):
        path = Path(self.directory.name) / "http.json"
        publish_endpoint(path, {"token": "synthetic"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            publish_endpoint(path, {"token": "replacement"})
        self.assertEqual(json.loads(path.read_text()), {"token": "synthetic"})
        self.assertCountEqual(
            list(Path(self.directory.name).iterdir()), [self.discovery, path]
        )
