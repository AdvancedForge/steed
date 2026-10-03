"""External-agent conversations beside the page, using existing task controls."""

import sqlite3

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor, QTextFormat
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
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
        body.setObjectName("agentBody")
        self._style_mode = None
        layout = QVBoxLayout(body)
        self.welcome = QLabel()
        self.welcome.setObjectName("agentWelcome")
        self.welcome.setWordWrap(True)
        self.welcome.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.welcome)
        self.start_controls = QWidget()
        start_buttons = QHBoxLayout(self.start_controls)
        start_buttons.setContentsMargins(0, 0, 0, 0)
        self.new_chat = QPushButton("New Codex chat")
        self.new_chat.setObjectName("agentPrimary")
        self.new_chat.clicked.connect(self.start_chat)
        self.new_chat.setVisible(providers is not None)
        start_buttons.addWidget(self.new_chat)
        self.setup = QPushButton(
            "Connect another agent…" if providers else "Connect an agent…"
        )
        self.setup.clicked.connect(lambda: self.setup_requested.emit())
        start_buttons.addWidget(self.setup)
        layout.addWidget(self.start_controls)
        self.tasks = QComboBox()
        self.tasks.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.tasks.setMinimumContentsLength(18)
        self.tasks.setAccessibleName("External agent task")
        self.tasks.currentIndexChanged.connect(self._select)
        layout.addWidget(self.tasks)
        self.activity_card = QFrame()
        self.activity_card.setObjectName("agentActivityCard")
        activity_layout = QVBoxLayout(self.activity_card)
        activity_layout.setContentsMargins(12, 10, 12, 10)
        self.activity_title = QLabel()
        self.activity_title.setObjectName("agentActivityTitle")
        self.activity_title.setWordWrap(True)
        self.activity_hint = QLabel()
        self.activity_hint.setWordWrap(True)
        self.activity_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.running_model = QLabel()
        self.running_model.setWordWrap(True)
        self.running_model.setTextFormat(Qt.TextFormat.PlainText)
        self.running_model.setAccessibleName("Model reported by the running agent")
        self.running_model.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setFixedHeight(4)
        activity_layout.addWidget(self.activity_title)
        activity_layout.addWidget(self.running_model)
        activity_layout.addWidget(self.activity_hint)
        activity_layout.addWidget(self.busy)
        layout.addWidget(self.activity_card)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.error = QLabel()
        self.error.setObjectName("agentError")
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        self.pending = QPushButton()
        self.pending.setObjectName("agentAttention")
        self.pending.clicked.connect(lambda: self.open_details("Permissions"))
        layout.addWidget(self.pending)
        self.agent_requests = QPushButton("Answer agent")
        self.agent_requests.setObjectName("agentAttention")
        self.agent_requests.clicked.connect(self.show_requests)
        layout.addWidget(self.agent_requests)
        self._request_dialogs = {}
        self.conversation = QWidget()
        chat_layout = QVBoxLayout(self.conversation)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        conversation_title = QLabel("Conversation")
        conversation_title.setObjectName("agentSectionTitle")
        chat_layout.addWidget(conversation_title)
        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("agentTranscript")
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Agent conversation")
        chat_layout.addWidget(self.transcript, 1)
        message_title = QLabel("Your message")
        message_title.setObjectName("agentSectionTitle")
        chat_layout.addWidget(message_title)
        self.composer = QPlainTextEdit()
        self.composer.setObjectName("agentComposer")
        self.composer.setPlaceholderText("Write a message… (Ctrl+Enter to send)")
        self.composer.setMaximumHeight(100)
        self.composer.setAccessibleName("Message to selected agent")
        chat_layout.addWidget(self.composer)
        self.send = QPushButton("Send")
        self.send.setObjectName("agentPrimary")
        self.send.clicked.connect(self.send_message)
        send_controls = QHBoxLayout()
        send_controls.addWidget(self.send)
        self.cancel = QPushButton("Cancel task")
        self.cancel.clicked.connect(self.cancel_task)
        send_controls.addWidget(self.cancel)
        chat_layout.addLayout(send_controls)
        self.delegate = QPushButton("Share browser tabs…")
        self.delegate.clicked.connect(self.delegate_tabs)
        chat_layout.addWidget(self.delegate)
        layout.addWidget(self.conversation, 1)
        self.more = QPushButton("More options ▸")
        self.more.setObjectName("agentMore")
        self.more.setCheckable(True)
        self.more.toggled.connect(self._show_options)
        layout.addWidget(self.more)
        self.tools_panel = QWidget()
        tools_layout = QVBoxLayout(self.tools_panel)
        self.tools_panel.setObjectName("agentTools")
        tools_layout.setContentsMargins(10, 10, 10, 10)
        tools_layout.addWidget(self.status)
        self.model = QComboBox()
        self.model.setAccessibleName("Model for new Codex chats")
        self.model.setToolTip(
            "Choose the model for a new chat. This does not change an existing conversation."
        )
        self.model.addItem("Codex default", None)
        self.model.addItem("GPT-5.4 (tested direct Steed tools)", "gpt-5.4")
        self.model.setVisible(providers is not None)
        self.model_caption = QLabel("Model for new chats")
        self.model_caption.setVisible(providers is not None)
        tools_layout.addWidget(self.model_caption)
        tools_layout.addWidget(self.model)
        self.connection_settings = QPushButton("Connection setup…")
        self.connection_settings.clicked.connect(lambda: self.setup_requested.emit())
        tools_layout.addWidget(self.connection_settings)
        self.library_button = QPushButton("File library…")
        self.library_button.clicked.connect(
            lambda: self.browser_window._open_file_library()
        )
        tools_layout.addWidget(self.library_button)
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
        tools_layout.addLayout(display)
        tools = QHBoxLayout()
        self.details = []
        for label, panel in (
            ("Permissions", "Permissions"),
            ("Task files", "Workspace"),
            ("Receipts", "Apps"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, panel=panel: self.open_details(panel)
            )
            tools.addWidget(button)
            self.details.append(button)
        tools_layout.addLayout(tools)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        tools_layout.addWidget(self.summary)
        administration = QHBoxLayout()
        self.clear = QPushButton("Clear chat")
        self.clear.clicked.connect(self.clear_messages)
        administration.addWidget(self.clear)
        tools_layout.addLayout(administration)
        privacy = QLabel(
            "Only sent messages are shared. Browser access needs your approval. "
            "Closing this panel keeps the task running."
        )
        privacy.setWordWrap(True)
        tools_layout.addWidget(privacy)
        self.tools_panel.hide()
        layout.addWidget(self.tools_panel)
        self._body_layout = layout
        self._spacer_index = layout.count()
        layout.addStretch(1)
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

    def _apply_style(self):
        config = getattr(self.browser_window, "config", None)
        dark = bool(getattr(config, "dark_mode", False))
        if dark == self._style_mode:
            return
        self._style_mode = dark
        bg, panel, ink, border, soft, blue = (
            ("#202630", "#293341", "#ecf1f7", "#48576c", "#233f62", "#9dc8ff")
            if dark
            else ("#f3f6fb", "#ffffff", "#24344a", "#c7d2e2", "#e9f2ff", "#245ba7")
        )
        self.setStyleSheet(f"""
            QWidget#agentBody {{ background: {bg}; color: {ink}; }}
            QLabel {{ color: {ink}; background: transparent; border: none; }}
            QLabel#agentWelcome, QFrame#agentActivityCard, QWidget#agentTools {{
                background: {panel}; border: 1px solid {border}; border-radius: 8px;
            }}
            QLabel#agentWelcome {{ padding: 12px; }}
            QLabel#agentSectionTitle {{ font-weight: 600; padding-top: 3px; }}
            QLabel#agentActivityTitle {{ font-weight: 600; font-size: 14px; color: {blue}; }}
            QPlainTextEdit, QComboBox {{ background: {panel}; color: {ink};
                border: 1px solid {border}; border-radius: 6px; padding: 6px; }}
            QPlainTextEdit#agentComposer:focus {{ border: 2px solid {blue}; }}
            QPushButton {{ padding: 6px; border: 1px solid {border};
                border-radius: 5px; background: {panel}; color: {ink}; }}
            QPushButton:hover {{ background: {soft}; }}
            QPushButton#agentPrimary {{ background: #315fc4; color: white;
                border-color: #315fc4; font-weight: 600; }}
            QPushButton:disabled, QPushButton#agentPrimary:disabled {{ color: {border}; background: {bg}; border-color: {border}; }}
            QPushButton#agentAttention {{ border: 2px solid #b87916; }}
            QPushButton#agentMore {{ text-align: left; background: {bg}; }}
            QLabel#agentError {{ border: 1px solid #bd655f; padding: 8px; }}
            QProgressBar {{ border: none; background: {soft}; }}
            QProgressBar::chunk {{ background: #4878d9; }}
        """)
        self._highlight_speakers()

    def _highlight_speakers(self):
        selections, line = [], 0
        for message in sorted(
            self._messages.values(), key=lambda m: m["created_sequence"]
        ):
            if message["state"] == "cleared":
                continue
            mark = QTextEdit.ExtraSelection()
            mark.cursor = QTextCursor(
                self.transcript.document().findBlockByNumber(line)
            )
            mark.format.setFontWeight(600)
            mark.format.setBackground(
                QColor(
                    ("#233f62" if message["role"] == "user" else "#29443a")
                    if self._style_mode
                    else "#e9f2ff"
                    if message["role"] == "user"
                    else "#edf3ed"
                )
            )
            mark.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selections.append(mark)
            line += message["text"].count("\n") + 3
        self.transcript.setExtraSelections(selections)

    def _update_activity(self, usable, active):
        if not usable:
            self.busy.hide()
            self.running_model.clear()
            return
        model = (
            self.providers.model_info(self._task_id)
            if self.providers is not None and hasattr(self.providers, "model_info")
            else None
        )
        if model:
            text = "Model: " + model["model"]
            if model.get("provider"):
                text += " · " + model["provider"]
            text += " (reported by Codex)" if model["live"] else " (last reported)"
            if model["source"] == "rerouted":
                text += "\nCodex rerouted this turn from " + model["from_model"] + "."
        else:
            text = "Model: not reported by the agent"
        self.running_model.setText(text)
        report = (
            self.providers.activity(self._task_id)
            if self.providers is not None and hasattr(self.providers, "activity")
            else None
        )
        state = report["state"] if report else "external" if active else "history"
        title, hint = {
            "connecting": (
                "Connecting to Codex…",
                "Starting the agent and its Steed connection.",
            ),
            "sending": ("Sending…", "Waiting for Codex to accept your message."),
            "thinking": (
                "Codex is thinking…",
                "Your message was accepted; the agent is preparing its next step.",
            ),
            "working": (
                "Codex is working…",
                "The agent is using tools. You can browse or queue a follow-up.",
            ),
            "replying": (
                "Codex is replying…",
                "The reply is arriving below. Work continues until the agent finishes.",
            ),
            "waiting": (
                "Codex needs your input",
                "Use Answer agent above to answer a question or review a request.",
            ),
            "ready": (
                "Codex is ready",
                "Send a message to continue this conversation.",
            ),
            "external": (
                "External agent connected",
                "Messages are delivered through Steed. This client's working state is not available.",
            ),
            "history": (
                "Conversation ended",
                "Saved messages are available below. Start a new chat to continue.",
            ),
        }.get(
            state,
            ("Agent status unavailable", "Open More options for connection details."),
        )
        if report and report["queued"]:
            hint += f" {report['queued']} follow-up message(s) queued."
        if (
            not report
            and self.providers
            and self.providers.status(self._task_id)
            and not active
        ):
            title = "Codex chat stopped"
            hint = self.providers.status(self._task_id)
        self.activity_title.setText(title)
        self.activity_hint.setText(hint)
        self.busy.setVisible(
            state in ("connecting", "sending", "thinking", "working", "replying")
        )

    def _show_options(self, expanded):
        self.tools_panel.setVisible(expanded and not self._private)
        self.more.setText("More options ▾" if expanded else "More options ▸")

    def _update_layout(self, usable, *, pending=0):
        available = (
            not self._private and not self.service._closed and not self.service.failure
        )
        self.welcome.setVisible(not usable)
        self.welcome.setText(
            "Agent conversations are unavailable in private browsing. Switch to a regular profile."
            if self._private
            else "Talk to an agent\n\nStart a Codex chat, then send your first message.\n"
            "To use another agent, open connection setup, add Steed to that agent, "
            "and ask it to start a task. Choose its conversation here.\n\n"
            "Browser access is shared only when you approve it."
            if self.providers is not None
            else "Connect your agent\n\n1. Open connection setup.\n"
            "2. Add Steed to your agent and ask it to start a task.\n"
            "3. Choose its conversation and send a message."
        )
        self.start_controls.setVisible(available)
        self.setup.setVisible(not usable)
        self.connection_settings.setVisible(usable)
        self._body_layout.setStretch(self._spacer_index, 0 if usable else 1)
        self.tasks.setVisible(available and self.tasks.count() > 1)
        self.status.setVisible(usable)
        self.activity_card.setVisible(usable)
        self.conversation.setVisible(usable)
        self.cancel.setVisible(usable and self.cancel.isEnabled())
        self.delegate.setVisible(usable and self.delegate.isEnabled())
        self.pending.setText(f"Review {pending} pending approval(s)…")
        self.pending.setVisible(usable and pending > 0)
        self.pending.setEnabled(usable and pending > 0)
        self.agent_requests.setVisible(usable and self.agent_requests.isEnabled())
        self.more.setVisible(available)
        self.error.setVisible(bool(self.error.text()))
        if not available:
            self.more.setChecked(False)
            self.tools_panel.hide()

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
            self.error.show()
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
            self.error.show()

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
            choices = [(None, "Choose a conversation")]
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
            self._apply_style()
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
        self.transcript.setExtraSelections([])
        self.status.setText("Conversation is unavailable.")
        self.summary.clear()
        self.error.setText(
            "Conversation could not be loaded. Your browsing is still available."
        )
        self._update_layout(False)

    def _select(self):
        if self._task_id and not self._private:
            self._drafts[self._task_id] = self.composer.toPlainText()
        self._task_id = self.tasks.currentData()
        self._messages = {}
        self._cursor = 0
        self.composer.setPlainText(self._drafts.get(self._task_id, ""))
        self.transcript.clear()
        self.transcript.setExtraSelections([])
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
        pending_count = (
            len(self.providers.chats.get(task_id, {}).get("requests", {}))
            if self.providers and usable
            else 0
        )
        self.agent_requests.setText(f"Answer agent ({pending_count})…")
        self.agent_requests.setEnabled(usable and pending_count > 0)
        active = False
        counts = {"pending": 0}
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
                self._highlight_speakers()
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
                else "Choose a conversation, or start a new chat."
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
        self._update_layout(usable, pending=counts["pending"])
        self._update_activity(usable, active)

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
            self.error.show()
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
            self.error.show()
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
            self.error.show()

    def open_details(self, panel):
        if self._task_id is None or self._private:
            return
        try:
            dialog = TaskDialog(self.service, self)
        except (ValueError, RuntimeError, sqlite3.Error, OSError):
            self.error.setText("Task details could not be opened.")
            self.error.show()
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
