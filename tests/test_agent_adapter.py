"""Adapter discovery and uncertain outcomes fail closed without automatic retry."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agent_mcp import BrowserConnection


class AgentAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_epoch_cannot_connect_to_a_later_browser_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            path.write_text(
                json.dumps({"version": 1, "socket": "/unused", "token": "new-run"})
            )
            path.chmod(0o600)
            connection = BrowserConnection(path)
            connection.expected_epoch = "previous-run"
            with patch(
                "asyncio.open_unix_connection", new_callable=AsyncMock
            ) as opened:
                with self.assertRaises(RuntimeError):
                    await connection.connect()
                opened.assert_not_called()

    async def test_waiting_call_is_not_dispatched_until_the_active_call_releases(self):
        connection = BrowserConnection("unused")
        writer = Mock(drain=AsyncMock())
        connection.writer = writer
        connection.reader = Mock(
            readline=AsyncMock(return_value=b'{"id":1,"result":{"accepted":true}}\n')
        )
        async with connection.lock:
            waiting = asyncio.create_task(
                connection.call("steed_task_start", {"objective": "overlap"})
            )
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            writer.write.assert_not_called()
        result = await waiting
        self.assertEqual(result, {"result": {"accepted": True}})
        writer.write.assert_called_once()
        self.assertEqual(connection.waiting, 0)

    async def test_insecure_or_symlinked_discovery_never_connects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            path.write_text(
                json.dumps({"version": 1, "socket": "/unused", "token": "synthetic"})
            )
            path.chmod(0o644)
            connection = BrowserConnection(path)
            with patch(
                "asyncio.open_unix_connection", new_callable=AsyncMock
            ) as opened:
                with self.assertRaises(RuntimeError):
                    await connection.connect()
                path.chmod(0o600)
                alias = Path(directory) / "alias.json"
                alias.symlink_to(path)
                with self.assertRaises(OSError):
                    await BrowserConnection(alias).connect()
                opened.assert_not_called()

    async def test_invalid_discovery_shapes_never_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            for value in (
                [],
                None,
                {},
                {"version": 2},
                {"version": 1, "token": 123, "socket": "/unused"},
            ):
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    path.chmod(0o600)
                    with patch(
                        "asyncio.open_unix_connection", new_callable=AsyncMock
                    ) as opened:
                        with self.assertRaises(ValueError):
                            await BrowserConnection(path).connect()
                        opened.assert_not_called()

    async def test_cancelled_or_lost_reply_closes_without_retry(self):
        for failure in (asyncio.CancelledError(), RuntimeError("closed")):
            with self.subTest(failure=type(failure).__name__):
                connection = BrowserConnection("unused")
                writer = Mock()
                writer.drain = AsyncMock()
                writer.wait_closed = AsyncMock()
                connection.writer = writer
                connection.reader = Mock()
                connection.reader.readline = AsyncMock(side_effect=failure)
                with self.assertRaises(type(failure)):
                    await connection.call(
                        "steed_page_submit", {"session_id": "synthetic"}
                    )
                writer.write.assert_called_once()
                writer.close.assert_called_once()
                self.assertIsNone(connection.writer)


class LateBrowserStartTests(unittest.IsolatedAsyncioTestCase):
    """A plain client may start before Steed; a pinned one may not."""

    async def test_plain_client_starts_without_steed_and_reports_unsent_calls(self):
        from agent_mcp import create_server

        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "agent-access.json"
            server = create_server(missing)
            async with server.lifespan(server) as connection:
                self.assertIsNone(connection.writer)
                reply = await connection.call("steed_task_status", {})
                self.assertEqual(reply["error"]["code"], "browser_unavailable")

    async def test_run_pinned_or_bound_client_fails_to_start_without_steed(self):
        from agent_mcp import create_server

        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "agent-access.json"
            for server in (
                create_server(missing, expected_epoch="run"),
                create_server(missing, binding_file=Path(directory) / "binding"),
            ):
                with self.assertRaises((OSError, ValueError, RuntimeError)):
                    async with server.lifespan(server):
                        pass
