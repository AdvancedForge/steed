"""Maintained HTML presentation of existing settings controllers.

Only enumerated controls and operations cross the bridge. Native controllers
still own validation, confirmations, persistence and service lifetimes.
"""

import base64
from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import QBuffer, QIODevice, QItemSelectionModel, QPoint, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QImage, QTextDocument
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from ui.html_surface import HtmlSurface


class HtmlPresentation:
    """Keep native controllers hidden while a maintained HTML host is visible."""

    def presentation_owner(self):
        owner = self.parentWidget()
        while owner is not None and not hasattr(owner, "_present_dialog"):
            owner = owner.parentWidget()
        return owner

    def presentation_host(self):
        host = getattr(self, "_html_host", None)
        return (
            host
            if host is not None and not sip.isdeleted(host) and not host.closed
            else None
        )

    def exec(self):
        owner = self.presentation_owner()
        if owner is not None:
            return owner._present_dialog(self, force_html=True)
        return super().exec()

    def show(self):
        if owner := self.presentation_owner():
            return owner._present_dialog(self, modal=False, force_html=True)
        return super().show()

    def hide(self):
        host = self.presentation_host()
        return host.hide() if host is not None else super().hide()

    def isVisible(self):
        if sip.isdeleted(self):
            return False
        host = self.presentation_host()
        return host.isVisible() if host is not None else super().isVisible()

    def raise_(self):
        host = self.presentation_host()
        return host.raise_() if host is not None else super().raise_()

    def activateWindow(self):
        host = self.presentation_host()
        return host.activateWindow() if host is not None else super().activateWindow()

    def resize(self, *size):
        host = self.presentation_host()
        return host.resize(*size) if host is not None else super().resize(*size)


class HtmlSettingsDialog(HtmlPresentation, QDialog):
    """A settings/review controller with maintained HTML presentation."""


def place_popup(widget, owner, anchor=None, size=(780, 640)):
    """Keep the popup on its owner's screen, beside or below its trigger."""
    point = anchor or owner.mapToGlobal(QPoint(24, 64))
    screen = QGuiApplication.screenAt(point) or owner.screen()
    available = screen.availableGeometry().adjusted(8, 8, -8, -8)
    widget.resize(min(size[0], available.width()), min(size[1], available.height()))
    widget.move(
        max(available.left(), min(point.x(), available.right() - widget.width() + 1)),
        max(available.top(), min(point.y(), available.bottom() - widget.height() + 1)),
    )


