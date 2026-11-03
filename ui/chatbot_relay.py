"""Explicit bidirectional website-chat exchanges on Steed's maintained HTML surface."""

import sqlite3

from PyQt6 import sip
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import webengine
from services.chatbot_protocol import RelayError, encode
from services.chatbot_relay import ChatbotRelay
from ui.html_dialog import HtmlSettingsDialog
from ui.web_chat import FOCUS_COMPOSER


class ChatbotRelayDialog(HtmlSettingsDialog):
    def __init__(self, window, target=None):
        super().__init__(window)
        self.window = window
        self.profile = window.profile_manager.current_profile_name
        self.runner = self.target = self.target_page = None
        self.destination = ""
        self._closed = self._paused = self._pasting = False
        self._proposal = None
        self._outbound_exchange = None
        self.setWindowTitle("Chatbot relay")
        self.resize(820, 760)
        window.profile_manager.profile_changed.connect(self._profile_changed)
        layout = QVBoxLayout(self)
        self.status = QLabel("Choose a task and how to exchange structured messages.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        if self.profile == "search":
            self.status.setText(
                "Switch to a regular profile to start a chatbot relay. Private task sessions remain available through approved tools."
            )
        self.status.setProperty("htmlStatus", True)
        layout.addWidget(self.status)
        self.binding = QLabel("Profile: " + self.profile + " · Relay not started")
        self.binding.setTextFormat(Qt.TextFormat.PlainText)
        self.binding.setWordWrap(True)
        layout.addWidget(self.binding)
        self.pages = QTabWidget()
        layout.addWidget(self.pages)
        setup = QWidget()
        form = QVBoxLayout(setup)
        note = QLabel(
            "Steed sends JSON instructions and tool schemas; the chatbot returns JSON requests or an answer. "
            "Review both directions. Task permissions still apply. This first relay uses text only."
        )
        note.setWordWrap(True)
        form.addWidget(note)
        form.addWidget(QLabel("Profile: " + self.profile))
        self.goal = self._editor(form, "Task for the chatbot")
        self.mode = QComboBox()
        self.mode.addItems(["Copy/paste JSON manually", "Enabled chat tab"])
        self.mode.setCurrentIndex(1 if target is not None else 0)
        form.addWidget(self.mode)
        self.targets = QComboBox()
        for view in window.tabs:
            url = view.url()
            if (
                url.scheme() == "https"
                and not url.userName()
                and not url.password()
                and not getattr(view, "agent_panel", False)
                and not getattr(view, "native_panel", False)
                and not getattr(view, "workspace_name", None)
            ):
                self.targets.addItem(url.toDisplayString(), view)
                if view is target:
                    self.targets.setCurrentIndex(self.targets.count() - 1)
        form.addWidget(self.targets)
        self.consent = QCheckBox(
            "Enable selected-response reads and reviewed-message pastes for this exact chat tab."
        )
        form.addWidget(self.consent)
        self.start = QPushButton("Start relay task")
        self.start.clicked.connect(self.start_task)
        form.addWidget(self.start)
        self.pages.addTab(setup, "1 · Setup")
        exchange = QWidget()
        form = QVBoxLayout(exchange)
        self.catalogue = QCheckBox("Include tool schemas (needed in the first message)")
        self.catalogue.setChecked(True)
        form.addWidget(self.catalogue)
        self.prepare = self._button(
            form, "Prepare outbound message", self.prepare_message
        )
        self.outbound = self._editor(
            form, "Exact outbound JSON · review before sharing", True
        )
        row = QHBoxLayout()
        self.copy = self._button(row, "Copy reviewed message", self.copy_message)
        self.paste = self._button(row, "Paste reviewed message", self.paste_message)
        form.addLayout(row)
        self.rearm = self._button(
            form, "Enable this initial conversation", self.rearm_target
        )
        self.read = self._button(form, "Read selected response", self.read_response)
        self.response = self._editor(form, "Chatbot response JSON")
        self.response.textChanged.connect(self._invalidate_review)
        self.review = self._button(form, "Review response", self.review_response)
        self.preview = self._editor(form, "Exact reviewed requests / answer", True)
        self.run = self._button(form, "Run reviewed requests", self.run_response)
        self.pages.addTab(exchange, "2 · Exchange")
        results = QWidget()
        form = QVBoxLayout(results)
        self.results = self._editor(
            form, "Tool results · included in the next prepared message", True
        )
        self.pages.addTab(results, "3 · Results")
        row = QHBoxLayout()
        self.details = self._button(
            row, "Task permissions / details…", self.task_details
        )
        self.stop = self._button(row, "Stop relay", self.stop_task)
        self._button(row, "Close", self.reject)
        layout.addLayout(row)
        self._refresh()

    @staticmethod
    def _editor(layout, label, readonly=False):
        layout.addWidget(QLabel(label))
        editor = QPlainTextEdit()
        editor.setAccessibleName(label)
        editor.setReadOnly(readonly)
        layout.addWidget(editor)
        return editor

    @staticmethod
    def _button(layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    @staticmethod
    def _address(url):
        return url.adjusted(QUrl.UrlFormattingOption.RemoveFragment).toString()

    def _guard(self):
        if (
            self._closed
            or self.window._closing
            or self.window.profile_manager.current_profile_name != self.profile
            or self.profile == "search"
        ):
            raise RelayError(
                "profile_changed", "The relay profile is no longer available."
            )
        if self.target is not None and (
            sip.isdeleted(self.target)
            or self.target not in self.window.tabs
            or self.target.page() is not self.target_page
            or self._paused
            or self._address(self.target.url()) != self.destination
        ):
            raise RelayError(
                "destination_changed", "The enabled chat destination changed."
            )

    def _attempt(self, action):
        try:
            action()
        except sqlite3.Error:
            self.window.profile_manager.registry.tasks._fatal()
            self.status.setText(
                "Task storage failed. No relay request will be retried."
            )
        except (ValueError, RuntimeError, OSError) as error:
            self.status.setText(
                str(error)
                if isinstance(error, RelayError)
                else "Relay unavailable. Check task details and start a fresh task."
            )
            if self.runner is not None and self.runner.response_feedback:
                self.status.setText(
                    self.status.text()
                    + " Prepare outbound message to share structured validation feedback."
                )
        self._refresh()

    def start_task(self):
        def start():
            if self.runner is not None and not self.runner.stopped:
                return
            self._unbind()
            self._paused = False
            if self.mode.currentIndex() == 1:
                view = self.targets.currentData()
                if not self.consent.isChecked() or view is None or sip.isdeleted(view):
                    raise RelayError(
                        "consent_required",
                        "Select a chat tab and explicitly enable it first.",
                    )
                url = view.url()
                if (
                    url.scheme() != "https"
                    or url.userName()
                    or url.password()
                    or view.is_loading
                ):
                    raise RelayError(
                        "invalid_target",
                        "Use a loaded HTTPS chat tab without credentials in its address.",
                    )
                self.target, self.target_page = view, view.page()
                self.destination = self._address(url)
                view.loadStarted.connect(self._navigation)
                view.loadFinished.connect(self._loaded)
                view.urlChanged.connect(self._url_changed)
                view.destroyed.connect(self._target_destroyed)
            self.runner = ChatbotRelay(
                self.window.profile_manager.registry.tasks,
                self.goal.toPlainText(),
                self._guard,
                mode="tab" if self.target else "manual",
                parent=self,
            )
            self.runner.changed.connect(self._runner_changed)
            self._update_binding()
            self.status.setText(self.runner.status)
            self.catalogue.setChecked(True)
            self.pages.setCurrentIndex(1)
            self.prepare_message()

        self._attempt(start)

    def _invalidate_review(self):
        self._proposal = None
        self.preview.clear()
        self.run.setEnabled(False)

    def _update_binding(self):
        self.binding.setText(
            "Profile: "
            + self.profile
            + " · "
            + (
                "Enabled chat: " + self.destination
                if self.target
                else "Manual exchange"
            )
            + (" · Task: " + self.runner.task_id if self.runner else "")
        )

    def prepare_message(self):
        def prepare():
            self._guard()
            if self.runner is None:
                return
            text = self.runner.outbound(include_catalogue=self.catalogue.isChecked())
            self.outbound.setPlainText(text)
            self._outbound_exchange = self.runner.exchange_id
            self.status.setText(
                "Review this JSON, then copy or paste it. Send it in the chatbot yourself."
            )

        self._attempt(prepare)

    def _sharing(self):
        self._guard()
        if (
            self.runner is None
            or self.runner.stopped
            or self.runner.busy
            or self._outbound_exchange != self.runner.exchange_id
            or not self.outbound.toPlainText()
        ):
            raise RelayError(
                "stale_exchange", "Prepare and review the current message first."
            )

    def copy_message(self):
        def copy():
            self._sharing()
            QApplication.clipboard().setText(self.outbound.toPlainText())
            self.runner.exported(self._outbound_exchange)
            self.status.setText(
                "Reviewed JSON copied. Paste and send it to your chatbot."
            )

        self._attempt(copy)

    def paste_message(self):
        def paste():
            self._sharing()
            if self.target is None or self._pasting:
                return
            self.window._take_back_tab(self.target)
            if (
                not self.window.web_chat_dock
                or self.window.web_chat_dock.view is not self.target
            ):
                self.window._switch_tab(self.window.tabs.index(self.target))
            self._pasting = True
            self.hide()
            self.target.setFocus()
            webengine.run_script(
                self.target_page,
                FOCUS_COMPOSER,
                self._focused,
                world=webengine.STEED_WORLD,
            )

        self._attempt(paste)

    def _focused(self, focused):
        if sip.isdeleted(self) or not self._pasting or self._closed:
            return
        self._pasting = False

        def paste():
            self._sharing()
            if focused is not True:
                raise RelayError(
                    "composer_unavailable",
                    "Couldn't identify one visible composer. Copy the reviewed message and paste into your chosen field.",
                )
            QApplication.clipboard().setText(self.outbound.toPlainText())
            webengine.trigger(self.target_page, "paste")
            self.runner.exported(self._outbound_exchange)
            self.status.setText(
                "JSON pasted. Review and send it in the website; Steed does not press Send."
            )

        self._attempt(paste)
        self.show()

    def read_response(self):
        def read():
            self._guard()
            if self.target is None:
                return
            text = self.target_page.selectedText()
            if not text or len(text.encode("utf-8")) > 65536:
                raise RelayError(
                    "selection_required",
                    "Select only the complete chatbot JSON response (at most 64 KiB), then try again.",
                )
            self._guard()
            self.response.setPlainText(text)
            self.status.setText(
                "Selected text loaded. Review it before running any requests."
            )

        self._attempt(read)

    def review_response(self):
        def review():
            self._invalidate_review()
            self._proposal = self.runner.review(self.response.toPlainText())
            self.preview.setPlainText(self._proposal.canonical)
            self.run.setText(
                "Save reviewed answer"
                if self._proposal.final is not None
                else "Run reviewed requests"
            )
            self.status.setText(
                "Exact request preview ready. Permissions are requested through Steed's normal task controls."
            )

        self._attempt(review)

    def run_response(self):
        def run():
            if self._proposal is not None:
                self.runner.execute(self._proposal)

        self._attempt(run)

    def _runner_changed(self):
        if self._closed:
            return
        restore = self._pasting
        if self.runner.stopped:
            self._conceal()
            self._clear()
        elif self._outbound_exchange != self.runner.exchange_id:
            self._outbound_exchange = None
            self.outbound.clear()
            self._invalidate_review()
            self.response.clear()
        self.status.setText(self.runner.status)
        self.results.setPlainText(
            encode(self.runner.results) if self.runner.results else ""
        )
        if not self.runner.busy and self.runner.exchanges:
            self.catalogue.setChecked(False)
        self._refresh()
        host = self.presentation_host()
        if host is not None:
            host.publish(force=True)
        if restore and self.runner.stopped:
            self.show()

    def _refresh(self):
        active = bool(self.runner and not self.runner.stopped)
        ready = (
            active and not self.runner.busy and not self._paused and not self._pasting
        )
        for widget in (self.goal, self.mode, self.targets, self.consent, self.start):
            widget.setEnabled(
                not active and self.profile != "search" and not self._closed
            )
        for widget in (self.prepare, self.review, self.response, self.catalogue):
            widget.setEnabled(ready)
        shared = (
            ready and self._outbound_exchange == self.runner.exchange_id
            if active
            else False
        )
        self.copy.setEnabled(shared)
        self.paste.setEnabled(shared and self.target is not None)
        self.read.setEnabled(ready and self.target is not None)
        self.run.setEnabled(ready and self._proposal is not None)
        self.stop.setEnabled(active)
        self.details.setEnabled(self.runner is not None)
        self.rearm.setVisible(active and self._paused)
        self.rearm.setEnabled(
            active
            and self._paused
            and self.target is not None
            and not self.target.is_loading
        )

    def _url_changed(self, url):
        if self._address(url) != self.destination:
            self._navigation()

    def _loaded(self, ok):
        self._refresh()

    def _navigation(self):
        if not self.runner or self.runner.stopped:
            return
        if self.runner.exchanges == 0:
            self._paused = True
            self._invalidate_review()
            self.response.clear()
            self.status.setText(
                "Chat paused at "
                + self.target.url().toDisplayString(
                    QUrl.UrlFormattingOption.RemoveUserInfo
                )
                + ". Enable this initial conversation explicitly, or stop and start fresh."
            )
            self._refresh()
        else:
            self.runner.stop(
                "Chat navigated. This relay ended; earlier requests will not be replayed."
            )

    def rearm_target(self):
        def rearm():
            if not self.runner or self.runner.exchanges or not self._paused:
                return
            view = self.target
            if (
                view is None
                or sip.isdeleted(view)
                or view not in self.window.tabs
                or view.page() is not self.target_page
                or view.is_loading
                or view.url().scheme() != "https"
                or view.url().authority() != QUrl(self.destination).authority()
                or self.window.profile_manager.current_profile_name != self.profile
            ):
                raise RelayError(
                    "destination_changed",
                    "Use the same loaded chat origin, or stop and start a new task.",
                )
            self.destination = self._address(view.url())
            self._paused = False
            self._guard()
            self._update_binding()
            self.status.setText("Initial conversation enabled: " + self.destination)

        self._attempt(rearm)

    def _target_destroyed(self):
        if self.runner:
            self.runner.stop("Enabled chat tab closed. Relay stopped.")

    def _unbind(self):
        if self.target is not None and not sip.isdeleted(self.target):
            self.target.loadStarted.disconnect(self._navigation)
            self.target.loadFinished.disconnect(self._loaded)
            self.target.urlChanged.disconnect(self._url_changed)
            self.target.destroyed.disconnect(self._target_destroyed)
        self.target = self.target_page = None

    def _conceal(self):
        host = self.presentation_host()
        if host is not None:
            host.surface.conceal()

    def _clear(self):
        self._pasting = False
        self._outbound_exchange = None
        self._invalidate_review()
        for editor in (self.response, self.outbound, self.results):
            editor.clear()

    def stop_task(self):
        if self.runner:
            self.runner.stop()

    def task_details(self):
        from ui.task_dialog import TaskDialog

        dialog = TaskDialog(self.window.profile_manager.registry.tasks, self.window)
        for row, (task, _) in enumerate(dialog._rows):
            if task.task_id == self.runner.task_id:
                dialog.table.selectRow(row)
                break
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _profile_changed(self, name):
        self.reject()

    def done(self, result):
        if not self._closed:
            self._closed = True
            self._conceal()
            self._clear()
            self.goal.clear()
            self.binding.clear()
            if self.runner:
                self.runner.stop()
                self.runner.goal = ""
            self._unbind()
            self.window.profile_manager.profile_changed.disconnect(
                self._profile_changed
            )
        super().done(result)
