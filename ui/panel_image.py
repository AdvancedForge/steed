"""Static native image preview; clearing removes source pixels immediately."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy


class PanelImage(QLabel):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self._image = QImage(image)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 100)
        self.setMaximumHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.draw()

    def set_image(self, image):
        self._image = QImage(image)
        self.draw()

    def draw(self):
        if not self._image.isNull():
            self.setPixmap(
                QPixmap.fromImage(self._image).scaled(
                    self.contentsRect().size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.draw()

    def clear(self):
        self._image = QImage()
        super().clear()
