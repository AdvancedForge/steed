"""Native preferences and HTML replacement for a profile's new-tab page."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from services.start_pages import (
    CUSTOM_EXAMPLE,
    MAX_HTML_BYTES,
    MODULES,
    clean_html,
    image_data,
)

LABELS = {
    "search": "Search bar",
    "image": "Background image",
    "bookmarks": "Bookmarks",
    "recent": "Recently closed tabs",
    "history": "Recent visits",
    "news": "News feed",
}


class StartPageDialog(QDialog):
    def __init__(self, pages, profile, parent=None):
        super().__init__(parent)
        self.pages, self.profile = pages, profile
        self.setWindowTitle("Customize New Tab")
        self.resize(650, 620)
        state = pages.state(profile)
        self.original = state
        self.image = state["settings"]["image"]
        layout = QVBoxLayout(self)
        label = (
            "Incognito — changes last only for this session"
            if profile == "search"
            else f"New-tab page for {profile}"
        )
        layout.addWidget(QLabel(label))
        tabs = QTabWidget()
        layout.addWidget(tabs)
        built_in, custom = QWidget(), QWidget()
        tabs.addTab(built_in, "Built-in sections")
        tabs.addTab(custom, "Custom HTML")
        form = QVBoxLayout(built_in)
        form.addWidget(
            QLabel(
                "Check sections to show. Drag content sections to reorder them; the image fills the background."
            )
        )
        self.modules = QListWidget()
        self.modules.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        enabled = state["settings"]["modules"]
        for module in [*enabled, *(m for m in MODULES if m not in enabled)]:
            item = QListWidgetItem(LABELS[module], self.modules)
            item.setData(Qt.ItemDataRole.UserRole, module)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if module in enabled else Qt.CheckState.Unchecked
            )
        form.addWidget(self.modules)
        self.minimal = QCheckBox("Minimal page — hide the heading, header and footer")
        self.minimal.setChecked(state["settings"]["minimal"])
        form.addWidget(self.minimal)
        images = QHBoxLayout()
        self.image_label = QLabel(
            "Background image chosen" if self.image else "No background image chosen"
        )
        images.addWidget(self.image_label, 1)
        choose = QPushButton("Choose image…")
        choose.clicked.connect(self.choose_image)
        images.addWidget(choose)
        remove = QPushButton("Remove image")
        remove.clicked.connect(self.remove_image)
        images.addWidget(remove)
        form.addLayout(images)
        feed_form = QFormLayout()
        self.feed = QLineEdit(state["settings"]["feed"])
        self.feed.setPlaceholderText("https://example.org/feed.xml")
        feed_form.addRow("RSS / Atom address:", self.feed)
        form.addLayout(feed_form)
        note = QLabel(
            "News is fetched only with the News section enabled and a feed address saved. The feed receives no browser cookies. Images are copied locally."
        )
        note.setWordWrap(True)
        form.addWidget(note)
        html_layout = QVBoxLayout(custom)
        self.page_choice = QComboBox()
        self.page_choice.addItem("New Tab", "newtab")
        self.page_choice.addItem("Welcome", "welcome")
        html_layout.addWidget(self.page_choice)
        self.use_custom = QCheckBox("Replace the built-in page with this HTML")
        self.use_custom.setChecked(bool(state["html"]))
        html_layout.addWidget(self.use_custom)
        help_label = QLabel(
            "Edit or import a self-contained page (up to 256 KB). Inline HTML, CSS and JavaScript work; external resources and native Steed actions are unavailable. Use General → Customize New Tab to edit again. Uncheck above to restore built-in sections."
        )
        help_label.setWordWrap(True)
        html_layout.addWidget(help_label)
        self.html = QPlainTextEdit(state["html"] or CUSTOM_EXAMPLE)
        self.html.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        html_layout.addWidget(self.html, 1)
        self.html_drafts = {
            "newtab": state["html"] or CUSTOM_EXAMPLE,
            "welcome": state["welcome_html"] or CUSTOM_EXAMPLE,
        }
        self.html_enabled = {
            "newtab": bool(state["html"]),
            "welcome": bool(state["welcome_html"]),
        }
        self.edited_page = "newtab"
        self.page_choice.currentIndexChanged.connect(self.change_page)
        import_html = QPushButton("Import HTML…")
        import_html.clicked.connect(self.import_html)
        html_layout.addWidget(import_html)
        sharing = QHBoxLayout()
        copy_html = QPushButton("Copy HTML for a chat")
        copy_html.clicked.connect(
            lambda: QApplication.clipboard().setText(self.html.toPlainText())
        )
        sharing.addWidget(copy_html)
        share_html = QPushButton("Share source with an agent…")
        share_html.clicked.connect(self.share_html)
        sharing.addWidget(share_html)
        export_html = QPushButton("Export HTML…")
        export_html.clicked.connect(self.export_html)
        sharing.addWidget(export_html)
        html_layout.addLayout(sharing)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_image(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose an image", "", "Images (*.png *.jpg *.jpeg *.webp *.gif)"
        )
        if not filename:
            return
        try:
            self.image = image_data(filename)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Image unavailable", str(error))
            return
        self.image_label.setText("Image chosen — enable the Local image section")

    def remove_image(self):
        self.image = ""
        self.image_label.setText("No image chosen")

    def import_html(self):
        from pathlib import Path

        filename, _ = QFileDialog.getOpenFileName(
            self, "Import new-tab HTML", "", "HTML (*.html *.htm)"
        )
        if not filename:
            return
        try:
            path = Path(filename)
            if path.stat().st_size > MAX_HTML_BYTES:
                raise ValueError("Use an HTML page of at most 256 KB")
            html = clean_html(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "HTML unavailable", str(error))
            return
        self.html.setPlainText(html)
        self.use_custom.setChecked(True)

    def save(self):
        modules = [
            self.modules.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.modules.count())
            if self.modules.item(i).checkState() == Qt.CheckState.Checked
        ]
        try:
            self.save_draft()
            self.pages.configure(
                self.profile,
                {
                    "modules": modules,
                    "image": self.image,
                    "feed": self.feed.text().strip(),
                    "minimal": self.minimal.isChecked(),
                },
                self.html_drafts["newtab"] if self.html_enabled["newtab"] else "",
                welcome_html=self.html_drafts["welcome"]
                if self.html_enabled["welcome"]
                else "",
            )
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Could not save New Tab", str(error))
            return
        self.accept()

    def save_draft(self):
        self.html_drafts[self.edited_page] = self.html.toPlainText()
        self.html_enabled[self.edited_page] = self.use_custom.isChecked()

    def change_page(self):
        self.save_draft()
        self.edited_page = self.page_choice.currentData()
        self.html.setPlainText(self.html_drafts[self.edited_page])
        self.use_custom.setChecked(self.html_enabled[self.edited_page])

    def share_html(self):
        from ui.start_page_share import PageSourceShareDialog

        registry = self.pages.registry
        if registry._tasks is None or not registry._tasks.external_tasks:
            QMessageBox.information(
                self,
                "Connect an agent",
                "Connect an external agent in the sidebar first, or use Copy HTML for a website chat.",
            )
            return
        PageSourceShareDialog(
            registry._tasks,
            self.profile,
            self.edited_page,
            self.html.toPlainText(),
            self,
        ).exec()

    def export_html(self):
        from pathlib import Path

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export HTML", self.edited_page + ".html", "HTML (*.html)"
        )
        if not filename:
            return
        try:
            Path(filename).write_text(
                clean_html(self.html.toPlainText()), encoding="utf-8"
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not export HTML", str(error))
