"""User-only review of actual recipe domains, API paths and returned fields."""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class RecipeReviewDialog(QDialog):
    def __init__(self, manager, recipe, parent=None, *, service=None, proposal=None):
        super().__init__(parent)
        self.manager, self.recipe, self.service, self.proposal = (
            manager,
            recipe,
            service,
            proposal,
        )
        self.request_key = (
            proposal.request_key if proposal else "recipe-import:" + recipe.sha256
        )
        self.task_id = proposal.task_id if proposal else None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Add this service connection?")
        self.resize(640, 540)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"Service: {recipe.name}\nDomain: {recipe.origin}\n"
            "Installs these exact API requests. Connecting an account and allowing task access are separate steps. "
            "This service receives its own token; credentials stay in Steed's desktop keyring. "
            "Check that the listed requests really read data from the intended service."
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        review = QPlainTextEdit()
        review.setReadOnly(True)
        review.setPlainText(
            f"Account identity: GET {recipe.identity_path}\n"
            f"Account ID: {recipe.subject_pointer}\nAccount label: {recipe.label_pointer}\n\n"
            + "\n\n".join(
                f"READ: {t.description}\nGET {t.path}\nReturns: "
                + ", ".join(f"{label} ({path})" for label, path in t.response)
                for t in recipe.tools
            )
            + "\n\nRecipe SHA-256: "
            + recipe.sha256
        )
        layout.addWidget(review)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.deny_button = QPushButton("Deny")
        self.deny_button.setDefault(True)
        self.allow_button = QPushButton("Add Connection Recipe")
        self.allow_button.setAutoDefault(False)
        self.deny_button.clicked.connect(lambda: self.decide(False))
        self.allow_button.clicked.connect(lambda: self.decide(True))
        row.addWidget(self.deny_button)
        row.addWidget(self.allow_button)
        layout.addLayout(row)

    def decide(self, approve):
        try:
            if self.proposal is not None:
                self.service.resolve_recipe(
                    self.proposal.task_id, self.proposal.request_id, approve=approve
                )
            elif approve:
                self.manager.install_recipe(self.recipe)
        except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
            self.error.setText(
                "The recipe could not be installed. Check the source, task permissions and app setup, or deny it."
            )
            return
        self.accept()
