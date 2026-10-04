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
    CREATE TABLE IF NOT EXISTS connector_probes (
        task_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        source_task_id TEXT NOT NULL,
        source_request_id TEXT NOT NULL,
        PRIMARY KEY(task_id, request_id),
        FOREIGN KEY(task_id, request_id) REFERENCES connector_calls(task_id, request_id),
        FOREIGN KEY(source_task_id, source_request_id) REFERENCES connector_calls(task_id, request_id)
    );
    CREATE TABLE IF NOT EXISTS connector_result_clears (
        task_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        cleared_at TEXT NOT NULL,
        PRIMARY KEY(task_id, request_id),
        FOREIGN KEY(task_id, request_id) REFERENCES connector_calls(task_id, request_id)
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
    result_cleared_at: str = ""


def request_fingerprint(connector, tool, effect, destination, artifact, inputs=None):
    data = [
        connector,
        tool,
        effect,
        destination,
        artifact.artifact_id if artifact else "",
        artifact.sha256 if artifact else "",
    ]
    # Keep pre-v7 receipts retryable: only typed values extend the old hash.
    if inputs:
        data.append(sorted(inputs.items()))
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
            "SELECT c.*, COALESCE(r.cleared_at, '') AS result_cleared_at FROM connector_calls c LEFT JOIN connector_result_clears r USING(task_id, request_id) WHERE c.task_id = ? AND c.request_id = ?",
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
                "SELECT c.*, COALESCE(r.cleared_at, '') AS result_cleared_at FROM connector_calls c LEFT JOIN connector_result_clears r USING(task_id, request_id) WHERE c.task_id = ? ORDER BY c.created_at, c.request_id",
                (task_id,),
            )
        ]

    def probe_source(self, task_id, request_id):
        self.get(task_id, request_id)
        row = self.store._db.execute(
            "SELECT source_task_id, source_request_id FROM connector_probes WHERE task_id = ? AND request_id = ?",
            (task_id, request_id),
        ).fetchone()
        return tuple(row) if row is not None else None

    def create(self, task_id, request, fingerprint, *, source=None):
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
            if source is not None:
                self.store._db.execute(
                    "INSERT INTO connector_probes VALUES (?, ?, ?, ?)",
                    (task_id, request.request_id, *source),
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
        with self.store._db:
            self._update(task_id, request_id, state, reference, text, reason)
        return self.get(task_id, request_id)

    def clear_results(self, task_id, request_ids=None):
        """Trusted user action; suppress future result retention without erasing identity."""
        receipts = {r.request_id: r for r in self.list(task_id)}
        if request_ids is None:
            request_ids = tuple(receipts)
        if (
            not isinstance(request_ids, (list, tuple))
            or len(request_ids) > 50
            or any(not isinstance(item, str) for item in request_ids)
            or len(set(request_ids)) != len(request_ids)
        ):
            raise ValueError("Choose at most 50 distinct owned app receipts")
        if any(item not in receipts for item in request_ids):
            raise KeyError("App request does not belong to this task")
        selected = [
            item for item in request_ids if not receipts[item].result_cleared_at
        ]
        if not selected:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        with self.store._db:
            for request_id in selected:
                self.store._db.execute(
                    "INSERT INTO connector_result_clears VALUES (?, ?, ?)",
                    (task_id, request_id, now),
                )
                self.store._db.execute(
                    "UPDATE connector_calls SET reference = '', text = '', updated_at = ? WHERE task_id = ? AND request_id = ?",
                    (now, task_id, request_id),
                )
                self.store._append(
                    task_id, "app.result_cleared", {"request_id": request_id}
                )
            self.store.clear_shared_app_results(task_id, selected)
        return len(selected)

    def finish_probe(
        self, task_id, request_id, state, *, reference="", text="", reason=""
    ):
        """Keep the probe and any confirmed source outcome in one transaction."""
        source = self.probe_source(task_id, request_id)
        with self.store._db:
            self._update(task_id, request_id, state, reference, text, reason)
            if source and state in {"succeeded", "not_applied"}:
                original = self.get(*source)
                if original.state in {"uncertain", "reconciling"}:
                    self._update(*source, state, reference, text, "read_only_followup")
        return self.get(task_id, request_id)

    def _update(self, task_id, request_id, state, reference, text, reason):
        previous = self.get(task_id, request_id)
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
        if (
            previous.state in {"succeeded", "not_applied"}
            and previous.reason == "read_only_followup"
        ):
            return  # An older worker cannot erase independently confirmed evidence.
        if previous.result_cleared_at:
            reference, text = "", ""
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
