"""Edit a screenshot before saving or copying it: crop, highlight, hide."""

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

HIGHLIGHT = QColor(255, 214, 0, 90)
TOOLS = ("crop", "highlight", "hide")


def apply_edit(image: QImage, tool: str, rect: QRect) -> QImage:
    """The image after one edit; rect is in image pixels."""
    rect = rect.normalized().intersected(image.rect())
    if rect.width() < 2 or rect.height() < 2:
        return image
    if tool == "crop":
        return image.copy(rect)
    edited = image.convertToFormat(QImage.Format.Format_ARGB32)
    painter = QPainter(edited)
    if tool == "highlight":
        painter.fillRect(rect, HIGHLIGHT)
        painter.setPen(QPen(QColor(230, 170, 0), 2))
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
    else:
        painter.fillRect(rect, Qt.GlobalColor.black)
    painter.end()
    return edited


class Canvas(QWidget):
    """Shows the image at a fitting scale; dragging marks an area."""

    marked = pyqtSignal(QRect)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.scale = 1.0
        self.start = None
        self.current = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_image(self, image, width):
        self.image = image
        self.scale = min(1.0, max(0.05, width / max(1, image.width())))
        self.setFixedSize(
            max(1, int(image.width() * self.scale)),
            max(1, int(image.height() * self.scale)),
        )
        self.update()

    def to_image(self, point):
        return QPoint(int(point.x() / self.scale), int(point.y() / self.scale))

    def mousePressEvent(self, event):
        self.start = self.current = event.position().toPoint()
        self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None:
            return
        rect = QRect(
            self.to_image(self.start), self.to_image(event.position().toPoint())
        )
        self.start = self.current = None
        self.update()
        self.marked.emit(rect.normalized())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.image)
        if self.start is not None and self.current is not None:
            painter.setPen(QPen(QColor(79, 124, 255), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRect(self.start, self.current).normalized())


class ScreenshotEditor(QDialog):
    saved = pyqtSignal(QImage)
    copied = pyqtSignal(QImage)

    def __init__(self, image: QImage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Screenshot")
        self.history = [image]
        self.tool = "crop"
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.group = QButtonGroup(self)
        for name, label in (
            ("crop", "Crop"),
            ("highlight", "Highlight"),
            ("hide", "Hide"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(name == self.tool)
            button.clicked.connect(lambda _=False, name=name: self.choose(name))
            self.group.addButton(button)
            tools.addWidget(button)
        tools.addStretch(1)
        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(self.undo)
        tools.addWidget(self.undo_button)
        layout.addLayout(tools)
        self.canvas = Canvas()
        self.canvas.marked.connect(self.edit)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        for label, signal in (("Copy", self.copied), ("Save…", self.saved)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, signal=signal: self.finish(signal))
            actions.addWidget(button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        self.resize(900, 700)
        self.show_image()

    @property
    def image(self):
        return self.history[-1]

    def show_image(self):
        self.canvas.set_image(self.image, 840)
        self.undo_button.setEnabled(len(self.history) > 1)

    def choose(self, tool):
        if tool in TOOLS:
            self.tool = tool

    def edit(self, rect):
        edited = apply_edit(self.image, self.tool, rect)
        if edited is not self.image:
            self.history.append(edited)
            self.show_image()

    def undo(self):
        if len(self.history) > 1:
            self.history.pop()
            self.show_image()

    def finish(self, signal):
        signal.emit(self.image)
        self.accept()
