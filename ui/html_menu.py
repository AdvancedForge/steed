"""HTML dropdowns backed by the browser's existing QAction registry."""

from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from ui.html_dialog import place_popup
from ui.html_surface import HtmlSurface

# A compact popup: rows and separators in pixels (see menu.css).
WIDTH, ROW, SEPARATOR, MAX_HEIGHT = 290, 31, 11, 560


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
        border = self._colors().get("border")
        if border:  # The 1px margin is the menu's border, in the toolbar's colour.
            self.setStyleSheet(f"QDialog {{ background: {border}; }}")
        body.addWidget(self.surface)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.publish)
        menu.aboutToShow.emit()
        self.anchor = anchor
        place_popup(self, owner, anchor, (WIDTH, self._height(menu)))
        self.timer.start()
        self.publish()

    def _height(self, menu):
        """Just tall enough for the menu's rows (it scrolls past MAX_HEIGHT)."""
        visible = [a for a in menu.actions() if a.isVisible()]
        rows = sum(SEPARATOR if a.isSeparator() else ROW for a in visible)
        chrome = 16 if self.context_menu and len(self.stack) == 1 else 92
        return min(MAX_HEIGHT, rows + chrome)

    def _fit(self):
        """Resize to the menu now shown, keeping the top left corner."""
        height = self._height(self.stack[-1])
        if self.screen() is not None:
            bottom = self.screen().availableGeometry().bottom() - 8
            height = min(height, bottom - self.y())
        self.resize(self.width(), max(120, height))

    def _colors(self):
        """The toolbar's colours, so the menu matches the browser around it."""
        colors = getattr(getattr(self.owner, "address_bar", None), "colors", None)
        if not isinstance(colors, dict):
            return {}
        return {
            key: colors[key]
            for key in ("toolbar", "field", "ink", "muted", "border", "hover", "accent")
            if isinstance(colors.get(key), str)
        }

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
                "colors": self._colors(),
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
            self._fit()
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
            self._fit()
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
