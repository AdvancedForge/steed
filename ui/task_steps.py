"""The Steps tab of Task Permissions: what an agent did, step by step, with
a picture of the page after each step when pictures are on."""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

THUMBNAIL = QSize(120, 75)


class TaskSteps(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.task_id = None
        self.shown = None  # What the list shows now, to skip needless rebuilds.
        layout = QVBoxLayout(self)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("role", "note")
        layout.addWidget(self.note)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setIconSize(THUMBNAIL)
        self.list.currentItemChanged.connect(self._preview)
        split.addWidget(self.list)
        self.picture = QLabel("Choose a step with a picture to see it here.")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setMinimumWidth(240)
        self.picture.setWordWrap(True)
        split.addWidget(self.picture)
        split.setSizes([320, 480])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.delete = QPushButton("Delete Pictures")
        self.delete.clicked.connect(self._delete)
        row.addWidget(self.delete)
        layout.addLayout(row)

    def show_task(self, task_id):
        replay = getattr(self.service, "replay", None)
        if replay is None:
            return
        on = replay.enabled()
        self.note.setText(
            "Each step your agent took, in order. Pictures show the page just "
            "after a step, when it was on screen; they stay on this computer."
            if on
            else "Each step your agent took, in order. To also keep a picture "
            "of the page after each step, turn on Keep a picture of each agent "
            "step in Settings → Agents."
        )
        steps = replay.steps(task_id) if task_id else []
        shown = (task_id, len(steps), sum(1 for *_, p in steps if p))
        if shown == self.shown:
            return
        self.shown, self.task_id = shown, task_id
        self.list.clear()
        self.picture.setPixmap(QPixmap())
        self.picture.setText("Choose a step with a picture to see it here.")
        for when, sentence, path in steps:
            item = QListWidgetItem(f"{when}  {sentence}")
            if path is not None:
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setIcon(
                    QIcon(
                        QPixmap(str(path)).scaled(
                            THUMBNAIL,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                )
            self.list.addItem(item)
        if not steps:
            self.list.addItem("No steps yet.")
        self.delete.setEnabled(shown[2] > 0)
        self.list.scrollToBottom()

    def _preview(self, item, _previous=None):
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not path:
            self.picture.setPixmap(QPixmap())
            self.picture.setText("No picture for this step.")
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.picture.setText("This picture was deleted.")
            return
        self.picture.setPixmap(
            pixmap.scaled(
                self.picture.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _delete(self):
        if self.task_id:
            self.service.replay.forget(self.task_id)
            self.shown = None
            self.show_task(self.task_id)
