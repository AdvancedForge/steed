"""Organize Tabs with Agent: your agent proposes named groups for the
window's tabs and spots duplicates; you see the plan and apply it (or not).

The agent gets only each tab's title and address, numbered, and answers in
JSON. Nothing changes until you choose Apply.
"""

import json
import re

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.agent_once import AgentOnce, agent_command, agent_name

MAX_TABS = 120
MAX_GROUPS = 12

PROMPT = """Help someone tidy their browser tabs. Here are the open tabs, numbered:

{tabs}

Group them into a few clear groups by what they are for (a short name each,
2-3 words), and list tabs that are exact or near duplicates of another open
tab (the later copies only). Leave out tabs that fit no group.
Answer with only this JSON:
{{"groups": [{{"name": "...", "tabs": [numbers]}}], "duplicates": [numbers]}}
"""


def organize_prompt(tabs):
    """tabs: [(title, url)]."""
    lines = [
        f"{number}. {' '.join((title or 'Untitled').split())[:100]} — {url[:200]}"
        for number, (title, url) in enumerate(tabs[:MAX_TABS], start=1)
    ]
    return PROMPT.format(tabs="\n".join(lines))


def parse_plan(reply, count):
    """{"groups": [(name, [index…])], "duplicates": [index…]} with 0-based
    indexes into the tabs, or None when the reply holds no usable plan. Each
    tab ends up in at most one group; nothing outside the list is accepted."""
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None

    def indexes(values):
        found = []
        for value in values if isinstance(values, list) else []:
            if type(value) is int and 1 <= value <= count and value - 1 not in found:
                found.append(value - 1)
        return found

    taken, groups = set(), []
    for group in (
        raw.get("groups", [])[:MAX_GROUPS]
        if isinstance(raw.get("groups"), list)
        else []
    ):
        if not isinstance(group, dict):
            continue
        name = " ".join(str(group.get("name", "")).split())[:40]
        members = [i for i in indexes(group.get("tabs")) if i not in taken]
        if name and members:
            taken.update(members)
            groups.append((name, members))
    duplicates = indexes(raw.get("duplicates"))
    if not groups and not duplicates:
        return None
    return {"groups": groups, "duplicates": duplicates}


class OrganizeDialog(QDialog):
    """Asks the agent, shows its plan, applies it on request."""

    applied = pyqtSignal(dict)

    def __init__(self, window, tabs, parent=None):
        super().__init__(parent or window)
        self.window_, self.tabs = window, list(tabs)
        self.plan = None
        self.setWindowTitle("Organize Tabs with Agent")
        layout = QVBoxLayout(self)
        command = agent_command(window.config)
        self.status = QLabel(
            f"Asking {agent_name(command)} (it sees only tab titles and addresses)…"
            if command
            else "Install Claude Code or Codex to have an agent organize your tabs."
        )
        self.status.setWordWrap(True)
        self.status.setProperty("role", "quote")
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Group", "Tab"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.hide()
        layout.addWidget(self.table)
        self.close_duplicates = QCheckBox("Close duplicates")
        self.close_duplicates.setChecked(True)
        self.close_duplicates.hide()
        layout.addWidget(self.close_duplicates)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton("Apply")
        self.apply_button.setProperty("primary", True)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.asking = None
        if command:
            self.asking = AgentOnce(
                command,
                organize_prompt([(t.title(), t.url().toString()) for t in self.tabs]),
                self,
            )
            self.asking.answered.connect(self.show_plan)
            self.asking.failed.connect(self.status.setText)
        self.finished.connect(lambda _: self.asking and self.asking.stop())

    def show_plan(self, reply):
        plan = parse_plan(reply, len(self.tabs))
        if plan is None:
            self.status.setText("The agent's answer had no plan. Try again later.")
            return
        self.plan = plan
        # Each group's name once, beside its first tab.
        rows = [
            (
                name if n == 0 else "",
                self.tabs[i].title() or self.tabs[i].url().toString(),
            )
            for name, members in plan["groups"]
            for n, i in enumerate(members)
        ]
        self.table.setRowCount(len(rows))
        for row, (name, title) in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(title))
        self.table.setVisible(bool(rows))
        duplicates = len(plan["duplicates"])
        self.close_duplicates.setText(
            f"Close {duplicates} duplicate tab{'s' * (duplicates != 1)}"
        )
        self.close_duplicates.setVisible(bool(duplicates))
        groups = len(plan["groups"])
        self.status.setText(
            f"{groups} group{'s' * (groups != 1)}"
            + (
                f" and {duplicates} duplicate{'s' * (duplicates != 1)}"
                if duplicates
                else ""
            )
            + ". Nothing changes until you apply it."
        )
        self.apply_button.setEnabled(True)

    def apply(self):
        if self.plan is None:
            return
        window = self.window_
        for name, members in self.plan["groups"]:
            for i in members:
                tab = self.tabs[i]
                if tab in window.tabs and not tab.pinned:
                    window._set_tab_group_existing(window.tabs.index(tab), name)
        if self.close_duplicates.isChecked():
            for i in self.plan["duplicates"]:
                tab = self.tabs[i]
                if tab in window.tabs and len(window.tabs) > 1:
                    window._close_tab(window.tabs.index(tab))
        self.applied.emit(self.plan)
        self.accept()
