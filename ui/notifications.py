"""Steed's notifications (agents replying or needing you, watches, routines,
automations, your pages), as Settings → General → Notifications says:

- where: on your desktop, only inside Steed (a note in the corner of the
  window you used last, kept until you have seen it), or nowhere;
- how often: every time, at most one every 15 minutes or every hour (the rest
  gathered into one), or only when something needs you.

Something that needs you (an approval, a page handed to you, a watch that
lost its sign-in, an agent over its cost limit) is never held back.
"""

import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
)

WHERE = ("desktop", "steed", "off")
OFTEN = ("every", "15min", "hourly", "needs_me")
GAPS = {"15min": 15 * 60, "hourly": 60 * 60}
KINDS = ("news", "needs_me")
TOAST_SECONDS = 10  # Counted only while the window is in front.
MAX_TOASTS = 3
SUMMARY_TITLES = 4


def settings(config):
    where = config.get("notifications_where", "desktop")
    often = config.get("notifications_often", "every")
    return (
        where if where in WHERE else "desktop",
        often if often in OFTEN else "every",
    )


def notifications_for(registry):
    """The one Notifications for a set of windows (an application)."""
    found = getattr(registry, "_notifications", None)
    if found is None or sip.isdeleted(found):
        found = registry._notifications = Notifications(registry)
    return found


class Notifications(QObject):
    def __init__(self, registry, *, clock=time.monotonic):
        super().__init__(registry)
        self.registry = registry
        self.config = registry.config
        self.clock = clock
        self.desktop = None
        self.last_sent = None
        self.held = []  # (summary, body, on_click) waiting for the summary
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)

    def notify(self, summary, body="", on_click=None, replaces=0, *, kind="news"):
        """Show (or hold, or drop) one notification, as the settings say."""
        where, often = settings(self.config)
        if where == "off" or (often == "needs_me" and kind != "needs_me"):
            return 0
        gap = GAPS.get(often)
        if kind != "needs_me" and gap:
            now = self.clock()
            if self.last_sent is not None and now - self.last_sent < gap:
                self.held.append((summary, body, on_click))
                if not self.timer.isActive():
                    wait = gap - (now - self.last_sent)
                    self.timer.start(max(1000, int(wait * 1000)))
                return 0
            self.last_sent = now
        return self._deliver(where, summary, body, on_click, replaces)

    def flush(self):
        """At the end of a quiet spell: what was held, as one notification."""
        held, self.held = self.held, []
        where, often = settings(self.config)
        if not held or where == "off" or often == "needs_me":
            return
        self.last_sent = self.clock()
        if len(held) == 1:
            summary, body, on_click = held[0]
            self._deliver(where, summary, body, on_click, 0)
            return
        titles = [summary for summary, _, _ in held]
        shown = "; ".join(titles[:SUMMARY_TITLES])
        if len(titles) > SUMMARY_TITLES:
            shown += f"; and {len(titles) - SUMMARY_TITLES} more"
        self._deliver(where, f"{len(held)} updates from Steed", shown, self._front, 0)

    def _deliver(self, where, summary, body, on_click, replaces):
        if where == "steed":
            window = self._window()
            if window is None:
                return 0
            show_toast(window, summary, body, on_click)
            return 1
        if self.desktop is None:
            from ui.desktop_notify import DesktopNotifier

            self.desktop = DesktopNotifier(self)
        return self.desktop.notify(summary, body, on_click=on_click, replaces=replaces)

    def _window(self):
        from browser.browser_window import BrowserWindow

        owner = getattr(self.registry, "last_active_owner", None)
        window = owner.parent() if owner is not None else None
        if (
            isinstance(window, BrowserWindow)
            and not sip.isdeleted(window)
            and not window._closing
        ):
            return window
        for widget in QApplication.topLevelWidgets():
            if (
                isinstance(widget, BrowserWindow)
                and not widget._closing
                and widget.isVisible()
            ):
                return widget
        return None

    def _front(self):
        window = self._window()
        if window is not None:
            window.showNormal()
            window.raise_()
            window.activateWindow()


class Toast(QFrame):
    """A note in the window's top-right corner: click it to open what it is
    about; it goes after TOAST_SECONDS in front of you, or with ×."""

    def __init__(self, window, summary, body, on_click):
        super().__init__(window)
        self.window_ = window
        self.on_click = on_click
        self.left = TOAST_SECONDS
        self.setObjectName("SteedToast")
        self.setAccessibleName("Notification")
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if on_click
            else Qt.CursorShape.ArrowCursor
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 8, 10)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(summary[:120])
        self.title.setWordWrap(True)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        text.addWidget(self.title)
        self.body = QLabel(body[:400])
        self.body.setWordWrap(True)
        self.body.setVisible(bool(body))
        text.addWidget(self.body)
        row.addLayout(text, 1)
        close = QToolButton(self)
        close.setText("×")
        close.setAccessibleName("Dismiss")
        close.setAutoRaise(True)
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        colors = getattr(window, "_shown_colors", None) or {}
        background = colors.get("toolbar", "palette(window)")
        ink = colors.get("ink", "palette(window-text)")
        self.setStyleSheet(
            f"#SteedToast {{ background: {background}; border-radius: 14px;"
            " border: 1px solid rgba(128,128,128,90); }"
            f"#SteedToast QLabel, #SteedToast QToolButton {{ color: {ink};"
            " background: transparent; border: none; }"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self._tick)
        self.ticker.start()
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.window_ and event.type() == QEvent.Type.Resize:
            place_toasts(self.window_)
        return False

    def _tick(self):
        if self.window_.isActiveWindow():
            self.left -= 1
            if self.left <= 0:
                self.dismiss()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.on_click:
            callback = self.on_click
            self.dismiss()
            callback()
            return
        super().mouseReleaseEvent(event)

    def dismiss(self):
        toasts = getattr(self.window_, "_toasts", [])
        if self in toasts:
            toasts.remove(self)
        self.ticker.stop()
        self.window_.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        place_toasts(self.window_)


def show_toast(window, summary, body="", on_click=None):
    toasts = getattr(window, "_toasts", None)
    if toasts is None:
        toasts = window._toasts = []
    while len(toasts) >= MAX_TOASTS:
        toasts[0].dismiss()
    toast = Toast(window, summary, body, on_click)
    toasts.append(toast)
    place_toasts(window)
    toast.show()
    toast.raise_()
    return toast


def place_toasts(window):
    """Newest on top, in the top-right corner of the page area."""
    width = min(360, max(240, window.width() - 32))
    top = 72
    bar = getattr(window, "address_bar", None)
    if bar is not None and not sip.isdeleted(bar) and bar.isVisible():
        top = bar.mapTo(window, bar.rect().bottomLeft()).y() + 12
    for toast in reversed(getattr(window, "_toasts", [])):
        toast.setFixedWidth(width)
        layout = toast.layout()
        layout.activate()
        height = layout.totalHeightForWidth(width)
        toast.setFixedHeight(height if height > 0 else toast.sizeHint().height())
        toast.move(window.width() - width - 16, top)
        top += toast.height() + 8
