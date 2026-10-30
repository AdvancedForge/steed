"""HTML dropdowns backed by the browser's existing QAction registry."""

from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from ui.html_dialog import place_popup
from ui.html_surface import HtmlSurface


class HtmlMenu(QDialog):
    def __init__(self, menu, owner, anchor, *, context=False):
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.section = next(
            (name for name, item in owner._layout_menus.items() if item is menu),
            "general",
        )
        self.context = token_hex(16)
        self.actions = {}
        self.sequence = 0
        self.stack = [menu]
        self.closed = False
        # Right-click menus have no settings section and open at the cursor.
        self.context_menu = context
        self.surface = HtmlSurface("menu", self, label="browser menu")
        self.surface.command.connect(self.command)
        self.surface.reloading.connect(self.reload)
        body = QVBoxLayout(self)
        body.setContentsMargins(1, 1, 1, 1)
        body.addWidget(self.surface)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.publish)
        menu.aboutToShow.emit()
        self.anchor = anchor
        visible = [a for a in menu.actions() if a.isVisible()]
        height = sum(11 if a.isSeparator() else 35 for a in visible)
        place_popup(
            self,
            owner,
            anchor,
            (290, min(660, height + 40))
            if context
            else (350, min(660, 100 + len(menu.actions()) * 39)),
        )
        self.timer.start()
        self.publish()

    def reload(self):
        self.context = token_hex(16)
        self.sequence = 0
        self.publish()

    def publish(self):
        if self.closed:
            return
        menu = self.stack[-1]
        if sip.isdeleted(menu):
            self.close()
            return
        old = {
            id(action): token
            for token, action in self.actions.items()
            if not sip.isdeleted(action)
        }
        actions, rows = {}, []
        for action in menu.actions():
            if not action.isVisible():
                continue
            token = old.get(id(action), token_hex(12))
            actions[token] = action
            rows.append(
                {
                    "id": token,
                    "text": action.text().replace("&", ""),
                    "separator": action.isSeparator(),
                    "enabled": action.isEnabled(),
                    "checked": action.isChecked(),
                    "checkable": action.isCheckable(),
                    "submenu": action.menu() is not None,
                    "editor": bool(action.property("steed_editor")),
                    "shortcut": action.shortcut().toString(),
                }
            )
        self.actions = actions
        self.surface.publish(
            {
                "context": self.context,
                "title": menu.title() or "Browser menu",
                "back": len(self.stack) > 1,
                "context_menu": self.context_menu,
                "rows": rows,
                "dark": self.owner.profile_manager.registry.page_style.dark(
                    self.owner.profile_manager.current_profile_name
                ),
            }
        )

    def command(self, command):
        if self.closed or command.get("context") != self.context:
            return
        sequence = command.get("sequence")
        if type(sequence) is not int or sequence <= self.sequence:
            return
        self.sequence = sequence
        if command.get("action") == "close":
            self.reject()
            return
        if command.get("action") == "settings":
            self.close()
            self.owner._open_settings(self.section)
            return
        if command.get("action") == "back" and len(self.stack) > 1:
            self.stack.pop()
            self.reload()
            return
        action = self.actions.get(command.get("id"))
        if (
            action is None
            or sip.isdeleted(action)
            or sip.isdeleted(self.stack[-1])
            or action not in self.stack[-1].actions()
            or not action.isEnabled()
            or not action.isVisible()
            or action.isSeparator()
        ):
            return
        if (
            command.get("action") == "hover"
            and action.menu() is None
            and not action.property("steed_editor")
        ):
            return
        if command.get("action") not in ("hover", "activate"):
            return
        if action.menu() is not None:
            self.stack.append(action.menu())
            action.menu().aboutToShow.emit()
            self.reload()
            return
        x, y = command.get("x"), command.get("y")
        if (
            type(x) not in (int, float)
            or type(y) not in (int, float)
            or not (0 <= x <= self.width() and 0 <= y <= self.height())
        ):
            return
        point = self.surface.view.mapToGlobal(QPoint(int(x), int(y)))
        self.close()
        self.owner._menu_anchor = point
        try:
            action.trigger()
        finally:
            self.owner._menu_anchor = None

    def done(self, result):
        if not self.closed:
            self.closed = True
            self.timer.stop()
            self.surface.dispose()
        super().done(result)

    def closeEvent(self, event):
        self.done(QDialog.DialogCode.Rejected)
        event.accept()

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)
