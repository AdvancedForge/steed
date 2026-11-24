"""steed:activity: what your agents did, one day at a time."""

import sqlite3
from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.agent_activity import (
    permissions_line,
    summarize,
    task_line,
    totals_line,
    usage_line,
)
from ui.html_dialog import HtmlSettingsDialog


def day_name(day: date, today: date):
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return day.strftime("%A %-d %B %Y")


def local_time(stamp):
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M")
    except ValueError:
        return ""


class ActivityDialog(HtmlSettingsDialog):
    def __init__(self, service, parent=None, *, today=None):
        super().__init__(parent)
        self.service = service
        self.today = today or date.today()
        self.day = self.today
        self.setWindowTitle("Agent activity")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "What your agents did each day: sites, pages read, actions, "
                "permissions, and the tokens and cost their apps reported."
            )
        )
        days = QHBoxLayout()
        self.earlier = QPushButton("Previous day")
        self.earlier.clicked.connect(lambda: self.show_day(-1))
        self.heading = QLabel()
        self.heading.setAccessibleName("Day shown")
        self.later = QPushButton("Next day")
        self.later.clicked.connect(lambda: self.show_day(1))
        days.addWidget(self.earlier)
        days.addWidget(self.heading, 1, Qt.AlignmentFlag.AlignCenter)
        days.addWidget(self.later)
        layout.addLayout(days)
        self.totals = QLabel()
        self.totals.setWordWrap(True)
        layout.addWidget(self.totals)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Conversation or task", "When", "What it did", "Permissions", "Usage"]
        )
        self.table.setProperty(
            "empty",
            "No agent activity on this day. Conversations and tasks appear here "
            "once an agent works in Steed.",
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.note = QLabel(
            "Tokens and cost are what each assistant's app reported; some apps "
            "report tokens only, and none are estimated."
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        service.changed.connect(self.refresh)
        self.finished.connect(lambda _: self._disconnect())
        self.refresh()

    def _disconnect(self):
        try:
            self.service.changed.disconnect(self.refresh)
        except TypeError:
            pass

    def show_day(self, days):
        self.day = min(self.today, self.day + timedelta(days=days))
        self.refresh()

    def refresh(self):
        self.heading.setText(day_name(self.day, self.today))
        self.later.setVisible(self.day < self.today)
        try:
            days = summarize(self.service.store, self.day)
        except (sqlite3.Error, RuntimeError):
            days = []
        self.totals.setText(totals_line(days))
        self.totals.setVisible(bool(days))
        self.note.setVisible(any(d.turns for d in days))
        self.table.setRowCount(0)
        for day in days:
            row = self.table.rowCount()
            self.table.insertRow(row)
            title = day.objective[:120] + (f" ({day.agent})" if day.agent else "")
            when = local_time(day.first)
            if local_time(day.last) != when:
                when += "–" + local_time(day.last)
            for column, text in enumerate(
                (title, when, task_line(day), permissions_line(day), usage_line(day))
            ):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, day.task_id)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
