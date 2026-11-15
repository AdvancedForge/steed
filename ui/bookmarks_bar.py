"""The bookmarks bar under the toolbar (View → Bookmarks Bar, Ctrl+Shift+B).

Bookmarks in the order you keep them; a folder is one button that opens its
bookmarks. What does not fit goes under » at the end. Click opens in this
tab, middle-click or Ctrl+click in a new one.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QMenu, QToolButton, QWidget

MAX_TEXT = 26


def _short(text):
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def items_of(bookmarks):
    """[("link", title, url)] and [("folder", name, [(title, url)])], in order:
    a folder stands where its first bookmark is."""
    items, folders = [], {}
    for bookmark in bookmarks:
        url = bookmark.get("url") or ""
        title = bookmark.get("title") or url
        folder = (bookmark.get("folder") or "").strip()
        if not url:
            continue
        if not folder:
            items.append(("link", title, url))
        elif folder in folders:
            folders[folder][2].append((title, url))
        else:
            folders[folder] = ("folder", folder, [(title, url)])
            items.append(folders[folder])
    return items


class LinkButton(QToolButton):
    """A bookmark: click opens here, middle-click or Ctrl+click in a new tab."""

    def __init__(self, title, url, bar):
        super().__init__(bar)
        self.url, self.bar = url, bar
        self.setText(_short(title))
        self.setToolTip(f"{title}\n{url}")
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.clicked.connect(self._clicked)

    def _clicked(self):
        control = QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
        self.bar.opened.emit(self.url, bool(control))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.bar.opened.emit(self.url, True)
            return
        super().mouseReleaseEvent(event)


class BookmarksBar(QWidget):
    opened = pyqtSignal(str, bool)  # url, in a new tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BookmarksBar")
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(8, 0, 8, 4)
        self.row.setSpacing(2)
        self.buttons = []
        self.more = QToolButton(self)
        self.more.setText("»")
        self.more.setToolTip("More bookmarks")
        self.more.setAutoRaise(True)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more)
        self.more.setMenu(self.more_menu)
        self.empty = QToolButton(self)
        self.empty.setText("Bookmarks you add (Ctrl+D) appear here")
        self.empty.setEnabled(False)
        self.empty.setAutoRaise(True)
        self.items = []

    def set_bookmarks(self, bookmarks):
        self.items = items_of(bookmarks)
        for button in self.buttons:
            button.deleteLater()
        self.buttons = []
        while self.row.count():
            self.row.takeAt(0)
        for kind, title, value in self.items:
            if kind == "link":
                button = LinkButton(title, value, self)
            else:
                button = QToolButton(self)
                button.setText(_short(title) + " ▾")
                button.setAutoRaise(True)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                button.setMenu(self._menu(value, button))
            self.buttons.append(button)
            self.row.addWidget(button)
        self.row.addWidget(self.empty)
        self.row.addStretch(1)
        self.row.addWidget(self.more)
        self.empty.setVisible(not self.items)
        self._fit()

    def _menu(self, links, parent):
        menu = QMenu(parent)
        for title, url in links:
            action = QAction(_short(title), menu)
            action.setToolTip(url)
            action.triggered.connect(lambda _=False, u=url: self.opened.emit(u, False))
            menu.addAction(action)
        return menu

    def _fit(self):
        """Show the buttons that fit; the rest go under »."""
        room = self.width() - 40
        used, hidden = 0, []
        for button, item in zip(self.buttons, self.items, strict=True):
            width = button.sizeHint().width() + self.row.spacing()
            fits = used + width <= room and not hidden
            button.setVisible(fits)
            if fits:
                used += width
            else:
                hidden.append(item)
        self.more_menu.clear()
        for kind, title, value in hidden:
            if kind == "link":
                action = QAction(_short(title), self.more_menu)
                action.triggered.connect(
                    lambda _=False, u=value: self.opened.emit(u, False)
                )
                self.more_menu.addAction(action)
            else:
                sub = self.more_menu.addMenu(_short(title))
                for link_title, url in value:
                    action = QAction(_short(link_title), sub)
                    action.triggered.connect(
                        lambda _=False, u=url: self.opened.emit(u, False)
                    )
                    sub.addAction(action)
        self.more.setVisible(bool(hidden))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()
