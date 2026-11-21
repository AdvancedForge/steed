"""Trusted user approval and cancellation, outside the client command surface."""

import json
import sqlite3
from dataclasses import asdict
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from services.task_store import ACTIVE_STATUSES
from ui.html_dialog import HtmlSettingsDialog
from ui.prompts import MessageBox as QMessageBox
from ui.task_approval import ACTION_LABELS, app_description
from ui.task_recovery import TaskRecoveryDialog


class TaskDialog(HtmlSettingsDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Agent tasks & permissions")
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "What your agents are doing and what they may do. Requests open for your approval as they come; revoking access cancels the task."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Task", "Task status", "Permission"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self.show_details)
        layout.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.panels = QTabWidget()
        self.panels.addTab(self.details, "Permissions")
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.panels.addTab(self.activity, "Activity")
        from ui.task_steps import TaskSteps

        self.steps = TaskSteps(service)
        self.panels.addTab(self.steps, "Steps")
        self.panels.currentChanged.connect(lambda _: self.refresh_activity())
        artifacts = QWidget()
        artifact_layout = QVBoxLayout(artifacts)
        artifact_notice = QLabel(
            "Notes and drafts your agent wrote, shown as plain text. Its sources are pages it looked at; it may still be wrong."
        )
        artifact_notice.setWordWrap(True)
        artifact_layout.addWidget(artifact_notice)
        self.artifacts = QComboBox()
        self.artifacts.currentIndexChanged.connect(self.show_artifact)
        artifact_layout.addWidget(self.artifacts)
        self.artifact_preview = QPlainTextEdit()
        self.artifact_preview.setReadOnly(True)
        artifact_layout.addWidget(self.artifact_preview)
        self.delete_artifact_button = QPushButton("Delete Selected Draft")
        self.delete_artifact_button.clicked.connect(self.delete_artifact)
        artifact_layout.addWidget(self.delete_artifact_button)
        self.panels.addTab(artifacts, "Artifacts")
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_notice = QLabel(
            "Files this task made or you shared with it. Sharing lets the agent read that file. Exporting never overwrites a file; running a custom Python package needs its own approval."
        )
        workspace_notice.setWordWrap(True)
        workspace_layout.addWidget(workspace_notice)
        self.workspace_files = QComboBox()
        self.workspace_files.currentIndexChanged.connect(self.show_workspace_file)
        workspace_layout.addWidget(self.workspace_files)
        self.workspace_preview = QPlainTextEdit()
        self.workspace_preview.setReadOnly(True)
        workspace_layout.addWidget(self.workspace_preview)
        workspace_buttons = QHBoxLayout()
        self.share_workspace_button = QPushButton("Share File With Task…")
        self.share_workspace_button.clicked.connect(self.share_workspace_file)
        self.export_workspace_button = QPushButton("Export Selected File…")
        self.export_workspace_button.clicked.connect(self.export_workspace_file)
        self.delete_workspace_button = QPushButton("Delete Selected File")
        self.delete_workspace_button.clicked.connect(self.delete_workspace_file)
        for button in (
            self.share_workspace_button,
            self.export_workspace_button,
            self.delete_workspace_button,
        ):
            workspace_buttons.addWidget(button)
        workspace_layout.addLayout(workspace_buttons)
        self.panels.addTab(workspace, "Workspace")
        recipes = QWidget()
        recipe_layout = QVBoxLayout(recipes)
        recipe_layout.addWidget(
            QLabel(
                "Services your agent wants to connect. Installing, signing in and what it may do are separate choices."
            )
        )
        self.recipe_requests = QTableWidget(0, 3)
        self.recipe_requests.setHorizontalHeaderLabels(["Request", "Provider", "State"])
        self.recipe_requests.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.recipe_requests.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.recipe_requests.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recipe_requests.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        recipe_layout.addWidget(self.recipe_requests)
        self.review_recipe_button = QPushButton("Review Connection Recipe…")
        self.review_recipe_button.clicked.connect(self.review_recipe)
        recipe_layout.addWidget(self.review_recipe_button)
        self._recipe_rows = []
        self.recipe_requests.itemSelectionChanged.connect(self.update_recipe_actions)
        self.panels.addTab(recipes, "Connector Recipes")
        layouts = QWidget()
        layout_body = QVBoxLayout(layouts)
        layout_notice = QLabel(
            "Browser layouts your agent designed. Applying one changes only how Steed looks."
        )
        layout_notice.setWordWrap(True)
        layout_body.addWidget(layout_notice)
        self.layout_requests = QTableWidget(0, 3)
        self.layout_requests.setHorizontalHeaderLabels(["Request", "Layout", "State"])
        self.layout_requests.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.layout_requests.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.layout_requests.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.layout_requests.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        layout_body.addWidget(self.layout_requests)
        self.review_layout_button = QPushButton("Review Layout…")
        self.review_layout_button.clicked.connect(self.review_layout)
        self.layout_requests.itemSelectionChanged.connect(self.update_layout_actions)
        layout_body.addWidget(self.review_layout_button)
        self._layout_rows = []
        self.panels.addTab(layouts, "Layouts")
        data_panels = QWidget()
        data_body = QVBoxLayout(data_panels)
        data_body.addWidget(
            QLabel(
                "Panels that show your agent's saved drafts, in a profile you choose. Approving one lets the same task update it; it gets no access to your data."
            )
        )
        self.data_panel_requests = QTableWidget(0, 4)
        self.data_panel_requests.setHorizontalHeaderLabels(
            ["Panel", "State", "Profile", "Revision"]
        )
        self.data_panel_requests.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.data_panel_requests.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.data_panel_requests.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.data_panel_requests.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        data_body.addWidget(self.data_panel_requests)
        data_buttons = QHBoxLayout()
        self.review_data_panel_button = QPushButton("Review Panel…")
        self.review_data_panel_button.clicked.connect(self.review_data_panel)
        self.remove_data_panel_button = QPushButton("Remove Panel")
        self.remove_data_panel_button.clicked.connect(self.remove_data_panel)
        data_buttons.addWidget(self.review_data_panel_button)
        data_buttons.addWidget(self.remove_data_panel_button)
        data_body.addLayout(data_buttons)
        self._data_panel_rows = []
        self.data_panel_requests.itemSelectionChanged.connect(
            self.update_data_panel_actions
        )
        self.panels.addTab(data_panels, "Data Panels")
        pages = QWidget()
        pages_body = QVBoxLayout(pages)
        pages_body.addWidget(
            QLabel(
                "Changes your agent suggests for Welcome and New Tab. No browsing data is shared."
            )
        )
        self.page_requests = QTableWidget(0, 3)
        self.page_requests.setHorizontalHeaderLabels(["Name", "State", "Request"])
        self.page_requests.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.page_requests.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.page_requests.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.page_requests.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        pages_body.addWidget(self.page_requests)
        self.review_page_button = QPushButton("Review Page…")
        self.review_page_button.clicked.connect(self.review_page)
        pages_body.addWidget(self.review_page_button)
        self._page_rows = []
        self.page_requests.itemSelectionChanged.connect(self.update_page_actions)
        self.panels.addTab(pages, "Pages & Scripts")
        workers = QWidget()
        worker_layout = QVBoxLayout(workers)
        worker_notice = QLabel(
            "Custom packages run in a separate, isolated worker, only after you approve them. What they did in your apps shows under Apps."
        )
        worker_notice.setWordWrap(True)
        worker_layout.addWidget(worker_notice)
        self.worker_setup_status = QLabel(
            "Check setup before building a worker. Without isolation, nothing runs on your computer directly."
        )
        self.worker_setup_status.setWordWrap(True)
        self.worker_setup_status.setTextFormat(Qt.TextFormat.PlainText)
        worker_layout.addWidget(self.worker_setup_status)
        self.worker_setup_button = QPushButton("Check Worker Setup")
        self.worker_setup_button.clicked.connect(self.check_worker_setup)
        worker_layout.addWidget(self.worker_setup_button)
        self.worker_runs = QTableWidget(0, 5)
        self.worker_runs.setHorizontalHeaderLabels(
            ["Request", "Entrypoint", "Environment", "State", "Result"]
        )
        self.worker_runs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.worker_runs.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.worker_runs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.worker_runs.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        worker_layout.addWidget(self.worker_runs)
        worker_buttons = QHBoxLayout()
        self.review_worker_button = QPushButton("Review Package…")
        self.review_worker_button.clicked.connect(self.review_worker)
        self.cancel_worker_button = QPushButton("Cancel Selected Worker")
        self.cancel_worker_button.clicked.connect(self.cancel_worker)
        worker_buttons.addWidget(self.review_worker_button)
        worker_buttons.addWidget(self.cancel_worker_button)
        worker_layout.addLayout(worker_buttons)
        self.worker_runs.itemSelectionChanged.connect(self.update_worker_actions)
        self._worker_rows = []
        self.panels.addTab(workers, "Workers")
        self.downloads = QTableWidget(0, 4)
        self.downloads.setHorizontalHeaderLabels(["File", "State", "Bytes", "Result"])
        self.downloads.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.downloads.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.downloads.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.downloads.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.panels.addTab(self.downloads, "Downloads")
        apps = QWidget()
        app_layout = QVBoxLayout(apps)
        app_notice = QLabel(
            "What your agent did in your apps. Cancelling does not undo changes already made; if a change is marked uncertain, check the app before asking again."
        )
        app_notice.setWordWrap(True)
        app_layout.addWidget(app_notice)
        self.apps = QTableWidget(0, 4)
        self.apps.setHorizontalHeaderLabels(
            ["Operation", "Destination", "State", "Request"]
        )
        self.apps.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.apps.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.apps.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.apps.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for column in (0, 2, 3):
            self.apps.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.apps.itemSelectionChanged.connect(self.show_app)
        app_layout.addWidget(self.apps)
        self.app_preview = QPlainTextEdit()
        self.app_preview.setReadOnly(True)
        app_layout.addWidget(self.app_preview)
        app_buttons = QHBoxLayout()
        self.cancel_app_button = QPushButton("Cancel App Operation")
        self.cancel_app_button.clicked.connect(lambda: self.perform_app("cancel"))
        self.reconcile_app_button = QPushButton("Reconcile Uncertain Result")
        self.reconcile_app_button.clicked.connect(lambda: self.perform_app("reconcile"))
        app_buttons.addWidget(self.cancel_app_button)
        app_buttons.addWidget(self.reconcile_app_button)
        self.review_app_button = QPushButton("Review Proposed Change")
        self.review_app_button.clicked.connect(self.review_app)
        app_buttons.addWidget(self.review_app_button)
        app_layout.addLayout(app_buttons)
        result_buttons = QHBoxLayout()
        self.clear_app_result_button = QPushButton("Clear Saved Result")
        self.clear_app_result_button.clicked.connect(self.clear_app_result)
        self.clear_task_results_button = QPushButton("Clear This Task’s Results")
        self.clear_task_results_button.clicked.connect(
            lambda: self.clear_app_result(all_owned=True)
        )
        result_buttons.addWidget(self.clear_app_result_button)
        result_buttons.addWidget(self.clear_task_results_button)
        app_layout.addLayout(result_buttons)
        self.panels.addTab(apps, "Apps")
        saved = QWidget()
        saved_layout = QVBoxLayout(saved)
        saved_notice = QLabel(
            "Saved approvals let any connected agent make matching requests without asking. Forget one to be asked again (tasks using it stop). Revoking a task keeps its saved approvals."
        )
        saved_notice.setWordWrap(True)
        saved_layout.addWidget(saved_notice)
        self.saved_rules = QTableWidget(0, 3)
        self.saved_rules.setHorizontalHeaderLabels(["Applies to", "Actions", "Saved"])
        self.saved_rules.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.saved_rules.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.saved_rules.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.saved_rules.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        saved_layout.addWidget(self.saved_rules)
        self.saved_details = QPlainTextEdit()
        self.saved_details.setReadOnly(True)
        saved_layout.addWidget(self.saved_details)
        self.forget_rule_button = QPushButton("Forget and Revoke Access")
        self.forget_rule_button.clicked.connect(self.forget_rule)
        self.saved_rules.itemSelectionChanged.connect(self.show_saved_rule)
        saved_layout.addWidget(self.forget_rule_button)
        self.panels.addTab(saved, "Saved Approvals")
        recovery = QWidget()
        recovery_layout = QVBoxLayout(recovery)
        self.recovery_details = QPlainTextEdit()
        self.recovery_details.setReadOnly(True)
        recovery_layout.addWidget(self.recovery_details)
        self.recovery_button = QPushButton("Choose Recovery Material…")
        self.recovery_button.clicked.connect(self.choose_recovery)
        recovery_layout.addWidget(self.recovery_button)
        self.panels.addTab(recovery, "Recovery")
        self._saved_rows = []
        layout.addWidget(self.panels)
        self.cancel_download_button = QPushButton("Cancel Selected Download")
        self.cancel_download_button.clicked.connect(self.cancel_download)
        self.downloads.itemSelectionChanged.connect(self.update_download_action)
        layout.addWidget(self.cancel_download_button)
        watching = QHBoxLayout()
        self.sessions = QComboBox()
        self.watch_button = QPushButton("Watch Page")
        self.watch_button.clicked.connect(self.watch_page)
        watching.addWidget(self.sessions)
        watching.addWidget(self.watch_button)
        layout.addLayout(watching)
        buttons = QHBoxLayout()
        self.approve_button = QPushButton("Approve Request")
        self.deny_button = QPushButton("Deny Request")
        self.revoke_button = QPushButton("Revoke Access")
        self.cancel_button = QPushButton("Cancel Task")
        for button, operation in (
            (self.approve_button, "approve"),
            (self.deny_button, "deny"),
            (self.revoke_button, "revoke"),
            (self.cancel_button, "cancel"),
        ):
            button.clicked.connect(lambda checked=False, op=operation: self.perform(op))
            buttons.addWidget(button)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        service.changed.connect(self.refresh)
        self.finished.connect(lambda: service.changed.disconnect(self.refresh))
        self._rows = []
        self._download_rows = []
        self._app_rows = []
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh_activity)
        self.finished.connect(self.timer.stop)
        self.timer.start()
        self.refresh()

    def selection(self):
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def refresh(self):
        self.refresh_saved_rules()
        selected = self.selection()
        selected_id = selected[0].task_id if selected else None
        self.table.blockSignals(True)
        try:
            self._rows = []
            if not self.service._closed and not self.service.failure:
                for task in self.service.store.list_tasks():
                    grants = self.service.store.grants(task.task_id)
                    self._rows.append((task, grants[-1] if grants else None))
            self.table.setRowCount(len(self._rows))
            selected_row = -1
            for row, (task, grant) in enumerate(self._rows):
                for column, text in enumerate(
                    (
                        " ".join(task.objective.split())[:120],
                        task.status,
                        grant.status if grant else "Not requested",
                    )
                ):
                    self.table.setItem(row, column, QTableWidgetItem(text))
                if task.task_id == selected_id:
                    selected_row = row
            if selected_row >= 0:
                self.table.selectRow(selected_row)
            elif self._rows:
                self.table.selectRow(0)
            self.table.resizeColumnsToContents()
        except (sqlite3.Error, OSError, ValueError):
            self._rows = []
            self.table.setRowCount(0)
            self.service._fatal()
        finally:
            self.table.blockSignals(False)
        self.show_details()

    def refresh_saved_rules(self):
        previous = self.saved_rules.currentRow()
        selected = (
            self._saved_rows[previous].rule_id
            if 0 <= previous < len(self._saved_rows)
            else None
        )
        self.saved_rules.blockSignals(True)
        try:
            self._saved_rows = (
                self.service.store.approval_rules()
                if not self.service._closed and not self.service.failure
                else []
            )
            self.saved_rules.setRowCount(len(self._saved_rows))
            for row, rule in enumerate(self._saved_rows):
                where = (
                    "All profiles (including private)"
                    if rule.mode == "global"
                    else rule.scope["profile"].removeprefix("existing:")
                )
                for col, value in enumerate(
                    (where, ", ".join(rule.scope["actions"]), rule.created_at)
                ):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.saved_rules.setItem(row, col, item)
                if selected == rule.rule_id:
                    self.saved_rules.selectRow(row)
        except (OSError, sqlite3.Error, ValueError):
            self.service._fatal()
            self._saved_rows = []
            self.saved_rules.setRowCount(0)
        finally:
            self.saved_rules.blockSignals(False)
        self.show_saved_rule()

    def show_saved_rule(self):
        row = self.saved_rules.currentRow()
        valid = 0 <= row < len(self._saved_rows)
        self.forget_rule_button.setEnabled(
            valid and not self.service.failure and not self.service._closed
        )
        if not valid:
            self.saved_details.setPlainText(
                "Select a saved approval to review its sites, actions, app destinations and limits."
            )
            return
        scope = self._saved_rows[row].scope
        lines = [
            "Sites: " + (", ".join(scope["origins"]) or "about:blank only"),
            "Actions:",
        ]
        lines.extend(ACTION_LABELS[action] for action in scope["actions"])
        for permission in scope["connector_permissions"]:
            lines.extend(
                (
                    f"{permission['effect'].upper()}: {app_description(self.service, permission)}",
                    "Destination: " + permission["destination"],
                )
            )
        for field, label in (
            ("download_destinations", "Download files"),
            ("upload_sources", "Upload files"),
            ("upload_origins", "Upload sites"),
        ):
            if scope[field]:
                lines.append(label + ": " + ", ".join(scope[field]))
        lines.append(
            f"Per task: {scope['duration_seconds']} seconds, {scope['max_operations']} operations, {scope['max_requests']} requests, {scope['max_sessions']} sessions."
        )
        lines.append(
            "Private network access: "
            + ("Allowed" if scope["allow_private_network"] else "Denied")
        )
        lines.append(
            "\nTechnical scope:\n" + json.dumps(scope, ensure_ascii=False, indent=2)
        )
        self.saved_details.setPlainText("\n".join(lines))

    def forget_rule(self):
        row = self.saved_rules.currentRow()
        if 0 <= row < len(self._saved_rows):
            try:
                self.service.forget_approval_rule(self._saved_rows[row].rule_id)
            except (OSError, sqlite3.Error, ValueError, RuntimeError):
                QMessageBox.warning(
                    self,
                    "Saved Approvals",
                    "The saved approval could not be removed. Check the permission journal.",
                )
            self.refresh()

    def show_details(self):
        selected = self.selection()
        available = not self.service._closed and not self.service.failure
        task, grant = selected if selected else (None, None)
        active = available and task is not None and task.status in ACTIVE_STATUSES
        pending = active and grant is not None and grant.status == "requested"
        recovery = (
            self.service.store.recovery(task.task_id)
            if available and task is not None
            else {"status": "none"}
        )
        self.recovery_button.setEnabled(
            bool(active and recovery["status"] == "requested")
        )
        self.recovery_details.setPlainText(
            json.dumps(recovery, ensure_ascii=False, indent=2)
            if recovery["status"] != "none"
            else "Agents can request a follow-up before requesting permissions. You choose a finished task and selected drafts. New grants and pages are required; previous writes are not resumed."
        )
        self.approve_button.setEnabled(bool(pending))
        self.deny_button.setEnabled(bool(pending))
        self.revoke_button.setEnabled(
            bool(active and grant and grant.status == "approved")
        )
        self.cancel_button.setEnabled(bool(active))
        selected_session = self.sessions.currentData()
        self.sessions.clear()
        if active:
            for session_id, owner in self.service._sessions.items():
                if owner == task.task_id:
                    state = self.service.backend.inspect(session_id)
                    self.sessions.addItem(state.url, session_id)
            index = self.sessions.findData(selected_session)
            if index >= 0:
                self.sessions.setCurrentIndex(index)
        self.watch_button.setEnabled(
            bool(
                active
                and grant
                and grant.status == "approved"
                and "attach" in grant.scope["actions"]
                and self.sessions.count()
            )
        )
        if self.service.failure:
            self.details.setPlainText(self.service.failure)
        elif grant:
            scope = grant.scope
            network = (
                "Private addresses and DNS hostnames are allowed at the listed origins. "
                "DNS routes are not pinned; a hostname may resolve to a private network."
                if scope["allow_private_network"]
                else "Public literal IP addresses only."
            )
            self.details.setPlainText(
                f"Task: {task.task_id}\n"
                + (
                    "Approved using saved permission: "
                    + self.service.store.grant_rule_id(grant.grant_id)
                    + "\n"
                    if self.service.store.grant_rule_id(grant.grant_id)
                    else ""
                )
                + f"Profile: {scope['profile']}\n"
                + "Origins:\n"
                + ("\n".join(scope["origins"]) or "about:blank only")
                + "\n"
                + (
                    "Storage: existing cookies, cached responses, and site storage are shared. "
                    "Site changes persist and may change your signed-in session. "
                    "The network scope and budget apply to task pages only. "
                    "Task pages bypass background service workers and keep document scripts disabled. "
                    "Your tabs and other approved tasks can use this profile at the same time.\n"
                    if scope["profile"] != "private"
                    else "Storage: a separate private profile for each session.\n"
                )
                + f"Actions: {', '.join(scope['actions']) or 'None'}\n"
                + (
                    "Upload files:\n"
                    + "\n".join(scope["upload_sources"])
                    + "\nUpload sites:\n"
                    + "\n".join(scope["upload_origins"])
                    + f"\nStaging per page: up to {scope['max_upload_bytes']:,} bytes.\n"
                    if scope.get("upload_sources")
                    else ""
                )
                + (
                    "Download files (no overwriting):\n"
                    + "\n".join(scope["download_destinations"])
                    + f"\nEach file: up to {scope['max_download_bytes']:,} bytes.\n"
                    if scope.get("download_destinations")
                    else ""
                )
                + (
                    "Control changes: text entry, selection by index, and checkbox/radio activation. "
                    "Active document scripts are unavailable.\n"
                    if "edit" in scope["actions"]
                    else ""
                )
                + (
                    "Submission: native GET/POST forms can change account data and send "
                    "current fields, including hidden site tokens. Password, multi-file and folder forms, "
                    "child frames and new windows are unavailable. Multipart needs approved single-file upload.\n"
                    if "submit" in scope["actions"]
                    else "Form submission is unavailable.\n"
                )
                + (
                    "Content access: readable page text and control labels are shared with "
                    "the task client. Form values and editable text are omitted.\n"
                    if "read" in scope["actions"]
                    else "Content access: URL/title metadata only; page text is not shared.\n"
                )
                + f"Network permission: {network}\n"
                + (
                    "Network methods: GET/HEAD, plus one bound POST per approved form action. "
                    if "submit" in scope["actions"]
                    else "Network methods: GET/HEAD. "
                )
                + "Requests can have effects on a website.\n"
                + (
                    "App operations (exact effect and destination):\n"
                    + "\n".join(
                        f"{p['effect'].upper()}: {p['connector']}.{p['tool']} → {p['destination']}"
                        for p in scope.get("connector_permissions", [])
                    )
                    + "\nApp results are retained; cancellation does not undo writes.\n"
                    if scope.get("connector_permissions")
                    else ""
                )
                + f"Duration: {scope['duration_seconds']} seconds after approval\n"
                + f"Operation limit: {scope['max_operations']}\n"
                + f"Network request limit: {scope['max_requests']}\n"
                + f"Session limit: {scope['max_sessions']}\n"
                + f"Expires: {grant.expires_at or 'Not approved'}\n"
                + "\nClient objective (preview): "
                + json.dumps(task.objective[:256], ensure_ascii=False)
            )
        else:
            self.details.setPlainText(
                "Task permission requests from internal clients appear here."
            )
        self.refresh_activity()

    def choose_recovery(self):
        selected = self.selection()
        if selected:
            approvals = getattr(self.service.profiles, "task_approvals", None)
            existing = approvals.dialog if approvals else None
            if (
                isinstance(existing, TaskRecoveryDialog)
                and existing.task_id == selected[0].task_id
            ):
                existing.raise_()
                existing.activateWindow()
                return
            dialog = TaskRecoveryDialog(self.service, selected[0], self)
            dialog.show()

    def refresh_activity(self):
        selected = self.selection()
        if self.service._closed or self.service.failure or not selected:
            self.activity.clear()
            self.downloads.setRowCount(0)
            self._download_rows = []
            self.update_download_action()
            self.artifacts.clear()
            self.artifact_preview.clear()
            self.refresh_workspace()
            self.refresh_workers()
            self.apps.setRowCount(0)
            self._app_rows = []
            self.show_app()
            return
        task = selected[0]
        try:
            if self.panels.currentWidget() is self.steps:
                self.steps.show_task(task.task_id)
            events = self.service.store.recent_events(task.task_id)
            lines = []
            for event in events:
                label = event.kind.replace(".", " ").replace("_", " ").capitalize()
                reason = event.payload.get("reason") or event.payload.get("error")
                if reason:
                    label += ": " + str(reason)[:100].replace("_", " ")
                if event.kind == "download.finished":
                    label = "Download " + event.payload.get("state", "finished")
                lines.append(event.created_at[11:19] + "  " + label)
            content = "\n".join(lines) or "No recorded activity."
            if self.activity.toPlainText() != content:
                scroll = self.activity.verticalScrollBar()
                follow = scroll.value() == scroll.maximum()
                previous = scroll.value()
                self.activity.setPlainText(content)
                scroll.setValue(scroll.maximum() if follow else previous)
            receipts = self.service.store.recent_events(
                task.task_id,
                limit=100,
                kinds=("download.requested", "download.finished", "download.failed"),
            )
            records = {}
            for event in receipts:
                data = dict(event.payload)
                data.setdefault("state", "requested")
                records[data["download_id"]] = data
            for session, owner in self.service._sessions.items():
                if owner != task.task_id:
                    continue
                listing = getattr(self.service.backend, "list_downloads", None)
                if callable(listing):
                    for receipt in listing(session):
                        records[receipt.download_id] = asdict(receipt)
            previous = self.downloads.currentRow()
            selected_id = (
                self._download_rows[previous]["download_id"]
                if 0 <= previous < len(self._download_rows)
                else None
            )
            self._download_rows = list(records.values())[-100:]
            self.downloads.blockSignals(True)
            self.downloads.setRowCount(len(self._download_rows))
            for row, record in enumerate(self._download_rows):
                size = record.get("received_bytes", 0)
                total = record.get("total_bytes", 0)
                text = f"{size:,}" + (f" / {total:,}" if total > 0 else "")
                for column, value in enumerate(
                    (
                        record["destination"],
                        record["state"].replace("_", " "),
                        text,
                        (record.get("error") or record.get("reason", "")).replace(
                            "_", " "
                        ),
                    )
                ):
                    self.downloads.setItem(row, column, QTableWidgetItem(value))
                if record["download_id"] == selected_id:
                    self.downloads.selectRow(row)
            self.downloads.blockSignals(False)
            self.update_download_action()
            previous = self.artifacts.currentData()
            self.artifacts.blockSignals(True)
            self.artifacts.clear()
            for artifact in self.service.store.artifacts(task.task_id):
                self.artifacts.addItem(artifact.title, artifact.artifact_id)
            index = self.artifacts.findData(previous)
            if index >= 0:
                self.artifacts.setCurrentIndex(index)
            self.artifacts.blockSignals(False)
            self.show_artifact()
            self.refresh_workspace()
            self.refresh_workers()
            self.refresh_apps(task.task_id)
        except (sqlite3.Error, OSError, ValueError, KeyError):
            self.service._fatal()

    def refresh_workspace(self):
        selected = self.selection()
        previous = self.workspace_files.currentData()
        self.workspace_files.blockSignals(True)
        self.workspace_files.clear()
        if selected and not self.service.failure and not self.service._closed:
            for item in self.service.workspace.list(selected[0].task_id):
                if item.status != "deleted":
                    self.workspace_files.addItem(item.name, item.file_id)
            index = self.workspace_files.findData(previous)
            if index >= 0:
                self.workspace_files.setCurrentIndex(index)
        self.workspace_files.blockSignals(False)
        self.show_workspace_file()

    def show_workspace_file(self):
        try:
            self._show_workspace_file()
        except (sqlite3.Error, OSError, ValueError, KeyError):
            self.service._fatal()
            self.share_workspace_button.setEnabled(False)
            self.export_workspace_button.setEnabled(False)
            self.delete_workspace_button.setEnabled(False)
            self.workspace_preview.setPlainText("Task workspace is unavailable.")

    def _show_workspace_file(self):
        selected = self.selection()
        available = bool(
            selected and not self.service.failure and not self.service._closed
        )
        task, grant = selected if selected else (None, None)
        self.share_workspace_button.setEnabled(
            bool(
                available
                and task.status in ACTIVE_STATUSES
                and grant
                and grant.status == "approved"
                and "workspace" in grant.scope["actions"]
            )
        )
        file_id = self.workspace_files.currentData()
        item = (
            self.service.workspace.get(task.task_id, file_id)
            if available and file_id
            else None
        )
        self.export_workspace_button.setEnabled(bool(item and item.status == "sealed"))
        self.delete_workspace_button.setEnabled(bool(item))
        lines = []
        if item:
            lines = [
                f"File: {item.name}",
                f"Source: {item.source.replace('_', ' ')}",
                f"Type: {item.media_type}",
                f"State: {item.status}; {item.byte_count:,} / {item.expected_bytes:,} bytes",
                f"SHA-256: {item.sha256}",
                "File contents are not rendered or executed here.",
            ]
            recovery = self.service.store.recovery(task.task_id)
            for imported in recovery.get("context", {}).get("imported_files", []):
                if imported["file_id"] == item.file_id:
                    lines.append(
                        "Shared from previous task: " + recovery["source_task_id"]
                    )
                    lines.append("Original file ID: " + imported["source_file_id"])
        if available:
            for package in self.service.workspace.extensions(task.task_id):
                lines.append(
                    "\nCustom integration: "
                    + package.get("name", "Source deleted")
                    + f" ({package['status']}; separate execution permission required)"
                )
                lines.append("Requested access (not granted):")
                capabilities = package.get("requested_capabilities", [])
                for capability in capabilities:
                    lines.append(
                        f"{capability['effect'].upper()}: "
                        + capability["connector"]
                        + " / "
                        + capability["tool"]
                        + "\nDestination: "
                        + capability["destination"]
                    )
                if not capabilities:
                    lines.append("None declared, or source no longer available.")
                lines.append("Package SHA-256: " + package["package_sha256"])
        content = "\n".join(lines) or "Select a task file, or share one with this task."
        if self.workspace_preview.toPlainText() != content:
            self.workspace_preview.setPlainText(content)

    def share_workspace_file(self):
        selected = self.selection()
        if not selected or not self.share_workspace_button.isEnabled():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Share a file with this task's agent"
        )
        if path:
            self._workspace_action(self.service.import_workspace_file, path)

    def export_workspace_file(self):
        selected = self.selection()
        file_id = self.workspace_files.currentData()
        if not selected or not file_id or not self.export_workspace_button.isEnabled():
            return
        item = self.service.workspace.get(selected[0].task_id, file_id)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export task file to a new filename", item.name.rsplit("/", 1)[-1]
        )
        if path:
            self._workspace_action(self.service.export_workspace_file, file_id, path)

    def delete_workspace_file(self):
        file_id = self.workspace_files.currentData()
        if not self.selection() or not file_id:
            return
        answer = QMessageBox.question(
            self,
            "Delete Task File",
            "Delete the saved bytes? Packages using this file will become unavailable. "
            "Exported copies and the task journal remain.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._workspace_action(self.service.delete_workspace_file, file_id)

    def _workspace_action(self, operation, *args):
        selected = self.selection()
        if not selected:
            return
        try:
            operation(selected[0].task_id, *args)
        except (OSError, sqlite3.Error, ValueError, KeyError, RuntimeError):
            QMessageBox.warning(
                self,
                "Task Workspace",
                "The file operation could not finish. Check task access, the size limit, "
                "and the destination. Exports need a new filename; a completed export "
                "can remain if recording its result fails.",
            )
        self.refresh_activity()

    def refresh_workers(self):
        self.refresh_recipes()
        selected = self.selection()
        previous = self.worker_runs.currentRow()
        self.worker_runs.blockSignals(True)
        self._worker_rows = (
            self.service.workers.store.list(selected[0].task_id)
            if selected and not self.service.failure and not self.service._closed
            else []
        )
        self.worker_runs.setRowCount(len(self._worker_rows))
        for row, record in enumerate(self._worker_rows):
            for column, value in enumerate(
                (
                    record.request_id,
                    record.entrypoint,
                    record.runtime,
                    record.state.replace("_", " "),
                    record.reason.replace("_", " "),
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    "Package: "
                    + record.package_sha256
                    + "\nEnvironment: "
                    + (record.environment_sha256 or "legacy receipt")
                )
                self.worker_runs.setItem(row, column, item)
        if self._worker_rows:
            self.worker_runs.selectRow(
                max(0, min(previous, len(self._worker_rows) - 1))
            )
        self.worker_runs.blockSignals(False)
        self.update_worker_actions()

    def selected_worker(self):
        row = self.worker_runs.currentRow()
        return self._worker_rows[row] if 0 <= row < len(self._worker_rows) else None

    def refresh_recipes(self):
        self.refresh_layouts()
        self.refresh_data_panels()
        self.refresh_pages()
        selected = self.selection()
        self._recipe_rows = (
            self.service.recipes.list(selected[0].task_id)
            if selected and not self.service.failure and not self.service._closed
            else []
        )
        self.recipe_requests.setRowCount(len(self._recipe_rows))
        for row, proposal in enumerate(self._recipe_rows):
            for col, value in enumerate(
                (
                    proposal.request_id,
                    proposal.provider,
                    proposal.state.replace("_", " "),
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(proposal.sha256)
                self.recipe_requests.setItem(row, col, item)
        self.update_recipe_actions()

    def refresh_data_panels(self):
        selected = self.selection()
        self._data_panel_rows = (
            self.service.data_panels.list(selected[0].task_id)
            if selected and not self.service.failure and not self.service._closed
            else []
        )
        self.data_panel_requests.setRowCount(len(self._data_panel_rows))
        for row, receipt in enumerate(self._data_panel_rows):
            for col, value in enumerate(
                (
                    receipt.name,
                    receipt.state.replace("_", " "),
                    receipt.profile_name or "Choose on review",
                    str(receipt.revision),
                )
            ):
                self.data_panel_requests.setItem(row, col, QTableWidgetItem(value))
        self.update_data_panel_actions()

    def refresh_pages(self):
        selected = self.selection()
        self._page_rows = (
            self.service.start_pages.list(selected[0].task_id)
            + self.service.customizations.list(selected[0].task_id)
            + self.service.watches.list(selected[0].task_id)
            + self.service.routines.list(selected[0].task_id)
            if selected and not self.service.failure and not self.service._closed
            else []
        )
        self.page_requests.setRowCount(len(self._page_rows))
        for row, proposal in enumerate(self._page_rows):
            for col, value in enumerate(
                (proposal.name, proposal.state.replace("_", " "), proposal.request_id)
            ):
                self.page_requests.setItem(row, col, QTableWidgetItem(value))
        self.update_page_actions()

    def update_page_actions(self):
        row = self.page_requests.currentRow()
        self.review_page_button.setEnabled(
            0 <= row < len(self._page_rows)
            and self._page_rows[row].state == "awaiting_approval"
        )

    def review_page(self):
        from services.customization_proposals import CustomizationProposal
        from services.routine_proposals import RoutineProposal
        from services.watch_proposals import WatchProposal
        from ui.customization_review import CustomizationReviewDialog
        from ui.routines_dialog import RoutineReviewDialog
        from ui.start_page_review import StartPageReviewDialog
        from ui.watch_review import WatchReviewDialog

        row = self.page_requests.currentRow()
        if not 0 <= row < len(self._page_rows):
            return
        proposal = self._page_rows[row]
        try:
            dialog = (
                CustomizationReviewDialog
                if isinstance(proposal, CustomizationProposal)
                else WatchReviewDialog
                if isinstance(proposal, WatchProposal)
                else RoutineReviewDialog
                if isinstance(proposal, RoutineProposal)
                else StartPageReviewDialog
            )(self.service, proposal, self)
            dialog.show()
        except (ValueError, KeyError):
            QMessageBox.warning(
                self, "Page review", "This page request is unavailable."
            )

    def update_data_panel_actions(self):
        row = self.data_panel_requests.currentRow()
        receipt = (
            self._data_panel_rows[row]
            if 0 <= row < len(self._data_panel_rows)
            else None
        )
        available = not self.service.failure and not self.service._closed
        self.review_data_panel_button.setEnabled(
            bool(available and receipt and receipt.state == "awaiting_approval")
        )
        self.remove_data_panel_button.setEnabled(
            bool(available and receipt and receipt.state == "applied")
        )

    def review_data_panel(self):
        from ui.data_panels import DataPanelReviewDialog

        row = self.data_panel_requests.currentRow()
        if not 0 <= row < len(self._data_panel_rows):
            return
        try:
            dialog = DataPanelReviewDialog(
                self.service, self._data_panel_rows[row], self
            )
            dialog.show()
        except (ValueError, KeyError, sqlite3.Error, OSError):
            QMessageBox.warning(
                self, "Data panel", "This panel or its drafts are unavailable."
            )

    def remove_data_panel(self):
        row = self.data_panel_requests.currentRow()
        if 0 <= row < len(self._data_panel_rows):
            receipt = self._data_panel_rows[row]
            try:
                self.service.data_panels.remove(receipt.task_id, receipt.request_id)
            except (ValueError, KeyError, sqlite3.Error, OSError):
                QMessageBox.warning(self, "Data panel", "This panel is unavailable.")
            self.refresh_data_panels()

    def refresh_layouts(self):
        selected = self.selection()
        self._layout_rows = (
            self.service.layouts.list(selected[0].task_id)
            if selected and not self.service.failure and not self.service._closed
            else []
        )
        self.layout_requests.setRowCount(len(self._layout_rows))
        for row, proposal in enumerate(self._layout_rows):
            for col, value in enumerate(
                (proposal.request_id, proposal.name, proposal.state.replace("_", " "))
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(proposal.sha256)
                self.layout_requests.setItem(row, col, item)
        self.update_layout_actions()

    def update_layout_actions(self):
        row = self.layout_requests.currentRow()
        self.review_layout_button.setEnabled(
            not self.service.failure
            and not self.service._closed
            and 0 <= row < len(self._layout_rows)
            and self._layout_rows[row].state == "awaiting_approval"
        )

    def review_layout(self):
        from ui.layout_review import LayoutReviewDialog

        row = self.layout_requests.currentRow()
        if not 0 <= row < len(self._layout_rows):
            return
        proposal = self._layout_rows[row]
        try:
            dialog = LayoutReviewDialog(
                self.service.profiles,
                self.service.layouts.definition(proposal),
                self,
                service=self.service,
                proposal=proposal,
            )
            dialog.show()
        except (OSError, ValueError, KeyError, RuntimeError):
            QMessageBox.warning(
                self, "Layout review", "This layout request is unavailable."
            )

    def update_recipe_actions(self):
        row = self.recipe_requests.currentRow()
        self.review_recipe_button.setEnabled(
            bool(
                not self.service.failure
                and not self.service._closed
                and 0 <= row < len(self._recipe_rows)
                and self._recipe_rows[row].state == "awaiting_approval"
            )
        )

    def review_recipe(self):
        from ui.recipe_review import RecipeReviewDialog

        row = self.recipe_requests.currentRow()
        if not 0 <= row < len(self._recipe_rows):
            return
        proposal = self._recipe_rows[row]
        try:
            dialog = RecipeReviewDialog(
                self.service.recipes.connections,
                self.service.recipes.recipe(proposal),
                self,
                service=self.service,
                proposal=proposal,
            )
            dialog.show()
        except (OSError, ValueError, KeyError, RuntimeError):
            QMessageBox.warning(
                self,
                "Connector Recipe",
                "This recipe source is unavailable. Check the task and workspace file.",
            )

    def update_worker_actions(self):
        record = self.selected_worker()
        available = not self.service.failure and not self.service._closed
        self.worker_setup_button.setEnabled(
            available and not getattr(self, "_checking_worker_setup", False)
        )
        self.review_worker_button.setEnabled(
            bool(available and record and record.state == "awaiting_approval")
        )
        self.cancel_worker_button.setEnabled(
            bool(
                available
                and record
                and record.state in {"awaiting_approval", "running", "cancelling"}
            )
        )

    def review_worker(self):
        from ui.worker_approval import WorkerApprovalDialog

        record = self.selected_worker()
        selected = self.selection()
        if not record or not selected or record.state != "awaiting_approval":
            return
        try:
            dialog = WorkerApprovalDialog(self.service, selected[0], record, self)
            dialog.show()
        except (OSError, sqlite3.Error, ValueError, KeyError, RuntimeError):
            QMessageBox.warning(
                self,
                "Worker Package",
                "The package is unavailable. Cancel the request or check task access.",
            )

    def check_worker_setup(self):
        self.worker_setup_button.setEnabled(False)
        self._checking_worker_setup = True
        self.worker_setup_status.setText(
            "Checking isolated Python, syscall policy and resource limits…"
        )
        try:
            future = self.service.check_worker_setup()
        except (OSError, sqlite3.Error, ValueError, RuntimeError):
            self._checking_worker_setup = False
            self.worker_setup_status.setText(
                "The setup check is unavailable or already running. Try again after it finishes."
            )
            self.update_worker_actions()
            return

        def finished(source):
            if sip.isdeleted(self):
                return
            self._checking_worker_setup = False
            try:
                result = source.result()
                self.worker_setup_status.setText(
                    "Local Python worker ready. Exact-package and task permissions still apply."
                    if result["available"]
                    else "Local worker unavailable. Install Bubblewrap/libseccomp and check system Python and user-namespace policy; then retry. Generated code will not run without isolation."
                )
                documents = next(
                    (
                        e
                        for e in result.get("environments", [])
                        if e["runtime"] == "documents"
                    ),
                    None,
                )
                if result["available"] and documents is not None:
                    self.worker_setup_status.setText(
                        self.worker_setup_status.text()
                        + "\n"
                        + (
                            "Document bundle ready: PDF, XLSX and images."
                            if documents["available"]
                            else "Optional document bundle unavailable. Run .venv/bin/python -m browser.install_worker_bundle once, then retry this check."
                        )
                    )
            except Exception:
                self.worker_setup_status.setText(
                    "The setup check ended. Reopen this panel or retry when task services are available."
                )
            self.update_worker_actions()

        future.add_done_callback(finished)

    def cancel_worker(self):
        record = self.selected_worker()
        if record:
            self._workspace_action(self.service.cancel_worker, record.request_id)

    def show_artifact(self):
        selected = self.selection()
        artifact_id = self.artifacts.currentData()
        self.delete_artifact_button.setEnabled(
            bool(
                selected
                and artifact_id
                and not self.service._closed
                and not self.service.failure
            )
        )
        if (
            not selected
            or not artifact_id
            or self.service._closed
            or self.service.failure
        ):
            self.artifact_preview.clear()
            return
        try:
            artifact = self.service.store.artifact(selected[0].task_id, artifact_id)
            sources = (
                ", ".join(f"#{s}" for s in artifact.source_events) or "None supplied"
            )
            content = (
                f"Agent-generated {artifact.format}; {artifact.byte_count:,} bytes\n"
                f"SHA-256: {artifact.sha256}\nPage inspection evidence: {sources}\n\n"
                + artifact.content
            )
            if self.artifact_preview.toPlainText() != content:
                self.artifact_preview.setPlainText(content)
        except (sqlite3.Error, OSError, ValueError, KeyError):
            self.service._fatal()

    def delete_artifact(self):
        selected = self.selection()
        artifact_id = self.artifacts.currentData()
        if (
            not selected
            or not artifact_id
            or self.service._closed
            or self.service.failure
        ):
            return
        if (
            QMessageBox.question(
                self,
                "Delete draft?",
                "Delete this retained draft? This cannot be undone.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.service.store.delete_artifact(selected[0].task_id, artifact_id)
        except (sqlite3.Error, OSError, ValueError, KeyError):
            self.service._fatal()
        self.refresh_activity()

    def refresh_apps(self, task_id):
        index = self.apps.currentRow()
        previous = (
            (self._app_rows[index].task_id, self._app_rows[index].request_id)
            if 0 <= index < len(self._app_rows)
            else None
        )
        self._app_rows = self.service.connector_store.list(task_id)
        recovery = self.service.store.recovery(task_id)
        if recovery["status"] == "approved":
            for shared in recovery["context"].get("app_receipts", []):
                self._app_rows.append(
                    self.service.connector_store.get(
                        shared["task_id"], shared["request_id"]
                    )
                )
        self.apps.blockSignals(True)
        self.apps.setRowCount(len(self._app_rows))
        for row, receipt in enumerate(self._app_rows):
            for column, value in enumerate(
                (
                    ("Previous " if receipt.task_id != task_id else "")
                    + f"{receipt.effect.upper()}: {receipt.connector}.{receipt.tool}",
                    receipt.destination,
                    receipt.state,
                    receipt.request_id,
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.apps.setItem(row, column, item)
            if (receipt.task_id, receipt.request_id) == previous:
                self.apps.selectRow(row)
        if previous is None and self._app_rows:
            self.apps.selectRow(0)
        self.apps.blockSignals(False)
        self.show_app()

    def show_app(self):
        index = self.apps.currentRow()
        receipt = self._app_rows[index] if 0 <= index < len(self._app_rows) else None
        selected = self.selection()
        owner = selected[0].task_id if selected else None
        archived = receipt is not None and receipt.task_id != owner
        active = not self.service._closed and not self.service.failure
        job = (
            self.service._connector_jobs.get((receipt.task_id, receipt.request_id))
            if receipt and not archived
            else None
        )
        try:
            review = (
                self.service.app_reviews.get(receipt.task_id, receipt.request_id)
                if receipt and not archived
                else None
            )
        except KeyError:
            review = None
        pending_review = review is not None and review.status == "pending"
        self.review_app_button.setEnabled(bool(active and pending_review))
        self.cancel_app_button.setEnabled(bool(active and (job or pending_review)))
        self.clear_app_result_button.setEnabled(
            bool(active and receipt and not receipt.result_cleared_at)
        )
        self.clear_task_results_button.setEnabled(
            bool(
                active
                and any(
                    r.task_id == owner and not r.result_cleared_at
                    for r in self._app_rows
                )
            )
        )
        can_reconcile = False
        if (
            active
            and receipt
            and receipt.state == "uncertain"
            and not any(task_id == owner for task_id, _ in self.service._connector_jobs)
        ):
            live = self.service._grants.get(owner)
            try:
                if live:
                    live[1].check(
                        "app",
                        connector=receipt.connector,
                        tool=receipt.tool,
                        effect="read" if archived else receipt.effect,
                        destination=receipt.destination,
                    )
                    definition = self.service.connectors.get(
                        receipt.connector, receipt.tool
                    )
                    can_reconcile = definition.reconcile is not None and not (
                        archived
                        and self.service.connector_store.probe_source(
                            receipt.task_id, receipt.request_id
                        )
                    )
            except (ValueError, PermissionError):
                pass
        self.reconcile_app_button.setEnabled(can_reconcile)
        self.reconcile_app_button.setToolTip(
            "Requires a fresh READ permission for this exact account, tool and destination."
            if archived
            else "Check the result without resending the original operation."
        )
        result_label = (
            receipt.reference
            or (
                "No saved reference"
                if receipt.result_cleared_at
                else "No confirmed reference"
            )
            if receipt
            else ""
        )
        text = (
            f"Operation: {receipt.effect.upper()} {receipt.connector}.{receipt.tool}\n"
            f"Destination: {receipt.destination}\n"
            f"State: {receipt.state}\nResult: {result_label}\n"
            + (
                f"Saved result cleared: {receipt.result_cleared_at}\n"
                if receipt.result_cleared_at
                else ""
            )
            + f"Reason: {receipt.reason or 'None'}\nOperation ID: {receipt.operation_id}\n"
            f"Draft ID: {receipt.artifact_id or 'Not used'}\n\n{receipt.text}"
            if receipt
            else ""
        )
        if self.app_preview.toPlainText() != text:
            self.app_preview.setPlainText(text)

    def clear_app_result(self, *, all_owned=False):
        selected = self.selection()
        row = self.apps.currentRow()
        if selected is None or (not all_owned and not 0 <= row < len(self._app_rows)):
            return
        task_id = selected[0].task_id if all_owned else self._app_rows[row].task_id
        ids = None if all_owned else [self._app_rows[row].request_id]
        subject = "this task's own app receipts" if all_owned else "this app receipt"
        if (
            QMessageBox.question(
                self,
                "Clear Saved App Results?",
                f"Clear saved provider text and links for {subject}, including shared receipt summaries? "
                "Later results for these receipts will not be saved. Outcomes, accounts, destinations "
                "and IDs are kept for recovery and duplicate-write protection. "
                "Provider data, drafts and results from separate checks are kept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.service.clear_app_results(task_id, ids)
        except Exception:
            QMessageBox.warning(
                self,
                "App Results",
                "Saved results could not be cleared. Review the task journal.",
            )
        self.refresh_activity()

    def perform_app(self, operation):
        index = self.apps.currentRow()
        if not 0 <= index < len(self._app_rows):
            return
        receipt = self._app_rows[index]
        selected = self.selection()
        if selected is None:
            return
        try:
            if operation == "cancel":
                self.service._cancel_app_job(receipt.task_id, receipt.request_id)
            elif receipt.task_id != selected[0].task_id:
                self.service._app_reconcile_history(
                    selected[0].task_id, receipt.request_id, uuid4().hex
                )
            else:
                self.service._app_reconcile(receipt.task_id, receipt.request_id)
        except Exception:
            QMessageBox.warning(
                self,
                "App Operation",
                "The operation or permission changed. Review its receipt before continuing.",
            )
        self.refresh_activity()

    def update_download_action(self):
        row = self.downloads.currentRow()
        record = (
            self._download_rows[row] if 0 <= row < len(self._download_rows) else None
        )
        selected = self.selection()
        self.cancel_download_button.setEnabled(
            bool(
                selected
                and not self.service._closed
                and not self.service.failure
                and record
                and self.service._sessions.get(record["session_id"])
                == selected[0].task_id
                and record["state"] in ("requested", "in_progress")
            )
        )

    def cancel_download(self):
        row = self.downloads.currentRow()
        selected = self.selection()
        if not selected or not 0 <= row < len(self._download_rows):
            return
        record = self._download_rows[row]
        try:
            self.service._download_cancel(
                selected[0].task_id, record["session_id"], record["download_id"]
            )
        except Exception:
            QMessageBox.warning(
                self, "Download", "The download changed. Review its current state."
            )
        self.refresh_activity()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)

    def perform(self, operation):
        selected = self.selection()
        if not selected:
            return
        task, grant = selected
        try:
            if operation == "cancel":
                self.service.cancel_task(task.task_id)
            elif grant:
                methods = {
                    "approve": self.service.approve_grant,
                    "deny": self.service.deny_grant,
                    "revoke": self.service.revoke_grant,
                }
                methods[operation](task.task_id, grant.grant_id)
        except ValueError as error:
            QMessageBox.warning(self, "Task Permissions", str(error))
        except (
            PermissionError,
            KeyError,
            RuntimeError,
            OSError,
            sqlite3.Error,
        ):
            QMessageBox.warning(
                self,
                "Task Permissions",
                "The task or permission changed, or its journal could not be saved. Review the current state before trying again.",
            )
        self.refresh()

    def review_app(self):
        index = self.apps.currentRow()
        if not 0 <= index < len(self._app_rows):
            return
        receipt = self._app_rows[index]
        from ui.app_review import AppReviewDialog

        try:
            review = self.service.app_reviews.get(receipt.task_id, receipt.request_id)
            task = self.service.store.get(receipt.task_id)
            dialog = AppReviewDialog(self.service, task, review, self)
            dialog.finished.connect(lambda _: dialog.deleteLater())
            dialog.show()
        except (KeyError, ValueError):
            return

    def watch_page(self):
        selected = self.selection()
        session_id = self.sessions.currentData()
        if not selected or not session_id:
            return
        try:
            self.service._watch(selected[0].task_id, session_id)
        except Exception:
            QMessageBox.warning(
                self,
                "Task Page",
                "The page could not be opened. Check its permission and current task state.",
            )
        else:
            self.accept()
