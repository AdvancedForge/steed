"""One-time tips: a small bubble the first time you reach something, once.

Each tip has a key; once shown (and closed, or acknowledged with "Got it")
it is remembered in the config ("tips_seen") and never shown again. Only
one tip at a time, none while the Welcome tour is the page in front, and
none while STEED_NO_TIPS is set (the tests set it).
"""

import os

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QPoint, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

TIPS = {
    "address": "Type to search or go. Start with @ to ask your agent.",
    "dictation": "Press Ctrl+Shift+Space to speak instead of typing.",
    "customize": "Make this page yours: Customize is in the ☰ menu.",
    "agent": "Select text on any page, right-click, and choose Ask Agent.",
}
# The New Tab tip when its gear is showing (not tucked into the ☰ menu).
CUSTOMIZE_GEAR = "Make this page yours: the gear in the corner customizes it."
SHOW_MS = 14000  # A tip left alone goes away (and counts as seen).


def wanted(config, key):
    if os.environ.get("STEED_NO_TIPS") or key not in TIPS:
        return False
    seen = config.get("tips_seen", [])
    return key not in (seen if isinstance(seen, list) else [])


def seen(config, key):
    found = config.get("tips_seen", [])
    found = found if isinstance(found, list) else []
    if key not in found:
        config.set("tips_seen", [*found, key])


class TipBubble(QWidget):
    """The bubble, over the window near what it is about."""

    def __init__(self, window, key, anchor, text=None):
        super().__init__(window)
        self.window_, self.key = window, key
        colors = getattr(getattr(window, "address_bar", None), "colors", {}) or {}
        self.fill = QColor(colors.get("field") or colors.get("toolbar") or "#2b2f3a")
        self.fill.setAlpha(250)
        self.edge = QColor(colors.get("accent") or "#8caaff")
        ink = colors.get("ink") or "#e8edf6"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 10, 12)
        layout.setSpacing(12)
        text = QLabel(text or TIPS[key])
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {ink}; background: transparent; font-size: 13px;")
        layout.addWidget(text, 1)
        ok = QPushButton("Got it")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(
            f"QPushButton {{ color: {ink}; background: transparent; border: 1px solid"
            f" {self.edge.name()}; border-radius: 13px; padding: 4px 12px; }}"
            f" QPushButton:hover {{ background: {self.edge.name()}33; }}"
        )
        ok.clicked.connect(self.dismiss)
        layout.addWidget(ok)
        self.setFixedWidth(360)
        self.adjustSize()
        self._place(anchor)
        # The bubble, its words and its button fade in together.
        self.opacity = 1.0
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)
        self.show()
        self.raise_()
        fade = QVariantAnimation(self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(260)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.valueChanged.connect(self._fade)
        fade.start()
        self._fade_in = fade
        QTimer.singleShot(SHOW_MS, self.dismiss)

    def _place(self, anchor):
        window = self.window_
        if (
            anchor is not None
            and not sip.isdeleted(anchor)
            and anchor.isVisible()
            and anchor.height() > window.height() // 2
        ):
            # A tall panel (the agent sidebar): near its top, clear of its
            # message box.
            point = anchor.mapTo(window, QPoint(16, 64))
        elif anchor is not None and not sip.isdeleted(anchor) and anchor.isVisible():
            point = anchor.mapTo(window, QPoint(16, anchor.height() + 8))
        else:  # About the page: inside it, left of its ☰ button.
            page = window.tab_container
            corner = page.mapTo(window, QPoint(page.width(), 0))
            point = QPoint(corner.x() - self.width() - 96, corner.y() + 24)
        x = max(12, min(point.x(), window.width() - self.width() - 12))
        y = max(12, min(point.y(), window.height() - self.height() - 12))
        self.move(x, y)

    def _fade(self, value):
        if not sip.isdeleted(self.effect):
            self.effect.setOpacity(float(value))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self.opacity)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, 16)
        painter.fillPath(shape, self.fill)
        painter.setPen(self.edge)
        painter.drawPath(shape)

    def dismiss(self):
        if sip.isdeleted(self):
            return
        seen(self.window_.config, self.key)
        if getattr(self.window_, "_tip", None) is self:
            self.window_._tip = None
        self.hide()  # Gone at once, whenever the deletion itself happens.
        self.deleteLater()


def offer(window, key, anchor=None, when=None, text=None):
    """Show this tip if it has not been shown yet and none is showing (and,
    for a tip about a page, ``when()`` says that page is still in front)."""
    if getattr(window, "_closing", False) or not window.isVisible():
        return None
    if when is not None and not when():
        return None
    current = getattr(window, "_tip", None)
    if current is not None and not sip.isdeleted(current):
        return None
    if not wanted(window.config, key):
        return None
    tab = window._current_browser_tab()
    if tab is not None and tab.url().host() == "welcome":
        return None  # The tour is talking.
    window._tip = TipBubble(window, key, anchor, text)
    return window._tip
