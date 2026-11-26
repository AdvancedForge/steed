"""Your agent proposes changing or removing something you made: see what it
is now and what it would be, then Apply or Decline."""

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.task_policy import TaskPermissionError
from ui.html_dialog import HtmlSettingsDialog

NOUNS = {
    "page": "page",
    "automation": "automation",
    "skill": "skill",
    "watch": "watch",
    "script": "user script",
    "app": "panel app",
}


def readable(kind, record):
    """A record as a person reads it: a page's or app's HTML, a script's code,
    anything else as indented JSON."""
    if record is None:
        return ""
    if kind in ("page", "app"):
        return record.get("html", "")
    if kind == "script":
        parts = [f"Sites: {', '.join(record.get('sites') or []) or 'all'}"]
        if record.get("css"):
            parts.append("/* CSS */\n" + record["css"])
        if record.get("js"):
            parts.append("// JavaScript\n" + record["js"])
        return "\n\n".join(parts)
    return json.dumps(record, indent=2, ensure_ascii=False)


class CustomizationChangeDialog(HtmlSettingsDialog):
    def __init__(self, service, request, parent=None):
        super().__init__(parent)
        self.service, self.request = service, request
        self.task_id = request.task_id
        self.request_key = request.request_key
        entry = service.customization_changes.entry(request.task_id, request.request_id)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        noun = NOUNS.get(request.kind, request.kind)
        removing = request.action == "remove"
        self.setWindowTitle(
            f"Remove your {noun}?" if removing else f"Change your {noun}?"
        )
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Your agent wants to remove “{request.name}”."
            if removing
            else f"Your agent wants to change “{request.name}”. Read what it "
            "would become before you apply it."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(QLabel("Now"))
        before = QPlainTextEdit(readable(request.kind, entry and entry["before"]))
        before.setReadOnly(True)
        before.setAccessibleName("Now")
        layout.addWidget(before, 1)
        if not removing:
            layout.addWidget(QLabel("After"))
            after = QPlainTextEdit(readable(request.kind, entry and entry["after"]))
            after.setReadOnly(True)
            after.setAccessibleName("After")
            layout.addWidget(after, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        apply = QPushButton("Remove" if removing else "Apply")
        apply.setProperty("primary", True)
        apply.clicked.connect(self.apply)
        decline = QPushButton("Decline")
        decline.clicked.connect(self.decline)
        for button in (apply, decline):
            row.addWidget(button)
        layout.addLayout(row)

    def _decide(self, approve):
        try:
            self.service.customization_changes.resolve(
                self.task_id, self.request.request_id, approve=approve
            )
        except (ValueError, KeyError, OSError, TaskPermissionError) as error:
            self.status.setText(str(error))
            return
        self.accept()

    def apply(self):
        self._decide(True)

    def decline(self):
        self._decide(False)
