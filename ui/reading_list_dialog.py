"""Tabs → Reading List…: pages saved to read later."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog


class ReadingListDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Reading List")
        self.resize(680, 460)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Pages saved with Save to Reading List. Read Offline shows the article "
            "text kept when you saved it, even without a connection."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _: self.open_online())
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.buttons = {}
        for label, action in (
            ("Open", self.open_online),
            ("Read Offline", self.read_offline),
            ("Mark Read", self.toggle_read),
            ("Remove", self.remove),
            ("Export as E-book…", self.export_book),
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(action)
            row.addWidget(button)
            self.buttons[label] = button
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.list.currentRowChanged.connect(self.update_buttons)
        self.refresh()

    def store(self):
        return self.window._reading_list()

    def refresh(self):
        self.entries = self.store().entries()
        self.list.clear()
        for entry in self.entries:
            mark = "✓ " if entry["read"] else "• "
            offline = "" if entry["article"] is None else "  (offline)"
            item = QListWidgetItem(mark + entry["title"] + offline)
            item.setToolTip(entry["url"])
            self.list.addItem(item)
        if not self.entries:
            self.list.addItem("Nothing saved yet: View → Save to Reading List.")
            self.list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
        else:
            self.list.setCurrentRow(0)
        self.update_buttons()

    def selected(self):
        row = self.list.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def update_buttons(self, *_):
        entry = self.selected()
        for label, button in self.buttons.items():
            button.setEnabled(entry is not None or label == "Export as E-book…")
        if entry is not None:
            self.buttons["Read Offline"].setEnabled(entry["article"] is not None)
            self.buttons["Mark Read"].setText(
                "Mark Unread" if entry["read"] else "Mark Read"
            )

    def open_online(self):
        entry = self.selected()
        if entry is not None:
            self.store().set_read(entry["id"], True)
            self.window._new_tab(url=entry["url"])
            self.refresh()

    def read_offline(self):
        entry = self.selected()
        if entry is not None and entry["article"] is not None:
            self.store().set_read(entry["id"], True)
            self.window.read_offline(entry)
            self.refresh()

    def toggle_read(self):
        entry = self.selected()
        if entry is not None:
            self.store().set_read(entry["id"], not entry["read"])
            self.refresh()

    def export_book(self, path=None):
        """Every article kept for offline reading, as one EPUB file."""
        import epub

        if path is None:
            from PyQt6.QtWidgets import QFileDialog

            path, _ = QFileDialog.getSaveFileName(
                self, "Export as E-book", "Reading list.epub", "E-books (*.epub)"
            )
            if not path:
                return None
            if not path.lower().endswith(".epub"):
                path += ".epub"
        try:
            count = epub.write(self.store().entries(), path)
        except (OSError, ValueError) as error:
            from ui.prompts import MessageBox

            MessageBox.warning(self, "Export as E-book", str(error))
            return None
        self.window.status_bar.showMessage(f"Saved {count} article(s) to {path}.", 6000)
        return path

    def remove(self):
        entry = self.selected()
        if entry is not None:
            self.store().remove(entry["id"])
            self.refresh()
