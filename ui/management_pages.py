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
    "watches": "Watches",
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


class WatchesDialog(HtmlSettingsDialog):
    """Pages Steed keeps an eye on, what you asked about, what last changed."""

    def __init__(self, service, parent, open_url):
        super().__init__(parent)
        import time

        from services.watches import INTERVAL_LABELS

        self.time, self.labels = time, INTERVAL_LABELS
        self.service, self.open_url = service, open_url
        self.setWindowTitle("Watches")
        body = QVBoxLayout(self)
        body.addWidget(
            QLabel(
                "Right-click a page (or a part you selected) and choose Watch "
                "with Agent. Steed looks in the background with your sign-ins."
            )
        )
        self.items = QTableWidget(0, 5)
        self.items.setHorizontalHeaderLabels(
            ["Page", "Tell me", "Then", "How often", "Last news"]
        )
        self.items.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.items.itemDoubleClicked.connect(lambda _: self.open_selected())
        body.addWidget(self.items)
        row = QHBoxLayout()
        for text, callback in (
            ("Open", self.open_selected),
            ("Look now", self.check_selected),
            ("Pause or resume", self.pause_selected),
            ("Remove", self.remove_selected),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        body.addLayout(row)
        service.changed.connect(self.refresh)
        self.finished.connect(lambda _: service.changed.disconnect(self.refresh))
        self.refresh()

    def refresh(self):
        selected = self.selected()
        self.items.setRowCount(0)
        for watch in self.service.list():
            row = self.items.rowCount()
            self.items.insertRow(row)
            when = (
                self.time.strftime(
                    "%b %d %H:%M", self.time.localtime(watch["last_change"])
                )
                if watch["last_change"]
                else "Nothing yet"
            )
            news = watch["last_summary"] or when
            if watch.get("signed_out"):
                news = "Sign in again (the page asks you to)"
            if watch["paused"]:
                news = "Paused" + (
                    " (the page could not be read)"
                    if watch.get("failures", 0) >= 5
                    else ""
                )
            for column, value in enumerate(
                (
                    watch["title"],
                    watch["instruction"] or "Any change",
                    self.then_label(watch),
                    self.labels.get(watch["every"], watch["every"]),
                    news,
                )
            ):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, watch["id"])
                self.items.setItem(row, column, item)
            if watch["id"] == selected:
                self.items.selectRow(row)

    @staticmethod
    def then_label(watch):
        then = watch.get("then", "")
        if not then:
            return "Just tell me"
        return then + (" (at once)" if watch.get("then_mode") == "now" else "")

    def selected(self):
        item = self.items.item(self.items.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _watch(self):
        found = self.selected()
        return next((w for w in self.service.list() if w["id"] == found), None)

    def open_selected(self):
        if watch := self._watch():
            self.open_url(watch["url"])

    def check_selected(self):
        if watch := self._watch():
            self.service.check_now(watch["id"])

    def pause_selected(self):
        if watch := self._watch():
            self.service.pause(watch["id"], not watch["paused"])

    def remove_selected(self):
        if watch := self._watch():
            self.service.remove(watch["id"])


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
    if name == "watches":
        return WatchesDialog(registry.watches, window, open_url)
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
            page_for=window._download_page,
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

        profile = window.profile_manager.current_profile_name
        return SitePermissionsDialog(
            registry.site_permissions,
            window,
            site_settings=registry.site_settings(profile),
            profile=profile,
        )
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
