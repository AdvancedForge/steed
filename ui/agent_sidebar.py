"""External-agent conversations beside the page, using existing task controls."""

import sqlite3

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from services.task_store import ACTIVE_STATUSES
from ui.agent_request import AgentRequestDialog
from ui.task_dialog import TaskDialog


class AgentSidebar(QDockWidget):
    setup_requested = pyqtSignal()

    def __init__(self, service, parent=None, *, providers=None):
        super().__init__("External Agents", parent)
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
        self._private = False
        self._choices = []
        body = QWidget()
        layout = QVBoxLayout(body)
        notice = QLabel(
            "Start a Codex chat here, or connect another external agent using Connection Setup. Codex uses its own sign-in; New Codex Chat enables local agent access for this browser run."
            if providers is not None
            else "Connect an external agent to Steed MCP and have it start a task. Select that task here. The agent must read and reply through the conversation tools."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.setup = QPushButton("Connection Setup…")
        self.setup.clicked.connect(lambda: self.setup_requested.emit())
        layout.addWidget(self.setup)
        self.library_button = QPushButton("File Library…")
        self.library_button.clicked.connect(
            lambda: self.browser_window._open_file_library()
        )
        layout.addWidget(self.library_button)
        display = QHBoxLayout()
        for label, method in (
            ("Open as tab", "_open_agent_tab"),
            ("Show sidebar", "_toggle_agent_sidebar"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, method=method: self.change_display(method)
            )
            display.addWidget(button)
        layout.addLayout(display)
        self.model = QComboBox()
        self.model.setAccessibleName("Codex model")
        self.model.addItem("Codex default", None)
        self.model.addItem("GPT-5.4 (tested direct Steed tools)", "gpt-5.4")
        self.model.setVisible(providers is not None)
        layout.addWidget(self.model)
        self.new_chat = QPushButton("New Codex Chat")
        self.new_chat.clicked.connect(self.start_chat)
        self.new_chat.setVisible(providers is not None)
        layout.addWidget(self.new_chat)
        self.tasks = QComboBox()
        self.tasks.setAccessibleName("External agent task")
        self.tasks.currentIndexChanged.connect(self._select)
        layout.addWidget(self.tasks)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Agent conversation")
        layout.addWidget(self.transcript, 1)
        self.composer = QPlainTextEdit()
        self.composer.setPlaceholderText("Message this external agent… (Ctrl+Enter)")
        self.composer.setMaximumHeight(100)
        self.composer.setAccessibleName("Message to selected agent")
        layout.addWidget(self.composer)
        buttons = QHBoxLayout()
        self.send = QPushButton("Send")
        self.send.clicked.connect(self.send_message)
        buttons.addWidget(self.send)
        self.cancel = QPushButton("Cancel Task")
        self.cancel.clicked.connect(self.cancel_task)
        buttons.addWidget(self.cancel)
        self.clear = QPushButton("Clear Chat")
        self.clear.clicked.connect(self.clear_messages)
        buttons.addWidget(self.clear)
        layout.addLayout(buttons)
        self.delegate = QPushButton("Delegate current tab…")
        self.delegate.clicked.connect(self.delegate_tabs)
        layout.addWidget(self.delegate)
        tools = QHBoxLayout()
        self.agent_requests = QPushButton("Agent requests")
        self.agent_requests.clicked.connect(self.show_requests)
        self.agent_requests.setVisible(providers is not None)
        layout.addWidget(self.agent_requests)
        self._request_dialogs = {}
        self.details = []
        for label, panel in (
            ("Permissions", "Permissions"),
            ("Files", "Workspace"),
            ("Receipts", "Apps"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, panel=panel: self.open_details(panel)
            )
            tools.addWidget(button)
            self.details.append(button)
        layout.addLayout(tools)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        privacy = QLabel(
            "Steed shares sent text. Codex retains its own tools and permissions. Browser/profile access requires Steed approval. Hiding this panel keeps the task running."
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.setWidget(scroll)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.composer)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.send_message)
        self.service.changed.connect(self.refresh)
        if self.providers is not None:
            self.providers.changed.connect(self.refresh)
            self.providers.requested.connect(self._request_added)
        self.refresh()

    def _request_added(self, task_id, request_id, payload):
        if task_id == self._task_id and not self._private and self.isVisible():
            self._show_request(task_id, request_id, payload)

    def change_display(self, method):
        window = self.browser_window
        if window is not None:
            if method == "_toggle_agent_sidebar":
                window._toggle_agent_sidebar(True)
            else:
                window._open_agent_tab()

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
            self.error.setText(
                "This agent request could not be displayed. Cancel the task."
            )
            return
        dialog.setWindowTitle(f"Codex request · {task_id[:8]}")
        self._request_dialogs[(task_id, request_id)] = dialog
        dialog.finished.connect(
            lambda: self._request_dialogs.pop((task_id, request_id), None)
        )
        dialog.show()

    def start_chat(self):
        if self._private or self.providers is None:
            return
        try:
            task_id = self.providers.new_chat(model=self.model.currentData())
            self.refresh()
            self.tasks.setCurrentIndex(self.tasks.findData(task_id))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.error.setText(str(error))

    def set_private(self, private):
        self._private = private
        if private:
            self._drafts.clear()
            self.composer.clear()
        self.refresh()

    def refresh(self):
        self.library_button.setEnabled(
            not self._private and hasattr(self.browser_window, "_open_file_library")
        )
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
        pending_count = (
            len(self.providers.chats.get(self._task_id, {}).get("requests", {}))
            if self.providers
            else 0
        )
        self.agent_requests.setText(f"Agent requests ({pending_count})")
        self.agent_requests.setEnabled(available and pending_count > 0)
        self.new_chat.setEnabled(available)
        self.model.setEnabled(available)
        try:
            records = self.service.store.list_tasks() if available else []
            # Preserve the selected history even as new tasks arrive.
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
            choices = [(None, "Choose an external agent task")]
            for task in reversed(records):
                connected = (
                    task.task_id in self.service.external_tasks
                    and task.status in ACTIVE_STATUSES
                )
                choices.append(
                    (
                        task.task_id,
                        f"{'Connected' if connected else task.status} · {' '.join(task.objective.split())[:80]} · {task.task_id[:8]}",
                    )
                )
            if choices != self._choices:
                self.tasks.blockSignals(True)
                self.tasks.clear()
                for task_id, label in choices:
                    self.tasks.addItem(label, task_id)
                index = self.tasks.findData(self._task_id)
                self.tasks.setCurrentIndex(max(index, 0))
                self.tasks.blockSignals(False)
                self._choices = choices
            if self.tasks.currentData() != self._task_id:
                self._select()
            self._refresh_conversation()
        except (sqlite3.Error, OSError, RuntimeError, ValueError):
            self.tasks.blockSignals(False)
            self._unavailable()

    def _unavailable(self):
        for button in (
            self.send,
            self.delegate,
            self.cancel,
            self.clear,
            self.new_chat,
            self.agent_requests,
            *self.details,
        ):
            button.setEnabled(False)
        self.composer.setEnabled(False)
        self.transcript.clear()
        self.status.setText("Conversation is unavailable.")
        self.summary.clear()
        self.error.setText("Conversation is unavailable.")

    def _select(self):
        if self._task_id and not self._private:
            self._drafts[self._task_id] = self.composer.toPlainText()
        self._task_id = self.tasks.currentData()
        self._messages = {}
        self._cursor = 0
        self.composer.setPlainText(self._drafts.get(self._task_id, ""))
        self.transcript.clear()
        self.error.clear()
        try:
            self._refresh_conversation()
        except (sqlite3.Error, OSError, RuntimeError, ValueError):
            self._unavailable()

    def _refresh_conversation(self):
        task_id = self._task_id
        usable = (
            task_id is not None
            and not self._private
            and not self.service._closed
            and not self.service.failure
        )
        active = False
        if usable:
            task = self.service.store.get(task_id)
            active = (
                task.status in ACTIVE_STATUSES
                and task_id in self.service.external_tasks
                and (self.providers is None or self.providers.can_send(task_id))
            )
            dirty = False
            while True:
                page = self.service.messages.read(task_id, self._cursor)
                for message in page["messages"]:
                    self._messages[message["message_id"]] = message
                dirty |= bool(page["messages"])
                self._cursor = page["cursor"]
                if not page["has_more"]:
                    break
            if dirty:
                scroll = self.transcript.verticalScrollBar()
                at_end, position = scroll.value() >= scroll.maximum(), scroll.value()
                text = "\n\n".join(
                    f"{'You' if m['role'] == 'user' else 'Agent'}{(' · ' + m['state']) if m['state'] != 'complete' else ''}\n{m['text']}"
                    for m in sorted(
                        self._messages.values(), key=lambda m: m["created_sequence"]
                    )
                    if m["state"] != "cleared"
                )
                self.transcript.setPlainText(text)
                scroll.setValue(scroll.maximum() if at_end else position)
            grants = self.service.store.grants(task_id)
            permission = grants[-1] if grants else None
            profile = (
                permission.scope.get("profile", "isolated task profile")
                if permission
                else "No profile access"
            )
            self.status.setText(
                f"{'Connected' if active else 'Read-only history · ' + task.status}\n{profile} · Permission: {permission.status if permission else 'not requested'}"
            )
            if self.providers is not None and self.providers.status(task_id):
                self.status.setText(
                    self.providers.status(task_id) + "\n" + self.status.text()
                )
            read = self.service.messages.read_sequence(task_id)
            waiting = sum(
                m["role"] == "user" and m["state"] != "cleared" and m["sequence"] > read
                for m in self._messages.values()
            )
            if waiting:
                self.status.setText(
                    self.status.text()
                    + f"\n{waiting} message(s) waiting for the agent to read"
                )
            elif any(
                m["role"] == "user" and m["state"] != "cleared"
                for m in self._messages.values()
            ):
                self.status.setText(
                    self.status.text() + "\nAgent fetched your sent messages"
                )
            counts = self.service.messages.summary(task_id)
            self.summary.setText(
                f"{counts['pending']} pending approval(s) · {counts['files']} file(s) · {counts['artifacts']} draft(s) · {counts['receipts']} app receipt(s)"
            )
        else:
            self.summary.clear()
            self.status.setText(
                "Agent conversations are disabled in incognito."
                if self._private
                else "Select a task. Enable Local Agent Access in File to connect an agent."
            )
        self.delegate.setEnabled(
            active and hasattr(self.browser_window, "delegate_tabs")
        )
        self.send.setEnabled(active)
        self.composer.setEnabled(active)
        self.cancel.setEnabled(usable and task.status in ACTIVE_STATUSES)
        self.clear.setEnabled(usable)
        for button in self.details:
            button.setEnabled(usable)

    def delegate_tabs(self):
        from PyQt6.QtWidgets import QInputDialog

        if not self.delegate.isEnabled():
            return
        choice, accepted = QInputDialog.getItem(
            self,
            "Share live tabs with this agent",
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
        except (ValueError, RuntimeError, sqlite3.Error, OSError) as error:
            self.error.setText(str(error))
        else:
            self.error.clear()

    def send_message(self):
        if not self.send.isEnabled():
            return
        try:
            self.service.send_message(self._task_id, self.composer.toPlainText())
        except (ValueError, RuntimeError, sqlite3.Error, OSError):
            self.error.setText(
                "Message was not sent. Use nonempty text up to 8 KiB; check the agent connection and task limits."
            )
            return
        self.composer.clear()
        self._drafts.pop(self._task_id, None)
        self.error.clear()

    def cancel_task(self):
        self._perform(self.service.cancel_task)

    def clear_messages(self):
        self._perform(self.service.clear_messages)

    def _perform(self, operation):
        if self._task_id is None or self._private:
            return
        try:
            operation(self._task_id)
        except (ValueError, RuntimeError, sqlite3.Error, OSError):
            self.error.setText("Task operation could not be completed.")

    def open_details(self, panel):
        if self._task_id is None or self._private:
            return
        try:
            dialog = TaskDialog(self.service, self)
        except (ValueError, RuntimeError, sqlite3.Error, OSError):
            self.error.setText("Task details could not be opened.")
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
