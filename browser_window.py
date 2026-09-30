"""Main browser window."""

from PyQt6.QtWidgets import (
    QMainWindow,
    QApplication,
    QWidget,
    QVBoxLayout,
    QMenuBar,
    QMenu,
    QStatusBar,
    QMessageBox,
    QInputDialog,
    QSizePolicy,
    QDialog,
    QListWidget,
    QLabel,
    QPushButton,
    QHBoxLayout
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QModelIndex
from PyQt6.QtGui import QKeySequence, QAction
from pathlib import Path
from typing import List, Optional
from config import Config
from profile_manager import ProfileManager
from tab_manager import TabManager, TabState
from cookie_manager import CookieManager
from ui.address_bar import AddressBar
from ui.tab_bar import TabBar
from ui.cookie_dialog import CookieDialog
from ui.settings_dialog import SettingsDialog
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineDownloadRequest, QWebEngineSettings, QWebEngineFullScreenRequest

class BrowserPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        # Print media/codec errors to terminal
        print(f"[JS:{level}] {sourceID}:{lineNumber} {message}")
        super().javaScriptConsoleMessage(level, message, lineNumber, sourceID)


class BrowserTab(QWebEngineView):
    """Represents a single browser tab."""

    def __init__(self, profile, parent=None, window=None, initial_qurl: QUrl | None = None):
        super().__init__(parent)
        self.profile = profile
        self.window = window
        self.group = None
        page = BrowserPage(profile, self)
        self.setPage(page)
        # Enable plugins and PDF viewer
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)

        # Media / embeds / animation
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(settings.WebAttribute.ScreenCaptureEnabled, True)
        try:
            settings.setAttribute(QWebEngineSettings.WebAttribute.ImageAnimationEnabled, True)
        except Exception:
            pass
        
        if initial_qurl is not None and initial_qurl.isValid():
            self.setUrl(initial_qurl)


    def createWindow(self, window_type: QWebEnginePage.WebWindowType) -> 'BrowserTab':
        return self.window._new_tab(return_tab=True)

