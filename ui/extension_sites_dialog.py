"""Site Access…: an extension on every site it asks for, or only on chosen ones."""

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from extension_build import clean_site
from ui.html_dialog import HtmlSettingsDialog


class ExtensionSitesDialog(HtmlSettingsDialog):
    def __init__(self, entry, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setWindowTitle(f"Site Access: {entry['name']}")
        self.resize(480, 400)
        layout = QVBoxLayout(self)
        self.everywhere = QRadioButton("On every site it asks for")
        self.only = QRadioButton("Only on these sites (and their subdomains):")
        group = QButtonGroup(self)
        for button in (self.everywhere, self.only):
            group.addButton(button)
            layout.addWidget(button)
        self.list = QPlainTextEdit()
        self.list.setPlaceholderText("example.com\nnews.example.org")
        layout.addWidget(self.list, 1)
        add = QPushButton("Add Current Site")
        add.setAutoDefault(False)
        add.clicked.connect(self.add_current)
        layout.addWidget(add)
        note = QLabel(
            "Steed installs the extension again with its access narrowed; its "
            "settings and data stay."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        limited = entry["sites"] is not None
        self.only.setChecked(limited)
        self.everywhere.setChecked(not limited)
        self.list.setPlainText("\n".join(entry["sites"] or []))
        self.only.toggled.connect(self.list.setEnabled)
        self.list.setEnabled(limited)

    def add_current(self):
        window = self.window
        if not (0 <= window.current_tab_index < len(window.tabs)):
            return
        site = clean_site(window.tabs[window.current_tab_index].url().host())
        if site and site not in self.sites_typed():
            self.list.appendPlainText(site)
        self.only.setChecked(True)

    def sites_typed(self):
        return [
            site for site in map(clean_site, self.list.toPlainText().split()) if site
        ]

    def sites(self):
        """The chosen sites, or None for every site it asks for."""
        return self.sites_typed() if self.only.isChecked() else None
