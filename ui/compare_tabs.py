"""Compare Tabs with Agent: the text of the tabs you tick, side by side, in a
message to your agent that you finish with your question ("which is best for
me?"). Only the tabs you tick are shared, and only their visible text."""

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

import webengine

PER_TAB = 6000
TOTAL = 24000
MAX_TABS = 6


def comparison(pages):
    """pages: [(title, url, text)] → the message's body."""
    budget = max(1500, min(PER_TAB, TOTAL // max(1, len(pages))))
    parts = ["Compare these pages:"]
    for number, (title, url, text) in enumerate(pages, start=1):
        body = "\n".join(line for line in (text or "").splitlines() if line.strip())
        parts.append(f"### {number}. {title or url}\n{url}\n\n{body[:budget]}")
    return "\n\n".join(parts) + "\n\n"


class CompareDialog(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window_ = window
        self.setWindowTitle("Compare Tabs with Agent")
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Tick up to {MAX_TABS} tabs. Their text goes to your agent in a "
            "message you finish with your question."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list = QListWidget()
        current = window._current_browser_tab()
        for tab in window.tabs:
            if tab.url().scheme() not in ("http", "https"):
                continue
            item = QListWidgetItem(tab.title() or tab.url().toString())
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chosen = tab is current or tab is window.split_tab
            item.setCheckState(
                Qt.CheckState.Checked if chosen else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, tab.tab_id)
            self.list.addItem(item)
        layout.addWidget(self.list)
        self.status = QLabel("" if self.list.count() else "Open some web pages first.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.go = QPushButton("Compare")
        self.go.clicked.connect(self.compare)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.go)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def chosen(self):
        ids = {
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        }
        return [tab for tab in self.window_.tabs if tab.tab_id in ids]

    def compare(self):
        tabs = self.chosen()
        if len(tabs) < 2:
            self.status.setText("Tick at least two tabs.")
            return
        if len(tabs) > MAX_TABS:
            self.status.setText(f"Tick at most {MAX_TABS} tabs.")
            return
        self.go.setEnabled(False)
        self.status.setText("Reading the pages…")
        pages = [None] * len(tabs)
        sent = []

        def finish():
            if sent or sip.isdeleted(self):
                return
            sent.append(True)
            filled = [
                page or (tab.title(), tab.url().toString(), "")
                for page, tab in zip(pages, tabs, strict=True)
            ]
            self.window_.agent_message(comparison(filled))
            self.accept()

        # A frozen or unloaded tab may not answer: it counts as empty.
        QTimer.singleShot(5000, finish)

        def read(index, tab):
            webengine.wake(tab.page())

            def got(text):
                if sip.isdeleted(self):
                    return
                pages[index] = (
                    tab.title(),
                    tab.url().toString(),
                    text if isinstance(text, str) else "",
                )
                if all(page is not None for page in pages):
                    finish()

            webengine.run_script(
                tab.page(),
                "document.body ? document.body.innerText : ''",
                got,
                world=webengine.STEED_WORLD,
            )

        for index, tab in enumerate(tabs):
            read(index, tab)
