"""Progress and explicit controls for shared downloads."""

from pathlib import Path

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog
from ui.prompts import InputDialog as QInputDialog
from ui.prompts import MessageBox as QMessageBox


def reveal_file(path):
    """Show a file selected in the file manager, or open its folder."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

    path = Path(path)
    if path.exists():
        # Nautilus, Nemo, Dolphin, Thunar and Caja answer this desktop standard.
        manager = QDBusInterface(
            "org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            QDBusConnection.sessionBus(),
        )
        if manager.isValid():
            reply = manager.call(
                "ShowItems", [QUrl.fromLocalFile(str(path)).toString()], ""
            )
            if reply.type() != QDBusMessage.MessageType.ErrorMessage:
                return True
    folder = path.parent if path.parent.exists() else None
    return bool(folder) and QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def choose_download_destination(suggested, parent=None):
    """UI adapter; managers can instead receive a noninteractive destination."""
    directory = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DownloadLocation
    )
    directory = Path(directory) if directory else Path.home() / "Downloads"
    path, _ = QFileDialog.getSaveFileName(
        parent, "Save Download", str(directory / Path(suggested).name)
    )
    return path


class DownloadDialog(HtmlSettingsDialog):
    def __init__(self, manager, parent=None, *, tasks=None, page_for=None):
        super().__init__(parent)
        self.manager = manager
        # Returns a page of a profile, for trying a download again.
        self.page_for = page_for
        # Returns the task service that owns the agent file library, if any.
        self.tasks = tasks
        self.setWindowTitle("Downloads")
        self.resize(850, 420)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filenames, profiles and destinations")
        self.search.setAccessibleName("Search downloads")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 5)
        self.table.setProperty(
            "empty", "No downloads yet. Files you download appear here."
        )
        self.table.setHorizontalHeaderLabels(
            ["Name / Profile", "Progress", "Status", "Destination", "Error"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for label, action in (
            ("Pause", "pause"),
            ("Resume", "resume"),
            ("Cancel", "cancel"),
            ("Retry", "retry"),
            ("Open", "open"),
            ("Show Folder", "reveal"),
            ("Copy Path", "copy_path"),
            *((("Add to Library…", "library"),) if tasks is not None else ()),
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, a=action: self.perform(a))
            buttons.addWidget(button)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        manager.changed.connect(self.refresh)
        self.finished.connect(lambda: manager.changed.disconnect(self.refresh))
        self.search.textChanged.connect(self.refresh)
        self.refresh()

    def selected_key(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        selected = self.selected_key()
        query = self.search.text().casefold().strip()
        records = [
            (key, record)
            for key, record in reversed(list(self.manager.records.items()))
            if not query
            or query in "\n".join((record.name, record.profile, record.path)).casefold()
        ]
        # Drop old row indices before restoring selection by download identity.
        self.table.setRowCount(0)
        self.table.setRowCount(len(records))
        for row, (key, record) in enumerate(records):
            progress = (
                f"{record.received:,} / {record.total:,} bytes"
                if record.total > 0
                else f"{record.received:,} bytes (size unknown)"
            )
            values = (
                f"{record.name} / {record.profile}",
                progress,
                "Paused" if record.paused else record.state.removeprefix("Download"),
                record.path,
                record.error,
            )
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
        if action == "copy_path":
            QApplication.clipboard().setText(record.path)
            return
        if action == "library":
            self.add_to_library(record)
            return
        if action == "retry":
            page = self.page_for(record.profile) if self.page_for else None
            if not self.manager.can_retry(key) or page is None:
                QMessageBox.information(
                    self,
                    "Download",
                    "Only failed or cancelled web downloads from a profile that is "
                    "open can be tried again.",
                )
                return
            self.manager.retry(key, page)
            return
        if action in ("open", "reveal"):
            if not Path(record.path).exists() or (
                action == "open" and record.state != "DownloadCompleted"
            ):
                QMessageBox.warning(
                    self, "Download", "The completed file is not available."
                )
                return
            if action == "reveal":
                reveal_file(record.path)
            else:
                QDesktopServices.openUrl(self.manager.file_url(record))
        else:
            self.manager.control(key, action)

    def add_to_library(self, record):
        """Copy a finished download into a chosen agent file library folder."""
        from services.file_library import read_import

        path = Path(record.path)
        private = record.profile == "search" or any(
            self.manager.records.get(key) is record for key in self.manager.private
        )
        if private:
            QMessageBox.warning(
                self,
                "Add to Library",
                "Downloads from incognito windows are not added to the library.",
            )
            return
        if record.state != "DownloadCompleted" or not path.is_file():
            QMessageBox.warning(
                self, "Add to Library", "The completed file is not available."
            )
            return
        library = self.tasks().library
        folders = library.folders()
        if not folders:
            QMessageBox.information(
                self,
                "Add to Library",
                "Create a folder in the agent File Library first.",
            )
            return
        labels = [
            folder["name"] + ("" if folder["enabled"] else " (agent access off)")
            for folder in folders
        ]
        label, accepted = QInputDialog.getItem(
            self, "Add to Library", "Copy into library folder:", labels, 0, False
        )
        if not accepted or label not in labels:
            return
        folder = folders[labels.index(label)]
        try:
            ((name, data),) = read_import([path])
            name = library.available_path(folder["id"], name)
            library.import_files(folder["id"], [(name, data)])
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Add to Library", str(error))
            return
        QMessageBox.information(
            self,
            "Add to Library",
            f"Copied to {folder['name']}/{name}."
            + (
                ""
                if folder["enabled"]
                else " Agents can use it once that folder's access is turned on."
            ),
        )
