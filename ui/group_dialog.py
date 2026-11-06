"""Group operations use stable IDs, independent of visual tab indices."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog
from ui.prompts import InputDialog as QInputDialog


class GroupDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Tab Groups")
        self.resize(500, 350)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        for label, operation in (
            ("Create", "create"),
            ("Rename", "rename"),
            ("Ungroup / Delete", "delete"),
            ("Collapse / Expand", "toggle"),
            ("Move to Start", "move"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, op=operation: self.perform(op))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.refresh()
        manager = self.window.tab_manager
        manager.group_data_updated.connect(self.refresh)
        self.finished.connect(
            lambda _: manager.group_data_updated.disconnect(self.refresh)
        )

    def refresh(self):
        self.list.clear()
        for group in self.window.tab_manager.groups.values():
            notes = [group.color.title()] if group.color else []
            if group.collapsed:
                notes.append("collapsed")
            item = QListWidgetItem(
                group.name + (f" ({', '.join(notes)})" if notes else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, group.group_id)
            self.list.addItem(item)

    def perform(self, operation):
        item = self.list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        if operation != "create" and key is None:
            return
        if operation in ("create", "rename"):
            previous = self.window.tab_manager.groups[key].name if key else ""
            name, ok = QInputDialog.getText(self, "Tab Group", "Name:", text=previous)
            if not ok or not name.strip():
                return
            if key is not None and key not in self.window.tab_manager.groups:
                self.refresh()
                return
            if operation == "create":
                self.window.tab_manager.ensure_group(name.strip())
            else:
                self.window.rename_group(key, name.strip())
        elif operation == "delete":
            self.window.delete_group(key)
        elif operation == "move":
            self.window.move_group(key)
        else:
            self.window.toggle_group(key)
        self.window._sync_tab_group_labels()
        self.refresh()
