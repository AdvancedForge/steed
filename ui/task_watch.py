"""Read-only task viewing and trusted, scoped control handoff."""

from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class InputShield(QObject):
    """The engine view renders only; all changes go through scoped controls."""

    BLOCKED = frozenset(
        getattr(QEvent.Type, name)
        for name in (
            "MouseButtonPress",
            "MouseButtonRelease",
            "MouseButtonDblClick",
            "MouseMove",
            "KeyPress",
            "KeyRelease",
            "ShortcutOverride",
            "InputMethod",
            "Wheel",
            "ContextMenu",
            "DragEnter",
            "DragMove",
            "Drop",
            "TouchBegin",
            "TouchUpdate",
            "TouchEnd",
            "TabletPress",
            "TabletMove",
            "TabletRelease",
            "NativeGesture",
        )
    )

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.enabled = True
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, target, event):
        if (
            self.enabled
            and event.type() in self.BLOCKED
            and isinstance(target, QWidget)
            and not sip.isdeleted(self.view)
            and (target is self.view or self.view.isAncestorOf(target))
        ):
            event.accept()
            return True
        return False

    def setEnabled(self, enabled):
        self.enabled = enabled
        self.view.setFocusPolicy(
            Qt.FocusPolicy.NoFocus if enabled else Qt.FocusPolicy.StrongFocus
        )
        if enabled:
            self.view.clearFocus()


