"""General → Extensions → Manage Extensions…: add, switch on or off, remove."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog


class ExtensionsDialog(HtmlSettingsDialog):
    def __init__(self, extensions, profile, parent=None):
        super().__init__(parent)
        self.extensions, self.profile = extensions, profile
        self.setWindowTitle("Extensions")
        self.resize(640, 440)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Chrome extensions (Manifest V3) for this profile, from an unpacked "
            "folder, a .zip or a .crx file. Qt WebEngine supports only part of "
            "Chrome's extension features, so some extensions do not work. An "
            "extension can read and change the pages it asks for: add only ones "
            "you trust."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.buttons = {}
        for label, action in (
            ("Add Folder…", self.add_folder),
            ("Add File…", self.add_file),
            ("Switch Off", self.toggle),
            ("Remove", self.remove),
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
        extensions.changed.connect(self._changed)
        extensions.failed.connect(self._failed)
        self.refresh()

    def _changed(self, profile):
        if profile == self.profile:
            self.status.clear()
            self.refresh()

    def _failed(self, profile, message):
        if profile == self.profile:
            self.status.setText(f"Not added: {message}")

    def refresh(self):
        selected = self.selected()
        self.entries = self.extensions.installed(self.profile)
        self.list.clear()
        for entry in self.entries:
            state = "on" if entry["enabled"] else "off"
            item = QListWidgetItem(f"{entry['name']}  ({state})")
            item.setToolTip(entry["description"] or entry["name"])
            self.list.addItem(item)
        if not self.entries:
            self.list.addItem("No extensions yet: Add Folder… or Add File….")
            self.list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
        else:
            ids = [e["id"] for e in self.entries]
            row = ids.index(selected["id"]) if selected and selected["id"] in ids else 0
            self.list.setCurrentRow(row)
        self.update_buttons()

    def selected(self):
        row = self.list.currentRow()
        entries = getattr(self, "entries", [])
        return entries[row] if 0 <= row < len(entries) else None

    def update_buttons(self, *_):
        entry = self.selected()
        for label in ("Switch Off", "Remove"):
            self.buttons[label].setEnabled(entry is not None)
        self.buttons["Switch Off"].setText(
            "Switch On" if entry and not entry["enabled"] else "Switch Off"
        )

    def install(self, path):
        try:
            self.extensions.install(self.profile, path)
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
        else:
            self.status.setText("Adding…")

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Extension Folder")
        if folder:
            self.install(folder)

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Extension File", "", "Extensions (*.crx *.zip)"
        )
        if path:
            self.install(path)

    def toggle(self):
        entry = self.selected()
        if entry is not None:
            self.extensions.set_enabled(self.profile, entry["id"], not entry["enabled"])

    def remove(self):
        entry = self.selected()
        if entry is not None:
            self.extensions.remove(self.profile, entry["id"])
