"""Manual browser layout settings dialog.

The dialog edits a copy of the saved layout and reports every change through
`layout_changed`, so the window can preview an edit immediately. Canceling
restores the saved layout; saving writes it to the shared configuration.

Only presentation is editable here. Nothing in this dialog changes browser,
profile, folder, file or account authority.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from browser.layout_components import TEXT_SIZES
from browser.layout_preferences import (
    DENSITIES,
    SIDEBAR_SIDES,
    SIDEBAR_WIDTH_RANGE,
    LayoutPreferences,
)
from ui.html_dialog import HtmlSettingsDialog
from ui.layout_panel_editor import PanelEditor
from ui.prompts import InputDialog as QInputDialog
from ui.prompts import MessageBox as QMessageBox

TOOLBAR_LABELS = {
    "back": "Back",
    "forward": "Forward",
    "reload": "Reload",
    "home": "Home",
    "zoom": "Reset zoom",
    "passwords": "Passwords",
    "address": "Address and search (always visible)",
    "menu": "Browser menu (always visible)",
}

MENU_LABELS = {
    "general": "General",
    "profile": "Profile",
    "tabs": "Tabs",
    "view": "View",
    "cookies": "History",
    "agents": "Agents",
}

DENSITY_LABELS = {"compact": "Compact", "comfortable": "Comfortable"}
SIDE_LABELS = {"left": "Left", "right": "Right"}


def _move(list_widget: QListWidget, offset: int) -> None:
    """Swap the selected row with its neighbour and keep it selected."""
    row = list_widget.currentRow()
    target = row + offset
    if row < 0 or not 0 <= target < list_widget.count():
        return
    item = list_widget.takeItem(row)
    list_widget.insertItem(target, item)
    list_widget.setCurrentRow(target)


def _reorder_rows(list_widget: QListWidget) -> list[str]:
    """Return the identifiers in the list's current order."""
    return [
        list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(list_widget.count())
    ]


def _fill_ordered(list_widget: QListWidget, order, labels: dict) -> None:
    """Populate a list with the stored order, using stable identifiers."""
    list_widget.clear()
    for name in order:
        item = QListWidgetItem(labels.get(name, name))
        item.setData(Qt.ItemDataRole.UserRole, name)
        list_widget.addItem(item)


