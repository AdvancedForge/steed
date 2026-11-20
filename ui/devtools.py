"""Chrome-style Developer Tools for ordinary tabs: docked right, bottom or apart."""

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QSplitter, QStackedWidget, QVBoxLayout, QWidget

from browser import webengine

SIDES = ("right", "bottom", "window")
DEFAULT_SIDE = "right"

# Runs in the bundled DevTools frontend once its UI exists. Module paths match
# the frontend shipped with Qt WebEngine 6.11 (see services/task_network.py).
FRONTEND_ACTION = """
window.__steedDevtoolsAction = null;
(async () => {
  const UI = await import(new URL('./ui/legacy/legacy.js', location.href).href);
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      %s
      window.__steedDevtoolsAction = true;
      return;
    } catch (_) { await new Promise(resolve => setTimeout(resolve, 100)); }
  }
  window.__steedDevtoolsAction = false;
})();
"""
SHOW_CONSOLE = "UI.InspectorView.InspectorView.instance().showPanel('console');"
PICK_ELEMENT = (
    "UI.InspectorView.InspectorView.instance().showPanel('elements');"
    "UI.ActionRegistry.ActionRegistry.instance()"
    ".getAction('elements.toggle-element-search').execute();"
)


class DevToolsUnavailable(RuntimeError):
    pass


class DevToolsHost(QObject):
    """One frontend per inspected tab; the window shows the current tab's."""

    def __init__(self, window, content: QSplitter):
        super().__init__(window)
        self.window = window
        self.content = content
        self.stack = QStackedWidget()
        self.stack.setMinimumSize(240, 160)
        self.stack.hide()
        self.content.addWidget(self.stack)
        self.content.setCollapsible(1, False)
        self.views = {}
        self.profile = None
        self.separate = None
        side = window.config.get("devtools_side", DEFAULT_SIDE)
        self.side = side if side in SIDES else DEFAULT_SIDE
        self._apply_side()

    # ---------- Public actions ----------

    def is_open(self, tab) -> bool:
        return tab in self.views

    def toggle(self, tab) -> None:
        if tab in self.views:
            self.close(tab)
        else:
            self.open(tab)

    def open(self, tab, action=None) -> None:
        """Show DevTools for a tab, optionally running a frontend action."""
        view = self.views.get(tab)
        if view is None:
            page = tab.page()
            if webengine.devtools_of(page) is not None:
                # An agent task or private helper already owns this page's
                # DevTools connection; taking it would break their protection.
                raise DevToolsUnavailable(
                    "Developer Tools are unavailable while an agent controls this tab."
                )
            view = webengine.View()
            frontend = webengine.Page(self._profile(), view)
            view.setPage(frontend)
            frontend.windowCloseRequested.connect(lambda: self.close(tab))
            webengine.attach_devtools(page, frontend)
            self.views[tab] = view
            self.stack.addWidget(view)
        self.show_for(tab)
        if action is not None:
            self._run(view, action)

    def console(self, tab) -> None:
        self.open(tab, SHOW_CONSOLE)

    def pick_element(self, tab) -> None:
        self.open(tab, PICK_ELEMENT)

    def inspect_element(self, tab) -> None:
        """Open on the element under the last page context menu."""
        self.open(tab)
        webengine.trigger(tab.page(), "inspect_element")

    def close(self, tab) -> None:
        view = self.views.pop(tab, None)
        if view is None:
            return
        if not sip.isdeleted(tab):
            webengine.attach_devtools(tab.page(), None)
        self.stack.removeWidget(view)
        # Destroy the frontend page before its profile, on the Qt thread.
        sip.delete(view)
        self.show_for(self._current())

    def show_for(self, tab) -> None:
        view = self.views.get(tab)
        target = self.separate if self.side == "window" else self.stack
        if view is None:
            self.stack.hide()
            if self.separate is not None:
                self.separate.hide()
            return
        self.stack.setCurrentWidget(view)
        self.stack.show()
        if target is self.separate:
            self.separate.show()
            self.separate.raise_()
        elif self.content.sizes()[-1] == 0:
            self._balance()

    def set_side(self, side) -> None:
        if side not in SIDES or side == self.side:
            return
        self.side = side
        self.window.config.set("devtools_side", side)
        self._apply_side()
        self.show_for(self._current())

    def dispose(self) -> None:
        for tab in list(self.views):
            self.close(tab)
        if self.separate is not None:
            self.separate.close()
            sip.delete(self.separate)
            self.separate = None
        if self.profile is not None:
            sip.delete(self.profile)
            self.profile = None

    # ---------- Internals ----------

    def _current(self):
        window = self.window
        if window.tabs and 0 <= window.current_tab_index < len(window.tabs):
            return window.tabs[window.current_tab_index]
        return None

    def _profile(self):
        # The frontend needs no website storage; keep its own off-the-record
        # profile so DevTools state never mixes with a browsing profile.
        if self.profile is None:
            self.profile = webengine.private_profile(self)
        return self.profile

    def _apply_side(self):
        if self.side == "window":
            if self.separate is None:
                self.separate = QWidget(None, Qt.WindowType.Window)
                self.separate.setWindowTitle("Developer Tools")
                self.separate.resize(900, 640)
                QVBoxLayout(self.separate).setContentsMargins(0, 0, 0, 0)
            self.separate.layout().addWidget(self.stack)
            return
        if self.content.indexOf(self.stack) < 0:
            self.content.addWidget(self.stack)
        if self.separate is not None:
            self.separate.hide()
        self.content.setOrientation(
            Qt.Orientation.Horizontal
            if self.side == "right"
            else Qt.Orientation.Vertical
        )
        self._balance()

    def _balance(self):
        total = sum(self.content.sizes()) or 1000
        share = 0.42 if self.side == "right" else 0.38
        self.content.setSizes([int(total * (1 - share)), int(total * share)])

    def _run(self, view, script):
        source = FRONTEND_ACTION % script

        page = view.page()

        def run(ok=True):
            if ok and not sip.isdeleted(view):
                webengine.run_in(page, source)

        if page.url().isEmpty() or page.isLoading():
            # Run once when the frontend first loads, not on later reloads.
            def loaded(ok):
                page.loadFinished.disconnect(loaded)
                run(ok)

            page.loadFinished.connect(loaded)
        else:
            QTimer.singleShot(0, run)
