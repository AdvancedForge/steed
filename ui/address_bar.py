"""Address bar widget."""

from PyQt6.QtCore import QRect, QSize, Qt, QUrl, pyqtSignal
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
BUTTON_ICONS = {
    "back": "back",
    "forward": "forward",
    "reload": "reload",
    "home": "home",
    "passwords": "key",
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
        icon("history", colors["muted"], 16).paint(
            painter,
            QRect(option.rect.left() + 10, option.rect.top() + line - 1, 16, 16),
        )
        title = index.data(TITLE_ROLE) or index.data(Qt.ItemDataRole.UserRole) or ""
        url = index.data(Qt.ItemDataRole.UserRole) or ""
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
        self.textEdited.connect(lambda _: setattr(self, "committed", False))

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
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setMaxVisibleItems(10)
        self.address_input.setCompleter(self.completer)
        self.completer.activated[str].connect(self._on_completion)
        self.completer.popup().setItemDelegate(SuggestionDelegate(self))
        self.site_action = self.address_input.addAction(
            icon("search", "#5d6c83", 16), QLineEdit.ActionPosition.LeadingPosition
        )
        self.site_action.setToolTip("Search or enter an address")
        self.site_kind = "search"
        self.passwords_btn = QPushButton()
        self.passwords_btn.setAccessibleName("Passwords")
        self.passwords_btn.setToolTip("KeePassXC passwords (Ctrl+Shift+L to fill)")
        self.passwords_btn.clicked.connect(self.passwords_requested.emit)
        self.zoom_btn = QPushButton("100%")
        self.zoom_btn.setObjectName("TextButton")
        self.zoom_btn.setToolTip("Reset page zoom (Ctrl+0)")
        self.zoom_btn.clicked.connect(self.reset_zoom_requested.emit)

        # Stable identifiers let a saved layout reorder or hide individual
        # buttons without depending on widget names.
        self.buttons = {
            "back": self.back_btn,
            "forward": self.forward_btn,
            "reload": self.reload_btn,
            "home": self.home_btn,
            "zoom": self.zoom_btn,
            "passwords": self.passwords_btn,
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
            button.setVisible(name not in self.hidden_toolbar)
        for name in self.toolbar_order:
            if name == "address":
                self.address_input.show()
                layout.addWidget(self.address_input, 1)
            elif name not in self.hidden_toolbar:
                layout.addWidget(self.buttons[name])

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

    def _on_completion(self, text: str) -> None:
        self.address_input.committed = True
        self.url_changed.emit(text)

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
            "lock": "Connection is encrypted (HTTPS)",
            "unlocked": "Connection is not encrypted",
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
            if name in BUTTON_ICONS:
                button.setIcon(icon(BUTTON_ICONS[name], colors["ink"]))
                button.setIconSize(QSize(18, 18))
        self._update_site_icon()

    def set_menu_badge(self, text: str) -> None:
        """Show a short label beside the menu icon (for example YOLO mode)."""
        self.menu_btn.setText(text)
        self.menu_btn.setObjectName("TextButton" if text else "")
        self.menu_btn.style().unpolish(self.menu_btn)
        self.menu_btn.style().polish(self.menu_btn)

    def set_history(self, entries: list[HistoryEntry]) -> None:
        self.completer.popup().hide()
        self.history_model.clear()
        for entry in entries:
            item = QStandardItem(f"{entry.title} — {entry.url}")
            item.setData(entry.url, Qt.ItemDataRole.UserRole)
            item.setData(entry.title, TITLE_ROLE)
            self.history_model.appendRow(item)

    def focus_address(self) -> None:
        self.address_input.setFocus()
        self.address_input.selectAll()

    def set_back_enabled(self, enabled: bool) -> None:
        """Enable/disable back button."""
        self.back_btn.setEnabled(enabled)

    def set_forward_enabled(self, enabled: bool) -> None:
        """Enable/disable forward button."""
        self.forward_btn.setEnabled(enabled)
