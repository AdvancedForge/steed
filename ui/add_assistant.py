"""Add an assistant: one place for every way an assistant can help in Steed.

Each card says what it is and what it needs; Steed fills in what it finds
already on this computer (Ollama or LM Studio running, Codex or Claude Code
installed), so there is often nothing to type.
"""

import json
import shutil

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout

from ui.html_dialog import HtmlSettingsDialog

# Model servers people run on their own computer, checked on opening.
LOCAL_SERVERS = (
    ("Ollama", "http://localhost:11434/v1"),
    ("LM Studio", "http://localhost:1234/v1"),
)
CODING_AGENTS = (("Codex", "codex"), ("Claude Code", "claude"))

CARDS = (
    (
        "local",
        "On this computer",
        "A model running on this computer (Ollama, LM Studio, llama.cpp). "
        "Private, free and offline.",
    ),
    (
        "key",
        "With your API key",
        "OpenAI, Anthropic, Google Gemini, OpenRouter or any OpenAI-compatible "
        "service. The key stays in your desktop keyring.",
    ),
    (
        "coding",
        "Coding agents",
        "Codex, Claude Code, or any agent that speaks ACP, started by Steed in "
        "the sidebar.",
    ),
    (
        "website",
        "Any chatbot website",
        "ChatGPT, Claude, Gemini or another chat site in the sidebar, signed in "
        "as you. Hand it your task and paste its requests back.",
    ),
    (
        "cloud",
        "Cloud agents and chatbots with connectors",
        "Let claude.ai, ChatGPT or a cloud agent use Steed's tools through a "
        "private, expiring link you pair yourself.",
    ),
)


class AddAssistantDialog(HtmlSettingsDialog):
    """Emits chosen(kind, detail) for the card picked; the sidebar opens it."""

    chosen = pyqtSignal(str, object)

    def __init__(self, parent=None, *, probe=True):
        super().__init__(parent)
        self.setWindowTitle("Add an assistant")
        self.resize(560, 560)
        self.found = {}  # server name -> (address, model count)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Steed works with any assistant you like. Each one gets the same "
            "tools and asks you the same way before it does anything."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.cards = {}
        for kind, title, text in CARDS:
            button = QPushButton(title)
            button.setProperty("role", "card")
            button.setProperty("detail", text)
            button.setToolTip(text)
            button.clicked.connect(lambda _=False, kind=kind: self._choose(kind))
            layout.addWidget(button)
            self.cards[kind] = button
        installed = [name for name, program in CODING_AGENTS if shutil.which(program)]
        if installed:
            self._detail(
                "coding",
                self.detail("coding")
                + " Installed here: "
                + " and ".join(installed)
                + " (already in the list).",
            )
        layout.addStretch()
        self.network = None
        if probe:
            self._probe()

    def _probe(self):
        """Look for a model server already running on this computer."""
        self.network = QNetworkAccessManager(self)
        for name, address in LOCAL_SERVERS:
            request = QNetworkRequest(QUrl(address + "/models"))
            request.setTransferTimeout(1500)
            reply = self.network.get(request)
            reply.finished.connect(
                lambda reply=reply, name=name, address=address: self._probed(
                    reply, name, address
                )
            )

    def _probed(self, reply, name, address):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            return
        try:
            data = json.loads(bytes(reply.readAll()[: 1024 * 1024]))
            count = len([m for m in data.get("data", []) if isinstance(m, dict)])
        except (ValueError, AttributeError, TypeError):
            return
        self.found[name] = (address, count)
        found = ", ".join(
            f"{server} ({models} model{'s' if models != 1 else ''})"
            for server, (_, models) in self.found.items()
        )
        self._detail("local", f"Found running here: {found}. Choose to add one.")

    def detail(self, kind):
        return self.cards[kind].property("detail")

    def _detail(self, kind, text):
        self.cards[kind].setProperty("detail", text)
        self.cards[kind].setToolTip(text)
        host = self.presentation_host()
        if host is not None:
            host.publish()

    def _choose(self, kind):
        detail = None
        if kind == "local" and self.found:
            detail = next(iter(self.found.values()))[0]
        self.chosen.emit(kind, detail)
        self.accept()
