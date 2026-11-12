"""Compact mode: the page gets the whole window.

The tab strip (when it is on top) and the toolbar leave the window's layout and
float over the page instead: shown while the pointer is at the top edge or on
them, or while you type an address (Ctrl+L), and hidden again shortly after.
The page does not move when they appear.
"""

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

# Pixels at the top of the page that bring the bars back, and how long they
# stay after the pointer leaves them (ms).
REVEAL_EDGE = 6
HIDE_DELAY = 700


class CompactChrome(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.active = False
        self.moving = False  # Bars being moved: their placement is ours.
        self.moved = []
        self.overlay = QWidget(window.chrome_column)
        self.overlay.setObjectName("CompactBars")
        self.layout = QVBoxLayout(self.overlay)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.overlay.hide()
        self.poll = QTimer(self)
        self.poll.setInterval(100)
        self.poll.timeout.connect(self._poll)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(HIDE_DELAY)
        self.hide_timer.timeout.connect(self.conceal)

    def _bars(self):
        window = self.window
        bars = []
        if self._strip_on_top():
            bars.append(window.tab_bar)
        bars.append(window.address_bar)
        return bars

    def _position(self):
        appearance = getattr(self.window.tab_bar, "appearance", None) or {}
        return appearance.get("position", "top")

    def _strip_on_top(self):
        return self._position() == "top"

    def enable(self, on):
        """Turn compact mode on or off (moving the bars, not recreating them)."""
        on = bool(on)
        if on == self.active:
            return
        self.active = on
        self.moving = True
        try:
            if on:
                for bar in self._bars():
                    self.window.chrome_layout.removeWidget(bar)
                    self.layout.addWidget(bar)
                    bar.show()
                    self.moved.append(bar)
                self.poll.start()
                self.overlay.hide()
            else:
                self.poll.stop()
                self.hide_timer.stop()
                self.overlay.hide()
                self._restore()
        finally:
            self.moving = False

    def _restore(self):
        window = self.window
        for bar in self.moved:
            self.layout.removeWidget(bar)
        if window.tab_bar in self.moved:
            window._place_tab_strip(self._position())
        if window.address_bar in self.moved:
            layout = window.chrome_layout
            index = layout.indexOf(window.tab_bar)
            layout.insertWidget(index + 1 if index >= 0 else 0, window.address_bar)
            window.address_bar.show()
        self.moved = []

    def resync(self):
        """The tab strip moved edge: take the bars that belong over the page."""
        if self.active:
            self.enable(False)
            self.enable(True)

    def keep(self):
        """Stay shown while typing an address or a menu from the bars is open."""
        field = getattr(self.window.address_bar, "address_input", None)
        return bool(
            (field is not None and field.hasFocus())
            or QApplication.activePopupWidget() is not None
        )

    def reveal(self):
        column = self.window.chrome_column
        self.overlay.setGeometry(0, 0, column.width(), self.overlay.sizeHint().height())
        self.overlay.show()
        self.overlay.raise_()
        self.hide_timer.stop()

    def conceal(self):
        if self.active and not self.keep():
            self.overlay.hide()

    def _poll(self):
        column = self.window.chrome_column
        point = column.mapFromGlobal(QCursor.pos())
        inside = 0 <= point.x() < column.width()
        at_top = inside and 0 <= point.y() <= REVEAL_EDGE
        over = self.overlay.isVisible() and self.overlay.geometry().contains(point)
        if (at_top and self.window.isActiveWindow()) or over or self.keep():
            if not self.overlay.isVisible():
                self.reveal()
            self.hide_timer.stop()
        elif self.overlay.isVisible() and not self.hide_timer.isActive():
            self.hide_timer.start()
