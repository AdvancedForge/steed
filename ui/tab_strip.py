"""Customizable HTML tab strip with the same index-based API the window uses."""

import base64
from urllib.parse import urlsplit
from uuid import uuid4

from PyQt6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QPoint,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from tab_appearance import TAB_WIDTH_RANGE, default_tabs
from ui.html_surface import HtmlSurface

GROUP_COLORS = ("#4f7cff", "#9b5de5", "#1b998b", "#f08a24", "#e0457b", "#5c6f84")
# Strip thickness for one row of horizontal tabs, by tab size and density.
ROW_HEIGHTS = {"compact": 30, "standard": 36, "wide": 42}
MAX_TITLE = 300


def ordered_colors(names):
    """Distinct colours in order of first appearance, cycling only when needed."""
    colors = {}
    for name in names:
        if name and name not in colors:
            colors[name] = GROUP_COLORS[len(colors) % len(GROUP_COLORS)]
    return colors


def group_palette(records, chosen):
    """Each group's colour: its own choice, or the next automatic colour."""
    automatic = ordered_colors(
        r["group"] for r in records if r["group"] and r["group"] not in chosen
    )
    return {**automatic, **chosen}


def site_of(url):
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def icon_data(icon, size=32):
    """A small PNG data URL, or "" for an empty icon."""
    if icon is None or icon.isNull():
        return ""
    pixmap = icon.pixmap(QSize(size, size))
    if pixmap.isNull():
        return ""
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    if raw.size() > 64 * 1024:
        return ""
    return "data:image/png;base64," + base64.b64encode(bytes(raw)).decode()


class ResizeGrip(QWidget):
    """Drag the inner edge of side tabs to change their width."""

    def __init__(self, strip):
        super().__init__(strip)
        self.strip = strip
        self.start = None
        self.setFixedWidth(5)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag to resize tabs")
        self.setAccessibleName("Resize side tabs")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = (event.globalPosition().x(), self.strip.width())

    def mouseMoveEvent(self, event):
        if self.start is not None:
            x, width = self.start
            delta = event.globalPosition().x() - x
            if self.strip.appearance["position"] == "right":
                delta = -delta
            low, high = TAB_WIDTH_RANGE
            self.strip.setFixedWidth(int(max(low, min(high, width + delta))))

    def mouseReleaseEvent(self, event):
        if self.start is not None:
            self.start = None
            self.strip.width_chosen.emit(self.strip.width())


