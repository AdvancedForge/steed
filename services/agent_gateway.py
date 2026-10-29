"""Opt-in, private local IPC into the running browser's task service."""

import json
import secrets
import sqlite3
import tempfile
import time
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
        self.bindings = {}
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

    def bind_task(self, task_id):
        """Issue a short-lived, single-use capability to a trusted provider launcher."""
        require_owner_thread(self)
        self.bindings = {
            key: value
            for key, value in self.bindings.items()
            if value[1] > time.monotonic()
        }
        self.service._active(task_id)
        if (
            self.closed
            or task_id in self.service.external_tasks
            or len(self.bindings) >= 4
        ):
            raise ValueError("Task connection unavailable")
        token = secrets.token_urlsafe(32)
        self.bindings[token] = (task_id, time.monotonic() + 60)
        return token

    def revoke_binding(self, token):
        require_owner_thread(self)
        self.bindings.pop(token, None)

    def _redeem(self, token):
        if not isinstance(token, str):
            raise ValueError("Invalid task binding")
        binding = self.bindings.pop(token, None)
        if binding is None or binding[1] <= time.monotonic():
            raise ValueError("Task binding expired")
        task_id = binding[0]
        self.service._active(task_id)
        if task_id in self.service.external_tasks:
            raise ValueError("Task already connected")
        return self.service.client(task_id)

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
        self.bindings.clear()
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
        self.managed = False
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
                        set(request) not in ({"token"}, {"token", "task_binding"})
                        or not isinstance(token, str)
                        or not secrets.compare_digest(token, self.gateway.token)
                    ):
                        self.close()
                        return
                    if "task_binding" in request:
                        self.client = self.gateway._redeem(request["task_binding"])
                        self.managed = True
                        self.gateway.service.external_connection(
                            self.client.task_id, True
                        )
                    self.authenticated = True
                    self.deadline.stop()
                    self._send({"connected": True})
                    continue
                self._request(request)
            except sqlite3.Error:
                self.gateway.service._fatal()
                self.close()
            except (
                ValueError,
                TypeError,
                UnicodeError,
                RecursionError,
                OSError,
                RuntimeError,
            ):
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
        spec = TOOLS_BY_NAME.get(request["tool"])
        if self.pending is not None:
            self._error(request_id, "request_in_progress", spec)
            return
        try:
            if spec is None:
                raise ValueError("Unknown tool")
            spec.validate(request["arguments"])
            if self.managed and spec.command in (
                "task.start",
                "conversation.reply",
                "conversation.wait",
            ):
                raise ValueError("Managed conversation is bound by the browser")
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
            future.add_done_callback(
                lambda result: self._complete(request_id, result, spec)
            )
        except Exception as error:
            service = self.gateway.service
            if self.client is not None and not service._closed and not service.failure:
                try:
                    service.store.record_event(
                        self.client.task_id,
                        "permission.denied",
                        {
                            "reason": "external_request_rejected",
                            "command": spec.command if spec else "unknown_tool",
                            "code": getattr(error, "code", "invalid_request"),
                            "stage": "validation",
                        },
                    )
                    service.changed.emit()
                except Exception:
                    service._fatal()
                    return
            self._error(
                request_id, getattr(error, "code", "invalid_request"), spec, error
            )

    def _error(self, request_id, code, spec=None, error=None):
        # Never return exception strings, secrets, storage paths, or tracebacks.
        from services.agent_errors import operation_guidance

        message = operation_guidance(code)
        from services.agent_errors import AgentRequestError

        details = {}
        if isinstance(error, AgentRequestError):
            message = error.public_message
            if error.field:
                details["field"] = error.field
        if spec and spec.command == "browser.tabs" and code == "approval_required":
            message = (
                "Live tab listing requires the user's Share browser tabs handoff "
                "and live/read approval. This does not block task capabilities, "
                "browser context, enabled library folders or connector discovery."
            )
        self._send(
            {
                "id": request_id,
                "error": {
                    "code": code,
                    "message": message,
                    **details,
                    **({"tool": spec.name} if spec else {}),
                },
            }
        )

    def _complete(self, request_id, future, spec):
        if self.closed:
            return
        self.pending = None
        self.deadline.stop()
        try:
            self._send({"id": request_id, "result": json_value(future.result())})
        except Exception as error:
            code = getattr(error, "code", "operation_failed")
            service = self.gateway.service
            if self.client is not None and not service._closed and not service.failure:
                try:
                    service.store.record_event(
                        self.client.task_id,
                        "client.rejected",
                        {"command": spec.command, "code": code, "stage": "execution"},
                    )
                    service.changed.emit()
                except Exception:
                    service._fatal()
                    return
            self._error(request_id, code, spec, error)

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
