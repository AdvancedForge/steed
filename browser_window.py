"""Main browser window."""

import json
import logging
import sqlite3
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qsl
from uuid import uuid4

from PyQt6 import QtWebEngineCore, sip
from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QKeySequence, QPalette
from PyQt6.QtWebEngineCore import (
    QWebEngineFullScreenRequest,
    QWebEnginePage,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import webengine
from config import DEFAULT_SEARCH_ENGINE, Config
from cookie_manager import CookieManager
from layout_preferences import (
    LayoutConflict,
    LayoutPreferences,
    preferences_from_config,
)
from profile_manager import ProfileManager
from services.permission_service import PermissionService
from services.site_permissions import FEATURES
from services.start_pages import (
    NEW_TAB,
    WELCOME,
    display_url,
    page_name,
    workspace_name,
)
from site_settings import site_key
from tab_manager import GROUP_COLORS, TabManager, TabState
from ui import media_controls
from ui.address_bar import AddressBar
from ui.chrome_style import bar_stylesheet, chrome_colors
from ui.devtools import DevToolsHost, DevToolsUnavailable
from ui.find_bar import FindBar
from ui.layout_dialog import LayoutDialog
from ui.media_dialog import DesktopMediaDialog
from ui.prompts import InputDialog as QInputDialog
from ui.prompts import MessageBox as QMessageBox
from ui.settings_dialog import SettingsDialog
from ui.site_permissions import SitePermissionDialog
from ui.tab_strip import TabStrip
from webengine import (
    SiteCompatibilityInterceptor,
    configure_web_settings,
    google_login_user_agent,
)

logger = logging.getLogger(__name__)
# How often a profile's open windows are recorded (only when they changed).
SESSION_SNAPSHOT_MS = 10 * 60 * 1000

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
        if (
            is_main_frame
            and navigation_type
            == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            and webengine.is_local(self.url())
            and url.scheme() in ("http", "https")
        ):
            # A local page may not reach the web, and the engine counts a
            # download its link starts as reaching it, so such a link would do
            # nothing. Open it the way a typed address opens instead.
            tab = self.parent()
            QTimer.singleShot(0, lambda: sip.isdeleted(tab) or tab.setUrl(url))
            return False
        if is_main_frame:
            window = getattr(self.parent(), "browser_window", None)
            if window is not None:
                # Before the new document loads, so its settings apply to it.
                window._apply_site_settings(self, url)
            assistant = getattr(window, "password_assistant", None)
            if assistant is not None:
                # Collect a login its form is submitting before the page goes.
                assistant.navigating(self.parent())
        if is_main_frame and workspace_name(url.toString()):
            tab = self.parent()
            window = getattr(tab, "browser_window", None)
            if (
                window is not None
                and page_name(self.url()) is not None
                and navigation_type
                == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
                and not window.profile_manager.registry.start_pages.state(
                    window.profile_manager.current_profile_name
                )["html" if page_name(self.url()) == "newtab" else "welcome_html"]
            ):
                QTimer.singleShot(
                    0, lambda: window._navigate_tab_to_url(tab, url.toString())
                )
            return False
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
        self.pinned = False
        self.native_panel = None
        self.reader_panel = None
        self.workspace_name = None
        self.workspace_controller = None
        self.load_progress = 100
        self.is_loading = False
        self.load_succeeded = None
        self.tab_id = uuid4().hex
        # A restored tab not loaded yet: its address and title until selected.
        self.pending_url = None
        self.pending_title = ""
        self.pending_scroll = None
        # Runtime references must stay distinct when windows restore saved IDs.
        self.agent_tab_id = uuid4().hex
        page = BrowserPage(
            profile, self, config=window.config if window is not None else None
        )
        self.setPage(page)
        settings = self.settings()
        configure_web_settings(settings)
        # Local pages must not be able to read remote content. Nothing in the
        # browser needs it: the bundled codec fixture is self-contained.
        webengine.set_options(
            settings,
            insecure_content=False,
            local_files_reach_web=False,
            screen_capture=True,
        )

        if initial_qurl is not None and initial_qurl.isValid():
            self.setUrl(initial_qurl)

    def url(self) -> QUrl:
        return self.pending_url if self.pending_url is not None else super().url()

    def title(self) -> str:
        return self.pending_title if self.pending_url is not None else super().title()

    def load_pending(self) -> bool:
        """Load a lazily restored tab; True when it was waiting."""
        if self.pending_url is None:
            return False
        url, self.pending_url = self.pending_url, None
        self.setUrl(url)
        return True

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        native = webengine.menu_action(self.page(), "inspect_element")
        if native in menu.actions():
            menu.removeAction(native)
        if self.browser_window is not None and webengine.is_local(self.url()):
            self.browser_window._local_download_actions(
                menu, self.page(), self.lastContextMenuRequest()
            )
        if self.browser_window is not None and not self.workspace_name:
            menu.addSeparator()
            menu.addAction("Inspect").triggered.connect(
                lambda: self.browser_window._inspect_page_element(self)
            )
        if self.browser_window is not None:
            self.browser_window._show_html_menu(menu, event.globalPos(), context=True)
        else:
            menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            menu.popup(event.globalPos())

    def createWindow(self, window_type: QWebEnginePage.WebWindowType) -> "BrowserTab":
        return self.browser_window._new_tab(return_tab=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.native_panel is not None:
            self.native_panel.setGeometry(self.rect())
        if self.reader_panel is not None:
            self.reader_panel.setGeometry(self.rect())


# One-time tokens for tabs being dragged out of a strip: token -> (window, tab).
# Another window's strip can only claim a tab through a token issued here.
_TAB_DRAGS: dict = {}


SEARCH_NAMES = {
    "duckduckgo": "DuckDuckGo",
    "google": "Google",
    "bing": "Bing",
    "startpage": "Startpage",
    "kagi": "Kagi",
    "search.brave": "Brave Search",
    "ecosia": "Ecosia",
}


def search_engine_name(config):
    host = QUrl(config.get("search_engine", DEFAULT_SEARCH_ENGINE)).host().lower()
    host = host.removeprefix("www.")
    for key, name in SEARCH_NAMES.items():
        if host.startswith(key + "."):
            return name
    return host or "the web"


def unique_keys(*keys):
    """Distinct key sequences from text and platform standard keys."""
    sequences = []
    for key in keys:
        found = (
            QKeySequence.keyBindings(key)
            if isinstance(key, QKeySequence.StandardKey)
            else [QKeySequence(key)]
        )
        for sequence in found:
            if not sequence.isEmpty() and sequence not in sequences:
                sequences.append(sequence)
    return sequences


class BrowserWindow(QMainWindow):
    """Main browser window."""

    def __init__(
        self, profile_name: str | None = None, restore_states: list | None = None
    ):
        super().__init__()
        self.browser_window_id = uuid4().hex
        # The window that opened this one; None for the application's root window.
        self._opener = None
        # Closing as part of quitting keeps this window in the saved session.
        self._keep_session = False
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
        self.chatbot_relay_dialog = None
        self.file_library_dialog = None
        self.current_tab_index = 0
        self._restoring_tabs = False
        self.closed_tabs = self.profile_manager.registry.closed_tabs
        self._child_windows: list[BrowserWindow] = []
        self._palette = None
        self.agent_sidebar = None
        self.data_panels_dock = None
        self.app_panel = None
        self.agent_setup_dialog = None
        self.settings_dialog = None
        self._menu_anchor = None
        self._html_menu = None
        self._html_dialogs = []
        self._newtab_hover = None
        self._newtab_hover_opened = False
        self._newtab_hover_since = 0
        self._newtab_hover_pending = False
        self._newtab_hover_timer = QTimer(self)
        self._newtab_hover_timer.setInterval(250)
        self._newtab_hover_timer.timeout.connect(self._poll_newtab_menu)
        self.setup_window()
        self.setup_menus()
        self.setup_ui()
        self.apply_dark_mode()
        # Manual layout is presentation only and is shared by every window.
        self.layout_preferences = preferences_from_config(self.config)
        self._layout_editing = False
        self.apply_layout(self.layout_preferences)
        self.profile_manager.registry.layout_changed.connect(self._layout_committed)
        self.profile_manager.registry.settings_changed.connect(self._settings_changed)

        self._was_fullscreen = False
        self._pending_desktop_request = None
        self._tab_save_pending = False
        self._saved_window_state = None
        self._saved_geometry = None
        self._saved_vis = {}
        self._closing = False

        # Connect profile signals
        self.profile_manager.profile_changed.connect(self._on_profile_changed)
        self.profile_manager.registry.site_settings_changed.connect(
            lambda profile, host: (
                profile == self.profile_manager.current_profile_name
                and self._site_settings_changed(host)
            )
        )
        self.profile_manager.registry.page_style.changed.connect(
            self._appearance_changed
        )
        self.profile_manager.profiles_updated.connect(self._update_profile_shortcuts)
        self.profile_manager.registry.start_pages.changed.connect(
            self._start_page_changed
        )
        self._apply_page_style()
        self.profile_manager.registry.start_pages.news_changed.connect(
            self._start_news_changed
        )
        # The initial profile was selected before the menu/signals existed.
        self._update_profile_shortcuts()
        # Initialize profile and managers
        self._initialize_profile(self.profile_manager.current_profile_name)
        # Restore or create initial tab
        if restore_states is not None:
            self._restore_states(restore_states)
        elif self.config.get("restore_tabs_on_startup", True):
            self._restore_tabs(reopen_windows=True)
        else:
            self._new_tab(url=self._startup_page())
        self._newtab_hover_timer.start()

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
        self.tab_manager = TabManager(
            profile_path, self, window_id=self.browser_window_id
        )
        profile = self.profile_manager.get_profile(profile_name)

        registry = self.profile_manager.registry
        registry.ensure_inspection(profile_name)
        snapshot = registry.snapshots.get(profile_name)
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
        self.saved_groups_menu = tabs_menu.addMenu("Saved Groups")
        self.saved_groups_menu.aboutToShow.connect(self._fill_saved_groups_menu)
        reopen_window = QAction("Reopen Closed Window", self)
        reopen_window.triggered.connect(self.reopen_closed_window)
        tabs_menu.addAction(reopen_window)
        history_action = QAction("Session History…", self)
        history_action.triggered.connect(self._open_session_history)
        tabs_menu.addAction(history_action)
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(SESSION_SNAPSHOT_MS)
        self._session_timer.timeout.connect(lambda: self._record_session("snapshot"))
        self._session_timer.start()
        save_all = QAction("Save All Tabs as Group…", self)
        save_all.triggered.connect(lambda: self.save_all_tabs_as_group())
        tabs_menu.addAction(save_all)
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
        self.back_action = QAction("Back", self)
        # A key registered twice is ambiguous and fires nothing, and the
        # platform's Back key is often Alt+Left itself.
        self.back_action.setShortcuts(
            unique_keys("Alt+Left", QKeySequence.StandardKey.Back)
        )
        self.back_action.triggered.connect(self._go_back)
        tabs_menu.addAction(self.back_action)
        self.forward_action = QAction("Forward", self)
        self.forward_action.setShortcuts(
            unique_keys("Alt+Right", QKeySequence.StandardKey.Forward)
        )
        self.forward_action.triggered.connect(self._go_forward)
        tabs_menu.addAction(self.forward_action)
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
        history_action = cookies_menu.addAction("Browsing History…")
        history_action.setShortcut(QKeySequence("Ctrl+H"))
        history_action.triggered.connect(lambda: self._open_management("history"))
        clear_history_action = QAction("Clear Browsing History...", self)
        clear_history_action.triggered.connect(self._clear_browsing_history)
        cookies_menu.addAction(clear_history_action)
        cookies_menu.addSeparator()
        passwords_action = QAction("Passwords...", self)
        passwords_action.triggered.connect(
            lambda: self._new_tab(url="steed:passwords", return_tab=True)
        )
        general_menu.addAction(passwords_action)
        fill_login_action = QAction("Fill Login", self)
        fill_login_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        fill_login_action.triggered.connect(
            lambda: self.password_assistant.fill_current()
        )
        general_menu.addAction(fill_login_action)
        downloads_action = QAction("Downloads...", self)
        downloads_action.setShortcut(QKeySequence("Ctrl+J"))
        downloads_action.triggered.connect(lambda: self._open_management("downloads"))
        general_menu.addAction(downloads_action)
        keywords_action = QAction("Search Keywords…", self)
        keywords_action.triggered.connect(self._open_search_keywords)
        general_menu.addAction(keywords_action)
        scripts_action = QAction("User Scripts && Styles…", self)
        scripts_action.triggered.connect(
            lambda: self._new_tab(url="steed:userscripts", return_tab=True)
        )
        general_menu.addAction(scripts_action)
        adblock_action = QAction("Ad Blocking...", self)
        adblock_action.triggered.connect(lambda: self._open_management("adblock"))
        general_menu.addAction(adblock_action)
        general_menu.addSeparator()
        settings_action = QAction("Settings...", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        general_menu.addAction(settings_action)
        style_action = QAction("Page Style…", self)
        style_action.triggered.connect(self._open_page_style)
        view_menu.addAction(style_action)
        shots = view_menu.addMenu("Take Screenshot")
        for label, whole, copy, key in (
            ("Save Full Page…", True, False, "Ctrl+Shift+S"),
            ("Save Visible Area…", False, False, ""),
            ("Copy Full Page", True, True, ""),
            ("Copy Visible Area", False, True, ""),
        ):
            action = shots.addAction(label)
            if key:
                action.setShortcut(QKeySequence(key))
                self.addAction(action)
            action.triggered.connect(
                lambda _=False, whole=whole, copy=copy: self.take_screenshot(
                    whole, copy
                )
            )
        media_action = QAction("Play or Pause Media", self)
        media_action.triggered.connect(lambda: media_controls.controller().toggle())
        view_menu.addAction(media_action)
        mini_action = QAction("Mini Player", self)
        mini_action.triggered.connect(lambda: self.open_mini_player())
        view_menu.addAction(mini_action)
        self.reader_action = QAction("Reader View", self)
        self.reader_action.setShortcut(QKeySequence("F9"))
        self.reader_action.triggered.connect(self._toggle_reader)
        view_menu.addAction(self.reader_action)
        self.addAction(self.reader_action)
        memory_action = QAction("Memory && Performance…", self)
        memory_action.triggered.connect(
            lambda: self._new_tab(url="steed:memory", return_tab=True)
        )
        view_menu.addAction(memory_action)
        features_action = QAction("Engine && Features…", self)
        features_action.triggered.connect(
            lambda: self._new_tab(url="steed:features", return_tab=True)
        )
        view_menu.addAction(features_action)
        reset_style = QAction("Reset Page Style…", self)
        reset_style.setShortcut(QKeySequence("Ctrl+Alt+Shift+R"))
        reset_style.triggered.connect(self._reset_page_style)
        view_menu.addAction(reset_style)
        self.menu_bar_action = QAction("Show Top Menu Bar", self, checkable=True)
        self.menu_bar_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.menu_bar_action.triggered.connect(
            lambda checked: self._set_bar("menu_bar", checked)
        )
        self.status_bar_action = QAction("Show Bottom Status Bar", self, checkable=True)
        self.status_bar_action.triggered.connect(
            lambda checked: self._set_bar("status_bar", checked)
        )
        for action in (self.menu_bar_action, self.status_bar_action):
            view_menu.addAction(action)
            self.addAction(action)
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
        manage_bookmarks_action = QAction("Manage Bookmarks…", self)
        manage_bookmarks_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        manage_bookmarks_action.triggered.connect(self._manage_bookmarks)
        tabs_menu.addAction(manage_bookmarks_action)
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
        access_action = QAction("Access && Permissions…", self)
        access_action.triggered.connect(
            lambda: self._new_tab(url="steed:access", return_tab=True)
        )
        agents_menu.addAction(access_action)
        task_action = QAction("Task Permissions...", self)
        task_action.triggered.connect(self._open_tasks)
        agents_menu.addAction(task_action)
        self.auto_approval_action = QAction(
            "YOLO mode: auto-approve agent requests (unsafe)", self
        )
        self.auto_approval_action.setCheckable(True)
        self.auto_approval_action.toggled.connect(self._toggle_auto_approval)
        agents_menu.addAction(self.auto_approval_action)
        self.auto_approval_notice = QLabel(
            "YOLO mode: agent requests auto-approved", self
        )
        self.auto_approval_notice.setStyleSheet("color:#cc5500;font-weight:bold;")
        if self.profile_manager.registry._tasks is not None:
            self.profile_manager.registry._tasks.changed.connect(
                self._refresh_auto_approval
            )
        self._refresh_auto_approval()
        site_action = QAction("Website Permissions...", self)
        site_action.triggered.connect(lambda: self._open_management("permissions"))
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
        self.app_panel_action = QAction("App Panel", self)
        self.app_panel_action.setCheckable(True)
        self.app_panel_action.triggered.connect(self._toggle_app_panel)
        view_menu.addAction(self.app_panel_action)
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
        web_chats_menu.addAction("Chatbot relay…").triggered.connect(
            lambda: self._open_chatbot_relay()
        )
        for label, key, mode in (
            ("Command Palette…", "Ctrl+Shift+P", ""),
            ("Search Tabs…", "Ctrl+Shift+E", "@"),
            ("Search History…", "Ctrl+Shift+Y", "#"),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(
                lambda _=False, mode=mode: self._open_palette(mode)
            )
            view_menu.addAction(action)
        for label, keys, step in (
            ("Next Tab", ("Ctrl+Tab", "Ctrl+PgDown"), 1),
            ("Previous Tab", ("Ctrl+Shift+Tab", "Ctrl+PgUp"), -1),
        ):
            action = QAction(label, self)
            action.setShortcuts([QKeySequence(key) for key in keys])
            action.triggered.connect(lambda _=False, step=step: self._cycle_tab(step))
            tabs_menu.addAction(action)
        # Ctrl+1–8 open that tab and Ctrl+9 the last one, as in Chrome.
        for number in range(1, 10):
            action = QAction(f"Go to Tab {number}", self)
            action.setShortcut(QKeySequence(f"Ctrl+{number}"))
            action.triggered.connect(lambda _=False, n=number: self._jump_to_tab(n))
            self.addAction(action)
        devtools_menu = view_menu.addMenu("Developer Tools")
        for label, shortcuts, handler in (
            ("Toggle Developer Tools", ("F12", "Ctrl+Shift+I"), self._toggle_devtools),
            ("JavaScript Console", ("Ctrl+Shift+J",), self._devtools_console),
            ("Inspect Element", ("Ctrl+Shift+C",), self._devtools_pick),
        ):
            action = QAction(label, self)
            action.setShortcuts([QKeySequence(key) for key in shortcuts])
            action.triggered.connect(handler)
            devtools_menu.addAction(action)
            self.addAction(action)
        devtools_menu.addSeparator()
        for label, side in (
            ("Dock to Right", "right"),
            ("Dock to Bottom", "bottom"),
            ("Separate Window", "window"),
        ):
            devtools_menu.addAction(label).triggered.connect(
                lambda _=False, side=side: self.devtools.set_side(side)
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

        def register_actions(menu):
            # Hidden menu bars must not disable ordinary browsing shortcuts.
            for action in menu.actions():
                self.addAction(action)
                if action.menu() is not None:
                    register_actions(action.menu())

        for menu in self._layout_menus.values():
            register_actions(menu)
        editors = {
            "Downloads",
            "Ad Blocking",
            "Settings",
            "Page Style",
            "Passwords",
            "Website Permissions",
            "Layout Settings",
            "Customize New Tab",
            "Manage Bookmarks",
            "Bookmark This Page",
            "Task Permissions",
            "App Connections",
            "Agent File Library",
            "Agent Connection Setup",
            "New Profile",
            "Switch Profile",
            "Rename Profile",
            "Delete Profile",
            "Pop out Profile",
            "Manage Tab Groups",
            "View Cookies",
        }
        for action in self.actions():
            if action.text().replace("...", "").replace("…", "").strip() in editors:
                action.setProperty("steed_editor", True)

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        # Side tab strips sit beside the whole browser column, so the column
        # (address bar, bars, page) lives inside a horizontal row.
        self.chrome_row = QHBoxLayout(central)
        self.chrome_row.setContentsMargins(0, 0, 0, 0)
        self.chrome_row.setSpacing(0)
        self.chrome_column = QWidget(central)
        layout = QVBoxLayout(self.chrome_column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.chrome_layout = layout
        self.chrome_row.addWidget(self.chrome_column, 1)
        # Tab strip. The native one needs no renderer process; switching applies
        # to windows opened afterwards.
        import memory_modes
        from ui.interface_mode import native

        self._memory = memory_modes.apply_process_policies(self.config)
        QTimer.singleShot(0, self._apply_memory_policies)
        import page_unload

        page_unload.ENABLED = bool(self.config.get("save_before_closing", True))
        # Mouse back/forward side buttons over any part of this window.
        QApplication.instance().installEventFilter(self)
        if len(self.profile_manager.registry.owners) == 1:
            QTimer.singleShot(1500, self._offer_old_pairing_cleanup)
            QTimer.singleShot(2500, self._warn_codecs_replaced)
        memory_modes.monitor().changed.connect(self._memory_pressure_changed)
        memory_modes.monitor().start()
        strip = TabStrip
        if native():
            from ui.native_tab_strip import NativeTabStrip

            strip = NativeTabStrip
        self.tab_bar = strip(self.chrome_column)
        self.tab_bar.new_tab_requested.connect(self._new_tab)
        self.tab_bar.tab_changed.connect(self._switch_tab)
        self.tab_bar.tab_close_requested.connect(self._close_tab)
        self.tab_bar.tab_moved.connect(self._on_tab_moved)
        self.tab_bar.group_rename_requested.connect(self._handle_group_rename_request)
        self.tab_bar.group_set_requested.connect(self._set_tab_group_existing)
        self.tab_bar.group_clear_requested.connect(self._clear_tab_group)
        self.tab_bar.group_toggle_requested.connect(self._toggle_named_group)
        self.tab_bar.context_requested.connect(self._show_tab_menu)
        self.tab_bar.tab_hovered.connect(self._show_tab_preview)
        self.tab_bar.hover_ended.connect(self._hide_tab_preview)
        self.tab_bar.group_context_requested.connect(self._show_group_menu)
        self.tab_bar.mute_toggled.connect(self._toggle_tab_mute)
        self.tab_bar.drag_started.connect(self._tab_drag_started)
        self.tab_bar.tab_received.connect(self._tab_dropped_here)
        self.tab_bar.detach_requested.connect(self._tear_off_tab)
        self.tab_bar.width_chosen.connect(self._commit_tab_width)
        layout.addWidget(self.tab_bar)
        # Address bar
        self.address_bar = AddressBar()
        self.address_bar.url_changed.connect(self._navigate_to_url)
        self.address_bar.tab_switch_requested.connect(self._switch_to_tab_key)
        self.address_bar.tab_source = self._suggestion_tabs
        self.address_bar.back_requested.connect(self._go_back)
        self.address_bar.forward_requested.connect(self._go_forward)
        self.address_bar.reload_requested.connect(self._reload)
        self.address_bar.home_requested.connect(self._go_home)
        self.address_bar.passwords_requested.connect(
            lambda: self.password_assistant.show_menu()
        )
        self.address_bar.reset_zoom_requested.connect(lambda: self._zoom())
        self.address_bar.site_menu_requested.connect(self._show_site_menu)
        self.address_bar.downloads_requested.connect(
            lambda: self._open_management("downloads")
        )
        self.address_bar.media_requested.connect(self._show_media_menu)
        self._downloads_recent = QTimer(self)
        self._downloads_recent.setSingleShot(True)
        self._downloads_recent.setInterval(30000)
        self._downloads_recent.timeout.connect(self._downloads_changed)
        self.download_manager.changed.connect(self._downloads_changed)
        self.download_manager.finished.connect(self._download_finished)
        self.browser_menu = QMenu(self)
        for menu in self._layout_menus.values():
            self.browser_menu.addMenu(menu)
        self.address_bar.menu_btn.setMenu(self.browser_menu)
        layout.addWidget(self.address_bar)
        self.loading_indicator = QProgressBar()
        self.loading_indicator.setRange(0, 100)
        self.loading_indicator.setAccessibleName("Page loading progress")
        self.loading_indicator.hide()
        layout.addWidget(self.loading_indicator)
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
        # Pages share a splitter with docked Developer Tools.
        from ui.password_assistant import PasswordAssistant

        self.password_assistant = PasswordAssistant(self)
        layout.addWidget(self.password_assistant.bar)
        self.content_area = QSplitter(Qt.Orientation.Horizontal)
        self.content_area.setChildrenCollapsible(False)
        self.content_area.addWidget(self.tab_container)
        layout.addWidget(self.content_area, 1)
        self.devtools = DevToolsHost(self, self.content_area)
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.addPermanentWidget(self.auto_approval_notice)
        # Permission and download signals are connected per tab in _new_tab

    def apply_dark_mode(self):
        styles = self.profile_manager.registry.page_style
        colors = styles.get(self.profile_manager.current_profile_name)["colors"]
        if colors:
            from services.page_style import palette

            chosen = {
                **palette(styles.dark(self.profile_manager.current_profile_name)),
                **colors,
            }
            self.setStyleSheet(
                "QWidget {background:" + chosen["bg"] + ";color:" + chosen["ink"] + ";}"
                "QLineEdit, QComboBox, QPushButton {background:"
                + chosen["surface"]
                + ";color:"
                + chosen["ink"]
                + ";border:1px solid "
                + chosen["border"]
                + ";padding:4px 8px;}"
                "QPushButton:hover {background:" + chosen["soft"] + ";}"
                "QMenu::item:selected {background:" + chosen["soft"] + ";}"
            )
        elif self.profile_manager.registry.page_style.dark(
            self.profile_manager.current_profile_name
        ):
            self.setStyleSheet("""
                QMainWindow {background-color:#1e1e1e;color:#fff;}
                QWidget {background-color:#1e1e1e;color:#fff;}
                QLineEdit {background:#2d2d2d;color:#fff;border:1px solid #3d3d3d;padding:4px;}
                QPushButton {background:#2d2d2d;color:#fff;border:1px solid #3d3d3d;padding:4px 8px;}
                QPushButton:hover {background:#3d3d3d;}
                QPushButton:pressed {background:#1d1d1d;}
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
        styles = self.profile_manager.registry.page_style
        profile = self.profile_manager.current_profile_name
        dark = styles.dark(profile)
        if colors:
            toolbar = chosen["bg"]
        elif dark:
            toolbar = "#1e1e1e"
        else:
            toolbar = QApplication.palette().color(QPalette.ColorRole.Window).name()
        self.tab_bar.set_chrome(toolbar, dark)
        colors = chrome_colors(toolbar, dark)
        self.address_bar.apply_style(colors)
        bars = bar_stylesheet(colors)
        self.menuBar().setStyleSheet(bars)
        self.status_bar.setStyleSheet(bars)
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
        self.tab_bar.apply_appearance(preferences.tabs)
        self._place_tab_strip(preferences.tabs["position"])
        self._apply_sidebar_layout()
        self._apply_layout_panels()
        self._apply_layout_text()

    def _place_tab_strip(self, position):
        """Move the tab strip to an edge without recreating it."""
        for layout in (self.chrome_layout, self.chrome_row):
            if layout.indexOf(self.tab_bar) >= 0:
                layout.removeWidget(self.tab_bar)
        if position in ("left", "right"):
            self.tab_bar.setParent(self.centralWidget())
            index = 0 if position == "left" else self.chrome_row.count()
            self.chrome_row.insertWidget(index, self.tab_bar)
        else:
            self.tab_bar.setParent(self.chrome_column)
            index = (
                0
                if position == "top"
                else self.chrome_layout.indexOf(self.content_area) + 1
            )
            self.chrome_layout.insertWidget(index, self.tab_bar)
        if not getattr(self, "_was_fullscreen", False):
            self.tab_bar.show()

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
        layout = self.chrome_layout
        for panel in getattr(self, "_layout_panels", []):
            layout.removeWidget(panel)
            panel.hide()
            panel.deleteLater()
        self._layout_panels = []
        for placement in ("top", "bottom"):
            position = (
                layout.indexOf(self.address_bar) + 1
                if placement == "top"
                else layout.indexOf(self.content_area) + 1
            )
            for definition in self.layout_preferences.panels:
                if definition["placement"] != placement:
                    continue
                panel = QuickActionsPanel(
                    definition, self._native_layout_actions, self.chrome_column
                )
                self._layout_panels.append(panel)
                layout.insertWidget(position, panel)
                position += 1

    def _open_layout_settings(self):
        return self._open_management("layout")

    def _layout_controller(self):
        if self._layout_editing:
            raise ValueError("Finish the open layout editor first.")
        saved = preferences_from_config(self.config)
        dialog = LayoutDialog(saved, self)
        dialog.layout_changed.connect(self.apply_layout)
        self._layout_editing = True

        def finish(result):
            try:
                if result == QDialog.DialogCode.Accepted:
                    self.profile_manager.registry.commit_layout(
                        dialog.preferences, base=saved
                    )
            except LayoutConflict:
                QMessageBox.warning(
                    self,
                    "Layout changed in another window",
                    "Your settings were not saved because another window changed the same setting. Reopen Layout Settings to try again.",
                )
            finally:
                self._layout_editing = False
                self.apply_layout(preferences_from_config(self.config))

        dialog.finished.connect(finish)
        return dialog

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
        for host in list(self._html_dialogs):
            host.reject()
        if self._html_menu is not None:
            self._html_menu.close()
        self._apply_page_style()
        self.apply_dark_mode()
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

    def _ensure_app_panel(self):
        if self.app_panel is None:
            from ui.app_panel import AppPanelDock

            self.app_panel = AppPanelDock(self)
            self.addDockWidget(self._sidebar_dock_area(), self.app_panel)
            self.app_panel.visibilityChanged.connect(self.app_panel_action.setChecked)
        return self.app_panel

    def _toggle_app_panel(self, visible):
        if self._closing:
            return
        if visible:
            panel = self._ensure_app_panel()
            panel.refresh()
            panel.show()
            panel.raise_()
        elif self.app_panel is not None:
            self.app_panel.hide()

    def _show_panel_app(self, identity):
        """Open a panel app in this window's App Panel."""
        if not self._closing:
            self._ensure_app_panel().show_app(identity)

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

    def _refresh_auto_approval(self):
        service = self.profile_manager.registry._tasks
        enabled = bool(service and service.auto_approve_requests)
        self.auto_approval_action.blockSignals(True)
        self.auto_approval_action.setChecked(enabled)
        self.auto_approval_action.blockSignals(False)
        self.auto_approval_notice.setVisible(enabled)
        if hasattr(self, "address_bar"):
            self.address_bar.set_menu_badge("YOLO" if enabled else "")
            self.address_bar.menu_btn.setToolTip(
                "Browser menu · unsafe agent auto-approval is enabled"
                if enabled
                else "Browser menu"
            )

    def _toggle_auto_approval(self, enabled):
        if (
            enabled
            and QMessageBox.warning(
                self,
                "Enable unsafe agent auto-approval?",
                "YOLO mode automatically approves Steed requests from every connected agent, "
                "across profiles, for this browser run. Agents can take control of tabs, "
                "send or change account data, run generated packages and apply page/layout changes "
                "without another approval. This may be unsafe: websites and agent output can "
                "contain malicious instructions.\n\nOnly enable this for agents you trust. "
                "You can turn it off in Agents; Take Back and Cancel still work. It does not "
                "connect accounts, enable library folders, bypass validation or replay uncertain effects.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            self._refresh_auto_approval()
            return
        try:
            self.profile_manager.registry.tasks.set_auto_approval(enabled)
        except (RuntimeError, ValueError, sqlite3.Error, OSError):
            QMessageBox.warning(
                self,
                "Agent approvals",
                "The permission mode could not be changed; check Task Permissions and journal status.",
            )
        self._refresh_auto_approval()

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

    def _open_chatbot_relay(self, target=None):
        from ui.chatbot_relay import ChatbotRelayDialog

        if self.chatbot_relay_dialog is None:
            dialog = ChatbotRelayDialog(self, target)
            self.chatbot_relay_dialog = dialog
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.finished.connect(lambda: setattr(self, "chatbot_relay_dialog", None))
        self.chatbot_relay_dialog.show()
        return self.chatbot_relay_dialog

    def _open_agent_setup(self):
        return self._open_management("agent-setup")

    def _open_file_library(self):
        return self._open_management("library")

    def _update_window_title(self, url=None):
        profile_name = self.profile_manager.current_profile_name
        label = (
            "Search Profile (Incognito)" if profile_name == "search" else profile_name
        )
        title = f"Custom Browser - {label}"
        if url is None and 0 <= self.current_tab_index < len(self.tabs):
            url = self.tabs[self.current_tab_index].url()
        # Desktop password managers' auto-type can match this host. Page titles must not
        # be able to masquerade as another site's window-title association.
        if url is not None and url.scheme() in ("http", "https") and url.host():
            title += f" - {url.host()}"
        self.setWindowTitle(title)

    def _switch_profile(self):
        self._open_settings("profile", intent="switch")

    def _switch_to_profile(self, profile_name: str):
        try:
            self.profile_manager.registry.check_user_profile(profile_name)
        except ValueError as error:
            QMessageBox.warning(self, "Profile in use", str(error))
            return
        self.find_bar.close_find(clear_query=True)
        self.find_bar.set_page(None)
        # Save current tabs, then leave that profile's session.
        if self.tab_manager:
            self._save_current_tabs()
            self.tab_manager.release()
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
        self._open_settings("profile", intent="create")

    def _rename_profile(self):
        self._open_settings("profile", intent="rename")

    def _delete_profile(self):
        self._open_settings("profile", intent="archive")

    def _pop_out_profile(self):
        self._open_settings("profile", intent="open_window")

    def _pop_out_profile_named(self, profile: str):
        try:
            self.profile_manager.registry.check_user_profile(profile)
        except ValueError as error:
            QMessageBox.warning(self, "Profile in use", str(error))
            return
        self._open_window(profile)

    def _open_window(self, profile, states=None):
        """Open a window this one owns; states restores specific tabs."""
        win = BrowserWindow(profile_name=profile, restore_states=states)
        win._opener = self
        # Free a closed pop-out now instead of when its opener closes.
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win.show()
        self._child_windows = [w for w in self._child_windows if not sip.isdeleted(w)]
        self._child_windows.append(win)
        return win

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
        defer: bool = False,
        scroll: list[float] | None = None,
    ):
        """Open a tab; defer=True waits to load it until it is selected."""
        if not url:
            url = self.config.get("new_tab_page", NEW_TAB) or NEW_TAB

        workspace = workspace_name(url)
        agent_panel = workspace == "agents"
        if workspace and workspace != "agents":
            existing = next(
                (t for t in self.tabs if t.workspace_name == workspace), None
            )
            if existing is not None:
                self._switch_tab(self.tabs.index(existing))
                return existing if return_tab else None
        source_tab = self.tabs[self.current_tab_index] if self.tabs else None
        if source_tab is not None and (
            source_tab.workspace_name or getattr(source_tab, "agent_panel", False)
        ):
            source_tab = next(
                (
                    t
                    for t in reversed(self.tabs)
                    if not t.workspace_name and not getattr(t, "agent_panel", False)
                ),
                None,
            )
        if agent_panel and self.agent_tab in self.tabs:
            self._switch_tab(self.tabs.index(self.agent_tab))
            return self.agent_tab if return_tab else None
        qurl = self._text_to_qurl("about:blank" if workspace else url)

        profile = self.profile_manager.current_profile
        tab = BrowserTab(profile, self.tab_container, self)
        tab.agent_source = source_tab if workspace else None
        self.tab_layout.addWidget(tab)
        self._connect_tab(tab)
        # Determine insertion index
        if index is None:
            index = self.current_tab_index + 1
        # Ordinary tabs never open inside the pinned block at the start.
        index = max(self._pinned_count(), min(index, len(self.tabs)))

        self.tabs.insert(index, tab)
        self.tab_bar.insert_tab(index, title or "New Tab")
        self._switch_tab(index)
        self._sync_tab_group_labels()

        if scroll:
            self._restore_scroll(tab, qurl, scroll)
        # Start loading only after signals and tab bookkeeping are ready.
        if defer and not workspace and not agent_panel:
            tab.pending_url = qurl
            tab.pending_title = title or qurl.toString()
        else:
            self.navigation.navigate(tab, qurl)
        if agent_panel:
            self._embed_agent_sidebar(tab)
        elif workspace:
            self._embed_workspace(tab, workspace)

        if not self._restoring_tabs:
            self._save_current_tabs()
        if return_tab:
            return tab

    def _close_tab(self, index: int, remember: bool = True):
        if not 0 <= index < len(self.tabs):
            return
        if len(self.tabs) == 1:
            # Closing the last tab closes the window and its profile session,
            # as in other browsers; the last window closing quits Steed.
            QTimer.singleShot(0, self.close)
            return
        import memory_trim

        memory_trim.schedule()
        active = self.tabs[self.current_tab_index]
        tab = self.tabs.pop(index)
        state = TabState(
            "steed:agents"
            if getattr(tab, "agent_panel", False)
            else (
                "steed:" + tab.workspace_name
                if tab.workspace_name
                else tab.url().toString() or tab.page().requestedUrl().toString()
            ),
            tab.title() or self.tab_bar.tab_text(index),
            tab.group,
            tab.tab_id,
            tab.group_id,
            tab.pinned,
            getattr(tab, "keep_loaded", False),
        )
        profile_name = self.profile_manager.current_profile_name
        if remember:
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
            if state.pinned:
                self._set_pinned(tab, True)
            if state.keep_loaded:
                self._set_keep_loaded(tab, True)
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
            and not getattr(previous, "workspace_name", None)
        ):
            self.tabs[index].agent_source = previous
        self.current_tab_index = index
        from time import monotonic

        for touched in (previous, self.tabs[index]):
            if touched is not None:
                touched.last_active = monotonic()
        # A frozen tab resumes; an unloaded one reloads its page; a restored
        # one loads for the first time.
        if not self.tabs[index].load_pending():
            webengine.wake(self.tabs[index].page())
        if self.web_chat_dock and self.web_chat_dock.view is self.tabs[index]:
            self.web_chat_dock.release()
        self.password_assistant.tab_changed()
        self.tab_bar.set_current_tab(index)
        self.devtools.show_for(self.tabs[index])

        # A small snapshot of the tab being left, for hover previews.
        if (
            previous is not None
            and previous is not self.tabs[index]
            and not previous.workspace_name
            and self.config.get("tab_previews", True)
        ):
            from ui.tab_preview import snapshot

            previous.preview = snapshot(previous)
        self._hide_tab_preview()
        # Selecting a tab shown in the Mini Player brings it back.
        mini = getattr(self.tabs[index], "mini_player", None)
        if mini is not None and not sip.isdeleted(mini):
            mini.close()
        # Hide all tabs
        for i, t in enumerate(self.tabs):
            if getattr(t, "mini_player", None) is not None:
                continue
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
            else (
                "steed:" + cur.workspace_name
                if cur.workspace_name
                else display_url(cur.url())
            )
        )
        self._update_history_buttons()
        self._update_window_title(cur.url())
        self.status_bar.showMessage(f"Tab {index + 1} of {len(self.tabs)}")
        self._refresh_live_control()
        self._update_loading_indicator()

    def eventFilter(self, watched, event):
        kind = event.type()
        if (
            kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease)
            and event.button()
            in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton)
            and isinstance(watched, QWidget)
            and not sip.isdeleted(self)
            and not self._closing
            and watched.window() is self
        ):
            if kind == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.BackButton:
                    self._go_back()
                else:
                    self._go_forward()
            return True
        return super().eventFilter(watched, event)

    def _update_history_buttons(self):
        """Back/forward follow the current tab's history after every navigation."""
        if self._closing or not (0 <= self.current_tab_index < len(self.tabs)):
            return
        tab = self.tabs[self.current_tab_index]
        # A steed: page or the agent panel always has somewhere to go back to:
        # Back leaves it for the page underneath.
        overlay = bool(tab.workspace_name or getattr(tab, "agent_panel", False))
        history = tab.history()
        back = overlay or history.canGoBack()
        forward = not overlay and history.canGoForward()
        # Only the buttons show availability. The shortcuts stay enabled: a
        # disabled one would swallow a key press made before history updates.
        self.address_bar.set_back_enabled(back)
        self.address_bar.set_forward_enabled(forward)

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
            self.find_bar.set_page(self._visible_page(tab) if page_available else None)

    @staticmethod
    def _visible_page(tab):
        return (
            tab.workspace_controller.surface.page if tab.workspace_name else tab.page()
        )

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
                and not tab.workspace_name
                and tab.url().scheme() in ("http", "https")
            ]
        else:
            tabs = [self.tabs[self.current_tab_index]] if self.tabs else []
            if tabs and tabs[0].workspace_name:
                raise ValueError("Select a browsing tab before delegating it")
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
        if tab not in self.tabs or self._closing:
            return
        tab.workspace_exit_token = None
        self._take_back_tab(tab)
        workspace = workspace_name(url_text)
        if workspace and workspace != "agents":
            existing = next(
                (t for t in self.tabs if t.workspace_name == workspace), None
            )
            if existing is not None:
                self._switch_tab(self.tabs.index(existing))
                return
        if workspace:
            if workspace == "agents":
                if self.agent_tab in self.tabs:
                    self._switch_tab(self.tabs.index(self.agent_tab))
                    return
                self._detach_workspace(tab)
                self.navigation.navigate(tab, QUrl("about:blank"))
                self._embed_agent_sidebar(tab)
            else:
                if getattr(tab, "agent_panel", False):
                    self._return_agent_sidebar(tab)
                self._detach_workspace(tab)
                self.navigation.navigate(tab, QUrl("about:blank"))
                self._embed_workspace(tab, workspace)
            self._schedule_tab_save()
            return
        self._detach_workspace(tab)
        if getattr(tab, "agent_panel", False):
            self._return_agent_sidebar(tab)
        self.navigation.navigate(tab, self._text_to_qurl(url_text))
        self._update_page_actions()
        self._update_zoom()

    def _text_to_qurl(self, url_text: str) -> QUrl:
        import search_keywords

        keyword = search_keywords.search_url(url_text, self.config)
        if keyword is not None:
            return QUrl(keyword)
        search = self.config.get("search_engine", DEFAULT_SEARCH_ENGINE)
        return self.navigation.address_url(url_text, search)

    def _go_back(self):
        if self.tabs:
            self._take_back_current_tab()
            tab = self.tabs[self.current_tab_index]
            tab.workspace_exit_token = None
            self._detach_workspace(tab)
            if getattr(tab, "agent_panel", False):
                self._return_agent_sidebar(tab)
            self.navigation.action(tab, "back")
            self._update_page_actions()
            self._update_zoom()
            self._update_history_buttons()

    def _go_forward(self):
        if self.tabs:
            self._take_back_current_tab()
            tab = self.tabs[self.current_tab_index]
            tab.workspace_exit_token = None
            self._detach_workspace(tab)
            if getattr(tab, "agent_panel", False):
                self._return_agent_sidebar(tab)
            self.navigation.action(tab, "forward")
            self._update_page_actions()
            self._update_zoom()
            self._update_history_buttons()

    def _reload(self):
        if self.tabs:
            self._take_back_current_tab()
            tab = self.tabs[self.current_tab_index]
            if tab.workspace_name:
                host = tab.workspace_controller
                if getattr(getattr(host, "source", None), "page_unavailable", False):
                    name = tab.workspace_name
                    self._detach_workspace(tab)
                    self._embed_workspace(tab, name)
                else:
                    host.surface.reload()
            else:
                self.navigation.action(tab, "reload")

    def _open_find(self):
        if not self._closing and self.tabs:
            self.find_bar.set_page(
                self._visible_page(self.tabs[self.current_tab_index])
            )
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
        return self._open_management("tasks")

    def _open_app_connections(self):
        return self._open_management("connections")

    def _focus_page(self):
        if not self._closing and self.tabs:
            tab = self.tabs[self.current_tab_index]
            if tab.workspace_name:
                tab.workspace_controller.surface.view.setFocus()
            else:
                tab.setFocus()

    def _zoom(self, direction=0):
        if not self._closing and self.tabs:
            tab = self.tabs[self.current_tab_index]
            factor = self.navigation.zoom(self._visible_page(tab), direction)
            host = site_key(tab.url())
            if host and not tab.workspace_name:
                if direction == 0 and getattr(tab, "auto_zoomed", None):
                    # Resetting an automatic zoom turns it off for the site.
                    self._site_settings().set(host, "auto_zoom", "block")
                tab.auto_zoomed = None
                # Remembered for the site, as other browsers do.
                self._site_settings().set(host, "zoom", factor)
            self._update_zoom()

    def _site_settings(self):
        return self.profile_manager.registry.site_settings(
            self.profile_manager.current_profile_name
        )

    def _apply_site_settings(self, page, url):
        """JavaScript, autoplay and pop-ups for the site a page is going to."""
        if self._closing or sip.isdeleted(page):
            return
        host = site_key(url)
        values = self._site_settings().get(host)
        webengine.set_options(
            page.settings(),
            javascript=values["javascript"] == "allow",
            media_needs_gesture=values["autoplay"] == "block",
            # Off, Chromium's pop-up blocker stops windows opened without a click.
            scripts_open_windows=values["popups"] == "allow",
        )
        page.site_host = host

    def _apply_site_zoom(self, tab):
        """Each site keeps its zoom; a tab moving to another site takes that one."""
        host = site_key(tab.url())
        if host is None or tab.workspace_name or getattr(tab, "zoom_host", "") == host:
            return
        tab.zoom_host = host
        tab.auto_zoomed = None
        tab.setZoomFactor(self._site_settings().get(host)["zoom"])
        if self.tabs and tab is self.tabs[self.current_tab_index]:
            self._update_zoom()

    def _toggle_reader(self):
        """Show the current page's article in reader view, or leave it (F9)."""
        from ui.reader_view import EXTRACT, ReaderPanel, article

        if self._closing or not self.tabs:
            return
        tab = self.tabs[self.current_tab_index]
        if tab.reader_panel is not None:
            tab.reader_panel.close_reader()
            return
        url = tab.url()
        if tab.workspace_name or url.scheme() not in ("http", "https", "file"):
            self.status_bar.showMessage("Reader view works on web pages.", 4000)
            return

        def extracted(raw):
            if (
                self._closing
                or sip.isdeleted(tab)
                or tab not in self.tabs
                or tab.url() != url
                or tab.reader_panel is not None
            ):
                return
            content = article(raw)
            if content is None:
                self.status_bar.showMessage(
                    "Reader view found no article on this page.", 5000
                )
                return
            panel = ReaderPanel(self, tab, content, url)
            tab.reader_panel = panel

            def closed():
                if not sip.isdeleted(tab):
                    tab.reader_panel = None
                    if tab in self.tabs and tab is self.tabs[self.current_tab_index]:
                        tab.setFocus()

            panel.closed.connect(closed)
            panel.surface.setFocus()

        webengine.run_script(
            tab.page(), EXTRACT, extracted, world=webengine.STEED_WORLD
        )

    def _local_download_actions(self, menu, page, request):
        """Save Link/Image/Media As on a local page, started by Steed instead.

        The engine refuses downloads that a local (steed: or file:) page starts.
        """
        for name, target in (
            ("download_link", request.linkUrl()),
            ("download_image", request.mediaUrl()),
            ("download_media", request.mediaUrl()),
        ):
            action = webengine.menu_action(page, name)
            if action not in menu.actions():
                continue
            replacement = QAction(action.text(), menu)
            url = QUrl(target)
            replacement.triggered.connect(
                lambda _=False, url=url: self._download_url(url)
            )
            menu.insertAction(action, replacement)
            menu.removeAction(action)

    def _download_url(self, url):
        """Download a web address in this window's profile; asks where to save."""
        if self._closing or url.scheme() not in ("http", "https", "data"):
            return False
        self._blank_download_page().download(url)
        return True

    def _show_tab_preview(self, index, point):
        if (
            self._closing
            or not self.config.get("tab_previews", True)
            or not 0 <= index < len(self.tabs)
            or index == self.current_tab_index
        ):
            return
        from ui.tab_preview import TabPreview

        if getattr(self, "tab_preview", None) is None:
            self.tab_preview = TabPreview(self)
        self.tab_preview.show_for(self.tabs[index], point)

    def _hide_tab_preview(self):
        preview = getattr(self, "tab_preview", None)
        if preview is not None and not sip.isdeleted(preview):
            preview.hide()

    def take_screenshot(self, whole_page=False, copy=False, path=None):
        """Save the current tab as PNG, or copy it; the whole page or what shows."""
        from ui import page_screenshot

        if self._closing or not self.tabs:
            return None
        tab = self.tabs[self.current_tab_index]

        def deliver(image, cut=False):
            if image.isNull():
                self.status_bar.showMessage("Nothing to capture.", 4000)
                return
            note = " (cut at 16,384 pixels)" if cut else ""
            if copy:
                QApplication.clipboard().setImage(image)
                self.status_bar.showMessage(f"Screenshot copied{note}", 5000)
                return
            target = path or self._screenshot_path(tab)
            if target and image.save(str(target), "PNG"):
                self.status_bar.showMessage(f"Saved {Path(target).name}{note}", 6000)
            elif target:
                QMessageBox.warning(self, "Screenshot", "The file could not be saved.")

        if (
            not whole_page
            or tab.workspace_name
            or tab.url().scheme()
            not in (
                "http",
                "https",
                "file",
            )
        ):
            deliver(page_screenshot.visible_area(tab))
            return None
        capture = page_screenshot.FullPageCapture(tab.page(), self)
        capture.finished.connect(lambda image, cut: deliver(image, cut))
        capture.failed.connect(lambda text: self.status_bar.showMessage(text, 6000))
        capture.finished.connect(lambda *_: capture.deleteLater())
        capture.failed.connect(lambda *_: capture.deleteLater())
        self.status_bar.showMessage("Capturing the page…")
        capture.start()
        self.screenshot_capture = capture
        return capture

    def _screenshot_path(self, tab):
        import re
        from datetime import datetime

        from PyQt6.QtCore import QStandardPaths
        from PyQt6.QtWidgets import QFileDialog

        folder = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        ) or str(Path.home())
        title = re.sub(r"[^\w .-]+", "", tab.title() or "Page").strip()[:60] or "Page"
        name = f"{title} {datetime.now():%Y-%m-%d %H.%M.%S}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot", str(Path(folder) / name), "PNG images (*.png)"
        )
        return path

    def _media_changed(self):
        """The toolbar's media button follows the controlled tab."""
        media = media_controls.controller()
        if self._closing:
            return
        if media.window is not self or not media.alive():
            self.address_bar.hide_media()
            return
        name = media.title or media.tab.title() or "Media"
        short = name if len(name) <= 24 else name[:23] + "…"
        self.address_bar.show_media(
            ("⏸ " if media.playing else "▶ ") + short,
            f"{'Playing' if media.playing else 'Paused'}: {name}",
        )

    def _show_media_menu(self):
        from PyQt6.QtWidgets import QMenu

        media = media_controls.controller()
        if not media.alive() or media.window is not self:
            return
        tab = media.tab
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addAction("Pause" if media.playing else "Play", media.toggle)
        muted = tab.page().isAudioMuted()
        menu.addAction(
            "Unmute Tab" if muted else "Mute Tab",
            lambda: self._toggle_tab_mute(self.tabs.index(tab)),
        )
        menu.addAction("Go to Tab", media.show)
        mini = getattr(tab, "mini_player", None)
        if mini is None:
            menu.addAction("Open Mini Player", lambda: self.open_mini_player(tab))
        else:
            menu.addAction("Close Mini Player", mini.close)
        self.media_menu = menu
        button = self.address_bar.media_btn
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def open_mini_player(self, tab=None):
        """Float a tab's video in a small window that stays on top."""
        media = media_controls.controller()
        tab = tab or (media.tab if media.alive() else None)
        if tab is None or tab not in self.tabs or tab.workspace_name:
            self.status_bar.showMessage("Play a video first.", 4000)
            return None
        if getattr(tab, "mini_player", None) is not None:
            return tab.mini_player
        if tab is self.tabs[self.current_tab_index] and len(self.tabs) > 1:
            # The window shows another tab while this one plays in the corner.
            others = [i for i, t in enumerate(self.tabs) if t is not tab]
            self._switch_tab(others[-1])
        player = media_controls.MiniPlayer(self, tab)
        player.show()
        return player

    def _open_search_keywords(self):
        from ui.search_keywords_dialog import SearchKeywordsDialog

        dialog = SearchKeywordsDialog(self.config, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        return self._present_dialog(dialog, modal=False)

    def _download_page(self, profile):
        """A page of an open window in that profile, to start a download from."""
        windows = [self, *self._same_profile_windows()]
        windows += [
            w
            for w in QApplication.topLevelWidgets()
            if isinstance(w, BrowserWindow) and w not in windows and not w._closing
        ]
        for window in windows:
            if window.profile_manager.current_profile_name != profile:
                continue
            # A Steed page (New Tab, Welcome) cannot start a second download.
            for tab in window.tabs:
                if tab.url().scheme() in ("http", "https") and not tab.workspace_name:
                    return tab.page()
        for window in windows:
            if window.profile_manager.current_profile_name == profile:
                return window._blank_download_page()
        return None

    def _blank_download_page(self):
        """A hidden blank page of this window's profile, for starting downloads."""
        page = getattr(self, "_retry_page", None)
        profile = self.profile_manager.current_profile
        if page is None or sip.isdeleted(page) or page.profile() is not profile:
            if page is not None and not sip.isdeleted(page):
                sip.delete(page)
            page = webengine.Page(profile, self)
            self._retry_page = page
        return page

    def _downloads_changed(self):
        """Toolbar progress: shown while downloading and briefly afterwards."""
        if self._closing:
            return
        count, fraction = self.download_manager.progress()
        if count:
            self._downloads_recent.stop()
            self.address_bar.show_downloads(
                f"↓ {round(fraction * 100)}%" if fraction is not None else "↓ …",
                f"{count} download{'s' if count > 1 else ''} in progress (Ctrl+J)",
            )
        elif not self._downloads_recent.isActive():
            self.address_bar.hide_downloads()

    def _download_finished(self, key):
        record = self.download_manager.records.get(key)
        if record is None or self._closing:
            return
        self.status_bar.showMessage(f"Downloaded {record.name}", 8000)
        if not self.download_manager.active():
            self.address_bar.show_downloads(
                "↓ Done", f"Downloaded {record.name} — open Downloads (Ctrl+J)"
            )
            self._downloads_recent.start()

    def _show_site_menu(self):
        """The site icon's menu: this site's zoom, JavaScript, autoplay, pop-ups."""
        from PyQt6.QtWidgets import QMenu

        import site_settings

        if self._closing or not self.tabs:
            return
        tab = self.tabs[self.current_tab_index]
        host = site_key(tab.url())
        if host is None or tab.workspace_name:
            return
        store = self._site_settings()
        values = store.get(host)
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addSection(host)
        for key, label in site_settings.LABELS.items():
            action = menu.addAction(f"Allow {label.lower()}")
            action.setCheckable(True)
            action.setChecked(values[key] == "allow")
            action.toggled.connect(
                lambda allowed, key=key: self._set_site_setting(
                    tab, host, key, "allow" if allowed else "block"
                )
            )
        zoom = round(values["zoom"] * 100)
        reset = menu.addAction(f"Reset zoom ({zoom}%)")
        reset.setEnabled(zoom != 100)
        reset.triggered.connect(lambda: store.set(host, "zoom", 1.0))
        menu.addSeparator()
        clear = menu.addAction("Clear settings for this site")
        clear.setEnabled(bool(store.changed_keys(host)))
        clear.triggered.connect(lambda: self._clear_site_settings(tab, host))
        menu.addAction("Website permissions…").triggered.connect(
            lambda: self._open_management("permissions")
        )
        self.site_menu = menu
        field = self.address_bar.address_input
        menu.popup(field.mapToGlobal(field.rect().bottomLeft()))

    def _set_site_setting(self, tab, host, key, value):
        self._site_settings().set(host, key, value)
        # The page loaded with the old setting; reload so it takes effect.
        if tab in self.tabs and site_key(tab.url()) == host:
            tab.reload()

    def _clear_site_settings(self, tab, host):
        reload = any(key != "zoom" for key in self._site_settings().changed_keys(host))
        self._site_settings().clear(host)
        if reload and tab in self.tabs and site_key(tab.url()) == host:
            tab.reload()

    # Typical text smaller than this is enlarged toward READABLE_TEXT_PX.
    SMALL_TEXT_PX = 13
    READABLE_TEXT_PX = 15
    MAX_AUTO_ZOOM = 1.5
    TEXT_SIZE = """(() => {
      const sizes = [];
      for (const node of document.querySelectorAll('p, li, td, dd, blockquote, article, main')) {
        const text = (node.innerText || '').trim();
        if (text.length < 40 || !node.getClientRects().length) continue;
        sizes.push(parseFloat(getComputedStyle(node).fontSize));
        if (sizes.length >= 200) break;
      }
      if (sizes.length < 3) return 0;
      sizes.sort((a, b) => a - b);
      return sizes[Math.floor(sizes.length / 2)];
    })()"""

    def _auto_zoom_small_text(self, tab):
        """Enlarge a page whose typical text is tiny, unless the site has a zoom."""
        from services.navigation_service import ZOOM_LEVELS

        url = tab.url()
        host = site_key(url)
        if (
            host is None
            or tab.workspace_name
            or not self.config.get("auto_zoom_small_text", True)
        ):
            return
        values = self._site_settings().get(host)
        if values["auto_zoom"] == "block" or values["zoom"] != 1.0:
            return

        def measured(size):
            if (
                self._closing
                or sip.isdeleted(tab)
                or tab.url() != url
                or not isinstance(size, (int, float))
                or not 0 < size < self.SMALL_TEXT_PX
            ):
                return
            wanted = min(self.MAX_AUTO_ZOOM, self.READABLE_TEXT_PX / size)
            zoom = next((z for z in ZOOM_LEVELS if z >= wanted - 0.001), wanted)
            zoom = min(zoom, self.MAX_AUTO_ZOOM)
            if zoom <= tab.zoomFactor() + 0.001:
                return
            tab.setZoomFactor(zoom)
            tab.auto_zoomed = zoom
            if tab is self.tabs[self.current_tab_index]:
                self._update_zoom()
                self.status_bar.showMessage(
                    f"Zoomed to {round(zoom * 100)}% because this page's text is "
                    "small. Ctrl+0 turns this off for the site.",
                    6000,
                )

        webengine.run_script(
            tab.page(), self.TEXT_SIZE, measured, world=webengine.STEED_WORLD
        )

    def _site_settings_changed(self, host):
        """Another window or the site menu changed a site: update its tabs."""
        for tab in self.tabs:
            if site_key(tab.url()) == host and not tab.workspace_name:
                tab.zoom_host = ""
                self._apply_site_zoom(tab)
                self._apply_site_settings(tab.page(), tab.url())

    def _update_zoom(self):
        if not self._closing and self.tabs:
            percent = round(
                self._visible_page(self.tabs[self.current_tab_index]).zoomFactor() * 100
            )
            self.address_bar.zoom_btn.setText(f"{percent}%")
            self.zoom_reset_action.setText(f"Reset Zoom ({percent}%)")

    def _refresh_address_history(self):
        self.address_bar.set_history(self.history.entries())
        self._refresh_address_sources()

    def _refresh_address_sources(self):
        """Bookmarks and the search engine's name for address suggestions."""
        try:
            state = self.profile_manager.registry.start_pages.state(
                self.profile_manager.current_profile_name
            )
            bookmarks = [(b["title"], b["url"]) for b in state.get("bookmarks", [])]
        except (OSError, ValueError, KeyError, TypeError):
            bookmarks = []
        self.address_bar.set_bookmarks(bookmarks)
        self.address_bar.set_search_name(search_engine_name(self.config))
        # Typed text goes to the search engine only when allowed, never from
        # incognito.
        import search_keywords

        self.address_bar.keyword_source = lambda text: search_keywords.match(
            text, self.config
        )
        self.address_bar.address_input.autocomplete = bool(
            self.config.get("address_autocomplete", True)
        )
        self.address_bar.set_search_suggestions(
            self.config.get("search_engine", DEFAULT_SEARCH_ENGINE),
            bool(self.config.get("search_suggestions", True))
            and not self.profile_manager.is_search_profile(),
        )

    def _suggestion_tabs(self):
        """Open tabs of this profile in every window: (title, url, key)."""
        rows = []
        for window in [self, *self._same_profile_windows()]:
            for tab in window.tabs:
                if getattr(tab, "agent_panel", False):
                    continue
                rows.append(
                    (
                        tab.title(),
                        window._tab_url(tab),
                        window.browser_window_id + ":" + tab.tab_id,
                    )
                )
        return rows

    def _switch_to_tab_key(self, key):
        window_id, _, tab_id = key.partition(":")
        for window in [self, *self._same_profile_windows()]:
            if window.browser_window_id != window_id:
                continue
            for index, tab in enumerate(window.tabs):
                if tab.tab_id == tab_id:
                    window._switch_tab(index)
                    window.raise_()
                    window.activateWindow()
                    tab.setFocus()
                    return

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

    def _go_home(self):
        home = self.config.get("home_page", NEW_TAB) or NEW_TAB
        self._navigate_to_url(home)

    def _on_tab_icon(self, tab, icon):
        if not self._closing and tab in self.tabs:
            self.tab_bar.set_tab_icon(self.tabs.index(tab), icon)

    def _on_tab_audio(self, tab):
        if not self._closing and tab in self.tabs:
            page = tab.page()
            self.tab_bar.set_tab_state(
                self.tabs.index(tab),
                audible=page.recentlyAudible(),
                muted=page.isAudioMuted(),
            )
            media = media_controls.controller()
            if page.recentlyAudible():
                media.start_desktop()
                media.refresh_tab(self, tab)
            elif media.tab is tab:
                media.refresh()

    def _offer_old_pairing_cleanup(self):
        """Once: remove KeePassXC pairing files the built-in vault replaced."""
        from password_service import old_pairings

        if self._closing or self.config.get("keepassxc_cleanup_asked", False):
            return
        files = old_pairings(self.config)
        if not files:
            return
        self.config.set("keepassxc_cleanup_asked", True)
        reply = QMessageBox.question(
            self,
            "Old KeePassXC connection",
            "Steed now keeps passwords in its own encrypted vault. Remove the old "
            f"KeePassXC connection files ({len(files)})? Your KeePassXC database "
            "is not touched. To bring its passwords over, export a CSV from "
            "KeePassXC and use Import on steed:passwords.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            for path in files:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _warn_codecs_replaced(self):
        """Say so when a pip update put back the engine without H.264/AAC."""
        import build_codecs

        if self._closing:
            return
        try:
            version = build_codecs.qt_version()
            replaced = build_codecs.replaced_by_pip(build_codecs.wheel_dir())
        except Exception:
            logger.exception("Could not check the codec build")
            return
        if not replaced or self.config.get("codec_warning_ignored") == version:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Video codecs missing")
        box.setText(
            "A Python package update replaced Steed's engine with one that cannot "
            "play H.264 video or AAC audio, so many videos and GIFs will not play. "
            "To fix it, close Steed and run\n\n"
            "    .venv/bin/python build_codecs.py install\n\n"
            f"from Steed's folder (or build, if Qt WebEngine {version} is new)."
        )
        details = box.addButton(
            "Open steed:features", QMessageBox.ButtonRole.ActionRole
        )
        ignore = box.addButton(
            "Don't remind me for this version", QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)

        def answered(button):
            if button is details:
                self._new_tab(url="steed:features")
            elif button is ignore:
                self.config.set("codec_warning_ignored", version)

        box.buttonClicked.connect(answered)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.codec_warning = box
        box.show()

    def _security_key(self, request):
        """A site asks to use a security key (WebAuthn)."""
        if self._closing:
            request.cancel()
            return
        if getattr(self, "web_auth", None) is None:
            from ui.web_auth import WebAuthPrompts

            self.web_auth = WebAuthPrompts(self)
        self.web_auth.handle(request)

    def _on_tab_lifecycle(self, tab):
        if not self._closing and tab in self.tabs:
            self.tab_bar.set_tab_state(
                self.tabs.index(tab), lifecycle=webengine.tab_state(tab.page())
            )

    def _set_keep_loaded(self, tab, keep):
        """Exempt one tab from freezing and unloading, and wake it if needed."""
        if tab not in self.tabs:
            return
        tab.keep_loaded = bool(keep)
        self.tab_bar.set_tab_state(self.tabs.index(tab), keep_loaded=tab.keep_loaded)
        if keep:
            webengine.wake(tab.page())

    def _toggle_tab_mute(self, index):
        if 0 <= index < len(self.tabs):
            page = self.tabs[index].page()
            page.setAudioMuted(not page.isAudioMuted())

    def _on_tab_url_changed(self, tab: BrowserTab, url: QUrl):
        if self._closing:
            return
        if tab in self.tabs:
            self.tab_bar.set_tab_state(self.tabs.index(tab), url=url.toString())
            self._apply_site_zoom(tab)
            if tab.reader_panel is not None and url != tab.reader_panel.url:
                # Reader view belongs to one article; following a link leaves it.
                tab.reader_panel.close_reader()
        if not self.tabs or not (0 <= self.current_tab_index < len(self.tabs)):
            return

        if (
            tab == self.tabs[self.current_tab_index]
            and not getattr(tab, "agent_panel", False)
            and not tab.workspace_name
        ):
            self.address_bar.set_url(display_url(url))
            self._update_window_title(url)
        if tab == self.tabs[self.current_tab_index]:
            self._update_history_buttons()
            # History can commit just after the address changes.
            QTimer.singleShot(0, self._update_history_buttons)

    def _on_tab_title_changed(self, tab: BrowserTab, title: str):
        if getattr(tab, "agent_panel", False) or tab.workspace_name:
            return
        if tab in self.tabs:
            idx = self.tabs.index(tab)
            if len(title) > 30:
                title = title[:27] + "..."
            self.tab_bar.set_tab_title(idx, title)
            if idx == self.current_tab_index:
                self._update_history_buttons()

    def _on_load_progress(self, tab: BrowserTab, progress: int):
        tab.load_progress = max(0, min(100, progress))
        if not self._closing and self.tabs and tab == self.tabs[self.current_tab_index]:
            self.status_bar.showMessage(f"Loading... {progress}%")
            self._update_loading_indicator()

    def _on_load_started(self, tab):
        tab.is_loading = True
        tab.load_progress = 0
        if not self._closing and tab in self.tabs:
            self.tab_bar.set_tab_state(self.tabs.index(tab), loading=True)
        if not self._closing and self.tabs and tab == self.tabs[self.current_tab_index]:
            self._update_loading_indicator()

    def _on_load_finished(self, tab: BrowserTab, ok: bool):
        if self._closing or tab not in self.tabs:
            return
        tab.is_loading = False
        self.tab_bar.set_tab_state(self.tabs.index(tab), loading=False)
        tab.load_succeeded = ok
        self.password_assistant.page_loaded(tab, ok)
        if ok:
            self._auto_zoom_small_text(tab)
        if tab == self.tabs[self.current_tab_index]:
            self._update_history_buttons()
            self.status_bar.showMessage("Ready" if ok else "Error loading page")
            self.find_bar.page_loaded()
            self._update_loading_indicator()
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
            elif tab.workspace_name:
                url = "steed:" + tab.workspace_name
            if not url:
                url = self.config.get("new_tab_page", NEW_TAB) or NEW_TAB
            title = tab.title() or "New Tab"
            if getattr(tab, "agent_panel", False):
                title = "External Agents"
            elif tab.workspace_name:
                title = tab.workspace_name.title()
            if not title or title == "about:blank":
                title = "New Tab"

            states.append(
                TabState(
                    url=url,
                    title=title,
                    group=group,
                    tab_id=tab.tab_id,
                    group_id=tab.group_id,
                    pinned=tab.pinned,
                    keep_loaded=getattr(tab, "keep_loaded", False),
                    scroll=self._tab_scroll(tab),
                )
            )

        self.tab_manager.save_tabs(states)

    @staticmethod
    def _tab_scroll(tab):
        if tab.pending_url is not None:
            return tab.pending_scroll
        if tab.workspace_name or webengine.tab_state(tab.page()) == webengine.UNLOADED:
            return getattr(tab, "pending_scroll", None)
        position = tab.page().scrollPosition()
        if position.x() <= 0 and position.y() <= 0:
            return None
        return [round(position.x()), round(position.y())]

    def _restore_scroll(self, tab, url, scroll):
        """Scroll a restored tab back once its saved page has loaded."""
        tab.pending_scroll = scroll

        def loaded(ok):
            tab.loadFinished.disconnect(loaded)
            if not ok or sip.isdeleted(tab) or tab.url() != url:
                tab.pending_scroll = None
                return
            # Pages that build their content late get a second try.
            source = f"window.scrollTo({float(scroll[0])}, {float(scroll[1])})"
            for delay in (0, 500):
                QTimer.singleShot(
                    delay,
                    lambda: (
                        sip.isdeleted(tab)
                        or webengine.run_script(
                            tab.page(), source, world=webengine.STEED_WORLD
                        )
                    ),
                )
            tab.pending_scroll = None

        tab.loadFinished.connect(loaded)

    def _restore_tabs(self, reopen_windows=False):
        if self.profile_manager.is_search_profile():
            self._new_tab()
            return
        if self.tab_manager.others_open():
            # Another window already shows this profile's session; a second
            # window starts fresh instead of duplicating every saved tab.
            self._new_tab()
            self._save_current_tabs()
            return
        windows = self.tab_manager.claim_saved()
        self._restore_states(windows[0] if windows else None)
        if reopen_windows:
            profile = self.profile_manager.current_profile_name
            for index, states in enumerate(windows[1:], 1):
                self.tab_manager.drop_saved(index)
                self._open_window(profile, states)

    def _restore_states(self, saved):
        import memory_modes

        lazy = memory_modes.values(self.config)["restore_tabs_lazily"]
        self._restoring_tabs = True
        try:
            if saved:
                for state in saved:
                    self._new_tab(
                        url=state.url,
                        title=state.title,
                        index=len(self.tabs),
                        defer=lazy,
                        scroll=state.scroll,
                    )
                    self.tabs[-1].group = state.group
                    self.tabs[-1].tab_id = state.tab_id
                    self.tabs[-1].group_id = state.group_id
                    if state.pinned:
                        self.tabs[-1].pinned = True
                        self.tab_bar.set_tab_state(len(self.tabs) - 1, pinned=True)
                    if state.keep_loaded:
                        self._set_keep_loaded(self.tabs[-1], True)
            else:
                self._new_tab(url=self._startup_page(), index=len(self.tabs))
        finally:
            self._restoring_tabs = False
        self._switch_tab(0)
        self._sync_tab_group_labels()
        # Register this window's entry in the session right away.
        self._save_current_tabs()

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

    # The engine waits inside these callbacks and cannot load any page until
    # they return, including Steed's own HTML dialogs. A question asked here
    # would never appear (the window hung on "Opening settings interface…"),
    # so each answer is worked out a moment later; requests stay valid until
    # answered.
    def _on_permission_requested(self, permission: QWebEnginePermission, page=None):
        # The signal's object is only valid during the call; keep a copy.
        permission = QWebEnginePermission(permission)
        QTimer.singleShot(0, lambda: self._resolve_permission(permission, page))

    def _resolve_permission(self, permission, page=None):
        PermissionService.respond(
            permission,
            lambda origin, feature: self._ask_permission(origin, feature, page),
            lambda: self._closing,
        )

    def _on_feature_permission_requested(self, page, origin, feature):
        QTimer.singleShot(
            0, lambda: self._resolve_feature_permission(page, origin, feature)
        )

    def _resolve_feature_permission(self, page, origin, feature):
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
    def _tab_url(self, tab):
        if getattr(tab, "agent_panel", False):
            return "steed:agents"
        if tab.workspace_name:
            return "steed:" + tab.workspace_name
        return tab.url().toString() or tab.page().requestedUrl().toString()

    def _open_palette(self, mode=""):

        if self._closing:
            return

        self._ensure_palette()
        self._palette.present(mode)

    def _ensure_palette(self):
        from ui.command_palette import CommandPalette
        from ui.interface_mode import native

        if self._palette is None:
            if native():
                from ui.command_palette import NativeCommandPalette

                self._palette = NativeCommandPalette(self)
            else:
                self._palette = CommandPalette(self)
            self._palette.released.connect(self._release_palette)
        self._palette.keep_ms = self._memory["palette_keep_seconds"] * 1000
        return self._palette

    def _apply_memory_policies(self):
        """Window parts of the memory mode: palette and background tabs."""
        if self._closing:
            return
        keep = self._memory["palette_keep_seconds"]
        if self._palette is not None:
            self._palette.keep_ms = keep * 1000
        elif keep < 0:
            # Preload so the first Ctrl+K is instant.
            QTimer.singleShot(1500, lambda: self._closing or self._ensure_palette())
        if not hasattr(self, "_lifecycle_timer"):
            self._lifecycle_timer = QTimer(self)
            self._lifecycle_timer.setInterval(30000)
            self._lifecycle_timer.timeout.connect(self._apply_tab_lifecycle)
        if self._memory["tab_freeze_minutes"] or self._memory["tab_discard_minutes"]:
            self._lifecycle_timer.start()
        else:
            self._lifecycle_timer.stop()
        self._apply_tab_lifecycle()

    def _memory_pressure_changed(self, low):
        """Low system memory: follow Memory saver now, then restore settings."""
        import memory_modes

        if self._closing:
            return
        self._memory = memory_modes.apply_process_policies(self.config)
        self._apply_memory_policies()
        if low and self._memory["auto_saver"]:
            memory_modes.free_now([self])

    def _tab_keeps_running(self, tab):
        """Tabs that must stay active whatever the memory mode says."""
        if (
            getattr(tab, "workspace_name", None)
            or getattr(tab, "agent_panel", False)
            or getattr(tab, "keep_loaded", False)
        ):
            return True
        page = tab.page()
        if page is None or page.recentlyAudible() or page.devToolsPage() is not None:
            return True
        if page.isLoading():
            return True
        tasks = self.profile_manager.registry._tasks
        if tasks is not None and tasks.backend.live_session(tab) is not None:
            return True
        return False

    def _apply_tab_lifecycle(self):
        """Freeze, then unload, tabs left in the background."""
        if self._closing:
            return
        from time import monotonic

        freeze = self._memory["tab_freeze_minutes"] * 60
        discard = self._memory["tab_discard_minutes"] * 60
        now = monotonic()
        for index, tab in enumerate(self.tabs):
            if index == self.current_tab_index or self._tab_keeps_running(tab):
                continue
            page = tab.page()
            idle = now - getattr(tab, "last_active", now)
            wanted = webengine.ACTIVE
            if discard and idle >= discard:
                wanted = webengine.UNLOADED
            elif freeze and idle >= freeze:
                wanted = webengine.FROZEN
            # Never go further than the engine recommends for this page.
            lightest = webengine.lightest_state(page)
            if webengine.deeper(wanted, lightest):
                wanted = lightest
            if webengine.deeper(wanted, webengine.tab_state(page)):
                if wanted == webengine.UNLOADED:
                    self._discard_tab(tab)
                else:
                    webengine.set_tab_state(page, wanted)

    def _discard_tab(self, tab):
        """Unload a background tab after letting the site save its state."""
        from page_unload import DISCARD_TIMEOUT_MS, run_unload_handlers

        if getattr(tab, "unloading", False) or getattr(tab, "unload_failures", 0) >= 3:
            return
        tab.unloading = True
        page = tab.page()

        def discard(answered):
            tab.unloading = False
            if (
                self._closing
                or sip.isdeleted(tab)
                or tab not in self.tabs
                or self.tabs.index(tab) == self.current_tab_index
            ):
                return
            if not answered:
                # Unloading now could lose what the site saves on the way out
                # (Discord's sign-in). Keep it asleep and try at a later check;
                # after three tries it stays loaded for this session.
                tab.unload_failures = getattr(tab, "unload_failures", 0) + 1
                logger.info("Kept a tab loaded: it did not finish saving in time")
                if webengine.lightest_state(page) != webengine.ACTIVE:
                    webengine.set_tab_state(page, webengine.FROZEN)
                return
            # The session keeps its scroll position while it is unloaded.
            tab.pending_scroll = self._tab_scroll(tab)
            if webengine.lightest_state(page) == webengine.ACTIVE:
                # Waking it for the handlers made it active; unload next check.
                webengine.set_tab_state(page, webengine.FROZEN)
            webengine.set_tab_state(page, webengine.UNLOADED)

        run_unload_handlers([page], discard, DISCARD_TIMEOUT_MS)

    def _release_palette(self):
        """Free an idle palette's renderer; the next open builds a new one."""
        palette, self._palette = self._palette, None
        if palette is not None:
            palette.dispose()
            palette.deleteLater()

    def _visible_tab_indexes(self):
        return [i for i in range(len(self.tabs)) if self.tab_bar.is_tab_visible(i)]

    def _cycle_tab(self, step):
        indexes = self._visible_tab_indexes()
        if len(indexes) < 2:
            return
        position = (
            indexes.index(self.current_tab_index)
            if self.current_tab_index in indexes
            else 0
        )
        self._switch_tab(indexes[(position + step) % len(indexes)])

    def _jump_to_tab(self, number):
        indexes = self._visible_tab_indexes()
        if not indexes:
            return
        if number == 9:
            self._switch_tab(indexes[-1])
        elif number <= len(indexes):
            self._switch_tab(indexes[number - 1])

    def _devtools_tab(self):
        if not self.tabs or not 0 <= self.current_tab_index < len(self.tabs):
            return None
        return self.tabs[self.current_tab_index]

    def _devtools_call(self, method, tab=None):
        tab = tab or self._devtools_tab()
        if tab is None:
            return
        try:
            method(tab)
        except DevToolsUnavailable as error:
            self.status_bar.showMessage(str(error), 6000)

    def _toggle_devtools(self):
        self._devtools_call(self.devtools.toggle)

    def _devtools_console(self):
        self._devtools_call(self.devtools.console)

    def _devtools_pick(self):
        self._devtools_call(self.devtools.pick_element)

    def _inspect_page_element(self, tab):
        self._devtools_call(self.devtools.inspect_element, tab)

    def _show_tab_menu(self, index, position):
        """Right-click actions for one tab, in Chrome's familiar order."""
        if not 0 <= index < len(self.tabs):
            return
        tab = self.tabs[index]
        menu = QMenu(self)
        menu.addAction("New Tab to the Right", lambda: self._new_tab(index=index + 1))
        menu.addSeparator()
        menu.addAction("Reload", lambda: self._reload_tab(tab))
        menu.addAction("Duplicate", lambda: self._duplicate_tab(tab))
        menu.addAction(
            "Unpin Tab" if tab.pinned else "Pin Tab",
            lambda: self._set_pinned(tab, not tab.pinned),
        )
        muted = tab.page().isAudioMuted()
        menu.addAction(
            "Unmute Tab" if muted else "Mute Tab",
            lambda: tab in self.tabs and self._toggle_tab_mute(self.tabs.index(tab)),
        )
        keep = menu.addAction(
            "Keep This Tab Loaded",
            lambda checked=False: self._set_keep_loaded(tab, checked),
        )
        keep.setCheckable(True)
        keep.setChecked(getattr(tab, "keep_loaded", False))
        copy = menu.addAction(
            "Copy Link Address",
            lambda: QApplication.clipboard().setText(tab.url().toString()),
        )
        copy.setEnabled(not tab.workspace_name and not tab.url().isEmpty())
        movable = len(self.tabs) > 1
        menu.addAction(
            "Move Tab to New Window", lambda: self._move_tab(tab)
        ).setEnabled(movable)
        others = self._same_profile_windows()
        if others:
            destinations = menu.addMenu("Move Tab to Window")
            destinations.setEnabled(movable)
            for other in others:
                current = other.tabs[other.current_tab_index] if other.tabs else None
                label = (current.title() if current else "") or "Window"
                destinations.addAction(
                    f"{label[:40]} ({len(other.tabs)} tabs)",
                    lambda other=other: self._move_tab(tab, other),
                )
        menu.addSeparator()
        groups = menu.addMenu("Add Tab to Group")
        groups.setEnabled(not tab.pinned)
        groups.addAction("New Group…", lambda: self._handle_group_rename_request(index))
        names = sorted(group.name for group in self.tab_manager.groups.values())
        if names:
            groups.addSeparator()
        for name in names:
            action = groups.addAction(
                name, lambda n=name: self._set_tab_group_existing(index, n)
            )
            action.setCheckable(True)
            action.setChecked(tab.group == name)
        if tab.group:
            menu.addAction("Remove from Group", lambda: self._clear_tab_group(index))
            menu.addAction(
                "Collapse / Expand Group",
                lambda: self._toggle_named_group(tab.group),
            )
        menu.addSeparator()
        single = len(self.tabs) <= 1
        menu.addAction(
            "Close Window" if single else "Close Tab",
            lambda: self._close_tab_object(tab),
        )
        menu.addAction(
            "Close Other Tabs", lambda: self._close_tabs_except(tab)
        ).setEnabled(not single)
        menu.addAction(
            "Close Tabs to the Right", lambda: self._close_tabs_after(tab)
        ).setEnabled(index < len(self.tabs) - 1)
        reopen = menu.addAction("Reopen Closed Tab", self._restore_closed_tab)
        reopen.setEnabled(self.restore_tab_action.isEnabled())
        self._show_html_menu(menu, position, context=True)

    def _show_group_menu(self, name, position):
        group = self.tab_manager.ensure_group(name)
        members = [tab for tab in self.tabs if tab.group_id == group.group_id]
        menu = QMenu(self)
        menu.addAction("Rename Group…", lambda: self._rename_group_prompt(group))
        menu.addAction(
            "Expand Group" if group.collapsed else "Collapse Group",
            lambda: self.toggle_group(group.group_id),
        )
        menu.addAction(
            "New Tab in Group",
            lambda: self._new_tab_in_group(group, members),
        )
        colors = menu.addMenu("Colour")
        automatic = colors.addAction("Automatic")
        automatic.setCheckable(True)
        automatic.setChecked(not group.color)
        automatic.triggered.connect(lambda: self.set_group_color(group.group_id, ""))
        for key in GROUP_COLORS:
            action = colors.addAction(key.title())
            action.setCheckable(True)
            action.setChecked(group.color == key)
            action.triggered.connect(
                lambda _=False, key=key: self.set_group_color(group.group_id, key)
            )
        menu.addSeparator()
        save = menu.addAction(
            "Save and Close Group", lambda: self.save_and_close_group(group.group_id)
        )
        save.setEnabled(not self.tab_manager.private)
        menu.addAction("Ungroup", lambda: self.delete_group(group.group_id))
        close = menu.addAction(
            "Close Group", lambda: [self._close_tab_object(t) for t in members]
        )
        close.setEnabled(len(members) < len(self.tabs))
        menu.addSeparator()
        menu.addAction("Manage Tab Groups…", self._manage_tab_groups)
        self._show_html_menu(menu, position, context=True)

    def _rename_group_prompt(self, group):
        name, ok = QInputDialog.getText(
            self, "Tab Group", "Group name:", text=group.name
        )
        if ok and name.strip():
            self.rename_group(group.group_id, name.strip())

    def _new_tab_in_group(self, group, members):
        index = (
            self.tabs.index(members[-1]) + 1
            if members and members[-1] in self.tabs
            else None
        )
        tab = self._new_tab(index=index, return_tab=True)
        if tab is not None and tab in self.tabs:
            self._set_tab_group_existing(self.tabs.index(tab), group.name)

    def _commit_tab_width(self, width):
        """Save a dragged side-tab width for every window."""
        if self._layout_editing:
            # The open Layout Settings editor owns the next commit.
            return
        saved = preferences_from_config(self.config)
        edited = saved.copy()
        edited.set_tabs({"vertical_width": width})
        try:
            self.profile_manager.registry.commit_layout(edited, base=saved)
        except LayoutConflict:
            self.apply_layout(preferences_from_config(self.config))

    # Signals one window owns on each of its tabs; moved tabs are rewired.
    TAB_SIGNALS = (
        "urlChanged",
        "titleChanged",
        "loadProgress",
        "loadStarted",
        "loadFinished",
        "iconChanged",
    )
    PAGE_SIGNALS = (
        "webAuthUxRequested",
        "lifecycleStateChanged",
        "recentlyAudibleChanged",
        "audioMutedChanged",
        "zoomFactorChanged",
        "permissionRequested",
        "featurePermissionRequested",
        "desktopMediaRequested",
        "fullScreenRequested",
    )

    def _connect_tab(self, tab):
        self._connect_page_signals(tab.page())
        tab.urlChanged.connect(lambda u: self._on_tab_url_changed(tab, u))
        tab.titleChanged.connect(lambda t: self._on_tab_title_changed(tab, t))
        tab.loadProgress.connect(lambda p: self._on_load_progress(tab, p))
        tab.loadStarted.connect(lambda: self._on_load_started(tab))
        tab.loadFinished.connect(lambda ok: self._on_load_finished(tab, ok))
        tab.iconChanged.connect(lambda icon: self._on_tab_icon(tab, icon))
        tab.page().recentlyAudibleChanged.connect(lambda _: self._on_tab_audio(tab))
        tab.page().lifecycleStateChanged.connect(lambda _: self._on_tab_lifecycle(tab))
        tab.page().webAuthUxRequested.connect(self._security_key)
        tab.page().audioMutedChanged.connect(lambda _: self._on_tab_audio(tab))
        if hasattr(tab.page(), "zoomFactorChanged"):
            tab.page().zoomFactorChanged.connect(lambda _: self._update_zoom())

    def _disconnect_tab(self, tab):
        for owner, names in ((tab, self.TAB_SIGNALS), (tab.page(), self.PAGE_SIGNALS)):
            for name in names:
                signal = getattr(owner, name, None)
                if signal is None:
                    continue
                try:
                    signal.disconnect()
                except TypeError:
                    pass

    def _live_movable(self, tab):
        """Whether the page itself, with its history and state, can move."""
        delegated = self._live_tasks is not None and (
            tab.agent_tab_id in self._live_tasks.live_tabs.offers
        )
        return (
            not tab.workspace_name
            and not getattr(tab, "agent_panel", False)
            and tab.native_panel is None
            and tab.page().devToolsPage() is None
            and not delegated
            and not (self.web_chat_dock and self.web_chat_dock.view is tab)
        )

    def _release_tab(self, tab):
        """Take a tab out of this window without destroying its page."""
        index = self.tabs.index(tab)
        active = self.tabs[self.current_tab_index]
        self._disconnect_tab(tab)
        self.tabs.pop(index)
        self.tab_bar.remove_tab(index)
        self.tab_layout.removeWidget(tab)
        if active is tab:
            self.current_tab_index = min(index, len(self.tabs) - 1)
            self._switch_tab(self.current_tab_index)
        else:
            self.current_tab_index = self.tabs.index(active)
            self.tab_bar.set_current_tab(self.current_tab_index)
        self._sync_tab_group_labels()
        self._save_current_tabs()

    def _adopt_tab(self, tab, state, index=None):
        """Show a live tab released by another window of this profile."""
        tab.setParent(self.tab_container)
        tab.browser_window = self
        self.tab_layout.addWidget(tab)
        self._connect_tab(tab)
        if index is None:
            index = len(self.tabs)
        index = max(self._pinned_count(), min(index, len(self.tabs)))
        self.tabs.insert(index, tab)
        self.tab_bar.insert_tab(index, tab.title() or state.title)
        self.tab_bar.set_tab_icon(index, tab.icon())
        self.tab_bar.set_tab_state(index, url=tab.url().toString())
        tab.group = tab.group_id = None
        tab.pinned = False
        if state.pinned:
            self._set_pinned(tab, True)
        if state.keep_loaded:
            self._set_keep_loaded(tab, True)
        elif state.group:
            self._set_tab_group_existing(self.tabs.index(tab), state.group)
        self._switch_tab(self.tabs.index(tab))
        tab.show()
        self._save_current_tabs()

    def _same_profile_windows(self):
        profile = self.profile_manager.current_profile_name
        return [
            widget
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, BrowserWindow)
            and widget is not self
            and not sip.isdeleted(widget)
            and not widget._closing
            and widget.isVisible()
            and widget.profile_manager.current_profile_name == profile
        ]

    def _tab_state(self, tab):
        return TabState(
            self._tab_url(tab),
            tab.title() or "New Tab",
            tab.group,
            tab.tab_id,
            tab.group_id,
            tab.pinned,
            getattr(tab, "keep_loaded", False),
        )

    def _move_tab(self, tab, target=None, index=None):
        """Reopen a tab in another window of this profile, then remove it here."""
        if tab not in self.tabs or len(self.tabs) <= 1:
            return None
        if target is not None and (
            target is self
            or target.profile_manager.current_profile_name
            != self.profile_manager.current_profile_name
        ):
            return None
        state = self._tab_state(tab)
        if self._live_movable(tab):
            # Move the page itself, keeping its history, scroll and form state.
            self._release_tab(tab)
            if target is None:
                target = self._open_window(
                    self.profile_manager.current_profile_name, []
                )
                placeholder = target.tabs[0] if target.tabs else None
                target._adopt_tab(tab, state)
                if placeholder is not None and placeholder is not tab:
                    target._close_tab(target.tabs.index(placeholder), remember=False)
            else:
                target._adopt_tab(tab, state, index)
        else:
            if target is None:
                target = self._open_window(
                    self.profile_manager.current_profile_name, [state]
                )
            else:
                target._receive_tab(state, index)
            # A move is not a close: it never enters Reopen Closed Tab.
            self._close_tab(self.tabs.index(tab), remember=False)
        target.raise_()
        target.activateWindow()
        return target

    def _receive_tab(self, state, index=None):
        if index is None:
            index = len(self.tabs)
        tab = self._new_tab(
            url=state.url, title=state.title, index=index, return_tab=True
        )
        if tab is None or tab not in self.tabs:
            return
        tab.tab_id = state.tab_id
        if state.keep_loaded:
            self._set_keep_loaded(tab, True)
        if state.pinned:
            self._set_pinned(tab, True)
        elif state.group:
            self._set_tab_group_existing(self.tabs.index(tab), state.group)
        self._save_current_tabs()

    def _tab_drag_started(self, token, index):
        if 0 <= index < len(self.tabs):
            while len(_TAB_DRAGS) >= 8:
                _TAB_DRAGS.pop(next(iter(_TAB_DRAGS)))
            _TAB_DRAGS[token] = (self, self.tabs[index])

    def _tab_dropped_here(self, token, index):
        source, tab = _TAB_DRAGS.pop(token, (None, None))
        if source is None or sip.isdeleted(source) or source is self:
            return
        source._move_tab(tab, self, index)

    def _tear_off_tab(self, index):
        from PyQt6.QtGui import QCursor

        # Released over this window (or a cancelled drag): nothing to do.
        if self.frameGeometry().contains(QCursor.pos()):
            return
        if 0 <= index < len(self.tabs):
            window = self._move_tab(self.tabs[index])
            if window is not None:
                window.move(QCursor.pos())

    def _pinned_count(self):
        return sum(1 for tab in self.tabs if tab.pinned)

    def _set_pinned(self, tab, pinned):
        """Pinned tabs stay together at the start and never belong to a group."""
        if tab not in self.tabs or tab.pinned == pinned:
            return
        index = self.tabs.index(tab)
        target = self._pinned_count() - (0 if pinned else 1)
        tab.pinned = pinned
        if pinned:
            tab.group = tab.group_id = None
        self.tab_bar.set_tab_state(index, pinned=pinned)
        if target != index:
            self.tab_bar.move_tab(index, target)
        else:
            self._sync_tab_group_labels()
            self._save_current_tabs()

    def _reload_tab(self, tab):
        if tab in self.tabs:
            if tab is self.tabs[self.current_tab_index]:
                self._reload()
            elif not tab.workspace_name:
                tab.reload()

    def _duplicate_tab(self, tab):
        if tab not in self.tabs:
            return
        index = self.tabs.index(tab)
        copy = self._new_tab(
            url=self._tab_url(tab), title=tab.title(), index=index + 1, return_tab=True
        )
        if copy is not None and tab.group and copy in self.tabs:
            self._set_tab_group_existing(self.tabs.index(copy), tab.group)

    def _close_tab_object(self, tab):
        if tab in self.tabs:
            self._close_tab(self.tabs.index(tab))

    def _close_tabs_except(self, keep):
        for tab in list(self.tabs):
            if tab is not keep:
                self._close_tab_object(tab)

    def _close_tabs_after(self, anchor):
        if anchor in self.tabs:
            for tab in list(self.tabs[self.tabs.index(anchor) + 1 :]):
                self._close_tab_object(tab)

    def _handle_group_rename_request(self, tab_index: int):
        name, ok = QInputDialog.getText(self, "Tab Group", "Group name:")
        if ok:
            group = name.strip() or None
            if group:
                self._set_tab_group_existing(tab_index, group)
            else:
                self._clear_tab_group(tab_index)

    def _set_tab_group_existing(self, tab_index: int, group_name: str):
        if 0 <= tab_index < len(self.tabs) and self.tabs[tab_index].pinned:
            self.status_bar.showMessage("Unpin the tab before adding it to a group.")
            return
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
        for i, tab in enumerate(self.tabs):
            self.tab_bar.set_tab_group(i, tab.group)
            group = self.tab_manager.groups.get(tab.group_id)
            visible = not (group and group.collapsed and group.group_id in seen)
            self.tab_bar.set_tab_visible(i, visible)
            if visible:
                visible_indices.append(i)
            if group:
                seen.add(group.group_id)
        self.tab_bar.set_collapsed_groups(
            group.name for group in self.tab_manager.groups.values() if group.collapsed
        )
        self.tab_bar.set_group_colors(
            {
                group.name: GROUP_COLORS[group.color]
                for group in self.tab_manager.groups.values()
                if group.color in GROUP_COLORS
            }
        )
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

    def set_group_color(self, group_id, color):
        if group_id not in self.tab_manager.groups or (
            color and color not in GROUP_COLORS
        ):
            return
        self.tab_manager.groups[group_id].color = color
        self.tab_manager.save_groups()
        self._sync_tab_group_labels()

    def save_and_close_group(self, group_id):
        """Keep a group's pages to reopen later, then close its tabs."""
        group = self.tab_manager.groups.get(group_id)
        if group is None or self.tab_manager.private:
            return None
        members = [tab for tab in self.tabs if tab.group_id == group_id]
        pages = [
            {"url": tab.url().toString(), "title": tab.title()}
            for tab in members
            if tab.url().scheme() in ("http", "https", "file")
        ]
        try:
            saved = self.tab_manager.save_closed_group(group, pages)
        except ValueError as error:
            QMessageBox.warning(self, "Save Group", str(error))
            return None
        if len(members) == len(self.tabs):
            # The window keeps a tab of its own.
            self._new_tab()
        for tab in members:
            self._close_tab_object(tab)
        self.tab_manager.groups.pop(group_id, None)
        self.tab_manager.save_groups()
        self._sync_tab_group_labels()
        self._save_current_tabs()
        self.status_bar.showMessage(
            f"Saved “{group.name}” — reopen it from Tabs → Saved Groups", 6000
        )
        return saved

    def _session_history(self):
        from session_history import SessionHistory

        if self.tab_manager is None:
            return None
        return SessionHistory(self.tab_manager.profile_dir)

    def _record_session(self, kind):
        """Snapshot every window of this profile, or this window as closed."""
        history = self._session_history()
        if history is None or history.private:
            return None
        from dataclasses import asdict

        if kind == "closed_window":
            windows = [self.tab_manager.tab_states]
        else:
            windows = [w for w in self.tab_manager._windows().values() if w]
        groups = [asdict(g) for g in self.tab_manager.groups.values()]
        try:
            return history.record(kind, windows, groups)
        except OSError:
            logger.exception("Could not record the session")
            return None

    def restore_session_entry(self, entry_id):
        """Open an entry's windows (tabs load when selected); a closed window
        leaves the list once reopened."""
        history = self._session_history()
        entry = next((e for e in history.entries() if e["id"] == entry_id), None)
        if entry is None:
            return []
        # Groups the entry's tabs belong to come back too.
        from tab_manager import TabGroup

        groups = self.tab_manager.groups
        for group in entry["groups"]:
            if group["group_id"] not in groups:
                taken = {g.name for g in groups.values()}
                name, number = group["name"], 2
                while name in taken:
                    name, number = f"{group['name']} {number}", number + 1
                groups[group["group_id"]] = TabGroup(
                    group["group_id"], name, group["collapsed"], group["color"]
                )
        self.tab_manager.save_groups()
        if entry["kind"] == "closed_window":
            history.remove(entry_id)
        profile = self.profile_manager.current_profile_name
        return [self._open_window(profile, states) for states in entry["windows"]]

    def reopen_closed_window(self):
        history = self._session_history()
        closed = next(
            (
                e
                for e in (history.entries() if history else [])
                if e["kind"] == "closed_window"
            ),
            None,
        )
        if closed is None:
            self.status_bar.showMessage("No closed windows to reopen.", 4000)
            return []
        return self.restore_session_entry(closed["id"])

    def _open_session_history(self):
        from ui.session_history_dialog import SessionHistoryDialog

        if self.tab_manager is None or self.tab_manager.private:
            QMessageBox.information(
                self, "Session History", "Incognito windows keep no session history."
            )
            return None
        dialog = SessionHistoryDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        return self._present_dialog(dialog, modal=False)

    def save_all_tabs_as_group(self, name=None):
        """Keep this window's web pages as a saved group; the tabs stay open."""
        from datetime import datetime
        from uuid import uuid4

        from tab_manager import TabGroup

        if self.tab_manager is None or self.tab_manager.private:
            self.status_bar.showMessage("Incognito windows do not save groups.", 5000)
            return None
        if name is None:
            default = "Tabs " + datetime.now().strftime("%b %d %H:%M")
            name, accepted = QInputDialog.getText(
                self, "Save All Tabs", "Name the saved group:", text=default
            )
            if not accepted:
                return None
        name = " ".join(str(name).split())[:100] or "Saved tabs"
        pages = [
            {"url": tab.url().toString(), "title": tab.title()}
            for tab in self.tabs
            if tab.url().scheme() in ("http", "https", "file")
            and not tab.workspace_name
        ]
        try:
            saved = self.tab_manager.save_closed_group(
                TabGroup(uuid4().hex, name), pages
            )
        except ValueError as error:
            QMessageBox.warning(self, "Save All Tabs", str(error))
            return None
        self.status_bar.showMessage(
            f"Saved {len(saved['tabs'])} tabs as “{name}” (Tabs → Saved Groups)", 6000
        )
        return saved

    def open_saved_group(self, group_id):
        """Reopen a saved group; its tabs load when first selected."""
        saved = self.tab_manager.take_saved_group(group_id)
        if saved is None:
            return None
        names = {g.name for g in self.tab_manager.groups.values()}
        name, number = saved["name"], 2
        while name in names:
            name, number = f"{saved['name']} {number}", number + 1
        group = self.tab_manager.ensure_group(name)
        group.color = saved["color"]
        self.tab_manager.save_groups()
        opened = []
        self._restoring_tabs = True
        try:
            for page in saved["tabs"]:
                tab = self._new_tab(
                    url=page["url"],
                    title=page["title"] or page["url"],
                    index=len(self.tabs),
                    return_tab=True,
                    defer=True,
                )
                if tab is not None:
                    tab.group, tab.group_id = group.name, group.group_id
                    opened.append(tab)
        finally:
            self._restoring_tabs = False
        self._sync_tab_group_labels()
        if opened:
            self._switch_tab(self.tabs.index(opened[0]))
        self._save_current_tabs()
        return group

    def _fill_saved_groups_menu(self):
        menu = self.saved_groups_menu
        menu.clear()
        saved = self.tab_manager.saved_groups() if self.tab_manager else []
        if not saved:
            empty = menu.addAction(
                "No saved groups (right-click a group → Save and Close Group)"
            )
            empty.setEnabled(False)
            return
        for group in saved:
            count = len(group["tabs"])
            entry = menu.addMenu(f"{group['name']} ({count} tab{'s' * (count != 1)})")
            entry.addAction(
                "Open", lambda gid=group["group_id"]: self.open_saved_group(gid)
            )
            entry.addAction(
                "Delete",
                lambda gid=group["group_id"]: self.tab_manager.delete_saved_group(gid),
            )

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
            self.tab_bar.move_tab(index, min(position + offset, len(self.tabs) - 1))
        self._sync_tab_group_labels()
        self._save_current_tabs()

    def _manage_tab_groups(self):
        return self._open_management("tab-groups")

    def _view_cookies(self):
        return self._open_management("cookies")

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
    def _dispose_tab(tab: BrowserTab, unload: bool = True) -> None:
        """Stop a tab and detach its callbacks before scheduling deletion."""
        if tab.reader_panel is not None:
            tab.reader_panel.close_reader()
        mini = getattr(tab, "mini_player", None)
        if mini is not None and not sip.isdeleted(mini):
            mini.close()
        if media_controls.controller().tab is tab:
            media_controls.controller().forget()
        tab.browser_window._take_back_tab(tab)
        window = tab.browser_window
        window.devtools.close(tab)
        window._detach_workspace(tab)
        window._return_agent_sidebar(tab)
        if window.web_chat_dock and window.web_chat_dock.view is tab:
            window.web_chat_dock.release()
        for signal in (
            tab.urlChanged,
            tab.titleChanged,
            tab.loadProgress,
            tab.loadStarted,
            tab.loadFinished,
            tab.iconChanged,
        ):
            signal.disconnect()
        tab.hide()
        from page_unload import run_unload_handlers

        def delete(*_):
            if not sip.isdeleted(tab):
                tab.stop()
                # BrowserPage is parented to the view and is destroyed with it.
                tab.deleteLater()

        # Sites save state on unload (Discord keeps its sign-in there).
        if unload:
            run_unload_handlers([tab.page()], delete)
        else:
            delete()

    # ---------- Close handling ----------
    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        import memory_trim

        memory_trim.schedule()
        # Downloads are shared by every window on this registry, so the decision is
        # "will closing this window cancel live downloads?" That is true exactly
        # when every owner is being closed; otherwise the remaining window keeps
        # them running and there is nothing to confirm.
        closing_owners = {self.profile_manager}
        self._child_windows = [w for w in self._child_windows if not sip.isdeleted(w)]
        pending_windows = list(self._child_windows)
        while pending_windows:
            child = pending_windows.pop()
            if sip.isdeleted(child):
                continue
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
        # Let sites save state before their pages are destroyed below.
        from page_unload import run_unload_handlers_now

        run_unload_handlers_now([tab.page() for tab in self.tabs])
        if self._closing or sip.isdeleted(self):
            event.accept()
            return
        self._closing = True
        self._newtab_hover_timer.stop()
        if self._html_menu is not None:
            self._html_menu.close()
        for host in list(self._html_dialogs):
            host.reject()
        if self.settings_dialog is not None:
            self.settings_dialog.reject()
        if self.data_panels_dock is not None:
            self.data_panels_dock.refresh()
            self.data_panels_dock.dispose()
        if self.app_panel is not None:
            self.app_panel.dispose()
        if self.web_chat_dock is not None:
            self.web_chat_dock.dispose()
        if self.file_library_dialog is not None:
            self.file_library_dialog.reject()
        if self.agent_sidebar is not None:
            self.agent_sidebar.detach()
        self.find_bar.close_find(clear_query=True)
        self.find_bar.set_page(None)
        self.tab_bar.dispose()
        self.devtools.dispose()
        if self._palette is not None:
            self._palette.dispose()
        if self.history is not None:
            self.history.changed.disconnect(self._refresh_address_history)

        self._save_current_tabs()
        # Before leaving the session: a window closed while others stay open
        # can be reopened; quitting records the whole session once more.
        self._record_session(
            "closed_window"
            if self.tab_manager is not None
            and self.tab_manager.others_open()
            and not self._keep_session
            else "snapshot"
        )
        self.config.update(
            {"window_width": self.width(), "window_height": self.height()}
        )

        for win in list(self._child_windows):
            if not sip.isdeleted(win):
                # Closing with their opener is quitting, not discarding them.
                win._keep_session = True
                win.close()
        if (
            self.tab_manager is not None
            and not self.profile_manager.is_search_profile()
        ):
            self.tab_manager.release(keep=self._keep_session or self._opener is None)

        from ui.task_watch import TaskWatchDialog

        for watcher in self.findChildren(TaskWatchDialog):
            watcher.close()

        if self.cookie_manager is not None:
            self.cookie_manager.close()
            self.cookie_manager = None
        # On a real screen each page gives back its graphics surface as it is
        # destroyed (tens of milliseconds a tab); go away first, then clean up.
        self.hide()
        for tab in list(self.tabs):
            # Their unload handlers already ran above.
            self._dispose_tab(tab, unload=False)

        self.tabs.clear()
        self.current_tab_index = 0
        # Qt requires pages to be destroyed before their profiles. Profile
        # destruction also flushes the engine's persistent cookies and storage.
        # Include tabs already awaiting deferred deletion after a profile switch.
        # Explicit destruction here also works while a nested dialog is closing.
        for tab in self.tab_container.findChildren(BrowserTab):
            sip.delete(tab)
        page = getattr(self, "_retry_page", None)
        if page is not None and not sip.isdeleted(page):
            sip.delete(page)
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
        self.loading_indicator.hide()

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
        self._apply_page_style()

    def keyPressEvent(self, event):
        if self._was_fullscreen and event.key() == Qt.Key.Key_Escape:
            self._exit_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_settings(self, section="general", *, intent="", _dialog_only=False):
        if self._closing:
            return
        if self._menu_anchor is not None and not _dialog_only:
            dialog = SettingsDialog(
                self.config, self, self.profile_manager.current_profile.httpUserAgent()
            )
            dialog.applied.connect(self._settings_applied)
            dialog.open_section(
                section if isinstance(section, str) else "general", intent=intent
            )
            self._present_dialog(dialog, modal=False)
            return dialog
        if not _dialog_only:
            self._new_tab(url="steed:settings")
            if self.settings_dialog is not None:
                self.settings_dialog.open_section(
                    section if isinstance(section, str) else "general", intent=intent
                )
            return self.settings_dialog
        if self.settings_dialog is None:
            try:
                dialog = SettingsDialog(
                    self.config,
                    self,
                    self.profile_manager.current_profile.httpUserAgent(),
                )
            except (OSError, ValueError, RuntimeError):
                QMessageBox.warning(
                    self, "Settings", "The settings workspace could not be opened."
                )
                return
            self.settings_dialog = dialog
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.applied.connect(self._settings_applied)
            dialog.finished.connect(lambda: setattr(self, "settings_dialog", None))
            dialog.finished.connect(lambda: self._workspace_finished(dialog))
        self.settings_dialog.open_section(
            section if isinstance(section, str) else "general", intent=intent
        )
        if _dialog_only:
            return self.settings_dialog

    def _open_page_style(self):
        if self._menu_anchor is not None:
            from ui.style_page import StylePage

            dialog = QDialog(self)
            dialog.setWindowTitle("Page Style")
            page = StylePage(self)
            QVBoxLayout(dialog).addWidget(page)
            dialog.surface = page.surface
            dialog.finished.connect(lambda _: page.dispose())
            self._present_dialog(dialog, modal=False)
            return dialog
        return self._new_tab(url="steed:style", return_tab=True)

    def _show_html_menu(self, menu, anchor, *, context=False):
        from ui.html_menu import HtmlMenu

        if self._closing:
            return
        if self._html_menu is not None:
            self._html_menu.close()
        from ui.interface_mode import native

        if native():
            # Plain Qt menu; a one-off right-click menu is freed once it closes.
            if context:
                menu.aboutToHide.connect(
                    menu.deleteLater, Qt.ConnectionType.QueuedConnection
                )
            menu.popup(anchor)
            return menu
        popup = HtmlMenu(menu, self, anchor, context=context)
        if context:
            # One-off right-click menus live only as long as their popup.
            popup.finished.connect(lambda _: menu.deleteLater())
        self._html_menu = popup
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup.finished.connect(
            lambda _: (
                setattr(self, "_html_menu", None) if self._html_menu is popup else None
            )
        )
        popup.show()
        return popup

    def _present_dialog(self, dialog, *, modal=True, force_html=False):
        from ui.html_dialog import HtmlDialog

        host = getattr(dialog, "_html_host", None)
        if host is None or sip.isdeleted(host) or host.closed:
            host = HtmlDialog(dialog, self, anchor=self._menu_anchor)
            dialog._html_host = host
            self._html_dialogs.append(host)
            host.finished.connect(
                lambda _: (
                    self._html_dialogs.remove(host)
                    if host in self._html_dialogs
                    else None
                )
            )
            host.finished.connect(host.deleteLater)
        if modal:
            return host.exec()
        host.show()
        host.raise_()
        host.activateWindow()
        return host

    def _builtin_menu_page(self, tab):
        return (
            not self._closing
            and tab in self.tabs
            and tab is self.tabs[self.current_tab_index]
            and tab.native_panel is None
            and not getattr(tab, "agent_panel", False)
            and page_name(tab.url()) in ("newtab", "welcome")
            and not self.profile_manager.registry.start_pages.state(
                self.profile_manager.current_profile_name
            )["html" if page_name(tab.url()) == "newtab" else "welcome_html"]
        )

    def _newtab_menu_at(self, tab, name, *, hover=False):
        """Read trigger geometry from the maintained page, never authored HTML."""
        if name not in self._layout_menus or not self._builtin_menu_page(tab):
            return
        profile = self.profile_manager.current_profile
        page = tab.page()
        selector = f'.browser-menu a[href="steed://action/menu?name={name}"]'
        script = (
            "(() => { const e=document.querySelector("
            + json.dumps(selector)
            + "); if (!e || !e.getClientRects().length) return null; "
            + ("if (!e.matches(':hover')) return null; " if hover else "")
            + "const r=e.getBoundingClientRect(); return [r.right,r.top,innerWidth,innerHeight]; })()"
        )

        def opened(point):
            if (
                not sip.isdeleted(self)
                and not self._closing
                and not sip.isdeleted(tab)
                and self._builtin_menu_page(tab)
                and self.profile_manager.current_profile is profile
                and tab.page() is page
                and self.isActiveWindow()
                and (not hover or self._html_menu is None)
                and isinstance(point, list)
                and len(point) == 4
                and all(type(value) in (int, float) for value in point)
                and point[2] > 0
                and point[3] > 0
                and 0 <= point[0] <= point[2]
                and 0 <= point[1] <= point[3]
            ):
                self._newtab_hover = (tab, name)
                self._newtab_hover_opened = True
                self._show_html_menu(
                    self._layout_menus[name],
                    tab.mapToGlobal(
                        QPoint(
                            int(point[0] * tab.width() / point[2]),
                            int(point[1] * tab.height() / point[3]),
                        )
                    ),
                )

        page.runJavaScript(script, opened)

    def _poll_newtab_menu(self):
        """Long hover applies only inside maintained New Tab, not desktop menus."""
        if self._closing or not self.tabs:
            self._newtab_hover = None
            return
        if (
            self._html_menu is not None
            or self._newtab_hover_pending
            or not self.isActiveWindow()
        ):
            return
        tab = self.tabs[self.current_tab_index]
        if not self._builtin_menu_page(tab):
            self._newtab_hover = None
            return
        profile = self.profile_manager.current_profile
        self._newtab_hover_pending = True

        def hovered(value):
            # The page answers asynchronously; the window may have closed since.
            if sip.isdeleted(self) or self._closing:
                return
            self._newtab_hover_pending = False
            if (
                sip.isdeleted(tab)
                or not self._builtin_menu_page(tab)
                or self.profile_manager.current_profile is not profile
                or self._html_menu is not None
                or not self.isActiveWindow()
            ):
                return
            key = (tab, value) if isinstance(value, str) else None
            if key != self._newtab_hover:
                self._newtab_hover, self._newtab_hover_since = key, monotonic()
                self._newtab_hover_opened = False
            elif (
                key is not None
                and not self._newtab_hover_opened
                and monotonic() - self._newtab_hover_since >= 0.65
            ):
                self._newtab_hover_opened = True
                if value == "summary":
                    tab.page().runJavaScript(
                        "document.querySelector('.browser-menu').open=true"
                    )
                else:
                    self._newtab_menu_at(tab, value, hover=True)

        tab.page().runJavaScript(
            "(() => { const s=document.querySelector('.browser-menu summary:hover'); "
            "if(s && !s.parentElement.open) return 'summary'; "
            "const a=document.querySelector('.browser-menu a:hover'); "
            "return a ? new URL(a.href).searchParams.get('name') : null; })()",
            hovered,
        )

    def _open_management(self, name):
        from ui.management_pages import PAGES, controller

        if self._closing or name not in PAGES:
            return
        # Passwords is a full page with no dialog form.
        if self._menu_anchor is not None and name != "passwords":
            source = self.tabs[self.current_tab_index] if self.tabs else None
            try:
                dialog = controller(self, name, source)
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                return self._present_dialog(dialog, modal=False)
            except (ValueError, OSError, sqlite3.Error) as error:
                QMessageBox.warning(self, PAGES[name], str(error))
                return
        return self._new_tab(url="steed:" + name, return_tab=True)

    def _embed_workspace(self, tab, name):
        if name == "settings":
            controller = self._open_settings(_dialog_only=True)
            if controller is None:
                return
            controller.hide()
            panel = controller.surface
        elif name == "style":
            from ui.style_page import StylePage

            controller = panel = StylePage(self)
        elif name == "memory":
            from ui.memory_page import MemoryPage

            controller = panel = MemoryPage(self)
        elif name == "userscripts":
            from ui.user_scripts_page import UserScriptsPage

            controller = panel = UserScriptsPage(self)
        elif name == "features":
            from ui.features_page import FeaturesPage

            controller = panel = FeaturesPage(self)
        elif name == "access":
            from ui.access_page import AccessPage

            controller = panel = AccessPage(self)
        elif name == "passwords":
            from ui.passwords_page import PasswordsPage

            controller = panel = PasswordsPage(self)
        elif name == "apps":
            from ui.apps_page import AppsPage

            controller = panel = AppsPage(self)
        else:
            from ui.html_dialog import HtmlDialog, HtmlSettingsDialog
            from ui.management_pages import controller as create_controller

            try:
                source = create_controller(self, name, tab.agent_source)
            except (ValueError, OSError, sqlite3.Error) as error:
                source = HtmlSettingsDialog(self)
                source.page_unavailable = True
                source.setWindowTitle("Page unavailable")
                label = QLabel(str(error))
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                QVBoxLayout(source).addWidget(label)
            source.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            controller = panel = HtmlDialog(source, self, workspace=name)
            source._html_host = controller
            controller.finished.connect(lambda _: self._workspace_finished(controller))
        tab.workspace_name = name
        tab.workspace_controller = controller
        tab.native_panel = panel
        panel.setParent(tab)
        panel.setGeometry(tab.rect())
        panel.show()
        panel.raise_()
        from ui.management_pages import PAGES

        self.tab_bar.set_tab_title(self.tabs.index(tab), PAGES.get(name, name.title()))
        self.address_bar.set_url("steed:" + name)
        self._update_page_actions()
        self._update_zoom()
        self._update_loading_indicator()
        self._update_history_buttons()

    def _detach_workspace(self, tab):
        controller = tab.workspace_controller
        name = tab.workspace_name
        if name is None:
            return
        if self.find_bar._page is controller.surface.page:
            self.find_bar.set_page(None)
        tab.workspace_name = tab.workspace_controller = tab.native_panel = None
        if name == "settings":
            controller.layout().addWidget(controller.surface)
            controller.hide()
        else:
            controller.dispose()
            controller.hide()
            controller.deleteLater()

    def _workspace_finished(self, controller):
        # closeEvent still needs the workspace address for its session snapshot.
        # The normal tab disposal detaches it after saving that snapshot.
        if self._closing:
            return
        for tab in self.tabs:
            if tab.workspace_controller is controller:
                return_to = (
                    tab.agent_source
                    if tab.workspace_name == "passwords"
                    and controller.source.result() == QDialog.DialogCode.Accepted
                    else None
                )
                self._detach_workspace(tab)
                if return_to in self.tabs:
                    self._switch_tab(self.tabs.index(return_to))
                token = uuid4().hex
                tab.workspace_exit_token = token

                def leave(tab=tab, token=token):
                    if (
                        not self._closing
                        and tab in self.tabs
                        and getattr(tab, "workspace_exit_token", None) == token
                    ):
                        self._navigate_tab_to_url(tab, NEW_TAB)

                QTimer.singleShot(0, leave)

    def _set_bar(self, name, visible):
        styles = self.profile_manager.registry.page_style
        profile = self.profile_manager.current_profile_name
        value = styles.get(profile)
        value[name] = visible
        try:
            styles.save(profile, value)
        except (OSError, ValueError):
            QMessageBox.warning(
                self, "Appearance", "Bar visibility could not be saved."
            )
            self._apply_page_style()

    def _reset_page_style(self):
        from PyQt6.QtWidgets import QMessageBox as NativeMessageBox

        from services.page_style import DEFAULTS

        previous = self.styleSheet()
        self.setStyleSheet("")
        try:
            decision = NativeMessageBox.question(
                self,
                "Reset page style?",
                "Reset the root CSS, theme, bar visibility and loading indicator for this profile? Custom page HTML and images are preserved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        finally:
            self.setStyleSheet(previous)
        if decision != QMessageBox.StandardButton.Yes:
            return
        try:
            self.profile_manager.registry.page_style.save(
                self.profile_manager.current_profile_name, DEFAULTS
            )
        except (OSError, ValueError):
            QMessageBox.warning(self, "Appearance", "Page style could not be reset.")

    def _apply_page_style(self):
        if self._closing:
            return
        value = self.profile_manager.registry.page_style.get(
            self.profile_manager.current_profile_name
        )
        self.menu_bar_action.setChecked(value["menu_bar"])
        self.status_bar_action.setChecked(value["status_bar"])
        if not self.isFullScreen():
            self.menuBar().setVisible(value["menu_bar"])
            self.status_bar.setVisible(value["status_bar"])
        indicator = self.loading_indicator
        indicator.setTextVisible(value["loading_percent"])
        indicator.setFormat("%p%")
        indicator.setFixedHeight(
            max(18 if value["loading_percent"] else 1, value["loading_height"])
        )
        indicator.setStyleSheet(
            "QProgressBar { border: 0; border-radius: 0; background: transparent; text-align: center; }"
            "QProgressBar::chunk { border-radius: 0; background: "
            + value["loading_color"]
            + "; }"
        )
        self._update_loading_indicator()

    def _update_loading_indicator(self):
        if not hasattr(self, "loading_indicator"):
            return
        tab = (
            self.tabs[self.current_tab_index]
            if 0 <= self.current_tab_index < len(self.tabs)
            else None
        )
        enabled = self.profile_manager.registry.page_style.get(
            self.profile_manager.current_profile_name
        )["loading"]
        active = (
            tab is not None
            and tab.is_loading
            and not tab.workspace_name
            and not getattr(tab, "agent_panel", False)
        )
        self.loading_indicator.setValue(tab.load_progress if tab is not None else 0)
        self.loading_indicator.setVisible(
            bool(enabled and active and not self.isFullScreen())
        )

    def _appearance_changed(self):
        if self._closing:
            return
        self.apply_dark_mode()
        self._apply_page_style()
        if self.settings_dialog is not None:
            self.settings_dialog.refresh()
        for tab in self.tabs:
            if tab.workspace_name and tab.workspace_name != "settings":
                tab.workspace_controller.publish()
        self._start_page_changed(self.profile_manager.current_profile_name)

    def _settings_applied(self):
        try:
            self.profile_manager.refresh_settings()
        except (OSError, RuntimeError, ValueError):
            QMessageBox.warning(
                self,
                "Settings saved",
                "Preferences were saved, but some engine settings could not be applied. Restart Steed to retry them.",
            )
        self.profile_manager.registry.settings_changed.emit()

    def _settings_changed(self):
        if not self._closing:
            import memory_modes
            from ui.interface_mode import native

            self._memory = memory_modes.apply_process_policies(self.config)
            import page_unload

            page_unload.ENABLED = bool(self.config.get("save_before_closing", True))
            if self._palette is not None and self._palette.native != native():
                self._palette.dispose()
                self._palette.deleteLater()
                self._palette = None
            self._apply_memory_policies()
            self._refresh_address_sources()
            side = self.config.get("devtools_side", "right")
            if side != self.devtools.side:
                self.devtools.set_side(side)
            self.apply_dark_mode()
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
        self._refresh_address_sources()
        for tab in self.tabs:
            if page_name(tab.url()) is not None:
                tab.reload()

    def _customize_new_tab(self):
        return self._open_management("newtab-settings")

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

        dialog = BookmarkDialog(
            self.profile_manager.registry.start_pages,
            self.profile_manager.current_profile_name,
            self,
            url,
            title,
        )
        dialog.exec()

    def _manage_bookmarks(self):
        return self._open_management("bookmarks")

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
        elif action == "menu" and len(params) == 1 and params[0][0] == "name":
            self._newtab_menu_at(tab, params[0][1])
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
        # As with permissions, the picker must open outside this callback.
        QTimer.singleShot(0, lambda: self._show_desktop_picker(request))

    def _show_desktop_picker(self, request):
        try:
            self._pick_desktop_media(request)
        except Exception:
            # Never let an error escape into the event loop.
            logger.exception("Screen share picker failed")

    def _pick_desktop_media(self, request):
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
