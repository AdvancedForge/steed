"""Nonmodal native-agent questions and approvals, separate from Steed grants."""

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class AgentRequestDialog(QDialog):
    def __init__(self, payload, respond, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Codex request")
        self.resize(620, 430)
        self.respond = respond
        self.method, self.params = payload["method"], payload["params"]
        self.answered = False
        self.answers = {}
        layout = QVBoxLayout(self)
        title = QLabel(
            {
                "item/commandExecution/requestApproval": "Allow Codex to run this command?",
                "item/fileChange/requestApproval": "Allow Codex to make these file changes?",
                "item/permissions/requestApproval": "Allow Codex this additional access?",
            }.get(self.method, "Codex needs your input.")
        )
        title.setWordWrap(True)
        layout.addWidget(title)
        if self.method == "tool/requestUserInput":
            questions = self.params.get("questions", [])
            if not 1 <= len(questions) <= 3:
                raise ValueError("Unsupported agent question count")
            for question in questions:
                label = QLabel(question["question"])
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                layout.addWidget(label)
                options = question.get("options") or []
                if len(options) > 10:
                    raise ValueError("Unsupported agent option count")
                choices = QComboBox()
                choices.addItem("Choose an answer…", None)
                for option in options:
                    choices.addItem(option["label"], option["label"])
                    choices.setItemData(
                        choices.count() - 1,
                        option["description"],
                        Qt.ItemDataRole.ToolTipRole,
                    )
                choices.setVisible(bool(options))
                layout.addWidget(choices)
                answer = QLineEdit()
                answer.setMaxLength(8192)
                answer.setPlaceholderText("Your answer")
                if question.get("isSecret"):
                    answer.setEchoMode(QLineEdit.EchoMode.Password)
                layout.addWidget(answer)
                self.answers[question["id"]] = (choices, answer)
        else:
            details = QPlainTextEdit()
            details.setReadOnly(True)
            if self.method == "item/commandExecution/requestApproval":
                preview = "Command:\n" + str(
                    self.params.get("command") or "Not provided"
                )
                preview += "\n\nFolder: " + str(
                    self.params.get("cwd") or "Codex workspace"
                )
            elif self.method == "item/fileChange/requestApproval":
                changes = payload.get("item", {}).get("changes", [])
                preview = (
                    "\n\n".join(
                        str(change.get("path", "")) + "\n" + str(change.get("diff", ""))
                        for change in changes
                    )
                    or "Codex has not supplied a file change preview."
                )
                if self.params.get("grantRoot"):
                    preview += "\n\nWrite access requested: " + self.params["grantRoot"]
            else:
                preview = json.dumps(
                    self.params["permissions"], ensure_ascii=False, indent=2
                )
            if self.params.get("reason"):
                preview += "\n\nReason: " + self.params["reason"]
            details.setPlainText(preview)
            layout.addWidget(details, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        self.allow = QPushButton("Send answer" if self.answers else "Allow once")
        self.allow.clicked.connect(lambda: self.complete("accept"))
        buttons.addWidget(self.allow)
        self.session = QPushButton("Allow for this chat")
        decisions = self.params.get("availableDecisions")
        self.allow.setEnabled(
            decisions is None or "accept" in decisions or bool(self.answers)
        )
        self.session.setVisible(
            not self.answers
            and self.method != "tool/requestUserInput"
            and (decisions is None or "acceptForSession" in decisions)
        )
        self.session.clicked.connect(lambda: self.complete("acceptForSession"))
        buttons.addWidget(self.session)
        deny = QPushButton("Cancel" if self.answers else "Deny")
        deny.clicked.connect(self.reject)
        buttons.addWidget(deny)
        layout.addLayout(buttons)

    def complete(self, decision):
        if self.answers:
            answers = {}
            for question_id, (choices, answer) in self.answers.items():
                text = answer.text().strip() or choices.currentData()
                if not text:
                    self.error.setText("Answer each question before sending.")
                    return
                answers[question_id] = {"answers": [text]}
            result = {"answers": answers}
        elif self.method == "item/permissions/requestApproval":
            result = {
                "permissions": self.params["permissions"]
                if decision != "decline"
                else {},
                "scope": "session" if decision == "acceptForSession" else "turn",
            }
        else:
            result = {"decision": decision}
        self.answered = True
        try:
            self.respond(result)
        except (ValueError, RuntimeError, OSError) as error:
            self.answered = False
            self.error.setText(str(error))
            return
        self.accept()

    def reject(self):
        if not self.answered:
            if self.answers:
                result = {"answers": {key: {"answers": []} for key in self.answers}}
                try:
                    self.respond(result)
                except (ValueError, RuntimeError, OSError):
                    pass
                self.answered = True
            else:
                self.complete("decline")
        super().reject()
