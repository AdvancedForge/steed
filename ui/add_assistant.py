"""Add an assistant: one place for every way an assistant can help in Steed.

Each card says what it is and what it needs; Steed fills in what it finds
already on this computer (Ollama or LM Studio running, Codex or Claude Code
installed), so there is often nothing to type.
"""

import json
import shutil

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

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
        "Codex, Claude Code or another coding assistant on this computer, "
        "started by Steed in the sidebar.",
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


# Help me choose: what people already have, in their words, and what suits it.
SITUATIONS = (
    (
        "A ChatGPT Plus or Pro subscription",
        "coding",
        "Use Codex. It signs in with your ChatGPT account, so it costs nothing "
        "extra, and it can use your tabs when you allow it. Install it once "
        "(npm install -g @openai/codex in a terminal), then sign in.",
    ),
    (
        "A Claude Pro or Max subscription",
        "coding",
        "Use Claude Code. It signs in with your Claude account, so it costs "
        "nothing extra, and it can use your tabs when you allow it. Install it "
        "once (npm install -g @anthropic-ai/claude-code), then sign in.",
    ),
    (
        "A free account on ChatGPT, Claude or Gemini",
        "website",
        "Use the chatbot website in the sidebar, signed in as you. It's the "
        "simplest start: you hand it your task and paste its requests back.",
    ),
    (
        "An API key (OpenAI, Anthropic, Google, OpenRouter…)",
        "key",
        "Use your API key. You pay the provider per use; the key stays in your "
        "desktop keyring.",
    ),
    (
        "Nothing yet, and I want it private and free",
        "local",
        "Run a model on this computer with Ollama or LM Studio. Nothing leaves "
        "your computer; it needs a recent computer and is slower than the big "
        "online models.",
    ),
    (
        "I use claude.ai or ChatGPT on the web and want them to use Steed",
        "cloud",
        "Pair them with Steed through a private link that expires. They can "
        "then use Steed's tools, and Steed still asks you first.",
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
        self.help_button = QPushButton("Not sure? Help me choose")
        self.help_button.clicked.connect(self._show_help)
        layout.addWidget(self.help_button)
        self.help = QWidget()
        guide = QVBoxLayout(self.help)
        guide.setContentsMargins(0, 0, 0, 0)
        guide.addWidget(QLabel("What do you have already?"))
        self.situations = []
        for index, (text, _kind, _why) in enumerate(SITUATIONS):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, i=index: self._suggest(i))
            guide.addWidget(button)
            self.situations.append(button)
        self.answer = QLabel()
        self.answer.setWordWrap(True)
        guide.addWidget(self.answer)
        self.use = QPushButton("Set it up")
        self.use.setProperty("primary", True)
        self.use.clicked.connect(lambda: self._choose(self.suggested))
        guide.addWidget(self.use)
        self.answer.hide()
        self.use.hide()
        self.help.hide()
        self.suggested = None
        layout.addWidget(self.help)
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

    def _show_help(self):
        self.help_button.hide()
        self.help.show()
        self._republish()

    def _suggest(self, index):
        _text, kind, why = SITUATIONS[index]
        self.suggested = kind
        self.answer.setText(why)
        self.answer.show()
        self.use.show()
        self._republish()

    def _republish(self):
        host = self.presentation_host()
        if host is not None:
            host.publish()

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
