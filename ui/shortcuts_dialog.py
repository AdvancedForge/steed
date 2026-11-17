"""General → Keyboard Shortcuts…: every command and its shortcut, yours to change."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from browser import shortcuts
from ui.html_dialog import HtmlSettingsDialog


class CaptureDialog(QDialog):
    """Press the keys for a command (a plain window: it needs the keys)."""

    def __init__(self, name, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Change shortcut")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Press the new shortcut for “{name}”."))
        self.edit = QKeySequenceEdit(QKeySequence(current))
        self.edit.setMaximumSequenceLength(1)
        layout.addWidget(self.edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.edit.setFocus()

    def sequence(self):
        return self.edit.keySequence().toString(
            QKeySequence.SequenceFormat.PortableText
        )


class ShortcutsDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Keyboard Shortcuts")
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a command or shortcut")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Command", "Shortcut"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _: self.change_selected())
        layout.addWidget(self.table)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        for text, callback in (
            ("Change…", self.change_selected),
            ("No shortcut", self.clear_selected),
            ("Steed's own", self.reset_selected),
            ("Reset all", self.reset_all),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self, *_):
        words = self.search.text().lower().split()
        rows = [
            (name, action.shortcut().toString(QKeySequence.SequenceFormat.NativeText))
            for name, action in shortcuts.commands(self.window)
        ]
        rows = [r for r in rows if all(w in (r[0] + " " + r[1]).lower() for w in words)]
        selected = self.selected()
        self.table.setRowCount(len(rows))
        for index, (name, keys) in enumerate(rows):
            for column, value in enumerate((name, keys)):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, name)
                self.table.setItem(index, column, item)
            if name == selected:
                self.table.selectRow(index)

    def selected(self):
        item = (
            self.table.item(self.table.currentRow(), 0)
            if self.table.rowCount()
            else None
        )
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _set(self, name, sequence):
        taken = shortcuts.change(self.window, name, sequence)
        self.status.setText(
            f"“{taken}” no longer has a shortcut; it went to “{name}”." if taken else ""
        )
        self.refresh()

    def change_selected(self):
        name = self.selected()
        if name is None:
            return
        current = (
            dict(shortcuts.commands(self.window))[name]
            .shortcut()
            .toString(QKeySequence.SequenceFormat.PortableText)
        )
        capture = CaptureDialog(name, current, self.window)
        if capture.exec() == QDialog.DialogCode.Accepted:
            self._set(name, capture.sequence())

    def clear_selected(self):
        name = self.selected()
        if name is not None:
            self._set(name, "")

    def reset_selected(self):
        name = self.selected()
        if name is not None:
            shortcuts.reset(self.window, name)
            self.status.setText("")
            self.refresh()

    def reset_all(self):
        shortcuts.reset(self.window)
        self.status.setText("Every command has Steed's own shortcut again.")
        self.refresh()
