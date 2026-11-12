"""The new tab page's background photo: one that changes now and then.

Photos come from Lorem Picsum (free Unsplash photos, credited on the page), or
from your own: photos you add (copied into <config>/newtab/mine/), a folder you
choose (read in place) or your favourites (<config>/newtab/favourites/). Local
photos are shown in a shuffled order, each once before any repeats. Steed keeps
the current one in <config>/newtab/ (shared by all profiles) and gets the next
when it is due, so a new tab never waits; until the first arrives, or offline,
the page shows a soft gradient. Picsum is only asked when a profile uses it.
"""

import json
import random
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

LIST_URL = "https://picsum.photos/v2/list?page={page}&limit=100"
IMAGE_URL = "https://picsum.photos/id/{id}/1920/1080.jpg"
MAX_BYTES = 5 << 20
# How often it changes: seconds (0: a new one for each new tab).
PERIODS = {"new_tab": 0, "hourly": 3600, "6h": 6 * 3600, "daily": 24 * 3600}
DEFAULT_PERIOD = "6h"
# Where photos come from, and how they look.
SOURCES = ("online", "mine", "folder", "favourites")
STYLES = ("color", "grayscale")
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_LOCAL_BYTES = 40 << 20
MAX_LOCAL_PIXELS = 80_000_000
MAX_FILES = 5000  # Read at most this many files from a folder.


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

    @property
    def mine_folder(self):
        return self.folder / "mine"

    @property
    def favourites_folder(self):
        return self.folder / "favourites"

    @property
    def blocked_path(self):
        return self.folder / "blocked.json"

    def _info(self):
        try:
            info = json.loads(self.info_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return info if isinstance(info, dict) else {}

    def local_folder(self, source, folder=""):
        """The folder a local source reads, or None for online."""
        if source == "mine":
            return self.mine_folder
        if source == "favourites":
            return self.favourites_folder
        if source == "folder" and folder:
            return Path(folder)
        return None

    def local_photos(self, directory):
        """Image files in a folder (not its subfolders), by name."""
        try:
            entries = sorted(Path(directory).iterdir())[:MAX_FILES]
        except OSError:
            return []
        return [
            path
            for path in entries
            if path.suffix.lower() in IMAGE_TYPES and path.is_file()
        ]

    def current(self) -> dict | None:
        """{"path", "author", "page", "fetched"} of the photo to show, or None."""
        try:
            info = json.loads(self.info_path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(info, dict) or not self.image_path.is_file():
            return None
        color = info.get("color")
        source = info.get("source", "online")
        return {
            "source": source if source in SOURCES else "online",
            "style": info.get("style") if info.get("style") in STYLES else "color",
            "folder": str(info.get("folder", "")),
            "favourite": info.get("favourite") is True,
            "id": str(info.get("id", "")),
            "path": self.image_path,
            "color": color if isinstance(color, str) and len(color) == 7 else None,
            "author": str(info.get("author", ""))[:100],
            "page": str(info.get("page", ""))
            if str(info.get("page", "")).startswith("https://")
            else "",
            "fetched": float(info.get("fetched", 0) or 0),
        }

    def due(self, period: str, source="online", folder="", style="color") -> bool:
        """A new photo is due: none yet, its time is up, or it came from
        another source or style than the one now chosen."""
        found = self.current()
        seconds = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])
        return (
            found is None
            or time.time() - found["fetched"] >= seconds
            or found["source"] != source
            or found["style"] != style
            or (source == "folder" and found["folder"] != str(folder))
        )

    def refresh(
        self,
        period: str = DEFAULT_PERIOD,
        now: bool = False,
        source: str = "online",
        folder: str = "",
        style: str = "color",
    ):
        """Get a new photo when one is due (or now): local ones at once,
        online ones in the background."""
        if self.fetching or not (now or self.due(period, source, folder, style)):
            return
        directory = self.local_folder(source, folder)
        if directory is not None:
            self._pick_local(source, directory, style)
            return
        self.style = style
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
        blocked = set(self.blocked()) | {self._info().get("id")}
        photos = [p for p in photos if str(p["id"]) not in blocked] or photos
        if not photos:
            self.fetching = False
            return
        chosen = random.choice(photos)
        grayscale = getattr(self, "style", "color") == "grayscale"
        self._get(
            IMAGE_URL.format(id=chosen["id"]) + ("?grayscale" if grayscale else ""),
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
        self._write_info(
            {
                "id": str(chosen["id"]),
                "author": str(chosen.get("author", ""))[:100],
                "page": str(chosen.get("url", "")),
                "fetched": time.time(),
                "color": color,
                "source": "online",
                "style": getattr(self, "style", "color"),
            }
        )
        self.changed.emit(first)

    def _write_info(self, info):
        temporary = self.info_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(info), "utf-8")
        temporary.replace(self.info_path)

    # ---------- Your own photos ----------

    def _pick_local(self, source, directory, style):
        """The next photo from a folder: a shuffled round, each photo once."""
        names = [path.name for path in self.local_photos(directory)]
        if not names:
            return False
        info = self._info()
        same = info.get("source") == source and info.get("folder") == str(directory)
        cycle = (
            info.get("cycle") if same and isinstance(info.get("cycle"), list) else []
        )
        cycle = [name for name in cycle if name in names]
        if not cycle:
            cycle = random.sample(names, len(names))
            if len(cycle) > 1 and cycle[0] == info.get("id"):
                cycle.append(cycle.pop(0))  # Not the one just shown, again.
        for name in list(cycle):
            cycle.remove(name)
            image = read_photo(Path(directory) / name)
            if image is not None:
                break
        else:
            return False
        first = self.current() is None
        self._store(image, style)
        self._write_info(
            {
                "id": name,
                "author": "",
                "page": "",
                "fetched": time.time(),
                "color": theme_color(image),
                "source": source,
                "style": style,
                "folder": str(directory),
                "cycle": cycle,
            }
        )
        # This can run while a New Tab page renders: tell the browser after.
        QTimer.singleShot(0, lambda: self._tell(first))
        return True

    def _tell(self, first):
        from PyQt6 import sip

        if not sip.isdeleted(self):
            self.changed.emit(first)

    def _store(self, image, style):
        from PyQt6.QtGui import QImage

        if style == "grayscale":
            image = image.convertToFormat(QImage.Format.Format_Grayscale8)
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.folder / "photo.tmp.jpg"
        if not image.save(str(temporary), "JPEG", 88):
            raise OSError("The photo could not be saved")
        temporary.replace(self.image_path)

    def add_photos(self, paths):
        """Copy photos into "Your photos" (resized, as JPEG). Returns how many."""
        import hashlib

        added = 0
        self.mine_folder.mkdir(parents=True, exist_ok=True)
        for path in paths:
            image = read_photo(path)
            if image is None:
                continue
            name = hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:16]
            if image.save(str(self.mine_folder / f"{name}.jpg"), "JPEG", 90):
                added += 1
        return added

    def save_favourite(self):
        """Keep the photo shown now in Favourites. Returns its file, or None."""
        import shutil

        found = self.current()
        if found is None:
            return None
        self.favourites_folder.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in found["id"] if c.isalnum() or c in "-_")[:40]
        target = self.favourites_folder / f"{found['source']}-{safe or 'photo'}.jpg"
        if found["source"] != "favourites":
            shutil.copyfile(found["path"], target)
        info = self._info()
        info["favourite"] = True
        self._write_info(info)
        return target

    def blocked(self):
        try:
            data = json.loads(self.blocked_path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return (
            [str(item) for item in data][:MAX_FILES] if isinstance(data, list) else []
        )

    def block_current(self):
        """Never show the current online photo again (True if it was one)."""
        found = self.current()
        if found is None or found["source"] != "online" or not found["id"]:
            return False
        blocked = [item for item in self.blocked() if item != found["id"]]
        blocked = (blocked + [found["id"]])[-MAX_FILES:]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.blocked_path.write_text(json.dumps(blocked), "utf-8")
        return True


def read_photo(path):
    """A local photo as a QImage no larger than 2560x1440, or None when it is
    not a readable image of sensible size."""
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QImageReader

    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size > MAX_LOCAL_BYTES:
            return None
    except OSError:
        return None
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isEmpty() or size.width() * size.height() > MAX_LOCAL_PIXELS:
        return None
    if size.width() > 2560 or size.height() > 1440:
        reader.setScaledSize(
            size.scaled(QSize(2560, 1440), Qt.AspectRatioMode.KeepAspectRatio)
        )
    image = reader.read()
    return None if image.isNull() else image
