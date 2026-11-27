"""Profile-scoped bookmark search, folders, editing and portable HTML files."""

from pathlib import Path

from PyQt6.QtCore import QIODevice, QSaveFile, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from browser.bookmark_format import MAX_BOOKMARK_HTML_BYTES
from ui.html_dialog import HtmlSettingsDialog, needs_rows, needs_selection
from ui.prompts import MessageBox as QMessageBox


def profile_identity(pages, profile):
    return None if profile == "search" else pages.profile_key(profile, create=True)


def check_profile(pages, profile, identity):
    if profile != "search" and pages.profile_key(profile) != identity:
        raise ValueError(
            "The profile changed or was recreated. Reopen Bookmarks in the current profile."
        )


class BookmarkDialog(HtmlSettingsDialog):
    def __init__(
        self, pages, profile, parent=None, url="", title="", *, identity=None, folder=""
    ):
        super().__init__(parent)
        self.pages, self.profile, self.identity = pages, profile, identity
        self.profile_identity = profile_identity(pages, profile)
        self.setWindowTitle("Edit Bookmark" if identity else "Save Bookmark")
        self.resize(540, 220)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title, self.url = QLineEdit(title), QLineEdit(url)
        self.title.setMaxLength(200)
        self.url.setPlaceholderText("https://example.org")
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.addItem("")
        self.folder.addItems(
            sorted(
                {b.get("folder", "") for b in pages.state(profile)["bookmarks"]} - {""}
            )
        )
        self.folder.setCurrentText(folder)
        self.folder.lineEdit().setMaxLength(200)
        self.folder.lineEdit().setPlaceholderText("Unfiled, or type a folder name")
        form.addRow("Name", self.title)
        form.addRow("Address", self.url)
        form.addRow("Folder", self.folder)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            check_profile(self.pages, self.profile, self.profile_identity)
            if self.identity:
                self.pages.update_bookmark(
                    self.profile,
                    self.identity,
                    self.url.text(),
                    self.title.text(),
                    self.folder.currentText(),
                )
            else:
                self.pages.add_bookmark(
                    self.profile,
                    self.url.text(),
                    self.title.text(),
                    folder=self.folder.currentText(),
                )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not save bookmark", str(error))
            return
        self.accept()


