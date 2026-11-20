"""Pair a cloud agent or a chatbot that supports connectors with Steed."""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from services.agent_bridge import EXPIRY_HOURS, available_methods
from ui.html_dialog import HtmlSettingsDialog

HOW_TO = (
    "Paste the link where your assistant adds connectors. On claude.ai: "
    "Settings → Connectors → Add custom connector. On ChatGPT: Settings → "
    "Apps & Connectors → Create (developer mode), with no authentication. A "
    "cloud agent takes it as a remote MCP server. Which plans allow custom "
    "connectors is up to each company."
)


class BridgeDialog(HtmlSettingsDialog):
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Pair a cloud agent")
        self.resize(600, 520)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "A private link that lets claude.ai, ChatGPT or a cloud agent use "
            "Steed's tools. Steed asks you about everything it requests, "
            f"whatever your access settings. The link stops working after "
            f"{EXPIRY_HOURS} hours, when you stop it, or when Steed closes. "
            "Anyone with the link can ask, so keep it to yourself."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(QLabel("How the link reaches this computer"))
        self.method = QComboBox()
        for method, label in available_methods():
            self.method.addItem(label, method)
        self.method.currentIndexChanged.connect(self._method_changed)
        layout.addWidget(self.method)
        self.base = QLineEdit()
        self.base.setPlaceholderText(
            "Your public https address, e.g. https://steed.example.com"
        )
        layout.addWidget(self.base)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "quote")
        layout.addWidget(self.status)
        self.link = QLineEdit()
        self.link.setReadOnly(True)
        self.link.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.link)
        how = QLabel(HOW_TO)
        how.setWordWrap(True)
        how.setProperty("role", "note")
        layout.addWidget(how)
        row = QHBoxLayout()
        self.start = QPushButton("Make a pairing link")
        self.start.setProperty("primary", True)
        self.start.clicked.connect(self._start)
        self.copy = QPushButton("Copy link")
        self.copy.clicked.connect(self._copy)
        self.show_link = QPushButton("Show link")
        self.show_link.clicked.connect(self._reveal)
        self.stop = QPushButton("Stop link")
        self.stop.clicked.connect(lambda: self.bridge.stop("You stopped the link."))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (self.start, self.copy, self.show_link, self.stop, close):
            row.addWidget(button)
        layout.addLayout(row)
        bridge.changed.connect(self.refresh)
        self.destroyed.connect(lambda: self._disconnect())
        self._method_changed()
        self.refresh()

    def _disconnect(self):
        try:
            self.bridge.changed.disconnect(self.refresh)
        except (TypeError, RuntimeError):
            pass

    def _method_changed(self, *_):
        self.base.setVisible(self.method.currentData() == "own")

    def _start(self):
        try:
            self.bridge.start(self.method.currentData(), self.base.text())
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))

    def _copy(self):
        if self.bridge.link:
            QApplication.clipboard().setText(self.bridge.link)
            self.status.setText(
                "Copied. Paste it into your assistant's connector settings."
            )

    def _reveal(self):
        shown = self.link.echoMode() == QLineEdit.EchoMode.Normal
        self.link.setEchoMode(
            QLineEdit.EchoMode.Password if shown else QLineEdit.EchoMode.Normal
        )
        self.show_link.setText("Show link" if shown else "Hide link")

    def refresh(self):
        bridge = self.bridge
        state = bridge.state
        if state == "on":
            left = max(0, int((bridge.expires - time.time()) // 60))
            text = f"The link works for {left // 60} h {left % 60} min more."
            if bridge.method == "own":
                text += f" Your address must forward to http://127.0.0.1:{bridge.port}."
        elif state == "starting":
            text = "Opening the link…"
        else:
            text = bridge.message or "No link is open."
        self.status.setText(text)
        self.link.setText(bridge.link)
        on = state == "on"
        for widget in (self.link, self.copy, self.show_link):
            widget.setEnabled(on)
        self.stop.setEnabled(state in ("starting", "on"))
        self.start.setEnabled(state not in ("starting", "on"))
        self.method.setEnabled(state not in ("starting", "on"))