class BrowserWindow(QMainWindow):
    _download_connected_profile_ids = set()
    """Main browser window."""

    def __init__(self, profile_name: Optional[str] = None):
        super().__init__()
        self.config = Config()
        self.profile_manager = ProfileManager(self.config, profile_name)
        self.tab_manager = None
        self.cookie_manager = None
        self.tabs: List[BrowserTab] = []
        self.current_tab_index = 0
        self._restoring_tabs = False
        self._closed_tabs_stack: List[TabState] = []
        self._child_windows: List[BrowserWindow] = []
        self.setup_window()
        self.setup_menus()
        self.setup_ui()
        self.apply_dark_mode()

        self._was_fullscreen = False
        self._saved_window_state = None
        self._saved_geometry = None
        self._saved_vis = {}
        self._closing = False

        # Connect profile signals
        self.profile_manager.profile_changed.connect(self._on_profile_changed)
        self.profile_manager.profiles_updated.connect(self._update_profile_shortcuts)
        # Initialize profile and managers
        self._initialize_profile(self.profile_manager.current_profile_name)
        # Restore or create initial tab
        if self.config.get("restore_tabs_on_startup", True):
            self._restore_tabs()
        else:
            self._new_tab()

    def _initialize_profile(self, profile_name: str):
        # Setup TabManager and CookieManager for the given profile
        profile_path = self.profile_manager.get_profile_data_path(profile_name)
        self.tab_manager = TabManager(profile_path)
        profile = self.profile_manager.get_profile(profile_name)

        # Connect downloadRequested ONCE per profile object
        pid = int(profile.__hash__()) if hasattr(profile, "__hash__") else id(profile)
        if pid not in BrowserWindow._download_connected_profile_ids:
            profile.downloadRequested.connect(self._on_download_requested)
            BrowserWindow._download_connected_profile_ids.add(pid)

        self.cookie_manager = CookieManager(profile)
        # Connect signals for tab groups
        if self.tab_manager:
            self.tab_manager.tab_state_changed.connect(self._update_tab_bar_group_display)
            self.tab_manager.group_data_updated.connect(self._refresh_all_tab_bar_groups)

    def setup_window(self):
        self.setWindowTitle("Custom Browser")
        self.setGeometry(100, 100, self.config.get("window_width", 1200), self.config.get("window_height", 800))

    def setup_menus(self):
        menubar = self.menuBar()
        # File menu
        file_menu = menubar.addMenu("File")
        new_tab_action = QAction("New Tab", self)
        new_tab_action.setShortcut(QKeySequence("Ctrl+T"))
        new_tab_action.triggered.connect(self._new_tab)
        file_menu.addAction(new_tab_action)
        restore_tab_action = QAction("Restore Closed Tab", self)
        restore_tab_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        restore_tab_action.triggered.connect(self._restore_closed_tab)
        file_menu.addAction(restore_tab_action)
        close_tab_action = QAction("Close Tab", self)
        close_tab_action.setShortcut(QKeySequence("Ctrl+W"))
        close_tab_action.triggered.connect(self._close_current_tab)
        file_menu.addAction(close_tab_action)
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
        pop_search_action.triggered.connect(lambda: self._pop_out_profile_named("search"))
        profile_menu.addAction(pop_search_action)
        search_profile_action = QAction("Switch to Search Profile (Incognito)", self)
        search_profile_action.setShortcut(QKeySequence("Ctrl+Shift+0"))
        search_profile_action.triggered.connect(lambda: self._switch_to_profile("search"))
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
        # View menu
        # view_menu = menubar.addMenu("View")
        # dark_mode_action = QAction("Dark Mode", self, checkable=True)
        # dark_mode_action.setChecked(self.config.dark_mode)
        # dark_mode_action.triggered.connect(self._toggle_dark_mode)
        # view_menu.addAction(dark_mode_action)

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Tab bar
        self.tab_bar = TabBar()
        self.tab_bar.new_tab_requested.connect(self._new_tab)
        self.tab_bar.tab_changed.connect(lambda idx: self._switch_tab(idx))
        self.tab_bar.tab_close_requested.connect(self._close_tab)
        self.tab_bar.tab_moved.connect(self._on_tab_moved)
        self.tab_bar.group_rename_requested.connect(self._handle_group_rename_request)
        self.tab_bar.group_set_requested.connect(self._set_tab_group_existing)
        self.tab_bar.group_clear_requested.connect(self._clear_tab_group)
        layout.addWidget(self.tab_bar)
        # Address bar
        self.address_bar = AddressBar()
        self.address_bar.url_changed.connect(self._navigate_to_url)
        self.address_bar.back_requested.connect(self._go_back)
        self.address_bar.forward_requested.connect(self._go_forward)
        self.address_bar.reload_requested.connect(self._reload)
        self.address_bar.home_requested.connect(self._go_home)
        layout.addWidget(self.address_bar)
        # Tab container
        self.tab_container = QWidget()
        self.tab_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        if profile_name == "search":
            self.setWindowTitle("Custom Browser - Search Profile (Incognito)")
        else:
            self.setWindowTitle(f"Custom Browser - {profile_name}")
        self._update_profile_shortcuts()

    def _switch_profile(self):
        profiles = self.profile_manager.list_profiles() + ["search"]
        profile, ok = QInputDialog.getItem(self, "Switch Profile", "Select profile:", profiles, 0, False)
        if ok:
            self._switch_to_profile(profile)

    def _switch_to_profile(self, profile_name: str):
        # Save current tabs
        if self.tab_manager:
            self._save_current_tabs()
        # Switch profile
        self.profile_manager.switch_to_profile(profile_name)
        # Reinitialize managers for new profile
        self._initialize_profile(profile_name)
        # Clean current tabs UI
        for tab in self.tabs:
            page = tab.page()
            if page:
                page.deleteLater()
            tab.deleteLater()
        self.tabs.clear()
        self.tab_bar.clear_all_tabs()
        # Restore tabs for new profile
        self._restore_tabs()

    def _create_profile(self):
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if ok and name:
            name = name.strip()
            self.profile_manager.create_profile(name)
            self._switch_to_profile(name)

    def _rename_profile(self):
        profiles = self.profile_manager.list_profiles()
        if not profiles:
            return
        profile, ok = QInputDialog.getItem(self, "Rename Profile", "Select profile:", profiles, 0, False)
        if ok and profile:
            new_name, ok2 = QInputDialog.getText(self, "Rename Profile", f"New name for '{profile}':")
            if ok2 and new_name:
                if self.profile_manager.rename_profile(profile, new_name):
                    QMessageBox.information(self, "Success", f"Profile renamed to '{new_name}'")
                    self._update_profile_shortcuts()
                    self._switch_to_profile(new_name)  # <- add this
                    self._update_profile_shortcuts()
                else:
                    QMessageBox.warning(self, "Error", "Could not rename profile.")

    def _delete_profile(self):
        profiles = self.profile_manager.list_profiles()
        current = self.profile_manager.current_profile_name
        # Exclude current profile
        profiles = [p for p in profiles if p != current]
        if not profiles:
            QMessageBox.information(self, "No Profiles", "No profiles available to delete.")
            return
        profile, ok = QInputDialog.getItem(self, "Delete Profile", "Select profile to delete:", profiles, 0, False)
        if ok and profile:
            reply = QMessageBox.question(self, "Delete Profile",
                f"Delete profile '{profile}'? This will remove all data.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                if self.profile_manager.delete_profile(profile):
                    QMessageBox.information(self, "Deleted", f"Profile '{profile}' deleted.")
                    self._update_profile_shortcuts()
                else:
                    QMessageBox.warning(self, "Failed", "Could not delete profile.")

    def _pop_out_profile(self):
        profiles = self.profile_manager.list_profiles()
        profile, ok = QInputDialog.getItem(self, "Pop out Profile", "Select profile:", profiles, 0, False)
        if ok and profile:
            _pop_out_profile_named(profile)
    
    def _pop_out_profile_named(self, profile: str):
        win = BrowserWindow(profile_name=profile)
        win.show()
        self._child_windows.append(win)

    def _update_profile_shortcuts(self):
        self.profile_shortcuts_menu.clear()
        profiles = self.profile_manager.list_profiles()
        for i, name in enumerate(profiles[:9]):
            action = QAction(f"{name} (Ctrl+Shift+{i+1})", self)
            action.setShortcut(QKeySequence(f"Ctrl+Shift+{i+1}"))
            action.triggered.connect(lambda checked, n=name: self._switch_to_profile(n))
            self.profile_shortcuts_menu.addAction(action)

    # ---------- Tab handling ----------
    def _new_tab(self, url: Optional[str] = None, title: Optional[str] = None, return_tab: bool = False, index: Optional[int] = None):
        home = self.config.get("home_page", "https://www.google.com")
        if not home or home == "about:blank":
            home = "https://www.duckduckgo.com"

        if not url or url == "about:blank":
            url = home

        # Convert the input text to a QUrl using your existing rules
        qurl = self._text_to_qurl(url)  # add helper below

        profile = self.profile_manager.current_profile
        tab = BrowserTab(profile, self.tab_container, self, initial_qurl=qurl)
        self.tab_layout.addWidget(tab)
        # Connect permission and download signals
        tab.page().featurePermissionRequested.connect(self._on_permission_requested)
        tab.page().fullScreenRequested.connect(self._on_fullscreen_requested)
        # Screen share picker (Qt 6.7+)
        try:
            tab.page().desktopMediaRequested.connect(self._on_desktop_media_requested)
        except Exception as e:
            print("desktopMediaRequested connect failed:", e)

        script = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        """
        tab.page().runJavaScript(script)
        #tab.page().profile().downloadRequested.connect(self._on_download_requested)
        tab.urlChanged.connect(lambda u: self._on_tab_url_changed(tab, u))
        tab.titleChanged.connect(lambda t: self._on_tab_title_changed(tab, t))
        tab.loadProgress.connect(lambda p: self._on_load_progress(tab, p))
        tab.loadFinished.connect(lambda ok: self._on_load_finished(tab, ok))
        # Determine insertion index
        if index is None:
            index = self.current_tab_index + 1
        index = max(0, min(index, len(self.tabs)))

        self.tabs.insert(index, tab)
        self.tab_bar.insert_tab(index, title or "New Tab")
        self._switch_tab(index)
        self._sync_tab_group_labels()

        if not self._restoring_tabs:
            self._save_current_tabs()
        if return_tab:
            return tab

    def _close_tab(self, index: int):
        if len(self.tabs) <= 1:
            return
        tab = self.tabs.pop(index)
        self.tab_bar.remove_tab(index)
        page = tab.page()
        if page:
            page.deleteLater()
        tab.deleteLater()
        self.current_tab_index = max(0, index - 1)
        self._switch_tab(self.current_tab_index)
        self._save_current_tabs()
        self._sync_tab_group_labels()

    def _close_current_tab(self):
        self._close_tab(self.current_tab_index)

    def _restore_closed_tab(self):
        if self._closed_tabs_stack:
            state = self._closed_tabs_stack.pop()
            self._new_tab(url=state.url, title=state.title)

    def _on_tab_moved(self, from_idx: int, to_idx: int):
        tab = self.tabs.pop(from_idx)
        self.tabs.insert(to_idx, tab)
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
        self.address_bar.set_url(cur.url().toString())
        self.address_bar.set_back_enabled(cur.history().canGoBack())
        self.address_bar.set_forward_enabled(cur.history().canGoForward())
        self.status_bar.showMessage(f"Tab {index+1} of {len(self.tabs)}")

    def _navigate_to_url(self, url_text: str):
        if not self.tabs:
            return
        self._navigate_tab_to_url(self.tabs[self.current_tab_index], url_text)

    def _navigate_tab_to_url(self, tab: BrowserTab, url_text: str):
        url_text = url_text.strip()
        if url_text.startswith(("http://", "https://", "file://", "about:")):
            url = QUrl(url_text)
        elif "." in url_text and " " not in url_text:
            url = QUrl(f"https://{url_text}")
        else:
            search = self.config.get("search_engine", "https://www.google.com/search?q=")
            url = QUrl(f"{search}{url_text}")
        tab.setUrl(url)
    
    def _text_to_qurl(self, url_text: str) -> QUrl:
        url_text = (url_text or "").strip()
        if url_text.startswith(("http://", "https://", "file://", "about:")):
            return QUrl(url_text)
        if "." in url_text and " " not in url_text:
            return QUrl(f"https://{url_text}")
        search = self.config.get("search_engine", "https://www.google.com/search?q=")
        return QUrl(f"{search}{url_text}")


    def _go_back(self):
        if self.tabs:
            self.tabs[self.current_tab_index].back()
    def _go_forward(self):
        if self.tabs:
            self.tabs[self.current_tab_index].forward()
    def _reload(self):
        if self.tabs:
            self.tabs[self.current_tab_index].reload()
    def _go_home(self):
        home = self.config.get("home_page", "https://www.google.com")
        self._navigate_to_url(home)

    def _on_tab_url_changed(self, tab: BrowserTab, url: QUrl):
        if self._closing:
            return
        if not self.tabs or not (0 <= self.current_tab_index < len(self.tabs)):
            return

        # If we're browsing in global, remember current host so host-only cookies can be mapped correctly
        if self.profile_manager.is_global_profile():
            self.profile_manager.note_global_active_url(url)

        if tab == self.tabs[self.current_tab_index]:
            self.address_bar.set_url(url.toString())

    def _on_tab_title_changed(self, tab: BrowserTab, title: str):
        if tab in self.tabs:
            idx = self.tabs.index(tab)
            if len(title) > 30:
                title = title[:27] + "..."
            self.tab_bar.set_tab_title(idx, title)
    def _on_load_progress(self, tab: BrowserTab, progress: int):
        if tab == self.tabs[self.current_tab_index]:
            self.status_bar.showMessage(f"Loading... {progress}%")
    def _on_load_finished(self, tab: BrowserTab, ok: bool):
        if tab == self.tabs[self.current_tab_index]:
            self.status_bar.showMessage("Ready" if ok else "Error loading page")
        self._save_current_tabs()

    def _save_current_tabs(self):
        if self._restoring_tabs or self.profile_manager.is_search_profile():
            return
        states = []
        for i, tab in enumerate(self.tabs):
            group = getattr(tab, "group", None)
            url = tab.url().toString()
            if not url or url == "about:blank":
                url = self.config.get("home_page", "https://www.google.com")
            title = self.tab_bar.tab_bar.tabText(i) if i < self.tab_bar.tab_bar.count() else tab.title()
            if not title or title == "about:blank":
                title = "New Tab"
            
            states.append(TabState(url=url, title=title, group=group))

        self.tab_manager.save_tabs(states)

    def _restore_tabs(self):
        if self.profile_manager.is_search_profile():
            self._new_tab()
            return
        saved = self.tab_manager.get_saved_tabs()
        self._restoring_tabs = True
        if saved:
            for state in saved:
                self._new_tab(url=state.url, title=state.title, index=len(self.tabs))
                # after the tab is created and index is known
                tab = self.tabs[-1]
                tab.group = getattr(state, "group", None)
                self._sync_tab_group_labels()
        else:
            self._new_tab(index=len(self.tabs))

        self._restoring_tabs = False
        self._switch_tab(0)
        self._sync_tab_group_labels()

    # ---------- Permission & Download ----------
    def _on_permission_requested(self, url, feature):
        feature_names = {
            QWebEnginePage.Feature.MediaAudioCapture: "Microphone",
            QWebEnginePage.Feature.MediaVideoCapture: "Camera",
            QWebEnginePage.Feature.MediaAudioVideoCapture: "Microphone and Camera",
            QWebEnginePage.Feature.DesktopVideoCapture: "Screen Share",
            QWebEnginePage.Feature.DesktopAudioVideoCapture: "Screen Share with Audio",
            QWebEnginePage.Feature.Notifications: "Notifications",
            QWebEnginePage.Feature.Geolocation: "Location",
        }
        name = feature_names.get(feature, "Unknown")
        reply = QMessageBox.question(self, "Permission Request",
            f"{url.toString()} requests {name}. Allow?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        tab = self.tabs[self.current_tab_index]
        if reply == QMessageBox.StandardButton.Yes:
            tab.page().setFeaturePermission(url, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)
        else:
            tab.page().setFeaturePermission(url, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)

    def _on_download_requested(self, download: QWebEngineDownloadRequest):
        url = download.url().toString()
        name = download.suggestedFileName()
        size = download.totalBytes()
        size_str = f"{size/1024:.2f} KB" if size > 0 else "Unknown"
        info = f"URL: {url}<br>Name: {name}<br>Size: {size_str}"
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Download Request")
        dlg.setText("A download was requested:")
        dlg.setInformativeText(info)
        dlg.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        if dlg.exec() == QMessageBox.StandardButton.Save:
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getSaveFileName(self, "Save File", name)
            if path:
                download.setDownloadDirectory(str(Path(path).parent))
                download.setDownloadFileName(Path(path).name)
                download.accept()
        else:
            download.cancel()

    # ---------- Tab Group UI ----------
    def _handle_group_rename_request(self, tab_index: int):
        name, ok = QInputDialog.getText(self, "Tab Group", "Group name:")
        if ok:
            group = name.strip() or None
            self.tabs[tab_index].group = group
            self._sync_tab_group_labels()
            self._save_current_tabs()
    
    def _set_tab_group_existing(self, tab_index: int, group_name: str):
        if 0 <= tab_index < len(self.tabs):
            self.tabs[tab_index].group = group_name
            self._sync_tab_group_labels()
            self._save_current_tabs()


    def _clear_tab_group(self, tab_index: int):
        self.tabs[tab_index].group = None
        self._sync_tab_group_labels()
        self._save_current_tabs()

    def _sync_tab_group_labels(self):
        for i, tab in enumerate(self.tabs):
            self.tab_bar.set_tab_group(i, getattr(tab, "group", None))

    def _manage_tab_groups(self):
        QMessageBox.information(self, "Not Implemented", "Tab Group management UI not implemented yet.")

    def _toggle_dark_mode(self, enabled: bool):
        self.config.dark_mode = enabled
        self.apply_dark_mode()

    def _view_cookies(self):
        if not self.cookie_manager:
            return
        cookies = self.cookie_manager.get_all_cookies()
        dlg = CookieDialog(cookies, self)
        dlg.exec()

    def _clear_all_cookies(self):
        if not self.cookie_manager:
            return
        reply = QMessageBox.question(self, "Clear Cookies", "Delete all cookies for this profile?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.cookie_manager.clear_all_cookies()

    # ---------- Tab Bar group display updates ----------
    def _update_tab_bar_group_display(self, index: int):
        if self.tab_manager and 0 <= index < len(self.tab_manager.tab_states):
            state = self.tab_manager.tab_states[index]
            self.tab_bar.set_tab_group(index, state.group)

    def _refresh_all_tab_bar_groups(self):
        for i in range(len(self.tabs)):
            self._update_tab_bar_group_display(i)

    # ---------- Close handling ----------
    def closeEvent(self, event):
        self._closing = True  # <-- add this first

        self._save_current_tabs()
        self.config.set("window_width", self.width())
        self.config.set("window_height", self.height())

        for win in self._child_windows:
            win.close()

        try:
            if self.profile_manager.current_profile:
                self.profile_manager.current_profile.cookieStore().flushStore()
        except Exception:
            pass

        try:
            self.profile_manager.flush_all_cookie_stores()
        except Exception:
            pass

        for tab in list(self.tabs):
            # Disconnect signals so late emissions don't hit handlers while closing
            try: tab.urlChanged.disconnect()
            except Exception: pass
            try: tab.titleChanged.disconnect()
            except Exception: pass
            try: tab.loadProgress.disconnect()
            except Exception: pass
            try: tab.loadFinished.disconnect()
            except Exception: pass

            try:
                tab.stop()
            except Exception:
                pass
            try:
                p = tab.page()
                if p:
                    p.deleteLater()
            except Exception:
                pass
            tab.deleteLater()

        self.tabs.clear()
        self.current_tab_index = 0
        QApplication.processEvents()

        event.accept()

    
    def _on_fullscreen_requested(self, request: QWebEngineFullScreenRequest):
        request.accept()

        if request.toggleOn():
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
            "status": self.status_bar.isVisible() if self.statusBar() else True,
        }

        if self.menuBar():
            self.menuBar().setVisible(False)
        self.tab_bar.setVisible(False)
        self.address_bar.setVisible(False)
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
        if self.statusBar():
            self.statusBar().setVisible(self._saved_vis.get("status", True))
    
    def keyPressEvent(self, event):
        if self._was_fullscreen and event.key() == Qt.Key.Key_Escape:
            self._exit_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)
    
    def _open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            dlg.apply()

            # Apply dark mode immediately
            self.apply_dark_mode()

            # Apply UA immediately to current profiles (best-effort)
            ua = self.config.get("user_agent_override", "")
            try:
                # current profile
                if self.profile_manager.current_profile:
                    self.profile_manager.current_profile.setHttpUserAgent(ua)
            except Exception:
                pass
            # try global + search too
            for name in ("global", "search"):
                try:
                    self.profile_manager.get_profile(name).setHttpUserAgent(ua)
                except Exception:
                    pass

            # Optional: if they set cookie sharing to all_profiles, propagate now if your ProfileManager supports it
            try:
                mode = self.config.get("global_cookie_sharing_mode", "on_switch")
                if mode == "all_profiles" and hasattr(self.profile_manager, "propagate_global_cookies_to_all_profiles"):
                    self.profile_manager.propagate_global_cookies_to_all_profiles()
            except Exception:
                pass

    def _on_desktop_media_requested(self, request):
        print("desktopMediaRequested fired")  # debug: you should see this in terminal

        # Keep it alive while dialog is open
        self._pending_desktop_request = request

        dlg = QDialog(self)
        dlg.setWindowTitle("Choose what to share")
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(dlg)

        screens_model = request.screensModel()
        windows_model = request.windowsModel()

        layout.addWidget(QLabel("Screens:"))
        screens_list = QListWidget()
        for r in range(screens_model.rowCount()):
            screens_list.addItem(str(screens_model.data(screens_model.index(r, 0))))
        layout.addWidget(screens_list)

        layout.addWidget(QLabel("Windows:"))
        windows_list = QListWidget()
        for r in range(windows_model.rowCount()):
            windows_list.addItem(str(windows_model.data(windows_model.index(r, 0))))
        layout.addWidget(windows_list)

        btns = QHBoxLayout()
        share_screen = QPushButton("Share selected screen")
        share_window = QPushButton("Share selected window")
        cancel = QPushButton("Cancel")
        btns.addWidget(share_screen)
        btns.addWidget(share_window)
        btns.addStretch(1)
        btns.addWidget(cancel)
        layout.addLayout(btns)

        # Disable buttons if nothing available
        share_screen.setEnabled(screens_model.rowCount() > 0)
        share_window.setEnabled(windows_model.rowCount() > 0)

        def do_cancel():
            try:
                if self._pending_desktop_request:
                    self._pending_desktop_request.cancel()
            except Exception:
                pass
            self._pending_desktop_request = None
            dlg.reject()

        def do_screen():
            row = screens_list.currentRow()
            if row >= 0 and self._pending_desktop_request:
                idx = screens_model.index(row, 0)  # QModelIndex required :contentReference[oaicite:2]{index=2}
                self._pending_desktop_request.selectScreen(idx)
                self._pending_desktop_request = None
                dlg.accept()

        def do_window():
            row = windows_list.currentRow()
            if row >= 0 and self._pending_desktop_request:
                idx = windows_model.index(row, 0)  # QModelIndex required :contentReference[oaicite:3]{index=3}
                self._pending_desktop_request.selectWindow(idx)
                self._pending_desktop_request = None
                dlg.accept()

        cancel.clicked.connect(do_cancel)
        dlg.rejected.connect(do_cancel)
        share_screen.clicked.connect(do_screen)
        share_window.clicked.connect(do_window)

        dlg.exec()
