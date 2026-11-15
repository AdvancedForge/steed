"""Tabs → Site Apps…: the sites installed as apps; open or remove them."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from site_apps import SiteApps
from ui.html_dialog import HtmlSettingsDialog


class SiteAppsDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.apps = SiteApps(window.config.config_dir)
        self.setWindowTitle("Site Apps")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Sites you installed as apps open in their own window and have their "
            "own entry in your applications menu. Install one from Tabs → Install "
            "This Site as an App…"
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "note")
        layout.addWidget(intro)
        self.list = QListWidget()
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(self.open_selected)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (self.open_button, remove, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for app in self.apps.list():
            item = QListWidgetItem(f"{app['name']} ({app['url']})")
            item.setData(Qt.ItemDataRole.UserRole, app["id"])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def selected(self):
        item = self.list.currentItem()
        found = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((a for a in self.apps.list() if a["id"] == found), None)

    def open_selected(self):
        app = self.selected()
        if app is not None:
            self.window.open_site_app(app["url"])

    def remove_selected(self):
        app = self.selected()
        if app is not None:
            self.apps.remove(app["id"])
            self.refresh()
