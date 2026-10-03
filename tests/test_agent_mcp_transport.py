"""Parallel native calls stay ordered without retrying uncertain operations."""

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_mcp import BrowserConnection


class Wire:
    def __init__(self, reader, *, automatic=False):
        self.reader = reader
        self.automatic = automatic
        self.calls = []
        self.closed = False

    def write(self, raw):
        request = json.loads(raw)
        self.calls.append(request)
        if self.automatic:
            asyncio.get_running_loop().call_soon(self.reply, request)

    def reply(self, request=None):
        request = request or self.calls[-1]
        self.reader.feed_data(
            (
                json.dumps({"id": request["id"], "result": request["tool"]}) + "\n"
            ).encode()
        )

    async def drain(self):
        pass

    def close(self):
        self.closed = True
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


class AgentMcpTransportTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, *, automatic=False):
        connection = BrowserConnection(Path("/tmp/synthetic-unused-discovery"))
        connection.reader = asyncio.StreamReader()
        wire = Wire(connection.reader, automatic=automatic)
        connection.writer = wire
        return connection, wire

    async def wait_for(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0)

        await asyncio.wait_for(wait(), 2)

    async def test_parallel_discovery_is_serialized_with_matching_results(self):
        connection, wire = self.connection(automatic=True)
        names = [
            "steed_task_capabilities",
            "steed_browser_context",
            "steed_library_folders",
            "steed_app_list_tools",
        ]
        replies = await asyncio.gather(*(connection.call(n, {}) for n in names))
        self.assertEqual(replies, [{"result": n} for n in names])
        self.assertEqual([c["id"] for c in wire.calls], [1, 2, 3, 4])
        self.assertEqual(connection.waiting, 0)

    async def test_queued_cancellation_preserves_active_connection(self):
        connection, wire = self.connection()
        owner = asyncio.create_task(connection.call("owner", {}))
        await self.wait_for(lambda: wire.calls)
        queued = asyncio.create_task(connection.call("cancelled", {}))
        await self.wait_for(lambda: connection.waiting == 2)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertFalse(wire.closed)
        wire.reply()
        self.assertEqual(await owner, {"result": "owner"})
        wire.automatic = True
        self.assertEqual(await connection.call("next", {}), {"result": "next"})
        self.assertEqual([c["tool"] for c in wire.calls], ["owner", "next"])

    async def test_cancelled_dispatched_call_closes_without_replaying_queue(self):
        connection, wire = self.connection()
        owner = asyncio.create_task(connection.call("uncertain_write", {}))
        await self.wait_for(lambda: wire.calls)
        queued = asyncio.create_task(connection.call("later_write", {}))
        await self.wait_for(lambda: connection.waiting == 2)
        owner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await owner
        with self.assertRaises(RuntimeError):
            await queued
        self.assertTrue(wire.closed)
        self.assertEqual(len(wire.calls), 1)
        self.assertEqual(connection.waiting, 0)

    async def test_queue_timeout_is_undispatched_and_keeps_owner_alive(self):
        connection, wire = self.connection()
        owner = asyncio.create_task(connection.call("owner", {}))
        await self.wait_for(lambda: wire.calls)
        with patch("agent_mcp.QUEUE_TIMEOUT", 0.01):
            reply = await connection.call("timed_out", {})
        self.assertEqual(reply["error"]["code"], "request_queue_timeout")
        self.assertIn("not dispatched", reply["error"]["message"])
        self.assertFalse(wire.closed)
        self.assertEqual(len(wire.calls), 1)
        wire.reply()
        await owner

    async def test_queue_limit_is_bounded_and_does_not_dispatch_overflow(self):
        connection, wire = self.connection()
        owner = asyncio.create_task(connection.call("owner", {}))
        await self.wait_for(lambda: wire.calls)
        queued = asyncio.create_task(connection.call("queued", {}))
        await self.wait_for(lambda: connection.waiting == 2)
        with patch("agent_mcp.CALL_LIMIT", 2):
            reply = await connection.call("overflow", {})
        self.assertEqual(reply["error"]["code"], "request_queue_full")
        wire.automatic = True
        wire.reply()
        self.assertEqual(await owner, {"result": "owner"})
        self.assertEqual(await queued, {"result": "queued"})
        self.assertEqual(len(wire.calls), 2)

    async def test_mismatched_wire_reply_closes_and_does_not_replay(self):
        connection, wire = self.connection()
        owner = asyncio.create_task(connection.call("uncertain_write", {}))
        await self.wait_for(lambda: wire.calls)
        queued = asyncio.create_task(connection.call("later", {}))
        await self.wait_for(lambda: connection.waiting == 2)
        connection.reader.feed_data(b'{"id":999,"result":{}}\n')
        with self.assertRaises(RuntimeError):
            await owner
        with self.assertRaises(RuntimeError):
            await queued
        self.assertTrue(wire.closed)
        self.assertEqual(len(wire.calls), 1)
