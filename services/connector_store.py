"""Request identity, content-free events and durable uncertain-write recovery."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

CONNECTOR_SCHEMA = """
    CREATE TABLE IF NOT EXISTS connector_calls (
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        request_id TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE,
        connector TEXT NOT NULL,
        tool TEXT NOT NULL,
        effect TEXT NOT NULL,
        destination TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        state TEXT NOT NULL,
        reference TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(task_id, request_id)
    );
"""


@dataclass(frozen=True)
class ConnectorReceipt:
    task_id: str
    request_id: str
    operation_id: str
    connector: str
    tool: str
    effect: str
    destination: str
    artifact_id: str
    fingerprint: str
    state: str
    reference: str
    text: str
    reason: str
    created_at: str
    updated_at: str


def request_fingerprint(connector, tool, effect, destination, artifact):
    data = [
        connector,
        tool,
        effect,
        destination,
        artifact.artifact_id if artifact else "",
        artifact.sha256 if artifact else "",
    ]
    return hashlib.sha256(json.dumps(data, ensure_ascii=True).encode()).hexdigest()


class ConnectorStore:
    def __init__(self, store):
        self.store = store
        rows = store._db.execute(
            "SELECT task_id, request_id, state, effect FROM connector_calls WHERE state IN ('requested', 'dispatched', 'reconciling')"
        ).fetchall()
        for row in rows:
            state = (
                "cancelled"
                if row["state"] == "requested"
                else "failed"
                if row["effect"] == "read" and row["state"] == "dispatched"
                else "uncertain"
            )
            self.update(row["task_id"], row["request_id"], state, reason="restart")

    def get(self, task_id, request_id):
        self.store.get(task_id)
        row = self.store._db.execute(
            "SELECT * FROM connector_calls WHERE task_id = ? AND request_id = ?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("App request does not belong to this task")
        return ConnectorReceipt(**dict(row))

    def list(self, task_id):
        self.store.get(task_id)
        return [
            ConnectorReceipt(**dict(row))
            for row in self.store._db.execute(
                "SELECT * FROM connector_calls WHERE task_id = ? ORDER BY created_at, request_id",
                (task_id,),
            )
        ]

    def create(self, task_id, request, fingerprint):
        if len(self.list(task_id)) >= 50:
            raise ValueError("Task app receipt limit reached")
        now = datetime.now(timezone.utc).isoformat()
        with self.store._db:
            self.store._db.execute(
                "INSERT INTO connector_calls(task_id, request_id, operation_id, connector, tool, effect, destination, artifact_id, fingerprint, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?, ?)",
                (
                    task_id,
                    request.request_id,
                    request.operation_id or uuid4().hex,
                    request.connector,
                    request.tool,
                    request.effect,
                    request.destination,
                    request.artifact.artifact_id if request.artifact else "",
                    fingerprint,
                    now,
                    now,
                ),
            )
            self.store._append(
                task_id,
                "app.requested",
                {
                    "request_id": request.request_id,
                    "connector": request.connector,
                    "tool": request.tool,
                    "effect": request.effect,
                },
            )
        return self.get(task_id, request.request_id)

    def update(self, task_id, request_id, state, *, reference="", text="", reason=""):
        self.get(task_id, request_id)
        if state not in {
            "requested",
            "dispatched",
            "reconciling",
            "succeeded",
            "not_applied",
            "failed",
            "cancelled",
            "uncertain",
        }:
            raise ValueError("Invalid app receipt state")
        with self.store._db:
            self.store._db.execute(
                "UPDATE connector_calls SET state = ?, reference = ?, text = ?, reason = ?, updated_at = ? WHERE task_id = ? AND request_id = ?",
                (
                    state,
                    reference,
                    text,
                    reason,
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                    request_id,
                ),
            )
            self.store._append(
                task_id, "app." + state, {"request_id": request_id, "reason": reason}
            )
        return self.get(task_id, request_id)
