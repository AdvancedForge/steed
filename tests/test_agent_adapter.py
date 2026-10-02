"""Adapter discovery and uncertain outcomes fail closed without automatic retry."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agent_mcp import BrowserConnection


class AgentAdapterTests(unittest.IsolatedAsyncioTestCase):
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
