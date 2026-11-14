"""HTML agent conversations backed by the existing Python task authority."""

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
            "send": {"value", "version"},
            "select": {"value"},
            "model": {"value"},
            "agent": {"value"},
        }
        operations = {
            "new_chat": self.start_chat,
            "continue_session": self.continue_session,
            "acp_agents": self.manage_acp_agents,
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
            or set(command) != {"action", "context"} | fields.get(action, set())
        ):
            return
        if action in ("draft", "send"):
            if self.set_draft(command["value"], version=command["version"]):
                if action == "send":
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
        }

    def _publish(self):
        if self._detached:
            return
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
                }
                for key, name in self._agent_choices()
            ],
            send_result=dict(self._send_result),
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
            self._choices = []
            for task in reversed(records):
                connected = (
                    task.task_id in self.service.external_tasks
                    and task.status in ACTIVE_STATUSES
                )
                self._choices.append(
                    {
                        "id": task.task_id,
                        "label": f"{'Connected' if connected else task.status} · {' '.join(task.objective.split())[:80]} · {task.task_id[:8]}",
                    }
                )
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
        if waiting:
            status += f"\n{waiting} message(s) waiting for the agent to read"
        elif any(
            m["role"] == "user" and m["state"] != "cleared"
            for m in self._messages.values()
        ):
            status += "\nAgent fetched your sent messages"
        counts = self.service.messages.summary(task_id)
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
                f"{waiting} message(s) waiting to be read"
                if waiting and active
                else f"{waiting} message(s) not read before the connection became unavailable"
                if waiting
                else "Sent messages were read; this does not confirm completed work."
                if any(
                    m["role"] == "user" and m["state"] != "cleared"
                    for m in self._messages.values()
                )
                else "Only messages you send are shared."
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
            summary=f"{counts['pending']} pending approval(s) · {counts['files']} file(s) · {counts['artifacts']} draft(s) · {counts['receipts']} app receipt(s)",
            activity=self._activity(active, counts["pending"], task.status),
        )
        self._state = state

    def _activity(self, active, pending=0, task_status=""):
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
            text += f" (reported by {agent})" if model["live"] else " (last reported)"
            if model["source"] == "rerouted":
                text += f"\n{agent} rerouted this turn from {model['from_model']}."
        else:
            text = "Model: not reported by the agent"
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
            "connecting": (
                f"Connecting to {name}…",
                "Starting the agent and its Steed connection.",
            ),
            "sending": ("Sending…", f"Waiting for {name} to accept your message."),
            "thinking": (
                f"{name} is thinking…",
                "Your message was accepted; the agent is preparing its next step.",
            ),
            "working": (
                f"{name} is working…",
                "The agent is using tools. You can browse or queue a follow-up.",
            ),
            "replying": (
                f"{name} is replying…",
                "The reply is arriving below. Work continues until the agent finishes.",
            ),
            "waiting": (
                f"{name} needs your input",
                "Use Answer agent to answer a question or review a request.",
            ),
            "ready": (
                f"{name} is ready",
                "Send a message to continue this conversation.",
            ),
            "external": (
                "External agent connected",
                "Messages queue in Steed; this client must fetch them. Its working state is not reported.",
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
            hint += f" {report['queued']} follow-up message(s) queued."
        if pending and active and not (report and report["state"] == "waiting"):
            title = "Approval requested"
            hint = "Review the pending access or action request. The agent may continue other work."
            state = "approval"
        resume_until = self.service.resumable.get(self._task_id)
        if not report and not active and task_status in ACTIVE_STATUSES:
            title = "Agent disconnected"
            hint = "Your conversation and draft are kept. Use Connection setup to reconnect your client; messages are not resent automatically."
            if resume_until is not None:
                title = "Agent disconnected · waiting for it to return"
                hint = (
                    "Its access ended at the disconnect. The agent's next call reconnects and resumes this task until "
                    + time.strftime("%H:%M", time.localtime(resume_until))
                    + "; after that Steed cancels it. Your conversation is kept."
                )
        if (
            not report
            and self.providers
            and self.providers.status(self._task_id)
            and not active
        ):
            title = f"{name} chat stopped"
            hint = self.providers.status(self._task_id)
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
        try:
            task_id = self.providers.new_chat(
                model=self._new_model, agent=self._new_agent
            )
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

    def manage_acp_agents(self):
        from ui.agent_sessions import AcpAgentsDialog

        dialog = AcpAgentsDialog(self.browser_window.config, self.browser_window)
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