class HtmlDialog(QDialog):
    """A visual host, not a second settings implementation."""

    def __init__(self, source, owner, *, anchor=None, workspace=None):
        flags = (
            Qt.WindowType.Widget
            if workspace
            else Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        )
        super().__init__(owner, flags)
        self.workspace = workspace
        self.source, self.owner = source, owner
        source.hide()
        self._source_close = source.close
        source.close = self.close
        self.context = token_hex(16)
        self.sequence = 0
        self.closed = False
        self.error = ""
        self.objects = {}
        self.tokens = {}
        self.table_offsets = {}
        self.image_cache = {}
        self.image_ids = set()
        self.active_tokens = set()
        self.existing_surface = getattr(source, "surface", None)
        view = getattr(source, "view", None)
        self.embedded_view = view if isinstance(view, QWebEngineView) else None
        self.surface = HtmlSurface(
            "dialog", self, label="settings", command_limit=650000
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.surface)
        if self.existing_surface:
            self.surface.setFixedHeight(68)
            layout.addWidget(self.existing_surface)
        if self.embedded_view:
            source.layout().removeWidget(self.embedded_view)
            layout.addWidget(self.embedded_view, 3)
            self.surface.setMaximumHeight(360)
            self.embedded_view.show()
        self.setWindowTitle(source.windowTitle())
        self.surface.command.connect(self.command)
        self.surface.reloading.connect(self.reload)
        source.finished.connect(self.source_done)
        self.sensitive_signal = None
        if getattr(source, "html_sensitive_updates", False):
            self.sensitive_signal = source.service.changed
            self.sensitive_signal.connect(self.refresh_sensitive)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        if not workspace:
            self.setWindowModality(source.windowModality())
            place_popup(self, owner, anchor)
        self.publish()

    def poll(self):
        if self.isVisible():
            self.publish()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "surface") and not self.closed:
            self.publish()

    def reload(self):
        self.context = token_hex(16)
        self.sequence = 0
        self.publish()

    def refresh_sensitive(self):
        if not self.closed:
            self.surface.conceal()
            self.publish(force=True)

    def token(self, obj):
        identity = id(obj)
        token = self.tokens.get(identity)
        if token is None or self.objects.get(token) is not obj:
            token = token_hex(12)
            self.tokens[identity] = token
            self.objects[token] = obj
        self.active_tokens.add(token)
        return token

    def children(self, layout):
        if layout is None:
            return []
        result = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget() is not None:
                node = self.node(item.widget())
                if node:
                    result.append(node)
            elif item.layout() is not None:
                result.append(
                    {
                        "id": self.token(item.layout()),
                        "enabled": True,
                        "kind": "group",
                        "children": self.children(item.layout()),
                    }
                )
        return result

    def node(self, widget):
        if widget.isHidden():
            return None
        node = {"id": self.token(widget), "enabled": widget.isEnabled()}
        if isinstance(widget, QTabWidget):
            current = widget.currentWidget()
            node.update(
                kind="tabs",
                selected=widget.currentIndex(),
                labels=[widget.tabText(i) for i in range(widget.count())],
                tab_enabled=[widget.isTabEnabled(i) for i in range(widget.count())],
                children=self.children(current.layout()) if current is not None else [],
            )
        elif isinstance(widget, QWebEngineView):
            return None  # Live website content is a separate, unbridged engine view.
        elif isinstance(widget, QAbstractButton):
            node.update(
                kind="button",
                text=widget.text().replace("&", ""),
                checkable=widget.isCheckable(),
                checked=widget.isChecked(),
            )
        elif isinstance(widget, QLabel):
            from ui.panel_image import PanelImage

            pixmap = widget.pixmap()
            image = (
                widget._image
                if isinstance(widget, PanelImage)
                else pixmap.toImage()
                if pixmap is not None
                else QImage()
            )
            if not image.isNull():
                key = id(widget)
                self.image_ids.add(key)
                cached = self.image_cache.get(key)
                if cached is None or cached[0] != image.cacheKey():
                    reduced = image.scaled(
                        1000,
                        700,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    while True:
                        data = QBuffer()
                        data.open(QIODevice.OpenModeFlag.WriteOnly)
                        reduced.save(data, "PNG")
                        encoded = bytes(data.data())
                        if len(encoded) <= 1024 * 1024:
                            break
                        reduced = reduced.scaledToWidth(
                            max(1, reduced.width() * 3 // 4)
                        )
                    cached = (
                        image.cacheKey(),
                        "data:image/png;base64,"
                        + base64.b64encode(encoded).decode("ascii"),
                    )
                    self.image_cache[key] = cached
                node.update(
                    kind="image",
                    value=cached[1],
                    text=widget.accessibleName() or "Image preview",
                )
                return node
            text = widget.text()
            if widget.textFormat() == Qt.TextFormat.RichText or (
                widget.textFormat() == Qt.TextFormat.AutoText
                and "<" in text
                and ">" in text
            ):
                document = QTextDocument()
                document.setHtml(text)
                text = document.toPlainText()
            node.update(kind="label", text=text)
        elif isinstance(widget, QLineEdit):
            secret = widget.echoMode() != QLineEdit.EchoMode.Normal
            node.update(
                kind="input",
                value="" if secret else widget.text(),
                secret=secret,
                has_value=bool(widget.text()),
                readonly=widget.isReadOnly(),
                placeholder=widget.placeholderText(),
                maximum=widget.maxLength(),
            )
        elif isinstance(widget, (QPlainTextEdit, QTextEdit)):
            node.update(
                kind="text", value=widget.toPlainText(), readonly=widget.isReadOnly()
            )
        elif isinstance(widget, QComboBox):
            node.update(
                kind="combo",
                selected=widget.currentIndex(),
                options=[widget.itemText(i) for i in range(widget.count())],
                editable=widget.isEditable(),
                value=widget.currentText(),
            )
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox, QSlider)):
            node.update(
                kind="number",
                value=widget.value(),
                minimum=widget.minimum(),
                maximum=widget.maximum(),
                step=widget.singleStep(),
            )
        elif isinstance(widget, (QListWidget, QTableWidget, QTreeWidget)):
            rows = []
            if isinstance(widget, QListWidget):
                items = [widget.item(i) for i in range(widget.count())]
                headers = []
            elif isinstance(widget, QTableWidget):
                items = [widget.item(i, 0) for i in range(widget.rowCount())]
                headers = [
                    widget.horizontalHeaderItem(i).text()
                    if widget.horizontalHeaderItem(i)
                    else ""
                    for i in range(widget.columnCount())
                ]
            else:
                items = []

                def visit(parent):
                    for i in range(parent.childCount()):
                        item = parent.child(i)
                        items.append(item)
                        if item.isExpanded():
                            visit(item)

                visit(widget.invisibleRootItem())
                headers = [
                    widget.headerItem().text(i) for i in range(widget.columnCount())
                ]
            key = id(widget)
            offset = min(
                self.table_offsets.get(key, 0), max(0, (len(items) - 1) // 500 * 500)
            )
            self.table_offsets[key] = offset
            for item in items[offset : offset + 500]:
                if item is None or (hasattr(item, "isHidden") and item.isHidden()):
                    continue
                if isinstance(widget, QTableWidget):
                    cells = [
                        widget.item(item.row(), i).text()
                        if widget.item(item.row(), i)
                        else ""
                        for i in range(widget.columnCount())
                    ]
                else:
                    cells = (
                        [item.text(i) for i in range(widget.columnCount())]
                        if isinstance(widget, QTreeWidget)
                        else [item.text()]
                    )
                rows.append(
                    {
                        "id": self.token(item),
                        "cells": cells,
                        "selected": item.isSelected(),
                        "checkable": isinstance(widget, QListWidget)
                        and bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                        and item.data(Qt.ItemDataRole.CheckStateRole) is not None,
                        "checked": item.checkState(0) == Qt.CheckState.Checked
                        if isinstance(widget, QTreeWidget)
                        else item.checkState() == Qt.CheckState.Checked
                        if isinstance(widget, QListWidget)
                        else False,
                        "expandable": isinstance(widget, QTreeWidget)
                        and bool(item.childCount()),
                        "expanded": item.isExpanded()
                        if isinstance(widget, QTreeWidget)
                        else False,
                    }
                )
            node.update(
                kind="table",
                headers=headers,
                rows=rows,
                count=len(items),
                offset=offset,
                reorder=isinstance(widget, QListWidget)
                and widget.dragDropMode()
                == QAbstractItemView.DragDropMode.InternalMove,
            )
        elif isinstance(widget, QDialogButtonBox):
            node.update(
                kind="group",
                footer=True,
                children=[self.node(button) for button in widget.buttons()],
            )
        elif isinstance(widget, QScrollArea):
            content = widget.widget()
            node.update(
                kind="group",
                children=self.children(content.layout()) if content is not None else [],
            )
        elif isinstance(widget, QSplitter):
            node.update(
                kind="group",
                children=[self.node(widget.widget(i)) for i in range(widget.count())],
            )
        else:
            node.update(kind="group", children=self.children(widget.layout()))
            if isinstance(widget, QGroupBox):
                node["title"] = QGroupBox.title(widget)
        return node

    def publish(self, *, force=False):
        if self.closed or sip.isdeleted(self.source):
            return
        self.active_tokens = set()
        self.image_ids = set()
        tree = [] if self.existing_surface else self.children(self.source.layout())
        self.image_cache = {
            key: value
            for key, value in self.image_cache.items()
            if key in self.image_ids
        }
        self.objects = {
            key: value
            for key, value in self.objects.items()
            if key in self.active_tokens
        }
        self.tokens = {id(obj): key for key, obj in self.objects.items()}
        from ui.management_pages import SHARED_PAGES

        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "title": self.source.windowTitle(),
                "children": tree,
                "error": self.error,
                "workspace": self.workspace,
                "profile": self.owner.profile_manager.current_profile_name,
                "shared": self.workspace in SHARED_PAGES,
                "dark": self.owner.profile_manager.registry.page_style.dark(
                    self.owner.profile_manager.current_profile_name
                ),
            },
            force=force,
        )

    def command(self, command):
        if self.closed or command.get("context") != self.context:
            return
        sequence = command.get("sequence")
        if type(sequence) is not int or sequence <= self.sequence:
            return
        self.sequence = sequence
        self.error = ""
        try:
            action = command.get("action")
            if (
                getattr(self.source, "html_sensitive_updates", False)
                and action == "choose"
            ):
                self.surface.conceal()
            if action == "workspace" and self.workspace:
                from services.start_pages import WORKSPACES

                if command.get("value") not in WORKSPACES:
                    raise ValueError("Unknown browser page.")
                self.owner._new_tab(url="steed:" + command["value"])
                return
            if action == "close":
                self.source.reject()
                return
            obj = self.objects.get(command.get("id"))
            if obj is None or sip.isdeleted(obj):
                raise ValueError(
                    "This control changed. Try again with the refreshed view."
                )
            if (
                not isinstance(obj, QWidget)
                or not obj.isEnabled()
                or not obj.isVisibleTo(self.source)
            ):
                raise ValueError("This control is unavailable.")
            value = command.get("value")
            if action == "page" and isinstance(
                obj, (QListWidget, QTableWidget, QTreeWidget)
            ):
                if type(value) is not int or value not in (-1, 1):
                    raise ValueError("Choose the previous or next results page.")
                self.table_offsets[id(obj)] = max(
                    0, self.table_offsets.get(id(obj), 0) + value * 500
                )
                obj.clearSelection()
                if isinstance(obj, QListWidget):
                    obj.setCurrentRow(-1)
                elif isinstance(obj, QTableWidget):
                    obj.setCurrentCell(-1, -1)
                else:
                    obj.setCurrentItem(None)
            elif action == "click" and isinstance(obj, QAbstractButton):
                obj.click()
            elif action == "enter" and isinstance(obj, QLineEdit):
                obj.returnPressed.emit()
            elif action == "edit" and isinstance(value, str) and len(value) <= 300000:
                if isinstance(obj, QLineEdit) and not obj.isReadOnly():
                    obj.setText(value[: obj.maxLength()])
                    obj.textEdited.emit(obj.text())
                elif (
                    isinstance(obj, (QPlainTextEdit, QTextEdit))
                    and not obj.isReadOnly()
                ):
                    obj.setPlainText(value)
                elif isinstance(obj, QComboBox) and obj.isEditable():
                    obj.setEditText(value)
                else:
                    raise ValueError("This field is read-only.")
            elif action == "choose" and type(value) is int:
                if (
                    isinstance(obj, QTabWidget)
                    and 0 <= value < obj.count()
                    and obj.isTabEnabled(value)
                ):
                    obj.setCurrentIndex(value)
                elif isinstance(obj, QComboBox) and 0 <= value < obj.count():
                    if (
                        not obj.model().flags(obj.model().index(value, 0))
                        & Qt.ItemFlag.ItemIsEnabled
                    ):
                        raise ValueError("This choice is unavailable.")
                    obj.setCurrentIndex(value)
                    obj.activated.emit(value)
                else:
                    raise ValueError("This choice is unavailable.")
            elif (
                action == "number"
                and type(value) in (int, float)
                and isinstance(obj, (QSpinBox, QDoubleSpinBox, QSlider))
                and obj.minimum() <= value <= obj.maximum()
            ):
                obj.setValue(value if isinstance(obj, QDoubleSpinBox) else int(value))
            elif action in (
                "select",
                "check",
                "expand",
                "activate",
                "move",
            ) and isinstance(obj, (QListWidget, QTableWidget, QTreeWidget)):
                item = self.objects.get(command.get("item"))
                if item is None or sip.isdeleted(item):
                    raise ValueError("This row changed. Select it again.")
                if isinstance(obj, QTableWidget):
                    valid = item.tableWidget() is obj
                elif isinstance(obj, QListWidget):
                    valid = item.listWidget() is obj
                else:
                    valid = item.treeWidget() is obj
                if not valid:
                    raise ValueError("This row no longer belongs to the control.")
                if (
                    action == "check"
                    and isinstance(obj, QListWidget)
                    and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                    and item.data(Qt.ItemDataRole.CheckStateRole) is not None
                    and type(value) is bool
                ):
                    item.setCheckState(
                        Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
                    )
                elif action == "expand" and isinstance(obj, QTreeWidget):
                    item.setExpanded(not item.isExpanded())
                elif (
                    action == "move"
                    and isinstance(obj, QListWidget)
                    and type(value) is int
                    and value in (-1, 1)
                ):
                    row = obj.row(item)
                    target = row + value
                    if 0 <= target < obj.count():
                        from PyQt6.QtCore import QModelIndex

                        obj.model().moveRow(
                            QModelIndex(), row, QModelIndex(), target + (value > 0)
                        )
                elif action in ("select", "activate"):
                    flag = QItemSelectionModel.SelectionFlag.Rows | (
                        QItemSelectionModel.SelectionFlag.Toggle
                        if command.get("additive") is True
                        else QItemSelectionModel.SelectionFlag.ClearAndSelect
                    )
                    obj.setCurrentItem(item, flag) if not isinstance(
                        obj, QTreeWidget
                    ) else obj.setCurrentItem(item, 0, flag)
                    if action == "activate":
                        if isinstance(obj, QTreeWidget):
                            obj.itemDoubleClicked.emit(item, 0)
                        else:
                            obj.itemDoubleClicked.emit(item)
                else:
                    raise ValueError("This row operation is unavailable.")
            else:
                raise ValueError("Unsupported settings operation.")
        except (ValueError, TypeError, RuntimeError) as error:
            self.error = str(error)
        if not self.closed:
            self.publish()

    def source_done(self, result):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        if not sip.isdeleted(self.source):
            self.source.close = self._source_close
            if self.embedded_view is not None and not sip.isdeleted(self.embedded_view):
                self.embedded_view.hide()
                self.embedded_view.setParent(self.source)
            for timer in self.source.findChildren(QTimer):
                timer.stop()
        if self.sensitive_signal is not None:
            try:
                self.sensitive_signal.disconnect(self.refresh_sensitive)
            except (TypeError, RuntimeError):
                pass
        self.objects.clear()
        self.tokens.clear()
        self.image_cache.clear()
        self.surface.dispose()
        if self.existing_surface:
            self.existing_surface.dispose()
        super().done(result)

    def reject(self):
        if not self.closed and not sip.isdeleted(self.source):
            self.source.reject()
        elif not self.closed:
            self.source_done(QDialog.DialogCode.Rejected)

    def dispose(self):
        self.reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()
