"""Address bar widget."""

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from browsing_history import HistoryEntry
from layout_preferences import TOOLBAR_ORDER, clean_toolbar_order
from ui.chrome_style import (
    chrome_colors,
    icon,
    popup_stylesheet,
    toolbar_stylesheet,
)

TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
KIND_ROLE = Qt.ItemDataRole.UserRole + 2
KEY_ROLE = Qt.ItemDataRole.UserRole + 3
KIND_ICONS = {
    "search": "search",
    "suggest": "search",
    "go": "go",
    "tab": "tab",
    "bookmark": "star",
    "history": "history",
}
LIMITS = {"suggest": 4, "tab": 3, "bookmark": 4, "history": 8}
MAX_SUGGESTIONS = 10


def looks_like_address(text):
    text = text.strip()
    if not text or " " in text:
        return False
    if "://" in text or text.startswith(("steed:", "about:", "localhost")):
        return True
    host = text.split("/", 1)[0]
    return "." in host and not host.startswith(".") and not host.endswith(".")


def matches(words, *fields):
    haystack = " ".join(fields).lower()
    return all(word in haystack for word in words)


BUTTON_ICONS = {
    "back": "back",
    "forward": "forward",
    "reload": "reload",
    "home": "home",
    "passwords": "key",
    "extensions": "puzzle",
    "menu": "menu",
}

# Spacing per density; the address input always keeps its width.
DENSITY_METRICS = {
    "compact": {"margin": 3, "spacing": 2},
    "comfortable": {"margin": 6, "spacing": 4},
}


class HistoryCompleter(QCompleter):
    """Display titles and URLs, but always complete to a URL."""

    def pathFromIndex(self, index):
        return index.data(Qt.ItemDataRole.UserRole) or ""


class SuggestionDelegate(QStyledItemDelegate):
    """Two-line history suggestions: page title above its address."""

    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar

    def sizeHint(self, option, index):
        height = option.fontMetrics.height()
        return QSize(option.rect.width(), height * 2 + 14)

    def paint(self, painter, option, index):
        colors = self.bar.colors
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["hover"]))
            painter.drawRoundedRect(option.rect.adjusted(2, 1, -2, -1), 8, 8)
        metrics = option.fontMetrics
        line = metrics.height()
        left = option.rect.left() + 34
        width = option.rect.width() - 44
        kind = index.data(KIND_ROLE) or "history"
        icon(KIND_ICONS.get(kind, "history"), colors["muted"], 16).paint(
            painter,
            QRect(option.rect.left() + 10, option.rect.top() + line - 1, 16, 16),
        )
        title = index.data(TITLE_ROLE) or index.data(Qt.ItemDataRole.UserRole) or ""
        url = index.data(Qt.ItemDataRole.UserRole) or ""
        if kind == "tab":
            url = "Switch to tab · " + url
        elif kind in ("search", "go", "suggest"):
            url = index.data(Qt.ItemDataRole.ToolTipRole) or ""
        painter.setPen(QColor(colors["ink"]))
        painter.drawText(
            QRect(left, option.rect.top() + 6, width, line),
            Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(title, Qt.TextElideMode.ElideRight, width),
        )
        painter.setPen(QColor(colors["accent"]))
        painter.drawText(
            QRect(left, option.rect.top() + 6 + line, width, line),
            Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(url, Qt.TextElideMode.ElideMiddle, width),
        )
        painter.restore()


