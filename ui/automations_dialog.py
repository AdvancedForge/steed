"""Agents → Automations: your workflows, and making them."""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.automations import (
    STEPS,
    WRITER,
    check,
    describe_step,
    describe_trigger,
    parse_written,
)
from services.routines import EVERY_HOURS
from ui.html_dialog import HtmlSettingsDialog

STEP_CHOICES = (
    ("read", "Read a page (with your sign-ins)", "https://…"),
    ("gather", "Read a page, signing in with my saved login if asked", "https://…"),
    ("noticed", "Add what Steed noticed (watched pages that changed)", ""),
    ("fetch", "Fetch a web address", "https://…/feed.json"),
    (
        "ask",
        "Ask your assistant (an answer, no tools)",
        "Summarize {last} in three bullets",
    ),
    ("chat", "Start a chat with your agent (it may act)", "Book the cheapest slot"),
    ("skill", "Run a skill", "Skill name"),
    ("notify", "Notify me", "{last}"),
    ("open", "Open a page", "https://…"),
    ("contains", "Go on only if it contains", "in stock"),
    ("changed", "Go on only if it changed since last time", ""),
)
TRIGGER_CHOICES = (
    ("manual", "When I run it"),
    ("schedule", "On a schedule"),
    ("watch", "When a watched page changes"),
    ("startup", "When Steed starts"),
)


