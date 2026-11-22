"""General → Clear Browsing Data…: what to clear, from when; and on exit."""

import time

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog

RANGES = {
    "Last hour": 3600,
    "Last 24 hours": 24 * 3600,
    "Last 7 days": 7 * 24 * 3600,
    "Last 4 weeks": 28 * 24 * 3600,
    "All time": 0,
}
DEFAULT_KINDS = ("history", "cookies", "cache")


class ClearDataDialog(HtmlSettingsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Clear Browsing Data")
        self.resize(480, 440)
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"For the {window.profile_manager.current_profile_name} profile. Cookies "
            "and site data cover the sites you visited in the time range (for All "
            "time, every site with data); clearing them signs you out of those "
            "sites. The cache is cleared whole, whatever the range."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.range = QComboBox()
        self.range.addItems(RANGES)
        self.range.setCurrentText("Last hour")
        form.addRow("Time range", self.range)
        layout.addLayout(form)
        on_exit = set(window.config.get("clear_on_exit", []) or [])
        self.boxes = {}
        for kind, label in window.CLEAR_KINDS.items():
            box = QCheckBox(label)
            box.setChecked(kind in (on_exit or DEFAULT_KINDS))
            layout.addWidget(box)
            self.boxes[kind] = box
        self.exit = QCheckBox("Clear all of these every time this profile closes")
        self.exit.setChecked(bool(on_exit))
        layout.addWidget(self.exit)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox()
        self.clear_button = buttons.addButton(
            "Clear Data", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.clear)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def kinds(self):
        return [kind for kind, box in self.boxes.items() if box.isChecked()]

    def since(self):
        seconds = RANGES[self.range.currentText()]
        return time.time() - seconds if seconds else 0

    def clear(self):
        kinds = self.kinds()
        self.window.config.set("clear_on_exit", kinds if self.exit.isChecked() else [])
        if not kinds:
            self.status.setText("Choose something to clear.")
            return
        self.clear_button.setEnabled(False)
        self.status.setText("Clearing…")
        self.window.clear_browsing_data(self.since(), kinds, self._done)

    def _done(self, summary):
        self.window.status_bar.showMessage(summary, 8000)
        self.status.setText(summary)
        self.accept()
