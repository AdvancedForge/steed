"""Address bar widget."""
from PyQt6.QtWidgets import QLineEdit, QHBoxLayout, QWidget, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon


class AddressBar(QWidget):
    """Address bar with navigation buttons."""
    
    url_changed = pyqtSignal(str)  # Emitted when URL is entered
    back_requested = pyqtSignal()
    forward_requested = pyqtSignal()
    reload_requested = pyqtSignal()
    home_requested = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
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
        self.reload_btn.setToolTip("Reload")
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
        
        layout.addWidget(self.back_btn)
        layout.addWidget(self.forward_btn)
        layout.addWidget(self.reload_btn)
        layout.addWidget(self.home_btn)
        layout.addWidget(self.address_input)
    
    def _on_return_pressed(self):
        """Handle return key press."""
        text = self.address_input.text().strip()
        if text:
            self.url_changed.emit(text)
    
    def set_url(self, url: str):
        """Set the URL in the address bar."""
        self.address_input.setText(url)
    
    def get_url(self) -> str:
        """Get the current URL text."""
        return self.address_input.text()
    
    def set_back_enabled(self, enabled: bool):
        """Enable/disable back button."""
        self.back_btn.setEnabled(enabled)
    
    def set_forward_enabled(self, enabled: bool):
        """Enable/disable forward button."""
        self.forward_btn.setEnabled(enabled)
