"""An extension's toolbar popup, shown in a small window under its button.

A frameless tool window rather than a Qt popup: Qt closes popup windows when
another popup (the menu it was opened from) closes, and web views inside them
lose their own dropdowns. It closes on Escape, when another window is
activated, or when the extension calls window.close().
"""

from PyQt6.QtCore import QEvent, QRectF, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout

from browser import webengine
from browser.webengine import QWebEngineView

WIDTH, HEIGHT = 380, 520


def badged(icon: QIcon, badge: dict, size=20) -> QIcon:
    """An extension's icon with its badge text (as set by the extension)."""
    text = (badge.get("text") or "")[:4]
    if not text:
        return icon
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not icon.isNull():
        painter.drawPixmap(0, 0, icon.pixmap(size * scale, size * scale))
    font = QFont()
    font.setPixelSize(int(size * scale * 0.42))
    font.setBold(True)
    painter.setFont(font)
    width = painter.fontMetrics().horizontalAdvance(text) + 6
    height = int(size * scale * 0.5)
    box = QRectF(size * scale - width, size * scale - height, width, height)
    color = QColor(badge.get("color") or "#d93025")
    if not color.isValid():
        color = QColor("#d93025")
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(box, height / 3, height / 3)
    painter.setPen(QColor("white"))
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class PopupPage(webengine.Page):
    """Links and window.open in a popup open as tabs of the window."""

    window = None

    def createWindow(self, kind):
        if self.window is None or getattr(self.window, "_closing", True):
            return None
        tab = self.window._new_tab(return_tab=True)
        return tab.page() if tab is not None else None

    # Speech recognition from the popup: Steed answers it like a tab's
    # (services.web_speech), with this page standing in for the tab.
    def page(self):
        return self

    def javaScriptConsoleMessage(self, level, message, line, source):
        from services.web_speech import PREFIX

        window = self.window
        if not message.startswith(PREFIX):
            super().javaScriptConsoleMessage(level, message, line, source)
        elif window is not None and not getattr(window, "_closing", True):
            window.web_speech.request(self, message)


class ExtensionPopup(QFrame):
    def __init__(self, profile, url, title, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFrameShape(QFrame.Shape.Box)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        self.view = QWebEngineView(self)
        self.page = PopupPage(profile, self.view)
        self.page.window = parent
        self.view.setPage(self.page)
        self.page.windowCloseRequested.connect(self.close)
        layout.addWidget(self.view)
        self.resize(WIDTH, HEIGHT)
        QShortcut(QKeySequence("Escape"), self, self.close)
        QApplication.instance().focusWindowChanged.connect(self._focus_moved)
        self.view.setUrl(QUrl(url))

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.view.setFocus()

    def _elsewhere(self, window):
        """Focus went to another window, not to the popup or its dropdowns."""
        if window is None or window.type() == Qt.WindowType.Popup:
            return False
        while window is not None:
            if window is self.windowHandle():
                return False
            window = window.transientParent()
        return True

    def _focus_moved(self, window):
        # A question it asked (the microphone) keeps it open.
        if QApplication.activeModalWidget() is not None:
            return
        if self._elsewhere(window):
            self.close()

    def event(self, event):
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and self.isVisible()
            and QApplication.activeModalWidget() is None
        ):
            if self._elsewhere(QApplication.focusWindow()):
                self.close()
        return super().event(event)

    def closeEvent(self, event):
        try:
            QApplication.instance().focusWindowChanged.disconnect(self._focus_moved)
        except TypeError:
            pass
        speech = getattr(self.page.window, "_web_speech", None)
        if speech is not None and speech.tab is self.page:
            speech.abort()
        # Pages go before their profile; this one goes with the popup.
        self.view.setPage(None)
        self.page.deleteLater()
        super().closeEvent(event)
