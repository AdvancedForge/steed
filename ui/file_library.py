"""Persistent folder management; enabling access is a deliberate user switch."""

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from services.file_library import FILE_BYTES, read_import
from services.task_downloads import DownloadDestination

PAGE_ROWS = 200


class FileLibraryDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Agent File Library")
        self.resize(850, 620)
        self._loading = False
        self._job = None
        self._page_context = None
        self._page_after = ""
        self._page_history = []
        self._page_last = ""
        self._executor = ThreadPoolExecutor(max_workers=1)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Add files once and reuse them across tasks. Enabling a folder gives "
            "all connected agents full read/create/edit/remove access to everything "
            "inside it, including future files and subfolders. Access persists across "
            "tasks and restarts until disabled. This library is shared across regular "
            "browser profiles; websites get no access. Imports are copies."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        roots = QHBoxLayout()
        self.folders = QComboBox()
        self.folders.setAccessibleName("Persistent library folder")
        self.folders.currentIndexChanged.connect(self.refresh)
        roots.addWidget(self.folders, 1)
        new = QPushButton("New library folder…")
        new.clicked.connect(self.new_folder)
        roots.addWidget(new)
        layout.addLayout(roots)
        self.enabled = QCheckBox("Full file access for all connected agents")
        self.enabled.toggled.connect(self.enable)
        layout.addWidget(self.enabled)
        self.trash = QCheckBox("Show Trash")
        self.trash.toggled.connect(self.refresh)
        layout.addWidget(self.trash)
        self.filter = QLineEdit()
        self.filter.setAccessibleName("Filter library entries")
        self.filter.setPlaceholderText("Filter by name or path (empty shows all)…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaxLength(512)
        self.filter.textChanged.connect(self.refresh)
        layout.addWidget(self.filter)
        self.files = QTreeWidget()
        self.files.setHeaderLabels(["Name", "Type", "Bytes", "Revision"])
        self.files.setColumnWidth(0, 400)
        self.files.setAccessibleName("Library files and subfolders")
        layout.addWidget(self.files, 1)
        paging = QHBoxLayout()
        self.previous_page = QPushButton("Previous page")
        self.previous_page.clicked.connect(self.show_previous_page)
        paging.addWidget(self.previous_page)
        self.page_label = QLabel()
        paging.addWidget(self.page_label, 1)
        self.next_page = QPushButton("Next page")
        self.next_page.clicked.connect(self.show_next_page)
        paging.addWidget(self.next_page)
        layout.addLayout(paging)
        buttons = QHBoxLayout()
        self.import_buttons = []
        for label, slot in (
            ("Add files…", self.add_files),
            ("Add folder…", self.add_folder),
            ("New subfolder…", self.new_subfolder),
            ("Export file…", self.export),
            ("Remove", self.remove),
            ("Restore", self.restore),
            ("Versions…", self.versions),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
            self.import_buttons.append(button)
        layout.addLayout(buttons)
        clear = QPushButton("Clear recovery copies…")
        clear.clicked.connect(self.clear_recovery)
        layout.addWidget(clear)
        self.status = QLabel(
            "Files: up to 64 MiB each; 512 MiB total including recovery copies. "
            "Worker inputs still use the task workspace's limits."
        )
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll_import)
        service.changed.connect(self.refresh)
        if parent is not None:
            parent.profile_manager.profile_changed.connect(self.profile_changed)
        self._detached = False
        self.refresh()

    def profile_changed(self, name):
        self.clear_filter()
        self.files.clear()
        self.folders.blockSignals(True)
        self.folders.clear()
        self.reject()

    def done(self, result):
        if not self._detached:
            self._detached = True
            self.clear_filter()
            self.service.changed.disconnect(self.refresh)
            if self.parent() is not None:
                self.parent().profile_manager.profile_changed.disconnect(
                    self.profile_changed
                )
            self.timer.stop()
            if self._job:
                self._job[1].cancel()
                self._job = None
            self._executor.shutdown(wait=False, cancel_futures=True)
        super().done(result)

    def folder_id(self):
        return self.folders.currentData()

    def selected(self):
        item = self.files.currentItem()
        if item is None:
            raise ValueError("Select a library file or subfolder")
        return item.data(0, Qt.ItemDataRole.UserRole)

    def perform(self, operation):
        try:
            self.service._usable()
            result = operation()
            self.service.changed.emit()
            return result
        except (ValueError, OSError, sqlite3.Error, RuntimeError) as error:
            self.status.setText(str(error))
            return None

    def clear_filter(self):
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.blockSignals(False)
        self._page_context = None
        self._page_after = ""
        self._page_history.clear()

    def show_next_page(self):
        if self.next_page.isEnabled():
            self._page_history.append(self._page_after)
            self._page_after = self._page_last
            self.refresh()

    def show_previous_page(self):
        if self._page_history:
            self._page_after = self._page_history.pop()
            self.refresh()

    def refresh(self):
        if self._loading or self.service._closed or getattr(self, "_detached", False):
            return
        self._loading = True
        try:
            chosen = self.folder_id()
            folders = self.service.library.folders()
            self.folders.blockSignals(True)
            self.folders.clear()
            for folder in folders:
                self.folders.addItem(folder["name"], folder["id"])
            self.folders.setCurrentIndex(max(0, self.folders.findData(chosen)))
            self.folders.blockSignals(False)
            folder_id = self.folder_id()
            self.enabled.blockSignals(True)
            self.enabled.setChecked(
                bool(folder_id and self.service.library.folder(folder_id)["enabled"])
            )
            self.enabled.setEnabled(bool(folder_id))
            self.enabled.blockSignals(False)
            selection = self.files.currentItem()
            old_path = (
                selection.data(0, Qt.ItemDataRole.UserRole)["path"]
                if selection
                else None
            )
            self.files.clear()
            query = self.filter.text()
            context = (folder_id, self.trash.isChecked(), query)
            if context != self._page_context:
                self._page_context = context
                self._page_after = ""
                self._page_history.clear()
            self.next_page.setEnabled(False)
            self.previous_page.setEnabled(bool(self._page_history))
            self.page_label.setText(f"Page {len(self._page_history) + 1}")
            if folder_id:
                # Literal filtering and keyset paging bound each Qt repaint.
                rows = self.service.library.db.execute(
                    "SELECT path,kind,revision,sha256,deleted,length(data) AS byte_count FROM entries WHERE folder=? AND deleted=? AND instr(path,?)>0 AND path>? ORDER BY path LIMIT ?",
                    (
                        folder_id,
                        int(self.trash.isChecked()),
                        query,
                        self._page_after,
                        PAGE_ROWS + 1,
                    ),
                ).fetchall()
                nodes = {}
                for row in rows[:PAGE_ROWS]:
                    item = QTreeWidgetItem(
                        [
                            row["path"].rsplit("/", 1)[-1],
                            row["kind"],
                            str(row["byte_count"]),
                            str(row["revision"]),
                        ]
                    )
                    item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
                    parent = row["path"].rsplit("/", 1)[0] if "/" in row["path"] else ""
                    if parent in nodes:
                        nodes[parent].addChild(item)
                    else:
                        # Filtering/paging can omit parents; retain the full path
                        # so equal leaf names cannot look like the same file.
                        item.setText(0, row["path"])
                        self.files.addTopLevelItem(item)
                    item.setToolTip(0, row["path"])
                    nodes[row["path"]] = item
                    if row["path"] == old_path:
                        self.files.setCurrentItem(item)
                self.files.expandAll()
                self.next_page.setEnabled(len(rows) > PAGE_ROWS)
                self._page_last = (
                    rows[min(len(rows), PAGE_ROWS) - 1]["path"]
                    if rows
                    else self._page_after
                )
                self.page_label.setText(
                    f"Page {len(self._page_history) + 1} · {min(len(rows), PAGE_ROWS)} entries"
                )
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
            self.status.setText(str(error))
        finally:
            self._loading = False

    def new_folder(self):
        name, ok = QInputDialog.getText(self, "New library folder", "Folder name:")
        if ok:
            folder_id = self.perform(lambda: self.service.library.create_folder(name))
            if folder_id:
                self.folders.setCurrentIndex(self.folders.findData(folder_id))

    def enable(self, enabled):
        if not self._loading and self.folder_id():
            folder_id = self.folder_id()
            self.perform(lambda: self.service.library.enable(folder_id, enabled))
            self.refresh()

    def import_selection(self, paths):
        if self._job or not self.folder_id() or not paths:
            return
        prefix = ""
        if self.files.currentItem() and not self.trash.isChecked():
            selected = self.selected()
            prefix = (
                selected["path"]
                if selected["kind"] == "directory"
                else str(Path(selected["path"]).parent)
            )
            prefix = "" if prefix == "." else prefix + "/"
        self._job = (
            self.folder_id(),
            self._executor.submit(read_import, [Path(p) for p in paths], prefix),
        )
        self.status.setText("Copying selected files into the library…")
        for button in self.import_buttons:
            button.setEnabled(False)
        self.timer.start()

    def poll_import(self):
        if self._job is None or not self._job[1].done():
            return
        folder_id, future = self._job
        self._job = None
        self.timer.stop()
        for button in self.import_buttons:
            button.setEnabled(True)
        result = self.perform(
            lambda: self.service.library.import_files(folder_id, future.result())
        )
        if result is not None:
            self.status.setText(
                "Files added; agents can reuse them when folder access is enabled."
            )

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files to library")
        self.import_selection(paths)

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Add folder to library")
        self.import_selection([path] if path else [])

    def new_subfolder(self):
        path, ok = QInputDialog.getText(
            self, "New subfolder", "Relative path (for example projects/research):"
        )
        if ok:
            self.perform(lambda: self.service.library.mkdir(self.folder_id(), path))

    def remove(self):
        def action():
            row = self.selected()
            return self.service.library.remove(
                self.folder_id(), row["path"], row["revision"]
            )

        self.perform(action)

    def restore(self):
        self.perform(
            lambda: self.service.library.restore(
                self.folder_id(), self.selected()["path"]
            )
        )

    def versions(self):
        try:
            row = self.selected()
            versions = self.service.library.history(self.folder_id(), row["path"])
            choices = [
                f"Revision {v['revision']} — {v['byte_count']} bytes"
                for v in versions
                if v["kind"] == "file"
            ]
            file_versions = [v for v in versions if v["kind"] == "file"]
            if not choices:
                raise ValueError("This file has no saved versions")
            choice, ok = QInputDialog.getItem(
                self,
                "Restore saved version",
                "Choose a revision to restore as the current file:",
                choices,
                0,
                False,
            )
            if ok:
                self.perform(
                    lambda: self.service.library.restore_version(
                        self.folder_id(),
                        row["path"],
                        file_versions[choices.index(choice)]["revision"],
                        0 if row["deleted"] else row["revision"],
                    )
                )
        except (ValueError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def export(self):
        try:
            row = self.selected()
            if row["kind"] != "file" or row["deleted"]:
                raise ValueError("Select an available file to export")
            destination, _ = QFileDialog.getSaveFileName(
                self, "Export library file", Path(row["path"]).name
            )
            if destination:
                data = self.service.library.row(self.folder_id(), row["path"])["data"]
                target = DownloadDestination(destination)
                try:
                    fd = os.open(
                        "payload",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=target.stage_fd,
                    )
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                    target.publish(len(data), FILE_BYTES)
                finally:
                    target.close()
                self.status.setText("Exported a copy.")
        except (ValueError, OSError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def clear_recovery(self):
        if not self.folder_id():
            return
        answer = QMessageBox.question(
            self,
            "Clear recovery copies",
            "Permanently delete this folder's Trash and old file versions? Current files remain. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.perform(lambda: self.service.library.clear_recovery(self.folder_id()))
