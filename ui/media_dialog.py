"""One-shot desktop screen/window selection for an engine share request."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog


class DesktopMediaDialog(HtmlSettingsDialog):
    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self._resolved = False
        self.setWindowTitle("Choose what to share")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        layout = QVBoxLayout(self)
        self.screens_model = request.screensModel()
        self.windows_model = request.windowsModel()
        self.screens_list = QListWidget()
        self.windows_list = QListWidget()
        for label, model, items in (
            ("Screens:", self.screens_model, self.screens_list),
            ("Windows:", self.windows_model, self.windows_list),
        ):
            layout.addWidget(QLabel(label))
            for row in range(model.rowCount()):
                items.addItem(str(model.data(model.index(row, 0))))
            layout.addWidget(items)
        buttons = QHBoxLayout()
        self.share_screen = QPushButton("Share selected screen")
        self.share_window = QPushButton("Share selected window")
        cancel = QPushButton("Cancel")
        buttons.addWidget(self.share_screen)
        buttons.addWidget(self.share_window)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.share_screen.setEnabled(self.screens_model.rowCount() > 0)
        self.share_window.setEnabled(self.windows_model.rowCount() > 0)
        self.share_screen.clicked.connect(lambda: self._select(True))
        self.share_window.clicked.connect(lambda: self._select(False))
        cancel.clicked.connect(self.reject)
        self.rejected.connect(self.cancel_pending)

    def _select(self, screen):
        model = self.screens_model if screen else self.windows_model
        items = self.screens_list if screen else self.windows_list
        row = items.currentRow()
        if self._resolved or row < 0:
            return
        index = model.index(row, 0)
        if screen:
            self.request.selectScreen(index)
        else:
            self.request.selectWindow(index)
        self._resolved = True
        self.accept()

    def cancel_pending(self):
        if not self._resolved:
            self._resolved = True
            self.request.cancel()
