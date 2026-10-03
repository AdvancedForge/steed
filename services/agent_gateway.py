"""Opt-in, private local IPC into the running browser's task service."""

import json
import secrets
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtNetwork import QLocalServer

from services.agent_tools import (
    REQUEST_LIMIT,
    RESPONSE_LIMIT,
    TOOLS_BY_NAME,
    json_value,
)
from services.command_broker import require_owner_thread
from services.task_store import ACTIVE_STATUSES
from utils import save_json


class AgentGateway(QObject):
    def __init__(self, service, config_dir, parent=None):
        super().__init__(parent)
        require_owner_thread(self)
        self.service = service
        self.discovery = Path(config_dir) / "agent-access.json"
        self.directory = tempfile.TemporaryDirectory(prefix="steed-agent-")
        self.token = secrets.token_urlsafe(32)
        self.connections = set()
        self.closed = False
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(4)
        self.server.newConnection.connect(self._accept)
        self.socket_path = str(Path(self.directory.name) / "browser.sock")
        try:
            if not self.server.listen(self.socket_path):
                raise RuntimeError("Could not start private agent access")
            save_json(
                self.discovery,
                {"version": 1, "socket": self.socket_path, "token": self.token},
            )
        except Exception:
            self.server.close()
            self.directory.cleanup()
            raise

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if self.closed or len(self.connections) >= 4:
                socket.abort()
                socket.deleteLater()
                continue
            connection = AgentConnection(self, socket)
            self.connections.add(connection)

    def close(self):
        require_owner_thread(self)
        if self.closed:
            return
        self.closed = True
        self.server.close()
        for connection in tuple(self.connections):
            connection.close()
        self.discovery.unlink(missing_ok=True)
        self.directory.cleanup()


class AgentConnection(QObject):
    """One connection owns one active task, with no caller-selected identity."""

    def __init__(self, gateway, socket):
        super().__init__(gateway)
        self.gateway, self.socket = gateway, socket
        socket.setParent(self)
        socket.setReadBufferSize(REQUEST_LIMIT + 1)
        self.buffer = bytearray()
        self.authenticated = False
        self.closed = False
        self.client = None
        self.pending = None
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.close)
        self.deadline.start(5000)
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self.close)
        QTimer.singleShot(0, self._read)

    def _send(self, payload):
        if self.closed:
            return
        data = json.dumps(payload, allow_nan=False).encode() + b"\n"
        if len(data) > RESPONSE_LIMIT or self.socket.bytesToWrite() > RESPONSE_LIMIT:
            self.close()
            return
        self.socket.write(data)

    def _read(self):
        if self.closed:
            return
        self.buffer.extend(bytes(self.socket.read(REQUEST_LIMIT + 1)))
        if len(self.buffer) > REQUEST_LIMIT:
            self.close()
            return
        while b"\n" in self.buffer and not self.closed:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                request = json.loads(
                    raw,
                    parse_constant=lambda _: (_ for _ in ()).throw(
                        ValueError("Invalid number")
                    ),
                )
                if not isinstance(request, dict):
                    raise ValueError("Invalid request")
                if not self.authenticated:
                    token = request.get("token")
                    if (
                        set(request) != {"token"}
                        or not isinstance(token, str)
                        or not secrets.compare_digest(token, self.gateway.token)
                    ):
                        self.close()
                        return
                    self.authenticated = True
                    self.deadline.stop()
                    self._send({"connected": True})
                    continue
                self._request(request)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                self.close()

    def _request(self, request):
        if (
            set(request) != {"id", "tool", "arguments"}
            or type(request["id"]) is not int
            or not 0 <= request["id"] <= 2147483647
            or not isinstance(request["tool"], str)
        ):
            raise ValueError("Invalid request envelope")
        request_id = request["id"]
        if self.pending is not None:
            self._error(request_id, "request_in_progress")
            return
        try:
            spec = TOOLS_BY_NAME.get(request["tool"])
            if spec is None:
                raise ValueError("Unknown tool")
            spec.validate(request["arguments"])
            self.gateway.service._usable()
            if spec.command == "task.start":
                if (
                    self.client is not None
                    and self.gateway.service.store.get(self.client.task_id).status
                    in ACTIVE_STATUSES
                ):
                    raise ValueError("Finish the current task before starting another")
                # Creation and binding happen together on the Qt thread. A
                # disconnect cannot leave queued creation without an owner.
                task = self.gateway.service._create(**request["arguments"])
                if self.client is not None:
                    self.gateway.service.external_connection(self.client.task_id, False)
                self.client = self.gateway.service.client(task.task_id)
                self.gateway.service.external_connection(task.task_id, True)
                self._send({"id": request_id, "result": json_value(task)})
                return
            if self.client is None:
                raise ValueError("Start a task first")
            future = self.client.submit(spec.command, **request["arguments"])
            self.pending = future
            self.deadline.start(30000)
            future.add_done_callback(lambda result: self._complete(request_id, result))
        except Exception as error:
            service = self.gateway.service
            if self.client is not None and not service._closed and not service.failure:
                try:
                    service.store.record_event(
                        self.client.task_id,
                        "permission.denied",
                        {"reason": "external_request_rejected"},
                    )
                except Exception:
                    service._fatal()
                    return
            self._error(request_id, getattr(error, "code", "invalid_request"))

    def _error(self, request_id, code):
        # Never return exception strings, secrets, storage paths, or tracebacks.
        self._send(
            {
                "id": request_id,
                "error": {
                    "code": code,
                    "message": "Task operation failed; check scope, references, and approval status.",
                },
            }
        )

    def _complete(self, request_id, future):
        if self.closed:
            return
        self.pending = None
        self.deadline.stop()
        try:
            self._send({"id": request_id, "result": json_value(future.result())})
        except Exception as error:
            self._error(request_id, getattr(error, "code", "operation_failed"))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.deadline.stop()
        self.socket.abort()
        if self.pending is not None:
            self.pending.cancel()
        service = self.gateway.service
        if self.client is not None and not service._closed:
            service.external_connection(self.client.task_id, False)
            try:
                if service.store.get(self.client.task_id).status in ACTIVE_STATUSES:
                    service.cancel_task(self.client.task_id)
            except Exception:
                service._fatal()
        self.gateway.connections.discard(self)
        self.deleteLater()
