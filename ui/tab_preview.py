"""Tab hover previews: a small snapshot of a background tab, title and site.

A tab's snapshot is taken when you switch away from it, kept as a small JPEG
(about 15 KB), and shown when you hover the tab for a moment.
"""

from urllib.parse import urlsplit

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

WIDTH = 320


def snapshot(view):
    """The view as small JPEG bytes, or None when it shows nothing yet."""
    if view is None or not view.isVisible() or view.width() < 50:
        return None
    pixmap = view.grab()
    if pixmap.isNull():
        return None
    image = pixmap.toImage().scaledToWidth(
        WIDTH, Qt.TransformationMode.SmoothTransformation
    )
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", 72)
    return bytes(data)


class TabPreview(QFrame):
    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("TabPreview")
        self.setStyleSheet(
            "#TabPreview { background: palette(window); border: 1px solid palette(mid);"
            " border-radius: 8px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        self.image = QLabel(self)
        self.title = QLabel(self)
        self.title.setStyleSheet("font-weight: 600;")
        self.site = QLabel(self)
        self.site.setStyleSheet("color: palette(placeholder-text);")
        for label in (self.title, self.site):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setMaximumWidth(WIDTH)
        layout.addWidget(self.image)
        layout.addWidget(self.title)
        layout.addWidget(self.site)

    def show_for(self, tab, point):
        data = getattr(tab, "preview", None)
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data, "JPEG")
        self.image.setPixmap(pixmap)
        self.image.setVisible(not pixmap.isNull())
        metrics = self.title.fontMetrics()
        self.title.setText(
            metrics.elidedText(
                tab.title() or "New Tab", Qt.TextElideMode.ElideRight, WIDTH
            )
        )
        url = tab.url()
        host = urlsplit(url.toString()).hostname or url.toString()
        self.site.setText(host.removeprefix("www."))
        self.adjustSize()
        self.move(point.x(), point.y() + 4)
        self.show()
