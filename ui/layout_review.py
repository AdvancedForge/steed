"""Native review of a portable layout, with an inert toolbar preview."""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from browser.layout_components import PANEL_ACTIONS
from browser.layout_format import layout_changes
from browser.layout_preferences import preferences_from_config
from ui.address_bar import AddressBar
from ui.html_dialog import HtmlSettingsDialog
from ui.layout_panel import QuickActionsPanel, apply_chrome_font


class LayoutReviewDialog(HtmlSettingsDialog):
    def __init__(
        self, registry, definition, parent=None, *, service=None, proposal=None
    ):
        super().__init__(parent)
        self.registry, self.definition = registry, definition
        self.service, self.proposal = service, proposal
        self.request_key = proposal.request_key if proposal else "imported-layout"
        self.setWindowTitle("Review browser layout")
        if proposal is not None:
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(740, 520)
        body = QVBoxLayout(self)
        title = QLabel("Layout: " + definition.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        body.addWidget(title)
        notice = QLabel(
            "Apply changes browser controls in all windows and keeps your saved "
            "layouts. It grants no website, profile, file or account access."
        )
        notice.setWordWrap(True)
        body.addWidget(notice)
        self.toolbar_preview = AddressBar()
        self.toolbar_preview.address_input.setPlaceholderText(
            "Preview — no page is opened"
        )
        body.addWidget(self.toolbar_preview)
        self.panel_preview = QWidget()
        self.panel_preview_layout = QVBoxLayout(self.panel_preview)
        self.panel_preview_layout.setContentsMargins(0, 0, 0, 0)
        self.inert_actions = {
            name: QAction(label, self) for name, label in PANEL_ACTIONS.items()
        }
        body.addWidget(self.panel_preview)
        self.changes = QPlainTextEdit()
        self.changes.setReadOnly(True)
        body.addWidget(self.changes, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        source_button = QPushButton("Show JSON source")
        source_button.setCheckable(True)
        self.source = QPlainTextEdit(definition.source)
        self.source.setReadOnly(True)
        self.source.hide()
        source_button.toggled.connect(self.source.setVisible)
        body.addWidget(source_button)
        body.addWidget(self.source)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton("Apply layout")
        self.apply_button.clicked.connect(self.apply_layout)
        buttons.addWidget(self.apply_button)
        if proposal is not None:
            deny = QPushButton("Decline")
            deny.clicked.connect(self.decline)
            buttons.addWidget(deny)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        body.addLayout(buttons)
        self.refresh_preview()

    def refresh_preview(self):
        self.base = preferences_from_config(self.registry.config)
        candidate = self.definition.preferences(self.base)
        self.changes.setPlainText("\n\n".join(layout_changes(self.base, candidate)))
        self.toolbar_preview.apply_toolbar(
            candidate.toolbar_order, candidate.hidden_toolbar
        )
        self.toolbar_preview.apply_density(candidate.density)
        apply_chrome_font(self.toolbar_preview, candidate.text_size)
        while self.panel_preview_layout.count():
            item = self.panel_preview_layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        for definition in candidate.panels:
            panel = QuickActionsPanel(
                definition, self.inert_actions, self.panel_preview
            )
            apply_chrome_font(panel, candidate.text_size)
            self.panel_preview_layout.addWidget(panel)
        self.panel_preview.setVisible(bool(candidate.panels))

    def apply_layout(self):
        try:
            current = preferences_from_config(self.registry.config)
            if current.to_dict() != self.base.to_dict():
                self.refresh_preview()
                self.status.setText(
                    "The saved layout changed. Review this updated preview and click Apply again."
                )
                return
            if self.proposal is not None:
                self.service.layouts.resolve(
                    self.proposal.task_id,
                    self.proposal.request_id,
                    approve=True,
                    base=self.base,
                )
            else:
                self.registry.commit_layout(
                    self.definition.preferences(self.base), base=self.base
                )
            self.accept()
        except (ValueError, KeyError, RuntimeError, OSError, sqlite3.Error):
            self.status.setText(
                "The layout could not be applied. Check the current settings and task, then reopen this review."
            )
            self.apply_button.setEnabled(False)

    def decline(self):
        try:
            self.service.layouts.resolve(
                self.proposal.task_id, self.proposal.request_id
            )
            self.accept()
        except (ValueError, KeyError, RuntimeError, OSError, sqlite3.Error):
            self.status.setText("This request is no longer available.")
            self.apply_button.setEnabled(False)
