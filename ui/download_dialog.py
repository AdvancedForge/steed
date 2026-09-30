"""Progress and explicit controls for shared downloads."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout


class DownloadDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Downloads")
        self.resize(850, 420)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name / Profile", "Progress", "Status", "Destination", "Error"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for label, action in (("Pause", "pause"), ("Resume", "resume"), ("Cancel", "cancel"), ("Open", "open"), ("Show Folder", "reveal")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, a=action: self.perform(a))
            buttons.addWidget(button)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        manager.changed.connect(self.refresh)
        self.finished.connect(lambda: manager.changed.disconnect(self.refresh))
        self.refresh()

    def selected_key(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        selected = self.selected_key()
        self.table.setRowCount(len(self.manager.records))
        for row, (key, record) in enumerate(reversed(list(self.manager.records.items()))):
            progress = f"{record.received:,} / {record.total:,} bytes" if record.total > 0 else f"{record.received:,} bytes (size unknown)"
            values = (f"{record.name} / {record.profile}", progress, "Paused" if record.paused else record.state.removeprefix("Download"), record.path, record.error)
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, key)
            if key == selected:
                self.table.selectRow(row)
        self.table.resizeColumnsToContents()

    def perform(self, action):
        key = self.selected_key()
        record = self.manager.records.get(key)
        if record is None:
            return
        if action in ("open", "reveal"):
            if not Path(record.path).exists() or (action == "open" and record.state != "DownloadCompleted"):
                QMessageBox.warning(self, "Download", "The completed file is not available.")
                return
            QDesktopServices.openUrl(self.manager.file_url(record, action == "reveal"))
        else:
            self.manager.control(key, action)
