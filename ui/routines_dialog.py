"""Agents → Routines…: what your agent does on a schedule."""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.routines import DAYS, EVERY_HOURS, describe
from ui.html_dialog import HtmlSettingsDialog

EXAMPLE = (
    "Read these pages and tell me, in a few lines, what needs my attention "
    "today and what can wait."
)


class RoutineEditor(HtmlSettingsDialog):
    def __init__(self, service, profile, routine=None, parent=None):
        super().__init__(parent)
        self.service, self.profile, self.routine = service, profile, routine
        self.setWindowTitle("Edit Routine" if routine else "New Routine")
        routine = routine or {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Name"))
        self.name = QLineEdit(routine.get("name", ""))
        self.name.setPlaceholderText("Morning brief")
        layout.addWidget(self.name)
        layout.addWidget(QLabel("What should your agent do?"))
        self.ask = QPlainTextEdit(routine.get("ask", ""))
        self.ask.setPlaceholderText(EXAMPLE)
        layout.addWidget(self.ask)
        layout.addWidget(QLabel("Pages to read first (one address per line, optional)"))
        self.pages = QPlainTextEdit("\n".join(routine.get("pages", [])))
        self.pages.setPlaceholderText(
            "https://mail.example.org/\nhttps://calendar.example.org/"
        )
        layout.addWidget(self.pages)
        layout.addWidget(QLabel("When"))
        self.kind = QComboBox()
        self.kind.addItem("Every day at a time", "daily")
        self.kind.addItem("Every few hours", "hours")
        self.kind.setCurrentIndex(0 if routine.get("kind", "daily") == "daily" else 1)
        layout.addWidget(self.kind)
        self.at = QLineEdit(routine.get("at", "08:00"))
        self.at.setPlaceholderText("08:00")
        layout.addWidget(self.at)
        days = QHBoxLayout()
        self.days = []
        for index, name in enumerate(DAYS):
            box = QCheckBox(name)
            box.setChecked(index in routine.get("days", range(7)))
            self.days.append(box)
            days.addWidget(box)
        layout.addLayout(days)
        self.every = QComboBox()
        for hours in EVERY_HOURS:
            self.every.addItem(f"Every {hours} hour{'s' * (hours != 1)}", hours)
        self.every.setCurrentIndex(max(0, self.every.findData(routine.get("every", 4))))
        layout.addWidget(self.every)
        layout.addWidget(QLabel("Each run"))
        self.tell = QComboBox()
        self.tell.addItem("Your agent reads it and replies", "agent")
        self.tell.addItem("Just tell me when it changes (no assistant)", "changes")
        self.tell.setCurrentIndex(1 if routine.get("tell") == "changes" else 0)
        layout.addWidget(self.tell)
        self.signin = QCheckBox(
            "If a page asks me to sign in, sign in with my saved login "
            "(Steed's passwords or an unlocked Bitwarden)"
        )
        self.signin.setChecked(bool(routine.get("signin")))
        layout.addWidget(self.signin)
        from services.routine_steps import describe_step

        self.saved_steps = list(routine.get("steps") or [])
        self.steps_label = QLabel(
            "Saved steps, which Steed does itself before reading, with no model:\n"
            + "\n".join(
                f"{n}. {describe_step(step)}"
                for n, step in enumerate(self.saved_steps, 1)
            )
        )
        self.steps_label.setTextFormat(Qt.TextFormat.PlainText)
        self.steps_label.setWordWrap(True)
        self.steps_label.setProperty("role", "quote")
        self.keep_steps = QCheckBox("Keep these saved steps")
        self.keep_steps.setChecked(True)
        if self.saved_steps:
            layout.addWidget(self.steps_label)
            layout.addWidget(self.keep_steps)
        note = QLabel(
            "Each run starts a new conversation in the agent sidebar. The pages "
            "are read with this profile's sign-ins and their text goes to your "
            "agent with the message; anything more it wants to do asks you first."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")
        layout.addWidget(note)
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
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self, *_):
        daily = self.kind.currentData() == "daily"
        self.at.setVisible(daily)
        for box in self.days:
            box.setVisible(daily)
        self.every.setVisible(not daily)

    def values(self):
        return {
            "name": self.name.text(),
            "ask": self.ask.toPlainText(),
            "pages": [p for p in self.pages.toPlainText().splitlines() if p.strip()],
            "kind": self.kind.currentData(),
            "at": self.at.text().strip(),
            "days": [i for i, box in enumerate(self.days) if box.isChecked()],
            "every": self.every.currentData(),
            "tell": self.tell.currentData(),
            "signin": self.signin.isChecked(),
            "steps": self.saved_steps if self.keep_steps.isChecked() else [],
        }

    def save(self):
        try:
            if self.routine:
                self.service.update(self.routine["id"], self.values())
            else:
                self.service.add(self.values(), self.profile)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.accept()


class RoutinesDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.service = window.profile_manager.registry.routines
        self.setWindowTitle("Routines")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Things your agent does on a schedule, like a morning brief of your "
            "inbox and calendar. Steed must be running; a missed run happens when "
            "it next starts (within a day)."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "note")
        layout.addWidget(intro)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Routine", "When", "Last run"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _: self.edit_selected())
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for text, callback in (
            ("New Routine…", self.add),
            ("Edit…", self.edit_selected),
            ("Run now", self.run_selected),
            ("Pause or resume", self.pause_selected),
            ("Remove", self.remove_selected),
        ):
            button = QPushButton(text)
            if text == "New Routine…":
                button.setProperty("primary", True)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.service.changed.connect(self.refresh)
        self.finished.connect(lambda _: self.service.changed.disconnect(self.refresh))
        self.refresh()

    def refresh(self):
        routines = self.service.list()
        self.table.setRowCount(len(routines))
        for index, routine in enumerate(routines):
            last = routine.get("last_run", 0)
            shown = (
                "Paused"
                if routine.get("paused")
                else time.strftime("%b %d %H:%M", time.localtime(last))
                if last and last - routine.get("created", 0) > 1
                else "Not yet"
            )
            for column, value in enumerate((routine["name"], describe(routine), shown)):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, routine["id"])
                self.table.setItem(index, column, item)

    def selected(self):
        item = (
            self.table.item(self.table.currentRow(), 0)
            if self.table.rowCount()
            else None
        )
        found = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((r for r in self.service.list() if r["id"] == found), None)

    def _editor(self, routine=None):
        editor = RoutineEditor(
            self.service,
            self.window.profile_manager.current_profile_name,
            routine,
            self.window,
        )
        self.window._present_dialog(editor, modal=False)
        return editor

    def add(self):
        return self._editor()

    def edit_selected(self):
        routine = self.selected()
        if routine is not None:
            return self._editor(routine)
        return None

    def run_selected(self):
        routine = self.selected()
        if routine is not None:
            self.service.run(routine)

    def pause_selected(self):
        routine = self.selected()
        if routine is not None:
            self.service.pause(routine["id"], not routine.get("paused"))

    def remove_selected(self):
        routine = self.selected()
        if routine is not None:
            self.service.remove(routine["id"])


