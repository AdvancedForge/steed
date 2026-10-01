"""Persistent tasks and an append-only journal, owned by the application thread."""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ACTIVE_STATUSES = frozenset({"created", "running"})
TERMINAL_STATUSES = frozenset({"completed", "cancelled", "interrupted", "failed"})


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    objective: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskEvent:
    sequence: int
    task_id: str
    kind: str
    payload: dict
    created_at: str


@dataclass(frozen=True)
class TaskGrant:
    grant_id: str
    task_id: str
    scope: dict
    status: str
    created_at: str
    expires_at: str


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    """Atomically persist state changes and their journal entries."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create with private permissions before SQLite writes any task metadata.
        with path.open("ab"):
            path.chmod(0o600)
        self._owner = threading.get_ident()
        self._closed = False
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self._db.close()
            raise RuntimeError("Unsupported task database version")
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'created', 'running', 'completed', 'cancelled',
                        'interrupted', 'failed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_task ON events(task_id, sequence);
                CREATE TRIGGER IF NOT EXISTS events_no_update
                    BEFORE UPDATE ON events BEGIN
                    SELECT RAISE(ABORT, 'Task events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete
                    BEFORE DELETE ON events BEGIN
                    SELECT RAISE(ABORT, 'Task events are append-only'); END;
                CREATE TABLE IF NOT EXISTS grants (
                    grant_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'requested', 'approved', 'denied', 'revoked', 'expired',
                        'exhausted', 'ended')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX IF NOT EXISTS grants_one_live_per_task
                    ON grants(task_id) WHERE status IN ('requested', 'approved');
                PRAGMA user_version = 2;
            """)
        # No engine sessions are restored or commands replayed after a restart.
        for task in self.list_tasks():
            if task.status in ACTIVE_STATUSES:
                self.transition(task.task_id, "interrupted", reason="restart")

    def _check(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Task storage must be accessed on its owning thread")
        if self._closed:
            raise RuntimeError("Task storage is closed")

    def _append(self, task_id: str, kind: str, payload: dict):
        self._db.execute(
            "INSERT INTO events(task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (
                task_id,
                kind,
                json.dumps(payload, sort_keys=True, allow_nan=False),
                timestamp(),
            ),
        )

    def create(self, objective: str) -> TaskRecord:
        self._check()
        if (
            not isinstance(objective, str)
            or not objective.strip()
            or len(objective) > 4096
        ):
            raise ValueError("Provide a nonempty objective of at most 4096 characters")
        task_id, now = uuid4().hex, timestamp()
        with self._db:
            self._db.execute(
                "INSERT INTO tasks VALUES (?, ?, 'created', ?, ?)",
                (task_id, objective.strip(), now, now),
            )
            self._append(task_id, "task.created", {})
        return self.get(task_id)

    def request_grant(self, task_id, scope) -> TaskGrant:
        if self.get(task_id).status not in ACTIVE_STATUSES:
            raise ValueError("Task is no longer active")
        if any(
            grant.status in ("requested", "approved") for grant in self.grants(task_id)
        ):
            raise ValueError("This task already has a pending or approved grant")
        grant_id = uuid4().hex
        with self._db:
            self._db.execute(
                "INSERT INTO grants(grant_id, task_id, scope, status, created_at) "
                "VALUES (?, ?, ?, 'requested', ?)",
                (
                    grant_id,
                    task_id,
                    json.dumps(scope, sort_keys=True, allow_nan=False),
                    timestamp(),
                ),
            )
            self._append(
                task_id, "grant.requested", {"grant_id": grant_id, "scope": scope}
            )
        return self.grant(task_id, grant_id)

    def grant(self, task_id, grant_id) -> TaskGrant:
        self.get(task_id)
        row = self._db.execute(
            "SELECT * FROM grants WHERE task_id = ? AND grant_id = ?",
            (task_id, grant_id),
        ).fetchone()
        if row is None:
            raise KeyError("Grant does not belong to this task")
        values = dict(row)
        values["scope"] = json.loads(values["scope"])
        return TaskGrant(**values)

    def grants(self, task_id=None) -> list[TaskGrant]:
        self._check()
        if task_id is not None:
            self.get(task_id)
        rows = self._db.execute(
            "SELECT task_id, grant_id FROM grants "
            + ("WHERE task_id = ? " if task_id is not None else "")
            + "ORDER BY created_at, grant_id",
            (task_id,) if task_id is not None else (),
        ).fetchall()
        return [self.grant(row["task_id"], row["grant_id"]) for row in rows]

    def resolve_grant(self, task_id, grant_id, status, *, expires_at="", reason=""):
        grant = self.grant(task_id, grant_id)
        allowed = {
            "requested": {"approved", "denied", "ended"},
            "approved": {"revoked", "expired", "exhausted", "ended"},
        }
        if status not in allowed.get(grant.status, set()):
            raise ValueError("Invalid grant state transition")
        if self.get(task_id).status not in ACTIVE_STATUSES:
            raise ValueError("Task is no longer active")
        with self._db:
            self._resolve_grant(grant, status, expires_at=expires_at, reason=reason)
        return self.grant(task_id, grant_id)

    def _resolve_grant(self, grant, status, *, expires_at="", reason=""):
        self._db.execute(
            "UPDATE grants SET status = ?, expires_at = ? WHERE grant_id = ?",
            (status, expires_at or grant.expires_at, grant.grant_id),
        )
        self._append(
            grant.task_id,
            f"grant.{status}",
            {"grant_id": grant.grant_id, "reason": reason}
            if reason
            else {"grant_id": grant.grant_id},
        )

    def get(self, task_id: str) -> TaskRecord:
        self._check()
        row = self._db.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError("Unknown task")
        return TaskRecord(**dict(row))

    def list_tasks(self) -> list[TaskRecord]:
        self._check()
        return [
            TaskRecord(**dict(row))
            for row in self._db.execute(
                "SELECT * FROM tasks ORDER BY created_at, task_id"
            )
        ]

    def transition(self, task_id: str, status: str, *, reason: str = "") -> TaskRecord:
        current = self.get(task_id)
        if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            raise ValueError("Unknown task status")
        if current.status == status:
            return current
        if current.status not in ACTIVE_STATUSES or status == "created":
            raise ValueError("Invalid task state transition")
        with self._db:
            if status in TERMINAL_STATUSES:
                for grant in self.grants(task_id):
                    if grant.status in ("requested", "approved"):
                        self._resolve_grant(grant, "ended", reason=status)
            self._db.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, timestamp(), task_id),
            )
            self._append(
                task_id, f"task.{status}", {"reason": reason} if reason else {}
            )
        return self.get(task_id)

    def record_event(self, task_id: str, kind: str, payload: dict):
        self.get(task_id)
        with self._db:
            self._append(task_id, kind, payload)

    def events(
        self, task_id: str, *, after: int = 0, limit: int = 100
    ) -> list[TaskEvent]:
        self.get(task_id)
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid event cursor or limit")
        rows = self._db.execute(
            "SELECT * FROM events WHERE task_id = ? AND sequence > ? ORDER BY sequence LIMIT ?",
            (task_id, after, limit),
        )
        return [
            TaskEvent(
                row["sequence"],
                row["task_id"],
                row["kind"],
                json.loads(row["payload"]),
                row["created_at"],
            )
            for row in rows
        ]

    def close(self):
        if self._closed:
            return
        self._check()
        self._db.close()
        self._closed = True
