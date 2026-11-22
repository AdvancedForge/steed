"""Find in the active page, with serialized searches and stale-result guards."""

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)


class FindInput(QLineEdit):
    step_requested = pyqtSignal(bool)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.step_requested.emit(
                bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            )
            event.accept()
            return
        super().keyPressEvent(event)


class FindBar(QWidget):
    closed = pyqtSignal()

    def __init__(self, navigation, parent=None):
        super().__init__(parent)
        self.navigation = navigation
        self._page = None
        self._generation = 0
        self._flight = None
        self._pending = None
        self._result_connection = None
        self._loading_connection = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.find_next)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)
        self.input = FindInput()
        self.input.setPlaceholderText("Find in page…")
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._query_changed)
        self.input.step_requested.connect(self.find_next)
        layout.addWidget(self.input, 1)
        self.result_label = QLabel("")
        self.result_label.setMinimumWidth(70)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.result_label)
        self.previous_button = QPushButton("Previous")
        self.previous_button.setToolTip("Previous match (Shift+Enter / Shift+F3)")
        self.previous_button.clicked.connect(lambda: self.find_next(True))
        self.next_button = QPushButton("Next")
        self.next_button.setToolTip("Next match (Enter / F3)")
        self.next_button.clicked.connect(lambda: self.find_next())
        layout.addWidget(self.previous_button)
        layout.addWidget(self.next_button)
        self.match_case = QCheckBox("Match case")
        self.match_case.toggled.connect(self._query_changed)
        layout.addWidget(self.match_case)
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close find (Escape)")
        self.close_button.clicked.connect(self.close_find)
        layout.addWidget(self.close_button)
        self.setObjectName("findBar")
        escape = QShortcut(QKeySequence("Esc"), self)
        escape.setContext(Qt.ShortcutContext.WindowShortcut)
        escape.activated.connect(self.close_find)
        self.hide()
        self._update_buttons()

    def apply_style(self, colors):
        """The toolbar's colours and rounded controls, like the address bar."""
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"""
            #findBar {{ background: {colors["toolbar"]};
                border-bottom: 1px solid {colors["border"]}; }}
            #findBar QLabel, #findBar QCheckBox {{ color: {colors["muted"]};
                background: transparent; }}
            #findBar QLineEdit {{ background: {colors["field"]}; color: {colors["ink"]};
                border: 1px solid {colors["border"]}; border-radius: 15px;
                padding: 4px 12px; min-height: 22px; }}
            #findBar QLineEdit:focus {{ border-color: {colors["accent"]}; }}
            #findBar QPushButton {{ background: transparent; color: {colors["ink"]};
                border: 1px solid transparent; border-radius: 14px;
                padding: 4px 12px; }}
            #findBar QPushButton:hover {{ background: {colors["hover"]}; }}
            #findBar QPushButton:pressed {{ background: {colors["pressed"]}; }}
            #findBar QPushButton:disabled {{ color: {colors["muted"]}; }}
            """
        )

    def set_page(self, page):
        if page is self._page:
            return
        self._generation += 1
        self._timer.stop()
        self._flight = self._pending = None
        old = self._page
        if old is not None and not sip.isdeleted(old):
            old.findTextFinished.disconnect(self._result_connection)
            old.loadStarted.disconnect(self._loading_connection)
            self.navigation.find(old, "")
        self._page = page
        self.result_label.clear()
        if page is not None and not sip.isdeleted(page):
            self._result_connection = lambda result: self._receive_result(page, result)
            self._loading_connection = self._load_started
            page.findTextFinished.connect(self._result_connection)
            page.loadStarted.connect(self._loading_connection)
        self._update_buttons()
        if self.isVisible() and self.input.text():
            self.find_next()

    def open_find(self):
        self.show()
        self.input.setFocus()
        self.input.selectAll()
        if self.input.text():
            self.find_next()

    def close_find(self, *, clear_query=False):
        self._generation += 1
        self._timer.stop()
        self._pending = None
        self.hide()
        if clear_query:
            self.input.clear()
        self.result_label.clear()
        if self._page is not None and not sip.isdeleted(self._page):
            self.navigation.find(self._page, "")
        self.closed.emit()

    def _update_buttons(self):
        enabled = self._page is not None and bool(self.input.text())
        self.previous_button.setEnabled(enabled)
        self.next_button.setEnabled(enabled)

    def _query_changed(self, *_):
        self._generation += 1
        self._pending = None
        self._update_buttons()
        if not self.isVisible():
            return
        if not self.input.text():
            self.result_label.clear()
            if self._page is not None and not sip.isdeleted(self._page):
                self.navigation.find(self._page, "")
            return
        self.result_label.setText("Searching…")
        self._timer.start()

    def _load_started(self):
        self._generation += 1
        self._flight = self._pending = None
        self._timer.stop()
        self.result_label.clear()
        # A new document must be searched after loading, not using old results.

    def page_loaded(self):
        if self.isVisible() and self.input.text():
            self.find_next()

    def find_next(self, backward=False):
        self._timer.stop()
        if not self.isVisible():
            self.open_find()
            return
        page = self._page
        if page is None or sip.isdeleted(page):
            return
        self._generation += 1
        self._pending = (
            page,
            self.input.text(),
            bool(backward),
            self.match_case.isChecked(),
            self._generation,
        )
        self._dispatch()

    def _dispatch(self):
        if self._flight is not None or self._pending is None:
            return
        page, text, backward, case, generation = self._pending
        self._pending = None
        if not text:
            self.navigation.find(page, "")
            self.result_label.clear()
            return
        flight = {"page": page, "generation": generation, "result": None}
        self._flight = flight
        self.result_label.setText("Searching…")

        def completed(value):
            # New bindings return the native result despite documenting bool.
            result = (
                (value.activeMatch(), value.numberOfMatches())
                if hasattr(value, "numberOfMatches")
                else flight["result"]
            )
            found = result[1] > 0 if result is not None else bool(value)
            if not sip.isdeleted(self):
                QTimer.singleShot(0, lambda: self._complete(flight, found, result))

        self.navigation.find(
            page, text, backward=backward, case_sensitive=case, callback=completed
        )

    def _receive_result(self, page, result):
        if self._flight is not None and self._flight["page"] is page:
            self._flight["result"] = (result.activeMatch(), result.numberOfMatches())

    def _complete(self, flight, found, result):
        if sip.isdeleted(self) or self._flight is not flight:
            return
        self._flight = None
        if (
            self.isVisible()
            and flight["generation"] == self._generation
            and flight["page"] is self._page
        ):
            if not found:
                self.result_label.setText("No matches")
            elif result and result[1] > 0:
                self.result_label.setText(f"{result[0]} of {result[1]}")
            else:
                self.result_label.setText("Match found")
        self._dispatch()
