"""Send to Your Phone: the page's address as a QR code to scan.

Nothing is sent anywhere: the phone's camera reads the code from the screen
and opens the address in its own browser.
"""

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from browser.qr import QrTooLong, encode
from ui.html_dialog import HtmlSettingsDialog

QUIET = 4  # Light modules around the code, as scanners need.


def qr_image(text, size=560):
    """A crisp black-on-white QR code about size pixels across."""
    rows = encode(text)
    modules = len(rows) + 2 * QUIET
    scale = max(2, size // modules)
    image = QImage(modules * scale, modules * scale, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    for y, row in enumerate(rows):
        for x, dark in enumerate(row):
            if dark:
                painter.fillRect(
                    (x + QUIET) * scale,
                    (y + QUIET) * scale,
                    scale,
                    scale,
                    QColor("black"),
                )
    painter.end()
    return image


class SendToPhoneDialog(HtmlSettingsDialog):
    def __init__(self, url, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Send to your phone")
        # Percent-encoded, as browsers send it: plain ASCII any phone reads.
        url = bytes(QUrl(url).toEncoded()).decode("ascii") or url
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Point your phone's camera at the code to open this page there. "
            "Nothing is sent: the phone reads it from your screen."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.code = QLabel()
        self.code.setAccessibleName("QR code for " + (title or url))
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.code)
        self.address = QLabel(url)
        self.address.setWordWrap(True)
        self.address.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.address)
        try:
            self.code.setPixmap(QPixmap.fromImage(qr_image(url)))
        except QrTooLong as error:
            self.code.setText(str(error) + " Copy the address instead.")
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
