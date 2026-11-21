"""Choose a shipped service; source, account and payload reviews remain separate."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QLabel, QPushButton, QVBoxLayout

from ui.html_dialog import HtmlSettingsDialog
from ui.recipe_review import RecipeReviewDialog


class BundledRecipesDialog(HtmlSettingsDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Add a service")
        self.resize(620, 350)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Choose a packaged connector, review its requests, then connect your account and choose allowed actions."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.entries = manager.bundled_recipes.list()
        self.choice = QComboBox()
        for entry in self.entries:
            self.choice.addItem(
                f"{entry.recipe.name} ({entry.recipe.origin.removeprefix('https://')})"
            )
        layout.addWidget(self.choice)
        self.description = QLabel()
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.review_button = QPushButton("Review Connection")
        self.review_button.clicked.connect(self.review)
        self.review_button.setEnabled(bool(self.entries))
        layout.addWidget(self.review_button)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.choice.currentIndexChanged.connect(self.describe)
        self.describe()

    def describe(self):
        if not self.entries:
            self.description.setText(
                "No packaged connectors are available in this installation. You can still use Google or import a connector recipe from App Connections."
            )
            return
        recipe = self.entries[self.choice.currentIndex()].recipe
        self.description.setText(
            f"Service: {recipe.name}\nAPI domain: {recipe.origin}\n"
            + "\n".join(f"{t.effect.upper()}: {t.description}" for t in recipe.tools)
            + "\n\nSource review adds this exact recipe. It does not sign in, enable account access, or authorize a task. Creates and sends need their own payload approval."
        )

    def review(self):
        try:
            recipe = self.manager.bundled_recipes.select(
                self.entries[self.choice.currentIndex()]
            )
        except (OSError, ValueError):
            self.status.setText(
                "This packaged connector is unavailable or changed. Close and reopen the service list."
            )
            return
        self.review_dialog = RecipeReviewDialog(self.manager, recipe, self)
        self.review_dialog.show()
