"""Quick palette: run a menu command, jump to a tab, or reopen history/bookmarks.

Prefixes narrow the search: > commands, @ tabs, # history, * bookmarks and
saved tab groups.
"""

from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ui.html_dialog import place_popup
from ui.html_surface import HtmlSurface

MAX_ITEMS = 400
# History and bookmarks are searched too, so their rows are allowed beyond that.
MAX_PLACES = 1200
# A hidden palette keeps its renderer this long for a quick reopen, then frees it.
RELEASE_MS = 60000


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

    released = pyqtSignal()
    native = False

    def __init__(self, window):
        super().__init__(
            window, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.browser = window
        self.context = token_hex(16)
        self.items = {}
        self.mode = ""
        self.idle = QTimer(self)
        self.idle.setSingleShot(True)
        self.idle.setInterval(RELEASE_MS)
        self.idle.timeout.connect(self.released.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        self._build(layout)

    def _build(self, layout):
        self.surface = HtmlSurface("palette", self, label="command palette")
        self.surface.command.connect(self.command)
        layout.addWidget(self.surface)

    def showEvent(self, event):
        self.idle.stop()
        super().showEvent(event)

    # Set by the window from the memory mode; negative keeps the palette loaded.
    keep_ms = RELEASE_MS

    def hideEvent(self, event):
        if self.keep_ms >= 0:
            self.idle.start(self.keep_ms)
        super().hideEvent(event)

    def _rows(self):
        """Fresh tokens for every open tab and enabled menu command."""
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
        open_urls = {row["detail"].split(" · ")[0] for row in rows}
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
        return rows[:MAX_ITEMS] + self._places(open_urls)

    def _places(self, open_urls):
        """Bookmarks, then history, newest first, for pages not already open."""
        window = self.browser
        profile = window.profile_manager.current_profile_name
        places, seen = [], set(open_urls)
        # Recently closed tabs, newest first: reopening keeps their place.
        for closed in window.closed_tabs.entries(profile):
            token = token_hex(8)
            self.items[token] = ("closed", None, closed.state.tab_id)
            places.append(
                {
                    "id": token,
                    "kind": "closed",
                    "title": closed.state.title or closed.state.url,
                    "detail": closed.state.url,
                }
            )
        bookmarks = []
        if profile != "search":
            try:
                state = window.profile_manager.registry.start_pages.state(profile)
                bookmarks = state["bookmarks"]
            except (KeyError, ValueError, OSError):
                bookmarks = []
        history = window.history.entries() if window.history is not None else []
        manager = window.tab_manager
        for group in manager.saved_groups() if manager is not None else []:
            token = token_hex(8)
            self.items[token] = ("group", None, group["group_id"])
            count = len(group["tabs"])
            places.append(
                {
                    "id": token,
                    "kind": "group",
                    "title": group["name"],
                    "detail": f"Saved group · {count} tab{'s' * (count != 1)}",
                }
            )
        for kind, entries in (
            ("bookmark", ((b["url"], b["title"]) for b in bookmarks)),
            ("history", ((e.url, e.title) for e in history)),
        ):
            for url, title in entries:
                if url in seen or len(places) >= MAX_PLACES:
                    continue
                seen.add(url)
                token = token_hex(8)
                self.items[token] = (kind, None, url)
                places.append(
                    {"id": token, "kind": kind, "title": title or url, "detail": url}
                )
        return places

    def _place(self):
        window = self.browser
        width = min(640, max(420, window.width() - 120))
        anchor = window.mapToGlobal(
            QPoint((window.width() - width) // 2, max(40, window.height() // 9))
        )
        place_popup(self, window, anchor, (width, 440))

    def present(self, mode=""):
        """Show commands and tabs; mode "@" starts on tabs, ">" on commands."""
        self.mode = mode
        window = self.browser
        rows = self._rows()
        self._place()
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
        if kind == "group":
            if not self.browser._closing:
                self.browser.open_saved_group(target)
            return
        if kind in ("history", "bookmark"):
            if not self.browser._closing:
                self.browser._new_tab(url=target)
            return
        if kind == "closed":
            if not self.browser._closing:
                self.browser._restore_closed_tab(target)
            return
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
        self.idle.stop()
        if self.surface is not None:
            self.surface.dispose()


class NativeCommandPalette(CommandPalette):
    """The same commands and tabs in a plain Qt list, with no renderer."""

    native = True

    def _build(self, layout):
        self.surface = None
        self.query = QLineEdit(self)
        self.query.setPlaceholderText(
            "Search tabs, history and commands  (> commands, @ tabs, # history, * bookmarks)"
        )
        self.query.setAccessibleName("Search tabs and commands")
        self.query.textChanged.connect(self._filter)
        self.query.returnPressed.connect(self._choose_current)
        self.results = QListWidget(self)
        self.results.setAccessibleName("Matching tabs and commands")
        self.results.itemActivated.connect(self._choose)
        self.query.installEventFilter(self)
        layout.addWidget(self.query)
        layout.addWidget(self.results)

    def present(self, mode=""):
        self.mode = mode
        self.rows = self._rows()
        self._place()
        self.query.setText(mode)
        self._filter()
        self.show()
        self.raise_()
        self.activateWindow()
        self.query.setFocus()

    def eventFilter(self, source, event):
        from PyQt6.QtCore import QEvent

        if source is self.query and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.results.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                if 0 <= row < self.results.count():
                    self.results.setCurrentRow(row)
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(source, event)

    def _filter(self):
        text = self.query.text()
        kind = None
        prefixes = {">": "command", "@": "tab", "#": "history", "*": "bookmark"}
        if text[:1] in prefixes:
            kind, text = prefixes[text[:1]], text[1:]
        words = text.casefold().split()
        phrase = " ".join(words)

        def rank(row):
            # Exact titles first, then titles starting with the query, then the rest.
            title = row["title"].casefold()
            return (title != phrase, not title.startswith(phrase), phrase not in title)

        self.results.clear()
        rows = [
            row
            for row in self.rows
            if (
                not kind
                or row["kind"] == kind
                or (kind == "bookmark" and row["kind"] == "group")
                or (kind == "tab" and row["kind"] == "closed")
            )
            # With nothing typed, show tabs rather than every page ever visited.
            and (kind or words or row["kind"] == "tab")
            and all(
                word in (row["title"] + " " + row["detail"]).casefold()
                for word in words
            )
        ]
        for row in sorted(rows, key=rank) if phrase else rows:
            label = row["title"]
            if row.get("shortcut"):
                label += f"    {row['shortcut']}"
            badge = {
                "tab": "Tab · ",
                "history": "History · ",
                "bookmark": "Bookmark · ",
                "group": "Group · ",
                "closed": "Closed tab · ",
            }
            item = QListWidgetItem(badge.get(row["kind"], "") + label)
            item.setToolTip(row["detail"])
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _choose_current(self):
        item = self.results.currentItem()
        if item is not None:
            self._choose(item)

    def _choose(self, item):
        self.command(
            {
                "context": self.context,
                "action": "choose",
                "id": item.data(Qt.ItemDataRole.UserRole),
            }
        )
