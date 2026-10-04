"""Main browser window."""

import json
import logging
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl
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
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config import Config
from cookie_manager import CookieManager
from layout_preferences import (
    LayoutConflict,
    LayoutPreferences,
    preferences_from_config,
)
from profile_manager import ProfileManager
from services.permission_service import PermissionService
from services.site_permissions import FEATURES
from services.start_pages import NEW_TAB, WELCOME, display_url, page_name
from tab_manager import TabManager, TabState
from ui.adblock_dialog import AdBlockDialog
from ui.address_bar import AddressBar
from ui.app_connections import AppConnectionsDialog
from ui.cookie_dialog import CookieDialog
from ui.download_dialog import DownloadDialog
from ui.find_bar import FindBar
from ui.group_dialog import GroupDialog
from ui.layout_dialog import LayoutDialog
from ui.media_dialog import DesktopMediaDialog
from ui.password_dialog import PasswordDialog
from ui.settings_dialog import SettingsDialog
from ui.site_permissions import SitePermissionDialog, SitePermissionsDialog
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


def reorder_menus(menubar, menus: dict, order) -> None:
    """Reorder a menu bar, keeping every menu present and visible.

    A layout may move menus around but never hides one, so Settings and Reset
    Layout stay reachable. Unknown identifiers are ignored and menus missing
    from the order keep their relative position at the end.
    """
    wanted = [menus[name] for name in order if name in menus]
    wanted.extend(menu for name, menu in menus.items() if menu not in wanted)
    actions = {menu.menuAction() for menu in wanted}
    for action in list(menubar.actions()):
        if action in actions:
            menubar.removeAction(action)
    for menu in wanted:
        menubar.addMenu(menu)


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
        if url.scheme() == "steed" and url.host() == "action":
            tab = self.parent()
            window = getattr(tab, "browser_window", None)
            if (
                window is not None
                and is_main_frame
                and page_name(self.url()) is not None
                and navigation_type
                in (
                    QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
                    QWebEnginePage.NavigationType.NavigationTypeFormSubmitted,
                )
                and not window.profile_manager.registry.start_pages.state(
                    window.profile_manager.current_profile_name
                )["html" if page_name(self.url()) == "newtab" else "welcome_html"]
            ):
                QTimer.singleShot(0, lambda: window._start_page_action(tab, url))
            return False
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
        self.native_panel = None
        self.tab_id = uuid4().hex
        # Runtime references must stay distinct when windows restore saved IDs.
        self.agent_tab_id = uuid4().hex
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.native_panel is not None:
            self.native_panel.setGeometry(self.rect())


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
        self.agent_tab = None
        self.web_chat_dock = None
        self.file_library_dialog = None
        self.current_tab_index = 0
        self._restoring_tabs = False
        self.closed_tabs = self.profile_manager.registry.closed_tabs
        self._child_windows: list[BrowserWindow] = []
        self._password_dialog = None
        self.agent_sidebar = None
        self.data_panels_dock = None
        self.agent_setup_dialog = None
        self.setup_window()
        self.setup_menus()
        self.setup_ui()
        self.apply_dark_mode()
        # Manual layout is presentation only and is shared by every window.
        self.layout_preferences = preferences_from_config(self.config)
        self._layout_editing = False
        self.apply_layout(self.layout_preferences)
        self.profile_manager.registry.layout_changed.connect(self._layout_committed)

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
        self.profile_manager.registry.start_pages.changed.connect(
            self._start_page_changed
        )
        self.profile_manager.registry.start_pages.news_changed.connect(
            self._start_news_changed
        )
        # The initial profile was selected before the menu/signals existed.
        self._update_profile_shortcuts()
        # Initialize profile and managers
        self._initialize_profile(self.profile_manager.current_profile_name)
        # Restore or create initial tab
        if self.config.get("restore_tabs_on_startup", True):
            self._restore_tabs()
        else:
            self._new_tab(url=self._startup_page())

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
        # Stable identifiers let a saved layout reorder the menu bar.
        self._layout_menus = {
            "general": menubar.addMenu("General"),
            "profile": menubar.addMenu("Profile"),
            "tabs": menubar.addMenu("Tabs"),
            "view": menubar.addMenu("View"),
            "cookies": menubar.addMenu("Cookies"),
            "agents": menubar.addMenu("Agents"),
        }
        general_menu = self._layout_menus["general"]
        profile_menu = self._layout_menus["profile"]
        tabs_menu = self._layout_menus["tabs"]
        view_menu = self._layout_menus["view"]
        cookies_menu = self._layout_menus["cookies"]
        agents_menu = self._layout_menus["agents"]
        new_tab_action = QAction("New Tab", self)
        new_tab_action.setShortcut(QKeySequence("Ctrl+T"))
        new_tab_action.triggered.connect(lambda: self._new_tab())
        tabs_menu.addAction(new_tab_action)
        restore_tab_action = QAction("Restore Closed Tab", self)
        restore_tab_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        restore_tab_action.triggered.connect(self._restore_closed_tab)
        self.restore_tab_action = restore_tab_action
        restore_tab_action.setEnabled(False)
        tabs_menu.addAction(restore_tab_action)
        close_tab_action = QAction("Close Tab", self)
        close_tab_action.setShortcut(QKeySequence("Ctrl+W"))
        close_tab_action.triggered.connect(self._close_current_tab)
        tabs_menu.addAction(close_tab_action)
        self.reload_action = QAction("Reload", self)
        self.reload_action.setShortcuts([QKeySequence("Ctrl+R"), QKeySequence("F5")])
        self.reload_action.triggered.connect(self._reload)
        tabs_menu.addAction(self.reload_action)
        # Home stays reachable here even if the address bar button is hidden.
        home_action = QAction("Home", self)
        home_action.setShortcut(QKeySequence("Ctrl+Shift+H"))
        home_action.triggered.connect(self._go_home)
        tabs_menu.addAction(home_action)
        focus_address_action = QAction("Go to Address Bar", self)
        focus_address_action.setShortcut(QKeySequence("Ctrl+L"))
        focus_address_action.triggered.connect(lambda: self.address_bar.focus_address())
        view_menu.addAction(focus_address_action)
        clear_history_action = QAction("Clear Browsing History...", self)
        clear_history_action.triggered.connect(self._clear_browsing_history)
        cookies_menu.addAction(clear_history_action)
        cookies_menu.addSeparator()
        passwords_action = QAction("Passwords...", self)
        passwords_action.triggered.connect(lambda: self._open_passwords())
        general_menu.addAction(passwords_action)
        fill_login_action = QAction("Fill Login", self)
        fill_login_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        fill_login_action.triggered.connect(
            lambda: self._open_passwords(auto_fill=True)
        )
        general_menu.addAction(fill_login_action)
        downloads_action = QAction("Downloads...", self)
        downloads_action.setShortcut(QKeySequence("Ctrl+J"))
        downloads_action.triggered.connect(
            lambda: DownloadDialog(self.download_manager, self).exec()
        )
        general_menu.addAction(downloads_action)
        adblock_action = QAction("Ad Blocking...", self)
        adblock_action.triggered.connect(
            lambda: AdBlockDialog(self.adblock, self).exec()
        )
        general_menu.addAction(adblock_action)
        general_menu.addSeparator()
        settings_action = QAction("Settings...", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        general_menu.addAction(settings_action)
        general_menu.addAction("Customize New Tab…").triggered.connect(
            self._customize_new_tab
        )
        general_menu.addAction("Welcome to Steed").triggered.connect(
            lambda: self._new_tab(url=WELCOME)
        )
        bookmark_action = QAction("Bookmark This Page…", self)
        bookmark_action.setShortcut(QKeySequence("Ctrl+D"))
        bookmark_action.triggered.connect(self._bookmark_current)
        tabs_menu.addAction(bookmark_action)
        general_menu.addAction("Manage Bookmarks…").triggered.connect(
            self._manage_bookmarks
        )
        layout_action = QAction("Layout Settings...", self)
        layout_action.setToolTip(
            "Reorder or hide address bar buttons, reorder menus, and choose "
            "the agent sidebar side, width and control spacing"
        )
        layout_action.triggered.connect(self._open_layout_settings)
        general_menu.addAction(layout_action)
        import_layout = QAction("Import Layout...", self)
        import_layout.triggered.connect(self._import_layout)
        general_menu.addAction(import_layout)
        export_layout = QAction("Export Layout...", self)
        export_layout.triggered.connect(self._export_layout)
        general_menu.addAction(export_layout)
        recover_layout = QAction("Recover Previous Layout", self)
        recover_layout.triggered.connect(self._recover_layout)
        view_menu.addAction(recover_layout)
        general_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        task_action = QAction("Task Permissions...", self)
        task_action.triggered.connect(self._open_tasks)
        agents_menu.addAction(task_action)
        site_action = QAction("Website Permissions...", self)
        site_action.triggered.connect(
            lambda: SitePermissionsDialog(
                self.profile_manager.registry.site_permissions, self
            ).exec()
        )
        general_menu.addAction(site_action)
        apps_action = QAction("App Connections...", self)
        apps_action.triggered.connect(self._open_app_connections)
        agents_menu.addAction(apps_action)
        self.file_library_action = QAction("Agent File Library…", self)
        self.file_library_action.triggered.connect(self._open_file_library)
        self.file_library_action.setEnabled(
            not self.profile_manager.is_search_profile()
        )
        agents_menu.addAction(self.file_library_action)
        self.agent_access_action = QAction("Enable Local Agent Access", self)
        self.agent_access_action.setCheckable(True)
        registry = self.profile_manager.registry
        self.agent_access_action.setChecked(registry.agent_gateway is not None)
        registry.agent_access_changed.connect(self.agent_access_action.setChecked)
        self.agent_access_action.triggered.connect(self._set_agent_access)
        agents_menu.addAction(self.agent_access_action)
        setup_agent_action = QAction("Agent Connection Setup…", self)
        setup_agent_action.triggered.connect(self._open_agent_setup)
        agents_menu.addAction(setup_agent_action)
        view_menu.addSeparator()
        self.find_action = QAction("Find in Page...", self)
        self.find_action.setShortcut(QKeySequence("Ctrl+F"))
        self.find_action.triggered.connect(self._open_find)
        view_menu.addAction(self.find_action)
        next_match = QAction("Find Next", self)
        next_match.setShortcuts([QKeySequence("F3"), QKeySequence("Ctrl+G")])
        next_match.triggered.connect(lambda: self.find_bar.find_next())
        view_menu.addAction(next_match)
        previous_match = QAction("Find Previous", self)
        previous_match.setShortcuts(
            [QKeySequence("Shift+F3"), QKeySequence("Ctrl+Shift+G")]
        )
        previous_match.triggered.connect(lambda: self.find_bar.find_next(True))
        view_menu.addAction(previous_match)
        agents_menu.addSeparator()
        self.agent_sidebar_action = QAction("Agent Sidebar", self)
        self.agent_sidebar_action.setCheckable(True)
        self.agent_sidebar_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.agent_sidebar_action.triggered.connect(self._toggle_agent_sidebar)
        agents_menu.addAction(self.agent_sidebar_action)
        self.data_panels_action = QAction("Data Panels", self)
        self.data_panels_action.setCheckable(True)
        self.data_panels_action.setEnabled(not self.profile_manager.is_search_profile())
        self.data_panels_action.triggered.connect(self._toggle_data_panels)
        agents_menu.addAction(self.data_panels_action)
        agents_tab_action = QAction("Agent Chat Tab", self)
        agents_tab_action.triggered.connect(lambda: self._open_agent_tab())
        agents_menu.addAction(agents_tab_action)
        web_chats_menu = agents_menu.addMenu("Website Chats")
        from ui.web_chat import WEB_CHATS

        for label, url in WEB_CHATS.items():
            action = web_chats_menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, url=url: self._new_tab(url=url)
            )
        web_chats_menu.addSeparator()
        web_chats_menu.addAction("Current tab in chat sidebar").triggered.connect(
            self._dock_web_chat
        )
        web_chats_menu.addAction("Share instructions with a chat…").triggered.connect(
            self._choose_web_chat
        )
        view_menu.addSeparator()
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
        # Reset Layout stays in View whatever the toolbar shows, so a layout
        # can always be recovered without a visible control.
        reset_layout_action = QAction("Reset Layout", self)
        reset_layout_action.triggered.connect(self.reset_layout)
        view_menu.addAction(reset_layout_action)
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
        general_menu.addSeparator()
        general_menu.addAction(exit_action)
        tabs_menu.addSeparator()
        group_menu = tabs_menu.addMenu("Tab Groups")
        group_menu.addAction("Set Current Tab Group…").triggered.connect(
            lambda: self._handle_group_rename_request(self.current_tab_index)
        )
        group_menu.addAction("Remove Current Tab from Group").triggered.connect(
            lambda: self._clear_tab_group(self.current_tab_index)
        )
        toggle_group_action = group_menu.addAction("Collapse / Expand Current Group")
        toggle_group_action.triggered.connect(self._toggle_current_tab_group)
        group_menu.aboutToShow.connect(
            lambda: toggle_group_action.setEnabled(
                0 <= self.current_tab_index < len(self.tabs)
                and self.tabs[self.current_tab_index].group_id
                in self.tab_manager.groups
            )
        )
        group_menu.addSeparator()
        manage_groups_action = QAction("Manage Tab Groups...", self)
        manage_groups_action.triggered.connect(self._manage_tab_groups)
        group_menu.addAction(manage_groups_action)
        view_cookies_action = QAction("View Cookies...", self)
        view_cookies_action.triggered.connect(self._view_cookies)
        cookies_menu.addAction(view_cookies_action)
        clear_cookies_action = QAction("Clear All Cookies", self)
        clear_cookies_action.triggered.connect(self._clear_all_cookies)
        cookies_menu.addAction(clear_cookies_action)
        use_global_action = QAction("Use Global Cookies for This Site...", self)
        use_global_action.triggered.connect(self._use_global_cookies)
        cookies_menu.addAction(use_global_action)
        self._native_layout_actions = {
            "new_tab": new_tab_action,
            "reload": self.reload_action,
            "home": home_action,
            "address": focus_address_action,
            "find": self.find_action,
            "zoom_in": self.zoom_in_action,
            "zoom_out": self.zoom_out_action,
            "zoom_reset": self.zoom_reset_action,
            "agents": self.agent_sidebar_action,
            "downloads": downloads_action,
            "passwords": passwords_action,
            "layout_settings": layout_action,
        }

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
        self.live_bar = QWidget()
        live_layout = QHBoxLayout(self.live_bar)
        self.live_label = QLabel()
        self.live_label.setTextFormat(Qt.TextFormat.PlainText)
        self.live_label.setWordWrap(True)
        live_layout.addWidget(self.live_label, 1)
        self.take_back_button = QPushButton("Take Back")
        self.take_back_button.clicked.connect(self._take_back_current_tab)
        live_layout.addWidget(self.take_back_button)
        self.live_bar.hide()
        layout.addWidget(self.live_bar)
        self._live_tasks = None
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

        sidebar = getattr(self, "agent_sidebar", None)
        if sidebar is not None:
            sidebar._apply_style()
        if hasattr(self, "layout_preferences"):
            self._apply_layout_text()

    # ---------- Manual layout ----------

    def _sidebar_dock_area(self):
        """Return the dock area the saved layout asks for."""
        preferences = getattr(self, "layout_preferences", None)
        side = preferences.sidebar_side if preferences else "right"
        return (
            Qt.DockWidgetArea.LeftDockWidgetArea
            if side == "left"
            else Qt.DockWidgetArea.RightDockWidgetArea
        )

    def _apply_menu_order(self, order) -> None:
        """Reorder the menu bar, keeping every menu visible."""
        reorder_menus(self.menuBar(), getattr(self, "_layout_menus", {}), order)

    def _apply_sidebar_layout(self) -> None:
        """Move and resize the docked agent sidebar to match the layout.

        The sidebar may not exist yet, and while it is embedded in an agent
        tab it belongs to that tab rather than the window; in both cases the
        saved layout is applied when the sidebar returns to the dock.
        """
        sidebar = getattr(self, "agent_sidebar", None)
        preferences = getattr(self, "layout_preferences", None)
        if sidebar is None or preferences is None or self.agent_tab is not None:
            return
        if sidebar.parent() is not self:
            return
        self.addDockWidget(self._sidebar_dock_area(), sidebar)
        self.resizeDocks(
            [sidebar], [preferences.sidebar_width], Qt.Orientation.Horizontal
        )

    def apply_layout(self, preferences: LayoutPreferences) -> None:
        """Show a layout in this window without saving it."""
        from ui.layout_panel import chrome_font

        self.layout_preferences = preferences
        self.setFont(chrome_font(preferences.text_size))
        self._apply_menu_order(preferences.menu_order)
        self.address_bar.apply_toolbar(
            preferences.toolbar_order, preferences.hidden_toolbar
        )
        self.address_bar.apply_density(preferences.density)
        self.tab_bar.apply_density(preferences.density)
        self._apply_sidebar_layout()
        self._apply_layout_panels()
        self._apply_layout_text()

    def _apply_layout_text(self):
        from ui.layout_panel import apply_chrome_font

        roots = [self.menuBar(), self.address_bar, self.tab_bar]
        roots.extend(getattr(self, name, None) for name in ("find_bar", "status_bar"))
        roots.extend(getattr(self, "_layout_panels", []))
        roots.append(getattr(self, "data_panels_dock", None))
        for root in roots:
            if root is not None:
                apply_chrome_font(root, self.layout_preferences.text_size)

    def _apply_layout_panels(self):
        from ui.layout_panel import QuickActionsPanel

        if not hasattr(self, "tab_container"):
            return
        layout = self.centralWidget().layout()
        for panel in getattr(self, "_layout_panels", []):
            layout.removeWidget(panel)
            panel.hide()
            panel.deleteLater()
        self._layout_panels = []
        for placement in ("top", "bottom"):
            position = (
                2 if placement == "top" else layout.indexOf(self.tab_container) + 1
            )
            for definition in self.layout_preferences.panels:
                if definition["placement"] != placement:
                    continue
                panel = QuickActionsPanel(
                    definition, self._native_layout_actions, self.centralWidget()
                )
                self._layout_panels.append(panel)
                layout.insertWidget(position, panel)
                position += 1

    def _open_layout_settings(self) -> None:
        """Edit the manual layout, previewing edits and saving on Save."""
        if self._layout_editing:
            return
        saved = preferences_from_config(self.config)
        dialog = LayoutDialog(saved, self)
        dialog.layout_changed.connect(self.apply_layout)
        self._layout_editing = True
        try:
            if dialog.exec():
                try:
                    self.profile_manager.registry.commit_layout(
                        dialog.preferences, base=saved
                    )
                except LayoutConflict:
                    QMessageBox.warning(
                        self,
                        "Layout changed in another window",
                        "Your settings were not saved because another window changed "
                        "the same setting. Reopen Layout Settings to try again.",
                    )
        finally:
            self._layout_editing = False
            self.apply_layout(preferences_from_config(self.config))
            dialog.deleteLater()

    def _layout_committed(self, preferences) -> None:
        if not self._layout_editing:
            self.apply_layout(preferences.copy())

    def reset_layout(self) -> None:
        """Restore today's appearance and keep the saved layout files."""
        preferences = preferences_from_config(self.config)
        preferences.reset()
        self.profile_manager.registry.commit_layout(preferences)

    def _import_layout(self):
        from layout_format import MAX_LAYOUT_BYTES, parse_layout
        from ui.layout_review import LayoutReviewDialog

        filename, _ = QFileDialog.getOpenFileName(
            self, "Import browser layout", "", "Layout JSON (*.json)"
        )
        if not filename:
            return
        try:
            with Path(filename).open("rb") as handle:
                source = handle.read(MAX_LAYOUT_BYTES + 1).decode("utf-8")
            definition = parse_layout(source)
            dialog = LayoutReviewDialog(self.profile_manager.registry, definition, self)
            dialog.exec()
            dialog.deleteLater()
        except (OSError, ValueError):
            QMessageBox.warning(
                self,
                "Import layout",
                "Choose a valid Steed layout JSON file of at most 16 KiB.",
            )

    def _export_layout(self):
        from layout_format import export_layout

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export browser layout", "steed-layout.json", "Layout JSON (*.json)"
        )
        if not filename:
            return
        try:
            source = export_layout(preferences_from_config(self.config))
            with Path(filename).open("x", encoding="utf-8") as handle:
                handle.write(source)
        except (OSError, ValueError):
            QMessageBox.warning(
                self,
                "Export layout",
                "The layout could not be exported. Choose a new filename; existing files are preserved.",
            )

    def _recover_layout(self):
        from layout_preferences import LAYOUT_VERSION

        previous = self.config.get("layout_previous")
        if (
            not isinstance(previous, dict)
            or type(previous.get("version")) is not int
            or previous["version"] not in (1, LAYOUT_VERSION)
        ):
            QMessageBox.information(
                self, "Previous layout", "There is no previous saved layout to recover."
            )
            return
        current = preferences_from_config(self.config)
        recovered = current.to_dict()
        recovered["layout"] = LayoutPreferences(previous).to_dict()["layout"]
        recovered["active"] = None
        self.profile_manager.registry.commit_layout(LayoutPreferences(recovered))

    # ---------- Profile handling ----------
    def _on_profile_changed(self, profile_name: str):
        self._update_window_title()
        self._update_profile_shortcuts()
        self.file_library_action.setEnabled(profile_name != "search")
        self.data_panels_action.setEnabled(profile_name != "search")
        if self.data_panels_dock is not None:
            self.data_panels_dock.refresh()
            if profile_name == "search":
                self.data_panels_dock.hide()

        if self.agent_sidebar is not None:
            self.agent_sidebar.set_private(profile_name == "search")

    def _toggle_data_panels(self, visible):
        if self._closing or self.profile_manager.is_search_profile():
            self.data_panels_action.setChecked(False)
            return
        if self.data_panels_dock is None and visible:
            from ui.data_panels import DataPanelDock

            self.data_panels_dock = DataPanelDock(
                self.profile_manager.registry.tasks, self
            )
            self.addDockWidget(self._sidebar_dock_area(), self.data_panels_dock)
            self.data_panels_dock.visibilityChanged.connect(
                self.data_panels_action.setChecked
            )
            if (
                self.agent_sidebar is not None
                and self.agent_tab is None
                and not self.agent_sidebar.isFloating()
                and self.dockWidgetArea(self.agent_sidebar)
                == self.dockWidgetArea(self.data_panels_dock)
            ):
                self.tabifyDockWidget(self.agent_sidebar, self.data_panels_dock)
        if self.data_panels_dock is not None:
            self.data_panels_dock.refresh()
            self.data_panels_dock.setVisible(visible)
            if visible:
                self.data_panels_dock.raise_()

    def _toggle_agent_sidebar(self, visible):
        if self.agent_tab is not None and visible:
            tab = self.agent_tab
            self._return_agent_sidebar(tab)
            if len(self.tabs) > 1:
                self._close_tab(self.tabs.index(tab))
        if self.agent_sidebar is None and visible:
            from ui.agent_sidebar import AgentSidebar

            try:
                self.agent_sidebar = AgentSidebar(
                    self.profile_manager.registry.tasks,
                    self,
                    providers=self.profile_manager.registry.agent_providers,
                )
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                self.agent_sidebar_action.setChecked(False)
                QMessageBox.warning(
                    self, "External Agents", "The agent sidebar could not be opened."
                )
                return
            self.addDockWidget(self._sidebar_dock_area(), self.agent_sidebar)
            if (
                self.data_panels_dock is not None
                and not self.data_panels_dock.isFloating()
                and self.dockWidgetArea(self.agent_sidebar)
                == self.dockWidgetArea(self.data_panels_dock)
            ):
                self.tabifyDockWidget(self.data_panels_dock, self.agent_sidebar)
            preferences = getattr(self, "layout_preferences", None)
            if preferences is not None:
                self.resizeDocks(
                    [self.agent_sidebar],
                    [preferences.sidebar_width],
                    Qt.Orientation.Horizontal,
                )
            self.agent_sidebar.setup_requested.connect(self._open_agent_setup)
            self.agent_sidebar.visibilityChanged.connect(
                lambda shown: self.agent_sidebar_action.setChecked(
                    shown and self.agent_tab is None
                )
            )
        if self.agent_sidebar is not None:
            self.agent_sidebar.set_private(self.profile_manager.is_search_profile())
            self.agent_sidebar.setVisible(visible)
            if visible:
                self.agent_sidebar.raise_()

    def _open_agent_tab(self):
        if self.agent_tab in self.tabs:
            self._switch_tab(self.tabs.index(self.agent_tab))
            return self.agent_tab
        return self._new_tab(
            url="steed:agents", title="External Agents", return_tab=True
        )

    def _embed_agent_sidebar(self, tab):
        self._toggle_agent_sidebar(True)
        if self.agent_sidebar is None:
            return
        self.removeDockWidget(self.agent_sidebar)
        tab.agent_panel = True
        self.agent_tab = tab
        # QWebEngineView owns its internal layout; overlay the native panel
        # without replacing or deleting that engine-owned layout.
        self.agent_sidebar.setParent(tab)
        tab.native_panel = self.agent_sidebar
        self.agent_sidebar.setTitleBarWidget(QWidget(self.agent_sidebar))
        self.agent_sidebar.setGeometry(tab.rect())
        self.agent_sidebar.show()
        self.agent_sidebar.raise_()
        self._update_page_actions()
        self.agent_sidebar_action.setChecked(False)
        self.tab_bar.set_tab_title(self.tabs.index(tab), "External Agents")
        self.address_bar.set_url("steed:agents")

    def _return_agent_sidebar(self, tab):
        if tab is not self.agent_tab:
            return
        self.agent_tab = None
        tab.agent_panel = False
        tab.native_panel = None
        self.agent_sidebar.setParent(self)
        self.agent_sidebar.setTitleBarWidget(None)
        self.addDockWidget(self._sidebar_dock_area(), self.agent_sidebar)
        preferences = getattr(self, "layout_preferences", None)
        if preferences is not None:
            self.resizeDocks(
                [self.agent_sidebar],
                [preferences.sidebar_width],
                Qt.Orientation.Horizontal,
            )
        self.agent_sidebar.hide()
        self._update_page_actions()

    def _dock_web_chat(self):
        from ui.web_chat import WebChatDock

        if not self.tabs:
            return
        view = self.tabs[self.current_tab_index]
        if getattr(view, "agent_panel", False) or view.url().scheme() != "https":
            QMessageBox.information(
                self, "Website chat", "Open an HTTPS chat website first."
            )
            return
        if self.web_chat_dock is None:
            self.web_chat_dock = WebChatDock(self)
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea, self.web_chat_dock
            )
        others = [t for t in self.tabs if t is not view]
        if not others:
            others = [self._new_tab(url="about:blank", return_tab=True)]
        self.web_chat_dock.adopt(view)
        self._switch_tab(self.tabs.index(others[0]))

    def _choose_web_chat(self):
        from ui.web_chat import WEB_CHATS

        hosts = {QUrl(url).host() for url in WEB_CHATS.values()}
        choices = [
            t
            for t in self.tabs
            if t.url().scheme() == "https" and t.url().host() in hosts
        ]
        if (
            self.web_chat_dock
            and self.web_chat_dock.view not in choices
            and self.web_chat_dock.view
        ):
            choices.append(self.web_chat_dock.view)
        if not choices:
            QMessageBox.information(
                self, "Website chat", "Open a chat from View → Website Chats first."
            )
            return
        labels = [
            f"{i + 1}. {t.url().host()} — {t.title()[:80]}"
            for i, t in enumerate(choices)
        ]
        label, ok = QInputDialog.getItem(
            self, "Share instructions", "Choose destination chat:", labels, 0, False
        )
        if ok:
            self._share_web_chat(choices[labels.index(label)])

    def _share_web_chat(self, view):
        from ui.web_chat import WebChatPromptDialog

        dialog = WebChatPromptDialog(self, view)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _open_agent_setup(self):
        if self._closing:
            return
        if self.agent_setup_dialog is None:
            from ui.agent_setup import AgentSetupDialog

            self.agent_setup_dialog = AgentSetupDialog(
                self.profile_manager.registry, self
            )
            self.agent_setup_dialog.enable_requested.connect(
                lambda: self._set_agent_access(True)
            )
            self.agent_setup_dialog.finished.connect(
                lambda: setattr(self, "agent_setup_dialog", None)
            )
        self.agent_setup_dialog.show()
        self.agent_setup_dialog.raise_()
        self.agent_setup_dialog.activateWindow()

    def _open_file_library(self):
        if self._closing or self.profile_manager.is_search_profile():
            return
        if self.file_library_dialog is None:
            from ui.file_library import FileLibraryDialog

            try:
                self.file_library_dialog = FileLibraryDialog(
                    self.profile_manager.registry.tasks, self
                )
            except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
                QMessageBox.warning(self, "Agent File Library", str(error))
                return
            self.file_library_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.file_library_dialog.finished.connect(
                lambda: setattr(self, "file_library_dialog", None)
            )
        self.file_library_dialog.show()
        self.file_library_dialog.raise_()

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
        if not url:
            url = self.config.get("new_tab_page", NEW_TAB) or NEW_TAB

        agent_panel = url == "steed:agents"
        source_tab = self.tabs[self.current_tab_index] if self.tabs else None
        if agent_panel and self.agent_tab in self.tabs:
            self._switch_tab(self.tabs.index(self.agent_tab))
            return self.agent_tab if return_tab else None
        qurl = self._text_to_qurl("about:blank" if agent_panel else url)

        profile = self.profile_manager.current_profile
        tab = BrowserTab(profile, self.tab_container, self)
        tab.agent_source = source_tab if agent_panel else None
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
        if agent_panel:
            self._embed_agent_sidebar(tab)

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
            "steed:agents"
            if getattr(tab, "agent_panel", False)
            else tab.url().toString() or tab.page().requestedUrl().toString(),
            tab.title() or self.tab_bar.tab_bar.tabText(index),
            tab.group,
            tab.tab_id,
            tab.group_id,
        )
        profile_name = self.profile_manager.current_profile_name
        self.closed_tabs.push(profile_name, state, index)
        self._start_page_changed(profile_name)
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

    def _restore_closed_tab(self, tab_id=None):
        profile = self.profile_manager.current_profile_name
        closed = (
            self.closed_tabs.pop_id(profile, tab_id)
            if isinstance(tab_id, str)
            else self.closed_tabs.pop(profile)
        )
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
        self._start_page_changed(profile)

    def _on_tab_moved(self, from_idx: int, to_idx: int):
        tab = self.tabs.pop(from_idx)
        self.tabs.insert(to_idx, tab)
        self.current_tab_index = self.tab_bar.get_current_index()
        self._save_current_tabs()
        self._sync_tab_group_labels()

    def _switch_tab(self, index: int):
        if not (0 <= index < len(self.tabs)):
            return
        previous = (
            self.tabs[self.current_tab_index]
            if 0 <= self.current_tab_index < len(self.tabs)
            else None
        )
        if (
            getattr(self.tabs[index], "agent_panel", False)
            and previous is not self.tabs[index]
            and not getattr(previous, "agent_panel", False)
        ):
            self.tabs[index].agent_source = previous
        self.current_tab_index = index
        if self.web_chat_dock and self.web_chat_dock.view is self.tabs[index]:
            self.web_chat_dock.release()
        self.tab_bar.set_current_tab(index)

        # Hide all tabs
        for i, t in enumerate(self.tabs):
            t.setVisible(
                i == index or bool(self.web_chat_dock and self.web_chat_dock.view is t)
            )
        # Update address bar and navigation buttons
        cur = self.tabs[index]
        self._update_page_actions()
        self._update_zoom()
        self.address_bar.set_url(
            "steed:agents"
            if getattr(cur, "agent_panel", False)
            else display_url(cur.url())
        )
        self.address_bar.set_back_enabled(cur.history().canGoBack())
        self.address_bar.set_forward_enabled(cur.history().canGoForward())
        self._update_window_title(cur.url())
        self.status_bar.showMessage(f"Tab {index + 1} of {len(self.tabs)}")
        self._refresh_live_control()

    def _update_page_actions(self):
        tab = (
            self.tabs[self.current_tab_index]
            if 0 <= self.current_tab_index < len(self.tabs)
            else None
        )
        page_available = tab is not None and not getattr(tab, "agent_panel", False)
        for action in (
            self.reload_action,
            self.find_action,
            self.zoom_in_action,
            self.zoom_out_action,
            self.zoom_reset_action,
        ):
            action.setEnabled(page_available)
        self.address_bar.reload_btn.setEnabled(page_available)
        self.address_bar.zoom_btn.setEnabled(page_available)
        if not getattr(self, "_closing", False):
            self.find_bar.set_page(tab.page() if page_available else None)

    def _bind_live_tasks(self, service):
        if self._live_tasks is service:
            return
        self._live_tasks = service
        service.changed.connect(self._refresh_live_control)
        self._refresh_live_control()

    def _refresh_live_control(self):
        tab = self.tabs[self.current_tab_index] if self.tabs else None
        service = self._live_tasks
        session_id = (
            service.backend.live_session(tab)
            if service is not None and tab is not None
            else None
        )
        self.live_bar.setVisible(session_id is not None and not self._closing)
        if session_id is not None:
            snapshot = service.backend.inspect(session_id)
            self.live_label.setText(
                f"Agent task {snapshot.task_id[:8]} controls this tab · Normal website activity continues"
            )

    def _take_back_tab(self, tab):
        if self._live_tasks is not None:
            self._live_tasks.live_tabs.release_id(tab.agent_tab_id)

    def _take_back_current_tab(self):
        if self.tabs:
            tab = self.tabs[self.current_tab_index]
            self._take_back_tab(tab)
            tab.setFocus()

    def delegate_tabs(self, task_id, *, whole_profile=False):
        service = self.profile_manager.registry.tasks
        if whole_profile:
            name = self.profile_manager.current_profile_name
            tabs = [
                tab
                for owner in service.profiles.owners
                if owner.current_profile_name == name
                for tab in getattr(owner.parent(), "tabs", ())
                if not getattr(tab, "agent_panel", False)
                and tab.url().scheme() in ("http", "https")
            ]
        else:
            tabs = [self.tabs[self.current_tab_index]] if self.tabs else []
            if tabs and getattr(tabs[0], "agent_panel", False):
                source = tabs[0].agent_source
                if source not in self.tabs:
                    raise ValueError("Select a browsing tab before delegating it")
                tabs = [source]
        return service.live_tabs.request(task_id, tabs)

    def _navigate_to_url(self, url_text: str):
        if not self.tabs:
            return
        self._navigate_tab_to_url(self.tabs[self.current_tab_index], url_text)

    def _navigate_tab_to_url(self, tab: BrowserTab, url_text: str):
        if getattr(tab, "agent_panel", False):
            self._return_agent_sidebar(tab)
        self._take_back_tab(tab)
        self.navigation.navigate(tab, self._text_to_qurl(url_text))

    def _text_to_qurl(self, url_text: str) -> QUrl:
        search = self.config.get("search_engine", "https://www.google.com/search?q=")
        return self.navigation.address_url(url_text, search)

    def _go_back(self):
        if self.tabs:
            self._take_back_current_tab()
            self.navigation.action(self.tabs[self.current_tab_index], "back")

    def _go_forward(self):
        if self.tabs:
            self._take_back_current_tab()
            self.navigation.action(self.tabs[self.current_tab_index], "forward")

    def _reload(self):
        if self.tabs:
            self._take_back_current_tab()
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
        home = self.config.get("home_page", NEW_TAB) or NEW_TAB
        self._navigate_to_url(home)

    def _on_tab_url_changed(self, tab: BrowserTab, url: QUrl):
        if self._closing:
            return
        if not self.tabs or not (0 <= self.current_tab_index < len(self.tabs)):
            return

        if tab == self.tabs[self.current_tab_index] and not getattr(
            tab, "agent_panel", False
        ):
            self.address_bar.set_url(display_url(url))
            self._update_window_title(url)

    def _on_tab_title_changed(self, tab: BrowserTab, title: str):
        if getattr(tab, "agent_panel", False):
            return
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
            if (
                page_name(tab.url()) == "newtab"
                and not self.profile_manager.registry.start_pages.state(
                    self.profile_manager.current_profile_name
                )["html"]
            ):
                tab.page().runJavaScript("window.scrollTo(0, 0)")
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
            if getattr(tab, "agent_panel", False):
                url = "steed:agents"
            if not url:
                url = self.config.get("new_tab_page", NEW_TAB) or NEW_TAB
            title = tab.title() or "New Tab"
            if getattr(tab, "agent_panel", False):
                title = "External Agents"
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
                self._new_tab(url=self._startup_page(), index=len(self.tabs))
        finally:
            self._restoring_tabs = False
        self._switch_tab(0)
        self._sync_tab_group_labels()

    # ---------- Permission & Download ----------
    def _connect_page_signals(self, page):
        PermissionService.connect_page(
            page,
            lambda permission: self._on_permission_requested(permission, page),
            self._on_feature_permission_requested,
            self._on_desktop_media_requested,
            modern_available=QWebEnginePermission is not None,
        )
        page.fullScreenRequested.connect(self._on_fullscreen_requested)

    def _on_permission_requested(self, permission: QWebEnginePermission, page=None):
        PermissionService.respond(
            permission,
            lambda origin, feature: self._ask_permission(origin, feature, page),
            lambda: self._closing,
        )

    def _on_feature_permission_requested(self, page, origin, feature):
        PermissionService.respond_legacy(
            page,
            origin,
            feature,
            lambda origin, feature: self._ask_permission(origin, feature, page),
            lambda: self._closing,
        )

    def _ask_permission(self, origin, feature, page=None):
        feature = feature.name
        if (
            self._closing
            or feature not in FEATURES
            or (page is not None and sip.isdeleted(page))
        ):
            return False
        registry = self.profile_manager.registry
        profile = (
            page.profile() if page is not None else self.profile_manager.current_profile
        )
        try:
            context = registry.site_permission_context(profile)
            store = registry.site_permissions
            if store.allowed(
                origin.toString(),
                feature,
                key=context["key"],
                private=context["private"],
            ):
                return True
            dialog = SitePermissionDialog(
                origin.toString(),
                feature,
                context["profile"],
                private=context["private"],
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            # A modal request can run profile deletion or window shutdown.
            if (
                self._closing
                or sip.isdeleted(profile)
                or (page is not None and sip.isdeleted(page))
            ):
                return False
            if registry.site_permission_context(profile) != context:
                return False
            mode = dialog.remember.currentData()
            if mode != "once":
                store.remember(origin.toString(), feature, mode, **context)
            return True
        except (OSError, ValueError):
            if not self._closing:
                QMessageBox.warning(
                    self,
                    "Website Permission",
                    "Could not safely read or save this permission. Access was denied.",
                )
            return False

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

    def _toggle_current_tab_group(self):
        if 0 <= self.current_tab_index < len(self.tabs):
            group_id = self.tabs[self.current_tab_index].group_id
            if group_id in self.tab_manager.groups:
                self.toggle_group(group_id)

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
        tab.browser_window._take_back_tab(tab)
        window = tab.browser_window
        window._return_agent_sidebar(tab)
        if window.web_chat_dock and window.web_chat_dock.view is tab:
            window.web_chat_dock.release()
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
        if self.data_panels_dock is not None:
            self.data_panels_dock.refresh()
        if self.file_library_dialog is not None:
            self.file_library_dialog.reject()
        if self.agent_sidebar is not None:
            self.agent_sidebar.detach()
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
            self._start_page_changed(self.profile_manager.current_profile_name)

    def _startup_page(self):
        home = self.config.get("home_page", NEW_TAB) or NEW_TAB
        if (
            not self.profile_manager.is_search_profile()
            and home in (NEW_TAB, "steed::newtab", "steed://newtab/")
            and not self.config.get("welcome_completed", False)
        ):
            return WELCOME
        return home

    def _start_page_changed(self, profile):
        if self._closing or profile != self.profile_manager.current_profile_name:
            return
        for tab in self.tabs:
            if page_name(tab.url()) is not None:
                tab.reload()

    def _customize_new_tab(self):
        from ui.start_page_dialog import StartPageDialog

        StartPageDialog(
            self.profile_manager.registry.start_pages,
            self.profile_manager.current_profile_name,
            self,
        ).exec()

    def _start_news_changed(self, profile):
        if self._closing or profile != self.profile_manager.current_profile_name:
            return
        pages = self.profile_manager.registry.start_pages
        if pages.state(profile)["html"]:
            return
        body = json.dumps(pages.news_section(profile))
        for tab in self.tabs:
            if page_name(tab.url()) == "newtab":
                tab.page().runJavaScript(
                    "{const card = document.getElementById('news'); if (card) card.outerHTML = "
                    + body
                    + ";}"
                )

    def _bookmark_current(self):
        if not self.tabs:
            return
        tab = self.tabs[self.current_tab_index]
        self._edit_bookmark(tab.url().toString(), tab.title())

    def _edit_bookmark(self, url="", title=""):
        from ui.bookmarks_dialog import BookmarkDialog

        BookmarkDialog(
            self.profile_manager.registry.start_pages,
            self.profile_manager.current_profile_name,
            self,
            url,
            title,
        ).exec()

    def _manage_bookmarks(self):
        from ui.bookmarks_dialog import BookmarksDialog

        BookmarksDialog(
            self.profile_manager.registry.start_pages,
            self.profile_manager.current_profile_name,
            self,
        ).exec()

    def _start_page_action(self, tab, url):
        if self._closing or tab not in self.tabs or page_name(tab.url()) is None:
            return
        pages = self.profile_manager.registry.start_pages
        profile = self.profile_manager.current_profile_name
        if pages.state(profile)[
            "html" if page_name(tab.url()) == "newtab" else "welcome_html"
        ]:
            return
        if url.userInfo() or url.port() != -1 or url.hasFragment():
            return
        action = url.path().removeprefix("/")
        params = parse_qsl(
            url.query(QUrl.ComponentFormattingOption.FullyEncoded),
            keep_blank_values=True,
        )
        if action == "search" and len(params) == 1 and params[0][0] == "q":
            text = params[0][1].strip()
            if text and len(text) <= 4096:
                self._navigate_tab_to_url(tab, text)
        elif action == "reopen" and len(params) == 1 and params[0][0] == "id":
            self._restore_closed_tab(params[0][1])
        elif not params:
            if action == "customize":
                self._customize_new_tab()
            elif action == "bookmark":
                self._edit_bookmark()
            elif action == "bookmarks":
                self._manage_bookmarks()
            elif action == "agents":
                self._toggle_agent_sidebar(True)
            elif action == "library":
                self._open_file_library()
            elif action == "start":
                if not self.profile_manager.is_search_profile():
                    self.config.set("welcome_completed", True)
                self._navigate_tab_to_url(tab, NEW_TAB)
            elif action == "refresh-news":
                pages.news.forget(profile)
                self._start_news_changed(profile)

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