class TaskWatchDialog(QDialog):
    def __init__(self, service, task_id, session_id, view, parent=None):
        super().__init__(parent)
        self.service, self.task_id, self.session_id = service, task_id, session_id
        self.view, self.control_key = view, uuid4().hex
        self._ended = self._closing = self._busy = False
        self.snapshot = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Task page")
        self.resize(1000, 750)
        layout = QVBoxLayout(self)
        self.objective = QLabel("Task: " + service.store.get(task_id).objective[:256])
        self.objective.setTextFormat(Qt.TextFormat.PlainText)
        self.objective.setWordWrap(True)
        layout.addWidget(self.objective)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        layout.addWidget(self.address)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        layout.addWidget(view, 1)
        top = QHBoxLayout()
        self.take_button = QPushButton("Take Control")
        self.return_button = QPushButton("Return to Agent")
        self.cancel_button = QPushButton("Cancel Task")
        self.take_button.clicked.connect(self.take_control)
        self.return_button.clicked.connect(self.return_control)
        self.cancel_button.clicked.connect(self.cancel_task)
        for button in (self.take_button, self.return_button, self.cancel_button):
            top.addWidget(button)
        layout.addLayout(top)
        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        self.targets = QComboBox()
        self.targets.currentIndexChanged.connect(self.update_actions)
        controls.addWidget(QLabel("Choose a link or field, then use its controls:"))
        controls.addWidget(self.targets)
        values = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Text to enter")
        self.choice = QSpinBox()
        self.choice.setRange(1, 1000)
        self.choice.setPrefix("Choice ")
        values.addWidget(self.text)
        values.addWidget(self.choice)
        controls.addLayout(values)
        self.sources = QComboBox()
        live = service._grants.get(task_id)
        if live:
            for source in live[1].scope.upload_sources:
                self.sources.addItem(source, source)
        controls.addWidget(self.sources)
        actions = QHBoxLayout()
        self.action_buttons = {}
        for operation, label in (
            ("navigate", "Open Link"),
            ("enter", "Fill Field"),
            ("activate", "Toggle"),
            ("select", "Choose Option"),
            ("submit", "Submit Form"),
            ("upload", "Select File"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, op=operation: self.act(op))
            actions.addWidget(button)
            self.action_buttons[operation] = button
        controls.addLayout(actions)
        extra = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh Controls")
        self.refresh_button.clicked.connect(self.refresh_controls)
        extra.addWidget(self.refresh_button)
        self.scroll_buttons = []
        for label, dy in (("Scroll Up", -500), ("Scroll Down", 500)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, offset=dy: self.scroll(offset))
            extra.addWidget(button)
            self.scroll_buttons.append(button)
        controls.addLayout(extra)
        controls.addWidget(
            QLabel(
                "Approved sites and actions still apply. Use the live page directly while you have control."
                if service.backend.inspect(session_id).mode == "live"
                else "Approved files, sites and actions still apply. Scripts, password/multi-file forms and custom controls are unavailable."
            )
        )
        layout.addWidget(self.controls)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh_state)
        self.timer.start()

        def destroyed():
            if not sip.isdeleted(service):
                service.close_watch(task_id, session_id, self.control_key)

        self.destroyed.connect(destroyed)
        self.refresh_state()

    def owns_control(self):
        return self.service._controls.get(self.session_id) == self.control_key

    def refresh_state(self):
        if self._ended or self._closing:
            return
        if (
            self.service._closed
            or self.service.failure
            or self.session_id not in self.service._sessions
        ):
            self.session_ended()
            return
        state = self.service.backend.inspect(self.session_id)
        self.address.setText(state.url)
        owned = self.owns_control()
        self.notice.setText(
            "You have control. Use this live page directly; the agent is paused until you return control."
            if owned and state.mode == "live"
            else "You have control. The agent is paused on this page. Use the controls below; return it when ready."
            if owned
            else "Watching the agent's page. Take control to use the supported links and fields below."
        )
        self.take_button.setEnabled(not owned and not self._busy and not state.loading)
        self.return_button.setEnabled(owned and not self._busy and not state.loading)
        self.controls.setVisible(owned)
        self.update_actions()

    def take_control(self):
        old_key = self.control_key
        self.control_key = uuid4().hex
        try:
            self.service.take_control(self.task_id, self.session_id, self.control_key)
        except Exception as error:
            self.control_key = old_key
            self.show_error(error)
        else:
            self.refresh_controls()
        self.refresh_state()

    def return_control(self):
        try:
            self.service.return_control(self.task_id, self.session_id, self.control_key)
        except Exception as error:
            self.show_error(error)
        else:
            self.snapshot = None
            self.targets.clear()
            self.text.clear()
            self.error.clear()
        self.refresh_state()

    def cancel_task(self):
        try:
            self.service.cancel_task(self.task_id)
        except Exception as error:
            self.show_error(error)

    def show_error(self, error):
        code = getattr(error, "code", "operation_failed")
        self.error.setText(
            {
                "session_busy": "Wait for the current page operation to finish, then try again.",
                "action_out_of_scope": "This action needs permission. Ask the agent to request it.",
                "stale_snapshot": "The page changed. Refresh the controls before continuing.",
                "page_not_ready": "The page is still loading. Refresh the controls when it finishes.",
            }.get(
                code,
                "The action could not finish. Check the task's permissions or refresh its controls.",
            )
        )

    def command(self, command, arguments, consume):
        if self._ended or self._busy or not self.owns_control():
            return
        self._busy = True
        self.error.clear()
        future = self.service.broker.submit(
            "user.command",
            task_id=self.task_id,
            session_id=self.session_id,
            key=self.control_key,
            operation_name=command,
            arguments=arguments,
        )

        def completed(source):
            if self._closing or self._ended or sip.isdeleted(self):
                return
            self._busy = False
            try:
                consume(source.result())
            except Exception as error:
                self.show_error(error)
            self.refresh_state()

        future.add_done_callback(completed)
        self.refresh_state()

    def refresh_controls(self):
        def received(snapshot):
            self.snapshot = snapshot
            self.targets.clear()
            for frame in snapshot.frames:
                for target in frame.targets:
                    if target.enabled and not target.read_only and target.operations:
                        self.targets.addItem(
                            f"{target.label[:160] or 'Unnamed'} ({target.kind})", target
                        )

        self.command("page.inspect", {}, received)

    def update_actions(self):
        target = self.targets.currentData()
        available = self.owns_control() and not self._busy and self.snapshot is not None
        live = self.service._grants.get(self.task_id)
        allowed = set(live[1].scope.actions) if live else set()
        for operation, button in self.action_buttons.items():
            needed = (
                "navigate"
                if operation == "navigate"
                else "submit"
                if operation == "submit"
                else "upload"
                if operation == "upload"
                else "edit"
            )
            button.setEnabled(
                bool(
                    available
                    and target
                    and operation in target.operations
                    and needed in allowed
                )
            )
        self.text.setEnabled(
            bool(
                available
                and target
                and "enter" in target.operations
                and "edit" in allowed
            )
        )
        self.choice.setEnabled(
            bool(
                available
                and target
                and "select" in target.operations
                and "edit" in allowed
            )
        )
        self.sources.setVisible(bool(target and "upload" in target.operations))
        self.sources.setEnabled(
            bool(
                available
                and target
                and "upload" in target.operations
                and "upload" in allowed
            )
        )
        self.refresh_button.setEnabled(self.owns_control() and not self._busy)
        for button in self.scroll_buttons:
            button.setEnabled(available and "scroll" in allowed)

    def acted(self, _):
        self.snapshot = None
        self.targets.clear()
        self.text.clear()
        self.refresh_controls()

    def act(self, operation):
        target = self.targets.currentData()
        if self.snapshot is None or target is None:
            return
        arguments = {
            "snapshot_id": self.snapshot.snapshot_id,
            "target_id": target.target_id,
        }
        if operation in ("navigate", "submit", "upload"):
            command = "page." + operation
            if operation == "upload":
                arguments["source"] = self.sources.currentData()
        else:
            command = "page.act"
            arguments["operation"] = operation
            if operation == "enter":
                arguments["text"] = self.text.text()
            elif operation == "select":
                arguments["index"] = self.choice.value() - 1
        self.command(command, arguments, self.acted)

    def scroll(self, dy):
        if self.snapshot is not None:
            self.command(
                "page.scroll",
                {"snapshot_id": self.snapshot.snapshot_id, "dy": dy, "dx": 0},
                self.acted,
            )

    def session_ended(self):
        if self._ended:
            return
        self._ended = True
        self.timer.stop()
        self.text.clear()
        self.close()

    def prepare_close(self):
        if self._closing:
            return
        self._closing = True
        self.timer.stop()
        self.text.clear()
        self.service.close_watch(self.task_id, self.session_id, self.control_key)

    def done(self, result):
        self.prepare_close()
        super().done(result)

    def closeEvent(self, event):
        self.prepare_close()
        # Qt's view does not own this page. Closing the watch leaves the task
        # page alive; service teardown closes the view before its engine profile.
        event.accept()
