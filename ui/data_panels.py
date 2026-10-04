"""Profile-bound native draft cards and an inert user-review popup."""

import json
import sqlite3

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.app_accounts import AccountError
from services.task_store import ACTIVE_STATUSES
from ui.data_panel_form import PanelForm
from ui.panel_image import PanelImage
from ui.panel_instruction import PanelInstruction


def display_text(value):
    """Keep generated text inert and make invisible control characters readable."""
    return "".join(
        c if c.isprintable() or c in "\n\t" else f"\\u{ord(c):04x}" for c in value
    )


def label(text, parent=None):
    result = QLabel(text, parent)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


def source_caption(widget):
    if "account" in widget:
        if not widget["available"]:
            return widget["unavailable"]
        caption = (
            "Linked account read • "
            + display_text(widget["artifact_title"])
            + " • fetched "
            + widget["fetched_at"]
        )
        if "source_count" in widget:
            caption += (
                f" • showing {widget['shown_count']} of {widget['source_count']} rows"
            )
            if widget["clipped_cells"]:
                caption += f" • {widget['clipped_cells']} shortened cells"
        return caption
    if "library" in widget:
        if not widget["available"]:
            return widget["unavailable"]
        return (
            "Linked library file • "
            + display_text(widget["folder_name"])
            + "/"
            + display_text(widget["artifact_title"])
            + f" • file revision {widget['source_revision']}"
        )
    return (
        "Saved task file • " if "file_id" in widget else "Saved agent draft • "
    ) + display_text(widget["artifact_title"])


def linked_description(service, definition):
    sources = []
    for widget in definition.library_widgets.values():
        sources.append(
            service.library.folder(widget["library"]["folder_id"])["name"]
            + "/"
            + widget["library"]["path"]
        )
    for widget in definition.account_widgets.values():
        source = widget["account"]
        try:
            binding = service.data_panels.account_reads.binding(widget)
        except (ValueError, AccountError):
            binding = {"label": "Unavailable account", "inputs": source["inputs"]}
        sources.append(
            f"Account: {binding['label']}\nRead: {source['connector']}.{source['tool']}\n"
            f"Destination: {source['destination']}\nInputs: {json.dumps(binding['inputs'] or {}, ensure_ascii=False)}"
            + (
                "\nDisplay projection: "
                + json.dumps(widget["mapping"], ensure_ascii=False)
                if "mapping" in widget
                else ""
            )
        )
    return "\n\n".join(sources)


