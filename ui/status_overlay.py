"""Status messages over the page while the bottom status bar is hidden.

The status bar is hidden by default; its messages ("Saved …", "Downloading …")
then show as a small label at the bottom left of the window, as Chrome shows
link addresses, and go when the message does.
"""

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QLabel

# Long enough to read; the status bar (View menu) keeps messages longer.
HIDE_MS = 5000


def routine(text):
    """Progress Steed shows all the time: in the bar, not over the page."""
    return text == "Ready" or text.startswith("Loading")


class StatusOverlay(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.label = QLabel(window)
        self.label.setObjectName("StatusOverlay")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.hide()
        self.timer = QTimer(self, singleShot=True, interval=HIDE_MS)
        self.timer.timeout.connect(self.label.hide)
        window.installEventFilter(self)

    def show_message(self, text):
        bar = getattr(self.window, "status_bar", None)
        if not text or bar is None or bar.isVisible() or routine(text):
            self.label.hide()
            return
        colors = getattr(getattr(self.window, "address_bar", None), "colors", None)
        if colors:
            self.label.setStyleSheet(
                f"#StatusOverlay {{ background: {colors['field']}; color: {colors['ink']};"
                f" border: 1px solid {colors['border']}; border-radius: 8px;"
                " padding: 4px 10px; }"
            )
        width = max(120, self.window.width() // 2)
        self.label.setText(
            self.label.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, width
            )
        )
        self.label.adjustSize()
        self._place()
        self.label.show()
        self.label.raise_()
        self.timer.start()

    def _place(self):
        self.label.move(8, self.window.height() - self.label.height() - 8)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Type.Resize:
            self._place()
        return False
