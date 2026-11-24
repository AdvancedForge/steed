"""Mouse gestures: hold the right button over a page and draw.

← Back · → Forward · ↓ New tab · ↓→ Close tab · ↑↓ Reload

Off by default (Settings → General → Mouse gestures): some sites use right
dragging themselves. While the right button is down Steed holds the press
back; a gesture runs its action, and a plain right-click is replayed so the
page's menu opens as usual (when the button comes up, as on Windows).
"""

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

# A stroke counts once the pointer has moved this far in one direction.
STEP = 24
GESTURES = {
    "L": ("Back", "_go_back"),
    "R": ("Forward", "_go_forward"),
    "D": ("New tab", "_new_tab"),
    "DR": ("Close tab", "_close_current_tab"),
    "UD": ("Reload", "_reload"),
}


def directions(points, step=STEP):
    """The strokes in a path of points, as letters (L R U D), repeats merged."""
    if not points:
        return ""
    found, anchor = [], points[0]
    for x, y in points[1:]:
        dx, dy = x - anchor[0], y - anchor[1]
        if max(abs(dx), abs(dy)) < step:
            continue
        if abs(dx) > abs(dy):
            letter = "R" if dx > 0 else "L"
        else:
            letter = "D" if dy > 0 else "U"
        if not found or found[-1] != letter:
            found.append(letter)
        anchor = (x, y)
    return "".join(found)


class MouseGestures(QObject):
    """One per window: watches right-button drags over its pages."""

    def __init__(self, window):
        super().__init__(window)
        self.browser = window
        self.target = None  # The page widget the right button went down on.
        self.press = None  # (local position, global position, modifiers)
        self.points = []
        self.replaying = False
        QApplication.instance().installEventFilter(self)
        window.destroyed.connect(self._stop)

    def _stop(self):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _enabled(self):
        return bool(self.browser.config.get("mouse_gestures", False))

    def _page_widget(self, widget):
        """True when widget is (inside) one of this window's tabs."""
        if not isinstance(widget, QWidget) or sip.isdeleted(widget):
            return False
        for tab in getattr(self.browser, "tabs", ()):
            if not sip.isdeleted(tab) and (widget is tab or tab.isAncestorOf(widget)):
                return True
        return False

    def eventFilter(self, target, event):
        if self.replaying:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if (
                event.button() == Qt.MouseButton.RightButton
                and self.target is None
                and self._enabled()
                and not self.browser._closing
                and self._page_widget(target)
            ):
                self.target = target
                self.press = (
                    event.position(),
                    event.globalPosition(),
                    event.modifiers(),
                )
                point = event.globalPosition()
                self.points = [(point.x(), point.y())]
                return True  # Held back until we know it isn't a gesture.
            return False
        if self.target is None or target is not self.target:
            return False
        if kind == QEvent.Type.ContextMenu:
            return True  # The menu waits for the button to come up.
        if kind == QEvent.Type.MouseMove:
            point = event.globalPosition()
            self.points.append((point.x(), point.y()))
            return True
        if (
            kind == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.RightButton
        ):
            gesture = directions(self.points)
            widget, press = self.target, self.press
            self.target, self.press, self.points = None, None, []
            if gesture:
                self._run(gesture)
            else:
                self._replay(widget, press, event)
            return True
        return False

    def _run(self, gesture):
        found = GESTURES.get(gesture)
        if found is None:
            return
        label, method = found
        getattr(self.browser, method)()
        self.browser.status_bar.showMessage(f"Gesture: {label}", 1500)

    def _replay(self, widget, press, release):
        """A plain right-click after all: give the page its press and release."""
        if sip.isdeleted(widget):
            return
        position, global_position, modifiers = press
        self.replaying = True
        try:
            for kind, buttons in (
                (QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton),
                (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
            ):
                QCoreApplication.sendEvent(
                    widget,
                    QMouseEvent(
                        kind,
                        QPointF(position),
                        QPointF(global_position),
                        Qt.MouseButton.RightButton,
                        buttons,
                        modifiers,
                    ),
                )
        finally:
            self.replaying = False
