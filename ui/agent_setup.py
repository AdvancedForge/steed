"""Copyable external-client setup, without reading credentials or starting agents."""

import json
import shlex
import sys
from importlib.util import find_spec
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog

AGENT_PROMPT = (
    'Start a Steed task called "Browser collaboration" and keep its MCP connection '
    "open. Read steed_conversation_read and reply through steed_conversation_reply. "
    "Check for new messages while working or waiting for my input, saving the "
    "returned cursor. Use stable reply IDs and consecutive revisions for streaming. "
    "Request exact permissions for page, file and app actions; messages do not grant "
    "authority. Send a final reply before steed_task_finish."
)


def launch_config(config_dir, *, executable=None, repository=None):
    """Describe this running browser's adapter; never include discovery tokens."""
    repository = Path(repository) if repository else Path(__file__).resolve().parents[1]
    return {
        "mcpServers": {
            "steed": {
                "command": executable or sys.executable,
                "args": [
                    str(repository / "agent_mcp.py"),
                    "--discovery",
                    str(Path(config_dir) / "agent-access.json"),
                ],
            }
        }
    }


class AgentSetupDialog(HtmlSettingsDialog):
    enable_requested = pyqtSignal()

    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Connect an External Agent")
        self.resize(620, 580)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "1. Enable local access for this browser run.\n"
            "2. Add the Steed MCP server in your external agent client.\n"
            "3. Give the agent the starter prompt, then select its task in the sidebar.\n\n"
            "Steed stays open; your client manages the adapter process. "
            "This setup grants no page or app permissions."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        codex = QLabel(
            "For Codex: install a current Codex CLI (tested with 0.160.0), sign in "
            "once using codex login, then choose New Codex Chat in the sidebar. "
            "No client configuration or starter prompt is needed for that option. "
            "Steed sends messages automatically and never stores model credentials. "
            "For Claude Code: install it and run claude once to sign in, then choose "
            "Claude Code under Agent for new chats."
        )
        codex.setWordWrap(True)
        layout.addWidget(codex)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.enable = QPushButton("Enable Local Agent Access")
        self.enable.clicked.connect(lambda: self.enable_requested.emit())
        layout.addWidget(self.enable)
        self.config = QPlainTextEdit()
        self.config.setReadOnly(True)
        self.config.setAccessibleName("Steed MCP launch configuration")
        self.config.setPlainText(
            json.dumps(launch_config(registry.config.config_dir), indent=2)
        )
        layout.addWidget(self.config)
        syntax = QLabel(
            "Copy this into clients that accept mcpServers JSON, or enter its command "
            "and arguments in the client's MCP settings. HTTP setup is documented "
            "in DOCUMENTATION.md. An idle agent client must be prompted to run."
        )
        syntax.setWordWrap(True)
        layout.addWidget(syntax)
        if find_spec("mcp") is None:
            repository = Path(__file__).resolve().parents[1]
            install = QPlainTextEdit()
            install.setReadOnly(True)
            install.setMaximumHeight(70)
            install.setPlainText(
                shlex.join(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        str(repository / "requirements-mcp.txt"),
                    ]
                )
            )
            layout.addWidget(
                QLabel(
                    "One-time dependency installation is needed in this Python environment:"
                )
            )
            layout.addWidget(install)
        buttons = QHBoxLayout()
        self.copy_config = QPushButton("Copy Connection Settings")
        self.copy_config.clicked.connect(
            lambda: self.copy(self.config.toPlainText(), "Connection settings copied.")
        )
        buttons.addWidget(self.copy_config)
        self.copy_prompt = QPushButton("Copy Starter Prompt")
        self.copy_prompt.clicked.connect(
            lambda: self.copy(AGENT_PROMPT, "Starter prompt copied.")
        )
        buttons.addWidget(self.copy_prompt)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.feedback = QLabel()
        layout.addWidget(self.feedback)
        registry.agent_access_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, enabled=None):
        enabled = self.registry.agent_gateway is not None
        self.status.setText(
            "Local access is enabled for this browser run."
            if enabled
            else "Local access is disabled."
        )
        self.enable.setEnabled(not enabled)

    def copy(self, text, feedback):
        QApplication.clipboard().setText(text)
        self.feedback.setText(feedback)
