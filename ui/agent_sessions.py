"""Continuing a saved Claude Code session, and setting up ACP agents.

Both are ordinary dialogs; Steed shows them as its own pages (HtmlDialog).
"""

import shlex
import time

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.acp_provider import clean_agent
from services.claude_sessions import list_sessions


def _when(stamp):
    seconds = max(0, time.time() - stamp)
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return time.strftime("%b %d", time.localtime(stamp))


class ClaudeSessionsDialog(QDialog):
    """Pick a saved Claude Code session to continue in the sidebar."""

    started = pyqtSignal(str)  # task id of the new chat

    def __init__(self, providers, parent=None):
        super().__init__(parent)
        self.providers = providers
        self.setWindowTitle("Continue a Claude Code Session")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Pick up a Claude Code conversation here, with its history. It "
                "continues as a copy in its own folder, so a session still open "
                "in a terminal is left as it is; the copy is saved in Claude "
                "Code's history too. Claude Code's tools ask you here, and "
                "browser access still needs Steed's approval."
            )
        )
        self.sessions = list_sessions()
        self.table = QTableWidget(len(self.sessions), 3)
        self.table.setHorizontalHeaderLabels(["Session", "Folder", "Last used"])
        for row, session in enumerate(self.sessions):
            for column, text in enumerate(
                (session["title"], session["cwd"], _when(session["updated"]))
            ):
                self.table.setItem(row, column, QTableWidgetItem(text))
        if self.sessions:
            self.table.selectRow(0)
        self.table.itemDoubleClicked.connect(lambda _: self.continue_chosen())
        layout.addWidget(self.table)
        self.status = QLabel(
            "" if self.sessions else "No saved Claude Code sessions were found."
        )
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.go = QPushButton("Continue here")
        self.go.setEnabled(bool(self.sessions))
        self.go.clicked.connect(self.continue_chosen)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.go)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def continue_chosen(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.sessions):
            return
        try:
            task_id = self.providers.new_chat(
                agent="claude", resume=self.sessions[row]["id"]
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.started.emit(task_id)
        self.accept()


class AcpAgentsDialog(QDialog):
    """The ACP agents a new chat can use (config "acp_agents")."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("ACP Agents")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Agents that speak the Agent Client Protocol can chat in the "
                "sidebar like Codex and Claude Code. Give each a name and the "
                "command that starts it in ACP mode, for example "
                "gemini --experimental-acp. Steed starts it in a private folder, "
                "gives it Steed's browser tools (with your approvals) and no file "
                "or terminal access of Steed's own. Only add commands you trust: "
                "they run as you."
            )
        )
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Command"])
        layout.addWidget(self.table)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name, e.g. Gemini CLI")
        self.command = QLineEdit()
        self.command.setPlaceholderText("Command, e.g. gemini --experimental-acp")
        layout.addWidget(self.name)
        layout.addWidget(self.command)
        self.status = QLabel("")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        add = QPushButton("Add agent")
        add.clicked.connect(self.add)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (add, remove, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()

    def agents(self):
        found = []
        for entry in self.config.get("acp_agents", []) or []:
            try:
                found.append(clean_agent(entry))
            except ValueError:
                continue
        return found

    def refresh(self):
        agents = self.agents()
        self.table.setRowCount(len(agents))
        for row, agent in enumerate(agents):
            self.table.setItem(row, 0, QTableWidgetItem(agent["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(shlex.join(agent["command"])))

    def add(self):
        try:
            command = shlex.split(self.command.text())
        except ValueError:
            self.status.setText("Check the command's quotes.")
            return
        try:
            agent = clean_agent({"name": self.name.text(), "command": command})
        except ValueError as error:
            self.status.setText(str(error))
            return
        agents = [a for a in self.agents() if a["name"] != agent["name"]]
        if len(agents) >= 12:
            self.status.setText("Keep at most 12 ACP agents.")
            return
        self.config.set("acp_agents", agents + [agent])
        self.name.clear()
        self.command.clear()
        self.status.setText(
            f"Added {agent['name']}. Choose it under Agent for new chats."
        )
        self.refresh()

    def remove(self):
        row = self.table.currentRow()
        agents = self.agents()
        if 0 <= row < len(agents):
            gone = agents.pop(row)
            self.config.set("acp_agents", agents)
            self.status.setText(f"Removed {gone['name']}.")
            self.refresh()
