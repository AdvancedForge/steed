"""Explicit filtering settings and update diagnostics."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class AdBlockDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Ad Blocking")
        self.resize(620, 260)
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
        self.service.config.update(
            {"adblock_enabled": self.enabled.isChecked(), "adblock_exceptions": hosts}
        )
        self.service.refresh_settings()
        self.accept()
