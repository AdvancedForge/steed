"""Tab hover previews: a small snapshot of a background tab, title and site.

A tab's snapshot is taken when you switch away from it, kept as a small JPEG
(about 15 KB), and shown when you hover the tab for a moment.
"""

from urllib.parse import urlsplit

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QColor, QPixmap
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
        super().__init__(
            parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        # The window itself is clear; the rounded card is drawn inside it, so
        # nothing square shows behind its corners.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("TabPreview")
        outer.addWidget(self.card)
        layout = QVBoxLayout(self.card)
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
            label.setParent(self.card)
        self.image.setParent(self.card)
        self.fixed_width = True
        self.notes.setWordWrap(True)
        layout.addWidget(self.image)
        layout.addWidget(self.title)
        layout.addWidget(self.site)
        layout.addWidget(self.notes)

    def apply_style(self, colors):
        """The tab strip's colours, not the system's (often yellow) tooltips."""
        if colors.get("glass"):  # Frosted: what is behind shows through.
            fill = QColor(colors["field"])
            alpha = 205 if colors["dark"] else 190
            card = (
                f"background: rgba({fill.red()},{fill.green()},{fill.blue()},{alpha});"
                " border: 1px solid "
                + ("rgba(255,255,255,60)" if colors["dark"] else "#ffffff")
                + "; border-radius: 14px;"
            )
        else:
            card = (
                f"background: {colors['field']};"
                f" border: 1px solid {colors['border']}; border-radius: 10px;"
            )
        self.setStyleSheet(
            f"#TabPreview {{ {card} }}"
            f" #TabPreview QLabel {{ background: transparent; color: {colors['ink']}; }}"
            " #TabPreviewTitle { font-weight: 600; }"
            f" #TabPreviewMuted {{ color: {colors['muted']}; }}"
        )

    def show_for(self, tab, point, notes=(), snapshot=True, fixed_width=True):
        """fixed_width: every card as wide as a snapshot (as in Chrome), so
        they do not change size from tab to tab; else as wide as the text."""
        self.fixed_width = fixed_width
        width = WIDTH + 20  # The snapshot and the card's margins.
        self.card.setMinimumWidth(width if fixed_width else 0)
        self.card.setMaximumWidth(width)
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
        self.card.adjustSize()
        self.adjustSize()
        self.move(point.x(), point.y() + 4)
        self.show()
