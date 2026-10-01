"""Address bar widget."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QCompleter, QHBoxLayout, QLineEdit, QPushButton, QWidget

from browsing_history import HistoryEntry


class HistoryCompleter(QCompleter):
    """Display titles and URLs, but always complete to a URL."""

    def pathFromIndex(self, index):
        return index.data(Qt.ItemDataRole.UserRole) or ""


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
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Navigation buttons
        self.back_btn = QPushButton("←")
        self.back_btn.setToolTip("Back")
        self.back_btn.setMaximumWidth(40)
        self.back_btn.clicked.connect(self.back_requested.emit)

        self.forward_btn = QPushButton("→")
        self.forward_btn.setToolTip("Forward")
        self.forward_btn.setMaximumWidth(40)
        self.forward_btn.clicked.connect(self.forward_requested.emit)

        self.reload_btn = QPushButton("↻")
        self.reload_btn.setToolTip("Reload (Ctrl+R / F5)")
        self.reload_btn.setMaximumWidth(40)
        self.reload_btn.clicked.connect(self.reload_requested.emit)

        self.home_btn = QPushButton("⌂")
        self.home_btn.setToolTip("Home")
        self.home_btn.setMaximumWidth(40)
        self.home_btn.clicked.connect(self.home_requested.emit)

        # Address bar
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Enter URL or search...")
        self.address_input.returnPressed.connect(self._on_return_pressed)
        self.history_model = QStandardItemModel(self)
        self.completer = HistoryCompleter(self.history_model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setMaxVisibleItems(10)
        self.address_input.setCompleter(self.completer)
        self.completer.activated[str].connect(self.url_changed.emit)
        self.passwords_btn = QPushButton("Passwords")
        self.passwords_btn.setToolTip("KeePassXC passwords (Ctrl+Shift+L to fill)")
        self.passwords_btn.clicked.connect(self.passwords_requested.emit)
        self.zoom_btn = QPushButton("100%")
        self.zoom_btn.setToolTip("Reset page zoom (Ctrl+0)")
        self.zoom_btn.setMaximumWidth(70)
        self.zoom_btn.clicked.connect(self.reset_zoom_requested.emit)

        layout.addWidget(self.back_btn)
        layout.addWidget(self.forward_btn)
        layout.addWidget(self.reload_btn)
        layout.addWidget(self.home_btn)
        layout.addWidget(self.address_input)
        layout.addWidget(self.zoom_btn)
        layout.addWidget(self.passwords_btn)

    def _on_return_pressed(self) -> None:
        """Handle return key press."""
        text = self.address_input.text().strip()
        if text:
            self.url_changed.emit(text)

    def set_url(self, url: str) -> None:
        """Set the URL in the address bar."""
        self.address_input.setText(url)

    def set_history(self, entries: list[HistoryEntry]) -> None:
        self.completer.popup().hide()
        self.history_model.clear()
        for entry in entries:
            item = QStandardItem(f"{entry.title} — {entry.url}")
            item.setData(entry.url, Qt.ItemDataRole.UserRole)
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
