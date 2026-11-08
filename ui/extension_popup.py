"""An extension's toolbar popup, shown in a small window under the menu."""

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QVBoxLayout, QWidget

import webengine
from webengine import QWebEngineView

WIDTH, HEIGHT = 380, 520


class ExtensionPopup(QWidget):
    def __init__(self, profile, url, title, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.page = webengine.Page(profile, self.view)
        self.view.setPage(self.page)
        self.page.windowCloseRequested.connect(self.close)
        layout.addWidget(self.view)
        self.resize(WIDTH, HEIGHT)
        self.view.setUrl(QUrl(url))

    def closeEvent(self, event):
        # Pages go before their profile; this one goes with the popup.
        self.view.setPage(None)
        self.page.deleteLater()
        super().closeEvent(event)
