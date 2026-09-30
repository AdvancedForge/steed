"""Browser preferences dialog."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from config import Config


class SettingsDialog(QDialog):
    """Edit preferences and commit them together when Save is selected."""

    def __init__(self, config: Config, parent=None, effective_ua: str = "") -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Click Save to apply changes. Some changes may require reload/restart."
            )
        )

        form = QFormLayout()
        layout.addLayout(form)

        self.home_page = QLineEdit(
            self.config.get("home_page", "https://www.google.com")
        )
        form.addRow("Home page:", self.home_page)

        self.search_engine = QLineEdit(
            self.config.get("search_engine", "https://www.google.com/search?q=")
        )
        form.addRow("Search engine URL prefix:", self.search_engine)

        self.restore_tabs = QCheckBox()
        self.restore_tabs.setChecked(
            bool(self.config.get("restore_tabs_on_startup", True))
        )
        form.addRow("Restore tabs on startup:", self.restore_tabs)

        self.dark_mode = QCheckBox()
        self.dark_mode.setChecked(bool(self.config.get("dark_mode", False)))
        form.addRow("Dark mode:", self.dark_mode)

        self.user_agent = QLineEdit(self.config.get("user_agent_override", ""))
        self.user_agent.setPlaceholderText("Leave blank to use default UA")
        form.addRow("User-Agent override:", self.user_agent)
        effective = QLabel(
            effective_ua or "The engine default is used when no override is set."
        )
        effective.setWordWrap(True)
        form.addRow("Current effective User-Agent:", effective)

        self.global_cookie_mode = QComboBox()
        self.global_cookie_mode.addItems(["on_switch", "all_profiles"])
        self.global_cookie_mode.setCurrentText(
            self.config.get("global_cookie_sharing_mode", "on_switch")
        )
        form.addRow("Global cookie sharing:", self.global_cookie_mode)

        self.global_cookie_apply_startup = QCheckBox()
        self.global_cookie_apply_startup.setChecked(
            bool(self.config.get("global_cookie_sharing_apply_on_startup", True))
        )
        form.addRow(
            "Apply global cookies on startup:", self.global_cookie_apply_startup
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        """Write changes to config."""
        self.config.update(
            {
                "home_page": self.home_page.text().strip() or "https://www.google.com",
                "search_engine": self.search_engine.text().strip()
                or "https://www.google.com/search?q=",
                "restore_tabs_on_startup": self.restore_tabs.isChecked(),
                "dark_mode": self.dark_mode.isChecked(),
                "user_agent_override": self.user_agent.text().strip(),
                "global_cookie_sharing_mode": self.global_cookie_mode.currentText(),
                "global_cookie_sharing_apply_on_startup": self.global_cookie_apply_startup.isChecked(),
            }
        )