class TabStrip(QWidget):
    """Owns presentation only; the window still owns tabs and their order."""

    tab_close_requested = pyqtSignal(int)
    tab_changed = pyqtSignal(int)
    new_tab_requested = pyqtSignal()
    tab_moved = pyqtSignal(int, int)
    group_rename_requested = pyqtSignal(int)
    group_set_requested = pyqtSignal(int, str)
    group_clear_requested = pyqtSignal(int)
    group_toggle_requested = pyqtSignal(str)
    context_requested = pyqtSignal(int, QPoint)
    # A tab hovered for a moment (index, where to show a preview), or none.
    tab_hovered = pyqtSignal(int, QPoint)
    hover_ended = pyqtSignal()
    group_context_requested = pyqtSignal(str, QPoint)
    mute_toggled = pyqtSignal(int)
    width_chosen = pyqtSignal(int)
    drag_started = pyqtSignal(str, int)
    tab_received = pyqtSignal(str, int)
    detach_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.density = "comfortable"
        self.appearance = default_tabs()
        self.records = []
        self.current = -1
        self.groups = {}
        self.available_groups = []
        # Group name -> chosen colour; other groups take automatic colours.
        self.group_colors = {}
        self.collapsed = set()
        self.content_height = 0
        self.chrome = {"toolbar": "", "dark": False}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.grip = ResizeGrip(self)
        layout.addWidget(self._build_view())
        layout.addWidget(self.grip)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._publish)
        self._resize()

    # ---------- Window-facing API (matches the former QTabBar wrapper) ----------

    def count(self) -> int:
        return len(self.records)

    def insert_tab(self, index: int, title: str = "New Tab") -> int:
        index = max(0, min(index, len(self.records)))
        self.records.insert(
            index,
            {
                "id": uuid4().hex,
                "title": str(title)[:MAX_TITLE],
                "group": None,
                "visible": True,
                "icon": "",
                "loading": False,
                "audible": False,
                "muted": False,
                "site": "",
                "pinned": False,
                # "active", "frozen" or "unloaded" (memory modes).
                "lifecycle": "active",
                "keep_loaded": False,
            },
        )
        if self.current >= index:
            self.current += 1
        if self.current < 0:
            self.current = index
        self._sync_groups()
        self.refresh()
        return index

    def remove_tab(self, index: int) -> None:
        if not 0 <= index < len(self.records):
            return
        self.records.pop(index)
        if self.current > index or self.current >= len(self.records):
            self.current -= 1
        self._sync_groups()
        self.refresh()

    def clear_all_tabs(self) -> None:
        self.records.clear()
        self.current = -1
        self.groups.clear()
        self.refresh()

    def tab_text(self, index: int) -> str:
        if 0 <= index < len(self.records):
            return self.records[index]["title"]
        return ""

    def set_tab_title(self, index: int, title: str) -> None:
        self._update(index, title=str(title)[:MAX_TITLE])

    def set_tab_icon(self, index: int, icon) -> None:
        self._update(index, icon=icon_data(icon))

    def set_tab_state(self, index: int, **values) -> None:
        allowed = {"loading", "audible", "muted", "pinned", "keep_loaded"}
        update = {k: bool(v) for k, v in values.items() if k in allowed}
        if values.get("lifecycle") in ("active", "frozen", "unloaded"):
            update["lifecycle"] = values["lifecycle"]
        if "url" in values:
            update["site"] = site_of(values["url"])
        self._update(index, **update)

    def set_current_tab(self, index: int) -> None:
        if 0 <= index < len(self.records) and index != self.current:
            self.current = index
            self.refresh()

    def get_current_index(self) -> int:
        return self.current

    def set_tab_group(self, index: int, group_name: str | None) -> None:
        self._update(index, group=group_name or None)
        self._sync_groups()

    def set_tab_visible(self, index: int, visible: bool) -> None:
        self._update(index, visible=bool(visible))

    def is_tab_visible(self, index: int) -> bool:
        return 0 <= index < len(self.records) and self.records[index]["visible"]

    def set_group_colors(self, colors) -> None:
        colors = dict(colors)
        if colors != self.group_colors:
            self.group_colors = colors
            self.refresh()

    def set_collapsed_groups(self, names) -> None:
        names = set(names)
        if names != self.collapsed:
            self.collapsed = names
            self.refresh()

    def move_tab(self, from_index: int, to_index: int) -> None:
        count = len(self.records)
        if not (0 <= from_index < count and 0 <= to_index < count):
            return
        if from_index == to_index:
            return
        record = self.records.pop(from_index)
        self.records.insert(to_index, record)
        if self.current == from_index:
            self.current = to_index
        elif from_index < self.current <= to_index:
            self.current -= 1
        elif to_index <= self.current < from_index:
            self.current += 1
        self._sync_groups()
        self.refresh()
        self.tab_moved.emit(from_index, to_index)

    def apply_density(self, density: str) -> None:
        self.density = density
        self._resize()
        self.refresh()

    def apply_appearance(self, appearance: dict) -> None:
        self.appearance = dict(appearance)
        self.content_height = 0
        self._resize()
        self.refresh()

    def set_chrome(self, toolbar: str, dark: bool) -> None:
        """Match the native toolbar so the active tab joins it."""
        chrome = {"toolbar": toolbar, "dark": bool(dark)}
        if chrome != self.chrome:
            self.chrome = chrome
            self.refresh()

    @property
    def vertical(self) -> bool:
        return self.appearance["position"] in ("left", "right")

    def refresh(self) -> None:
        """Coalesce bursts of tab changes into one render."""
        if not self._timer.isActive():
            self._timer.start()

    def dispose(self) -> None:
        self._timer.stop()
        self.surface.dispose()

    # ---------- Internals ----------

    def _build_view(self):
        self.surface = HtmlSurface("tabs", self, label="tab strip", command_limit=2000)
        # A loading notice cannot fit in a single tab row; keep the space quiet.
        self.surface.notice.hide()
        self.surface.command.connect(self._command)
        return self.surface

    def _update(self, index, **values):
        if 0 <= index < len(self.records):
            record = self.records[index]
            changed = {k: v for k, v in values.items() if record.get(k) != v}
            if changed:
                record.update(changed)
                self.refresh()

    def _sync_groups(self):
        self.groups = {i: r["group"] for i, r in enumerate(self.records)}

    def _row_height(self):
        height = ROW_HEIGHTS[self.appearance["size"]]
        return height - 4 if self.density == "compact" else height

    def _resize(self):
        layout = self.layout()
        if layout is not None:
            # The grip sits on the edge facing the page.
            layout.removeWidget(self.grip)
            layout.insertWidget(
                0 if self.appearance["position"] == "right" else 1, self.grip
            )
            self.grip.setVisible(self.vertical)
        if self.vertical:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.setFixedWidth(self.appearance["vertical_width"])
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            base = self._row_height() + 10
            rows = self.content_height if self.appearance["overflow"] == "wrap" else 0
            # Wrapped rows grow the strip up to four rows, then scroll.
            self.setFixedHeight(max(base, min(rows, base * 4)))

    def _publish(self):
        groups = group_palette(self.records, self.group_colors)
        sites = ordered_colors(r["site"] for r in self.records)
        tabs = []
        for index, record in enumerate(self.records):
            tabs.append(
                {
                    **record,
                    "index": index,
                    "active": index == self.current,
                    "group_color": groups.get(record["group"], ""),
                }
            )
        self.surface.publish(
            {
                "tabs": tabs,
                "appearance": self.appearance,
                "density": self.density,
                "row_height": self._row_height(),
                "collapsed": sorted(self.collapsed),
                "site_colors": sites,
                "chrome": self.chrome,
                "content_height": self.content_height,
            }
        )

    def _index(self, tab_id):
        return next(
            (i for i, record in enumerate(self.records) if record["id"] == tab_id), -1
        )

    def _point(self, command):
        x, y = command.get("x"), command.get("y")
        if not all(isinstance(v, (int, float)) for v in (x, y)):
            return self.mapToGlobal(QPoint(0, self.height()))
        return self.surface.view.mapToGlobal(QPoint(int(x), int(y)))

    @staticmethod
    def _token(command):
        token = command.get("token")
        return (
            isinstance(token, str)
            and len(token) == 32
            and all(c in "0123456789abcdef" for c in token)
        )

    def _command(self, command):
        action = command.get("action")
        tab_id = command.get("id")
        index = self._index(tab_id) if isinstance(tab_id, str) else -1
        if action == "select" and index >= 0:
            if index != self.current:
                self.current = index
                self.refresh()
                self.tab_changed.emit(index)
        elif action == "close" and index >= 0:
            self.tab_close_requested.emit(index)
        elif action == "new":
            self.new_tab_requested.emit()
        elif action == "move" and index >= 0:
            target = command.get("to")
            if type(target) is int and 0 <= target < len(self.records):
                # Pinned and ordinary tabs each stay within their own block.
                pinned = sum(1 for record in self.records if record["pinned"])
                if self.records[index]["pinned"]:
                    target = min(target, pinned - 1)
                else:
                    target = max(target, pinned)
                self.move_tab(index, target)
        elif action == "context" and index >= 0:
            self.context_requested.emit(index, self._point(command))
        elif action == "hover" and index >= 0:
            self.tab_hovered.emit(index, self._point(command))
        elif action == "unhover":
            self.hover_ended.emit()
        elif action == "drag_start" and index >= 0 and self._token(command):
            self.drag_started.emit(command["token"], index)
        elif action == "receive" and self._token(command):
            target = command.get("to")
            if type(target) is int and 0 <= target <= len(self.records):
                self.tab_received.emit(command["token"], target)
        elif action == "detach" and index >= 0:
            self.detach_requested.emit(index)
        elif action == "mute" and index >= 0:
            self.mute_toggled.emit(index)
        elif action == "toggle_group" and isinstance(command.get("group"), str):
            self.group_toggle_requested.emit(command["group"])
        elif action == "group_context" and isinstance(command.get("group"), str):
            self.group_context_requested.emit(command["group"], self._point(command))
        elif action == "size" and type(command.get("height")) is int:
            height = max(0, min(command["height"], 400))
            if not self.vertical and height != self.content_height:
                self.content_height = height
                self._resize()
                self.refresh()
