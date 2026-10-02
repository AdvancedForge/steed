"""Private transport ownership and failure paths using temporary task services."""

import json
import os
import stat
import unittest

import test_task_services as fixtures
from PyQt6.QtNetwork import QLocalSocket

from services.agent_gateway import AgentGateway
from services.agent_tools import REQUEST_LIMIT


class AgentGatewayTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    wait_for = fixtures.TaskServiceTests.wait_for
    command = fixtures.TaskServiceTests.command

    def setUp(self):
        fixtures.TaskServiceTests.setUp(self)
        self.gateway = AgentGateway(self.service, self.directory.name)
        self.sockets = []
        self.sequence = 0

    def tearDown(self):
        self.gateway.close()
        for socket in self.sockets:
            socket.abort()
        fixtures.TaskServiceTests.tearDown(self)

    def receive(self, socket):
        self.wait_for(lambda: socket.canReadLine())
        return json.loads(bytes(socket.readLine()))

    def connect(self, token=None):
        socket = QLocalSocket()
        self.sockets.append(socket)
        socket.connectToServer(self.gateway.socket_path)
        self.wait_for(
            lambda: socket.state() == QLocalSocket.LocalSocketState.ConnectedState
        )
        socket.write(
            json.dumps({"token": token or self.gateway.token}).encode() + b"\n"
        )
        if token is None:
            self.assertEqual(self.receive(socket), {"connected": True})
        return socket

    def call(self, socket, name, **arguments):
        self.sequence += 1
        socket.write(
            json.dumps(
                {
                    "id": self.sequence,
                    "tool": "steed_" + name.replace(".", "_"),
                    "arguments": arguments,
                }
            ).encode()
            + b"\n"
        )
        response = self.receive(socket)
        self.assertEqual(response["id"], self.sequence)
        return response

    def start(self, socket):
        return self.call(socket, "task.start", objective="Synthetic external task")[
            "result"
        ]["task_id"]

    def approved_session(self, socket, task_id):
        grant = self.call(
            socket,
            "grant.request",
            scope={"origins": [self.origin], "allow_private_network": True},
        )["result"]
        self.service.approve_grant(task_id, grant["grant_id"])
        return self.call(socket, "session.open")["result"]["session_id"]

    def test_authentication_and_private_discovery(self):
        self.assertEqual(stat.S_IMODE(self.gateway.discovery.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(os.stat(self.gateway.directory.name).st_mode), 0o700
        )
        socket = self.connect("wrong-token")
        self.wait_for(
            lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
        )
        self.assertEqual(self.service.store.list_tasks(), [])

    def test_context_is_bound_to_an_active_task_and_never_opens_a_page(self):
        socket = self.connect()
        self.assertIn("error", self.call(socket, "browser.context"))
        task_id = self.start(socket)
        self.assertEqual(
            self.call(socket, "browser.context")["result"],
            {
                "window_id": None,
                "profile": None,
                "profile_selector": None,
                "shared_profile_available": False,
                "source": "no_browser_window",
            },
        )
        self.assertIn("error", self.call(socket, "browser.context", task_id="foreign"))
        self.assertFalse(self.service._sessions)
        self.assertEqual(self.service.store.grants(task_id), [])
        self.call(socket, "task.finish", status="completed")
        self.assertIn("error", self.call(socket, "browser.context"))

    def test_approval_is_required_and_cannot_be_supplied_by_client(self):
        socket = self.connect()
        task_id = self.start(socket)
        self.assertFalse(
            self.call(socket, "task.capabilities")["result"]["website_scripts"]
        )
        self.assertEqual(
            self.call(socket, "session.open")["error"]["code"], "approval_required"
        )
        self.assertIn("error", self.call(socket, "grant.approve", task_id=task_id))
        events = self.service.store.events(task_id)
        self.assertTrue(
            any(
                event.payload.get("reason") == "external_request_rejected"
                for event in events
            )
        )
        self.assertIn(
            "error", self.call(socket, "grant.request", scope={"actions": ["scripts"]})
        )
        self.assertEqual(self.service._sessions, {})

    def test_task_identity_and_foreign_sessions_are_rejected(self):
        first, second = self.connect(), self.connect()
        first_task, second_task = self.start(first), self.start(second)
        session_id = self.approved_session(second, second_task)
        self.assertIn("error", self.call(first, "task.status", task_id=second_task))
        self.assertEqual(
            self.call(first, "session.inspect", session_id=session_id)["error"]["code"],
            "session_not_owned",
        )
        self.assertIn("error", self.call(first, "task.list"))
        self.assertEqual(
            self.call(first, "task.status")["result"]["task_id"], first_task
        )
        self.assertIn(session_id, self.service._sessions)

    def test_disconnect_cancels_only_owned_task_and_disposes_its_page(self):
        first, second = self.connect(), self.connect()
        first_task, second_task = self.start(first), self.start(second)
        first_session = self.approved_session(first, first_task)
        other_session = self.approved_session(second, second_task)
        first.abort()
        self.wait_for(lambda: self.service.store.get(first_task).status == "cancelled")
        self.assertNotIn(first_session, self.service._sessions)
        self.assertIn(other_session, self.service._sessions)
        self.assertEqual(
            self.call(second, "task.status")["result"]["status"], "running"
        )

    def test_disable_closes_connections_and_removes_discovery(self):
        socket = self.connect()
        task_id = self.start(socket)
        self.approved_session(socket, task_id)
        self.gateway.close()
        self.wait_for(
            lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
        )
        self.assertFalse(self.gateway.discovery.exists())
        self.assertFalse(self.service._sessions)
        self.assertEqual(self.service.store.get(task_id).status, "cancelled")

    def test_malformed_oversized_and_deep_messages_close_connection(self):
        for raw in (
            b"[]\n",
            b"x" * (REQUEST_LIMIT + 1),
            b"[" * 2000 + b"]" * 2000 + b"\n",
            b'{"id":NaN}\n',
        ):
            with self.subTest(size=len(raw)):
                socket = self.connect()
                socket.write(raw)
                self.wait_for(
                    lambda socket=socket: (
                        socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
                    )
                )
        self.assertEqual(self.service.store.list_tasks(), [])

    def test_disconnect_discards_queued_operation_before_side_effect(self):
        socket = self.connect()
        task_id = self.start(socket)
        self.approved_session(socket, task_id)
        connection = next(iter(self.gateway.connections))
        connection._request({"id": 99, "tool": "steed_task_finish", "arguments": {}})
        connection.close()
        self.app.processEvents()
        self.assertEqual(self.service.store.get(task_id).status, "cancelled")

    def test_revocation_denies_further_operations(self):
        socket = self.connect()
        task_id = self.start(socket)
        session_id = self.approved_session(socket, task_id)
        grant_id = self.service.store.grants(task_id)[0].grant_id
        self.service.revoke_grant(task_id, grant_id)
        self.assertEqual(
            self.call(socket, "session.inspect", session_id=session_id)["error"][
                "code"
            ],
            "task_inactive",
        )
        self.assertNotIn(session_id, self.service._sessions)

    def test_operation_timeout_cancels_task_and_closes_connection(self):
        socket = self.connect()
        task_id = self.start(socket)
        self.approved_session(socket, task_id)
        connection = next(iter(self.gateway.connections))
        connection.deadline.timeout.emit()
        self.wait_for(
            lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
        )
        self.assertEqual(self.service.store.get(task_id).status, "cancelled")
        self.assertFalse(self.service._sessions)

    def test_task_reuse_requires_completion_and_fresh_approval(self):
        socket = self.connect()
        first = self.start(socket)
        first_session = self.approved_session(socket, first)
        self.assertIn(
            "error", self.call(socket, "task.start", objective="Premature replacement")
        )
        self.assertEqual(self.call(socket, "task.status")["result"]["task_id"], first)
        self.call(socket, "task.finish")
        second = self.start(socket)
        self.assertNotEqual(first, second)
        self.assertFalse(self.service.store.grants(second))
        self.assertEqual(
            self.call(socket, "session.open")["error"]["code"], "approval_required"
        )
        self.assertEqual(
            self.call(socket, "session.inspect", session_id=first_session)["error"][
                "code"
            ],
            "session_not_owned",
        )
