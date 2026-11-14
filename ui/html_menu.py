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
    def __init__(self, menu, owner, anchor, *, context=False, beside=None):
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
        # A sub-menu panel and the panel it came from.
        self.child = self.parent_menu = None
        # Right-click menus have no settings section and open at the cursor.
        self.context_menu = context
        self.surface = HtmlSurface("menu", self, label="browser menu")
        self.surface.command.connect(self.command)
        self.surface.reloading.connect(self.reload)
        body = QVBoxLayout(self)
        chrome = getattr(getattr(owner, "address_bar", None), "colors", {})
        # Lumen menus are the same rounded, frosted panels as liquid glass.
        self.glass = bool(chrome.get("glass") or chrome.get("lumen"))
        self.motion = bool(chrome.get("glide"))
        self.backdrop = self.frosted = None
        if self.glass:
            # Rounded glass without asking the desktop for see-through
            # windows: paintEvent draws what is behind (a snapshot of the
            # browser), frosted inside the rounded shape, and the page draws
            # its tint over it.
            self.setStyleSheet("QDialog QWidget { background: transparent; }")
            self.surface.transparent = True
            body.setContentsMargins(0, 0, 0, 0)
        else:
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
        if beside is not None and self.geometry().intersects(beside):
            # No room on the right: open on the left, never over it.
            screen = self.screen().availableGeometry() if self.screen() else None
            x = beside.left() - self.width() - 4
            if screen is None or x >= screen.left():
                self.move(x, self.y())
        self.timer.start()
        self.publish()

    def _snapshot(self):
        """What the browser window shows behind this panel, and a frosted copy."""
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QColor, QPainter, QPixmap

        from ui.chrome_style import blurred

        backdrop = QPixmap(self.size())
        backdrop.fill(QColor(self._colors().get("menu", "#202020")))
        owner = self.owner
        area = QRect(owner.mapFromGlobal(self.pos()), self.size())
        shown = area.intersected(owner.rect())
        if not shown.isEmpty():
            painter = QPainter(backdrop)
            painter.drawPixmap(shown.topLeft() - area.topLeft(), owner.grab(shown))
            painter.end()
        self.backdrop, self.frosted = backdrop, blurred(backdrop)

    def showEvent(self, event):
        if self.glass:
            self._snapshot()
        super().showEvent(event)
        if self.motion:
            from ui.lumen import open_window

            open_window(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.glass and self.isVisible():
            self._snapshot()
            self.update()

    def paintEvent(self, event):
        if not self.glass or self.backdrop is None:
            return super().paintEvent(event)
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainter, QPainterPath

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, self.backdrop)  # The corners: what is behind.
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        painter.setClipPath(shape)
        painter.drawPixmap(0, 0, self.frosted)
        painter.end()

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
            for key in ("menu", "field", "ink", "muted", "border", "hover", "accent")
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
                "glass": self.glass,
                "motion": self.motion,
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
        # Hovering only opens sub-menus; everything else needs a click.
        if command.get("action") == "hover" and action.menu() is None:
            return
        if command.get("action") not in ("hover", "activate"):
            return
        x, y = command.get("x"), command.get("y")
        if (
            type(x) not in (int, float)
            or type(y) not in (int, float)
            or not (0 <= x <= self.width() and 0 <= y <= self.height())
        ):
            return
        point = self.surface.view.mapToGlobal(QPoint(int(x), int(y)))
        if action.menu() is not None:
            self._open_child(action.menu(), point.y())
            return
        self.close()
        if self.parent_menu is not None and not self.parent_menu.closed:
            self.parent_menu.close()
        self.owner._menu_anchor = point
        try:
            action.trigger()
        finally:
            self.owner._menu_anchor = None

    def _open_child(self, menu, top):
        """A sub-menu opens as its own panel beside this one, never over it."""
        if self.child is not None and not self.child.closed:
            if self.child.stack[0] is menu:
                return
            self.child.close()
        anchor = QPoint(self.geometry().right() + 4, top)
        child = HtmlMenu(
            menu, self.owner, anchor, context=self.context_menu, beside=self.geometry()
        )
        child.parent_menu = self
        child.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.child = child
        child.show()

    def done(self, result):
        if self.child is not None and not self.child.closed:
            self.child.close()
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
