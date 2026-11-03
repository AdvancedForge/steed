"""The App Panel: your own small HTML/JavaScript apps beside the page.

Each app is served at its own steed://app-<id>/ address in the window's
profile, so apps keep separate storage. They ask Steed for data through
steed://app-<id>/api/… under the Local pages access settings, and links they
open go to normal tabs.
"""

from PyQt6 import sip
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from services.start_pages import app_id


class AppPage(QWebEnginePage):
    """Stays on its app; web links and pop-ups open as ordinary tabs."""

    def __init__(self, profile, dock):
        super().__init__(profile, dock)
        self.dock = dock

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if not is_main_frame:
            return url.scheme() in ("http", "https", "data", "blob", "about")
        if app_id(url) is not None:
            return True
        if url.scheme() in ("http", "https"):
            self.dock.open_link(url)
        return False

    def createWindow(self, kind):
        # A pop-up's first navigation becomes a normal tab.
        page = QWebEnginePage(self.profile(), self)

        def opened(url):
            if url.scheme() in ("http", "https"):
                self.dock.open_link(url)
            page.deleteLater()

        page.urlChanged.connect(opened)
        return page


class AppPanelDock(QDockWidget):
    def __init__(self, window):
        super().__init__("Apps", window)
        self.setObjectName("AppPanelDock")
        self.window = window
        self.pages = window.profile_manager.registry.start_pages
        self.page = None
        self.profile = None
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(6, 4, 6, 4)
        self.chooser = QComboBox(body)
        self.chooser.setAccessibleName("Panel app")
        self.chooser.currentIndexChanged.connect(self._chosen)
        header.addWidget(self.chooser, 1)
        manage = QToolButton(body)
        manage.setText("Manage…")
        manage.setToolTip("Add or edit panel apps (steed:apps)")
        manage.clicked.connect(
            lambda: self.window._new_tab(url="steed:apps", return_tab=True)
        )
        header.addWidget(manage)
        layout.addLayout(header)
        self.notice = QLabel(body)
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.notice)
        self.view = QWebEngineView(body)
        self.view.setAccessibleName("Panel app")
        layout.addWidget(self.view, 1)
        self.setWidget(body)
        self.pages.changed.connect(self._pages_changed)
        window.profile_manager.profile_changed.connect(lambda _: self.refresh())
        self.refresh()

    def open_link(self, url):
        if not self.window._closing:
            self.window._new_tab(url=url.toString())

    def _apps(self):
        name = self.window.profile_manager.current_profile_name
        if name == "search":
            return []
        return self.pages.state(name).get("apps", [])

    def refresh(self, select=None):
        """Rebuild the chooser for the current profile and load the app."""
        if sip.isdeleted(self):
            return
        profile = self.window.profile_manager.current_profile_name
        if profile != self.profile:
            # A page belongs to one profile; never keep it across a switch.
            self._drop_page()
            self.profile = profile
        apps = self._apps()
        wanted = select or self.window.config.get("panel_app", "")
        self.chooser.blockSignals(True)
        self.chooser.clear()
        for app in apps:
            self.chooser.addItem(app["name"], app["id"])
        index = max(0, self.chooser.findData(wanted))
        self.chooser.setCurrentIndex(index if apps else -1)
        self.chooser.blockSignals(False)
        self.chooser.setVisible(bool(apps))
        if profile == "search":
            self._message("Panel apps are not available in incognito windows.")
        elif not apps:
            self._message(
                "No panel apps yet. Choose Manage… to add one: a small HTML page "
                "with its own scripts, such as notes, a feed reader or a tracker."
            )
        else:
            if select:
                self.window.config.set("panel_app", apps[index]["id"])
            self._load(apps[index]["id"])

    def _message(self, text):
        self._drop_page()
        self.notice.setText(text)
        self.notice.show()
        self.view.hide()

    def _chosen(self, index):
        identity = self.chooser.itemData(index)
        if identity:
            self.window.config.set("panel_app", identity)
            self._load(identity)

    def _load(self, identity, *, force=False):
        url = QUrl(f"steed://app-{identity}/")
        if self.page is None:
            self.page = AppPage(self.window.profile_manager.current_profile, self)
            self.view.setPage(self.page)
        if force or self.page.url() != url:
            self.page.setUrl(url)
        self.notice.hide()
        self.view.show()

    def _pages_changed(self, profile):
        if sip.isdeleted(self) or profile != self.profile:
            return
        current = self.chooser.currentData()
        self.refresh(select=current)
        if current and self.page is not None:
            self._load(current, force=True)

    def show_app(self, identity):
        self.refresh(select=identity)
        self.show()
        self.raise_()

    def _drop_page(self):
        if self.page is not None:
            page, self.page = self.page, None
            self.view.setPage(QWebEnginePage(self.view))
            sip.delete(page)

    def dispose(self):
        """Pages go before their profile when the window closes."""
        try:
            self.pages.changed.disconnect(self._pages_changed)
        except TypeError:
            pass
        self._drop_page()
        sip.delete(self.view)