class PanelContent(QWidget):
    def __init__(self, task, widgets, parent=None, *, form_context=None):
        super().__init__(parent)
        body = QVBoxLayout(self)
        body.setContentsMargins(8, 8, 8, 8)
        self.displays = {}
        self.captions = {}
        self._widgets = {}
        for widget in widgets:
            heading = label(widget["title"])
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            body.addWidget(heading)
            if widget["kind"] == "task":
                self.captions[widget["id"]] = label("Task status: " + task.status)
                body.addWidget(self.captions[widget["id"]])
                display = QPlainTextEdit()
                display.setReadOnly(True)
                display.setTabChangesFocus(True)
                display.setPlainText(display_text(task.objective))
                display.setMinimumHeight(70)
                display.setMaximumHeight(120)
            elif widget["kind"] == "form":
                display = PanelForm(widget, context=form_context)
            elif widget["kind"] == "instruction":
                display = PanelInstruction(widget, context=form_context)
            else:
                caption = label(source_caption(widget))
                self.captions[widget["id"]] = caption
                body.addWidget(caption)
                if widget["kind"] == "text":
                    display = QPlainTextEdit()
                    display.setReadOnly(True)
                    display.setTabChangesFocus(True)
                    display.setPlainText(display_text(widget["content"]))
                    display.setMinimumHeight(90)
                    display.setMaximumHeight(190)
                elif widget["kind"] == "list":
                    display = QListWidget()
                    display.setWordWrap(True)
                    display.addItems([display_text(item) for item in widget["content"]])
                    display.setMinimumHeight(90)
                    display.setMaximumHeight(190)
                elif widget["kind"] == "image":
                    display = PanelImage(widget["content"])
                else:
                    display = QTableWidget()
                    display.setEditTriggers(
                        QAbstractItemView.EditTrigger.NoEditTriggers
                    )
                    display.setMinimumHeight(90)
                    display.setMaximumHeight(240)
                    self._table(display, widget["content"])
            display.setAccessibleName(widget["title"])
            if "library" in widget or "account" in widget:
                display.setEnabled(widget["available"])
            self.displays[widget["id"]] = display
            self._widgets[widget["id"]] = widget
            body.addWidget(display)
        body.addStretch(1)

    @staticmethod
    def _table(display, content):
        display.setColumnCount(len(content["columns"]))
        display.setHorizontalHeaderLabels(content["columns"])
        display.setRowCount(len(content["rows"]))
        for i, row in enumerate(content["rows"]):
            for j, value in enumerate(row):
                display.setItem(i, j, QTableWidgetItem(display_text(value)))

    def update_content(self, task, widgets):
        """Update saved outputs in place, preserving unrelated form values/focus."""
        for widget in widgets:
            key, kind = widget["id"], widget["kind"]
            display, previous = self.displays[key], self._widgets[key]
            if kind in ("form", "instruction"):
                display.refresh_available()
            elif kind == "task":
                self.captions[key].setText("Task status: " + task.status)
            else:
                self.captions[key].setText(source_caption(widget))
                if "library" in widget or "account" in widget:
                    display.setEnabled(widget["available"])
                if kind == "image":
                    if previous["source_sha256"] != widget["source_sha256"]:
                        display.set_image(widget["content"])
                elif previous["content"] != widget["content"]:
                    if kind == "text":
                        display.setPlainText(display_text(widget["content"]))
                    elif kind == "list":
                        display.clear()
                        display.addItems(
                            [display_text(item) for item in widget["content"]]
                        )
                    else:
                        self._table(display, widget["content"])
            self._widgets[key] = widget


