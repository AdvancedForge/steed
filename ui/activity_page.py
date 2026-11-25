"""steed:activity: what your agents did, a day or a week at a time, what
each assistant used, and an optional alert when a day's cost passes a limit."""

import sqlite3
from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from services.agent_activity import (
    agents_line,
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


def local_time(stamp, *, with_day=False):
    try:
        moment = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return ""
    return moment.strftime("%a %H:%M" if with_day else "%H:%M")


# Settings → the alert: off, or a day's reported cost in US dollars.
ALERTS = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0)


class ActivityDialog(HtmlSettingsDialog):
    def __init__(self, service, parent=None, *, today=None, config=None):
        super().__init__(parent)
        self.service = service
        self.config = config
        self.today = today or date.today()
        self.day = self.today
        self.week = False
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
        self.span = QPushButton("Show the week")
        self.span.clicked.connect(lambda: self._span_changed(0 if self.week else 1))
        days.addWidget(self.span)
        layout.addLayout(days)
        self.totals = QLabel()
        self.totals.setWordWrap(True)
        layout.addWidget(self.totals)
        self.agents = QLabel()
        self.agents.setWordWrap(True)
        layout.addWidget(self.agents)
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
        alert = QHBoxLayout()
        alert.addWidget(QLabel("Tell me when a day's reported cost passes"))
        self.alert = QComboBox()
        self.alert.setAccessibleName("Cost alert")
        self.alert.addItems(["Never"] + [f"${value:.0f}" for value in ALERTS[1:]])
        current = float(config.get("agent_cost_alert", 0.0)) if config else 0.0
        self.alert.setCurrentIndex(ALERTS.index(current) if current in ALERTS else 0)
        self.alert.currentIndexChanged.connect(self._alert_changed)
        alert.addWidget(self.alert)
        alert.addStretch()
        layout.addLayout(alert)
        service.changed.connect(self.refresh)
        self.finished.connect(lambda _: self._disconnect())
        self.refresh()

    def _disconnect(self):
        try:
            self.service.changed.disconnect(self.refresh)
        except TypeError:
            pass

    def _alert_changed(self, index):
        if self.config is not None:
            self.config.set("agent_cost_alert", ALERTS[index])

    def _span_changed(self, index):
        self.week = index == 1
        self.span.setText("Show one day" if self.week else "Show the week")
        self.earlier.setText("Previous week" if self.week else "Previous day")
        self.later.setText("Next week" if self.week else "Next day")
        self.refresh()

    def show_day(self, steps):
        days = steps * (7 if self.week else 1)
        self.day = min(self.today, self.day + timedelta(days=days))
        self.refresh()

    def refresh(self):
        name = day_name(self.day, self.today)
        self.heading.setText(
            (
                "The week to "
                + (name.lower() if self.day >= self.today - timedelta(1) else name)
            )
            if self.week
            else name
        )
        self.later.setVisible(self.day < self.today)
        try:
            days = summarize(self.service.store, self.day, days=7 if self.week else 1)
        except (sqlite3.Error, RuntimeError):
            days = []
        self.totals.setText(totals_line(days))
        self.totals.setVisible(bool(days))
        self.agents.setText(agents_line(days))
        self.agents.setVisible(bool(self.agents.text()))
        self.note.setVisible(any(d.turns for d in days))
        self.table.setRowCount(0)
        for day in days:
            row = self.table.rowCount()
            self.table.insertRow(row)
            title = day.objective[:120] + (f" ({day.agent})" if day.agent else "")
            when = local_time(day.first, with_day=self.week)
            last = local_time(day.last, with_day=self.week)
            if last != when:
                when += "–" + last
            for column, text in enumerate(
                (title, when, task_line(day), permissions_line(day), usage_line(day))
            ):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, day.task_id)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
