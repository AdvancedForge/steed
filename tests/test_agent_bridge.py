"""Pairing links for cloud agents: secret path, any host, always asked."""

import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_agent_http as http_fixture
import test_task_services as fixtures

from agent_mcp import BrowserConnection
from browser import access_levels
from services.agent_bridge import AgentBridge, clean_base, read_endpoint
from services.agent_gateway import AgentGateway
from services.agent_http import create_http_app

SECRET = "s" * 43


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP SDK not installed")
class PairingAppTests(unittest.IsolatedAsyncioTestCase):
    """The pairing app on its own, with a stand-in browser connection."""

    setUp = http_fixture.AgentHTTPTests.setUp
    tearDown = http_fixture.AgentHTTPTests.tearDown

    @asynccontextmanager
    async def paired(self):
        import httpx

        def connection(_):
            result = BrowserConnection(self.discovery)
            result.writer = Mock()
            result.writer.drain = AsyncMock()
            result.writer.wait_closed = AsyncMock()
            result.connect = AsyncMock()
            self.connections.append(result)
            return result

        with patch("agent_mcp.BrowserConnection", side_effect=connection):
            app = create_http_app(
                self.discovery, {}, "127.0.0.1:8765", path_secret=SECRET
            )
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="https://abc.trycloudflare.com",
                ) as client:
                    yield client

    async def test_only_the_secret_path_works_from_any_host_and_never_a_website(self):
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "cloud", "version": "1"},
            },
        }
        headers = {"Accept": "application/json, text/event-stream"}
        async with self.paired() as client:
            for path, extra, expected in (
                ("/mcp", {}, 404),
                ("/wrong/mcp", {}, 404),
                (f"/{SECRET}/mcp", {"Origin": "https://evil.invalid"}, 403),
                (f"/{SECRET}/mcp", {}, 200),
            ):
                with self.subTest(path=path, extra=extra):
                    response = await client.post(
                        path, headers={**headers, **extra}, json=initialize
                    )
                    self.assertEqual(response.status_code, expected, response.text)
        # The cloud agent's connection tells Steed it is remote.
        self.assertTrue(self.connections and all(c.remote for c in self.connections))

    def test_links_and_endpoint_files(self):
        self.assertEqual(
            clean_base("https://steed.example.com/"), "https://steed.example.com"
        )
        for bad in (
            "http://steed.example.com",
            "https://u:p@x.com",
            "https://x.com?a=1",
        ):
            with self.assertRaises(ValueError):
                clean_base(bad)
        path = self.discovery.with_name("bridge.json")
        path.write_text(json.dumps({"port": 4242, "path": f"/{SECRET}/mcp"}))
        path.chmod(0o644)
        with self.assertRaises(ValueError):  # Readable by others: refused.
            read_endpoint(path)
        path.chmod(0o600)
        self.assertEqual(read_endpoint(path), (4242, f"/{SECRET}/mcp"))


class Config(dict):
    def set(self, key, value):
        self[key] = value


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP SDK not installed")
class BridgeTests(unittest.TestCase):
    """A real pairing link (your own address), a real cloud request."""

    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    wait_for = fixtures.TaskServiceTests.wait_for

    def setUp(self):
        fixtures.TaskServiceTests.setUp(self)
        self.gateway = AgentGateway(self.service, self.directory.name)
        self.registry = SimpleNamespace(
            agent_gateway=self.gateway, set_agent_access=lambda _: None
        )
        self.bridge = AgentBridge(self.registry)
        self.addCleanup(self.bridge.stop)

    def tearDown(self):
        self.bridge.stop()
        self.gateway.close()
        fixtures.TaskServiceTests.tearDown(self)

    def post(self, url, payload, session=None):
        """An HTTP call in a thread, so Steed's own loop keeps answering."""
        result = {}

        def run():
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "steed.example.test",
            }
            if session:
                headers["Mcp-Session-Id"] = session
            request = urllib.request.Request(
                url, json.dumps(payload).encode(), headers, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    result["status"] = response.status
                    result["session"] = response.headers.get("mcp-session-id")
                    result["body"] = response.read().decode()
            except urllib.error.HTTPError as error:
                result["status"] = error.code

        thread = threading.Thread(target=run)
        thread.start()
        self.wait_for(lambda: not thread.is_alive())
        return result

    def test_a_cloud_agent_reaches_steed_and_is_always_asked(self):
        self.bridge.start("own", "https://steed.example.test/")
        self.wait_for(lambda: self.bridge.state in ("on", "failed"))
        self.assertEqual(self.bridge.state, "on", self.bridge.message)
        self.assertTrue(self.bridge.link.startswith("https://steed.example.test/"))
        self.assertTrue(self.bridge.link.endswith("/mcp"))
        local = f"http://127.0.0.1:{self.bridge.port}" + self.bridge.path
        opened = self.post(
            local,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "cloud", "version": "1"},
                },
            },
        )
        self.assertEqual(opened["status"], 200)
        session = opened["session"]
        self.post(
            local, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session
        )
        started = self.post(
            local,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "steed_task_start",
                    "arguments": {"objective": "Cloud research"},
                },
            },
            session,
        )
        self.assertEqual(started["status"], 200, started)
        self.wait_for(lambda: self.service.remote_tasks)
        task = next(iter(self.service.remote_tasks))
        self.assertIn("agent.remote", [e.kind for e in self.service.store.events(task)])
        # Steed's own end needs no address but its own, and a wrong path fails.
        self.assertEqual(
            self.post(f"http://127.0.0.1:{self.bridge.port}/mcp", {})["status"], 404
        )
        self.bridge.stop("done")
        self.assertEqual(self.bridge.state, "off")
        self.assertEqual(self.bridge.link, "")


class CloudTasksAlwaysAskTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    setUp = fixtures.TaskServiceTests.setUp
    tearDown = fixtures.TaskServiceTests.tearDown

    def test_access_settings_and_auto_approval_never_cover_a_cloud_task(self):
        config = Config()
        self.service.profiles = SimpleNamespace(
            config=config,
            validate_task_profile=lambda _: None,
            release_task_profiles=lambda *_: None,
        )
        access_levels.apply_preset(config, "agents", "everything")
        self.service.set_auto_approval(True)
        task = self.service._create("Cloud work")
        self.service.mark_remote(task.task_id)
        grant = self.service._request_grant(
            task.task_id,
            {
                "origins": ["https://example.com"],
                "actions": ["navigate", "read"],
            },
        )
        self.assertEqual(grant.status, "requested")
        careful = self.service._create("Work in an ask-everything workspace")
        self.service.mark_always_ask(careful.task_id)
        grant = self.service._request_grant(
            careful.task_id,
            {"origins": ["https://example.com"], "actions": ["navigate", "read"]},
        )
        self.assertEqual(grant.status, "requested")
        self.assertIn(
            "agent.always_ask",
            [e.kind for e in self.service.store.events(careful.task_id)],
        )
        local = self.service._create("Local work")
        grant = self.service._request_grant(
            local.task_id,
            {"origins": ["https://example.com"], "actions": ["navigate", "read"]},
        )
        self.assertEqual(grant.status, "approved")
        self.service.set_auto_approval(False)


if __name__ == "__main__":
    unittest.main()
