"""General → Search Keywords…: built-in and your own address bar keywords."""

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import search_keywords
from ui.html_dialog import HtmlSettingsDialog
from ui.prompts import MessageBox


class SearchKeywordsDialog(HtmlSettingsDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Search Keywords")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Type a keyword, a space and your search in the address bar: "
            "“w steed” searches Wikipedia. Your own keywords add to these or "
            "replace a built-in one with the same keyword."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Keyword", "Searches", "Address"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.keyword = QLineEdit(placeholderText="Keyword, e.g. mdn")
        self.name = QLineEdit(placeholderText="Name, e.g. MDN")
        self.url = QLineEdit(
            placeholderText="https://developer.mozilla.org/search?q={q}"
        )
        add = QPushButton("Add")
        add.clicked.connect(self.add)
        for widget, stretch in ((self.keyword, 1), (self.name, 1), (self.url, 3)):
            row.addWidget(widget, stretch)
        row.addWidget(add)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        self.remove_button = QPushButton("Remove Your Keyword")
        self.remove_button.clicked.connect(self.remove)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def custom(self):
        value = self.config.get("search_keywords", {})
        return dict(value) if isinstance(value, dict) else {}

    def refresh(self):
        custom = self.custom()
        found = search_keywords.keywords(self.config)
        self.table.setRowCount(len(found))
        for row, keyword in enumerate(sorted(found)):
            name, url = found[keyword]
            label = name + ("" if keyword in custom else " (built in)")
            for column, value in enumerate((keyword, label, url)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def add(self):
        try:
            keyword, name, url = search_keywords.clean(
                self.keyword.text(), self.name.text(), self.url.text()
            )
        except ValueError as error:
            MessageBox.warning(self, "Search Keywords", str(error))
            return
        custom = self.custom()
        if keyword not in custom and len(custom) >= search_keywords.MAX_CUSTOM:
            MessageBox.warning(self, "Search Keywords", "Remove a keyword first.")
            return
        custom[keyword] = [name, url]
        self.config.set("search_keywords", custom)
        for field in (self.keyword, self.name, self.url):
            field.clear()
        self.refresh()

    def remove(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        custom = self.custom()
        if item is None or item.text() not in custom:
            MessageBox.information(
                self, "Search Keywords", "Select one of your own keywords to remove."
            )
            return
        del custom[item.text()]
        self.config.set("search_keywords", custom)
        self.refresh()
