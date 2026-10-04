"""Manual editor for a small number of native quick-action shelves."""

from copy import deepcopy
from uuid import uuid4

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from layout_components import (
    DEFAULT_PANEL_ACTIONS,
    MAX_PANEL_ACTIONS,
    MAX_PANELS,
    PANEL_ACTIONS,
    PANEL_EMPHASES,
    PANEL_PLACEMENTS,
    clean_panels,
)
from layout_preferences import clean_name


class PanelEditor(QGroupBox):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Quick-action panels", parent)
        self._panels = []
        self._updating = False
        body = QVBoxLayout(self)
        row = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self._select)
        row.addWidget(self.selector, 1)
        self.add_button = QPushButton("Add panel")
        self.add_button.clicked.connect(self.add_panel)
        row.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_panel)
        row.addWidget(self.remove_button)
        body.addLayout(row)
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setMaxLength(48)
        self.title.textEdited.connect(self._edit)
        form.addRow("Title:", self.title)
        self.placement = QComboBox()
        self.placement.addItems(list(PANEL_PLACEMENTS))
        self.placement.currentIndexChanged.connect(self._edit)
        form.addRow("Placement:", self.placement)
        self.emphasis = QComboBox()
        self.emphasis.addItems(list(PANEL_EMPHASES))
        self.emphasis.currentIndexChanged.connect(self._edit)
        form.addRow("Appearance:", self.emphasis)
        body.addLayout(form)
        self.actions = QListWidget()
        self.actions.itemChanged.connect(self._edit)
        body.addWidget(self.actions)
        moves = QHBoxLayout()
        up = QPushButton("Move action up")
        up.clicked.connect(lambda: self._move_action(-1))
        down = QPushButton("Move action down")
        down.clicked.connect(lambda: self._move_action(1))
        self.move_up, self.move_down = up, down
        moves.addWidget(up)
        moves.addWidget(down)
        body.addLayout(moves)
        self.notice = QLabel(
            "Choose up to eight actions per panel. Buttons run only when you click them."
        )
        self.notice.setWordWrap(True)
        body.addWidget(self.notice)
        self.set_panels([])

    @property
    def panels(self):
        return deepcopy(self._panels)

    def set_panels(self, panels):
        selected = self.selector.currentData()
        self._panels = clean_panels(deepcopy(panels))
        self._updating = True
        self.selector.clear()
        for panel in self._panels:
            self.selector.addItem(panel["title"], panel["id"])
        if not self._panels:
            self.selector.addItem("No panels")
        index = self.selector.findData(selected)
        self.selector.setCurrentIndex(max(0, index))
        self._updating = False
        self._select()

    def _select(self, *_):
        if self._updating:
            return
        self._updating = True
        index = self.selector.currentIndex()
        panel = self._panels[index] if 0 <= index < len(self._panels) else None
        self.title.setText(panel["title"] if panel else "")
        self.placement.setCurrentText(panel["placement"] if panel else "top")
        self.emphasis.setCurrentText(panel["emphasis"] if panel else "neutral")
        self.actions.clear()
        order = (
            panel["actions"] + [n for n in PANEL_ACTIONS if n not in panel["actions"]]
            if panel
            else list(PANEL_ACTIONS)
        )
        for name in order:
            label = PANEL_ACTIONS[name]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if panel and name in panel["actions"]
                else Qt.CheckState.Unchecked
            )
            self.actions.addItem(item)
        for widget in (
            self.title,
            self.placement,
            self.emphasis,
            self.actions,
            self.remove_button,
            self.move_up,
            self.move_down,
        ):
            widget.setEnabled(panel is not None)
        self.add_button.setEnabled(len(self._panels) < MAX_PANELS)
        self._updating = False

    def _edit(self, *_):
        index = self.selector.currentIndex()
        if self._updating or not 0 <= index < len(self._panels):
            return
        panel = self._panels[index]
        actions = [
            self.actions.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.actions.count())
            if self.actions.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not 1 <= len(actions) <= MAX_PANEL_ACTIONS:
            self.notice.setText(
                "Keep between one and eight actions. The selection was restored."
            )
            self._updating = True
            for i in range(self.actions.count()):
                item = self.actions.item(i)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if item.data(Qt.ItemDataRole.UserRole) in panel["actions"]
                    else Qt.CheckState.Unchecked
                )
            self._updating = False
            return
        self.notice.setText("Buttons run only when you click them.")
        panel.update(
            title=clean_name(self.title.text()) or "Quick actions",
            placement=self.placement.currentText(),
            emphasis=self.emphasis.currentText(),
            actions=actions,
        )
        self.selector.setItemText(index, panel["title"])
        self.changed.emit()

    def _move_action(self, offset):
        row = self.actions.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.actions.count():
            return
        item = self.actions.takeItem(row)
        self.actions.insertItem(target, item)
        self.actions.setCurrentRow(target)
        self._edit()

    def add_panel(self):
        if len(self._panels) >= MAX_PANELS:
            return
        panels = self.panels + [
            {
                "kind": "quick_actions",
                "id": uuid4().hex,
                "title": "Quick actions",
                "placement": "top",
                "emphasis": "neutral",
                "actions": list(DEFAULT_PANEL_ACTIONS),
            }
        ]
        self.set_panels(panels)
        self.selector.setCurrentIndex(len(panels) - 1)
        self.changed.emit()

    def remove_panel(self):
        index = self.selector.currentIndex()
        if 0 <= index < len(self._panels):
            panels = self.panels
            del panels[index]
            self.set_panels(panels)
            self.changed.emit()