class BookmarksDialog(HtmlSettingsDialog):
    def __init__(
        self, pages, profile, parent=None, *, open_url=None, close_on_open=True
    ):
        super().__init__(parent)
        self.pages, self.profile = pages, profile
        self.open_url = open_url
        self.close_on_open = close_on_open
        self.profile_identity = profile_identity(pages, profile)
        self.setWindowTitle("Bookmarks")
        self.resize(680, 480)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, addresses and folders")
        self.search.setAccessibleName("Search bookmarks")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(
            "Press Enter to open the first matching bookmark in a new tab."
        )
        self.folder = QComboBox()
        self.folder.setAccessibleName("Bookmark folder")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.folder)
        layout.addLayout(filters)
        self.items = QListWidget()
        self.items.setProperty(
            "empty", "No bookmarks yet. Press Ctrl+D on a page to keep it here."
        )
        self.items.itemDoubleClicked.connect(
            lambda _: self.open_selected() if self.open_url else self.edit()
        )
        self.items.setToolTip(
            "Use the arrow keys to select a bookmark, then Enter to open. "
            "Right-click one for more."
        )
        self.items.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.items.customContextMenuRequested.connect(lambda _: self.row_menu())
        for key in ("Return", "Enter"):
            shortcut = QShortcut(QKeySequence(key), self.items)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.setEnabled(self.open_url is not None)
            shortcut.activated.connect(self.open_selected)
        layout.addWidget(self.items)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        chosen, whole = [], []
        for label, callback in (
            ("Open", self.open_selected),
            ("Add…", self.add),
            ("Edit…", self.edit),
            ("Remove", self.remove),
            ("Import HTML…", self.import_html),
            ("Export HTML…", self.export_html),
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            if label in ("Open", "Edit…", "Remove"):
                chosen.append(button)
            elif label == "Export HTML…":
                whole.append(button)
            row.addWidget(button)
        layout.addLayout(row)
        open_button, *edits = chosen
        needs_selection(self.items, edits)
        needs_selection(
            self.items, [open_button], also=lambda: self.open_url is not None
        )
        needs_rows(self.items, whole)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.filter_items)
        self.search.returnPressed.connect(self.open_first_match)
        self.folder.currentIndexChanged.connect(self.filter_items)
        pages.changed.connect(self._changed)
        self.refresh()

    def _changed(self, profile):
        if profile == self.profile:
            self.refresh()

    def refresh(self):
        try:
            check_profile(self.pages, self.profile, self.profile_identity)
            self.bookmarks = self.pages.state(self.profile)["bookmarks"]
        except (OSError, ValueError):
            self.bookmarks = []
            self.items.clear()
            self.status.setText(
                "Profile unavailable; reopen Bookmarks in the current profile."
            )
            return
        selected = self.folder.currentData()
        self.folder.blockSignals(True)
        self.folder.clear()
        self.folder.addItem("All folders", None)
        self.folder.addItem("Unfiled", "")
        for folder in sorted({b.get("folder", "") for b in self.bookmarks} - {""}):
            self.folder.addItem(folder, folder)
        self.folder.setCurrentIndex(max(0, self.folder.findData(selected)))
        self.folder.blockSignals(False)
        self.filter_items()

    def filter_items(self):
        previous = self.items.currentItem()
        identity = previous.data(Qt.ItemDataRole.UserRole) if previous else None
        self.items.clear()
        query, folder = self.search.text().casefold().strip(), self.folder.currentData()
        for bookmark in self.bookmarks:
            label = bookmark["title"] + "\n" + bookmark["url"]
            if bookmark.get("folder"):
                label += "\nFolder: " + bookmark["folder"]
            if (folder is not None and bookmark.get("folder", "") != folder) or (
                query and query not in label.casefold()
            ):
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, bookmark["id"])
            self.items.addItem(item)
            if bookmark["id"] == identity:
                self.items.setCurrentItem(item)
        count = f"{self.items.count()} of {len(self.bookmarks)} bookmarks"
        self.status.setText(
            count if self.profile == "global" else f"{count} in this profile"
        )

    def row_menu(self):
        """Right-click on a bookmark: open, edit, copy or remove it."""
        bookmark = self.selected()
        if bookmark is None:
            return
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication, QMenu

        menu = QMenu(self)
        if self.open_url is not None:
            menu.addAction("Open", self.open_selected)
        menu.addAction("Edit…", self.edit)
        menu.addAction(
            "Copy Address", lambda: QApplication.clipboard().setText(bookmark["url"])
        )
        menu.addSeparator()
        menu.addAction("Remove", self.remove)
        menu.popup(QCursor.pos())
        self.menu = menu

    def selected(self):
        item = self.items.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((b for b in self.bookmarks if b["id"] == identity), None)

    def add(self):
        folder = self.folder.currentData() or ""
        BookmarkDialog(self.pages, self.profile, self, folder=folder).exec()
        self.refresh()

    def edit(self):
        item = self.selected()
        if item:
            BookmarkDialog(
                self.pages,
                self.profile,
                self,
                item["url"],
                item["title"],
                identity=item["id"],
                folder=item.get("folder", ""),
            ).exec()
            self.refresh()

    def open_first_match(self):
        if self.items.count():
            self.items.setCurrentRow(0)
            self.open_selected()

    def open_selected(self):
        item = self.selected()
        if item is None or self.open_url is None:
            return
        try:
            check_profile(self.pages, self.profile, self.profile_identity)
            self.open_url(item["url"])
        except (OSError, ValueError):
            self.status.setText(
                "Could not open this bookmark; reopen Bookmarks in the current profile."
            )
            return
        if self.close_on_open:
            self.accept()

    def remove(self):
        item = self.selected()
        if item:
            try:
                check_profile(self.pages, self.profile, self.profile_identity)
                self.pages.remove_bookmark(self.profile, item["id"])
            except (OSError, ValueError) as error:
                self.status.setText("Could not remove bookmark: " + str(error))

    def import_html(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import bookmarks", "", "Bookmark HTML (*.html *.htm)"
        )
        if not filename:
            return
        try:
            check_profile(self.pages, self.profile, self.profile_identity)
            with Path(filename).open("rb") as source:
                result = self.pages.import_bookmarks(
                    self.profile, source.read(MAX_BOOKMARK_HTML_BYTES + 1)
                )
        except (OSError, ValueError) as error:
            self.status.setText(
                "Import failed; existing bookmarks were kept. " + str(error)
            )
            return
        self.status.setText(
            f"Imported {result['added']} bookmarks; kept {result['duplicates']} existing/duplicate links; skipped {result['skipped']} invalid links."
        )

    def export_html(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export all profile bookmarks",
            "steed-bookmarks.html",
            "Bookmark HTML (*.html)",
        )
        if not filename:
            return
        output = QSaveFile(filename)
        try:
            check_profile(self.pages, self.profile, self.profile_identity)
            content = self.pages.export_bookmarks(self.profile)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(content) != len(content) or not output.commit():
                raise OSError(output.errorString())
        except (OSError, ValueError) as error:
            output.cancelWriting()
            self.status.setText("Export failed: " + str(error))
            return
        self.status.setText(f"Exported all {len(self.bookmarks)} profile bookmarks.")