class DataPanelDock(QDockWidget):
    def __init__(self, service, browser):
        super().__init__("Data Panels", browser)
        self.service, self.browser = service, browser
        self.setObjectName("steedDataPanels")
        self.setMinimumWidth(300)
        self._scheduled = False
        self._signature = None
        self._rows = []
        content = QWidget()
        body = QVBoxLayout(content)
        self.profile_label = label("")
        body.addWidget(self.profile_label)
        row = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setAccessibleName("Data panel")
        self.selector.currentIndexChanged.connect(self.refresh_content)
        row.addWidget(self.selector, 1)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)
        body.addLayout(row)
        controls = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh linked sources")
        self.refresh_button.clicked.connect(self.refresh_sources)
        controls.addWidget(self.refresh_button)
        self.access_button = QPushButton("Linked file access…")
        self.access_button.clicked.connect(self.change_source_access)
        controls.addWidget(self.access_button)
        body.addLayout(controls)
        self.status = label("")
        body.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body.addWidget(self.scroll, 1)
        self.setWidget(content)
        service.changed.connect(self.schedule)
        self.refresh()

    def schedule(self):
        if self.browser._closing:
            return
        # Erase revoked linked contents synchronously, before deferred paint/deletion.
        self.refresh_content()
        if not self._scheduled:
            self._scheduled = True
            QTimer.singleShot(0, self.refresh)

    def _clear(self):
        widget = self.scroll.takeWidget()
        if widget is not None:
            # Erase old profile content immediately, including deferred Qt children.
            for form in widget.findChildren(PanelForm):
                form.clear_private()
            for instruction in widget.findChildren(PanelInstruction):
                instruction.clear_private()
            for child in widget.findChildren(QPlainTextEdit):
                child.clear()
            for child in widget.findChildren(QListWidget):
                child.clear()
            for child in widget.findChildren(QTableWidget):
                child.clear()
                child.setRowCount(0)
                child.setColumnCount(0)
            for child in widget.findChildren(QLabel):
                child.clear()
            if hasattr(widget, "_widgets"):
                widget._widgets.clear()
            widget.hide()
            widget.deleteLater()
        self._signature = None

    def refresh(self):
        self._scheduled = False
        if self.browser._closing:
            self._clear()
            return
        profile = self.browser.profile_manager.current_profile_name
        self.profile_label.setText(
            "Private browsing" if profile == "search" else "Profile: " + profile
        )
        old = self.selector.currentData()
        if self.service._closed or self.service.failure or profile == "search":
            rows = []
        else:
            try:
                rows = self.service.data_panels.for_profile(profile)
            except (ValueError, sqlite3.Error, OSError):
                rows = []
        self._rows = rows
        self.selector.blockSignals(True)
        self.selector.clear()
        for receipt in rows:
            self.selector.addItem(receipt.name, (receipt.task_id, receipt.request_id))
        # Qt's QVariant lookup does not compare Python tuple payloads reliably.
        index = next(
            (
                i
                for i in range(self.selector.count())
                if self.selector.itemData(i) == old
            ),
            0,
        )
        self.selector.setCurrentIndex(max(0, index))
        self.selector.blockSignals(False)
        self.refresh_content()

    def refresh_content(self, *_):
        if self.browser._closing or self.service._closed or self.service.failure:
            self._clear()
            self.remove_button.setEnabled(False)
            self.access_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.status.setText("Data panels are unavailable.")
            return
        key = self.selector.currentData()
        self.remove_button.setEnabled(
            bool(key) and not self.service.failure and not self.service._closed
        )
        if not key:
            self._clear()
            self.access_button.setVisible(False)
            self.refresh_button.setVisible(False)
            self.status.setText(
                "Panels are unavailable in private browsing."
                if self.browser.profile_manager.is_search_profile()
                else "No panels in this profile. Ask an agent to propose a task dashboard."
            )
            return
        try:
            receipt = self.service.data_panels.get(*key)
            task = self.service.store.get(receipt.task_id)
            widgets = self.service.data_panels.view(*key)
            linked = any("library" in w or "account" in w for w in widgets)
            self.access_button.setVisible(linked)
            self.refresh_button.setVisible(linked)
            self.access_button.setEnabled(linked)
            self.refresh_button.setEnabled(linked)
            signature = (key, receipt.revision, task.status, widgets)
            live = self.service._grants.get(task.task_id)
            actions = (
                {"artifact"} if any("artifact_id" in w for w in widgets) else set()
            ) | ({"workspace"} if any("file_id" in w for w in widgets) else set())
            updating = task.status in ACTIVE_STATUSES and (
                not actions
                or (
                    live is not None
                    and not live[1].reason()
                    and actions <= set(live[1].scope.actions)
                )
            )
            has_drafts = bool(actions)
            detail = (
                (
                    "Draft updates enabled for this task"
                    if updating
                    else "Saved drafts • task updates stopped"
                )
                if has_drafts
                else (
                    "Live task status"
                    if task.status in ACTIVE_STATUSES
                    else "Saved task status"
                )
            )
            self.status.setText(f"Revision {receipt.revision} • " + detail)
            if linked:
                access = self.service.data_panels.source_access(*key)
                self.access_button.setText(
                    "Disable linked reads" if access else "Allow linked reads…"
                )
                self.status.setText(
                    f"Revision {receipt.revision} • Linked reads {'allowed' if access else 'disabled'} in this profile"
                    + " • reusable after task end"
                )
            content = self.scroll.widget()
            if (
                self._signature is not None
                and self._signature[0] == key
                and content is not None
            ):
                content.update_content(task, widgets)
                self._signature = signature
                return
            self._clear()
            profile_key = self.service.data_panels.profile_key(
                self.browser.profile_manager.current_profile_name
            )

            def form_context():
                profile = self.browser.profile_manager.current_profile_name
                if self.service.data_panels.profile_key(profile) != profile_key:
                    raise ValueError("The display profile changed")
                if self.browser._closing:
                    raise ValueError("The browser is closing")
                return self.service, key[0], key[1], profile_key, profile

            self.scroll.setWidget(
                PanelContent(task, widgets, form_context=form_context)
            )
            from ui.layout_panel import apply_chrome_font

            apply_chrome_font(self.widget(), self.browser.layout_preferences.text_size)
            self._signature = signature
        except (ValueError, KeyError, AccountError, sqlite3.Error, OSError):
            self._clear()
            self.access_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.status.setText(
                "This panel's saved drafts are unavailable. You can remove the panel; no data is fetched elsewhere."
            )

    def change_source_access(self):
        if self.browser._closing or self.service._closed or self.service.failure:
            return
        key = self.selector.currentData()
        if not key:
            return
        try:
            panels = self.service.data_panels
            profile = self.browser.profile_manager.current_profile_name
            profile_key = panels.profile_key(profile)
            definition = panels.definition(*key)
            sources = linked_description(self.service, definition)
            enabled = panels.source_access(*key)
            if enabled:
                panels.set_source_access(*key, profile_key=profile_key, enabled=False)
            else:
                prompt = QMessageBox(self)
                prompt.setWindowTitle("Allow this panel to read linked sources?")
                prompt.setTextFormat(Qt.TextFormat.PlainText)
                prompt.setText(
                    f"Panel: {definition.name}\nProfile: {profile}\nSelected sources:\n{sources}\n\n"
                    "Files can refresh when viewed; account reads run only when you click Refresh. "
                    "Permission lasts after task end. Folder/account read access must stay enabled. "
                    "Disable linked reads here at any time. This grants no edits, sends or agent-task access."
                )
                prompt.setStandardButtons(
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                prompt.setDefaultButton(QMessageBox.StandardButton.No)
                decision = prompt.exec()
                if (
                    self.browser._closing
                    or self.service._closed
                    or self.service.failure
                ):
                    return
                if decision == QMessageBox.StandardButton.Yes:
                    if (
                        panels.profile_key(
                            self.browser.profile_manager.current_profile_name
                        )
                        != profile_key
                    ):
                        raise ValueError("Display profile changed; review again")
                    panels.set_source_access(
                        *key, profile_key=profile_key, enabled=True
                    )
            self.refresh_content()
        except (ValueError, KeyError, AccountError, sqlite3.Error, OSError):
            self.status.setText(
                "Linked access could not be changed; check the panel and profile."
            )

    def refresh_sources(self):
        if self.browser._closing or self.service._closed or self.service.failure:
            return
        key = self.selector.currentData()
        if key:
            try:
                self.service.data_panels.account_reads.refresh(
                    *key,
                    profile_key=self.service.data_panels.profile_key(
                        self.browser.profile_manager.current_profile_name
                    ),
                )
                self.refresh_content()
            except (ValueError, KeyError, AccountError, sqlite3.Error, OSError):
                self.refresh_content()
                self.status.setText(
                    "Refresh unavailable; check linked permission and account read access."
                )

    def remove_selected(self):
        key = self.selector.currentData()
        if key:
            try:
                self.service.data_panels.remove(*key)
            except (ValueError, KeyError, AccountError, sqlite3.Error, OSError):
                self.status.setText("The panel could not be removed.")
            self.refresh()


class DataPanelReviewDialog(QDialog):
    def __init__(self, service, receipt, parent=None):
        super().__init__(parent)
        self.service, self.receipt = service, receipt
        self.task_id = receipt.task_id
        self.request_key = receipt.request_key
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Review data panel")
        self.resize(680, 600)
        body = QVBoxLayout(self)
        body.addWidget(label("Panel: " + receipt.name))
        body.addWidget(
            label(
                "Enable these native widgets in one normal profile. Saved reports remain after task end; updates need artifact/workspace permission. Linked sources need separate read permission and hide when access is disabled. Forms send to the owning task; reusable instruction buttons let you choose a connected conversation. Both require a message preview and Send to agent. Website and account access remain separate."
            )
        )
        self.profiles = QComboBox()
        self.profiles.setAccessibleName("Display profile")
        self.profiles.currentIndexChanged.connect(self._profile_changed)
        body.addWidget(self.profiles)
        self._profile_key = ""
        self._load_profiles()
        browser = getattr(parent, "profile_manager", None)
        if browser is not None:
            self.profiles.setCurrentText(browser.current_profile_name)
        task = service.store.get(receipt.task_id)
        self.preview = QScrollArea()
        self.preview.setWidgetResizable(True)
        self.preview.setWidget(
            PanelContent(
                task, service.data_panels.view(receipt.task_id, receipt.request_id)
            )
        )
        body.addWidget(self.preview, 1)
        definition = service.data_panels.definition(receipt.task_id, receipt.request_id)
        self.linked_access = QCheckBox(
            "Allow this panel to read the selected linked sources in this profile, including after task end"
        )
        self.linked_access.setStyleSheet(
            "QCheckBox { background-color: #fff6d8; color: #18202c; padding: 5px; }"
        )
        self.linked_access.setVisible(bool(definition.linked_widgets))
        if definition.linked_widgets:
            sources = linked_description(service, definition)
            body.addWidget(
                label(
                    "Linked sources (read only; account calls require manual Refresh):"
                )
            )
            self.linked_sources = QPlainTextEdit()
            self.linked_sources.setReadOnly(True)
            self.linked_sources.setPlainText(sources)
            self.linked_sources.setMaximumHeight(80)
            self.linked_sources.setAccessibleName("Selected linked sources")
            body.addWidget(self.linked_sources)
        body.addWidget(self.linked_access)
        if definition.linked_widgets:
            service.changed.connect(self.refresh_linked_preview)
        body.addWidget(
            label(
                "Preview only • agent-generated drafts are not verified. Links and commands in draft text are never executed."
            )
        )
        self.error = label("")
        body.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.approve_button = buttons.addButton(
            "Enable panel and draft updates", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.approve_button.setAutoDefault(False)
        self.approve_button.clicked.connect(self.approve)
        self.deny_button = buttons.addButton(
            "Decline", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        self.deny_button.setDefault(True)
        self.deny_button.clicked.connect(self.decline)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(
            self.reject
        )
        body.addWidget(buttons)
        self.approve_button.setEnabled(bool(self._profile_key))

    def refresh_linked_preview(self):
        try:
            if self.service._closed or self.service.failure:
                raise ValueError("Task services are unavailable")
            if self.preview.widget() is None:
                return
            widgets = self.service.data_panels.view(
                self.receipt.task_id, self.receipt.request_id
            )
            self.preview.widget().update_content(
                self.service.store.get(self.receipt.task_id), widgets
            )
        except (ValueError, KeyError, AccountError, sqlite3.Error, OSError):
            content = self.preview.takeWidget()
            if content is not None:
                for child in content.findChildren(QPlainTextEdit):
                    child.clear()
                for child in content.findChildren(QListWidget):
                    child.clear()
                for child in content.findChildren(QTableWidget):
                    child.clear()
                    child.setRowCount(0)
                content._widgets.clear()
                content.hide()
                content.deleteLater()
            self.approve_button.setEnabled(False)

    def _load_profiles(self):
        selected = self.profiles.currentText()
        self.profiles.clear()
        self.profiles.addItems(self.service.data_panels.profile_names())
        if selected in self.service.data_panels.profile_names():
            self.profiles.setCurrentText(selected)
        self._profile_changed()

    def _profile_changed(self, *_):
        try:
            self._profile_key = self.service.data_panels.profile_key(
                self.profiles.currentText(), create=True
            )
        except (ValueError, OSError):
            self._profile_key = ""
        if hasattr(self, "approve_button"):
            self.approve_button.setEnabled(bool(self._profile_key))

    def approve(self):
        try:
            self.service.data_panels.resolve(
                self.receipt.task_id,
                self.receipt.request_id,
                approve=True,
                profile=self.profiles.currentText(),
                profile_key=self._profile_key,
                library_access=self.linked_access.isChecked(),
            )
        except (ValueError, KeyError, AccountError, sqlite3.Error, OSError):
            self.error.setText(
                "The panel or profile changed, permission expired, or a draft is unavailable. Review the profile again; changed drafts need a new proposal."
            )
            self._load_profiles()
            return
        browser = self.parentWidget()
        while browser is not None and not callable(
            getattr(browser, "_toggle_data_panels", None)
        ):
            browser = browser.parentWidget()
        if (
            callable(getattr(browser, "_toggle_data_panels", None))
            and browser.profile_manager.current_profile_name
            == self.profiles.currentText()
        ):
            browser.data_panels_action.setChecked(True)
            browser._toggle_data_panels(True)
        self.done(QDialog.DialogCode.Accepted)

    def decline(self):
        try:
            self.service.data_panels.resolve(
                self.receipt.task_id, self.receipt.request_id
            )
        except (ValueError, KeyError, AccountError, sqlite3.Error, OSError):
            self.error.setText("This request is no longer available.")
            return
        self.done(QDialog.DialogCode.Accepted)
