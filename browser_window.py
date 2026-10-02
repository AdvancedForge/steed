"""Main browser window."""

import logging
import sqlite3
from uuid import uuid4

from PyQt6 import QtWebEngineCore, sip
from PyQt6.QtCore import QEvent, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWebEngineCore import (
    QWebEngineFullScreenRequest,
    QWebEnginePage,
    QWebEngineSettings,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config import Config
from cookie_manager import CookieManager
from profile_manager import ProfileManager
from services.permission_service import PermissionService
from tab_manager import ClosedTabHistory, TabManager, TabState
from ui.adblock_dialog import AdBlockDialog
from ui.address_bar import AddressBar
from ui.app_connections import AppConnectionsDialog
from ui.cookie_dialog import CookieDialog
from ui.download_dialog import DownloadDialog
from ui.find_bar import FindBar
from ui.group_dialog import GroupDialog
from ui.media_dialog import DesktopMediaDialog
from ui.password_dialog import PasswordDialog
from ui.settings_dialog import SettingsDialog
from ui.tab_bar import TabBar
from ui.task_dialog import TaskDialog
from webengine import (
    SiteCompatibilityInterceptor,
    configure_web_settings,
    google_login_user_agent,
)

logger = logging.getLogger(__name__)

# Qt versions before 6.8 expose permissions through QWebEnginePage.Feature.
QWebEnginePermission = getattr(QtWebEngineCore, "QWebEnginePermission", None)

# Enough to merge a redirect chain or a SPA's boot sequence into one write.
TAB_SAVE_DEBOUNCE_MS = 750


class BrowserPage(QWebEnginePage):
    """Keep page console output out of the application's own logs.

    Page-controlled text must not reach the terminal log: a site could inject
    newlines or escape sequences and forge log entries. Messages are dropped;
    the browser is not a debugging tool for remote pages.
    """

    def __init__(self, profile, parent=None, config=None):
        super().__init__(profile, parent)
        self.site_compatibility = SiteCompatibilityInterceptor(config, self)
        self.setUrlRequestInterceptor(self.site_compatibility)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        compatibility = self.site_compatibility
        if (
            compatibility.enabled
            and is_main_frame
            and navigation_type == QWebEnginePage.NavigationType.NavigationTypeRedirect
            and compatibility.last_main_method == b"GET"
            and (
                google_login_user_agent(url) is not None
                or google_login_user_agent(compatibility.last_main_url) is not None
            )
        ):
            # Qt can retain the previous host's UA or revert to the profile UA
            # on redirects. Start a fresh GET so the target's header is applied.
            # Never replay a POST: doing so would lose its body or duplicate it.
            def follow_redirect():
                if not sip.isdeleted(self):
                    self.setUrl(url)

            QTimer.singleShot(0, follow_redirect)
            return False
        return True


class BrowserTab(QWebEngineView):
    """Represents a single browser tab."""

    def __init__(
        self, profile, parent=None, window=None, initial_qurl: QUrl | None = None
    ):
        super().__init__(parent)
        self.profile = profile
        self.browser_window = window
        self.group = None
        self.group_id = None
        self.tab_id = uuid4().hex
        page = BrowserPage(
            profile, self, config=window.config if window is not None else None
        )
        self.setPage(page)
        settings = self.settings()
        configure_web_settings(settings)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False
        )
        # Local pages must not be able to read remote content. Nothing in the
        # browser needs it: the bundled codec fixture is self-contained.
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        settings.setAttribute(settings.WebAttribute.ScreenCaptureEnabled, True)

        if initial_qurl is not None and initial_qurl.isValid():
            self.setUrl(initial_qurl)

    def createWindow(self, window_type: QWebEnginePage.WebWindowType) -> "BrowserTab":
        return self.browser_window._new_tab(return_tab=True)


