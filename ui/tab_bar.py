"""Tab bar widget."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QTabBar, QWidget


class TabBar(QWidget):
    """Custom tab bar with add button."""

    tab_close_requested = pyqtSignal(int)
    tab_changed = pyqtSignal(int)
    new_tab_requested = pyqtSignal()
    tab_moved = pyqtSignal(int, int)
    group_rename_requested = pyqtSignal(int)
    group_set_requested = pyqtSignal(int, str)
    group_clear_requested = pyqtSignal(int)
    group_toggle_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tab_bar = QTabBar()
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.tabCloseRequested.connect(self.tab_close_requested.emit)
        self.tab_bar.currentChanged.connect(self.tab_changed.emit)
        self.tab_bar.tabMoved.connect(self._on_internal_tab_moved)
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._show_context_menu)

        self.groups = {}  # index -> group_name
        self.available_groups = []

        self.new_tab_btn = QPushButton("+")
        self.new_tab_btn.setToolTip("New Tab")
        self.new_tab_btn.setMaximumWidth(30)
        self.new_tab_btn.setMaximumHeight(30)
        self.new_tab_btn.clicked.connect(self.new_tab_requested.emit)

        layout.addWidget(self.tab_bar)
        layout.addWidget(self.new_tab_btn)

    def insert_tab(self, index: int, title: str = "New Tab") -> int:
        index = max(0, min(index, self.tab_bar.count()))
        groups = [self.groups.get(i) for i in range(self.tab_bar.count())]
        groups.insert(index, None)
        self.groups = dict(enumerate(groups))
        return self.tab_bar.insertTab(index, title)

    def remove_tab(self, index: int) -> None:
        self.tab_bar.removeTab(index)
        # reindex groups
        new_groups = {}
        for i in range(self.tab_bar.count()):
            new_groups[i] = self.groups.get(i if i < index else i + 1)
        self.groups = new_groups

    def clear_all_tabs(self) -> None:
        """Clear all tabs."""
        for _ in range(self.tab_bar.count()):
            self.tab_bar.removeTab(0)
        self.groups.clear()

    def set_tab_title(self, index: int, title: str) -> None:
        """Set the title of a tab."""
        if 0 <= index < self.tab_bar.count():
            self.tab_bar.setTabText(index, title)

    def set_current_tab(self, index: int) -> None:
        if 0 <= index < self.tab_bar.count():
            was_blocked = self.tab_bar.blockSignals(True)
            self.tab_bar.setCurrentIndex(index)
            self.tab_bar.blockSignals(was_blocked)

    def set_tab_group(self, index: int, group_name: str | None) -> None:
        self.groups[index] = group_name
        current_text = self.tab_bar.tabText(index)
        if group_name:
            # Prefix title with group name in brackets
            if "] " in current_text and current_text.startswith("["):
                current_text = current_text.partition("] ")[2]
            self.tab_bar.setTabText(index, f"[{group_name}] {current_text}")
        else:
            if "] " in current_text and current_text.startswith("["):
                self.tab_bar.setTabText(index, current_text.partition("] ")[2])

    def _show_context_menu(self, position):
        index = self.tab_bar.tabAt(position)
        if index < 0:
            return

        menu = QMenu(self)
        set_new = menu.addAction("Set Group…")

        existing_groups = sorted(
            {g for g in self.groups.values() if g} | set(self.available_groups)
        )
        if existing_groups:
            sub = menu.addMenu("Set to existing")
            for g in existing_groups:
                act = sub.addAction(g)
                act.triggered.connect(
                    lambda checked=False, i=index, name=g: (
                        self.group_set_requested.emit(i, name)
                    )
                )

        clear_group = menu.addAction("Clear Group")
        toggle_group = (
            menu.addAction("Collapse / Expand Group")
            if self.groups.get(index)
            else None
        )
        action = menu.exec(self.tab_bar.mapToGlobal(position))

        if action == set_new:
            self.group_rename_requested.emit(index)
        elif action == clear_group:
            self.group_clear_requested.emit(index)
        elif action == toggle_group and toggle_group is not None:
            self.group_toggle_requested.emit(self.groups[index])

    def get_current_index(self) -> int:
        """Get the current tab index."""
        return self.tab_bar.currentIndex()

    def _on_internal_tab_moved(self, from_idx: int, to_idx: int) -> None:
        # Update groups mapping to match visual order
        if from_idx == to_idx:
            return
        groups = [self.groups.get(i) for i in range(self.tab_bar.count())]
        groups.insert(to_idx, groups.pop(from_idx))
        self.groups = dict(enumerate(groups))
        self.tab_moved.emit(from_idx, to_idx)
