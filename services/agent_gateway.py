"""Opt-in, private local IPC into the running browser's task service."""

import hashlib
import json
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtNetwork import QLocalServer

from browser.utils import save_json
from services.agent_errors import AgentRequestError
from services.agent_tools import (
    REQUEST_LIMIT,
    RESPONSE_LIMIT,
    TOOLS_BY_NAME,
    json_value,
)
from services.command_broker import require_owner_thread
from services.task_store import ACTIVE_STATUSES

# A disconnected client may resume its task for five minutes.
RESUME_GRACE_MS = 5 * 60 * 1000

# What an agent may still do while you have paused all agents.
WHILE_PAUSED = frozenset(
    {
        "conversation.read",
        "conversation.wait",
        "task.capabilities",
        "task.events",
        "grant.list",
        # Stopping is always allowed.
        "session.stop",
        "session.close",
        "download.cancel",
        "worker.cancel",
        "app.cancel",
    }
)


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
        # Resumable tasks: task_id -> token digest, and disconnected tasks
        # held for a grace period before they are cancelled.
        self.resume_digests = {}
        self.held = {}
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

    def issue_resume(self, task_id):
        """A single-use token that lets a new connection resume this task."""
        token = secrets.token_hex(32)
        self.resume_digests[task_id] = hashlib.sha256(token.encode()).hexdigest()
        return token

    def hold(self, task_id):
        """Keep a disconnected task alive briefly so its client can resume it."""
        if self.closed or task_id not in self.resume_digests:
            return False
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._abandon(task_id))
        timer.start(RESUME_GRACE_MS)
        self.held[task_id] = timer
        self.service.resumable[task_id] = time.time() + RESUME_GRACE_MS / 1000
        self.service.changed.emit()
        return True

    def _abandon(self, task_id):
        timer = self.held.pop(task_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self.resume_digests.pop(task_id, None)
        service = self.service
        if service.resumable.pop(task_id, None) is not None:
            service.changed.emit()
        if service._closed or service.failure:
            return
        try:
            if service.store.get(task_id).status in ACTIVE_STATUSES:
                service.cancel_task(task_id)
        except Exception:
            service._fatal()

    def claim(self, token):
        """Return the held task this token resumes, consuming the token."""
        if not isinstance(token, str) or len(token) != 64:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        for task_id, expected in list(self.resume_digests.items()):
            if task_id in self.held and secrets.compare_digest(digest, expected):
                timer = self.held.pop(task_id)
                timer.stop()
                timer.deleteLater()
                del self.resume_digests[task_id]
                self.service.resumable.pop(task_id, None)
                return task_id
        return None

    def close(self):
        require_owner_thread(self)
        if self.closed:
            return
        self.closed = True
        self.bindings.clear()
        self.server.close()
        for connection in tuple(self.connections):
            connection.close()
        # Without agent access nobody can resume; end every held task.
        for task_id in list(self.held):
            self._abandon(task_id)
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
        self.remote = False
        self.pending = None
        # Set when Steed itself drops the connection (a stuck operation):
        # such a task is cancelled rather than offered for resumption.
        self.cancel_on_close = False
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._timed_out)
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
                        set(request)
                        not in (
                            {"token"},
                            {"token", "task_binding"},
                            {"token", "remote"},
                        )
                        or not isinstance(token, str)
                        or not secrets.compare_digest(token, self.gateway.token)
                        or request.get("remote", True) is not True
                    ):
                        self.close()
                        return
                    self.remote = "remote" in request
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
                if request == {"release": "cancel"}:
                    # The client ended its session on purpose; do not hold
                    # the task for task.resume when the connection closes.
                    self.cancel_on_close = True
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
                "task.resume",
                "conversation.reply",
                "conversation.wait",
            ):
                raise ValueError("Managed conversation is bound by the browser")
            self.gateway.service._usable()
            if self.gateway.service.agents_paused and spec.command not in WHILE_PAUSED:
                raise AgentRequestError(
                    "agents_paused",
                    "The user paused all agents. Nothing was done. Tell the user "
                    "what you were about to do and wait; do not retry until they "
                    "resume agents.",
                )
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
                if self.remote:
                    self.gateway.service.mark_remote(task.task_id)
                if self.client is not None:
                    self.gateway.service.external_connection(self.client.task_id, False)
                self.client = self.gateway.service.client(task.task_id)
                self.gateway.service.external_connection(task.task_id, True)
                result = {
                    **json_value(task),
                    "resume_token": self.gateway.issue_resume(task.task_id),
                }
                self._send({"id": request_id, "result": result})
                return
            if spec.command == "task.resume":
                if (
                    self.client is not None
                    and self.gateway.service.store.get(self.client.task_id).status
                    in ACTIVE_STATUSES
                ):
                    raise ValueError("Finish the current task before resuming another")
                task_id = self.gateway.claim(request["arguments"]["resume_token"])
                if task_id is None:
                    raise ValueError("Unknown or expired resume token")
                task = self.gateway.service.store.get(task_id)
                if task.status not in ACTIVE_STATUSES:
                    raise ValueError("Unknown or expired resume token")
                if self.remote:
                    self.gateway.service.mark_remote(task_id)
                self.client = self.gateway.service.client(task_id)
                self.gateway.service.external_connection(task_id, True)
                result = {
                    **json_value(task),
                    "resume_token": self.gateway.issue_resume(task_id),
                }
                self._send({"id": request_id, "result": result})
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

    def _timed_out(self):
        self.cancel_on_close = True
        self.close()

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
            task_id = self.client.task_id
            service.external_connection(task_id, False)
            try:
                if service.store.get(task_id).status in ACTIVE_STATUSES:
                    if (
                        not self.managed
                        and not self.cancel_on_close
                        and self.gateway.hold(task_id)
                    ):
                        # Nobody may act for the task while it waits to be
                        # resumed: end its access, keep its conversation.
                        service._end_authority(task_id, "disconnected")
                    elif self.managed:
                        # A chat Steed runs lost its connection: cut off, not
                        # a cancellation the person asked for.
                        service.interrupt_task(task_id)
                    else:
                        service.cancel_task(task_id)
            except Exception:
                service._fatal()
        self.gateway.connections.discard(self)
        self.deleteLater()
