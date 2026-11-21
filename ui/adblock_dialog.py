"""Explicit filtering settings and update diagnostics."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from browser.ad_blocker import MAX_OWN_RULES, own_rules
from ui.html_dialog import HtmlSettingsDialog


class AdBlockDialog(HtmlSettingsDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Ad blocking")
        self.resize(620, 460)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox(
            "Enable network blocking and supported cosmetic filters"
        )
        self.enabled.setChecked(service.config.get("adblock_enabled", True))
        layout.addWidget(self.enabled)
        layout.addWidget(
            QLabel(
                "Site exceptions (comma-separated host names; reload affected pages):"
            )
        )
        self.exceptions = QLineEdit(
            ", ".join(service.config.get("adblock_exceptions", []))
        )
        layout.addWidget(self.exceptions)
        layout.addWidget(
            QLabel(
                "Your own rules, one per line, in the same syntax as the lists: "
                "||ads.example.com^ blocks a site's requests, "
                "example.com##.cookie-banner hides an element there, and "
                "@@||example.com^ lets a site through. Lines starting with ! "
                f"are notes. Up to {MAX_OWN_RULES:,} rules."
            )
        )
        self.rules = QPlainTextEdit("\n".join(own_rules(service.config)))
        self.rules.setPlaceholderText("example.com##.newsletter-popup")
        layout.addWidget(self.rules, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        update = QPushButton("Download / Update EasyList and EasyPrivacy")
        self.update_button = update
        update.clicked.connect(service.update)
        layout.addWidget(update)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        service.changed.connect(self.refresh)
        self.finished.connect(lambda: service.changed.disconnect(self.refresh))
        self.refresh()

    def refresh(self):
        self.status.setText(
            f"{self.service.status}\nBlocked requests this session: {self.service.count:,}"
        )
        self.update_button.setEnabled(
            not self.service.updating and not self.service.compiling
        )

    def save(self):
        hosts = sorted(
            {
                host.strip().lower().lstrip(".")
                for host in self.exceptions.text().split(",")
                if host.strip()
            }
        )
        rules = [line.strip() for line in self.rules.toPlainText().splitlines()]
        changed = own_rules({"adblock_rules": rules}) != own_rules(self.service.config)
        self.service.config.update(
            {
                "adblock_enabled": self.enabled.isChecked(),
                "adblock_exceptions": hosts,
                "adblock_rules": [line for line in rules if line],
            }
        )
        self.service.refresh_settings()
        if changed:
            self.service.refresh_rules()
        self.accept()
