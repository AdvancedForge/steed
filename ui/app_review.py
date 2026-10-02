"""Trusted before/after review; generated text is never rendered as HTML."""

import difflib

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
)


class AppReviewDialog(QDialog):
    def __init__(self, service, task, review, parent=None):
        super().__init__(parent)
        self.service, self.review = service, review
        self.request_key = review.request_key
        self.setWindowTitle("Review Document Change")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        receipt = service.connector_store.get(task.task_id, review.request_id)
        definition = service.connectors.get(receipt.connector, receipt.tool)
        summary = QLabel(
            f"Task: {task.objective}\n{definition.description}\nDestination: {receipt.destination}\nReplace the selected tab's body with the draft below. Formatting may change. If the document changed since this preview, the write will be rejected."
        )
        from PyQt6.QtCore import Qt

        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        tabs = QTabWidget()
        diff = "\n".join(
            difflib.unified_diff(
                review.before_text.splitlines(),
                review.after_text.splitlines(),
                fromfile="Current text",
                tofile="Proposed text",
                lineterm="",
            )
        )
        for title, content in (
            ("Changes", diff),
            ("Current text", review.before_text),
            ("Proposed text", review.after_text),
        ):
            editor = QPlainTextEdit(content)
            editor.setReadOnly(True)
            tabs.addTab(editor, title)
        layout.addWidget(tabs)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox()
        self.approve_button = self.buttons.addButton(
            "Apply This Change", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.deny_button = self.buttons.addButton(
            "Deny", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.deny_button.setDefault(True)
        self.approve_button.setAutoDefault(False)
        self.approve_button.clicked.connect(lambda: self.decide(True))
        self.deny_button.clicked.connect(lambda: self.decide(False))
        layout.addWidget(self.buttons)

    def decide(self, approve):
        try:
            self.service.resolve_app_review(
                self.review.task_id, self.review.request_id, approve=approve
            )
        except Exception:
            self.status.setText(
                "This change is no longer eligible. Check the task permission and receipt, then create a fresh proposal if needed."
            )
            try:
                pending = (
                    self.service.app_reviews.get(
                        self.review.task_id, self.review.request_id
                    ).status
                    == "pending"
                )
            except Exception:
                pending = False
            self.approve_button.setEnabled(pending and not self.service.failure)
            return
        self.accept()
