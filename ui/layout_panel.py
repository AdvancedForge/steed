"""A bounded native shelf binds only to browser-owned QAction instances."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from browser.layout_components import PANEL_ACTIONS


def chrome_font(text_size):
    """Scale native controls from the system font without compounding previews."""
    font = QFont(QApplication.font())
    if text_size == "large":
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * 1.15)
        elif font.pixelSize() > 0:
            font.setPixelSize(round(font.pixelSize() * 1.15))
    return font


def apply_chrome_font(widget, text_size):
    """Apply native text sizes explicitly, including under Qt stylesheets."""
    target = chrome_font(text_size)
    for child in [widget, *widget.findChildren(QWidget)]:
        font = QFont(child.font())
        if target.pointSizeF() > 0:
            font.setPointSizeF(target.pointSizeF())
        else:
            font.setPixelSize(target.pixelSize())
        child.setFont(font)
    if isinstance(widget, QuickActionsPanel):
        widget.scroll.setFixedHeight(
            widget.scroll.widget().sizeHint().height()
            + widget.scroll.horizontalScrollBar().sizeHint().height()
            + 2
        )


class QuickActionsPanel(QFrame):
    def __init__(self, definition, actions, parent=None):
        super().__init__(parent)
        self.panel_id = definition["id"]
        self.setObjectName("quickActions")
        self.setAccessibleName(definition["title"])
        body = QVBoxLayout(self)
        body.setContentsMargins(6, 4, 6, 4)
        body.setSpacing(2)
        title = QLabel(definition["title"])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        body.addWidget(title)
        row = QWidget(self)
        buttons = QHBoxLayout(row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        buttons.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.buttons = {}
        self.actions = {}
        for name in definition["actions"]:
            if name not in actions or name not in PANEL_ACTIONS:
                continue
            button = QPushButton(PANEL_ACTIONS[name])
            button.setAccessibleName(PANEL_ACTIONS[name])
            button.clicked.connect(actions[name].trigger)
            button.installEventFilter(self)
            actions[name].changed.connect(self.refresh_actions)
            self.buttons[name] = button
            self.actions[name] = actions[name]
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(row)
        self.scroll.setMinimumWidth(0)
        self.scroll.setFixedHeight(
            row.sizeHint().height()
            + self.scroll.horizontalScrollBar().sizeHint().height()
            + 2
        )
        body.addWidget(self.scroll)
        if definition["emphasis"] == "accent":
            self.setStyleSheet(
                "QFrame#quickActions { border-left: 3px solid palette(highlight); }"
            )
        else:
            self.setStyleSheet(
                "QFrame#quickActions { border-bottom: 1px solid palette(mid); }"
            )
        self.refresh_actions()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in self.buttons.values():
            self.scroll.ensureWidgetVisible(watched, 8, 0)
        return super().eventFilter(watched, event)

    def refresh_actions(self):
        for name, action in self.actions.items():
            button = self.buttons[name]
            button.setEnabled(action.isEnabled())
            button.setCheckable(action.isCheckable())
            button.setChecked(action.isChecked())
            shortcut = action.shortcut().toString()
            button.setToolTip(
                PANEL_ACTIONS[name] + (" (" + shortcut + ")" if shortcut else "")
            )
