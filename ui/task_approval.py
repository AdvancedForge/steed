"""Automatic, nonmodal user approval for queued task permission requests."""

import sqlite3

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.task_recovery import TaskRecoveryDialog

ACTION_LABELS = {
    "visual": "See viewport screenshots, including visible form values and embedded content from other sites (images are returned to the agent, not saved in the task journal)",
    "live": "Control tabs you explicitly share, with normal JavaScript and background network activity (website handlers can change account data). Take Back ends access to a tab immediately.",
    "navigate": "Open links on these sites",
    "inspect": "See page addresses and titles",
    "read": "Read page text and control labels",
    "attach": "Show task pages in a browser view",
    "edit": "Change text, choices, checkboxes and radio buttons",
    "scroll": "Scroll task pages",
    "submit": "Submit forms on these sites (can change account data)",
    "download": "Save downloads to the exact files listed below (no overwriting)",
    "upload": "Send the listed local files to the listed upload sites",
    "artifact": "Save and read task notes/drafts here (kept after the task ends)",
    "workspace": "Create and read task files, including inputs you explicitly share; stage custom integration source (no host-folder access, execution or network permission)",
    "worker": "Request custom Python execution; each exact package needs separate approval (no direct network, host files or subprocesses)",
    "connector": "Propose new service connections; each exact recipe needs a separate review before installation (no account or network access granted here)",
    "app": "Use the exact app tools/destinations below (results kept here)",
}


def app_description(service, permission):
    try:
        definition = service.connectors.get(permission["connector"], permission["tool"])
        if permission["effect"] == "read" and definition.effect != "read":
            return "Check a previous result (read only): " + definition.description
        return definition.description
    except ValueError:
        return "App connection is no longer available."


