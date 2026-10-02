"""Simple user-only approval of one immutable custom-code package."""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from services.worker_bundle import environment
from ui.task_approval import app_description


class WorkerApprovalDialog(QDialog):
    def __init__(self, service, task, receipt, parent=None):
        super().__init__(parent)
        self.service, self.task_id, self.request_id = (
            service,
            task.task_id,
            receipt.request_id,
        )
        self.request_key = receipt.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Run custom code?")
        self.resize(640, 560)
        package = service.workspace.package(task.task_id, receipt.extension_id)
        env = environment(receipt.runtime)
        if env.sha256 != receipt.environment_sha256:
            raise ValueError(
                "The requested environment changed; request a fresh approval"
            )
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"Task: {task.objective}\nPackage: {package['name']}\n"
            f"Environment: {env.name}\n"
            + (
                ", ".join(f"{name} {version}" for name, version in env.libraries) + "\n"
                if env.libraries
                else ""
            )
            + "Runs in a local Python sandbox. No direct network, host files, "
            "passwords or subprocesses. Existing task file/draft/app permissions "
            "still control every broker operation.\n"
            "Limits: 20 seconds, 10 CPU seconds, 512 MiB memory and 32 MiB scratch space."
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        if package["manifest"]["capabilities"]:
            layout.addWidget(
                QLabel("Requested app access within this task's permissions:")
            )
            access = QPlainTextEdit(
                "\n\n".join(
                    p["effect"].upper()
                    + ": "
                    + app_description(service, p)
                    + "\nDestination: "
                    + p["destination"]
                    for p in package["manifest"]["capabilities"]
                )
            )
            access.setReadOnly(True)
            access.setMaximumHeight(120)
            layout.addWidget(access)
        self.source = QPlainTextEdit()
        self.source.setReadOnly(True)
        preview = []
        remaining = 16384
        for name, data in package["files"].items():
            if remaining <= 0:
                break
            preview.append(
                "File: "
                + name
                + "\n"
                + data[:remaining].decode("utf-8", errors="replace")
            )
            remaining -= len(data)
        self.source.setPlainText("\n\n".join(preview))
        layout.addWidget(self.source)
        notice = QLabel(
            "Source preview is limited to 16 KiB. Export source from Workspace to inspect larger packages.\nSHA-256: "
            + receipt.package_sha256
            + "\nEnvironment SHA-256: "
            + receipt.environment_sha256
        )
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.remember = QCheckBox(
            "Allow this exact package and environment again for this task"
        )
        layout.addWidget(self.remember)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        self.deny_button = QPushButton("Deny")
        self.deny_button.setDefault(True)
        self.allow_button = QPushButton("Run Package")
        self.allow_button.setAutoDefault(False)
        self.deny_button.clicked.connect(lambda: self.decide(False))
        self.allow_button.clicked.connect(lambda: self.decide(True))
        buttons.addWidget(self.deny_button)
        buttons.addWidget(self.allow_button)
        layout.addLayout(buttons)

    def decide(self, approve):
        try:
            self.service.resolve_worker(
                self.task_id,
                self.request_id,
                approve=approve,
                remember=self.remember.isChecked(),
            )
        except (OSError, sqlite3.Error, ValueError, KeyError, RuntimeError):
            self.error.setText(
                "The package could not start. Check task access, package files and available worker slots, or deny the request."
            )
            return
        self.accept()
