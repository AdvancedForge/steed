"""Showing the person what an agent handed them (browser.ask_person).

The page comes to the front with a bar above it: what the agent needs, in its
own short words, with Done and "I can't". A task page that is not one of the
person's tabs opens in the watch window instead, already in their control;
Return to Agent there counts as Done. A desktop notification says so when
Steed is not in front.
"""

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from services.handoff import REASONS


class HandoffBar(QFrame):
    """ "Claude Code needs you to solve a check…" above the handed-over tab."""

    def __init__(self, window):
        super().__init__(window)
        self.browser = window
        self.setObjectName("PasswordBar")  # Same look as Steed's other bars.
        self.setAccessibleName("Agent needs you")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.label, 1)
        self.done = QPushButton("Done, hand back", self)
        self.done.setProperty("primary", True)
        self.cannot = QPushButton("I can't", self)
        for button in (self.done, self.cannot):
            button.setAutoDefault(False)
            row.addWidget(button)
        self.done.clicked.connect(lambda: self._finish(True))
        self.cannot.clicked.connect(lambda: self._finish(False))
        self.entries = []  # (entry, view, service) waiting in this window
        self.hide()

    def add(self, entry, view, service, text):
        self.entries.append((entry, view, service, text))
        self.tab_changed()

    def remove(self, request_id):
        self.entries = [e for e in self.entries if e[0]["request_id"] != request_id]
        self.tab_changed()

    def _current(self):
        if self.browser._closing or not self.browser.tabs:
            return None
        tab = self.browser.tabs[self.browser.current_tab_index]
        for item in self.entries:
            if not sip.isdeleted(item[1]) and item[1] is tab:
                return item
        return None

    def tab_changed(self):
        item = self._current()
        if item is None:
            self.hide()
            return
        self.label.setText(item[3])
        self.show()

    def _finish(self, done):
        item = self._current()
        if item is None:
            return
        entry, _, service, _ = item
        try:
            service.handoffs.finish(entry["task_id"], entry["request_id"], done=done)
        except Exception as error:  # The page is busy loading, or it ended.
            self.browser.status_bar.showMessage(str(error), 5000)
            return
        self.remove(entry["request_id"])


class HandoffPrompt(QObject):
    """One per task service: brings handed-over pages to the person."""

    def __init__(self, registry, service):
        super().__init__(service)
        self.registry = registry
        self.service = service
        service.handoffs.asked.connect(self._asked)
        service.handoffs.ended.connect(self._ended)

    def _agent(self, task_id):
        try:
            return self.registry.agent_providers.agent_label_for(task_id)
        except Exception:
            return "Your agent"

    def text(self, entry):
        need = (
            f"{self._agent(entry['task_id'])} needs you to {REASONS[entry['reason']]}"
        )
        said = f": “{entry['message']}”" if entry["message"] else "."
        where = (
            f" It's ringed on the page ({entry['target']})."
            if entry.get("target")
            else ""
        )
        return f"{need}{said}{where} Do it on this page, then hand it back."

    def _asked(self, entry):
        session = self.service.backend._session(entry["session_id"])
        view = session.borrowed_view
        window = getattr(view, "browser_window", None) if view is not None else None
        if window is not None and not window._closing and view in window.tabs:
            window._switch_tab(window.tabs.index(view))
            window.handoff_bar.add(entry, view, self.service, self.text(entry))
            if window.isMinimized():
                window.showNormal()
        else:
            window = self._watch(entry)
        self._notify(entry, window)

    def _watch(self, entry):
        """Show a task page in the watch window, already in the person's hands."""
        from ui.task_watch import TaskWatchDialog

        service, session_id = self.service, entry["session_id"]
        dialog = service._watchers.get(session_id)
        if dialog is None:
            view = service.backend.attach_view(session_id)
            parent = next(
                (
                    o.parent()
                    for o in self.registry.owners
                    if getattr(o.parent(), "browser_window_id", None)
                    == self.registry.agent_context().get("window_id")
                ),
                None,
            )
            dialog = TaskWatchDialog(
                service, entry["task_id"], session_id, view, parent
            )
            service._watchers[session_id] = dialog
        # Its Return to Agent hands this request back.
        dialog.control_key = entry["key"]
        dialog.handoff_text = self.text(entry)
        dialog.refresh_state()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog.parent()

    def _notify(self, entry, window):
        config = self.registry.config
        if not config.get("agent_notifications", True):
            return
        if window is not None and window.isActiveWindow():
            return  # The bar or watch window is already in front of them.
        try:
            from ui.desktop_notify import DesktopNotifier

            notifier = getattr(self.registry, "_agent_notifier", None)
            if notifier is None:
                notifier = self.registry._agent_notifier = DesktopNotifier(
                    self.registry
                )

            def bring_up():
                if window is not None and not sip.isdeleted(window):
                    window.showNormal()
                    window.raise_()
                    window.activateWindow()

            notifier.notify(
                f"{self._agent(entry['task_id'])} needs you",
                entry["message"] or REASONS[entry["reason"]].capitalize() + ".",
                on_click=bring_up,
            )
        except Exception:
            pass  # No notification service: the bar still shows.

    def _ended(self, entry):
        for owner in self.registry.owners:
            window = owner.parent()
            bar = getattr(window, "handoff_bar", None)
            if bar is not None and not sip.isdeleted(bar):
                bar.remove(entry["request_id"])
        dialog = self.service._watchers.get(entry["session_id"])
        if dialog is not None and not sip.isdeleted(dialog):
            dialog.handoff_text = ""
