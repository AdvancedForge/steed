"""Bounded static PNG/JPEG decoding for owned immutable panel files."""

from PyQt6.QtCore import QBuffer, QIODevice, QSize, Qt
from PyQt6.QtGui import QImageReader

MAX_IMAGE_PIXELS = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def decode_image(data, media_type):
    expected = {"image/png": b"png", "image/jpeg": b"jpeg"}.get(media_type)
    if expected is None or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError("Use a static PNG or JPEG of at most 8 MB")
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, expected)
    reader.setAutoDetectImageFormat(False)
    if not reader.canRead():
        raise ValueError("Use bytes matching the declared PNG/JPEG type")
    size = reader.size()
    if (
        bytes(reader.format()) != expected
        or size.isEmpty()
        or size.width() * size.height() > MAX_IMAGE_PIXELS
        or max(size.width(), size.height()) > 8192
    ):
        raise ValueError("Use a matching PNG/JPEG of at most four megapixels")
    reader.setAutoTransform(True)
    if size.width() > 1600 or size.height() > 1000:
        reader.setScaledSize(
            size.scaled(QSize(1600, 1000), Qt.AspectRatioMode.KeepAspectRatio)
        )
    image = reader.read()
    if image.isNull():
        raise ValueError("The saved image could not be decoded")
    return image
