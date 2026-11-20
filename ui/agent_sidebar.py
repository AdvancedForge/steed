"""HTML agent conversations backed by the existing Python task authority."""

import re
import sqlite3
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDockWidget

from services.conversation_health import (
    connection_state,
    delivery_label,
    latest_failure,
)
from services.task_store import ACTIVE_STATUSES
from ui.agent_request import AgentRequestDialog
from ui.html_surface import HtmlSurface
from ui.task_dialog import TaskDialog

_ERRORS = (sqlite3.Error, OSError, RuntimeError, ValueError)
# Model choices per native agent; None keeps the agent's own default.
_AGENT_MODELS = {
    "codex": (None, "gpt-5.4"),
    "claude": (None, "opus", "sonnet", "haiku"),
}


def ended_state(task_status, reason="", *, cut_off=False, can_continue=False):
    """How a chat that is no longer live reads: (title, hint). Only reasons
    Steed itself knows (an error, a closed conversation, Steed closing it);
    nothing is inferred about the agent, and nothing is ever replayed."""
    title = {
        "closed": "Chat closed",
        "failed": "Stopped after an error",
        "ended": "Chat ended",
    }.get(reason) or {
        "interrupted": "Chat interrupted",
        "cancelled": "Chat stopped",
        "failed": "Stopped after an error",
    }.get(task_status, "Chat ended")
    hint = "Steed closed this chat. " if reason == "closed" else ""
    hint += "The last reply was cut off. " if cut_off else ""
    hint += (
        "Continue this chat to pick up where it left off."
        if can_continue
        else "Messages are saved. Start a new chat to keep going."
    )
    return title, hint


def count(number, word):
    """ "1 message", "2 messages"."""
    return f"{number} {word}{'' if number == 1 else 's'}"


def continued_root(task_id, continued, limit=50):
    """The first chat of a chain of continuations."""
    earlier = {new: old for old, new in continued.items()}
    for _ in range(limit):
        if task_id not in earlier:
            break
        task_id = earlier[task_id]
    return task_id


def chat_choice(task, external, opener=None):
    """One row of the chat list: what it was about, which agent, when."""
    objective = " ".join(task.objective.split())
    agent, _, title = objective.partition(": ")
    if not title:
        agent, title = "", objective
    if title.endswith(" conversation"):
        agent, title = agent or title.removesuffix(" conversation"), "Untitled chat"
    if opener and title in ("Untitled chat", "Untitled session"):
        # Markdown punctuation is not part of a title.
        words = re.sub(r"[|#*`>_~]+", " ", opener).split()
        title = " ".join(words) or title
        title = title if len(title) <= 80 else title[:79] + "…"
    live = external and task.status in ACTIVE_STATUSES
    return {
        "id": task.task_id,
        "title": title[:80],
        "agent": agent,
        "when": task.updated_at,
        "live": live,
        "status": "" if live or task.status in ACTIVE_STATUSES else task.status,
        # Kept for anything that still shows one line.
        "label": f"{title[:80]}{' · ' + agent if agent else ''}",
    }


def reported_at_text(connection):
    """When the agent last reported, without judging what it was doing."""
    reported_at = (connection or {}).get("reported_at")
    if isinstance(reported_at, bool) or not isinstance(reported_at, (int, float)):
        return ""
    try:
        return time.strftime("%H:%M:%S", time.localtime(reported_at))
    except (OverflowError, OSError, ValueError):
        return ""


