"""HTML browser prompts with Qt-compatible return values and native OS pickers."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog as NativeInputDialog
from PyQt6.QtWidgets import QMessageBox as NativeMessageBox

from ui.html_dialog import HtmlPresentation


class MessageBox(HtmlPresentation, NativeMessageBox):
    @classmethod
    def _message(cls, parent, title, text, buttons, default):
        box = cls(parent)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default == cls.StandardButton.NoButton:
            default = next(
                (
                    button
                    for button in (
                        cls.StandardButton.No,
                        cls.StandardButton.Cancel,
                        cls.StandardButton.Ok,
                    )
                    if button & buttons
                ),
                cls.StandardButton.NoButton,
            )
        if default != cls.StandardButton.NoButton:
            box.setDefaultButton(default)
        try:
            return box.exec()
        finally:
            box.deleteLater()

    @classmethod
    def question(
        cls,
        parent,
        title,
        text,
        buttons=NativeMessageBox.StandardButton.Yes
        | NativeMessageBox.StandardButton.No,
        defaultButton=NativeMessageBox.StandardButton.NoButton,
    ):
        return cls._message(parent, title, text, buttons, defaultButton)

    @classmethod
    def information(
        cls,
        parent,
        title,
        text,
        buttons=NativeMessageBox.StandardButton.Ok,
        defaultButton=NativeMessageBox.StandardButton.NoButton,
    ):
        return cls._message(parent, title, text, buttons, defaultButton)

    warning = information
    critical = information


class InputDialog(HtmlPresentation, NativeInputDialog):
    @classmethod
    def getText(
        cls,
        parent,
        title,
        label,
        echo=0,
        text="",
        flags=Qt.WindowType.Widget,
        inputMethodHints=Qt.InputMethodHint.ImhNone,
    ):
        from PyQt6.QtWidgets import QLineEdit

        box = cls(parent)
        box.setWindowTitle(title)
        box.setLabelText(label)
        box.setTextEchoMode(QLineEdit.EchoMode(echo))
        box.setTextValue(text)
        return cls._result(box)

    @classmethod
    def getItem(
        cls,
        parent,
        title,
        label,
        items,
        current=0,
        editable=True,
        flags=Qt.WindowType.Widget,
        inputMethodHints=Qt.InputMethodHint.ImhNone,
    ):
        box = cls(parent)
        box.setWindowTitle(title)
        box.setLabelText(label)
        box.setComboBoxItems(items)
        box.setComboBoxEditable(editable)
        if items and 0 <= current < len(items):
            box.setTextValue(items[current])
        return cls._result(box)

    @staticmethod
    def _result(box):
        # QInputDialog constructs its layout lazily; polishing it avoids showing
        # a native frame while allowing the HTML renderer to enumerate controls.
        box.setVisible(False)
        box.ensurePolished()
        try:
            result = box.exec()
            return box.textValue(), result == box.DialogCode.Accepted
        finally:
            box.deleteLater()
