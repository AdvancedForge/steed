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
    """The card shown when hovering a tab, in Steed's own colours."""

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("TabPreview")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(3)
        self.image = QLabel(self)
        self.title = QLabel(self)
        self.title.setObjectName("TabPreviewTitle")
        self.site = QLabel(self)
        self.site.setObjectName("TabPreviewMuted")
        self.notes = QLabel(self)
        self.notes.setObjectName("TabPreviewMuted")
        for label in (self.title, self.site, self.notes):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setMaximumWidth(WIDTH)
        self.notes.setWordWrap(True)
        layout.addWidget(self.image)
        layout.addWidget(self.title)
        layout.addWidget(self.site)
        layout.addWidget(self.notes)

    def apply_style(self, colors):
        """The tab strip's colours, not the system's (often yellow) tooltips."""
        self.setStyleSheet(
            f"#TabPreview {{ background: {colors['field']};"
            f" border: 1px solid {colors['border']}; border-radius: 10px; }}"
            f" #TabPreview QLabel {{ background: transparent; color: {colors['ink']}; }}"
            " #TabPreviewTitle { font-weight: 600; }"
            f" #TabPreviewMuted {{ color: {colors['muted']}; }}"
        )

    def show_for(self, tab, point, notes=(), snapshot=True):
        data = getattr(tab, "preview", None) if snapshot else None
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
        self.site.setVisible(bool(host))
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))
        self.adjustSize()
        self.move(point.x(), point.y() + 4)
        self.show()
