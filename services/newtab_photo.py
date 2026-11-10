"""The new tab page's background photo: a random one that changes now and then.

Photos come from Lorem Picsum (free Unsplash photos, credited on the page).
Steed keeps the current one in <config>/newtab/ (shared by all profiles) and
fetches the next in the background when it is due, so a new tab never waits;
until the first arrives, or offline, the page shows a soft gradient. Picsum is
only asked when a profile uses the photo background.
"""

import json
import random
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

LIST_URL = "https://picsum.photos/v2/list?page={page}&limit=100"
IMAGE_URL = "https://picsum.photos/id/{id}/1920/1080.jpg"
MAX_BYTES = 5 << 20
# How often it changes: seconds (0: a new one for each new tab).
PERIODS = {"new_tab": 0, "hourly": 3600, "6h": 6 * 3600, "daily": 24 * 3600}
DEFAULT_PERIOD = "6h"


def theme_color(image) -> str | None:
    """The colour a photo is about: the strongest hue among its colourful
    pixels (or its average, for a grey photo), as #rrggbb. image: a QImage."""
    from PyQt6.QtCore import Qt

    if image is None or image.isNull():
        return None
    small = image.scaled(
        48,
        27,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    buckets = {}
    total = [0.0, 0.0, 0.0, 0.0]
    for y in range(small.height()):
        for x in range(small.width()):
            color = small.pixelColor(x, y)
            red, green, blue = color.red(), color.green(), color.blue()
            total = [total[0] + red, total[1] + green, total[2] + blue, total[3] + 1]
            hue, saturation, value = (
                color.hsvHueF(),
                color.hsvSaturationF(),
                color.valueF(),
            )
            weight = saturation * value * (1.0 if 0.2 < value < 0.95 else 0.2)
            if hue < 0 or weight < 0.08:
                continue
            bucket = buckets.setdefault(int(hue * 12) % 12, [0.0, 0.0, 0.0, 0.0])
            bucket[0] += red * weight
            bucket[1] += green * weight
            bucket[2] += blue * weight
            bucket[3] += weight
    if buckets:
        best = max(buckets.values(), key=lambda b: b[3])
        if best[3] > 2:
            return "#" + "".join(f"{int(best[i] / best[3]):02x}" for i in range(3))
    if not total[3]:
        return None
    return "#" + "".join(f"{int(total[i] / total[3]):02x}" for i in range(3))


class NewTabPhotos(QObject):
    """changed(first) when a new photo is ready; first: there was none."""

    changed = pyqtSignal(bool)

    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.network = None
        self.fetching = False

    @property
    def image_path(self):
        return self.folder / "photo.jpg"

    @property
    def info_path(self):
        return self.folder / "photo.json"

    def current(self) -> dict | None:
        """{"path", "author", "page", "fetched"} of the photo to show, or None."""
        try:
            info = json.loads(self.info_path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(info, dict) or not self.image_path.is_file():
            return None
        color = info.get("color")
        return {
            "path": self.image_path,
            "color": color if isinstance(color, str) and len(color) == 7 else None,
            "author": str(info.get("author", ""))[:100],
            "page": str(info.get("page", ""))
            if str(info.get("page", "")).startswith("https://")
            else "",
            "fetched": float(info.get("fetched", 0) or 0),
        }

    def due(self, period: str) -> bool:
        found = self.current()
        seconds = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])
        return found is None or time.time() - found["fetched"] >= seconds

    def refresh(self, period: str = DEFAULT_PERIOD, now: bool = False):
        """Fetch a new photo in the background when one is due (or now)."""
        if self.fetching or not (now or self.due(period)):
            return
        self.fetching = True
        if self.network is None:
            self.network = QNetworkAccessManager(self)
        url = LIST_URL.format(page=random.randint(1, 10))
        self._get(url, self._listed)

    def _get(self, url, done):
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self.network.get(request)
        reply.downloadProgress.connect(
            lambda received, total: (
                (received > MAX_BYTES or total > MAX_BYTES) and reply.abort()
            )
        )
        reply.finished.connect(lambda: self._finished(reply, done))

    def _finished(self, reply, done):
        ok = reply.error() == QNetworkReply.NetworkError.NoError
        # A failed reply can return None (see search_suggestions).
        data = bytes(reply.readAll() or b"") if ok else b""
        reply.deleteLater()
        if not ok:
            self.fetching = False
            return
        done(data)

    def _listed(self, data):
        try:
            photos = [
                p
                for p in json.loads(data)
                if isinstance(p, dict) and str(p.get("id", "")).isdigit()
            ]
        except ValueError:
            photos = []
        if not photos:
            self.fetching = False
            return
        chosen = random.choice(photos)
        self._get(
            IMAGE_URL.format(id=chosen["id"]),
            lambda image: self._saved(image, chosen),
        )

    def _saved(self, image, chosen):
        self.fetching = False
        if not image.startswith(b"\xff\xd8"):
            return  # Not a JPEG.
        first = self.current() is None
        from PyQt6.QtGui import QImage

        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.image_path.with_suffix(".tmp")
        temporary.write_bytes(image)
        temporary.replace(self.image_path)
        color = theme_color(QImage.fromData(image, "JPEG"))
        self.info_path.write_text(
            json.dumps(
                {
                    "id": str(chosen["id"]),
                    "author": str(chosen.get("author", ""))[:100],
                    "page": str(chosen.get("url", "")),
                    "fetched": time.time(),
                    "color": color,
                }
            ),
            "utf-8",
        )
        self.changed.emit(first)
