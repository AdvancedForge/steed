"""Agents → Automations: your workflows, and making them."""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
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
    ("decide", "Go on only if my assistant says yes", "Is this a good deal?"),
    ("save", "Save it to my file library", "kettle prices {date}.md"),
    ("wait", "Wait (minutes)", "10"),
)
TRIGGER_CHOICES = (
    ("manual", "When I run it"),
    ("schedule", "On a schedule"),
    ("watch", "When a watched page changes"),
    ("startup", "When Steed starts"),
    ("download", "When a download finishes"),
    ("visit", "When I visit a site"),
    ("shortcut", "When I press a shortcut"),
    ("page", "From a page's right-click menu"),
)
TRIGGER_FIELDS = {
    "download": ("match", "Only files named like (optional), e.g. .pdf"),
    "visit": ("site", "example.com"),
    "shortcut": ("keys", "Ctrl+Alt+K"),
}


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
        # What Write it for me is doing, right under it.
        self.write_status = QLabel("")
        self.write_status.setWordWrap(True)
        self.write_status.setProperty("role", "note")
        layout.addWidget(self.write_status)
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
        self.days = QComboBox()
        self.set_days(trigger.get("days", list(range(7))))
        layout.addWidget(self.days)
        self.watch = QComboBox()
        for watch in window.profile_manager.registry.watches.list():
            self.watch.addItem(watch["title"], watch["id"])
        if trigger.get("watch"):
            self.watch.setCurrentIndex(max(0, self.watch.findData(trigger["watch"])))
        layout.addWidget(self.watch)
        self.trigger_value = QLineEdit()
        kind = trigger.get("kind")
        if kind in TRIGGER_FIELDS:
            self.trigger_value.setText(trigger.get(TRIGGER_FIELDS[kind][0], ""))
        layout.addWidget(self.trigger_value)
        layout.addWidget(
            QLabel(
                "Steps, in order. Write {last} in a step to use what the one before found."
            )
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
        # Kept in view at the bottom however long the steps get.
        row = QDialogButtonBox()
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addButton(save, QDialogButtonBox.ButtonRole.AcceptRole)
        row.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(row)
        self.trigger.currentIndexChanged.connect(self._trigger_changed)
        self.timing.currentIndexChanged.connect(self._trigger_changed)
        self._trigger_changed()
        self._kind_changed()
        self.refresh_steps()
        self.finished.connect(lambda _: self.asking and self.asking.stop())

    def set_days(self, days):
        """Which days a daily schedule runs: the usual sets, or the one it had."""
        from services.routines import DAYS

        days = sorted(set(days))
        self.days.clear()
        choices = [
            ("Every day", list(range(7))),
            ("Weekdays", [0, 1, 2, 3, 4]),
            ("Weekends", [5, 6]),
        ]
        if days not in [d for _, d in choices]:
            choices.append((", ".join(DAYS[d] for d in days), days))
        for label, value in choices:
            self.days.addItem(label, value)
        self.days.setCurrentIndex(
            next(i for i, (_, value) in enumerate(choices) if value == days)
        )

    def _trigger_changed(self, *_):
        kind = self.trigger.currentData()
        self.timing.setVisible(kind == "schedule")
        daily = kind == "schedule" and self.timing.currentData() == "daily"
        self.at.setVisible(daily)
        self.days.setVisible(daily)
        self.watch.setVisible(kind == "watch")
        self.trigger_value.setVisible(kind in TRIGGER_FIELDS)
        if kind in TRIGGER_FIELDS:
            self.trigger_value.setPlaceholderText(TRIGGER_FIELDS[kind][1])

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
                trigger["days"] = list(self.days.currentData())
            else:
                trigger["every"] = self.timing.currentData()
        elif kind == "watch":
            trigger["watch"] = self.watch.currentData() or ""
        elif kind in TRIGGER_FIELDS:
            trigger[TRIGGER_FIELDS[kind][0]] = self.trigger_value.text().strip()
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
            self.set_days(trigger.get("days", list(range(7))))
        if trigger.get("watch"):
            self.watch.setCurrentIndex(max(0, self.watch.findData(trigger["watch"])))
        if trigger["kind"] in TRIGGER_FIELDS:
            self.trigger_value.setText(
                trigger.get(TRIGGER_FIELDS[trigger["kind"]][0], "")
            )
        self.steps = list(automation["steps"])
        self.refresh_steps()

    def write_for_me(self):
        from ui.assistant_choice import ensure

        description = self.describe.text().strip()
        if not description:
            self.write_status.setText("Describe what it should do first.")
            return
        ensure(self.window, lambda command: self._write(description, command))

    def _write(self, description, command):
        from PyQt6 import sip

        from services.agent_once import AgentOnce, agent_name

        if sip.isdeleted(self):
            return
        if command is None:
            self.write_status.setText(
                "Add an assistant first (Agents → Add an Assistant…)."
            )
            return
        self.write_status.setText(f"Writing with {agent_name(command)}…")
        self.asking = AgentOnce(
            command, WRITER.replace("{description}", description), self
        )
        self.asking.answered.connect(self._written)
        self.asking.failed.connect(self.write_status.setText)

    def _written(self, reply):
        try:
            self.load(parse_written(reply))
        except ValueError as error:
            self.write_status.setText(f"{error} Try describing it differently.")
            return
        self.write_status.setText("Check each step, change anything, then Save.")

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
        window = self.window
        uses_helper = any(s["do"] in ("ask", "decide") for s in self.steps)
        self.accept()
        if uses_helper:  # It will ask in the background: settle who, now.
            from ui.assistant_choice import ensure

            ensure(window, lambda _command: None)


class AutomationsDialog(HtmlSettingsDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.service = window.profile_manager.registry.automations
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Automations")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Steps Steed runs for you when something happens: at a time, when a "
            "page changes, when you press a shortcut, and more."
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
            ("Examples…", self.examples),
            ("Run now", self.run),
            ("Edit…", self.edit),
            ("History…", self.history),
            ("Pause", self.pause),
            ("Remove", self.remove),
        ):
            button = QPushButton(text)
            if text == "New automation…":
                button.setProperty("primary", True)
            elif text != "Examples…":
                self.selection_buttons.append(button)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.pause_button = self.selection_buttons[3]
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

    def examples(self):
        """Ready-made automations to start from."""
        dialog = ExamplesDialog(self.window)
        self.window._present_dialog(dialog, modal=False)
        return dialog

    def morning_brief(self):
        """The Morning brief example, ready to finish."""
        return ExamplesDialog.open_example(self.window, 0)

    def edit(self):
        item = self.selected()
        if item is None:
            return None
        editor = AutomationEditor(self.window, item)
        self.window._present_dialog(editor, modal=False)
        return editor

    def history(self):
        item = self.selected()
        if item is None:
            return None
        dialog = RunHistoryDialog(self.window, item)
        self.window._present_dialog(dialog, modal=False)
        return dialog

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


def describe_run(run):
    """One run, step by step, as the history shows it."""
    lines = [f"Started with: {run['start']}" if run.get("start") else "Started"]
    for step in run.get("steps", []):
        lines.append(f"\n{step['n']}. {step['what']}")
        if step.get("got"):
            lines.append(f"   had: {step['got']}")
        if "gave" in step:
            lines.append(f"   gave: {step['gave']}")
        if step.get("stopped"):
            lines.append("   stopped here")
    lines.append(f"\n{run['outcome']}")
    return "\n".join(lines)


class RunHistoryDialog(HtmlSettingsDialog):
    """An automation's last runs: what each step had and gave, where it stopped."""

    def __init__(self, window, automation, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.automation = automation
        self.service = window.profile_manager.registry.automations
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"History: {automation['name']}")
        layout = QVBoxLayout(self)
        note = QLabel(
            "The last 20 runs, newest first, with a short part of what each step "
            "had and gave. Kept on this computer only."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
        self.runs = QListWidget()
        self.runs.setProperty("empty", "No runs yet. Run it now to see each step.")
        self.runs.currentRowChanged.connect(self._show)
        layout.addWidget(self.runs)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail, 1)
        row = QHBoxLayout()
        clear = QPushButton("Clear History")
        clear.clicked.connect(self.clear)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(clear)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.items = self.service.history(self.automation["id"])
        self.runs.clear()
        for run in self.items:
            when = time.strftime("%b %d %H:%M", time.localtime(run["started"]))
            self.runs.addItem(f"{when}: {run['outcome']}")
        if self.items:
            self.runs.setCurrentRow(0)
        else:
            self.detail.setPlainText("")

    def _show(self, row):
        if 0 <= row < len(self.items):
            self.detail.setPlainText(describe_run(self.items[row]))

    def clear(self):
        self.service.clear_history(self.automation["id"])
        self.refresh()


class ExamplesDialog(HtmlSettingsDialog):
    """Starters: each opens in the editor to check and change before saving."""

    def __init__(self, window, parent=None):
        from services.automations import starters

        super().__init__(parent or window)
        self.window = window
        self.items = starters()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Start from an example")
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        for title, what, _automation, _hint in self.items:
            self.list.addItem(f"{title}\n{what}")
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _: self.use())
        layout.addWidget(self.list, 1)
        note = QLabel(
            "It opens in the editor: change anything, then Save. Nothing runs "
            "until you do."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
        row = QDialogButtonBox()
        use = QPushButton("Use this one")
        use.setProperty("primary", True)
        use.clicked.connect(self.use)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addButton(use, QDialogButtonBox.ButtonRole.AcceptRole)
        row.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(row)

    @staticmethod
    def open_example(window, index):
        from services.automations import check, starters

        _title, _what, automation, hint = starters()[index]
        editor = AutomationEditor(window)
        editor.load(check(automation))
        editor.status.setText(hint)
        window._present_dialog(editor, modal=False)
        return editor

    def use(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.items):
            return None
        window = self.window
        self.accept()
        return self.open_example(window, row)
