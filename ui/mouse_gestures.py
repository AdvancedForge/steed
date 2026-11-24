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
    """One filter for the whole application (never deleted while Steed runs),
    so no window's lifetime can leave a half-gone filter behind: it finds the
    window from the page widget the right button went down on."""

    _shared = None

    def __new__(cls, window=None):
        if cls._shared is None or sip.isdeleted(cls._shared):
            app = QApplication.instance()
            shared = super().__new__(cls)
            QObject.__init__(shared, app)
            shared.browser = None  # The window of the drag in progress.
            shared.target = None  # The page widget the right button went down on.
            shared.press = None  # (local position, global position, modifiers)
            shared.points = []
            shared.replaying = False
            app.installEventFilter(shared)
            cls._shared = shared
        return cls._shared

    def __init__(self, window=None):
        pass  # Set up once, in __new__.

    @staticmethod
    def _window_of(widget):
        """The browser window whose tab holds widget, if gestures are on."""
        if not isinstance(widget, QWidget) or sip.isdeleted(widget):
            return None
        window = widget.window()
        tabs = getattr(window, "tabs", None)
        if (
            tabs is None
            or getattr(window, "_closing", True)
            or not window.config.get("mouse_gestures", False)
        ):
            return None
        for tab in tabs:
            if not sip.isdeleted(tab) and (widget is tab or tab.isAncestorOf(widget)):
                return window
        return None

    def eventFilter(self, target, event):
        # Every event in Steed passes here: an error must never escape (PyQt
        # would abort).
        try:
            return self._filter(target, event)
        except Exception:
            return False

    def _filter(self, target, event):
        if self.replaying:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton and self.target is None:
                window = self._window_of(target)
                if window is None:
                    return False
                self.browser, self.target = window, target
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
            window, widget, press = self.browser, self.target, self.press
            self.browser = self.target = self.press = None
            self.points = []
            if sip.isdeleted(window) or window._closing:
                return True
            if gesture:
                self._run(window, gesture)
            else:
                self._replay(widget, press)
            return True
        return False

    @staticmethod
    def _run(window, gesture):
        found = GESTURES.get(gesture)
        if found is None:
            return
        label, method = found
        getattr(window, method)()
        window.status_bar.showMessage(f"Gesture: {label}", 1500)

    def _replay(self, widget, press):
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
