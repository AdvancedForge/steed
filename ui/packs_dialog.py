"""View → Packs…: share your pages, skills and automations as one file, or
add someone's pack after looking at every item in it."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services import packs
from ui.html_dialog import HtmlSettingsDialog

FILTER = "Steed packs (*.steedpack *.json)"
WORDS = {"pages": "Page", "skills": "Skill", "automations": "Automation"}


def _label(section, item):
    if section == "pages":
        return f"Page: steed:{item['name']} ({item['title'] or 'untitled'})"
    return f"{WORDS[section]}: {item['name']}"


def _detail(section, item):
    if section == "pages":
        return item["html"]
    if section == "skills":
        lines = [item["ask"]]
        for step in item.get("steps", ()):
            lines.append("Step: " + ", ".join(f"{k} {v}" for k, v in step.items()))
        return "\n\n".join(lines)
    from services.automations import describe_step, describe_trigger

    trigger = describe_trigger(item["trigger"])
    if item.get("was_watch"):
        trigger += " (it ran when a watched page changed; pick a watch after adding)"
    lines = ["Starts: " + trigger]
    lines += [f"{n}. {describe_step(s)}" for n, s in enumerate(item["steps"], 1)]
    for step in item["steps"]:
        for saved in step.get("steps", ()):
            lines.append("   then " + ", ".join(f"{k} {v}" for k, v in saved.items()))
    return "\n".join(lines)


def _checkable(text, data, checked):
    item = QListWidgetItem(text)
    item.setData(Qt.ItemDataRole.UserRole, data)
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return item


def _ticked(widget):
    for row in range(widget.count()):
        item = widget.item(row)
        if item.checkState() == Qt.CheckState.Checked:
            yield item.data(Qt.ItemDataRole.UserRole)


class PacksDialog(HtmlSettingsDialog):
    """Choose what to share and save it as a pack, or add a pack."""

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Packs")
        self.resize(600, 520)
        registry = window.profile_manager.registry
        self.registry = registry
        self.profile = window.profile_manager.current_profile_name
        layout = QVBoxLayout(self)
        intro = QLabel(
            "A pack is your pages, skills and automations in one file, to keep or "
            "to give someone. It holds exactly what each item is, with the web "
            "addresses and text in it, and nothing else: no logins, history or "
            "settings."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "note")
        layout.addWidget(intro)
        layout.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("My dashboards")
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Put in the pack"))
        self.items = QListWidget()
        self.items.setProperty("empty", "Nothing to share yet.")
        found = {
            "pages": registry.start_pages.your_pages(self.profile),
            "skills": registry.skills.list(),
            "automations": [
                a for a in registry.automations.list() if a["profile"] == self.profile
            ],
        }
        for section in packs.SECTIONS:
            for entry in found[section]:
                key = entry["name"] if section == "pages" else entry["id"]
                text = _label(section, entry)
                if packs.private_text(section, entry):
                    text += " (includes text a step types: check it first)"
                self.items.addItem(_checkable(text, (section, key), False))
        layout.addWidget(self.items, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        save = QPushButton("Save pack…")
        save.setProperty("primary", True)
        save.clicked.connect(self.save)
        add = QPushButton("Add a pack…")
        add.clicked.connect(self.add)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (save, add, close):
            row.addWidget(button)
        layout.addLayout(row)

    def pack(self):
        chosen = {section: [] for section in packs.SECTIONS}
        for section, key in _ticked(self.items):
            chosen[section].append(key)
        return packs.build(
            self.registry,
            self.profile,
            pages=chosen["pages"],
            skills=chosen["skills"],
            automations=chosen["automations"],
            name=self.name.text(),
        )

    def save_to(self, path):
        pack = self.pack()
        if not any(pack[s] for s in packs.SECTIONS):
            self.status.setText("Tick what to put in the pack first.")
            return False
        try:
            Path(path).write_text(packs.dumps(pack), encoding="utf-8")
        except OSError as error:
            self.status.setText(f"Could not save the pack: {error}")
            return False
        self.status.setText(f"Saved {Path(path).name}.")
        return True

    def save(self):
        if not any(True for _ in _ticked(self.items)):
            self.status.setText("Tick what to put in the pack first.")
            return
        name = "-".join(self.name.text().lower().split()) or "steed"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save pack", str(Path.home() / f"{name}.steedpack"), FILTER
        )
        if path:
            self.save_to(path)

    def add(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add a pack", "", FILTER)
        if path:
            self.open_pack(path)

    def open_pack(self, path):
        try:
            found = packs.read(Path(path).read_bytes()[: packs.MAX_BYTES + 1])
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return None
        review = PackReviewDialog(self.window, found, self)
        self.window._present_dialog(review, modal=False)
        return review


class PackReviewDialog(HtmlSettingsDialog):
    """Every item in a pack, each shown in full; adds only what is ticked."""

    def __init__(self, window, found, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.found = found
        self.result_text = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Add a pack")
        self.resize(680, 600)
        layout = QVBoxLayout(self)
        heading = QLabel(found["name"] or "A pack")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setProperty("role", "quote")
        layout.addWidget(heading)
        self.items = QListWidget()
        for section in packs.SECTIONS:
            for index, entry in enumerate(found[section]):
                self.items.addItem(
                    _checkable(_label(section, entry), (section, index), True)
                )
        self.items.currentItemChanged.connect(lambda *_: self._show())
        layout.addWidget(self.items, 1)
        layout.addWidget(QLabel("What it is"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail, 1)
        notes = [
            "Look at each item before adding it. Automations arrive paused; "
            "resume them in Agents → Automations. What a page's scripts may do "
            "follows Access → Local pages."
        ]
        if found["problems"]:
            notes.append("Left out: " + " ".join(found["problems"][:5]))
        note = QLabel("\n".join(notes))
        note.setWordWrap(True)
        note.setProperty("role", "warning" if found["problems"] else "note")
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        add = QPushButton("Add ticked")
        add.setProperty("primary", True)
        add.clicked.connect(self.install)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(add)
        row.addWidget(cancel)
        layout.addLayout(row)
        if self.items.count():
            self.items.setCurrentRow(0)

    def _show(self):
        item = self.items.currentItem()
        if item is None:
            self.detail.setPlainText("")
            return
        section, index = item.data(Qt.ItemDataRole.UserRole)
        self.detail.setPlainText(_detail(section, self.found[section][index]))

    def install(self):
        chosen = {section: [] for section in packs.SECTIONS}
        for section, index in _ticked(self.items):
            chosen[section].append(index)
        if not any(chosen.values()):
            self.status.setText("Tick what to add.")
            return None
        manager = self.window.profile_manager
        try:
            outcome = packs.install(
                manager.registry, manager.current_profile_name, self.found, chosen
            )
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return None
        self.result_text = "Added: " + (", ".join(outcome["added"]) or "nothing")
        if outcome["skipped"]:
            self.result_text += ". Skipped: " + " ".join(outcome["skipped"])
        self.window.status_bar.showMessage(self.result_text, 8000)
        self.accept()
        return outcome
