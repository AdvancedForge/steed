"""General → Import from Another Browser…: bookmarks, history and open tabs."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

import browser_import
from ui.html_dialog import HtmlSettingsDialog


class ImportDialog(HtmlSettingsDialog):
    def __init__(self, window, sources=None):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Import from Another Browser")
        self.resize(560, 320)
        self.sources = browser_import.discover() if sources is None else sources
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Steed reads a copy of the other browser's files, so it can stay open "
            "and nothing in it changes. Imports go into this profile ("
            + window.profile_manager.current_profile_name
            + ")."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.source = QComboBox()
        for source in self.sources:
            self.source.addItem(f"{source['browser']} — {source['name']}")
        layout.addWidget(self.source)
        self.bookmarks = QCheckBox("Bookmarks (duplicates are skipped)")
        self.history = QCheckBox("History (the latest 1,000 pages)")
        self.tabs = QCheckBox("Open tabs, as a saved group (Firefox)")
        for box in (self.bookmarks, self.history, self.tabs):
            box.setChecked(True)
            layout.addWidget(box)
        self.status = QLabel(
            ""
            if self.sources
            else "No Chrome, Chromium, Brave, Edge, Vivaldi or "
            "Firefox profiles were found."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.import_button = QPushButton("Import")
        self.import_button.setEnabled(bool(self.sources))
        self.import_button.clicked.connect(self.run)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(self.import_button)
        row.addWidget(close)
        layout.addLayout(row)
        self.source.currentIndexChanged.connect(self._source_changed)
        self._source_changed()

    def _source_changed(self, *_):
        source = self.selected()
        firefox = source is not None and source["kind"] == "firefox"
        self.tabs.setEnabled(firefox)
        if not firefox:
            self.tabs.setChecked(False)

    def selected(self):
        index = self.source.currentIndex()
        return self.sources[index] if 0 <= index < len(self.sources) else None

    def run(self):
        source = self.selected()
        if source is None:
            return None
        summary = self.window.import_from_browser(
            source,
            bookmarks=self.bookmarks.isChecked(),
            history=self.history.isChecked(),
            tabs=self.tabs.isChecked(),
        )
        self.status.setText(summary)
        return summary
