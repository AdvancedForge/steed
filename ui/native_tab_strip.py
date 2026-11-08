"""Native Qt tab strip for the lower-memory interface mode.

It keeps TabStrip's records and window-facing API and shows them in a QTabBar,
which needs no renderer process. Tab placement, groups (as colours), pinning,
collapsed groups, reordering, closing and right-click menus are supported;
dragging tabs between windows is not (use the tab menu to move a tab).
"""

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QHBoxLayout, QTabBar, QToolButton, QWidget

from ui.tab_strip import TabStrip, group_palette

SHAPES = {
    "top": QTabBar.Shape.RoundedNorth,
    "bottom": QTabBar.Shape.RoundedSouth,
    "left": QTabBar.Shape.RoundedWest,
    "right": QTabBar.Shape.RoundedEast,
}


class NativeTabStrip(TabStrip):
    def _build_view(self):
        self.surface = None
        self.icons = {}
        self._ids = []
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.bar = QTabBar(holder)
        self.bar.setAccessibleName("Tabs")
        self.bar.setDocumentMode(True)
        self.bar.setExpanding(False)
        self.bar.setMovable(True)
        self.bar.setTabsClosable(True)
        self.bar.setUsesScrollButtons(True)
        self.bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bar.currentChanged.connect(self._selected)
        self.bar.tabCloseRequested.connect(self.tab_close_requested.emit)
        self.bar.tabMoved.connect(self._moved)
        self.bar.customContextMenuRequested.connect(self._context)
        # Hovering a tab for a moment shows its preview.
        self.bar.setMouseTracking(True)
        self.bar.installEventFilter(self)
        self._hover_index = -1
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._hover_shown)
        self.bar.tabBarDoubleClicked.connect(
            lambda index: self.new_tab_requested.emit() if index < 0 else None
        )
        self.new_button = QToolButton(holder)
        self.new_button.setText("+")
        self.new_button.setToolTip("New tab")
        self.new_button.setAccessibleName("New tab")
        self.new_button.setAutoRaise(True)
        self.new_button.clicked.connect(self.new_tab_requested.emit)
        row.addWidget(self.bar, 1)
        row.addWidget(self.new_button)
        self.holder = holder
        return holder

    def eventFilter(self, watched, event):
        if watched is self.bar:
            kind = event.type()
            if kind == QEvent.Type.MouseMove:
                index = self.bar.tabAt(event.position().toPoint())
                if index != self._hover_index:
                    self._hover_index = index
                    self.hover_ended.emit()
                    if index >= 0:
                        self._hover_timer.start()
                    else:
                        self._hover_timer.stop()
            elif kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
                self._hover_index = -1
                self._hover_timer.stop()
                self.hover_ended.emit()
        return super().eventFilter(watched, event)

    def _hover_shown(self):
        index = self._hover_index
        if 0 <= index < self.bar.count():
            rect = self.bar.tabRect(index)
            self.tab_hovered.emit(index, self.bar.mapToGlobal(rect.bottomLeft()))

    def set_tab_icon(self, index, icon):
        # Keep the QIcon; the HTML strip's PNG encoding is not needed here.
        if 0 <= index < len(self.records):
            record = self.records[index]
            self.icons[record["id"]] = QIcon(icon) if icon is not None else QIcon()
            self._update(index, icon=str(id(self.icons[record["id"]])))

    def dispose(self):
        self._timer.stop()

    def _point(self, command):
        return self.bar.mapToGlobal(QPoint(0, self.bar.height()))

    def _publish(self):
        bar = self.bar
        bar.blockSignals(True)
        try:
            bar.setShape(SHAPES[self.appearance["position"]])
            row = self.holder.layout()
            row.setDirection(
                QHBoxLayout.Direction.TopToBottom
                if self.vertical
                else QHBoxLayout.Direction.LeftToRight
            )
            ids = [record["id"] for record in self.records]
            if ids != self._ids:
                while bar.count():
                    bar.removeTab(0)
                for record in self.records:
                    bar.addTab(record["title"])
                self._ids = ids
            groups = group_palette(self.records, self.group_colors)
            live = {record["id"] for record in self.records}
            self.icons = {k: v for k, v in self.icons.items() if k in live}
            for index, record in enumerate(self.records):
                title = record["title"] or "New Tab"
                if record["loading"]:
                    title = "⟳ " + title
                if record["muted"]:
                    title += " 🔇"
                elif record["audible"]:
                    title += " 🔊"
                text = "" if record["pinned"] else title
                if bar.tabText(index) != text:
                    bar.setTabText(index, text)
                bar.setTabToolTip(
                    index,
                    title + (f" · {record['group']}" if record["group"] else ""),
                )
                bar.setTabIcon(index, self.icons.get(record["id"], QIcon()))
                color = groups.get(record["group"], "")
                dormant = record["lifecycle"] != "active"
                if dormant and not color:
                    color = "#8a94a6"  # Dimmed while frozen or unloaded.
                bar.setTabTextColor(index, QColor(color) if color else QColor())
                if dormant:
                    bar.setTabToolTip(
                        index,
                        bar.tabToolTip(index)
                        + (
                            " · unloaded, reloads when selected"
                            if record["lifecycle"] == "unloaded"
                            else " · frozen, resumes when selected"
                        ),
                    )
                visible = record["visible"] and record["group"] not in self.collapsed
                bar.setTabVisible(index, visible or index == self.current)
                button = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
                if button is not None:
                    button.setVisible(not record["pinned"])
            if 0 <= self.current < bar.count():
                bar.setCurrentIndex(self.current)
        finally:
            bar.blockSignals(False)

    def _selected(self, index):
        if 0 <= index < len(self.records) and index != self.current:
            self.current = index
            self.tab_changed.emit(index)

    def _moved(self, source, target):
        # QTabBar already moved the tab; mirror it in the records.
        record = self.records.pop(source)
        self.records.insert(target, record)
        self._ids = [r["id"] for r in self.records]
        if self.current == source:
            self.current = target
        elif source < self.current <= target:
            self.current -= 1
        elif target <= self.current < source:
            self.current += 1
        self._sync_groups()
        self.tab_moved.emit(source, target)

    def _context(self, position):
        index = self.bar.tabAt(position)
        if index >= 0:
            self.context_requested.emit(index, self.bar.mapToGlobal(position))