class AgentSidebar(QDockWidget):
    """Share one HTML surface between the sidebar and the agent tab."""

    setup_requested = pyqtSignal()

    def __init__(self, service, parent=None, *, providers=None):
        super().__init__("Agents", parent)
        self.service = service
        self.browser_window = parent
        self.providers = providers
        self.setObjectName("externalAgentSidebar")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.setMinimumWidth(300)
        self._task_id = None
        self._messages = {}
        self._cursor = 0
        self._drafts = {}
        self._draft_versions = {}
        self._private = False
        self._context = 0
        self._choices = []
        self._request_dialogs = {}
        self._recovery_dialog = None
        self._new_model = None
        self._new_agent = "codex"
        self._agent_picked = False
        self._tab_tokens = {}
        self._send_result = {"context": -1, "version": -1}
        self._error = ""
        self._load_failed = False
        self._dismissed_failures = {}
        self._detached = False
        self._state = {}
        self.surface = HtmlSurface("agents", self)
        self.surface.command.connect(self._on_command)
        self.surface.reloading.connect(self._reload_context)
        self.setWidget(self.surface)
        self.service.changed.connect(self.refresh)
        if self.providers is not None:
            self.providers.changed.connect(self.refresh)
            self.providers.requested.connect(self._request_added)
        self.refresh()

    @property
    def state(self):
        """Presentation data only; authority remains in the task service."""
        return self._state

    @property
    def draft(self):
        return self._drafts.get(self._task_id, "")

    @property
    def transcript_text(self):
        return "\n\n".join(
            f"{'You' if m['role'] == 'user' else 'Agent'}"
            f"{(' · ' + m['state']) if m['state'] != 'complete' else ''}\n{m['text']}"
            for m in sorted(
                self._messages.values(), key=lambda m: m["created_sequence"]
            )
            if m["state"] != "cleared"
        )

    def set_draft(self, text, *, version=None):
        if self._private or not isinstance(text, str) or len(text) > 32768:
            return False
        current = self._draft_versions.get(self._task_id, 0)
        if version is None:
            version = current + 1
        if type(version) is not int or not current < version <= 2**53 - 1:
            return False
        self._drafts[self._task_id] = text
        self._draft_versions[self._task_id] = version
        self._publish()
        return True

    def select_task(self, task_id):
        if self._private or task_id not in (None, *(t["id"] for t in self._choices)):
            return
        if task_id == self._task_id:
            return
        if self._recovery_dialog is not None:
            self._recovery_dialog.reject()
        self._task_id = task_id
        self._context += 1
        self._messages = {}
        self._cursor = 0
        self._error = ""
        self.refresh()

    def _agent_choices(self):
        choices = getattr(self.providers, "agent_choices", None)
        if choices is None:
            return [("codex", "Codex"), ("claude", "Claude Code")]
        return choices()

    def set_model(self, model):
        if model in _AGENT_MODELS.get(self._new_agent, (None,)):
            self._new_model = model
            self._publish()

    def set_agent(self, agent):
        if agent in dict(self._agent_choices()) and agent != self._new_agent:
            self._agent_picked = True
            self._new_agent = agent
            self._new_model = None
            self._publish()

    def _reload_context(self):
        if self._recovery_dialog is not None:
            self._recovery_dialog.reject()
        self._context += 1
        self.refresh()

    def _on_command(self, command):
        """Accept exact UI commands, pinned to the displayed conversation."""
        action = command.get("action")
        fields = {
            "draft": {"value", "version"},
            "send": {"value", "version", "attach"},
            "select": {"value"},
            "model": {"value"},
            "agent": {"value"},
        }
        operations = {
            "new_chat": self.start_chat,
            "continue_session": self.continue_session,
            "continue_chat": self.continue_chat,
            "acp_agents": self.manage_acp_agents,
            "models": self.manage_models,
            "add_assistant": self.add_assistant,
            "pause_agents": lambda: self.set_agents_paused(True),
            "bridge": lambda: self.browser_window._open_bridge(),
            "resume_agents": lambda: self.set_agents_paused(False),
            "setup": self.setup_requested.emit,
            "requests": self.show_requests,
            "permissions": lambda: self.open_details("Permissions"),
            "files": lambda: self.open_details("Workspace"),
            "receipts": lambda: self.open_details("Apps"),
            "delegate": self.delegate_tabs,
            "cancel": self.cancel_task,
            "clear": self.clear_messages,
            "dismiss_failure": self.dismiss_failure,
            "recover": self.recover_conversation,
            "library": self.open_library,
            "open_tab": lambda: self.change_display("_open_agent_tab"),
            "show_sidebar": lambda: self.change_display("_toggle_agent_sidebar"),
        }
        if (
            self._detached
            or self._private
            or self.service._closed
            or self.service.failure
            or type(command.get("context")) is not int
            or command["context"] != self._context
            or not isinstance(action, str)
            or action not in fields.keys() | operations.keys()
            or not (
                set(command) == {"action", "context"} | fields.get(action, set())
                or (
                    action == "send"
                    and set(command) == {"action", "context", "value", "version"}
                )
            )
        ):
            return
        if action in ("draft", "send"):
            if self.set_draft(command["value"], version=command["version"]):
                if action == "send":
                    attach = command.get("attach") or []
                    if (
                        isinstance(attach, list)
                        and attach
                        and all(isinstance(t, str) for t in attach)
                    ):
                        self.send_with_tabs(attach[:5])
                    else:
                        self.send_message()
        elif action == "select" and isinstance(command["value"], str):
            self.select_task(command["value"] or None)
        elif action == "model" and isinstance(command["value"], str):
            self.set_model(command["value"] or None)
        elif action == "agent" and isinstance(command["value"], str):
            self.set_agent(command["value"])
        elif action in operations:
            operations[action]()

    def _base_state(self):
        available = (
            not self._private and not self.service._closed and not self.service.failure
        )
        return {
            "available": available,
            "private": self._private,
            "usable": False,
            "active": False,
            "has_provider": self.providers is not None,
            "yolo": self.service.auto_approve_requests,
            "paused": getattr(self.service, "agents_paused", False),
            "bridge": self._bridge_state(),
            "tasks": self._choices if available else [],
            "task_id": self._task_id,
            "messages": [],
            "pending": 0,
            "requests": 0,
            "can_cancel": False,
            "can_delegate": False,
            "can_draft": False,
            "connection": {"state": "unavailable", "label": "", "tone": "neutral"},
            "delivery": "",
            "failure": None,
            "can_library": available
            and hasattr(self.browser_window, "_open_file_library"),
            "status": (
                "Agent conversations are disabled in incognito."
                if self._private
                else "Choose a conversation, or start a new chat."
            ),
            "summary": "",
            "activity": {"title": "", "hint": "", "model": "", "busy": False},
            "ended": False,
            "can_continue": False,
            "agent_name": "",
            "earlier": [],
        }

    def _publish(self):
        if self._detached:
            return
        choices = self._agent_choices()
        where = {
            key: place
            for key, _, place in getattr(self.providers, "assistants", list)()
        }
        if choices and (not self._agent_picked or self._new_agent not in dict(choices)):
            # Until you pick one, new chats use the first: a model you added,
            # else Codex.
            if self._new_agent != choices[0][0]:
                self._new_agent, self._new_model = choices[0][0], None
        self._state.update(
            context=self._context,
            draft=self.draft,
            draft_version=self._draft_versions.get(self._task_id, 0),
            new_model=self._new_model,
            new_agent=self._new_agent,
            agents=[
                {
                    "id": key,
                    "name": name,
                    "models": [
                        [model or "", model.title() if model else f"{name} default"]
                        for model in _AGENT_MODELS.get(key, (None,))
                    ],
                    "where": where.get(key, "Coding agents"),
                }
                for key, name in choices
            ],
            send_result=dict(self._send_result),
            mention_tabs=[] if self._private else self._mention_tabs(),
            error=self._error,
            dark=bool(
                getattr(
                    getattr(self.browser_window, "config", None), "dark_mode", False
                )
            ),
        )
        self.surface.publish(dict(self._state))

    def _apply_style(self):
        self._publish()

    def set_private(self, private):
        if self._private == private:
            return
        self.surface.conceal()
        if self._recovery_dialog is not None:
            self._recovery_dialog.reject()
        self._private = private
        self._context += 1
        if private:
            self._drafts.clear()
            self._draft_versions.clear()
            self._dismissed_failures.clear()
            self._task_id = None
            self._messages = {}
            self._cursor = 0
            self._error = ""
        self.refresh()

    def refresh(self):
        if self._detached:
            return
        available = (
            not self._private and not self.service._closed and not self.service.failure
        )
        for key, dialog in list(self._request_dialogs.items()):
            pending = (
                self.providers.chats.get(key[0], {}).get("requests", {})
                if self.providers
                else {}
            )
            if key[1] not in pending:
                dialog.answered = True
                dialog.reject()
            elif not available:
                dialog.reject()
        try:
            records = self.service.store.list_tasks() if available else []
            records = [
                t
                for t in records
                if t.task_id in self.service.external_tasks
                or t.task_id == self._task_id
            ] + [
                t
                for t in records[-100:]
                if t.task_id not in self.service.external_tasks
                and t.task_id != self._task_id
            ]
            # A chat that was continued is shown as the newer one.
            continued = self.service.store.continuations() if records else {}
            records.sort(key=lambda t: t.updated_at, reverse=True)
            openers = self.service.messages.openers() if records else {}
            self._choices = [
                chat_choice(
                    task,
                    task.task_id in self.service.external_tasks,
                    openers.get(continued_root(task.task_id, continued))
                    or openers.get(task.task_id),
                )
                for task in records
                if task.task_id not in continued or task.task_id == self._task_id
            ]
            if self._task_id not in (t["id"] for t in self._choices):
                if self._task_id is not None:
                    self._context += 1
                self._task_id = None
                self._messages = {}
                self._cursor = 0
            self._refresh_conversation()
            if self._load_failed:
                self._error = ""
                self._load_failed = False
        except _ERRORS:
            self._messages = {}
            self._cursor = 0
            self._load_failed = True
            self._state = self._base_state()
            self._state["status"] = "Conversation is unavailable."
            self._error = (
                "Conversation could not be loaded. Your browsing is still available."
            )
        self._publish()

    def _refresh_conversation(self):
        state = self._base_state()
        task_id = self._task_id
        if task_id is None or not state["available"]:
            self._state = state
            return
        task = self.service.store.get(task_id)
        active = (
            task.status in ACTIVE_STATUSES
            and task_id in self.service.external_tasks
            and (self.providers is None or self.providers.can_send(task_id))
        )
        while True:
            page = self.service.messages.read(task_id, self._cursor)
            for message in page["messages"]:
                self._messages[message["message_id"]] = message
            self._cursor = page["cursor"]
            if not page["has_more"]:
                break
        grants = self.service.store.grants(task_id)
        permission = grants[-1] if grants else None
        profile = (
            permission.scope.get("profile", "isolated task profile")
            if permission
            else "No profile access"
        )
        status = (
            f"{'Connected' if active else 'Read-only history · ' + task.status}\n"
            f"{profile} · Permission: {permission.status if permission else 'not requested'}"
        )
        if self.providers is not None and self.providers.status(task_id):
            status = self.providers.status(task_id) + "\n" + status
        read = self.service.messages.read_sequence(task_id)
        waiting = sum(
            m["role"] == "user" and m["state"] != "cleared" and m["sequence"] > read
            for m in self._messages.values()
        )
        connection = (
            self.providers.connection(task_id)
            if self.providers is not None and hasattr(self.providers, "connection")
            else None
        )
        stopped = bool(connection and not connection["live"] and connection["reason"])
        if waiting:
            # Once the chat has stopped, nothing will read them: say so.
            status += (
                f"\n{count(waiting, 'unread message')}; this chat has stopped"
                if stopped
                else f"\n{count(waiting, 'message')} waiting for the agent to read"
            )
        elif any(
            m["role"] == "user" and m["state"] != "cleared"
            for m in self._messages.values()
        ):
            status += "\nAgent fetched your sent messages"
        counts = self.service.messages.summary(task_id)
        ended = {}
        if task.status not in ACTIVE_STATUSES:
            closing = self.service.store.recent_events(
                task_id, limit=1, kinds=(f"task.{task.status}",)
            )
            ended = {
                "reason": closing[-1].payload.get("reason", "") if closing else "",
                "cut_off": any(
                    m["state"] == "interrupted" for m in self._messages.values()
                ),
                "can_continue": bool(
                    self.providers is not None
                    and hasattr(self.providers, "continuable")
                    and self.providers.continuable(task_id)
                ),
            }
        requests = (
            len(self.providers.chats.get(task_id, {}).get("requests", {}))
            if self.providers
            else 0
        )
        state.update(
            usable=True,
            active=active,
            can_draft=True,
            connection=connection_state(
                task.status,
                task_id in self.service.external_tasks,
                self.providers.activity(task_id)
                if self.providers and hasattr(self.providers, "activity")
                else None,
                self.service.resumable.get(task_id),
            ),
            delivery=(
                f"{count(waiting, 'message')} waiting to be read"
                if waiting and active
                else f"{count(waiting, 'message')} not read before the agent went away"
                if waiting
                else ""
            ),
            failure=latest_failure(
                self.service.store, task_id, self._dismissed_failures.get(task_id, 0)
            ),
            messages=[
                {
                    "id": m["message_id"],
                    "role": m["role"],
                    "text": m["text"],
                    "state": m["state"],
                    "delivery": delivery_label(
                        m, read, active=active, provider=self.providers, task_id=task_id
                    ),
                }
                for m in sorted(
                    self._messages.values(), key=lambda m: m["created_sequence"]
                )
                if m["state"] != "cleared"
            ],
            pending=counts["pending"],
            requests=requests,
            can_cancel=task.status in ACTIVE_STATUSES,
            can_delegate=active and hasattr(self.browser_window, "delegate_tabs"),
            status=status,
            summary=", ".join(
                (
                    count(counts["pending"], "pending approval"),
                    count(counts["files"], "file"),
                    count(counts["artifacts"], "draft"),
                    count(counts["receipts"], "app receipt"),
                )
            ),
            activity=self._activity(active, counts["pending"], task.status, ended),
            ended=task.status not in ACTIVE_STATUSES,
            agent_name=(
                self.providers.agent_name(task_id)
                if self.providers is not None and hasattr(self.providers, "agent_name")
                else None
            )
            or chat_choice(task, False)["agent"]
            or "Agent",
            can_continue=ended.get("can_continue", False),
            earlier=self._earlier_messages(task_id),
        )
        self._state = state

    def _earlier_messages(self, task_id, limit=10):
        """Messages of the chats this one continues, oldest first; shown
        above its own, read-only."""
        chain = []
        for _ in range(limit):
            link = self.service.store.recent_events(
                task_id, limit=1, kinds=("conversation.continues",)
            )
            if not link:
                break
            task_id = link[-1].payload.get("task_id")
            try:
                self.service.store.get(task_id)
            except (KeyError, ValueError):
                break
            chain.append(task_id)
        earlier = []
        for previous in reversed(chain):
            rows, cursor = {}, 0
            while True:
                page = self.service.messages.read(previous, cursor)
                for m in page["messages"]:
                    rows[m["message_id"]] = m
                cursor = page["cursor"]
                if not page["has_more"]:
                    break
            earlier += [
                {
                    "id": f"{previous[:8]}-{m['message_id']}",
                    "role": m["role"],
                    "text": m["text"],
                    "state": m["state"],
                    "delivery": "",
                }
                for m in sorted(rows.values(), key=lambda m: m["created_sequence"])
                if m["state"] != "cleared"
            ]
        return earlier

    def _activity(self, active, pending=0, task_status="", ended=None):
        ended = ended or {}
        model = (
            self.providers.model_info(self._task_id)
            if self.providers is not None and hasattr(self.providers, "model_info")
            else None
        )
        if model:
            text = "Model: " + model["model"]
            if model.get("provider"):
                text += " · " + model["provider"]
            agent = model.get("agent", "the agent")
            text += "" if model["live"] else " (last reported)"
            if model["source"] == "rerouted":
                text += f"\n{agent} rerouted this turn from {model['from_model']}."
        else:
            text = ""
        report = (
            self.providers.activity(self._task_id)
            if self.providers is not None and hasattr(self.providers, "activity")
            else None
        )
        state = report["state"] if report else "external" if active else "history"
        name = (
            (report or {}).get("agent")
            or (
                self.providers.agent_name(self._task_id)
                if self.providers is not None and hasattr(self.providers, "agent_name")
                else None
            )
            or "The agent"
        )
        title, hint = {
            "connecting": (f"Connecting to {name}…", ""),
            "sending": ("Sending…", ""),
            "thinking": (f"{name} is thinking…", ""),
            "working": (
                f"{name} is working…",
                "You can keep browsing or queue a follow-up.",
            ),
            "replying": (f"{name} is replying…", ""),
            "waiting": (
                f"{name} needs your input",
                "Answer it with the button above the chat.",
            ),
            "ready": (f"{name} is ready", ""),
            "external": (
                "External agent connected",
                "Messages wait in Steed until it fetches them. Steed can't see what it is doing.",
            ),
            "history": (
                "Conversation ended",
                "Saved messages and local drafts stay available. Use Continue / hand off to prepare a reviewed continuation.",
            ),
        }.get(
            state,
            (
                "Agent status unavailable",
                "Open Conversation & settings for connection details.",
            ),
        )
        if report and report["queued"]:
            queued = count(report["queued"], "follow-up message")
            hint = f"{hint} {queued} queued.".strip()
        if pending and active and not (report and report["state"] == "waiting"):
            title = "Approval requested"
            hint = "Review it with the button above the chat. The agent may keep working meanwhile."
            state = "approval"
        resume_until = self.service.resumable.get(self._task_id)
        if not report and not active and task_status in ACTIVE_STATUSES:
            title = "Agent disconnected"
            hint = "Reconnect it from Connection setup. Your messages and draft are kept, and nothing is resent."
            if resume_until is not None:
                title = "Agent disconnected, waiting for it to return"
                hint = (
                    "Its access ended at the disconnect. The agent's next call reconnects and resumes this task until "
                    + time.strftime("%H:%M", time.localtime(resume_until))
                    + "; after that Steed cancels it. Your conversation is kept."
                )
        connection = (
            self.providers.connection(self._task_id)
            if self.providers is not None and hasattr(self.providers, "connection")
            else None
        )
        reason = (connection or {}).get("reason", "") or ended.get("reason", "")
        detail = (connection or {}).get("detail", "")
        if not report and not active and (reason or task_status not in ACTIVE_STATUSES):
            title, hint = ended_state(
                task_status,
                reason,
                cut_off=ended.get("cut_off", False),
                can_continue=ended.get("can_continue", False),
            )
            if reason == "failed" and detail:
                hint = f"{detail} {hint}"
        stamp = reported_at_text(connection)
        if stamp:
            hint = (hint + " " if hint else "") + f"Last agent update {stamp}."
        return {
            "title": title,
            "hint": hint,
            "model": text,
            "busy": state
            in ("connecting", "sending", "thinking", "working", "replying"),
        }

    def dismiss_failure(self):
        failure = self.state.get("failure")
        if failure is not None:
            self._dismissed_failures[self._task_id] = failure["sequence"]
            self.refresh()

    def recover_conversation(self):
        if not self.state.get("usable") or self._private or self._detached:
            return
        if self._recovery_dialog is not None:
            self._recovery_dialog.raise_()
            return
        from ui.conversation_recovery import ConversationRecoveryDialog

        dialog = ConversationRecoveryDialog(self)
        self._recovery_dialog = dialog
        dialog.finished.connect(lambda *_: setattr(self, "_recovery_dialog", None))
        dialog.show()

    def _request_added(self, task_id, request_id, payload):
        if task_id == self._task_id and not self._private and self.isVisible():
            self._show_request(task_id, request_id, payload)

    def change_display(self, method):
        if self.browser_window is not None:
            if method == "_toggle_agent_sidebar":
                self.browser_window._toggle_agent_sidebar(True)
            else:
                self.browser_window._open_agent_tab()

    def open_library(self):
        if self.state.get("can_library"):
            self.browser_window._open_file_library()

    def show_requests(self):
        if self._private or self.providers is None:
            return
        chat = self.providers.chats.get(self._task_id, {})
        for request_id, request in list(chat.get("requests", {}).items()):
            self._show_request(self._task_id, request_id, request["payload"])

    def _show_request(self, task_id, request_id, payload):
        if not self.providers.claim_request(task_id, request_id):
            return
        try:
            dialog = AgentRequestDialog(
                payload,
                lambda result: self.providers.respond(task_id, request_id, result),
                self,
            )
        except (ValueError, KeyError, TypeError):
            self._error = "This agent request could not be displayed. Cancel the task."
            self._publish()
            return
        agent = payload.get("agent", "Codex")
        dialog.setWindowTitle(f"{agent} request · {task_id[:8]}")
        self._request_dialogs[(task_id, request_id)] = dialog
        dialog.finished.connect(
            lambda: self._request_dialogs.pop((task_id, request_id), None)
        )
        dialog.show()

    def start_chat(self):
        if self._private or self.providers is None or not self.state.get("available"):
            return
        if self._new_agent.startswith("web:"):
            site = next(
                (
                    s
                    for s in self.providers.website_chats()
                    if "web:" + s["name"] == self._new_agent
                ),
                None,
            )
            if site is not None:
                self.browser_window._open_website_assistant(site["url"])
            return
        try:
            task_id = self.providers.new_chat(
                model=self._new_model, agent=self._new_agent
            )
            self.refresh()
            self.select_task(task_id)
        except _ERRORS as error:
            self._error = str(error)
            self._publish()

    def continue_chat(self):
        """Carry on a closed chat whose Claude Code conversation was saved."""
        if (
            self._private
            or self.providers is None
            or not self.state.get("can_continue")
            or self._task_id is None
        ):
            return
        try:
            task_id = self.providers.new_chat(continues=self._task_id)
            self.refresh()
            self.select_task(task_id)
        except _ERRORS as error:
            self._error = str(error)
            self._publish()

    def continue_session(self):
        """Pick a saved Claude Code session and go on with it here."""
        if self._private or self.providers is None or not self.state.get("available"):
            return
        from ui.agent_sessions import ClaudeSessionsDialog

        dialog = ClaudeSessionsDialog(self.providers, self.browser_window)
        dialog.started.connect(
            lambda task_id: (self.refresh(), self.select_task(task_id))
        )
        present = getattr(self.browser_window, "_present_dialog", None)
        if present is not None:
            present(dialog, modal=False)
        else:
            dialog.show()

    def _bridge_state(self):
        registry = getattr(
            getattr(self.browser_window, "profile_manager", None), "registry", None
        )
        bridge = getattr(registry, "agent_bridge_link", None)
        return bool(bridge is not None and bridge.state in ("starting", "on"))

    def set_agents_paused(self, paused):
        try:
            self.service.set_agents_paused(paused)
        except _ERRORS as error:
            self._error = str(error)
        self.refresh()

    def add_assistant(self):
        from ui.add_assistant import AddAssistantDialog

        dialog = AddAssistantDialog(self.browser_window)
        dialog.chosen.connect(self._assistant_chosen)
        self._show_agent_dialog(dialog)

    def _assistant_chosen(self, kind, detail):
        from ui.agent_sessions import ModelsDialog

        config = self.browser_window.config
        if kind == "local":
            self._show_agent_dialog(
                ModelsDialog(config, self.browser_window, start="local", address=detail)
            )
        elif kind == "key":
            self._show_agent_dialog(
                ModelsDialog(config, self.browser_window, start="key")
            )
        elif kind == "coding":
            self.manage_acp_agents()
        elif kind == "website":
            self.browser_window._choose_website_assistant()
        elif kind == "cloud":
            self.browser_window._open_bridge()

    def manage_models(self):
        from ui.agent_sessions import ModelsDialog

        self._show_agent_dialog(
            ModelsDialog(self.browser_window.config, self.browser_window)
        )

    def manage_acp_agents(self):
        from ui.agent_sessions import AcpAgentsDialog

        self._show_agent_dialog(
            AcpAgentsDialog(self.browser_window.config, self.browser_window)
        )

    def _show_agent_dialog(self, dialog):
        dialog.finished.connect(lambda _: self._publish())
        present = getattr(self.browser_window, "_present_dialog", None)
        if present is not None:
            host = present(dialog, modal=False)
            # The list of agents for new chats follows what was added.
            host.finished.connect(lambda _: self._publish())
        else:
            dialog.show()

    def delegate_tabs(self):
        from PyQt6.QtWidgets import QInputDialog

        if not self.state.get("can_delegate"):
            return
        choice, accepted = QInputDialog.getItem(
            self,
            "Give this agent live tab control",
            "Tabs to delegate:",
            ["Current tab", "All open website tabs on this profile (up to 10)"],
            0,
            False,
        )
        if not accepted:
            return
        try:
            self.browser_window.delegate_tabs(
                self._task_id, whole_profile=choice != "Current tab"
            )
        except _ERRORS as error:
            self._error = str(error)
        else:
            self._error = ""
        self._publish()

    def _mention_tabs(self):
        """The window's web tabs, to @-mention in a message: [{id, title, host}]."""
        window = self.browser_window
        tabs = [
            tab
            for tab in getattr(window, "tabs", [])
            if tab.url().scheme() in ("http", "https")
        ][:30]
        self._tab_tokens = {f"tab-{id(tab)}": tab for tab in tabs}
        return [
            {
                "id": token,
                "title": (tab.title() or tab.url().host())[:80],
                "host": tab.url().host(),
            }
            for token, tab in self._tab_tokens.items()
        ]

    def send_with_tabs(self, tokens):
        """Send the draft with the @-mentioned tabs' title, address and text,
        sharing the room an 8 KiB message has."""
        from browser import webengine

        tabs = [
            self._tab_tokens.get(token)
            for token in tokens
            if self._tab_tokens.get(token) in getattr(self.browser_window, "tabs", [])
        ]
        if not tabs:
            self.send_message()
            return
        texts = {}
        task_id, draft = self._task_id, self.draft

        def read(tab, value):
            texts[id(tab)] = value if isinstance(value, str) else ""
            if len(texts) < len(tabs) or self._task_id != task_id:
                return
            room = 7600 - len(draft.encode("utf-8"))
            share = max(200, room // len(tabs) - 200)
            parts = [draft.rstrip()]
            for tab in tabs:
                body = " ".join(texts[id(tab)].split())
                clipped = body.encode("utf-8")[:share].decode("utf-8", "ignore")
                more = " …(more on the page)" if len(clipped) < len(body) else ""
                parts.append(
                    f"[Attached tab: {tab.title()[:120]} — {tab.url().toString()[:300]}]\n"
                    + clipped
                    + more
                )
            self._drafts[task_id] = "\n\n".join(parts)
            self.send_message()

        for tab in tabs:
            webengine.run_script(
                tab.page(),
                "document.body ? document.body.innerText : ''",
                lambda value, tab=tab: read(tab, value),
                world=webengine.STEED_WORLD,
            )

    def send_message(self):
        if not self.state.get("active") or self._private or self._detached:
            return
        try:
            self.service.send_message(self._task_id, self.draft)
        except _ERRORS:
            if not self.draft.strip():
                self._error = "Enter a message before sending."
            elif len(self.draft.encode("utf-8", errors="replace")) > 8192:
                self._error = "Message exceeds 8 KiB of UTF-8 text. Shorten it; your draft is kept."
            elif self._task_id not in self.service.external_tasks:
                self._error = "Agent disconnected. Your draft is kept; use Connection setup before sending."
            elif not self.state.get("can_cancel"):
                self._error = "This task has ended. Your draft is kept; prepare a continuation for another chat."
            else:
                self._error = "Message could not be queued. Your draft is kept; check conversation limits and connection details."
        else:
            self._drafts.pop(self._task_id, None)
            self._error = ""
        self._send_result = {
            "context": self._context,
            "version": self._draft_versions.get(self._task_id, 0),
        }
        self._publish()

    def cancel_task(self):
        if self.state.get("can_cancel"):
            self._perform(self.service.cancel_task)

    def clear_messages(self):
        self._perform(self.service.clear_messages)

    def _perform(self, operation):
        if self._task_id is None or self._private or not self.state.get("usable"):
            return
        try:
            operation(self._task_id)
        except _ERRORS:
            self._error = "Task operation could not be completed."
            self._publish()

    def open_details(self, panel):
        if self._task_id is None or self._private or not self.state.get("usable"):
            return
        try:
            dialog = TaskDialog(self.service, self)
        except _ERRORS:
            self._error = "Task details could not be opened."
            self._publish()
            return
        for row, (task, _) in enumerate(dialog._rows):
            if task.task_id == self._task_id:
                dialog.table.selectRow(row)
                break
        for index in range(dialog.panels.count()):
            if dialog.panels.tabText(index) == panel:
                dialog.panels.setCurrentIndex(index)
                break
        dialog.setModal(False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def detach(self):
        if self._detached:
            return
        self._detached = True
        if self._recovery_dialog is not None:
            self._recovery_dialog.reject()
        for key, dialog in list(self._request_dialogs.items()):
            dialog.reject()
            if self.providers and key[0] in self.providers.chats:
                request = self.providers.chats[key[0]]["requests"].get(key[1])
                if request:
                    request["claimed"] = False
        self.service.changed.disconnect(self.refresh)
        if self.providers is not None:
            self.providers.changed.disconnect(self.refresh)
            self.providers.requested.disconnect(self._request_added)
        self._drafts.clear()
        self._messages.clear()
        self.surface.dispose()
