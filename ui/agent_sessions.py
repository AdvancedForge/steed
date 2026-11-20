"""Continuing a saved Claude Code session, and setting up models and ACP agents.

Both are ordinary dialogs; Steed shows them as its own pages (HtmlDialog).
"""

import json
import shlex
import time

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
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
from services.model_assistant import clean_model, forget_key, save_key


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
                "Pick up a Claude Code conversation here, with its history. It continues as a copy, so a session still open in a terminal is left alone. Browser access still needs your approval."
            )
        )
        self.sessions = list_sessions()
        self.table = QTableWidget(len(self.sessions), 4)
        self.table.setHorizontalHeaderLabels(
            ["Session", "You said last", "Folder", "Last used"]
        )
        for row, session in enumerate(self.sessions):
            for column, text in enumerate(
                (
                    f"{session['title']} · {session['id'][:8]}",
                    session.get("last", ""),
                    session["cwd"],
                    _when(session["updated"]),
                )
            ):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Nothing is chosen for you: the newest row is often the session
        # still open in a terminal.
        self.table.setCurrentCell(-1, -1)
        self.table.itemSelectionChanged.connect(
            lambda: self.go.setEnabled(self.table.currentRow() >= 0)
        )
        self.table.itemDoubleClicked.connect(lambda _: self.continue_chosen())
        layout.addWidget(self.table)
        self.status = QLabel(
            "" if self.sessions else "No saved Claude Code sessions were found."
        )
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.go = QPushButton("Continue here")
        self.go.setEnabled(False)
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


# Where people usually run a model; "Other" is any OpenAI-compatible address.
MODEL_SERVICES = (
    ("Ollama on this computer", "http://localhost:11434/v1", "none"),
    ("LM Studio on this computer", "http://localhost:1234/v1", "none"),
    ("llama.cpp server on this computer", "http://localhost:8080/v1", "none"),
    ("OpenAI", "https://api.openai.com/v1", "keyring"),
    ("Anthropic", "https://api.anthropic.com/v1", "keyring"),
    (
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "keyring",
    ),
    ("OpenRouter", "https://openrouter.ai/api/v1", "keyring"),
    ("Other (any OpenAI-compatible API)", "", "keyring"),
)


class ModelsDialog(QDialog):
    """Models Steed runs as chat agents itself (config "models"). start picks
    the first service shown: "local" (or the address of one found running)
    or "key" for a hosted service."""

    def __init__(self, config, parent=None, *, start=None, address=None):
        super().__init__(parent)
        self.config = config
        self.network = None
        self.setWindowTitle("Models")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Chat with any model, with nothing else to install: a local model "
            "(Ollama, LM Studio, llama.cpp) or a hosted one with your own API "
            "key. Steed gives it the same browser tools and asks you before it "
            "does anything, just like any other agent. Your API key stays in "
            "the desktop keyring."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Model", "Address"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        form = QFormLayout()
        self.service = QComboBox()
        for label, address, key in MODEL_SERVICES:
            self.service.addItem(label, (address, key))
        self.service.currentIndexChanged.connect(self._service_changed)
        form.addRow("Service", self.service)
        self.address = QLineEdit()
        self.address.setPlaceholderText("https://…/v1")
        form.addRow("API address", self.address)
        model_row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText("e.g. qwen3:8b or gpt-5-mini")
        model_row.addWidget(self.model, 1)
        find = QPushButton("Find models")
        find.clicked.connect(self.find_models)
        model_row.addWidget(find)
        form.addRow("Model", model_row)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Paste the key; leave empty for local models")
        form.addRow("API key", self.key)
        self.tools = QComboBox()
        self.tools.addItem("Browsing tools (best for smaller models)", "browsing")
        self.tools.addItem("All of Steed's tools", "all")
        form.addRow("Tools", self.tools)
        self.vision = QCheckBox("It can look at screenshots")
        form.addRow("", self.vision)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name shown in chats, e.g. Local Qwen")
        form.addRow("Name", self.name)
        layout.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        add = QPushButton("Add model")
        add.setProperty("primary", True)
        add.clicked.connect(self.add)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (add, remove, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._service_changed()
        self.refresh()
        if start == "key":
            self.service.setCurrentIndex(
                next(
                    i
                    for i, (_, _, key) in enumerate(MODEL_SERVICES)
                    if key == "keyring"
                )
            )
        elif address:
            index = next(
                (i for i, (_, url, _) in enumerate(MODEL_SERVICES) if url == address),
                None,
            )
            if index is not None:
                self.service.setCurrentIndex(index)
                self.find_models()

    def _service_changed(self, *_):
        address, _key = self.service.currentData()
        self.address.setText(address)
        self.address.setReadOnly(bool(address))

    def models(self):
        found = []
        for entry in self.config.get("models", []) or []:
            try:
                found.append(clean_model(entry))
            except ValueError:
                continue
        return found

    def refresh(self):
        models = self.models()
        self.table.setRowCount(len(models))
        for row, entry in enumerate(models):
            for column, value in enumerate(
                (entry["name"], entry["model"], entry["base_url"])
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def find_models(self):
        """Ask the service which models it offers (GET /models)."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtNetwork import (
            QNetworkAccessManager,
            QNetworkReply,
            QNetworkRequest,
        )

        address = self.address.text().strip().rstrip("/")
        if not address.startswith(("http://", "https://")):
            self.status.setText("Enter the API address first.")
            return
        if self.network is None:
            self.network = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(address + "/models"))
        request.setTransferTimeout(8000)
        if self.key.text().strip():
            request.setRawHeader(
                b"Authorization", f"Bearer {self.key.text().strip()}".encode()
            )
        self.status.setText("Asking for its models…")
        reply = self.network.get(request)

        def done():
            reply.deleteLater()
            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    raise ValueError
                data = json.loads(bytes(reply.readAll()[: 2 * 1024 * 1024]))
                names = sorted(
                    {
                        str(item["id"])
                        for item in data.get("data", [])
                        if isinstance(item, dict) and item.get("id")
                    }
                )
            except (ValueError, TypeError, AttributeError, KeyError):
                self.status.setText(
                    "No models found there. Check the address (and the key), or "
                    "type the model's name."
                )
                return
            current = self.model.currentText()
            self.model.clear()
            self.model.addItems(names[:500])
            if current:
                self.model.setEditText(current)
            self.status.setText(f"Found {len(names)} models. Choose one.")

        reply.finished.connect(done)

    def add(self):
        key_text = self.key.text().strip()
        model = self.model.currentText().strip()
        name = self.name.text().strip() or model.split("/")[-1][:40]
        key = "keyring" if key_text else "none"
        try:
            entry = clean_model(
                {
                    "name": name,
                    "base_url": self.address.text().strip(),
                    "model": model,
                    "key": key,
                    "tools": self.tools.currentData(),
                    "vision": self.vision.isChecked(),
                }
            )
            if key_text:
                save_key(entry["name"], key_text)
        except ValueError as error:
            self.status.setText(str(error))
            return
        models = [m for m in self.models() if m["name"] != entry["name"]]
        if len(models) >= 12:
            self.status.setText("Keep at most 12 models.")
            return
        self.config.set("models", models + [entry])
        self.key.clear()
        self.name.clear()
        self.status.setText(
            f"Added {entry['name']}. Choose it under Agent for new chats."
        )
        self.refresh()

    def remove(self):
        row = self.table.currentRow()
        models = self.models()
        if 0 <= row < len(models):
            gone = models.pop(row)
            forget_key(gone["name"])
            self.config.set("models", models)
            self.status.setText(f"Removed {gone['name']}.")
            self.refresh()
