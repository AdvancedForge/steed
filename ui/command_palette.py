"""Quick palette: run any menu command or jump to any open tab by name."""

from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from ui.html_dialog import place_popup
from ui.html_surface import HtmlSurface

MAX_ITEMS = 400


def menu_commands(menus):
    """(path, action) for every enabled, named action, following submenus."""
    found, seen = [], set()

    def walk(menu, path):
        if sip.isdeleted(menu) or id(menu) in seen:
            return
        seen.add(id(menu))
        for action in menu.actions():
            if action.isSeparator() or not action.isVisible():
                continue
            text = action.text().replace("&", "").strip()
            if not text:
                continue
            if action.menu() is not None:
                walk(action.menu(), path + [text])
            elif action.isEnabled():
                found.append((" › ".join(path), text, action))

    for name, menu in menus.items():
        walk(menu, [menu.title().replace("&", "") or name.title()])
    return found


class CommandPalette(QDialog):
    """One reusable popup per window; filtering happens in the page."""

    def __init__(self, window):
        super().__init__(
            window, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.browser = window
        self.context = token_hex(16)
        self.items = {}
        self.mode = ""
        self.surface = HtmlSurface("palette", self, label="command palette")
        self.surface.command.connect(self.command)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.surface)

    def present(self, mode=""):
        """Show commands and tabs; mode "@" starts on tabs, ">" on commands."""
        self.mode = mode
        self.context = token_hex(16)
        self.items = {}
        rows = []
        window = self.browser
        for owner in [window, *window._same_profile_windows()]:
            for tab in owner.tabs:
                if getattr(tab, "agent_panel", False):
                    continue
                token = token_hex(8)
                self.items[token] = ("tab", owner, tab)
                rows.append(
                    {
                        "id": token,
                        "kind": "tab",
                        "title": tab.title() or owner._tab_url(tab),
                        "detail": owner._tab_url(tab)
                        + (" · other window" if owner is not window else ""),
                        "current": owner is window
                        and tab is window.tabs[window.current_tab_index],
                    }
                )
        for path, text, action in menu_commands(window._layout_menus):
            token = token_hex(8)
            self.items[token] = ("command", None, action)
            rows.append(
                {
                    "id": token,
                    "kind": "command",
                    "title": text,
                    "detail": path,
                    "shortcut": action.shortcut().toString(),
                }
            )
        rows = rows[:MAX_ITEMS]
        width = min(640, max(420, window.width() - 120))
        anchor = window.mapToGlobal(
            QPoint((window.width() - width) // 2, max(40, window.height() // 9))
        )
        place_popup(self, window, anchor, (width, 440))
        self.surface.publish(
            {
                "context": self.context,
                "rows": rows,
                "mode": mode,
                "dark": window.profile_manager.registry.page_style.dark(
                    window.profile_manager.current_profile_name
                ),
            }
        )
        self.show()
        self.raise_()
        self.activateWindow()
        if self.surface.ready:
            self.surface.view.setFocus()
            # A page still loading focuses its field when it first renders.
            self.surface.page.runJavaScript(
                "window.steedPalette && steedPalette.focus()"
            )

    def command(self, command):
        if command.get("context") != self.context:
            return
        if command.get("action") == "close":
            self.hide()
            return
        if command.get("action") != "choose":
            return
        item = self.items.get(command.get("id"))
        if item is None:
            return
        kind, owner, target = item
        self.hide()
        if kind == "tab":
            if (
                sip.isdeleted(owner)
                or sip.isdeleted(target)
                or target not in owner.tabs
            ):
                return
            owner._switch_tab(owner.tabs.index(target))
            owner.raise_()
            owner.activateWindow()
            target.setFocus()
        elif not sip.isdeleted(target) and target.isEnabled():
            target.trigger()

    def dispose(self):
        self.surface.dispose()
