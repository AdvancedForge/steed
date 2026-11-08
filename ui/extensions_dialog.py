"""General → Extensions → Manage Extensions…: add, switch on or off, remove."""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from extensions import STORE, icon_from
from ui.html_dialog import HtmlSettingsDialog


class ExtensionsDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.extensions = window.profile_manager.registry.extensions
        self.profile = window.profile_manager.current_profile_name
        self.setWindowTitle("Extensions")
        self.resize(700, 500)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Chrome extensions (Manifest V3) for this profile. Qt WebEngine "
            "supports only part of Chrome's extension features, so some do not "
            "work. Extensions from the Chrome Web Store sync with your other "
            "computers when sync is on."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.about = QLabel()
        self.about.setWordWrap(True)
        self.about.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.about)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        grid = QGridLayout()
        self.buttons = {}
        for index, (label, action) in enumerate(
            (
                ("Browse Chrome Web Store", self.browse),
                ("Add from Store Link…", self.add_link),
                ("Add Folder…", self.add_folder),
                ("Add File…", self.add_file),
                ("Switch Off", self.toggle),
                ("Pin to Toolbar", self.toggle_pin),
                ("Site Access…", self.site_access),
                ("Options", self.options),
                ("Errors…", self.show_errors),
                ("Remove", self.remove),
                ("Check for Updates", self.check_updates),
                ("Update…", self.update),
                ("Close", self.accept),
            )
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(action)
            grid.addWidget(button, index // 4, index % 4)
            self.buttons[label] = button
        layout.addLayout(grid)
        self.list.currentRowChanged.connect(self.update_selection)
        self.extensions.changed.connect(self._changed)
        self.extensions.failed.connect(self._failed)
        self.extensions.updates_checked.connect(self._checked)
        self.refresh()

    def _checked(self, profile, summary):
        if profile == self.profile:
            self.status.setText(summary)

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
        self.details = [self.extensions.details(e) for e in self.entries]
        self.list.clear()
        for entry, details in zip(self.entries, self.details, strict=True):
            state = "on" if entry["enabled"] else "off"
            item = QListWidgetItem(f"{entry['name']}  ({state})")
            if details is not None:
                item.setIcon(icon_from(details["icon"]))
            item.setToolTip(entry["description"] or entry["name"])
            self.list.addItem(item)
        if not self.entries:
            self.list.addItem("No extensions yet: browse the Chrome Web Store.")
            self.list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
        else:
            ids = [e["id"] for e in self.entries]
            row = ids.index(selected["id"]) if selected and selected["id"] in ids else 0
            self.list.setCurrentRow(row)
        self.update_selection()

    def selected(self):
        row = self.list.currentRow()
        entries = getattr(self, "entries", [])
        return entries[row] if 0 <= row < len(entries) else None

    def update_selection(self, *_):
        entry = self.selected()
        for label in ("Switch Off", "Remove", "Site Access…"):
            self.buttons[label].setEnabled(entry is not None)
        self.buttons["Switch Off"].setText(
            "Switch On" if entry and not entry["enabled"] else "Switch Off"
        )
        self.buttons["Pin to Toolbar"].setEnabled(bool(entry and entry["popup"]))
        self.buttons["Pin to Toolbar"].setText(
            "Unpin" if entry and entry["pinned"] else "Pin to Toolbar"
        )
        self.buttons["Update…"].setVisible(bool(entry and entry["update"]))
        self.buttons["Options"].setEnabled(bool(entry and entry.get("options")))
        errors = (
            self.extensions.bridges.recent_errors(self.profile, entry["id"])
            if entry
            else []
        )
        self.buttons["Errors…"].setEnabled(bool(errors))
        self.buttons["Errors…"].setText(
            f"Errors ({len(errors)})…" if errors else "Errors…"
        )
        if entry is None:
            self.about.clear()
            return
        details = self.details[self.entries.index(entry)] or {}
        source = "the Chrome Web Store" if entry["store"] else "a file on this computer"
        lines = [
            f"<b>{_html(entry['name'])}</b> {_html(details.get('version', ''))}"
            f" — from {source}"
        ]
        if entry["error"]:
            lines.append(f"Problem: {_html(entry['error'])}")
        if entry["sites"] is not None:
            lines.append(
                "Only on: " + (_html(", ".join(entry["sites"])) or "no sites") + "."
            )
        if entry["update"]:
            lines.append(
                f"Version {_html(entry['update'])} is waiting: it asks for more "
                "than this one, so it needs your yes (Update…)."
            )
        asks = details.get("permissions") or ["Nothing beyond its own pages"]
        lines.append("It can: " + "; ".join(_html(line) for line in asks) + ".")
        if details.get("unsupported"):
            lines.append(
                "Steed's engine lacks Chrome features it uses: "
                + ", ".join(details["unsupported"])
                + "; parts of it may not work."
            )
        self.about.setText("<br>".join(lines))

    def browse(self):
        self.window._new_tab(url=STORE)
        self.accept()

    def add_link(self):
        self.window._add_extension_link()

    def install(self, path):
        """Ask, then add; problems show in the status line."""
        if self.window.extension_installer.add_path(path):
            self.status.setText("Adding…")
        else:
            self.status.setText(self.window.status_bar.currentMessage())

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

    def toggle_pin(self):
        entry = self.selected()
        if entry is not None:
            self.extensions.set_pinned(self.profile, entry["id"], not entry["pinned"])

    def site_access(self):
        from ui.extension_sites_dialog import ExtensionSitesDialog

        entry = self.selected()
        if entry is None:
            return
        dialog = ExtensionSitesDialog(entry, self.window, self)
        if dialog.exec():
            self.extensions.set_sites(self.profile, entry["id"], dialog.sites())
            self.status.setText("Changing where it runs…")

    def check_updates(self):
        self.status.setText("Checking the Chrome Web Store…")
        self.extensions.check_updates(self.profile)

    def update(self):
        from extensions import inspect
        from ui.extension_install import confirm

        entry = self.selected()
        waiting = entry and self.extensions.waiting_update(self.profile, entry["store"])
        if not waiting:
            return
        if confirm(self.window, inspect(waiting)):
            self.extensions.apply_update(self.profile, entry["store"])
            self.status.setText("Updating…")

    def options(self):
        entry = self.selected()
        if entry and entry.get("options"):
            self.window._new_tab(url=entry["options"])

    def show_errors(self):
        from ui.prompts import MessageBox

        entry = self.selected()
        if entry is None:
            return
        errors = self.extensions.bridges.recent_errors(self.profile, entry["id"])
        lines = [
            time.strftime("%H:%M:%S", time.localtime(e["time"]))
            + f"  {e['message']}"
            + (f"\n    {e['source']}:{e['line']}" if e["source"] else "")
            for e in reversed(errors)
        ]
        box = MessageBox(self)
        box.setWindowTitle(f"Errors: {entry['name']}")
        box.setText(f"The latest {len(errors)} error(s), newest first.")
        box.setDetailedText("\n\n".join(lines))
        clear = box.addButton("Clear", MessageBox.ButtonRole.ResetRole)
        box.addButton(MessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is clear:
            self.extensions.bridges.clear_errors(self.profile, entry["id"])
            self.update_selection()

    def remove(self):
        entry = self.selected()
        if entry is not None:
            self.extensions.remove(self.profile, entry["id"])


def _html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
