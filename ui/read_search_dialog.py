"""History → Search What You've Read… (Ctrl+Shift+F): pages you read, found by
any words in them; open one, or hand the matches to your agent."""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog

AGENT_PAGES = 5
AGENT_TEXT = 3000


def when(seconds):
    days = int((time.time() - seconds) // 86400)
    return "today" if days < 1 else "yesterday" if days < 2 else f"{days} days ago"


def agent_message(question, results):
    """The draft for your agent: your question and what the pages said."""
    parts = [question or "About these pages I read:"]
    for number, result in enumerate(results[:AGENT_PAGES], start=1):
        parts.append(
            f"### {number}. {result['title']}\n{result['url']}\n\n"
            + result["text"][:AGENT_TEXT]
        )
    return "\n\n".join(parts) + "\n\n"


class ReadSearchDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.results = []
        self.setWindowTitle("Search What You've Read")
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Words from a page you read")
        self.query.textChanged.connect(self.search)
        layout.addWidget(self.query)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "note")
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Page", "Where the words are", "Read"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _: self.open_selected())
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(self.open_selected)
        self.ask_button = QPushButton("Ask Agent About These")
        self.ask_button.clicked.connect(self.ask_agent)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (self.open_button, self.ask_button, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.search()

    @property
    def memory(self):
        history = self.window.history
        return history.memory if history is not None else None

    def search(self, *_):
        memory = self.memory
        if not self.window.config.get("remember_pages", False):
            self.status.setText(
                "Steed is not remembering pages. Turn on Settings → General → "
                "Remember what I read; pages you read from then on can be found here."
            )
        elif self.window.profile_manager.is_search_profile():
            self.status.setText("Incognito pages are never remembered.")
        else:
            count = memory.count() if memory is not None else 0
            self.status.setText(f"{count} page{'s' * (count != 1)} remembered.")
        self.results = memory.search(self.query.text()) if memory is not None else []
        self.table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            for column, value in enumerate(
                (result["title"], result["snippet"], when(result["visited"]))
            ):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setToolTip(result["url"])
                self.table.setItem(row, column, item)
        if self.results:
            self.table.selectRow(0)
        self.ask_button.setEnabled(bool(self.results))
        self.open_button.setEnabled(bool(self.results))

    def selected(self):
        row = self.table.currentRow()
        return self.results[row] if 0 <= row < len(self.results) else None

    def open_selected(self):
        result = self.selected()
        if result is not None:
            self.window._new_tab(url=result["url"])

    def ask_agent(self):
        if self.results:
            self.window.agent_message(agent_message("", self.results))
            self.accept()
