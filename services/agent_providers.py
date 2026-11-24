"""Application-owned chat lifecycle across windows, independent of provider choice."""

import importlib.util
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections import deque
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from browser.utils import save_json
from services.claude_code_provider import ClaudeCodeProvider
from services.codex_provider import CodexProvider
from services.command_broker import require_owner_thread
from services.task_messages import MESSAGE_BYTES, TASK_BYTES
from services.task_store import ACTIVE_STATUSES

# Retained per conversation so the UI can say when the agent last reported.
REPORTED_LIMIT = 256


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


AGENT_NAMES = {"codex": "Codex", "claude": "Claude Code"}


_SAVED_THREADS = set()


def codex_thread_saved(thread_id):
    """Whether Codex still has this thread saved (its rollout file under
    $CODEX_HOME/sessions, ~/.codex by default)."""
    if not isinstance(thread_id, str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,128}", thread_id
    ):
        return False
    if thread_id in _SAVED_THREADS:
        return True
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        found = any((home / "sessions").rglob(f"rollout-*{thread_id}.jsonl"))
    except OSError:
        return False
    if found:
        _SAVED_THREADS.add(thread_id)
    return found


class AgentProviders(QObject):
    changed = pyqtSignal()
    requested = pyqtSignal(str, str, object)
    replied = pyqtSignal(str)  # task id: its agent finished a reply

    def __init__(self, registry, parent=None, *, factory=CodexProvider, factories=None):
        super().__init__(parent)
        self.registry = registry
        self.service = registry.tasks
        self.factories = {"codex": factory, "claude": ClaudeCodeProvider}
        self.factories.update(factories or {})
        self.chats = {}
        self.states = {}
        self.stopped = {}
        self.reported = {}
        self.model_reports = {}
        self.agent_names = {}
        self.service.user_message_sent.connect(self._send)
        self.service.conversation_cleared.connect(self._clear)
        self.service.changed.connect(self._check_tasks)
        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(100)
        self.flush_timer.timeout.connect(self._flush)

    def acp_agents(self):
        """ACP agents set up in Agents → ACP Agents (config "acp_agents")."""
        from services.acp_provider import clean_agent

        config = getattr(self.registry, "config", None)
        found = []
        for entry in (config.get("acp_agents", []) if config else []) or []:
            try:
                found.append(clean_agent(entry))
            except ValueError:
                continue
        return found

    def _acp_factory(self, agent):
        if not isinstance(agent, str) or not agent.startswith("acp:"):
            return None
        entry = next(
            (a for a in self.acp_agents() if "acp:" + a["name"] == agent), None
        )
        if entry is None:
            return None
        from services.acp_provider import AcpProvider

        return lambda parent: AcpProvider(
            parent, name=entry["name"], command=entry["command"]
        )

    def models(self):
        """Models Steed runs itself, set up in Agents → Models (config "models")."""
        from services.model_assistant import clean_model

        config = getattr(self.registry, "config", None)
        found = []
        for entry in (config.get("models", []) if config else []) or []:
            try:
                found.append(clean_model(entry))
            except ValueError:
                continue
        return found

    def _model_factory(self, agent):
        if not isinstance(agent, str) or not agent.startswith("model:"):
            return None
        entry = next((m for m in self.models() if "model:" + m["name"] == agent), None)
        if entry is None:
            return None
        from services.model_assistant import ModelAssistant

        return lambda parent: ModelAssistant(parent, entry=entry)

    def agent_choices(self):
        """[(key, name)] of every agent a new chat can use: your models first,
        then Codex and Claude Code (when installed) and any ACP agents."""
        return [(key, name) for key, name, _ in self.assistants()]

    def _installed(self, key):
        default = {"codex": CodexProvider, "claude": ClaudeCodeProvider}[key]
        if self.factories.get(key) is not default:
            return True  # Supplied by the caller (tests, embedders).
        return shutil.which("codex" if key == "codex" else "claude") is not None

    def assistants(self):
        """[(key, name, where)]: every assistant set up, and where it runs."""
        found = []
        for entry in self.models():
            host = urlsplit(entry["base_url"]).hostname
            where = (
                "On this computer"
                if host in ("localhost", "127.0.0.1", "::1")
                else "With your API key"
            )
            found.append(("model:" + entry["name"], entry["name"], where))
        found += [
            (key, AGENT_NAMES[key], "Coding agents")
            for key in ("codex", "claude")
            if self._installed(key)
        ]
        found += [
            ("acp:" + a["name"], a["name"], "Coding agents") for a in self.acp_agents()
        ]
        found += [
            ("web:" + site["name"], site["name"], "Chatbot websites")
            for site in self.website_chats()
        ]
        return found

    def website_chats(self):
        """Chatbot websites you added (config "website_chats"): opened in the
        sidebar, signed in as you; they reach Steed only through the relay."""
        from services.website_chats import clean_website_chat

        config = getattr(self.registry, "config", None)
        found = []
        for entry in (config.get("website_chats", []) if config else []) or []:
            try:
                found.append(clean_website_chat(entry))
            except ValueError:
                continue
        return found

    def agent_label_for(self, task_id):
        """The agent's name for a chat's task."""
        return self.agent_names.get(task_id, "Your agent")

    def agent_label(self, agent):
        """The name shown for an agent key (built-in or an ACP agent)."""
        if agent in AGENT_NAMES:
            return AGENT_NAMES[agent]
        return agent.removeprefix("acp:").removeprefix("model:")

    @property
    def factory(self):
        """The Codex runtime factory, kept for callers that replace it."""
        return self.factories["codex"]

    @factory.setter
    def factory(self, value):
        self.factories["codex"] = value

    def keeps_chats(self):
        """Settings → Agents: new Claude Code and Codex chats are saved so
        they can continue after Steed closes (on unless turned off)."""
        config = getattr(self.registry, "config", None)
        return bool(
            config is not None
            and getattr(config, "config_dir", None)
            and config.get("keep_agent_chats", True)
        )

    def saved_session(self, task_id):
        """The saved conversation a chat was kept under, if any:
        {"agent", "session_id", "cwd"} (a Claude Code session or Codex thread)."""
        events = self.service.store.recent_events(
            task_id, limit=1, kinds=("agent.session",)
        )
        payload = events[-1].payload if events else {}
        return payload if payload.get("session_id") else None

    def continuable(self, task_id):
        """A closed chat whose saved conversation can carry on: its
        session, or None. A chat continues once; later it is the newer one."""
        try:
            task = self.service.store.get(task_id)
        except (KeyError, ValueError):
            return None
        if (
            task.status in ACTIVE_STATUSES
            or task_id in self.chats
            or task_id in self.service.store.continuations()
        ):
            return None
        saved = self.saved_session(task_id)
        if saved is None:
            return None
        if saved.get("agent") == "codex":
            folder = saved.get("cwd")
            if not (folder and Path(folder).is_dir()) or not codex_thread_saved(
                saved["session_id"]
            ):
                return None
            return {"id": saved["session_id"], "cwd": folder, "agent": "codex"}
        from services.claude_sessions import find_session

        found = find_session(saved["session_id"])
        if found is None or found["cwd"] != saved.get("cwd"):
            return None
        return {**found, "agent": "claude"}

    def new_chat(self, model=None, agent="codex", resume=None, continues=None):
        """Start a chat. resume: a Claude Code session id to continue as a
        copy; continues: a closed Steed chat whose saved conversation (Claude
        Code session or Codex thread) carries on."""
        require_owner_thread(self)
        self.service._usable()
        session = None
        if continues is not None:
            session = self.continuable(continues)
            if session is None:
                raise ValueError("This chat can no longer be continued.")
            agent = session["agent"]
        factory = (
            self.factories.get(agent)
            or self._acp_factory(agent)
            or self._model_factory(agent)
        )
        if factory is None:
            raise ValueError("Choose a supported agent")
        name = self.agent_label(agent)
        if continues is None and resume is not None:
            from services.claude_sessions import find_session

            session = find_session(resume) if agent == "claude" else None
            if session is None:
                raise ValueError("That Claude Code session is no longer available.")
        if model is not None and (
            not isinstance(model, str)
            or re.fullmatch(r"[A-Za-z0-9._\[\]-]{1,100}", model) is None
        ):
            raise ValueError(f"Choose a valid {name} model")
        if importlib.util.find_spec("mcp") is None:
            raise RuntimeError(
                "Install requirements/mcp.txt in Steed's environment first."
            )
        if len(self.chats) >= 2:
            raise ValueError(
                "Cancel an existing agent chat before starting another (limit: two)."
            )
        self.registry.set_agent_access(True)
        if continues is not None:
            objective = self.service.store.get(continues).objective
        elif session:
            objective = f"{name}: {session['title']}"[:200]
        else:
            objective = f"{name} conversation"
        task = self.service._create(objective)
        if continues is not None:
            self.service.store.record_event(
                task.task_id, "conversation.continues", {"task_id": continues}
            )
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
            driver = factory(self)
            driver.model = model
            driver.expected_task_id = task.task_id
            if session is not None and agent == "codex":
                driver.resume_thread = session["id"]
                driver.workdir = Path(session["cwd"])
                driver.keep = True
            elif session is not None:
                driver.resume = (session["id"], session["cwd"], continues is None)
            elif agent in ("claude", "codex") and self.keeps_chats():
                folder = Path(self.registry.config.config_dir) / "agent_chats"
                driver.workdir = folder / task.task_id
                driver.workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
                driver.keep = True
            if hasattr(driver, "session_reported"):
                driver.session_reported.connect(
                    lambda sid: self._session_saved(task.task_id, sid)
                )
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
                "agent": name,
                "agent_key": agent,
            }
            self.chats[task.task_id] = chat
            self.agent_names[task.task_id] = name
            self.states[task.task_id] = f"Starting {name}…"
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
            driver.usage_reported.connect(
                lambda report: self._usage(task.task_id, report)
            )
            driver.turn_done.connect(lambda: self._done(task.task_id))
            driver.failed.connect(lambda error: self._failed(task.task_id, error))
            self.changed.emit()
            driver.start(gateway.discovery, binding)
        except Exception:
            if task.task_id in self.chats:
                self._failed(task.task_id, f"{name} could not be started.")
            else:
                if token is not None:
                    gateway.revoke_binding(token)
                directory.cleanup()
                self.service.cancel_task(task.task_id)
            raise
        return task.task_id

    def _session_saved(self, task_id, session_id):
        chat = self.chats.get(task_id)
        if chat is None or self.service._closed or self.service.failure:
            return
        try:
            self.service.store.record_event(
                task_id,
                "agent.session",
                {
                    "agent": chat["agent_key"],
                    "session_id": session_id,
                    "cwd": str(chat["driver"].cwd),
                },
            )
        except sqlite3.Error:
            self.service._fatal()

    @provider_callback
    def _request(self, task_id, request_id, payload):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        self._report(task_id)
        chat["requests"][request_id] = {"payload": payload, "claimed": False}
        self.states[task_id] = f"{chat['agent']} needs your input"
        self.requested.emit(task_id, request_id, payload)
        self.changed.emit()

    def _resolve_request(self, task_id, request_id):
        chat = self.chats.get(task_id)
        if chat is not None:
            self._report(task_id)
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
        name = chat["agent"]
        self.states[task_id] = (
            f"{name} replying…" if chat["current"] else f"{name} ready"
        )
        self.changed.emit()

    def status(self, task_id):
        return self.states.get(task_id, "")

    def connection(self, task_id):
        """Why a conversation is reachable, without inferring any model state."""
        chat = self.chats.get(task_id)
        return {
            "live": chat is not None,
            "reason": "" if chat is not None else self.stopped.get(task_id, ""),
            "detail": self.states.get(task_id, ""),
            "reported_at": self.reported.get(task_id),
            "queued": len(chat["queue"]) if chat is not None else 0,
            "requests": len(chat["requests"]) if chat is not None else 0,
        }

    def _report(self, task_id):
        """Note that the runtime reported something for this conversation."""
        self.reported.pop(task_id, None)
        self.reported[task_id] = time.time()
        while len(self.reported) > REPORTED_LIMIT:
            self.reported.pop(next(iter(self.reported)))

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
        return {"state": state, "queued": len(chat["queue"]), "agent": chat["agent"]}

    def model_info(self, task_id):
        """Only runtime reports count; the requested/default selection is no proof."""
        report = self.model_reports.get(task_id)
        if report is None:
            return None
        return {
            **report,
            "live": task_id in self.chats,
            "agent": self.agent_names.get(task_id, "the agent"),
        }

    def agent_name(self, task_id):
        return self.agent_names.get(task_id)

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

    def _usage(self, task_id, report):
        """Keep what a turn used in the task's journal (for Agent activity)."""
        chat = self.chats.get(task_id)
        if chat is None or self.service._closed or self.service.failure:
            return
        model = (self.model_reports.get(task_id) or {}).get("model")
        try:
            self.service.store.record_event(
                task_id,
                "agent.usage",
                {
                    **report,
                    "agent": chat["agent"],
                    **({"model": str(model)[:80]} if model else {}),
                },
            )
        except sqlite3.Error:
            self.service._fatal()

    def _model_reported(self, task_id, report):
        if task_id not in self.chats:
            return
        self._report(task_id)
        if report is None:
            self.model_reports.pop(task_id, None)
        else:
            self.model_reports[task_id] = dict(report)
        self.changed.emit()

    def _activity(self, task_id, state):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        self._report(task_id)
        if (
            chat["current"]
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
    def _ready(self, task_id, attempt=0):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        bound = getattr(chat["driver"], "bound_task_id", None) == task_id
        attempts = getattr(chat["driver"], "connect_attempts", 20)
        if bound and task_id not in self.service.external_tasks and attempt < attempts:
            # The runtime can report its MCP server before the gateway's Qt
            # callback records the connection; wait briefly, never assume it.
            QTimer.singleShot(100, lambda: self._ready(task_id, attempt + 1))
            return
        if not bound or task_id not in self.service.external_tasks:
            self._failed(task_id, f"{chat['agent']} connected to an unexpected task.")
            return
        self._report(task_id)
        chat["ready"] = True
        chat["activity"] = "ready"
        chat["binding_file"].unlink(missing_ok=True)
        self.states[task_id] = f"{chat['agent']} ready"
        self.changed.emit()
        self._next(task_id)

    @provider_callback
    def _send(self, task_id, message):
        chat = self.chats.get(task_id)
        if chat is None or message["message_id"] in chat["seen"]:
            return
        # Messages can arrive between the gateway connection and the agent's
        # handshake; _ready drains them in order.
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
        self.states[task_id] = f"{chat['agent']} replying… (follow-up messages queue)"
        self.changed.emit()
        try:
            chat["driver"].send(message["message_id"], message["text"])
        except (RuntimeError, ValueError, OSError):
            self._failed(
                task_id,
                f"{chat['agent']} could not accept this message; it will not be retried.",
            )

    @provider_callback
    def _accepted(self, task_id, message_id):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        self._report(task_id)
        if chat["current"] is None or chat["current"]["message_id"] != message_id:
            self._failed(
                task_id, "Unexpected agent turn; messages will not be retried."
            )
            return
        chat["activity"] = "thinking"
        self.service.messages.acknowledge(task_id, {"messages": [chat["current"]]})
        self.service.changed.emit()

    def _message(self, task_id, item_id, text, final):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        self._report(task_id)
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
                            "id": "agent_" + uuid4().hex,
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
        self._report(task_id)
        try:
            for item in chat["items"].values():
                item["final"] = True
            self._flush_task(task_id)
            chat["current"] = None
            chat["activity"] = "ready"
            chat["discard_reply"] = False
            chat["items"].clear()
            self.states[task_id] = f"{chat['agent']} ready"
            self.changed.emit()
            self.replied.emit(task_id)
            # Let the provider consume the rest of its notification batch first.
            QTimer.singleShot(0, lambda: self._next(task_id))
        except (ValueError, RuntimeError, sqlite3.Error):
            self._failed(
                task_id, "Chat storage is unavailable; no work will be retried."
            )

    def _failed(self, task_id, error):
        chat = self.chats.get(task_id)
        if chat is None:
            return
        self.stopped[task_id] = "failed"
        self.states[task_id] = error
        self._stop(task_id)
        self._end_chat(task_id, chat["current"] is not None)
        self.changed.emit()

    def _end_chat(self, task_id, interrupted, reason=""):
        """A turn cut off mid-flight, or a chat Steed closed, is an
        interruption, never a cancellation the person did not ask for."""
        if self.service._closed or self.service.failure:
            return
        try:
            if self.service.store.get(task_id).status not in ACTIVE_STATUSES:
                return
            if interrupted or reason:
                self.service.interrupt_task(task_id, reason)
            else:
                self.service.cancel_task(task_id)
        except sqlite3.Error:
            self.service._fatal()

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
                    self.stopped[task_id] = "ended"
                    self.states[task_id] = f"{self.chats[task_id]['agent']} chat ended"
                    self._stop(task_id)
                    self.changed.emit()
        except sqlite3.Error:
            self.service._fatal()

    def close(self):
        require_owner_thread(self)
        for task_id in tuple(self.chats):
            self.stopped[task_id] = "closed"
            self.states[task_id] = f"{self.chats[task_id]['agent']} disconnected"
            chat = self.chats.get(task_id)
            self._stop(task_id)
            self._end_chat(
                task_id, chat is not None and chat["current"] is not None, "closed"
            )
        self.changed.emit()
