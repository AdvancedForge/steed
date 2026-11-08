"""A crashed tab's page: what happened, and a way back."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

import crash_log


class CrashPanel(QFrame):
    """Laid over a tab whose page process ended, until it loads again."""

    def __init__(self, window, tab, reason):
        super().__init__(tab)
        self.setObjectName("CrashPanel")
        self.setAutoFillBackground(True)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        title = QLabel("This tab crashed")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        text = QLabel(
            crash_log.REASONS.get(reason, crash_log.REASONS["abnormal"])
            + "\nReloading usually brings it back. Anything you typed on the page"
            " may be lost."
        )
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setWordWrap(True)
        layout.addWidget(text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.reload = QPushButton("Reload")
        self.reload.setDefault(True)
        self.reload.clicked.connect(tab.reload)
        row.addWidget(self.reload)
        log = QPushButton("Crash Log…")
        log.clicked.connect(window._open_crash_log)
        row.addWidget(log)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(2)
        self.setGeometry(tab.rect())


class CrashLogDialog(QDialog):
    def __init__(self, config_dir, parent=None):
        super().__init__(parent)
        self.config_dir = config_dir
        self.setWindowTitle("Crash Log")
        self.resize(640, 380)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Tabs whose page crashed: when, which site and why. Steed keeps the "
            "last 200, with no page content."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        clear = buttons.addButton("Clear", QDialogButtonBox.ButtonRole.ResetRole)
        clear.clicked.connect(self.clear)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        found = crash_log.entries(self.config_dir)
        for entry in reversed(found):
            self.list.addItem(crash_log.describe(entry))
        if not found:
            self.list.addItem("No crashes recorded.")

    def clear(self):
        crash_log.clear(self.config_dir)
        self.refresh()
