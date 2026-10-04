"""Small profile-scoped bookmark editor and manager."""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class BookmarkDialog(QDialog):
    def __init__(self, pages, profile, parent=None, url="", title=""):
        super().__init__(parent)
        self.pages, self.profile = pages, profile
        self.setWindowTitle("Save Bookmark")
        self.resize(520, 160)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title, self.url = QLineEdit(title), QLineEdit(url)
        self.url.setPlaceholderText("https://example.org")
        form.addRow("Name:", self.title)
        form.addRow("Address:", self.url)
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
            self.pages.add_bookmark(self.profile, self.url.text(), self.title.text())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not save bookmark", str(error))
            return
        self.accept()


class BookmarksDialog(QDialog):
    def __init__(self, pages, profile, parent=None):
        super().__init__(parent)
        self.pages, self.profile = pages, profile
        self.setWindowTitle("Manage Bookmarks")
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(lambda _: self.edit())
        layout.addWidget(self.items)
        row = QHBoxLayout()
        for label, callback in (
            ("Add…", self.add),
            ("Edit name…", self.edit),
            ("Remove", self.remove),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.bookmarks = self.pages.state(self.profile)["bookmarks"]
        self.items.clear()
        for item in self.bookmarks:
            self.items.addItem(item["title"] + "\n" + item["url"])

    def add(self):
        BookmarkDialog(self.pages, self.profile, self).exec()
        self.refresh()

    def edit(self):
        index = self.items.currentRow()
        if 0 <= index < len(self.bookmarks):
            item = self.bookmarks[index]
            dialog = BookmarkDialog(
                self.pages, self.profile, self, item["url"], item["title"]
            )
            dialog.url.setReadOnly(True)
            dialog.exec()
            self.refresh()

    def remove(self):
        index = self.items.currentRow()
        if 0 <= index < len(self.bookmarks):
            self.pages.remove_bookmark(self.profile, self.bookmarks[index]["id"])
            self.refresh()
