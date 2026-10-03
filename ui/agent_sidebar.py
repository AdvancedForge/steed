"""External-agent conversations beside the page, using existing task controls."""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.task_store import ACTIVE_STATUSES
from ui.task_dialog import TaskDialog


class AgentSidebar(QDockWidget):
    def __init__(self, service, parent=None):
        super().__init__("External Agents", parent)
        self.service = service
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
            "Connect an external agent to Steed MCP and have it start a task. Select that task here. The agent must read and reply through the conversation tools."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
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
        tools = QHBoxLayout()
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
            "Only sent text is shared. Messages grant no page, profile, file or app access. Hiding this panel keeps the task running."
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self.setWidget(body)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.composer)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.send_message)
        self.service.changed.connect(self.refresh)
        self.refresh()

    def set_private(self, private):
        self._private = private
        if private:
            self._drafts.clear()
            self.composer.clear()
        self.refresh()

    def refresh(self):
        available = (
            not self._private and not self.service._closed and not self.service.failure
        )
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
                        f"{'Connected' if connected else task.status} · {' '.join(task.objective.split())[:80]}",
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
        for button in (self.send, self.cancel, self.clear, *self.details):
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
        self.send.setEnabled(active)
        self.composer.setEnabled(active)
        self.cancel.setEnabled(active)
        self.clear.setEnabled(usable)
        for button in self.details:
            button.setEnabled(usable)

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
        self.service.changed.disconnect(self.refresh)
