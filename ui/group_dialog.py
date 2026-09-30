"""Group operations use stable IDs, independent of visual tab indices."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class GroupDialog(QDialog):
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

    def refresh(self):
        self.list.clear()
        for group in self.window.tab_manager.groups.values():
            item = QListWidgetItem(
                group.name + (" (collapsed)" if group.collapsed else "")
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
