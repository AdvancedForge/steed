"""Internal management pages backed by the existing owner-side controllers."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog

PAGES = {
    "cookies": "Cookies",
    "downloads": "Downloads",
    "bookmarks": "Bookmarks",
    "history": "History",
    "library": "File library",
    "connections": "App connections",
    "tasks": "Agent tasks & permissions",
    "panels": "Data panels",
    "passwords": "Passwords",
    "layout": "Browser layout",
    "newtab-settings": "Customize start pages",
    "tab-groups": "Tab groups",
    "permissions": "Website permissions",
    "agent-setup": "Agent setup",
    "adblock": "Ad blocking",
    "services": "Add a service",
}

SHARED_PAGES = frozenset(
    (
        "downloads",
        "library",
        "connections",
        "tasks",
        "layout",
        "permissions",
        "agent-setup",
        "adblock",
        "services",
    )
)


class HistoryDialog(HtmlSettingsDialog):
    def __init__(self, history, parent, open_url):
        super().__init__(parent)
        self.history, self.open_url = history, open_url
        self.setWindowTitle("Browsing history")
        body = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search page names and addresses")
        self.search.setAccessibleName("Search history")
        self.search.textChanged.connect(self.refresh)
        body.addWidget(self.search)
        body.addWidget(
            QLabel("Most recent visits first. History belongs to this profile.")
        )
        self.items = QTableWidget(0, 2)
        self.items.setHorizontalHeaderLabels(["Page", "Address"])
        self.items.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.items.itemDoubleClicked.connect(lambda _: self.open_selected())
        body.addWidget(self.items)
        row = QHBoxLayout()
        for text, callback in (
            ("Open selected", self.open_selected),
            ("Copy address", self.copy_selected),
            ("Clear history…", parent._clear_browsing_history),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        body.addLayout(row)
        history.changed.connect(self.refresh)
        self.finished.connect(lambda _: history.changed.disconnect(self.refresh))
        self.refresh()

    def refresh(self):
        selected = self.selected()
        query = self.search.text().casefold().strip()
        entries = [
            entry
            for entry in self.history.entries()
            if query in (entry.title + " " + entry.url).casefold()
        ]
        self.items.setRowCount(0)
        for entry in entries:
            row = self.items.rowCount()
            self.items.insertRow(row)
            for column, value in enumerate((entry.title, entry.url)):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, entry.url)
                self.items.setItem(row, column, item)
            if entry.url == selected:
                self.items.selectRow(row)

    def selected(self):
        item = self.items.item(self.items.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def open_selected(self):
        if url := self.selected():
            self.open_url(url)

    def copy_selected(self):
        if url := self.selected():
            QApplication.clipboard().setText(url)


def controller(window, name, source_tab=None):
    """Build a fresh controller; opening a page never enables agent access."""
    registry = window.profile_manager.registry
    profile = window.profile_manager.current_profile_name

    def open_url(url):
        if window._closing or window.profile_manager.current_profile_name != profile:
            raise ValueError("The display profile changed. Reopen this page.")
        window._new_tab(url=url)

    if profile == "search" and name in ("library", "newtab-settings", "panels"):
        result = HtmlSettingsDialog(window)
        result.setWindowTitle(PAGES[name])
        QVBoxLayout(result).addWidget(
            QLabel("Switch to a regular profile to use this page.")
        )
        return result
    if name == "panels":
        from ui.data_panels import DataPanelController

        return DataPanelController(registry.tasks, window)
    if name == "history":
        return HistoryDialog(window.history, window, open_url)
    if name == "cookies":
        from ui.cookie_dialog import CookieDialog

        return CookieDialog(
            window.cookie_manager,
            window,
            window.profile_manager.protect_cookie_deletion,
        )
    if name == "downloads":
        from ui.download_dialog import DownloadDialog

        # The agent library is unavailable from incognito windows.
        private = window.profile_manager.is_search_profile()
        return DownloadDialog(
            window.download_manager,
            window,
            tasks=None if private else lambda: registry.tasks,
        )
    if name == "bookmarks":
        from ui.bookmarks_dialog import BookmarksDialog

        return BookmarksDialog(
            registry.start_pages,
            profile,
            window,
            open_url=open_url,
            close_on_open=False,
        )
    if name == "library":
        from ui.file_library import FileLibraryDialog

        result = FileLibraryDialog(registry.tasks, window)
        window.file_library_dialog = result
        result.finished.connect(lambda _: setattr(window, "file_library_dialog", None))
        return result
    if name == "connections":
        from ui.app_connections import AppConnectionsDialog

        return AppConnectionsDialog(registry.app_connections, window)
    if name == "tasks":
        from ui.task_dialog import TaskDialog

        return TaskDialog(registry.tasks, window)
    if name == "passwords":
        from ui.password_dialog import PasswordDialog

        page = (
            source_tab.page()
            if source_tab is not None
            else window.tabs[window.current_tab_index].page()
        )
        return PasswordDialog(registry.passwords, profile, page, window)
    if name == "layout":
        return window._layout_controller()
    if name == "newtab-settings":
        from ui.start_page_dialog import StartPageDialog

        return StartPageDialog(registry.start_pages, profile, window)
    if name == "tab-groups":
        from ui.group_dialog import GroupDialog

        return GroupDialog(window)
    if name == "permissions":
        from ui.site_permissions import SitePermissionsDialog

        return SitePermissionsDialog(registry.site_permissions, window)
    if name == "agent-setup":
        from ui.agent_setup import AgentSetupDialog

        result = AgentSetupDialog(registry, window)
        result.enable_requested.connect(lambda: window._set_agent_access(True))
        window.agent_setup_dialog = result
        result.finished.connect(lambda _: setattr(window, "agent_setup_dialog", None))
        return result
    if name == "adblock":
        from ui.adblock_dialog import AdBlockDialog

        return AdBlockDialog(window.adblock, window)
    if name == "services":
        from ui.bundled_recipes import BundledRecipesDialog

        return BundledRecipesDialog(registry.app_connections, window)
    raise ValueError("Unknown management page")