class AddressInput(QLineEdit):
    """Keep the user's typing, and its undo/redo history, while pages update."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page_url = ""
        self.committed = False
        # Sites you visit, most often first, for completing what you type.
        self.hosts: list[str] = []
        self.autocomplete = True
        self._typed = ""
        self.textEdited.connect(lambda _: setattr(self, "committed", False))
        self.textEdited.connect(self._complete_inline)

    def _complete_inline(self, text: str) -> None:
        """Typing the start of a site you visit fills in the rest, selected."""
        previous, self._typed = self._typed, text
        if (
            not self.autocomplete
            or len(text) < 2
            or " " in text
            or "/" in text
            or not text.startswith(previous)
            or len(text) <= len(previous)
            or self.cursorPosition() != len(text)
        ):
            return
        typed = text.lower()
        bare = typed.removeprefix("www.")
        for host in self.hosts:
            if host.startswith(bare) and host != bare:
                completed = text + host[len(bare) :]
                self.setText(completed)
                self.setSelection(len(text), len(completed) - len(text))
                return

    def show_page_url(self, url: str) -> None:
        self.page_url = url
        # setText clears undo/redo; a page changing its URL (redirects, SPAs)
        # must not replace what the user is editing.
        if self.hasFocus() and self.editing():
            return
        if self.text() != url:
            self.setText(url)

    def editing(self) -> bool:
        # Undoing back to the original text clears isModified() but keeps redo.
        return not self.committed and (
            self.isModified() or self.isUndoAvailable() or self.isRedoAvailable()
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.editing():
            self.setText(self.page_url)
            self.selectAll()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if not self.editing() and self.text() != self.page_url:
            self.setText(self.page_url)


class AddressBar(QWidget):
    """Address bar with navigation buttons."""

    url_changed = pyqtSignal(str)  # Emitted when URL is entered
    back_requested = pyqtSignal()
    forward_requested = pyqtSignal()
    reload_requested = pyqtSignal()
    home_requested = pyqtSignal()
    passwords_requested = pyqtSignal()
    reset_zoom_requested = pyqtSignal()
    site_menu_requested = pyqtSignal()
    downloads_requested = pyqtSignal()
    media_requested = pyqtSignal()
    extensions_requested = pyqtSignal()
    tab_switch_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SteedToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.colors = chrome_colors("#eef1f6", False)
        self.setup_ui()
        self.apply_style(self.colors)

    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Navigation buttons
        self.back_btn = QPushButton()
        self.back_btn.setAccessibleName("Back")
        self.back_btn.setToolTip("Back")
        self.back_btn.clicked.connect(self.back_requested.emit)

        self.forward_btn = QPushButton()
        self.forward_btn.setAccessibleName("Forward")
        self.forward_btn.setToolTip("Forward")
        self.forward_btn.clicked.connect(self.forward_requested.emit)

        self.reload_btn = QPushButton()
        self.reload_btn.setAccessibleName("Reload")
        self.reload_btn.setToolTip("Reload (Ctrl+R / F5)")
        self.reload_btn.clicked.connect(self.reload_requested.emit)

        self.home_btn = QPushButton()
        self.home_btn.setAccessibleName("Home")
        self.home_btn.setToolTip("Home")
        self.home_btn.clicked.connect(self.home_requested.emit)

        # Address bar
        self.address_input = AddressInput()
        self.address_input.setPlaceholderText("Enter URL or search...")
        self.address_input.returnPressed.connect(self._on_return_pressed)
        self.history_model = QStandardItemModel(self)
        self.completer = HistoryCompleter(self.history_model, self)
        # Suggestions are ranked here, so the completer shows them unfiltered.
        self.completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion
        )
        self.completer.setMaxVisibleItems(MAX_SUGGESTIONS)
        self.address_input.setCompleter(self.completer)
        self.completer.activated[QModelIndex].connect(self._on_suggestion)
        self.address_input.textEdited.connect(self._suggest)
        self.history_entries = []
        self.bookmark_entries = []
        self.tab_source = lambda: []
        self.search_name = "the web"
        self.completer.popup().setItemDelegate(SuggestionDelegate(self))
        self.site_action = self.address_input.addAction(
            icon("search", "#5d6c83", 16), QLineEdit.ActionPosition.LeadingPosition
        )
        self.site_action.setToolTip("Search or enter an address")
        self.site_action.triggered.connect(self.site_menu_requested.emit)
        self.site_kind = "search"
        self.passwords_btn = QPushButton()
        self.passwords_btn.setAccessibleName("Passwords")
        self.passwords_btn.setToolTip("Passwords for this site (Ctrl+Shift+L to fill)")
        self.passwords_btn.clicked.connect(self.passwords_requested.emit)
        self.zoom_btn = QPushButton("100%")
        self.zoom_btn.setObjectName("TextButton")
        self.zoom_btn.setToolTip("Reset page zoom (Ctrl+0)")
        self.zoom_btn.clicked.connect(self.reset_zoom_requested.emit)
        # Appears only while something downloads, and briefly afterwards.
        self.downloads_btn = QPushButton()
        self.downloads_btn.setObjectName("TextButton")
        self.downloads_btn.setAccessibleName("Downloads")
        self.downloads_btn.clicked.connect(self.downloads_requested.emit)
        self.downloads_shown = False
        # Appears while a tab plays media.
        self.media_btn = QPushButton()
        self.media_btn.setObjectName("TextButton")
        self.media_btn.setAccessibleName("Media controls")
        self.media_btn.clicked.connect(self.media_requested.emit)
        self.media_shown = False
        # Hidden in incognito windows, which have no extensions.
        # Pinned extensions' buttons, then the puzzle button listing them all.
        self.extensions_box = QWidget(self)
        self.extensions_box.setObjectName("ToolbarGroup")
        pins = QHBoxLayout(self.extensions_box)
        pins.setContentsMargins(0, 0, 0, 0)
        pins.setSpacing(2)
        self.extension_pins = []
        self.extensions_btn = QPushButton(self.extensions_box)
        self.extensions_btn.setAccessibleName("Extensions")
        self.extensions_btn.setToolTip("Extensions")
        self.extensions_btn.clicked.connect(self.extensions_requested.emit)
        pins.addWidget(self.extensions_btn)
        self.extensions_shown = True

        # Stable identifiers let a saved layout reorder or hide individual
        # buttons without depending on widget names.
        self.buttons = {
            "back": self.back_btn,
            "forward": self.forward_btn,
            "reload": self.reload_btn,
            "home": self.home_btn,
            "zoom": self.zoom_btn,
            "passwords": self.passwords_btn,
            "downloads": self.downloads_btn,
            "media": self.media_btn,
            "extensions": self.extensions_box,
        }
        self.menu_btn = QPushButton()
        self.menu_btn.setToolTip("Browser menu")
        self.menu_btn.setAccessibleName("Browser menu")
        self.buttons["menu"] = self.menu_btn
        self.toolbar_order = list(TOOLBAR_ORDER)
        self.hidden_toolbar: list[str] = []
        self.density = "comfortable"
        self._apply_toolbar()

    def _apply_toolbar(self) -> None:
        """Place controls around the mandatory address field and hide omissions."""
        layout = self.layout()
        for widget in list(self.buttons.values()) + [self.address_input]:
            layout.removeWidget(widget)
        for name, button in self.buttons.items():
            if button.parent() is not self:
                button.setParent(self)
            button.setVisible(
                name not in self.hidden_toolbar
                and (name != "downloads" or self.downloads_shown)
                and (name != "media" or self.media_shown)
                and (name != "extensions" or self.extensions_shown)
            )
        for name in self.toolbar_order:
            if name == "address":
                self.address_input.show()
                layout.addWidget(self.address_input, 1)
            elif name not in self.hidden_toolbar:
                layout.addWidget(self.buttons[name])

    def show_downloads(self, text: str, tooltip: str) -> None:
        self.downloads_btn.setText(text)
        self.downloads_btn.setToolTip(tooltip)
        self.downloads_shown = True
        self.downloads_btn.setVisible("downloads" not in self.hidden_toolbar)

    def hide_downloads(self) -> None:
        self.downloads_shown = False
        self.downloads_btn.hide()

    def show_media(self, text: str, tooltip: str) -> None:
        self.media_btn.setText(text)
        self.media_btn.setToolTip(tooltip)
        self.media_shown = True
        self.media_btn.setVisible("media" not in self.hidden_toolbar)

    def hide_media(self) -> None:
        self.media_shown = False
        self.media_btn.hide()

    def set_extension_pins(self, pins) -> None:
        """Pinned extensions: [(icon, name, clicked)], left of the puzzle."""
        layout = self.extensions_box.layout()
        for button in self.extension_pins:
            layout.removeWidget(button)
            button.deleteLater()
        self.extension_pins = []
        for index, (button_icon, name, clicked) in enumerate(pins):
            button = QPushButton(self.extensions_box)
            button.setIcon(button_icon)
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.clicked.connect(clicked)
            layout.insertWidget(index, button)
            self.extension_pins.append(button)

    def show_extensions(self, shown: bool) -> None:
        self.extensions_shown = shown
        self.extensions_box.setVisible(
            shown and "extensions" not in self.hidden_toolbar
        )

    def apply_toolbar(self, order, hidden) -> None:
        """Show and hide address bar buttons according to a layout.

        An order that omits a button still shows it, in today's default
        position, so a layout written against an older build cannot make a
        control unreachable.
        """
        self.toolbar_order = clean_toolbar_order(order)
        self.hidden_toolbar = [
            name for name in hidden if name in self.buttons and name != "menu"
        ]
        self._apply_toolbar()

    def apply_density(self, density: str) -> None:
        """Use compact or comfortable spacing for the address bar."""
        metrics = DENSITY_METRICS.get(density, DENSITY_METRICS["comfortable"])
        self.density = density if density in DENSITY_METRICS else "comfortable"
        layout = self.layout()
        margin = metrics["margin"]
        layout.setContentsMargins(margin * 2, margin, margin * 2, margin)
        layout.setSpacing(metrics["spacing"])
        self.setStyleSheet(toolbar_stylesheet(self.colors, self.density))

    def _on_return_pressed(self) -> None:
        """Handle return key press."""
        text = self.address_input.text().strip()
        if text:
            # The edit is committed; later page URLs may replace it again.
            self.address_input.committed = True
            self.url_changed.emit(text)

    def _on_suggestion(self, index) -> None:
        self.address_input.committed = True
        kind = index.data(KIND_ROLE)
        if kind == "tab":
            self.address_input.setText(self.address_input.page_url)
            self.tab_switch_requested.emit(index.data(KEY_ROLE))
            return
        self.url_changed.emit(index.data(Qt.ItemDataRole.UserRole) or "")

    def set_bookmarks(self, entries) -> None:
        """(title, url) pairs from the current profile's bookmarks."""
        self.bookmark_entries = list(entries)

    def set_search_name(self, name: str) -> None:
        self.search_name = name or "the web"

    def set_search_suggestions(self, search_url: str, enabled: bool) -> None:
        """Ask the search engine for suggestions while typing, when enabled."""
        self.search_url = search_url
        self.remote_enabled = bool(enabled)
        if not enabled and hasattr(self, "remote"):
            self.remote.cancel()

    def _remote_ready(self, query, found):
        if (
            self.remote_enabled
            and self.address_input.hasFocus()
            and self.address_input.text().strip() == query
        ):
            self._suggest(query)

    def _suggest(self, text: str) -> None:
        """Rank the typed action, open tabs, bookmarks and history."""
        text = text.strip()
        self.history_model.clear()
        if not text:
            self.completer.popup().hide()
            return
        words = text.lower().split()
        rows, seen = [], set()

        def add(kind, title, url, key="", hint=""):
            if kind in LIMITS:
                if url in seen or sum(r[0] == kind for r in rows) >= LIMITS[kind]:
                    return
                seen.add(url)
            rows.append((kind, title, url, key, hint))

        keyword = getattr(self, "keyword_source", lambda _: None)(text)
        if keyword is not None:
            _, name, rest = keyword
            add("search", f"Search {name} for “{rest}”", text, hint="Search keyword")
        elif looks_like_address(text):
            add("go", text, text, hint="Go to this address")
        else:
            add("search", f"Search {self.search_name} for “{text}”", text)
            if getattr(self, "remote_enabled", False):
                if not hasattr(self, "remote"):
                    from search_suggestions import SearchSuggestions

                    self.remote = SearchSuggestions(self)
                    self.remote.ready.connect(self._remote_ready)
                for suggestion in self.remote.request(text, self.search_url):
                    add(
                        "suggest",
                        suggestion,
                        suggestion,
                        hint=f"{self.search_name} suggestion",
                    )
        for title, url, key in self.tab_source():
            if url != self.address_input.page_url and matches(words, title, url):
                add("tab", title or url, url, key)
        for title, url in self.bookmark_entries:
            if matches(words, title, url):
                add("bookmark", title or url, url)
        for entry in self.history_entries:
            if matches(words, entry.title, entry.url):
                add("history", entry.title or entry.url, entry.url)
        for kind, title, url, key, hint in rows[:MAX_SUGGESTIONS]:
            item = QStandardItem(title)
            item.setData(url, Qt.ItemDataRole.UserRole)
            item.setData(title, TITLE_ROLE)
            item.setData(kind, KIND_ROLE)
            item.setData(key, KEY_ROLE)
            item.setData(hint, Qt.ItemDataRole.ToolTipRole)
            self.history_model.appendRow(item)
        self.completer.setCompletionPrefix("")
        self.completer.complete()

    def set_url(self, url: str) -> None:
        """Show the current page URL unless the user is editing the field."""
        self.address_input.show_page_url(url)
        scheme = QUrl(url).scheme()
        kind = {"https": "lock", "http": "unlocked", "steed": "internal"}.get(
            scheme, "search"
        )
        if kind != self.site_kind:
            self.site_kind = kind
            self._update_site_icon()

    def _update_site_icon(self):
        tips = {
            "lock": "Connection is encrypted (HTTPS). Click for this site's settings",
            "unlocked": "Connection is not encrypted. Click for this site's settings",
            "internal": "Steed page",
            "search": "Search or enter an address",
        }
        color = "#c4683c" if self.site_kind == "unlocked" else self.colors["muted"]
        self.site_action.setIcon(icon(self.site_kind, color, 16))
        self.site_action.setToolTip(tips[self.site_kind])

    def apply_style(self, colors) -> None:
        """Repaint the toolbar to match the tab strip's colours."""
        self.colors = colors
        self.setStyleSheet(toolbar_stylesheet(colors, self.density))
        self.completer.popup().setStyleSheet(popup_stylesheet(colors))
        for name, button in self.buttons.items():
            if name == "extensions":
                button = self.extensions_btn
            if name in BUTTON_ICONS:
                button.setIcon(icon(BUTTON_ICONS[name], colors["ink"]))
                button.setIconSize(QSize(18, 18))
        self._update_site_icon()

    def set_menu_badge(self, text: str) -> None:
        """Show a short label beside the menu icon (for example YOLO mode)."""
        self.menu_btn.setText(text)
        # A warning pill (styled as WarningBadge in chrome_style), not plain text.
        self.menu_btn.setObjectName("WarningBadge" if text else "")
        self.menu_btn.style().unpolish(self.menu_btn)
        self.menu_btn.style().polish(self.menu_btn)

    def set_history(self, entries: list[HistoryEntry]) -> None:
        from collections import Counter
        from urllib.parse import urlsplit

        # Each visited page counts once for its site; ties keep recency order.
        counts, order = Counter(), {}
        for index, entry in enumerate(entries):
            parts = urlsplit(entry.url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                continue
            host = parts.hostname.lower().removeprefix("www.")
            counts[host] += 1
            order.setdefault(host, index)
        self.address_input.hosts = sorted(counts, key=lambda h: (-counts[h], order[h]))
        self.history_entries = list(entries)

    def focus_address(self) -> None:
        self.address_input.setFocus()
        self.address_input.selectAll()

    def set_back_enabled(self, enabled: bool) -> None:
        """Enable/disable back button."""
        self.back_btn.setEnabled(enabled)

    def set_forward_enabled(self, enabled: bool) -> None:
        """Enable/disable forward button."""
        self.forward_btn.setEnabled(enabled)
