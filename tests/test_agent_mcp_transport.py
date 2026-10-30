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


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    """A dropped connection comes back on the next call, never replaying one."""

    def connection(self):
        connection = BrowserConnection(Path("/tmp/synthetic-unused-discovery"))
        self.wires = []
        self.replies = {}

        async def connect():
            connection.reader = asyncio.StreamReader()
            wire = Wire(connection.reader)
            wire.reply = lambda request=None: connection.reader.feed_data(
                (
                    json.dumps(
                        {
                            "id": (request or wire.calls[-1])["id"],
                            **self.replies.get(
                                (request or wire.calls[-1])["tool"],
                                {"result": (request or wire.calls[-1])["tool"]},
                            ),
                        }
                    )
                    + "\n"
                ).encode()
            )
            wire.automatic = True
            connection.writer = wire
            self.wires.append(wire)

        connection.connect = connect
        return connection

    async def started(self, connection):
        token = "a" * 64
        self.replies["steed_task_start"] = {
            "result": {"task_id": "task-1", "resume_token": token}
        }
        await connection.connect()
        await connection.call("steed_task_start", {})
        self.assertEqual(connection.resume_token, token)
        return token

    async def lose(self, connection):
        wire = self.wires[-1]
        wire.automatic = False
        call = asyncio.create_task(connection.call("steed_page_act", {}))
        while not wire.calls or wire.calls[-1]["tool"] != "steed_page_act":
            await asyncio.sleep(0)
        connection.reader.feed_eof()
        with self.assertRaises(RuntimeError):
            await call

    async def test_next_call_reconnects_and_resumes_the_task(self):
        connection = self.connection()
        token = await self.started(connection)
        await self.lose(connection)
        self.replies["steed_task_resume"] = {
            "result": {"task_id": "task-1", "resume_token": "b" * 64}
        }
        reply = await connection.call("steed_task_status", {})
        fresh = self.wires[-1]
        self.assertEqual(len(self.wires), 2)
        self.assertEqual(
            [c["tool"] for c in fresh.calls],
            ["steed_task_resume", "steed_task_status"],
        )
        self.assertEqual(fresh.calls[0]["arguments"], {"resume_token": token})
        self.assertEqual(reply["result"], "steed_task_status")
        self.assertTrue(reply["connection"]["task_resumed"])
        self.assertEqual(reply["connection"]["task_id"], "task-1")
        self.assertEqual(connection.resume_token, "b" * 64)
        # The failed call is never sent again.
        self.assertNotIn("steed_page_act", [c["tool"] for c in fresh.calls])

    async def test_expired_task_reconnects_without_resuming(self):
        connection = self.connection()
        await self.started(connection)
        await self.lose(connection)
        self.replies["steed_task_resume"] = {"error": {"code": "invalid_request"}}
        reply = await connection.call("steed_task_status", {})
        self.assertFalse(reply["connection"]["task_resumed"])
        self.assertIn("Start a new task", reply["connection"]["message"])
        self.assertIsNone(connection.resume_token)

    async def test_calls_queued_before_the_loss_are_not_sent(self):
        connection = self.connection()
        await self.started(connection)
        wire = self.wires[-1]
        wire.automatic = False
        first = asyncio.create_task(connection.call("steed_page_act", {}))
        while not wire.calls or wire.calls[-1]["tool"] != "steed_page_act":
            await asyncio.sleep(0)
        queued = asyncio.create_task(connection.call("steed_page_submit", {}))
        while connection.waiting < 2:
            await asyncio.sleep(0)
        connection.reader.feed_eof()
        with self.assertRaises(RuntimeError):
            await first
        with self.assertRaises(RuntimeError):
            await queued
        self.assertEqual(len(self.wires), 1)

    async def test_unreachable_browser_reports_an_unsent_call(self):
        connection = self.connection()
        await self.started(connection)
        await self.lose(connection)

        async def unreachable():
            raise FileNotFoundError("no socket")

        connection.connect = unreachable
        reply = await connection.call("steed_task_status", {})
        self.assertEqual(reply["error"]["code"], "browser_unavailable")
        # The token is spent only by an actual resume attempt.
        self.assertEqual(connection.resume_token, "a" * 64)

    async def test_bound_chat_connection_does_not_reconnect(self):
        connection = self.connection()
        connection.binding_file = Path("/tmp/synthetic-binding")
        await self.started(connection)
        await self.lose(connection)
        with self.assertRaises(RuntimeError):
            await connection.call("steed_task_status", {})
        self.assertEqual(len(self.wires), 1)
