"""Which assistant helps with Steed's small jobs, chosen by you.

Write it for me, Help Me Write, history topics, organizing tabs, and the
ask, decide and watch steps of automations and watches each send one
question to an assistant. With more than one available (a model you added,
Codex, Claude Code), Steed asks once which to use instead of picking quietly,
and remembers it ("watch_agent"); Agents → Helper Assistant changes it.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from services import agent_once
from ui.html_dialog import HtmlSettingsDialog

QUESTION = (
    "Which assistant should help with writing, automations and watches? "
    "Steed sends it one question at a time, with no browser access. You can "
    "change it in Agents → Helper Assistant."
)


def choose(config, key):
    """Remember ``key`` as the helper."""
    if key not in dict(agent_once.helper_choices(config)):
        raise ValueError("That assistant is not available.")
    config.set("watch_agent", key)


def ensure(window, then):
    """Call ``then(command)`` with the helper: the one you chose, the only one
    there is, or the one you pick now. ``then(None)`` when there is none."""
    config = window.config
    choices = agent_once.helper_choices(config)
    if agent_once.helper_chosen(config) or not choices:
        then(agent_once.agent_command(config))
        return None
    if len(choices) == 1:
        choose(config, choices[0][0])
        then(agent_once.agent_command(config))
        return None
    dialog = HelperDialog(window, choices)
    dialog.then = then  # Called with the one you pick (not on Cancel).
    window._present_dialog(dialog, modal=False)
    return dialog


class HelperDialog(HtmlSettingsDialog):
    def __init__(self, window, choices, parent=None):
        super().__init__(parent or window)
        self.owner = window
        self.choices = choices
        self.then = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Choose a helper assistant")
        layout = QVBoxLayout(self)
        intro = QLabel(QUESTION)
        intro.setWordWrap(True)
        intro.setProperty("role", "note")
        layout.addWidget(intro)
        self.list = QListWidget()
        for _, label in choices:
            self.list.addItem(label)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _: self.use())
        layout.addWidget(self.list)
        row = QDialogButtonBox()
        use = QPushButton("Use this one")
        use.setProperty("primary", True)
        use.clicked.connect(self.use)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addButton(use, QDialogButtonBox.ButtonRole.AcceptRole)
        row.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(row)

    def use(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.choices):
            return
        config = self.owner.config
        choose(config, self.choices[row][0])
        then = self.then
        self.accept()
        if then is not None:
            then(agent_once.agent_command(config))
