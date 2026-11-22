"""Readable website approval and saved-choice management."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.site_permissions import FEATURES
from ui.html_dialog import HtmlSettingsDialog
from ui.prompts import MessageBox as QMessageBox


def plain_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class SitePermissionDialog(HtmlSettingsDialog):
    def __init__(
        self, origin, feature, profile, *, private=False, parent=None, who=None
    ):
        super().__init__(parent)
        self.setWindowTitle("Website Permission")
        self.resize(470, 260)
        layout = QVBoxLayout(self)
        title, description = FEATURES[feature]
        layout.addWidget(
            plain_label(
                f"{who or origin}\nRequests: {title}\n{description}\nProfile: {profile}"
            )
        )
        self.remember = QComboBox()
        self.remember.addItem("Allow once", "once")
        self.remember.addItem(
            "Allow every time in this private session"
            if private
            else "Allow every time",
            "global",
        )
        if not private:
            self.remember.addItem("Allow every time on this profile", "profile")
        layout.addWidget(self.remember)
        self.notice = plain_label("")
        layout.addWidget(self.notice)

        def explain():
            mode = self.remember.currentData()
            where = (
                "Only this request."
                if mode == "once"
                else "This private session only; forgotten when Steed closes."
                if private
                else "This site and capability on every regular profile. Incognito is excluded."
                if mode == "global"
                else f"This site and capability on profile {profile} only."
            )
            self.notice.setText(
                where
                + "\nWebsite permissions do not authorize agents."
                + (
                    "\nYou will still choose a screen or window each time."
                    if feature.startswith("Desktop")
                    else ""
                )
            )

        self.remember.currentIndexChanged.connect(explain)
        explain()
        buttons = QHBoxLayout()
        deny = QPushButton("Deny")
        deny.setDefault(True)
        deny.clicked.connect(self.reject)
        allow = QPushButton("Allow")
        allow.setAutoDefault(False)
        allow.clicked.connect(self.accept)
        buttons.addWidget(deny)
        buttons.addWidget(allow)
        layout.addLayout(buttons)


def describe_site_settings(values):
    """Changed settings in words, such as "JavaScript blocked · zoom 125%"."""
    from browser.site_settings import LABELS

    parts = [f"{LABELS[key]} blocked" for key in LABELS if values.get(key) == "block"]
    if "zoom" in values:
        parts.append(f"zoom {round(values['zoom'] * 100)}%")
    return " · ".join(parts)


class SitePermissionsDialog(HtmlSettingsDialog):
    def __init__(self, store, parent=None, site_settings=None, profile=""):
        super().__init__(parent)
        self.store = store
        self.site_settings = site_settings
        self.setWindowTitle("Website permissions")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Saved website approvals are separate from agent approvals. Forgetting makes future "
                "requests ask again. Close any tab currently using camera, microphone or screen sharing "
                "to stop its existing capture."
            )
        )
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Site", "Capability", "Applies to"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        forget = QPushButton("Forget Selected Permission")
        forget.clicked.connect(self.forget)
        layout.addWidget(forget)
        if site_settings is not None:
            where = "this incognito session" if profile == "search" else profile
            layout.addWidget(
                plain_label(
                    f"Site settings in {where}: zoom, JavaScript, autoplay and pop-ups "
                    "you changed from the site icon in the address bar."
                )
            )
            self.sites = QTableWidget(0, 2)
            self.sites.setHorizontalHeaderLabels(["Site", "Settings"])
            self.sites.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.sites.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.sites.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.sites)
            reset = QPushButton("Reset Selected Site")
            reset.clicked.connect(self.reset_site)
            layout.addWidget(reset)
            site_settings.changed.connect(self.refresh_sites)
            self.refresh_sites()
        self.refresh()

    def refresh_sites(self, *_):
        hosts = sorted(self.site_settings.sites)
        self.sites.setRowCount(len(hosts))
        for row, host in enumerate(hosts):
            self.sites.setItem(row, 0, QTableWidgetItem(host))
            self.sites.setItem(
                row,
                1,
                QTableWidgetItem(
                    describe_site_settings(self.site_settings.sites[host])
                ),
            )
        self.sites.resizeColumnsToContents()

    def reset_site(self):
        row = self.sites.currentRow()
        if row >= 0:
            self.site_settings.clear(self.sites.item(row, 0).text())

    def done(self, result):
        if self.site_settings is not None:
            try:
                self.site_settings.changed.disconnect(self.refresh_sites)
            except TypeError:
                pass
        super().done(result)

    def refresh(self):
        try:
            self.rules = self.store.rules()
        except (OSError, ValueError):
            QMessageBox.warning(
                self, "Website Permissions", "Could not read saved website permissions."
            )
            self.rules = []
        self.table.setRowCount(len(self.rules))
        for row, rule in enumerate(self.rules):
            where = (
                "Private session"
                if rule["private"]
                else "All regular profiles"
                if rule["mode"] == "global"
                else rule["profile"]
            )
            for column, value in enumerate(
                (rule["origin"], FEATURES[rule["feature"]][0], where)
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def forget(self):
        row = self.table.currentRow()
        if row < 0:
            return
        try:
            self.store.forget(self.rules[row]["id"])
        except (OSError, ValueError):
            QMessageBox.warning(
                self,
                "Website Permissions",
                "Could not remove the saved website permission.",
            )
        self.refresh()
