"""Application-owned chat lifecycle across windows, independent of provider choice."""

import importlib.util
import re
import sqlite3
import tempfile
from collections import deque
from functools import wraps
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.codex_provider import CodexProvider
from services.command_broker import require_owner_thread
from services.task_messages import MESSAGE_BYTES, TASK_BYTES
from services.task_store import ACTIVE_STATUSES
from utils import save_json


def provider_callback(method):
    """Keep runtime/storage failures from escaping Qt signal delivery."""

    @wraps(method)
    def guarded(self, task_id, *args):
        try:
            return method(self, task_id, *args)
        except sqlite3.Error:
            self.service._fatal()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            self._failed(
                task_id,
                "Chat storage or delivery failed; existing work will not be retried.",
            )

    return guarded


class AgentProviders(QObject):
    changed = pyqtSignal()
    requested = pyqtSignal(str, str, object)

    def __init__(self, registry, parent=None, *, factory=CodexProvider):
        super().__init__(parent)
        self.registry = registry
        self.service = registry.tasks
        self.factory = factory
        self.chats = {}
        self.states = {}
        self.model_reports = {}
        self.service.user_message_sent.connect(self._send)
        self.service.conversation_cleared.connect(self._clear)
        self.service.changed.connect(self._check_tasks)
        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(100)
        self.flush_timer.timeout.connect(self._flush)

    def new_chat(self, model=None):
        require_owner_thread(self)
        self.service._usable()
        if model is not None and (
            not isinstance(model, str)
            or re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model) is None
        ):
            raise ValueError("Choose a valid Codex model")
        if importlib.util.find_spec("mcp") is None:
            raise RuntimeError(
                "Install requirements-mcp.txt in Steed's environment first."
            )
        if len(self.chats) >= 2:
            raise ValueError(
                "Cancel an existing Codex chat before starting another (limit: two)."
            )
        self.registry.set_agent_access(True)
        task = self.service._create("Codex conversation")
        directory = tempfile.TemporaryDirectory(prefix="steed-codex-")
        gateway = self.registry.agent_gateway
        token = None
        try:
            token = gateway.bind_task(task.task_id)
            binding = Path(directory.name) / "task-binding.json"
            save_json(
                binding,
                {
                    "version": 1,
                    "socket": gateway.socket_path,
                    "token": gateway.token,
                    "task_binding": token,
                },
            )
            driver = self.factory(self)
            driver.model = model
            chat = {
                "driver": driver,
                "directory": directory,
                "binding": token,
                "binding_file": binding,
                "gateway": gateway,
                "queue": deque(),
                "seen": set(),
                "current": None,
                "ready": False,
                "items": {},
                "bytes": 0,
                "discard_reply": False,
                "requests": {},
                "activity": "connecting",
            }
            self.chats[task.task_id] = chat
            self.states[task.task_id] = "Starting Codex…"
            driver.request_resolved.connect(
                lambda request: self._resolve_request(task.task_id, request)
            )
            driver.requested.connect(
                lambda request, payload: self._request(task.task_id, request, payload)
            )
            driver.ready.connect(lambda: self._ready(task.task_id))
            driver.accepted.connect(
                lambda message: self._accepted(task.task_id, message)
            )
            driver.message.connect(
                lambda item, text, final: self._message(task.task_id, item, text, final)
            )
            driver.activity.connect(lambda state: self._activity(task.task_id, state))
            driver.model_reported.connect(
                lambda report: self._model_reported(task.task_id, report)
            )
            driver.turn_done.connect(lambda: self._done(task.task_id))
            driver.failed.connect(lambda error: self._failed(task.task_id, error))
            self.changed.emit()
            driver.start(gateway.discovery, binding)
        except Exception:
            if task.task_id in self.chats:
                self._failed(task.task_id, "Codex could not be started.")
            else:
                if token is not None:
                    gateway.revoke_binding(token)
                directory.cleanup()
                self.service.cancel_task(task.task_id)
            raise
        return task.task_id

    @provider_callback
    def _request(self, task_id, request_id, payload):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        chat["requests"][request_id] = {"payload": payload, "claimed": False}
        self.states[task_id] = "Codex needs your input"
        self.requested.emit(task_id, request_id, payload)
        self.changed.emit()

    def _resolve_request(self, task_id, request_id):
        chat = self.chats.get(task_id)
        if chat is not None:
            chat["requests"].pop(request_id, None)
            self.changed.emit()

    def claim_request(self, task_id, request_id):
        chat = self.chats.get(task_id)
        request = chat["requests"].get(request_id) if chat else None
        if request is None or request["claimed"]:
            return False
        request["claimed"] = True
        return True

    def respond(self, task_id, request_id, result):
        chat = self.chats.get(task_id)
        if chat is None or request_id not in chat["requests"]:
            raise ValueError("Agent request is no longer active")
        chat["driver"].respond(request_id, result)
        chat["requests"].pop(request_id)
        self.states[task_id] = "Codex replying…" if chat["current"] else "Codex ready"
        self.changed.emit()

    def status(self, task_id):
        return self.states.get(task_id, "")

    def can_send(self, task_id):
        return task_id not in self.states or bool(
            self.chats.get(task_id, {}).get("ready")
        )

    def activity(self, task_id):
        """Transient UI state; never infer another client's execution from reads."""
        chat = self.chats.get(task_id)
        if chat is None:
            return None
        state = "waiting" if chat["requests"] else chat["activity"]
        return {"state": state, "queued": len(chat["queue"])}

    def model_info(self, task_id):
        """Only runtime reports count; the requested/default selection is no proof."""
        report = self.model_reports.get(task_id)
        if report is None:
            return None
        return {**report, "live": task_id in self.chats}

    def delivery(self, task_id, message_id):
        chat = self.chats.get(task_id)
        if chat is None:
            return None
        current = chat["current"]
        if current is not None and current["message_id"] == message_id:
            return "Awaiting acceptance"
        if any(message["message_id"] == message_id for message in chat["queue"]):
            return "Queued for agent"
        return None

    def _model_reported(self, task_id, report):
        if task_id not in self.chats:
            return
        if report is None:
            self.model_reports.pop(task_id, None)
        else:
            self.model_reports[task_id] = dict(report)
        self.changed.emit()

    def _activity(self, task_id, state):
        chat = self.chats.get(task_id)
        if (
            chat is not None
            and chat["current"]
            and state
            in (
                "thinking",
                "working",
                "replying",
            )
            and chat["activity"] != state
        ):
            chat["activity"] = state
            self.changed.emit()

    @provider_callback
    def _ready(self, task_id):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        if (
            getattr(chat["driver"], "bound_task_id", None) != task_id
            or task_id not in self.service.external_tasks
        ):
            self._failed(task_id, "Codex connected to an unexpected task.")
            return
        chat["ready"] = True
        chat["activity"] = "ready"
        chat["binding_file"].unlink(missing_ok=True)
        self.states[task_id] = "Codex ready"
        self.changed.emit()
        self._next(task_id)

    @provider_callback
    def _send(self, task_id, message):
        chat = self.chats.get(task_id)
        if chat is None or not chat["ready"] or message["message_id"] in chat["seen"]:
            return
        chat["seen"].add(message["message_id"])
        chat["queue"].append(message)
        self._next(task_id)
        self.changed.emit()

    @provider_callback
    def _next(self, task_id):
        chat = self.chats.get(task_id)
        if chat is None or not chat["ready"] or chat["current"] or not chat["queue"]:
            return
        message = chat["queue"].popleft()
        # Clearing a queued message removes it from future model input as well.
        row = self.service.store._db.execute(
            "SELECT state FROM task_messages WHERE task_id=? AND message_id=?",
            (task_id, message["message_id"]),
        ).fetchone()
        if row is None or row[0] == "cleared":
            QTimer.singleShot(0, lambda: self._next(task_id))
            return
        chat["current"] = message
        chat["activity"] = "sending"
        self.states[task_id] = "Codex replying… (follow-up messages queue)"
        self.changed.emit()
        try:
            chat["driver"].send(message["message_id"], message["text"])
        except (RuntimeError, ValueError, OSError):
            self._failed(
                task_id, "Codex could not accept this message; it will not be retried."
            )

    @provider_callback
    def _accepted(self, task_id, message_id):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        if chat["current"] is None or chat["current"]["message_id"] != message_id:
            self._failed(
                task_id, "Unexpected Codex turn; messages will not be retried."
            )
            return
        chat["activity"] = "thinking"
        self.service.messages.acknowledge(task_id, {"messages": [chat["current"]]})
        self.service.changed.emit()

    def _message(self, task_id, item_id, text, final):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        if chat["discard_reply"]:
            return
        try:
            if (
                not chat["current"]
                or not isinstance(item_id, str)
                or not 1 <= len(item_id) <= 128
                or not isinstance(text, str)
            ):
                raise ValueError("Unexpected reply")
            key = (chat["current"]["message_id"], item_id)
            if key not in chat["items"] and len(chat["items"]) >= 256:
                raise ValueError("Reply item limit")
            item = chat["items"].setdefault(
                key, {"text": "", "final": False, "chunks": []}
            )
            previous = item["text"]
            combined = text if final else previous + text
            if not combined.startswith(previous) or (
                item["final"] and combined != previous
            ):
                raise ValueError("Reply changed after streaming")
            chat["bytes"] += len(combined.encode()) - len(previous.encode())
            if chat["bytes"] > TASK_BYTES:
                raise ValueError("Reply budget exceeded")
            item.update(text=combined, final=final)
            if final:
                self._flush_task(task_id)
            else:
                if not self.flush_timer.isActive():
                    self.flush_timer.start()
        except (ValueError, RuntimeError, sqlite3.Error):
            self._failed(
                task_id,
                "The reply exceeded chat limits or changed unexpectedly; no work will be retried.",
            )

    def _flush(self):
        for task_id in tuple(self.chats):
            try:
                self._flush_task(task_id)
            except (ValueError, RuntimeError, sqlite3.Error):
                self._failed(
                    task_id, "Chat storage is unavailable; no work will be retried."
                )

    def _clear(self, task_id):
        chat = self.chats.get(task_id)
        if chat is not None:
            chat["queue"].clear()
            chat["items"].clear()
            chat["discard_reply"] = chat["current"] is not None

    def _flush_task(self, task_id):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        changed = False
        for item in chat["items"].values():
            raw = item["text"].encode()
            parts = []
            while raw:
                end = min(MESSAGE_BYTES, len(raw))
                while end < len(raw) and raw[end] & 0xC0 == 0x80:
                    end -= 1
                parts.append(raw[:end].decode())
                raw = raw[end:]
            for index, part in enumerate(parts):
                state = (
                    "complete"
                    if item["final"] or index < len(parts) - 1
                    else "streaming"
                )
                if index == len(item["chunks"]):
                    item["chunks"].append(
                        {
                            "id": "codex_" + uuid4().hex,
                            "revision": -1,
                            "text": "",
                            "state": "streaming",
                        }
                    )
                chunk = item["chunks"][index]
                if chunk["text"] == part and chunk["state"] == state:
                    continue
                revision = chunk["revision"] + 1
                self.service.messages.post_agent(
                    task_id, chunk["id"], part, revision, state
                )
                chunk.update(revision=revision, text=part, state=state)
                changed = True
        if changed:
            self.service.changed.emit()

    def _done(self, task_id):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        try:
            for item in chat["items"].values():
                item["final"] = True
            self._flush_task(task_id)
            chat["current"] = None
            chat["activity"] = "ready"
            chat["discard_reply"] = False
            chat["items"].clear()
            self.states[task_id] = "Codex ready"
            self.changed.emit()
            # Let the provider consume the rest of its notification batch first.
            QTimer.singleShot(0, lambda: self._next(task_id))
        except (ValueError, RuntimeError, sqlite3.Error):
            self._failed(
                task_id, "Chat storage is unavailable; no work will be retried."
            )

    def _failed(self, task_id, error):
        if task_id not in self.chats:
            return
        self.states[task_id] = error
        self._stop(task_id)
        if not self.service._closed and not self.service.failure:
            try:
                if self.service.store.get(task_id).status in ACTIVE_STATUSES:
                    self.service.cancel_task(task_id)
            except sqlite3.Error:
                self.service._fatal()
        self.changed.emit()

    def _stop(self, task_id):
        chat = self.chats.pop(task_id, None)
        if chat is None:
            return
        chat["gateway"].revoke_binding(chat["binding"])
        chat["driver"].stop()
        chat["driver"].deleteLater()
        chat["directory"].cleanup()
        if not self.chats:
            self.flush_timer.stop()

    def _check_tasks(self):
        try:
            for task_id in tuple(self.chats):
                if (
                    self.service._closed
                    or self.service.failure
                    or self.service.store.get(task_id).status not in ACTIVE_STATUSES
                ):
                    self.states[task_id] = "Codex chat ended"
                    self._stop(task_id)
                    self.changed.emit()
        except sqlite3.Error:
            self.service._fatal()

    def close(self):
        require_owner_thread(self)
        for task_id in tuple(self.chats):
            self.states[task_id] = "Codex disconnected"
            self._stop(task_id)
            if not self.service._closed and not self.service.failure:
                if self.service.store.get(task_id).status in ACTIVE_STATUSES:
                    self.service.cancel_task(task_id)
        self.changed.emit()
