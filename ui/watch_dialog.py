"""Watch with Agent: what to keep an eye on, what matters, how often, and
optionally what your agent should then do about it."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from services.agent_once import agent_name
from services.watches import INTERVAL_LABELS


class WatchDialog(QDialog):
    watched = pyqtSignal(dict)

    def __init__(
        self,
        service,
        url,
        profile,
        title,
        selector,
        text,
        *,
        selected=None,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.url, self.profile, self.title_ = url, profile, title
        self.selector, self.text = selector, text
        self.setWindowTitle("Watch with Agent")
        layout = QVBoxLayout(self)
        what = (
            f"“{' '.join(selected.split())[:80]}” on {title}"
            if selected
            else title or url
        )
        intro = QLabel(f"Keep an eye on {what}.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(QLabel("What should Steed tell you about?"))
        self.instruction = QLineEdit()
        self.instruction.setPlaceholderText(
            "e.g. when it's under $50 (empty: any change)"
        )
        self.instruction.setMaxLength(400)
        layout.addWidget(self.instruction)
        layout.addWidget(QLabel("How often to look"))
        self.every = QComboBox()
        for key, label in INTERVAL_LABELS.items():
            self.every.addItem(label, key)
        self.every.setCurrentIndex(list(INTERVAL_LABELS).index("hourly"))
        layout.addWidget(self.every)
        layout.addWidget(QLabel("Then (optional)"))
        self.then = QLineEdit()
        self.then.setPlaceholderText("e.g. add it to my cart, or email me a summary")
        self.then.setMaxLength(400)
        layout.addWidget(self.then)
        self.then_now = QCheckBox("Start without asking me first")
        self.then_now.setToolTip(
            "Off: the notification asks before your agent starts. Either way it "
            "asks your permission before acting on a site."
        )
        self.then_now.setEnabled(False)
        self.then.textChanged.connect(
            lambda text: self.then_now.setEnabled(bool(text.strip()))
        )
        layout.addWidget(self.then_now)
        agent = service.agent()
        note = QLabel(
            f"When the page changes, {agent_name(agent)} decides whether it is what "
            "you asked about. It sees only the before and after text."
            if agent
            else "No agent to judge changes was found (Claude Code or Codex), so "
            "Steed will tell you about any change."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        watch = QPushButton("Watch")
        watch.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(watch)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def save(self):
        try:
            watch = self.service.add(
                self.url,
                self.profile,
                self.title_,
                self.instruction.text(),
                self.selector,
                self.text,
                self.every.currentData(),
                then=self.then.text(),
                then_mode="now" if self.then_now.isChecked() else "ask",
            )
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.watched.emit(watch)
        self.accept()