class BrowserWindow(QMainWindow):
    """Main browser window."""

    def __init__(self, profile_name: str | None = None):
        super().__init__()
        self.browser_window_id = uuid4().hex
        self.config = Config()
        self.profile_manager = ProfileManager(self.config, profile_name, self)
        self.config = self.profile_manager.config
        self.download_manager = self.profile_manager.registry.downloads
        self.adblock = self.profile_manager.registry.adblock
        self.navigation = self.profile_manager.registry.navigation
        self.tab_manager = None
        self.cookie_manager = None
        self.history = None
        self.tabs: list[BrowserTab] = []
        self.current_tab_index = 0
        self._restoring_tabs = False
        self.closed_tabs = ClosedTabHistory()
        self._child_windows: list[BrowserWindow] = []
        self._password_dialog = None
        self.setup_window()
        self.setup_menus()
        self.setup_ui()
        self.apply_dark_mode()

        self._was_fullscreen = False
        self._pending_desktop_request = None
        self._tab_save_pending = False
        self._saved_window_state = None
        self._saved_geometry = None
        self._saved_vis = {}
        self._closing = False

        # Connect profile signals
        self.profile_manager.profile_changed.connect(self._on_profile_changed)
        self.profile_manager.profiles_updated.connect(self._update_profile_shortcuts)
        # The initial profile was selected before the menu/signals existed.
        self._update_profile_shortcuts()
        # Initialize profile and managers
        self._initialize_profile(self.profile_manager.current_profile_name)
        # Restore or create initial tab
        if self.config.get("restore_tabs_on_startup", True):
            self._restore_tabs()
        else:
            self._new_tab()

    def _initialize_profile(self, profile_name: str):
        if self.history is not None:
            self.history.changed.disconnect(self._refresh_address_history)
        self.history = self.profile_manager.registry.get_history(profile_name)
        self.history.changed.connect(self._refresh_address_history)
        self._refresh_address_history()
        if self.cookie_manager is not None:
            self.cookie_manager.close()
        if self.tab_manager is not None:
            self.tab_manager.deleteLater()
        profile_path = self.profile_manager.get_profile_path(profile_name)
        self.tab_manager = TabManager(profile_path, self)
        profile = self.profile_manager.get_profile(profile_name)

        snapshot = self.profile_manager.registry.snapshots.get(profile_name)
        self.cookie_manager = CookieManager(profile, self, snapshot)
        # Connect signals for tab groups
        if self.tab_manager:
            self.tab_manager.group_data_updated.connect(
                self._refresh_all_tab_bar_groups
            )

    def setup_window(self):
        self.setWindowTitle("Custom Browser")
        self.setGeometry(
            100,
            100,
            self.config.get("window_width", 1200),
            self.config.get("window_height", 800),
        )

    def setup_menus(self):
        menubar = self.menuBar()
        # File menu
        file_menu = menubar.addMenu("File")
        new_tab_action = QAction("New Tab", self)
        new_tab_action.setShortcut(QKeySequence("Ctrl+T"))
        new_tab_action.triggered.connect(lambda: self._new_tab())
        file_menu.addAction(new_tab_action)
        restore_tab_action = QAction("Restore Closed Tab", self)
        restore_tab_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        restore_tab_action.triggered.connect(self._restore_closed_tab)
        self.restore_tab_action = restore_tab_action
        restore_tab_action.setEnabled(False)
        file_menu.addAction(restore_tab_action)
        close_tab_action = QAction("Close Tab", self)
        close_tab_action.setShortcut(QKeySequence("Ctrl+W"))
        close_tab_action.triggered.connect(self._close_current_tab)
        file_menu.addAction(close_tab_action)
        self.reload_action = QAction("Reload", self)
        self.reload_action.setShortcuts([QKeySequence("Ctrl+R"), QKeySequence("F5")])
        self.reload_action.triggered.connect(self._reload)
        file_menu.addAction(self.reload_action)
        focus_address_action = QAction("Go to Address Bar", self)
        focus_address_action.setShortcut(QKeySequence("Ctrl+L"))
        focus_address_action.triggered.connect(lambda: self.address_bar.focus_address())
        file_menu.addAction(focus_address_action)
        clear_history_action = QAction("Clear Browsing History...", self)
        clear_history_action.triggered.connect(self._clear_browsing_history)
        file_menu.addAction(clear_history_action)
        passwords_action = QAction("Passwords...", self)
        passwords_action.triggered.connect(lambda: self._open_passwords())
        file_menu.addAction(passwords_action)
        fill_login_action = QAction("Fill Login", self)
        fill_login_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        fill_login_action.triggered.connect(
            lambda: self._open_passwords(auto_fill=True)
        )
        file_menu.addAction(fill_login_action)
        downloads_action = QAction("Downloads...", self)
        downloads_action.setShortcut(QKeySequence("Ctrl+J"))
        downloads_action.triggered.connect(
            lambda: DownloadDialog(self.download_manager, self).exec()
        )
        file_menu.addAction(downloads_action)
        adblock_action = QAction("Ad Blocking...", self)
        adblock_action.triggered.connect(
            lambda: AdBlockDialog(self.adblock, self).exec()
        )
        file_menu.addAction(adblock_action)
        file_menu.addSeparator()
        settings_action = QAction("Settings...", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        task_action = QAction("Task Permissions...", self)
        task_action.triggered.connect(self._open_tasks)
        file_menu.insertAction(exit_action, task_action)
        apps_action = QAction("App Connections...", self)
        apps_action.triggered.connect(self._open_app_connections)
        file_menu.insertAction(task_action, apps_action)
        self.agent_access_action = QAction("Enable Local Agent Access", self)
        self.agent_access_action.setCheckable(True)
        registry = self.profile_manager.registry
        self.agent_access_action.setChecked(registry.agent_gateway is not None)
        registry.agent_access_changed.connect(self.agent_access_action.setChecked)
        self.agent_access_action.triggered.connect(self._set_agent_access)
        file_menu.insertAction(task_action, self.agent_access_action)
        edit_menu = menubar.addMenu("Edit")
        self.find_action = QAction("Find in Page...", self)
        self.find_action.setShortcut(QKeySequence("Ctrl+F"))
        self.find_action.triggered.connect(self._open_find)
        edit_menu.addAction(self.find_action)
        next_match = QAction("Find Next", self)
        next_match.setShortcuts([QKeySequence("F3"), QKeySequence("Ctrl+G")])
        next_match.triggered.connect(lambda: self.find_bar.find_next())
        edit_menu.addAction(next_match)
        previous_match = QAction("Find Previous", self)
        previous_match.setShortcuts(
            [QKeySequence("Shift+F3"), QKeySequence("Ctrl+Shift+G")]
        )
        previous_match.triggered.connect(lambda: self.find_bar.find_next(True))
        edit_menu.addAction(previous_match)
        view_menu = menubar.addMenu("View")
        self.zoom_in_action = QAction("Zoom In", self)
        self.zoom_in_action.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")]
        )
        self.zoom_in_action.triggered.connect(lambda: self._zoom(1))
        view_menu.addAction(self.zoom_in_action)
        self.zoom_out_action = QAction("Zoom Out", self)
        self.zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        self.zoom_out_action.triggered.connect(lambda: self._zoom(-1))
        view_menu.addAction(self.zoom_out_action)
        self.zoom_reset_action = QAction("Reset Zoom", self)
        self.zoom_reset_action.setShortcut(QKeySequence("Ctrl+0"))
        self.zoom_reset_action.triggered.connect(lambda: self._zoom())
        view_menu.addAction(self.zoom_reset_action)
        # Profile menu
        profile_menu = menubar.addMenu("Profile")
        new_profile_action = QAction("New Profile...", self)
        new_profile_action.triggered.connect(self._create_profile)
        profile_menu.addAction(new_profile_action)
        switch_profile_action = QAction("Switch Profile...", self)
        switch_profile_action.triggered.connect(self._switch_profile)
        profile_menu.addAction(switch_profile_action)
        pop_out_action = QAction("Pop out Profile...", self)
        pop_out_action.triggered.connect(self._pop_out_profile)
        profile_menu.addAction(pop_out_action)
        rename_profile_action = QAction("Rename Profile...", self)
        rename_profile_action.triggered.connect(self._rename_profile)
        profile_menu.addAction(rename_profile_action)
        delete_profile_action = QAction("Delete Profile...", self)
        delete_profile_action.triggered.connect(self._delete_profile)
        profile_menu.addAction(delete_profile_action)
        profile_menu.addSeparator()
        pop_search_action = QAction("Pop Out Search Profile (Incognito)", self)
        pop_search_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        pop_search_action.triggered.connect(
            lambda: self._pop_out_profile_named("search")
        )
        profile_menu.addAction(pop_search_action)
        search_profile_action = QAction("Switch to Search Profile (Incognito)", self)
        search_profile_action.setShortcut(QKeySequence("Ctrl+Shift+0"))
        search_profile_action.triggered.connect(
            lambda: self._switch_to_profile("search")
        )
        profile_menu.addAction(search_profile_action)
        # Quick shortcuts submenu
        self.profile_shortcuts_menu = QMenu("Quick Switch", self)
        profile_menu.addMenu(self.profile_shortcuts_menu)
        # Tab Groups menu
        group_menu = menubar.addMenu("Tab Groups")
        manage_groups_action = QAction("Manage Tab Groups...", self)
        manage_groups_action.triggered.connect(self._manage_tab_groups)
        group_menu.addAction(manage_groups_action)
        # Cookies menu
        cookies_menu = menubar.addMenu("Cookies")
        view_cookies_action = QAction("View Cookies...", self)
        view_cookies_action.triggered.connect(self._view_cookies)
        cookies_menu.addAction(view_cookies_action)
        clear_cookies_action = QAction("Clear All Cookies", self)
        clear_cookies_action.triggered.connect(self._clear_all_cookies)
        cookies_menu.addAction(clear_cookies_action)
        use_global_action = QAction("Use Global Cookies for This Site...", self)
        use_global_action.triggered.connect(self._use_global_cookies)
        cookies_menu.addAction(use_global_action)

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Tab bar
        self.tab_bar = TabBar()
        self.tab_bar.new_tab_requested.connect(self._new_tab)
        self.tab_bar.tab_changed.connect(self._switch_tab)
        self.tab_bar.tab_close_requested.connect(self._close_tab)
        self.tab_bar.tab_moved.connect(self._on_tab_moved)
        self.tab_bar.group_rename_requested.connect(self._handle_group_rename_request)
        self.tab_bar.group_set_requested.connect(self._set_tab_group_existing)
        self.tab_bar.group_clear_requested.connect(self._clear_tab_group)
        self.tab_bar.group_toggle_requested.connect(self._toggle_named_group)
        layout.addWidget(self.tab_bar)
        # Address bar
        self.address_bar = AddressBar()
        self.address_bar.url_changed.connect(self._navigate_to_url)
        self.address_bar.back_requested.connect(self._go_back)
        self.address_bar.forward_requested.connect(self._go_forward)
        self.address_bar.reload_requested.connect(self._reload)
        self.address_bar.home_requested.connect(self._go_home)
        self.address_bar.passwords_requested.connect(lambda: self._open_passwords())
        self.address_bar.reset_zoom_requested.connect(lambda: self._zoom())
        layout.addWidget(self.address_bar)
        self.find_bar = FindBar(self.navigation)
        self.find_bar.closed.connect(self._focus_page)
        layout.addWidget(self.find_bar)
        # Tab container
        self.tab_container = QWidget()
        self.tab_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.tab_layout = QVBoxLayout(self.tab_container)
        self.tab_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_layout.setSpacing(0)
        layout.addWidget(self.tab_container, 1)
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        # Permission and download signals are connected per tab in _new_tab

    def apply_dark_mode(self):
        if self.config.dark_mode:
            self.setStyleSheet("""
                QMainWindow {background-color:#1e1e1e;color:#fff;}
                QWidget {background-color:#1e1e1e;color:#fff;}
                QLineEdit {background:#2d2d2d;color:#fff;border:1px solid #3d3d3d;padding:4px;}
                QPushButton {background:#2d2d2d;color:#fff;border:1px solid #3d3d3d;padding:4px 8px;}
                QPushButton:hover {background:#3d3d3d;}
                QPushButton:pressed {background:#1d1d1d;}
                QTabBar::tab {background:#2d2d2d;color:#fff;padding:8px 16px;border:1px solid #3d3d3d;}
                QTabBar::tab:selected {background:#1e1e1e;}
                QTabBar::tab:hover {background:#3d3d3d;}
                QMenuBar {background:#1e1e1e;color:#fff;}
                QMenu {background:#2d2d2d;color:#fff;}
                QMenu::item:selected {background:#3d3d3d;}
                QStatusBar {background:#1e1e1e;color:#fff;}
            """)
        else:
            self.setStyleSheet("")

    # ---------- Profile handling ----------
    def _on_profile_changed(self, profile_name: str):
        self._update_window_title()
        self._update_profile_shortcuts()

    def _update_window_title(self, url=None):
        profile_name = self.profile_manager.current_profile_name
        label = (
            "Search Profile (Incognito)" if profile_name == "search" else profile_name
        )
        title = f"Custom Browser - {label}"
        if url is None and 0 <= self.current_tab_index < len(self.tabs):
            url = self.tabs[self.current_tab_index].url()
        # KeePassXC Auto-Type can match this host. Page-provided titles must not
        # be able to masquerade as another site's window-title association.
        if url is not None and url.scheme() in ("http", "https") and url.host():
            title += f" - {url.host()}"
        self.setWindowTitle(title)

    def _switch_profile(self):
        profiles = self.profile_manager.list_profiles() + ["search"]
        profile, ok = QInputDialog.getItem(
            self, "Switch Profile", "Select profile:", profiles, 0, False
        )
        if ok:
            self._switch_to_profile(profile)

    def _switch_to_profile(self, profile_name: str):
        try:
            self.profile_manager.registry.check_user_profile(profile_name)
        except ValueError as error:
            QMessageBox.warning(self, "Profile in use", str(error))
            return
        self.find_bar.close_find(clear_query=True)
        self.find_bar.set_page(None)
        # Save current tabs
        if self.tab_manager:
            self._save_current_tabs()
        # Switch profile
        self.profile_manager.switch_to_profile(profile_name)
        # Reinitialize managers for new profile
        self._initialize_profile(profile_name)
        # Clean current tabs UI
        for tab in self.tabs:
            self._dispose_tab(tab)
        self.tabs.clear()
        self.tab_bar.clear_all_tabs()
        # Restore tabs for new profile
        self._restore_tabs()
        self.restore_tab_action.setEnabled(self.closed_tabs.available(profile_name))

    def _create_profile(self):
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if ok and name:
            name = name.strip()
            if self.profile_manager.create_profile(name):
                self._switch_to_profile(name)
            else:
                QMessageBox.warning(
                    self, "New Profile", "Choose a new, non-reserved profile name."
                )

    def _rename_profile(self):
        profiles = self.profile_manager.list_profiles()
        if not profiles:
            return
        profile, ok = QInputDialog.getItem(
            self, "Rename Profile", "Select profile:", profiles, 0, False
        )
        if ok and profile:
            new_name, ok2 = QInputDialog.getText(
                self, "Rename Profile", f"New name for '{profile}':"
            )
            if ok2 and new_name:
                if self.profile_manager.rename_profile(profile, new_name):
                    QMessageBox.information(
                        self, "Success", f"Profile renamed to '{new_name}'"
                    )
                    self._update_profile_shortcuts()
                    self._switch_to_profile(new_name)
                    self._update_profile_shortcuts()
                else:
                    QMessageBox.warning(
                        self,
                        "Error",
                        "Could not rename profile. Switch all windows away from it first, and choose an unused name.",
                    )

    def _delete_profile(self):
        profiles = self.profile_manager.list_profiles()
        current = self.profile_manager.current_profile_name
        # Exclude current profile
        profiles = [p for p in profiles if p != current]
        if not profiles:
            QMessageBox.information(
                self, "No Profiles", "No profiles available to delete."
            )
            return
        profile, ok = QInputDialog.getItem(
            self, "Delete Profile", "Select profile to delete:", profiles, 0, False
        )
        if ok and profile:
            reply = QMessageBox.question(
                self,
                "Delete Profile",
                f"Archive profile '{profile}'? It can be recovered from deleted_profiles in the browser data directory.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if self.profile_manager.delete_profile(profile):
                    QMessageBox.information(
                        self,
                        "Archived",
                        f"Profile '{profile}' moved to deleted_profiles and can be recovered.",
                    )
                    self._update_profile_shortcuts()
                else:
                    QMessageBox.warning(
                        self,
                        "Failed",
                        "Could not archive profile. Switch all windows away from it first.",
                    )

    def _pop_out_profile(self):
        profiles = self.profile_manager.list_profiles()
        profile, ok = QInputDialog.getItem(
            self, "Pop out Profile", "Select profile:", profiles, 0, False
        )
        if ok and profile:
            self._pop_out_profile_named(profile)

    def _pop_out_profile_named(self, profile: str):
        try:
            self.profile_manager.registry.check_user_profile(profile)
        except ValueError as error:
            QMessageBox.warning(self, "Profile in use", str(error))
            return
        win = BrowserWindow(profile_name=profile)
        win.show()
        self._child_windows.append(win)

    def _update_profile_shortcuts(self):
        for action in self.profile_shortcuts_menu.actions():
            self.removeAction(action)
            action.deleteLater()
        self.profile_shortcuts_menu.clear()
        profiles = self.profile_manager.list_profiles()
        for i, name in enumerate(profiles[:9]):
            action = QAction(f"{name} (Ctrl+Shift+{i + 1})", self)
            action.setShortcut(QKeySequence(f"Ctrl+Shift+{i + 1}"))
            action.triggered.connect(lambda checked, n=name: self._switch_to_profile(n))
            self.profile_shortcuts_menu.addAction(action)
            self.addAction(action)

    # ---------- Tab handling ----------
    def _new_tab(
        self,
        url: str | None = None,
        title: str | None = None,
        return_tab: bool = False,
        index: int | None = None,
    ):
        home = self.config.get("home_page", "https://www.google.com")
        if not home:
            home = "https://www.duckduckgo.com"

        if not url:
            url = home

        qurl = self._text_to_qurl(url)

        profile = self.profile_manager.current_profile
        tab = BrowserTab(profile, self.tab_container, self)
        self.tab_layout.addWidget(tab)
        self._connect_page_signals(tab.page())

        tab.urlChanged.connect(lambda u: self._on_tab_url_changed(tab, u))
        tab.titleChanged.connect(lambda t: self._on_tab_title_changed(tab, t))
        tab.loadProgress.connect(lambda p: self._on_load_progress(tab, p))
        tab.loadFinished.connect(lambda ok: self._on_load_finished(tab, ok))
        if hasattr(tab.page(), "zoomFactorChanged"):
            tab.page().zoomFactorChanged.connect(lambda _: self._update_zoom())
        # Determine insertion index
        if index is None:
            index = self.current_tab_index + 1
        index = max(0, min(index, len(self.tabs)))

        self.tabs.insert(index, tab)
        self.tab_bar.insert_tab(index, title or "New Tab")
        self._switch_tab(index)
        self._sync_tab_group_labels()

        # Start loading only after signals and tab bookkeeping are ready.
        self.navigation.navigate(tab, qurl)

        if not self._restoring_tabs:
            self._save_current_tabs()
        if return_tab:
            return tab

    def _close_tab(self, index: int):
        # The final tab is never closed: this window always keeps one page and
        # the application cannot lose its last tab bar.
        if len(self.tabs) <= 1 or not 0 <= index < len(self.tabs):
            return
        active = self.tabs[self.current_tab_index]
        tab = self.tabs.pop(index)
        state = TabState(
            tab.url().toString() or tab.page().requestedUrl().toString(),
            tab.title() or self.tab_bar.tab_bar.tabText(index),
            tab.group,
            tab.tab_id,
            tab.group_id,
        )
        profile_name = self.profile_manager.current_profile_name
        self.closed_tabs.push(profile_name, state, index)
        self.tab_bar.remove_tab(index)
        self._dispose_tab(tab)
        self.current_tab_index = (
            self.tabs.index(active) if active in self.tabs else max(0, index - 1)
        )
        self._switch_tab(self.current_tab_index)
        self._save_current_tabs()
        self._sync_tab_group_labels()
        self.restore_tab_action.setEnabled(self.closed_tabs.available(profile_name))

    def _close_current_tab(self):
        self._close_tab(self.current_tab_index)

    def _restore_closed_tab(self):
        profile = self.profile_manager.current_profile_name
        closed = self.closed_tabs.pop(profile)
        if closed is not None:
            state = closed.state
            tab = self._new_tab(
                url=state.url, title=state.title, index=closed.index, return_tab=True
            )
            tab.group = state.group
            tab.group_id = state.group_id
            tab.tab_id = state.tab_id
            # A collapsed group hides all but its first tab; restoring into one
            # would keep the new tab invisible, so expand it deliberately.
            group = self.tab_manager.groups.get(state.group_id)
            if group is not None and group.collapsed:
                group.collapsed = False
                self.tab_manager.save_groups()
            self._sync_tab_group_labels()
            self._save_current_tabs()
        self.restore_tab_action.setEnabled(self.closed_tabs.available(profile))

    def _on_tab_moved(self, from_idx: int, to_idx: int):
        tab = self.tabs.pop(from_idx)
        self.tabs.insert(to_idx, tab)
        self.current_tab_index = self.tab_bar.get_current_index()
        self._save_current_tabs()
        self._sync_tab_group_labels()

    def _switch_tab(self, index: int):
        if not (0 <= index < len(self.tabs)):
            return
        self.current_tab_index = index
        self.tab_bar.set_current_tab(index)

        # Hide all tabs
        for i, t in enumerate(self.tabs):
            t.setVisible(i == index)
        # Update address bar and navigation buttons
        cur = self.tabs[index]
        self.find_bar.set_page(cur.page())
        self._update_zoom()
        self.address_bar.set_url(cur.url().toString())
        self.address_bar.set_back_enabled(cur.history().canGoBack())
        self.address_bar.set_forward_enabled(cur.history().canGoForward())
        self._update_window_title(cur.url())
        self.status_bar.showMessage(f"Tab {index + 1} of {len(self.tabs)}")

    def _navigate_to_url(self, url_text: str):
        if not self.tabs:
            return
        self._navigate_tab_to_url(self.tabs[self.current_tab_index], url_text)

    def _navigate_tab_to_url(self, tab: BrowserTab, url_text: str):
        self.navigation.navigate(tab, self._text_to_qurl(url_text))

    def _text_to_qurl(self, url_text: str) -> QUrl:
        search = self.config.get("search_engine", "https://www.google.com/search?q=")
        return self.navigation.address_url(url_text, search)

    def _go_back(self):
        if self.tabs:
            self.navigation.action(self.tabs[self.current_tab_index], "back")

    def _go_forward(self):
        if self.tabs:
            self.navigation.action(self.tabs[self.current_tab_index], "forward")

    def _reload(self):
        if self.tabs:
            self.navigation.action(self.tabs[self.current_tab_index], "reload")

    def _open_find(self):
        if not self._closing and self.tabs:
            self.find_bar.set_page(self.tabs[self.current_tab_index].page())
            self.find_bar.open_find()

    def event(self, event):
        if event.type() == QEvent.Type.WindowActivate and hasattr(
            self, "profile_manager"
        ):
            manager = self.profile_manager
            if not manager._closed:
                manager.registry.last_active_owner = manager
        return super().event(event)

    def _set_agent_access(self, enabled):
        try:
            self.profile_manager.registry.set_agent_access(enabled)
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            self.agent_access_action.setChecked(False)
            QMessageBox.warning(
                self, "Agent Access", "Local agent access could not be started."
            )
            return
        self.status_bar.showMessage(
            "Local agent access enabled; tasks still require approval."
            if enabled
            else "Local agent access disabled; connected tasks were cancelled.",
            8000,
        )

    def _open_tasks(self):
        if not self._closing:
            try:
                dialog = TaskDialog(self.profile_manager.registry.tasks, self)
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                logger.error("Could not open task permissions")
                QMessageBox.warning(
                    self,
                    "Task Permissions",
                    "Task permissions could not be opened. Check that the task database is available and supported.",
                )
                return
            dialog.exec()
            dialog.deleteLater()

    def _open_app_connections(self):
        if not self._closing:
            dialog = AppConnectionsDialog(
                self.profile_manager.registry.app_connections, self
            )
            dialog.exec()
            dialog.deleteLater()

    def _focus_page(self):
        if not self._closing and self.tabs:
            self.tabs[self.current_tab_index].setFocus()

    def _zoom(self, direction=0):
        if not self._closing and self.tabs:
            self.navigation.zoom(self.tabs[self.current_tab_index], direction)
            self._update_zoom()

    def _update_zoom(self):
        if not self._closing and self.tabs:
            percent = round(self.tabs[self.current_tab_index].zoomFactor() * 100)
            self.address_bar.zoom_btn.setText(f"{percent}%")
            self.zoom_reset_action.setText(f"Reset Zoom ({percent}%)")

    def _refresh_address_history(self):
        self.address_bar.set_history(self.history.entries())

    def _clear_browsing_history(self):
        if (
            QMessageBox.question(
                self,
                "Clear Browsing History",
                "Clear browsing history and address suggestions for this profile?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.history.clear()

    def _open_passwords(self, auto_fill=False):
        if self._closing or not self.tabs:
            return
        if self._password_dialog is not None:
            self._password_dialog.raise_()
            return
        dialog = PasswordDialog(
            self.profile_manager.registry.passwords,
            self.profile_manager.current_profile_name,
            self.tabs[self.current_tab_index].page(),
            self,
            auto_fill=auto_fill,
        )
        self._password_dialog = dialog
        try:
            dialog.exec()
        finally:
            dialog.reject()
            dialog.deleteLater()
            self._password_dialog = None

    def _go_home(self):
        home = self.config.get("home_page", "https://www.google.com")
        self._navigate_to_url(home)

    def _on_tab_url_changed(self, tab: BrowserTab, url: QUrl):
        if self._closing:
            return
        if not self.tabs or not (0 <= self.current_tab_index < len(self.tabs)):
            return

        if tab == self.tabs[self.current_tab_index]:
            self.address_bar.set_url(url.toString())
            self._update_window_title(url)

    def _on_tab_title_changed(self, tab: BrowserTab, title: str):
        if tab in self.tabs:
            idx = self.tabs.index(tab)
            if len(title) > 30:
                title = title[:27] + "..."
            self.tab_bar.set_tab_title(idx, title)

    def _on_load_progress(self, tab: BrowserTab, progress: int):
        if not self._closing and self.tabs and tab == self.tabs[self.current_tab_index]:
            self.status_bar.showMessage(f"Loading... {progress}%")

    def _on_load_finished(self, tab: BrowserTab, ok: bool):
        if self._closing or tab not in self.tabs:
            return
        if tab == self.tabs[self.current_tab_index]:
            self.status_bar.showMessage("Ready" if ok else "Error loading page")
            self.find_bar.page_loaded()
        if ok:
            self.history.record(tab.url().toString(), tab.title())
            self.adblock.apply_cosmetic(tab)
        self._schedule_tab_save()

    def _schedule_tab_save(self):
        """Coalesce the per-navigation save into one write per burst.

        Every finished page load used to fsync tabs.json; a redirect chain or a
        SPA's initial load chain would write several times for no benefit.
        """
        if self._tab_save_pending or self._closing:
            return
        self._tab_save_pending = True
        QTimer.singleShot(TAB_SAVE_DEBOUNCE_MS, self._flush_tab_save)

    def _flush_tab_save(self):
        self._tab_save_pending = False
        self._save_current_tabs()

    def _save_current_tabs(self):
        if self._restoring_tabs or self.profile_manager.is_search_profile():
            return
        states = []
        if self._normalize_groups():
            self.tab_manager.save_groups()
        for tab in self.tabs:
            group = tab.group
            url = tab.url().toString() or tab.page().requestedUrl().toString()
            if not url:
                url = self.config.get("home_page", "https://www.google.com")
            title = tab.title() or "New Tab"
            if not title or title == "about:blank":
                title = "New Tab"

            states.append(
                TabState(
                    url=url,
                    title=title,
                    group=group,
                    tab_id=tab.tab_id,
                    group_id=tab.group_id,
                )
            )

        self.tab_manager.save_tabs(states)

    def _restore_tabs(self):
        if self.profile_manager.is_search_profile():
            self._new_tab()
            return
        saved = self.tab_manager.get_saved_tabs()
        self._restoring_tabs = True
        try:
            if saved:
                for state in saved:
                    self._new_tab(
                        url=state.url, title=state.title, index=len(self.tabs)
                    )
                    self.tabs[-1].group = state.group
                    self.tabs[-1].tab_id = state.tab_id
                    self.tabs[-1].group_id = state.group_id
            else:
                self._new_tab(index=len(self.tabs))
        finally:
            self._restoring_tabs = False
        self._switch_tab(0)
        self._sync_tab_group_labels()

    # ---------- Permission & Download ----------
    def _connect_page_signals(self, page):
        PermissionService.connect_page(
            page,
            self._on_permission_requested,
            self._on_feature_permission_requested,
            self._on_desktop_media_requested,
            modern_available=QWebEnginePermission is not None,
        )
        page.fullScreenRequested.connect(self._on_fullscreen_requested)

    def _on_permission_requested(self, permission: QWebEnginePermission):
        PermissionService.respond(
            permission, self._ask_permission, lambda: self._closing
        )

    def _on_feature_permission_requested(self, page, origin, feature):
        PermissionService.respond_legacy(
            page, origin, feature, self._ask_permission, lambda: self._closing
        )

    def _ask_permission(self, origin, feature):
        name = PermissionService.feature_name(feature)
        reply = QMessageBox.question(
            self,
            "Permission Request",
            f"{origin.toString()} requests {name}. Allow?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # ---------- Tab Group UI ----------
    def _handle_group_rename_request(self, tab_index: int):
        name, ok = QInputDialog.getText(self, "Tab Group", "Group name:")
        if ok:
            group = name.strip() or None
            if group:
                self._set_tab_group_existing(tab_index, group)
            else:
                self._clear_tab_group(tab_index)

    def _set_tab_group_existing(self, tab_index: int, group_name: str):
        if 0 <= tab_index < len(self.tabs):
            group = self.tab_manager.ensure_group(group_name)
            group.collapsed = False
            self.tabs[tab_index].group = group.name
            self.tabs[tab_index].group_id = group.group_id
            self.tab_manager.save_groups()
            self._sync_tab_group_labels()
            self._save_current_tabs()

    def _clear_tab_group(self, tab_index: int):
        if not 0 <= tab_index < len(self.tabs):
            return
        self.tabs[tab_index].group = None
        self.tabs[tab_index].group_id = None
        self._sync_tab_group_labels()
        self._save_current_tabs()

    def _sync_tab_group_labels(self):
        dirty = self._normalize_groups()
        self.tab_bar.available_groups = [
            group.name for group in self.tab_manager.groups.values()
        ]
        seen = set()
        visible_indices = []
        was_blocked = self.tab_bar.tab_bar.blockSignals(True)
        for i, tab in enumerate(self.tabs):
            self.tab_bar.set_tab_group(i, tab.group)
            group = self.tab_manager.groups.get(tab.group_id)
            visible = not (group and group.collapsed and group.group_id in seen)
            self.tab_bar.tab_bar.setTabVisible(i, visible)
            if visible:
                visible_indices.append(i)
            if group:
                seen.add(group.group_id)
        self.tab_bar.tab_bar.blockSignals(was_blocked)
        if self.tabs and self.current_tab_index not in visible_indices:
            self._switch_tab(visible_indices[0])
        if dirty:
            # Only write when something actually changed: this runs on every
            # label refresh, and an fsync per navigation is needless I/O.
            self.tab_manager.save_groups()

    def _normalize_groups(self):
        """Reconcile tab labels with stored groups; report whether it changed.

        This runs on a read path, so it must not write to disk unless a group
        was actually created, renamed, or dropped.
        """
        changed = False
        for tab in self.tabs:
            if tab.group_id in self.tab_manager.groups:
                name = self.tab_manager.groups[tab.group_id].name
                if tab.group != name:
                    tab.group = name
                    changed = True
            elif tab.group_id is not None:
                tab.group = None
                tab.group_id = None
                changed = True
            elif tab.group:
                tab.group_id = self.tab_manager.ensure_group(tab.group).group_id
                changed = True
        return changed

    def rename_group(self, group_id, name):
        if any(
            group.name == name and group.group_id != group_id
            for group in self.tab_manager.groups.values()
        ):
            QMessageBox.warning(
                self, "Tab Group", "Another group already has that name."
            )
            return False
        self.tab_manager.groups[group_id].name = name
        self.tab_manager.save_groups()
        self._sync_tab_group_labels()
        self._save_current_tabs()
        return True

    def delete_group(self, group_id):
        self.tab_manager.groups.pop(group_id, None)
        self.tab_manager.save_groups()
        self._sync_tab_group_labels()
        self._save_current_tabs()

    def toggle_group(self, group_id):
        group = self.tab_manager.groups[group_id]
        group.collapsed = not group.collapsed
        self.tab_manager.save_groups()
        self._sync_tab_group_labels()

    def _toggle_named_group(self, name):
        group = self.tab_manager.ensure_group(name)
        self.toggle_group(group.group_id)

    def move_group(self, group_id, position=0):
        members = [tab.tab_id for tab in self.tabs if tab.group_id == group_id]
        for offset, tab_id in enumerate(members):
            index = next(i for i, tab in enumerate(self.tabs) if tab.tab_id == tab_id)
            self.tab_bar.tab_bar.moveTab(
                index, min(position + offset, len(self.tabs) - 1)
            )
        self._sync_tab_group_labels()
        self._save_current_tabs()

    def _manage_tab_groups(self):
        GroupDialog(self).exec()

    def _view_cookies(self):
        if not self.cookie_manager:
            return
        dlg = CookieDialog(
            self.cookie_manager, self, self.profile_manager.protect_cookie_deletion
        )
        dlg.exec()

    def _clear_all_cookies(self):
        if not self.cookie_manager:
            return
        reply = QMessageBox.question(
            self,
            "Clear Cookies",
            "Delete all cookies for this profile?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.profile_manager.protect_cookie_deletion()
            self.cookie_manager.clear_all_cookies()

    # ---------- Tab Bar group display updates ----------
    def _refresh_all_tab_bar_groups(self):
        # Live tabs are the source of truth for labels; saved state can lag
        # behind the window that most recently wrote it.
        self._sync_tab_group_labels()

    @staticmethod
    def _dispose_tab(tab: BrowserTab) -> None:
        """Stop a tab and detach its callbacks before scheduling deletion."""
        for signal in (
            tab.urlChanged,
            tab.titleChanged,
            tab.loadProgress,
            tab.loadFinished,
        ):
            signal.disconnect()
        tab.stop()
        tab.hide()
        # BrowserPage is parented to the view and is destroyed with it.
        tab.deleteLater()

    # ---------- Close handling ----------
    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        # Downloads are shared by every window on this registry, so the decision is
        # "will closing this window cancel live downloads?" That is true exactly
        # when every owner is being closed; otherwise the remaining window keeps
        # them running and there is nothing to confirm.
        closing_owners = {self.profile_manager}
        pending_windows = list(self._child_windows)
        while pending_windows:
            child = pending_windows.pop()
            closing_owners.add(child.profile_manager)
            pending_windows.extend(child._child_windows)
        if (
            self.profile_manager.registry.owners <= closing_owners
            and self.download_manager.active()
        ):
            reply = QMessageBox.question(
                self,
                "Active Downloads",
                "Closing will cancel active downloads. Close anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        self.find_bar.close_find(clear_query=True)
        self.find_bar.set_page(None)
        if self._password_dialog is not None:
            self._password_dialog.reject()
        if self.history is not None:
            self.history.changed.disconnect(self._refresh_address_history)

        self._save_current_tabs()
        self.config.update(
            {"window_width": self.width(), "window_height": self.height()}
        )

        for win in self._child_windows:
            win.close()

        from ui.task_watch import TaskWatchDialog

        for watcher in self.findChildren(TaskWatchDialog):
            watcher.close()

        if self.cookie_manager is not None:
            self.cookie_manager.close()
            self.cookie_manager = None
        for tab in list(self.tabs):
            self._dispose_tab(tab)

        self.tabs.clear()
        self.current_tab_index = 0
        # Qt requires pages to be destroyed before their profiles. Profile
        # destruction also flushes the engine's persistent cookies and storage.
        # Include tabs already awaiting deferred deletion after a profile switch.
        # Explicit destruction here also works while a nested dialog is closing.
        for tab in self.tab_container.findChildren(BrowserTab):
            sip.delete(tab)
        self.profile_manager.close()

        event.accept()

    def _on_fullscreen_requested(self, request: QWebEngineFullScreenRequest):
        # toggleOn describes the requested state, not whether to invert ours.
        entering = request.toggleOn()
        request.accept()
        if entering:
            self._enter_fullscreen()
        else:
            self._exit_fullscreen()

    def _enter_fullscreen(self):
        if self._was_fullscreen:
            return
        self._was_fullscreen = True

        self._saved_window_state = self.windowState()
        self._saved_geometry = self.geometry()
        self._saved_vis = {
            "menubar": self.menuBar().isVisible() if self.menuBar() else True,
            "tabbar": self.tab_bar.isVisible(),
            "address": self.address_bar.isVisible(),
            "find": self.find_bar.isVisible(),
            "status": self.status_bar.isVisible() if self.statusBar() else True,
        }

        if self.menuBar():
            self.menuBar().setVisible(False)
        self.tab_bar.setVisible(False)
        self.address_bar.setVisible(False)
        self.find_bar.hide()
        if self.statusBar():
            self.statusBar().setVisible(False)

        self.showFullScreen()

    def _exit_fullscreen(self):
        if not self._was_fullscreen:
            return
        self._was_fullscreen = False

        self.showNormal()

        if self._saved_window_state is not None:
            self.setWindowState(self._saved_window_state)
        if self._saved_geometry is not None:
            self.setGeometry(self._saved_geometry)

        if self.menuBar():
            self.menuBar().setVisible(self._saved_vis.get("menubar", True))
        self.tab_bar.setVisible(self._saved_vis.get("tabbar", True))
        self.address_bar.setVisible(self._saved_vis.get("address", True))
        self.find_bar.setVisible(self._saved_vis.get("find", False))
        self.find_bar.page_loaded()
        if self.statusBar():
            self.statusBar().setVisible(self._saved_vis.get("status", True))

    def keyPressEvent(self, event):
        if self._was_fullscreen and event.key() == Qt.Key.Key_Escape:
            self._exit_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_settings(self):
        dlg = SettingsDialog(
            self.config, self, self.profile_manager.current_profile.httpUserAgent()
        )
        if dlg.exec():
            dlg.apply()

            # Apply dark mode immediately
            self.apply_dark_mode()

            self.profile_manager.refresh_settings()

    def _use_global_cookies(self):
        if not self.tabs or self.profile_manager.current_profile_name in (
            "global",
            "search",
        ):
            return
        domain = self.tabs[self.current_tab_index].url().host()
        if not domain:
            return
        reply = QMessageBox.question(
            self,
            "Use Global Cookies",
            f"Replace the local cookie baseline for {domain} with global cookies? This can change your signed-in account. Reload the page afterwards.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.profile_manager.use_global_cookies(domain)

    def _on_desktop_media_requested(self, request):
        # One picker can be open at a time. A second request while it is up must
        # be declined explicitly, or it would silently overwrite the slot and
        # hang until Chromium timed it out.
        if self._pending_desktop_request is not None:
            logger.warning("Declining overlapping desktop media request")
            request.cancel()
            return
        self._pending_desktop_request = request
        dialog = None
        try:
            dialog = DesktopMediaDialog(request, self)
            dialog.exec()
        finally:
            # Resolve escaping, closing, constructor errors, and execution errors.
            if dialog is not None:
                dialog.cancel_pending()
                dialog.deleteLater()
            else:
                request.cancel()
            self._pending_desktop_request = None
