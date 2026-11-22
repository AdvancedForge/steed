"""Agents → Skills → Manage Skills…: your skills, and writing new ones."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog

BUILDER = (
    "Write the instruction for a reusable browser skill from this description. "
    "The skill is sent to an assistant together with the page the person is "
    "on. Reply with the instruction only, two or three sentences, addressed to "
    "the assistant.\n\nDescription: {description}"
)


class SkillEditor(HtmlSettingsDialog):
    def __init__(self, window, skill=None, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.skills = window.profile_manager.registry.skills
        self.skill = skill
        self.asking = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Edit Skill" if skill else "New Skill")
        skill = skill or {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Name (type / and this name in the agent chat to run it)")
        )
        self.name = QLineEdit(skill.get("name", ""))
        self.name.setPlaceholderText("Summarize this page")
        layout.addWidget(self.name)
        layout.addWidget(QLabel("What to ask your assistant"))
        self.ask = QPlainTextEdit(skill.get("ask", ""))
        self.ask.setPlaceholderText(
            "Summarize this page in five short bullets, then say what I should do."
        )
        layout.addWidget(self.ask)
        builder = QHBoxLayout()
        self.describe = QLineEdit()
        self.describe.setPlaceholderText(
            "Or describe it and let your assistant write it, e.g. turn recipes into a shopping list"
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
        layout.addWidget(QLabel("It looks at"))
        self.context = QComboBox()
        self.context.addItem("The page you are on", "page")
        self.context.addItem("Nothing but what you ask", "none")
        self.context.setCurrentIndex(0 if skill.get("context", "page") == "page" else 1)
        layout.addWidget(self.context)
        from services.routine_steps import describe_step

        self.steps = list(skill.get("steps") or [])
        self.keep_steps = QCheckBox(
            "Keep its saved steps (Steed does them first, with no model):\n"
            + "\n".join(f"{n}. {describe_step(s)}" for n, s in enumerate(self.steps, 1))
        )
        self.keep_steps.setChecked(True)
        if self.steps:
            layout.addWidget(self.keep_steps)
        self.routines = QComboBox()
        self.routines.addItem("Take saved steps from a routine…", None)
        for routine in window.profile_manager.registry.routines.list():
            if routine.get("steps"):
                self.routines.addItem(routine["name"], routine["steps"])
        self.routines.currentIndexChanged.connect(self._take_steps)
        if self.routines.count() > 1:
            layout.addWidget(self.routines)
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
        self.finished.connect(lambda _: self.asking and self.asking.stop())

    def _take_steps(self, *_):
        steps = self.routines.currentData()
        if steps:
            self.steps = list(steps)
            self.keep_steps.setChecked(True)
            self.status.setText(f"Takes the routine's {len(steps)} saved steps.")

    def write_for_me(self):
        from ui.assistant_choice import ensure

        description = self.describe.text().strip()
        if not description:
            self.write_status.setText("Describe what the skill should do first.")
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
        self.asking = AgentOnce(command, BUILDER.format(description=description), self)
        self.asking.answered.connect(self._written)
        self.asking.failed.connect(self.write_status.setText)

    def _written(self, text):
        self.ask.setPlainText(text.strip())
        if not self.name.text().strip():
            self.name.setText(self.describe.text().strip()[:60].capitalize())
        self.write_status.setText("Check it, change it if you like, then Save.")

    def values(self):
        return {
            "name": self.name.text(),
            "ask": self.ask.toPlainText(),
            "context": self.context.currentData(),
            "steps": self.steps if self.keep_steps.isChecked() else [],
        }

    def save(self):
        try:
            if self.skill:
                self.skills.update(self.skill["id"], self.values())
            else:
                self.skills.add(self.values())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.accept()


class SkillsDialog(HtmlSettingsDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.skills = window.profile_manager.registry.skills
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Skills")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Things you ask your assistant often, kept to run in one go: from "
            "Agents → Skills, the command palette, or by typing / in the agent "
            "chat. A skill looks at the page you are on unless you say otherwise, "
            "and can carry saved steps Steed does first, with no model."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "note")
        layout.addWidget(intro)
        self.list = QListWidget()
        self.list.setProperty("empty", "No skills yet. Make one with New skill….")
        self.list.itemDoubleClicked.connect(lambda _: self.edit())
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.selection_buttons = []
        for text, callback in (
            ("New skill…", self.new),
            ("Edit…", self.edit),
            ("Run", self.run),
            ("Remove", self.remove),
        ):
            button = QPushButton(text)
            if text == "New skill…":
                button.setProperty("primary", True)
            else:
                self.selection_buttons.append(button)
            if text == "Remove":
                row.addStretch()
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.list.currentItemChanged.connect(lambda *_: self._selection_changed())
        self.skills.changed.connect(self.refresh)
        self.finished.connect(lambda _: self.skills.changed.disconnect(self.refresh))
        self.refresh()

    def refresh(self):
        self.list.clear()
        for skill in self.skills.list():
            item = QListWidgetItem(
                skill["name"] + (" · with saved steps" if skill.get("steps") else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, skill["id"])
            item.setToolTip(skill["ask"])
            self.list.addItem(item)
        self._selection_changed()

    def _selection_changed(self):
        for button in self.selection_buttons:
            button.setEnabled(self.list.currentItem() is not None)

    def selected(self):
        item = self.list.currentItem()
        return self.skills.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def new(self):
        editor = SkillEditor(self.window)
        self.window._present_dialog(editor, modal=False)
        return editor

    def edit(self):
        skill = self.selected()
        if skill is not None:
            editor = SkillEditor(self.window, skill)
            self.window._present_dialog(editor, modal=False)
            return editor
        return None

    def run(self):
        skill = self.selected()
        if skill is not None:
            self.window.run_skill(skill["id"])
            self.accept()

    def remove(self):
        skill = self.selected()
        if skill is not None:
            self.skills.remove(skill["id"])
