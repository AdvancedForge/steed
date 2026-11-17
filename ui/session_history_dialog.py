"""Session History: earlier sessions and closed windows of this profile."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from browser.session_history import describe
from ui.html_dialog import HtmlSettingsDialog


class SessionHistoryDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Session History")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Your windows, saved every 10 minutes and whenever you close one. Open brings an entry back; its tabs load as you select them."
        )
        notice.setWordWrap(True)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(notice)
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setAccessibleName("Saved sessions and closed windows")
        self.preview = QListWidget()
        self.preview.setAccessibleName("Tabs in the selected entry")
        row.addWidget(self.list, 3)
        row.addWidget(self.preview, 4)
        layout.addLayout(row, 1)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(self.open_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (self.open_button, self.delete_button):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.list.currentRowChanged.connect(self.show_preview)
        self.list.itemActivated.connect(lambda _: self.open_selected())
        self.refresh()

    def refresh(self):
        self.entries = self.window._session_history().entries()
        self.list.clear()
        for entry in self.entries:
            item = QListWidgetItem(describe(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.list.addItem(item)
        if not self.entries:
            self.list.addItem("Nothing recorded yet.")
            self.list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
        else:
            self.list.setCurrentRow(0)
        self.show_preview(self.list.currentRow())

    def selected(self):
        row = self.list.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def show_preview(self, _row=None):
        self.preview.clear()
        entry = self.selected()
        self.open_button.setEnabled(entry is not None)
        self.delete_button.setEnabled(entry is not None)
        if entry is None:
            return
        for number, window in enumerate(entry["windows"], 1):
            if len(entry["windows"]) > 1:
                heading = QListWidgetItem(f"Window {number}")
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                self.preview.addItem(heading)
            for state in window:
                item = QListWidgetItem(state.title or state.url)
                item.setToolTip(state.url)
                self.preview.addItem(item)

    def open_selected(self):
        entry = self.selected()
        if entry is not None:
            self.window.restore_session_entry(entry["id"])
            self.refresh()

    def delete_selected(self):
        entry = self.selected()
        if entry is not None:
            self.window._session_history().remove(entry["id"])
            self.refresh()