class AutomationEditor(HtmlSettingsDialog):
    def __init__(self, window, automation=None, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.service = window.profile_manager.registry.automations
        self.automation = automation
        self.asking = None
        self.steps = list((automation or {}).get("steps", []))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Edit automation" if automation else "New automation")
        automation = automation or {}
        trigger = automation.get("trigger", {"kind": "manual"})
        layout = QVBoxLayout(self)
        builder = QHBoxLayout()
        self.describe = QLineEdit()
        self.describe.setPlaceholderText(
            "Describe it, e.g. every morning read my bank's rates and tell me if they rose"
        )
        builder.addWidget(self.describe, 1)
        write = QPushButton("Write it for me")
        write.clicked.connect(self.write_for_me)
        builder.addWidget(write)
        layout.addLayout(builder)
        layout.addWidget(QLabel("Name"))
        self.name = QLineEdit(automation.get("name", ""))
        self.name.setPlaceholderText("Morning rates")
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Runs"))
        self.trigger = QComboBox()
        for kind, label in TRIGGER_CHOICES:
            self.trigger.addItem(label, kind)
        self.trigger.setCurrentIndex(
            max(0, self.trigger.findData(trigger.get("kind", "manual")))
        )
        layout.addWidget(self.trigger)
        self.timing = QComboBox()
        self.timing.addItem("Every day at", "daily")
        for hours in EVERY_HOURS:
            self.timing.addItem(f"Every {hours} hour{'s' * (hours != 1)}", hours)
        if trigger.get("schedule") == "hours":
            self.timing.setCurrentIndex(
                max(0, self.timing.findData(trigger.get("every")))
            )
        layout.addWidget(self.timing)
        self.at = QLineEdit(trigger.get("at", "08:00"))
        self.at.setPlaceholderText("08:00")
        layout.addWidget(self.at)
        self.watch = QComboBox()
        for watch in window.profile_manager.registry.watches.list():
            self.watch.addItem(watch["title"], watch["id"])
        if trigger.get("watch"):
            self.watch.setCurrentIndex(max(0, self.watch.findData(trigger["watch"])))
        layout.addWidget(self.watch)
        layout.addWidget(
            QLabel("Steps, in order ({last} is what the step before found)")
        )
        self.list = QListWidget()
        self.list.setProperty("empty", "No steps yet. Add the first below.")
        layout.addWidget(self.list, 1)
        adding = QHBoxLayout()
        self.step_kind = QComboBox()
        for kind, label, _ in STEP_CHOICES:
            self.step_kind.addItem(label, kind)
        self.step_kind.currentIndexChanged.connect(self._kind_changed)
        adding.addWidget(self.step_kind)
        self.step_value = QLineEdit()
        adding.addWidget(self.step_value, 1)
        add = QPushButton("Add step")
        add.clicked.connect(self.add_step)
        adding.addWidget(add)
        layout.addLayout(adding)
        moves = QHBoxLayout()
        for text, callback in (
            ("Move up", lambda: self.move_step(-1)),
            ("Move down", lambda: self.move_step(1)),
            ("Remove step", self.remove_step),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            moves.addWidget(button)
        layout.addLayout(moves)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(save)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.trigger.currentIndexChanged.connect(self._trigger_changed)
        self.timing.currentIndexChanged.connect(self._trigger_changed)
        self._trigger_changed()
        self._kind_changed()
        self.refresh_steps()
        self.finished.connect(lambda _: self.asking and self.asking.stop())

    def _trigger_changed(self, *_):
        kind = self.trigger.currentData()
        self.timing.setVisible(kind == "schedule")
        self.at.setVisible(kind == "schedule" and self.timing.currentData() == "daily")
        self.watch.setVisible(kind == "watch")

    def _kind_changed(self, *_):
        kind = self.step_kind.currentData()
        placeholder = next(p for k, _, p in STEP_CHOICES if k == kind)
        self.step_value.setPlaceholderText(placeholder)
        self.step_value.setVisible(STEPS[kind] is not None)

    def refresh_steps(self):
        self.list.clear()
        for number, step in enumerate(self.steps, 1):
            self.list.addItem(f"{number}. {describe_step(step)}")

    def add_step(self):
        kind = self.step_kind.currentData()
        step = {"do": kind}
        if kind == "gather":
            step.update(value=[self.step_value.text().strip()], signin=True)
        elif STEPS[kind] is not None:
            step["value"] = self.step_value.text().strip()
        try:
            check({"name": "x", "steps": [step]})
        except ValueError as error:
            self.status.setText(str(error).replace("Step 1", "This step"))
            return
        self.steps.append(step)
        self.step_value.clear()
        self.status.setText("")
        self.refresh_steps()

    def move_step(self, offset):
        row = self.list.currentRow()
        target = row + offset
        if 0 <= row < len(self.steps) and 0 <= target < len(self.steps):
            self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
            self.refresh_steps()
            self.list.setCurrentRow(target)

    def remove_step(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.steps):
            del self.steps[row]
            self.refresh_steps()

    def values(self):
        kind = self.trigger.currentData()
        trigger = {"kind": kind}
        if kind == "schedule":
            if self.timing.currentData() == "daily":
                trigger["at"] = self.at.text().strip()
            else:
                trigger["every"] = self.timing.currentData()
        elif kind == "watch":
            trigger["watch"] = self.watch.currentData() or ""
        return {"name": self.name.text(), "trigger": trigger, "steps": self.steps}

    def load(self, automation):
        """Show an automation (one your assistant wrote) to check and save."""
        self.name.setText(automation["name"])
        trigger = automation["trigger"]
        self.trigger.setCurrentIndex(max(0, self.trigger.findData(trigger["kind"])))
        if trigger.get("schedule") == "hours":
            self.timing.setCurrentIndex(max(0, self.timing.findData(trigger["every"])))
        else:
            self.timing.setCurrentIndex(0)
            self.at.setText(trigger.get("at", "08:00"))
        if trigger.get("watch"):
            self.watch.setCurrentIndex(max(0, self.watch.findData(trigger["watch"])))
        self.steps = list(automation["steps"])
        self.refresh_steps()

    def write_for_me(self):
        from services.agent_once import AgentOnce, agent_command

        description = self.describe.text().strip()
        command = agent_command(self.window.config)
        if not description:
            self.status.setText("Describe what it should do first.")
            return
        if command is None:
            self.status.setText("Add an assistant first (Agents → Add an Assistant…).")
            return
        self.status.setText("Writing…")
        self.asking = AgentOnce(
            command, WRITER.replace("{description}", description), self
        )
        self.asking.answered.connect(self._written)
        self.asking.failed.connect(self.status.setText)

    def _written(self, reply):
        try:
            self.load(parse_written(reply))
        except ValueError as error:
            self.status.setText(f"{error} Try describing it differently.")
            return
        self.status.setText("Check each step, change anything, then Save.")

    def save(self):
        try:
            if self.automation:
                self.service.update(self.automation["id"], self.values())
            else:
                self.service.add(
                    self.values(), self.window.profile_manager.current_profile_name
                )
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.accept()


class AutomationsDialog(HtmlSettingsDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.service = window.profile_manager.registry.automations
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Automations")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Your own workflows: something starts them (a time, a watched page "
            "changing, Steed starting, or you) and they do steps in order: read "
            "pages, fetch data, ask your assistant or start an agent chat, run a "
            "skill, notify you, open pages, or stop unless something is true. "
            "Many need no AI at all."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "note")
        layout.addWidget(intro)
        self.table = QTableWidget(0, 3)
        self.table.setProperty(
            "empty", "No automations yet. Describe one and let your assistant write it."
        )
        self.table.setHorizontalHeaderLabels(["Automation", "Runs", "Last time"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _: self.edit())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.selection_buttons = []
        for text, callback in (
            ("New automation…", self.new),
            ("Morning brief…", self.morning_brief),
            ("Run now", self.run),
            ("Edit…", self.edit),
            ("Pause", self.pause),
            ("Remove", self.remove),
        ):
            button = QPushButton(text)
            if text == "New automation…":
                button.setProperty("primary", True)
            elif text != "Morning brief…":
                self.selection_buttons.append(button)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.pause_button = self.selection_buttons[2]
        layout.addLayout(row)
        self.service.changed.connect(self.refresh)
        self.finished.connect(lambda _: self.service.changed.disconnect(self.refresh))
        self.refresh()

    def refresh(self):
        items = self.service.list()
        self.table.setRowCount(len(items))
        for index, item in enumerate(items):
            last = (
                time.strftime("%b %d %H:%M", time.localtime(item["last_run"]))
                + (f": {item['last_outcome']}" if item["last_outcome"] else "")
                if item["last_run"]
                else "Not yet"
            )
            runs = "Paused" if item["paused"] else describe_trigger(item["trigger"])
            for column, value in enumerate((item["name"], runs, last)):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item["id"])
                self.table.setItem(index, column, cell)
        self._selection_changed()

    def selected(self):
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        return self.service.get(cell.data(Qt.ItemDataRole.UserRole)) if cell else None

    def _selection_changed(self):
        item = self.selected()
        for button in self.selection_buttons:
            button.setEnabled(item is not None)
        self.pause_button.setText("Resume" if item and item["paused"] else "Pause")

    def new(self):
        editor = AutomationEditor(self.window)
        self.window._present_dialog(editor, modal=False)
        return editor

    def morning_brief(self):
        """A brief that is ready to go: add the pages it should read."""
        from services.automations import check
        from services.routines import BRIEF_ASK

        editor = AutomationEditor(self.window)
        editor.load(
            check(
                {
                    "name": "Morning brief",
                    "trigger": {
                        "kind": "schedule",
                        "at": "08:00",
                        "days": [0, 1, 2, 3, 4],
                    },
                    "steps": [{"do": "noticed"}, {"do": "chat", "value": BRIEF_ASK}],
                }
            )
        )
        editor.status.setText(
            "Add a step that reads your calendar and inbox (Read a page) before "
            "the chat, then Save."
        )
        self.window._present_dialog(editor, modal=False)
        return editor

    def edit(self):
        item = self.selected()
        if item is None:
            return None
        editor = AutomationEditor(self.window, item)
        self.window._present_dialog(editor, modal=False)
        return editor

    def run(self):
        item = self.selected()
        if item is not None:
            self.service.run(item["id"])

    def pause(self):
        item = self.selected()
        if item is not None:
            self.service.pause(item["id"], not item["paused"])

    def remove(self):
        item = self.selected()
        if item is not None:
            self.service.remove(item["id"])
