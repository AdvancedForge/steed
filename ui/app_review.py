"""Trusted before/after review; generated text is never rendered as HTML."""

import difflib
import json

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
        self.task_id = task.task_id
        self.request_key = review.request_key
        self.setWindowTitle("Review Document Change")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        receipt = service.connector_store.get(task.task_id, review.request_id)
        definition = service.connectors.get(receipt.connector, receipt.tool)
        payload_review = definition.review_kind == "payload"
        if payload_review:
            self.setWindowTitle("Review App Action")
        explanation = (
            "Approve this exact payload once. The service's API defines what it does. An uncertain result will not be retried automatically."
            if payload_review
            else "Replace the selected tab's body with the draft below. Formatting may change. If the document changed since this preview, the write will be rejected."
        )
        summary = QLabel(
            f"Task: {task.objective}\n{definition.description}\nAction: {receipt.effect}\nDestination: {receipt.destination}\n{explanation}"
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
        contents = (
            (
                ("Request Payload", review.before_text),
                ("Request Details", review.context),
                ("Draft", review.after_text),
            )
            if payload_review
            else (
                ("Changes", diff),
                ("Current text", review.before_text),
                ("Proposed text", review.after_text),
            )
        )
        if payload_review:
            details = json.loads(review.context)
            summary.setText(
                summary.text()
                + f"\n{details['method']} {details['url']}\nAccount: {details['account']}"
                + (
                    f"\nPlan step {details['step']}/{details['steps']}: {details['step_name']}\nApprove only this step. Later steps need separate reviews; stopping does not undo completed steps."
                    if "plan_request_id" in details
                    else ""
                )
            )
        for title, content in contents:
            editor = QPlainTextEdit(content)
            editor.setReadOnly(True)
            tabs.addTab(editor, title)
        layout.addWidget(tabs)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox()
        self.approve_button = self.buttons.addButton(
            ("Send This Message" if receipt.effect == "send" else "Create This Item")
            if payload_review
            else "Apply This Change",
            QDialogButtonBox.ButtonRole.AcceptRole,
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
                "This action is no longer eligible. Check the task permission and receipt, then create a fresh proposal if needed."
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