class LayoutDialog(HtmlSettingsDialog):
    """Edit toolbar, menu, sidebar and density settings by hand."""

    layout_changed = pyqtSignal(object)

    def __init__(self, preferences: LayoutPreferences, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browser layout")
        self.setMinimumWidth(520)
        self.resize(640, 640)
        self._saved = preferences.copy()
        self._working = preferences.copy()
        self._updating = False

        layout = QVBoxLayout(self)
        notice = QLabel("Changes show right away. Save keeps them; Cancel goes back.")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        controls = QWidget()
        body = QVBoxLayout(controls)
        moved = QLabel(
            "Tab position and style, control spacing and browser text are also on "
            "the steed:style page, with the rest of Steed's styling."
        )
        moved.setWordWrap(True)
        body.addWidget(moved)
        body.addWidget(self._build_toolbar_group())
        body.addWidget(self._build_menu_group())
        body.addWidget(self._build_sidebar_group())
        body.addWidget(self._build_named_group())
        self.panel_editor = PanelEditor()
        self.panel_editor.changed.connect(self._on_density_changed)
        body.addWidget(self.panel_editor)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        layout.addWidget(scroll)
        self._reload_controls()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        reset = QPushButton("Reset Layout")
        reset.setToolTip("Restore today's toolbar, menus, sidebar and spacing")
        reset.clicked.connect(self.reset_layout)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        layout.addWidget(buttons)

    # ---------- Construction ----------

    def _build_toolbar_group(self) -> QGroupBox:
        group = QGroupBox("Address bar buttons")
        body = QVBoxLayout(group)
        self.toolbar_list = QListWidget()
        self.toolbar_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.toolbar_list.itemChanged.connect(self._on_toolbar_changed)
        self.toolbar_list.currentRowChanged.connect(self._on_row_changed)
        body.addWidget(self.toolbar_list)

        controls = QHBoxLayout()
        up = QPushButton("Move Up")
        up.clicked.connect(lambda: self._move_row(self.toolbar_list, -1))
        down = QPushButton("Move Down")
        down.clicked.connect(lambda: self._move_row(self.toolbar_list, 1))
        controls.addWidget(up)
        controls.addWidget(down)
        controls.addStretch(1)
        body.addLayout(controls)
        return group

    def _build_menu_group(self) -> QGroupBox:
        group = QGroupBox("Menu order")
        body = QVBoxLayout(group)
        self.menu_list = QListWidget()
        self.menu_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.menu_list.currentRowChanged.connect(self._on_row_changed)
        body.addWidget(self.menu_list)

        controls = QHBoxLayout()
        up = QPushButton("Move Up")
        up.clicked.connect(lambda: self._move_row(self.menu_list, -1))
        down = QPushButton("Move Down")
        down.clicked.connect(lambda: self._move_row(self.menu_list, 1))
        controls.addWidget(up)
        controls.addWidget(down)
        controls.addStretch(1)
        body.addLayout(controls)
        return group

    def _build_sidebar_group(self) -> QGroupBox:
        group = QGroupBox("Agent sidebar and density")
        form = QFormLayout(group)

        self.sidebar_side = QComboBox()
        for side in SIDEBAR_SIDES:
            self.sidebar_side.addItem(SIDE_LABELS[side], side)
        self.sidebar_side.setCurrentIndex(
            SIDEBAR_SIDES.index(self._working.sidebar_side)
        )
        self.sidebar_side.currentIndexChanged.connect(self._on_sidebar_changed)
        form.addRow("Sidebar side", self.sidebar_side)

        self.sidebar_width = QSpinBox()
        low, high = SIDEBAR_WIDTH_RANGE
        self.sidebar_width.setRange(low, high)
        self.sidebar_width.setValue(self._working.sidebar_width)
        self.sidebar_width.setSuffix(" px")
        self.sidebar_width.valueChanged.connect(self._on_sidebar_changed)
        form.addRow("Sidebar width", self.sidebar_width)

        self.density = QComboBox()
        for name in DENSITIES:
            self.density.addItem(DENSITY_LABELS[name], name)
        self.density.setCurrentIndex(DENSITIES.index(self._working.density))
        self.density.currentIndexChanged.connect(self._on_density_changed)
        form.addRow("Control spacing", self.density)
        self.text_size = QComboBox()
        for value in TEXT_SIZES:
            self.text_size.addItem(value.title(), value)
        self.text_size.currentIndexChanged.connect(self._on_density_changed)
        form.addRow("Browser text", self.text_size)
        return group

    def _build_named_group(self) -> QGroupBox:
        group = QGroupBox("Named layouts")
        body = QHBoxLayout(group)
        self.named_layouts = QComboBox()
        body.addWidget(self.named_layouts, 1)

        save = QPushButton("Save As…")
        save.setToolTip("Store the current settings under a new name")
        save.clicked.connect(self.save_named_layout)
        body.addWidget(save)

        apply_button = QPushButton("Apply Saved")
        apply_button.setToolTip("Use a saved layout")
        apply_button.clicked.connect(self.apply_named_layout)
        body.addWidget(apply_button)

        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_named_layout)
        body.addWidget(delete)
        self._refresh_named_layouts()
        return group

    # ---------- State ----------

    @property
    def preferences(self) -> LayoutPreferences:
        """The edited layout, saved on Save and discarded on Cancel."""
        return self._working

    def _refresh_named_layouts(self, select: str | None = None) -> None:
        self.named_layouts.blockSignals(True)
        self.named_layouts.clear()
        for name in self._working.named_layouts:
            self.named_layouts.addItem(name)
        if self.named_layouts.count() == 0:
            self.named_layouts.addItem("No saved layouts")
            self.named_layouts.setEnabled(False)
        else:
            self.named_layouts.setEnabled(True)
            index = (
                self.named_layouts.findText(select)
                if select is not None
                else self.named_layouts.findText(self._working.active_layout or "")
            )
            self.named_layouts.setCurrentIndex(max(0, index))
        self.named_layouts.blockSignals(False)

    def _read_controls(self) -> None:
        """Copy the visible controls into the working layout."""
        hidden = [
            self.toolbar_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.toolbar_list.count())
            if self.toolbar_list.item(row).checkState() == Qt.CheckState.Unchecked
            and self.toolbar_list.item(row).data(Qt.ItemDataRole.UserRole)
            not in ("address", "menu")
        ]
        self._working.set_toolbar(_reorder_rows(self.toolbar_list), hidden)
        self._working.set_menu_order(_reorder_rows(self.menu_list))
        self._working.set_sidebar(
            self.sidebar_side.currentData(), self.sidebar_width.value()
        )
        self._working.set_density(self.density.currentData())
        self._working.set_text_size(self.text_size.currentData())
        self._working.set_panels(self.panel_editor.panels)

    def _publish(self) -> None:
        """Report the edited layout so the window can preview it."""
        self._read_controls()
        self.layout_changed.emit(self._working.copy())

    def _move_row(self, list_widget: QListWidget, offset: int) -> None:
        self._updating = True
        _move(list_widget, offset)
        self._updating = False
        self._publish()

    def _on_row_changed(self, _row: int) -> None:
        if not self._updating:
            self._publish()

    def _on_toolbar_changed(self, _item: QListWidgetItem) -> None:
        if not self._updating:
            self._publish()

    def _on_sidebar_changed(self, _index: int = 0) -> None:
        if not self._updating:
            self._publish()

    def _on_density_changed(self, _index: int = 0) -> None:
        if not self._updating:
            self._publish()

    def _reload_controls(self) -> None:
        """Show the working layout in the controls without reporting it."""
        self._updating = True
        hidden = self._working.hidden_toolbar
        _fill_ordered(self.toolbar_list, self._working.toolbar_order, TOOLBAR_LABELS)
        for row in range(self.toolbar_list.count()):
            item = self.toolbar_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) in ("address", "menu"):
                continue
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.data(Qt.ItemDataRole.UserRole) in hidden
                else Qt.CheckState.Checked
            )
        _fill_ordered(self.menu_list, self._working.menu_order, MENU_LABELS)
        self.sidebar_side.setCurrentIndex(
            SIDEBAR_SIDES.index(self._working.sidebar_side)
        )
        self.sidebar_width.setValue(self._working.sidebar_width)
        self.density.setCurrentIndex(DENSITIES.index(self._working.density))
        self.text_size.setCurrentIndex(TEXT_SIZES.index(self._working.text_size))
        self.panel_editor.set_panels(self._working.panels)
        self._updating = False

    # ---------- Commands ----------

    def reset_layout(self) -> None:
        """Restore today's appearance in the dialog, still unsaved."""
        self._working.reset()
        self._reload_controls()
        self._publish()

    def save_named_layout(self) -> None:
        """Store the edited settings under a name chosen by the user."""
        self._read_controls()
        name, accepted = QInputDialog.getText(
            self, "Save Layout", "Layout name:", text=self._working.active_layout or ""
        )
        if not accepted:
            return
        if not self._working.can_save(name):
            QMessageBox.warning(
                self,
                "Save Layout",
                "That name cannot be used. Choose a different name, or delete "
                "a saved layout first.",
            )
            return
        self._working.save_named(name)
        self._refresh_named_layouts(self._working.active_layout)
        self._reload_controls()
        self._publish()

    def apply_named_layout(self) -> None:
        """Adopt a saved layout in the dialog."""
        if not self._working.apply_named(self.named_layouts.currentText()):
            return
        self._reload_controls()
        self._publish()

    def delete_named_layout(self) -> None:
        """Forget a saved layout, leaving the current appearance alone."""
        name = self.named_layouts.currentText()
        if self._working.delete_named(name):
            self._refresh_named_layouts()
            self._publish()