class WrappedLabel(QLabel):
    """Keep complete wrapped text visible inside the scrollable scope."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(self.heightForWidth(event.size().width()))


class TaskApprovalDialog(QDialog):
    def __init__(self, service, task, grant, parent=None):
        super().__init__(parent)
        self.service, self.task_id, self.grant_id = (
            service,
            task.task_id,
            grant.grant_id,
        )
        self.setWindowTitle("Allow task access?")
        self.request_key = grant.grant_id
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(500, 560)
        outer = QVBoxLayout(self)
        objective = WrappedLabel("Task: " + task.objective[:256])
        objective.setTextFormat(Qt.TextFormat.PlainText)
        objective.setWordWrap(True)
        objective.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        outer.addWidget(objective)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        scope = grant.scope
        existing = scope["profile"] != "private"
        seconds = scope["duration_seconds"]
        duration = (
            f"{seconds // 60} minutes" if seconds % 60 == 0 else f"{seconds} seconds"
        )
        summary = WrappedLabel(
            "Profile: "
            + (scope["profile"].removeprefix("existing:") if existing else "Private")
            + "\n"
            + "\n".join(ACTION_LABELS[action] for action in scope["actions"])
            + f"\nAccess lasts {duration}."
        )
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        summary.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(summary)
        if "live" in scope["actions"]:
            titles = []
            for owner, grant_id, reference in service.live_tabs.offers.values():
                view = reference()
                if (
                    owner == task.task_id
                    and grant_id == grant.grant_id
                    and view is not None
                    and not sip.isdeleted(view)
                ):
                    titles.append(view.title()[:256] or "Untitled tab")
            selected = WrappedLabel(
                "Shared tabs: "
                + (
                    ", ".join(titles)
                    if titles
                    else "None yet. Share tabs through the agent sidebar; this approval alone does not expose ordinary tabs."
                )
            )
            selected.setTextFormat(Qt.TextFormat.PlainText)
            selected.setWordWrap(True)
            layout.addWidget(selected)
        if service.store.recovery(task.task_id)["status"] == "approved":
            recovery_notice = WrappedLabel(
                "Follow-up task: these are fresh permissions. Previously shared drafts/evidence do not restore old access or authorize replaying completed or uncertain writes."
            )
            recovery_notice.setWordWrap(True)
            layout.addWidget(recovery_notice)
        layout.addWidget(QLabel("Allowed sites:"))
        self.sites = QPlainTextEdit("\n".join(scope["origins"]) or "about:blank only")
        self.sites.setReadOnly(True)
        self.sites.setMaximumHeight(100)
        layout.addWidget(self.sites)
        if scope.get("download_destinations"):
            destinations = QPlainTextEdit("\n".join(scope["download_destinations"]))
            destinations.setReadOnly(True)
            destinations.setMaximumHeight(100)
            layout.addWidget(QLabel("Allowed download files:"))
            layout.addWidget(destinations)
            size = scope["max_download_bytes"]
            layout.addWidget(QLabel(f"Each download is limited to {size:,} bytes."))
        if scope.get("upload_sources"):
            layout.addWidget(QLabel("Local files allowed for upload:"))
            files = QPlainTextEdit("\n".join(scope["upload_sources"]))
            files.setReadOnly(True)
            files.setMaximumHeight(80)
            layout.addWidget(files)
            sites = QPlainTextEdit("\n".join(scope["upload_origins"]))
            sites.setReadOnly(True)
            sites.setMaximumHeight(70)
            layout.addWidget(QLabel("Allowed upload sites:"))
            layout.addWidget(sites)
            layout.addWidget(
                QLabel(f"Up to {scope['max_upload_bytes']:,} staged bytes per page.")
            )
        if "worker" in scope["actions"]:
            worker_notice = WrappedLabel(
                "Python standard library only. Each run: up to 20 seconds, 10 CPU "
                "seconds and 512 MiB memory; at most two workers at once. Code can "
                "use only the file/draft/app operations separately listed in this request."
            )
            worker_notice.setWordWrap(True)
            layout.addWidget(worker_notice)
        if scope.get("connector_permissions"):
            layout.addWidget(QLabel("Allowed app operations:"))
            apps = QPlainTextEdit(
                "\n\n".join(
                    f"{p['effect'].upper()}: {app_description(service, p)}"
                    + f"\nDestination: {p['destination']}"
                    for p in scope["connector_permissions"]
                )
            )
            apps.setReadOnly(True)
            apps.setMaximumHeight(180)
            layout.addWidget(apps)
            app_notice = WrappedLabel(
                "Read-only app access to the listed tools and destinations."
                if all(p["effect"] == "read" for p in scope["connector_permissions"])
                else "App tools may send drafts or change external data. Only listed "
                "destinations are allowed. Dispatched writes can still happen after cancellation."
            )
            app_notice.setWordWrap(True)
            layout.addWidget(app_notice)
        notice = WrappedLabel(
            (
                "Uses your existing sign-in and site storage. You can keep browsing. "
                "Site changes persist and may affect your signed-in session. "
                if existing
                else "Uses a separate private session. "
            )
            + (
                "These sites may reach private network addresses. "
                if scope["allow_private_network"]
                else ""
            )
            + (
                "Live tabs keep normal scripts and background requests. "
                if "live" in scope["actions"]
                else "Task scripts are unavailable. "
            )
            + (
                "Forms send their current fields, including hidden site tokens. "
                + (
                    "Password, multi-file and folder forms are unavailable. "
                    if "upload" in scope["actions"]
                    else "Password and file forms are unavailable. "
                )
                if "submit" in scope["actions"]
                else ""
            )
            + "You can revoke access in File → Task Permissions."
        )
        notice.setWordWrap(True)
        notice.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(notice)
        more = QPushButton("Show limits")
        self.limits = WrappedLabel(
            f"Up to {scope['max_operations']} operations and {scope['max_sessions']} sessions.\n"
            + (
                "Live website background requests are not filtered or counted.\n"
                if "live" in scope["actions"]
                else f"Up to {scope['max_requests']} network requests. GET/HEAD can have website effects. DNS routes are not pinned.\n"
            )
            + "Form values and editable text are omitted from page reads."
            + (
                "\nApp tool identifiers:\n"
                + "\n".join(
                    f"{p['connector']}.{p['tool']}"
                    for p in scope["connector_permissions"]
                )
                if scope.get("connector_permissions")
                else ""
            )
        )
        self.limits.setWordWrap(True)
        self.limits.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self.limits.hide()
        more.clicked.connect(
            lambda: self.limits.setVisible(not self.limits.isVisible())
        )
        layout.addWidget(more)
        layout.addWidget(self.limits)
        self.error = QLabel()
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        self.remember = QComboBox()
        self.remember.addItem("Allow once", "once")
        self.remember.addItem("Allow every time", "global")
        self.remember.addItem("Allow every time on this profile", "profile")
        if not existing:
            self.remember.model().item(2).setEnabled(False)
        self.remember_notice = QLabel()
        self.remember_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.remember_notice.setWordWrap(True)
        self.remember.currentIndexChanged.connect(self.update_remember_notice)
        outer.addWidget(self.remember)
        outer.addWidget(self.remember_notice)
        self.update_remember_notice()
        buttons = QHBoxLayout()
        self.deny_button = QPushButton("Deny")
        self.approve_button = QPushButton("Approve")
        # Enter should never silently approve an unexpected popup.
        self.deny_button.setDefault(True)
        self.approve_button.setAutoDefault(False)
        self.deny_button.clicked.connect(lambda: self.decide(False))
        self.approve_button.clicked.connect(lambda: self.decide(True))
        buttons.addWidget(self.deny_button)
        buttons.addWidget(self.approve_button)
        outer.addLayout(buttons)

    def update_remember_notice(self):
        mode = self.remember.currentData()
        if mode == "once":
            message = "Approve this task only. Private tasks cannot save a profile-specific approval."
        else:
            where = (
                "all profiles, including private tasks"
                if mode == "global"
                else "this named profile only"
            )
            message = (
                f"Automatically approve matching requests from any connected agent for {where}. "
                "Sites, actions, files, app accounts/destinations and limits stay within this request. "
                "Each task gets its own duration and budgets. "
                "Remove saved approvals in Task Permissions."
            )
        self.remember_notice.setText(message)

    def decide(self, approve):
        try:
            if approve:
                self.service.approve_grant(
                    self.task_id, self.grant_id, remember=self.remember.currentData()
                )
            else:
                self.service.deny_grant(self.task_id, self.grant_id)
        except (
            ValueError,
            PermissionError,
            KeyError,
            RuntimeError,
            OSError,
            sqlite3.Error,
        ):
            self.error.setText(
                "Access could not be saved. Review the request and try again."
            )
            return
        self.done(QDialog.DialogCode.Accepted)


class TaskApprovalQueue(QObject):
    def __init__(self, registry, service):
        super().__init__(registry)
        self.registry, self.service = registry, service
        self.dialog = None
        self.deferred = set()
        self.scheduled = False
        service.changed.connect(self.schedule)
        self.schedule()

    def schedule(self):
        if not self.scheduled:
            self.scheduled = True
            QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self.scheduled = False
        pending = []
        if not self.service._closed and not self.service.failure:
            try:
                for task in self.service.store.list_tasks():
                    if (
                        self.service.store.recovery(task.task_id)["status"]
                        == "requested"
                    ):
                        pending.append((task, None))
                    pending.extend(
                        (task, grant)
                        for grant in self.service.store.grants(task.task_id)
                        if grant.status == "requested"
                    )
                    pending.extend(
                        (task, review)
                        for review in self.service.app_reviews.pending(task.task_id)
                    )
                    pending.extend(
                        (task, receipt)
                        for receipt in self.service.workers.store.pending(task.task_id)
                    )
                    pending.extend(
                        (task, proposal)
                        for proposal in self.service.recipes.pending(task.task_id)
                    )
                    pending.extend(
                        (task, proposal)
                        for proposal in self.service.layouts.pending(task.task_id)
                    )
                    pending.extend(
                        (task, proposal)
                        for proposal in self.service.data_panels.pending(task.task_id)
                    )
            except (sqlite3.Error, OSError, ValueError):
                self.service._fatal()
        if self.dialog is not None and not sip.isdeleted(self.dialog):
            if any(
                self.request_key(task, grant) == self.dialog.request_key
                for task, grant in pending
            ):
                return
            self.dialog.done(QDialog.DialogCode.Accepted)
            return
        windows = [
            owner.parent()
            for owner in self.registry.owners
            if owner.parent() is not None
            and not getattr(owner.parent(), "_closing", False)
        ]
        if not windows:
            return
        parent = QApplication.activeWindow()
        if parent not in windows:
            parent = windows[0]
        for task, grant in reversed(pending):
            key = self.request_key(task, grant)
            if key in self.deferred:
                continue
            from services.connector_reviews import AppReview
            from services.data_panels import PanelReceipt
            from services.layout_proposals import LayoutProposal
            from services.recipe_proposals import RecipeProposal
            from services.worker_store import WorkerReceipt
            from ui.app_review import AppReviewDialog
            from ui.data_panels import DataPanelReviewDialog
            from ui.layout_review import LayoutReviewDialog
            from ui.recipe_review import RecipeReviewDialog
            from ui.worker_approval import WorkerApprovalDialog

            if (
                isinstance(grant, PanelReceipt)
                and parent.profile_manager.is_search_profile()
            ):
                normal = [
                    w for w in windows if not w.profile_manager.is_search_profile()
                ]
                if not normal:
                    self.deferred.add(key)
                    continue
                parent = normal[0]
            try:
                dialog = (
                    DataPanelReviewDialog(self.service, grant, parent)
                    if isinstance(grant, PanelReceipt)
                    else LayoutReviewDialog(
                        self.registry,
                        self.service.layouts.definition(grant),
                        parent,
                        service=self.service,
                        proposal=grant,
                    )
                    if isinstance(grant, LayoutProposal)
                    else RecipeReviewDialog(
                        self.service.recipes.connections,
                        self.service.recipes.recipe(grant),
                        parent,
                        service=self.service,
                        proposal=grant,
                    )
                    if isinstance(grant, RecipeProposal)
                    else AppReviewDialog(self.service, task, grant, parent)
                    if isinstance(grant, AppReview)
                    else WorkerApprovalDialog(self.service, task, grant, parent)
                    if isinstance(grant, WorkerReceipt)
                    else (
                        TaskApprovalDialog(self.service, task, grant, parent)
                        if grant
                        else TaskRecoveryDialog(self.service, task, parent)
                    )
                )
            except (ValueError, KeyError):
                # Unavailable source/review remains manageable through trusted UI.
                self.deferred.add(key)
                continue
            except (OSError, sqlite3.Error):
                self.service._fatal()
                return
            self.dialog = dialog
            dialog.finished.connect(lambda result, gid=key: self._finished(result, gid))
            dialog.show()
            return

    def request_key(self, task, item):
        from services.connector_reviews import AppReview
        from services.data_panels import PanelReceipt
        from services.layout_proposals import LayoutProposal
        from services.recipe_proposals import RecipeProposal
        from services.worker_store import WorkerReceipt

        return (
            item.request_key
            if isinstance(
                item,
                (
                    AppReview,
                    WorkerReceipt,
                    RecipeProposal,
                    LayoutProposal,
                    PanelReceipt,
                ),
            )
            else item.grant_id
            if item
            else "recovery:" + task.task_id
        )

    def _finished(self, result, grant_id):
        # Closing/Escape leaves a request pending in the management dialog.
        if result == QDialog.DialogCode.Rejected:
            self.deferred.add(grant_id)
        self.dialog = None
        self.schedule()