class RoutineReviewDialog(RoutineEditor):
    """An agent's proposed routine: change it if you like, then add or decline."""

    def __init__(self, service, proposal, parent=None):
        manager = getattr(parent, "profile_manager", None)
        profile = manager.current_profile_name if manager else "global"
        super().__init__(service.profiles.routines, profile, proposal.routine, parent)
        self.task_service, self.proposal = service, proposal
        self.task_id = proposal.task_id
        self.request_key = proposal.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Your agent suggests a routine")
        buttons = {b.text(): b for b in self.findChildren(QPushButton)}
        buttons["Save"].setText("Add routine")
        buttons["Cancel"].setText("Later")
        decline = QPushButton("Decline")
        decline.clicked.connect(self.decline)
        row = self.layout().itemAt(self.layout().count() - 1).layout()
        row.insertWidget(1, decline)

    def save(self):
        self.apply()

    def apply(self):
        from services.routines import check

        try:
            routine = check(self.values())
            self.task_service.routines.resolve(
                self.task_id,
                self.proposal.request_id,
                approve=True,
                profile=self.profile,
                routine=routine,
            )
        except (ValueError, KeyError) as error:
            self.status.setText(str(error) or "This suggestion is no longer available.")
            return
        self.accept()

    def decline(self):
        try:
            self.task_service.routines.resolve(
                self.task_id, self.proposal.request_id, approve=False
            )
        except (ValueError, KeyError):
            self.status.setText("This suggestion is no longer available.")
            return
        self.accept()
