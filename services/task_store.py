"""Persistent tasks and an append-only journal, owned by the application thread."""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.app_plans import PLAN_SCHEMA
from services.approval_rules import RULE_SCHEMA, ApprovalRule
from services.connector_access import ACCESS_SCHEMA
from services.connector_reviews import REVIEW_SCHEMA
from services.connector_store import CONNECTOR_SCHEMA
from services.data_panels import PANEL_SCHEMA
from services.layout_proposals import LAYOUT_SCHEMA
from services.recipe_proposals import RECIPE_SCHEMA
from services.start_page_proposals import PAGE_SCHEMA
from services.task_artifacts import (
    MAX_TASK_ARTIFACT_BYTES,
    MAX_TASK_ARTIFACTS,
    ArtifactSummary,
    TaskArtifact,
    artifact_input,
)
from services.task_messages import MESSAGE_SCHEMA, TaskMessages
from services.task_recovery import (
    MAX_RECOVERY_ARTIFACTS,
    MAX_RECOVERY_BYTES,
    MAX_RECOVERY_DEPTH,
    MAX_RECOVERY_FILES,
    RECOVERY_SCHEMA,
)
from services.task_workspace import WORKSPACE_SCHEMA, TaskWorkspace
from services.worker_store import WORKER_SCHEMA

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
        if version not in range(21):
            self._db.close()
            raise RuntimeError("Unsupported task database version")
        with self._db:
            # Legacy approvals cannot authorize any dependency environment.
            columns = {
                row[1] for row in self._db.execute("PRAGMA table_info(worker_runs)")
            }
            if columns:
                if "runtime" not in columns:
                    self._db.execute(
                        "ALTER TABLE worker_runs ADD COLUMN runtime TEXT NOT NULL DEFAULT 'stdlib'"
                    )
                if "environment_sha256" not in columns:
                    self._db.execute(
                        "ALTER TABLE worker_runs ADD COLUMN environment_sha256 TEXT NOT NULL DEFAULT ''"
                    )
                    self._db.execute("DROP TABLE IF EXISTS worker_package_approvals")
            self._db.executescript(
                """
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
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    title TEXT NOT NULL,
                    format TEXT NOT NULL CHECK(format IN ('text', 'markdown')),
                    byte_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    source_events TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS artifacts_by_task
                    ON artifacts(task_id, created_at, artifact_id);
                PRAGMA user_version = 20;
            """
                + CONNECTOR_SCHEMA
                + PLAN_SCHEMA
                + REVIEW_SCHEMA
                + WORKSPACE_SCHEMA
                + ACCESS_SCHEMA
                + WORKER_SCHEMA
                + RECIPE_SCHEMA
                + RULE_SCHEMA
                + RECOVERY_SCHEMA
                + MESSAGE_SCHEMA
                + LAYOUT_SCHEMA
                + PANEL_SCHEMA
                + PAGE_SCHEMA
            )
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

    def approval_rules(self):
        self._check()
        return [
            ApprovalRule.from_row(row)
            for row in self._db.execute(
                "SELECT * FROM approval_rules ORDER BY mode DESC, created_at, rule_id"
            )
        ]

    def approve_with_rule(
        self,
        task_id,
        grant_id,
        expires_at,
        *,
        remember="once",
        profile_key="",
        rule=None,
        account_access=False,
    ):
        """Grant, remembered choice and provenance commit in one transaction."""
        grant = self.grant(task_id, grant_id)
        if (
            grant.status != "requested"
            or self.get(task_id).status not in ACTIVE_STATUSES
        ):
            raise ValueError("Only an active pending request can be approved")
        if remember not in {"once", "global", "profile"} or (
            remember == "profile" and not profile_key
        ):
            raise ValueError("Invalid remembered approval choice")
        rule_id = rule.rule_id if rule else None
        with self._db:
            if remember != "once":
                if len(self.approval_rules()) >= 200:
                    raise ValueError("Remove a saved approval before adding another")
                rule_id = uuid4().hex
                self._db.execute(
                    "INSERT INTO approval_rules VALUES (?, ?, ?, ?, ?)",
                    (
                        rule_id,
                        remember,
                        profile_key if remember == "profile" else "",
                        json.dumps(grant.scope, sort_keys=True),
                        timestamp(),
                    ),
                )
                self._append(
                    task_id,
                    "permission.remembered",
                    {"rule_id": rule_id, "mode": remember},
                )
            elif rule is not None and not any(
                r.rule_id == rule_id for r in self.approval_rules()
            ):
                raise ValueError("Saved approval is no longer available")
            self._resolve_grant(
                grant,
                "approved",
                expires_at=expires_at,
                reason="saved_approval:" + rule_id
                if rule_id
                else "account_access"
                if account_access
                else "",
            )
            if account_access:
                self._append(task_id, "permission.account_access", {})
            if rule_id:
                self._db.execute(
                    "INSERT INTO approval_rule_grants VALUES (?, ?)",
                    (grant_id, rule_id),
                )
        return self.grant(task_id, grant_id)

    def grant_rule_id(self, grant_id):
        self._check()
        row = self._db.execute(
            "SELECT rule_id FROM approval_rule_grants WHERE grant_id = ?", (grant_id,)
        ).fetchone()
        return row[0] if row else None

    def remove_approval_rule(self, rule_id):
        self._check()
        links = []
        with self._db:
            removed = self._db.execute(
                "DELETE FROM approval_rules WHERE rule_id = ?", (rule_id,)
            )
            if not removed.rowcount:
                raise ValueError("Saved approval is no longer available")
            for row in self._db.execute(
                "SELECT g.task_id, g.grant_id FROM grants g JOIN approval_rule_grants r ON r.grant_id = g.grant_id WHERE r.rule_id = ? AND g.status = 'approved'",
                (rule_id,),
            ):
                links.append((row["task_id"], row["grant_id"]))
                self._append(
                    row["task_id"], "permission.forgotten", {"rule_id": rule_id}
                )
        return links

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
                TaskMessages(self).end(task_id)
                self._db.execute(
                    "UPDATE task_recoveries SET status = 'ended' WHERE task_id = ? AND status = 'requested'",
                    (task_id,),
                )
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

    def recovery(self, task_id):
        self.get(task_id)
        row = self._db.execute(
            "SELECT * FROM task_recoveries WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return {"status": "none"}
        if len(row["snapshot"].encode("utf-8")) > MAX_RECOVERY_BYTES:
            raise ValueError("Invalid retained recovery size")
        snapshot = json.loads(row["snapshot"])
        if not isinstance(snapshot, dict):
            raise ValueError("Invalid retained recovery context")
        return {
            "status": row["status"],
            "source_task_id": row["source_task_id"],
            "context": snapshot,
        }

    def request_recovery(self, task_id):
        if (
            self.get(task_id).status != "created"
            or self.grants(task_id)
            or self.artifacts(task_id)
            or TaskWorkspace(self).list(task_id)
        ):
            raise ValueError("Request recovery before grants or work")
        current = self.recovery(task_id)
        if current["status"] != "none":
            return current
        with self._db:
            self._db.execute(
                "INSERT INTO task_recoveries(task_id, status, created_at) VALUES (?, 'requested', ?)",
                (task_id, timestamp()),
            )
            self._append(task_id, "recovery.requested", {})
        return self.recovery(task_id)

    def recovery_ancestors(self, task_id):
        ancestors, current = [], task_id
        while current is not None:
            if current in ancestors or len(ancestors) >= MAX_RECOVERY_DEPTH:
                raise ValueError("Recovery chain exceeds its limit")
            ancestors.append(current)
            current = self.recovery(current).get("source_task_id")
        return ancestors

    def events_by_sequence(self, task_id, sequences):
        self.get(task_id)
        if not sequences:
            return []
        if len(sequences) > 200:
            raise ValueError("Too many recovery source events")
        rows = self._db.execute(
            "SELECT * FROM events WHERE task_id = ? AND sequence IN ("
            + ",".join("?" for _ in sequences)
            + ") ORDER BY sequence",
            (task_id, *sequences),
        )
        return [
            TaskEvent(
                row["sequence"],
                task_id,
                row["kind"],
                json.loads(row["payload"]),
                row["created_at"],
            )
            for row in rows
        ]

    def artifact_digest(self, task_id, artifact_id):
        if not artifact_id:
            return ""
        try:
            return self.artifact(task_id, artifact_id, include_content=False).sha256
        except KeyError:
            # Deleting a draft must not erase the evidence that its body was sent.
            for event in self.recent_events(
                task_id, limit=1000, kinds=("artifact.stored", "artifact.imported")
            ):
                if event.payload.get("artifact_id") == artifact_id:
                    return event.payload.get("sha256", "")
        return ""

    def approve_recovery(
        self, task_id, source_task_id, artifacts, snapshot, effects, file_ids=()
    ):
        if (
            self.get(task_id).status != "created"
            or self.grants(task_id)
            or self.artifacts(task_id)
            or TaskWorkspace(self).list(task_id)
            or self.recovery(task_id)["status"] != "requested"
        ):
            raise ValueError("Recovery request is no longer available")
        if self.get(source_task_id).status not in TERMINAL_STATUSES:
            raise ValueError("Choose a finished or interrupted task")
        if any(artifact.task_id != source_task_id for artifact in artifacts):
            raise ValueError("Recovery draft belongs to another task")
        if (
            len(artifacts) > MAX_RECOVERY_ARTIFACTS
            or not isinstance(file_ids, (list, tuple))
            or len(file_ids) > MAX_RECOVERY_FILES
            or any(not isinstance(value, str) for value in file_ids)
            or len(set(file_ids)) != len(file_ids)
            or len(self.recovery_ancestors(source_task_id)) >= MAX_RECOVERY_DEPTH
        ):
            raise ValueError("Recovery selection exceeds its limit")
        imported = []
        with self._db:
            for artifact in artifacts:
                artifact_id, now = uuid4().hex, timestamp()
                self._db.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        artifact_id,
                        task_id,
                        artifact.title,
                        artifact.format,
                        artifact.byte_count,
                        artifact.sha256,
                        "[]",
                        now,
                        artifact.content,
                    ),
                )
                imported.append(
                    {
                        "artifact_id": artifact_id,
                        "source_artifact_id": artifact.artifact_id,
                        "source_events": list(artifact.source_events),
                        "sha256": artifact.sha256,
                    }
                )
                self._append(
                    task_id,
                    "artifact.imported",
                    {
                        "artifact_id": artifact_id,
                        "source_task_id": source_task_id,
                        "source_artifact_id": artifact.artifact_id,
                        "sha256": artifact.sha256,
                        "byte_count": artifact.byte_count,
                    },
                )
            imported_files = TaskWorkspace(self)._import_selected(
                task_id, source_task_id, file_ids
            )
            snapshot = {
                **snapshot,
                "imported_artifacts": imported,
                "imported_files": imported_files,
            }
            encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_RECOVERY_BYTES:
                raise ValueError(
                    "Recovery snapshot exceeds 128 KiB; omit evidence or reduce the selection"
                )
            self._db.execute(
                "UPDATE task_recoveries SET status = 'approved', source_task_id = ?, snapshot = ? WHERE task_id = ?",
                (
                    source_task_id,
                    encoded,
                    task_id,
                ),
            )
            for source, receipt, digest in effects:
                self._db.execute(
                    "INSERT INTO recovery_effects VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task_id,
                        source,
                        receipt.request_id,
                        receipt.connector,
                        receipt.tool,
                        receipt.effect,
                        receipt.destination,
                        digest,
                    ),
                )
            self._append(
                task_id,
                "recovery.approved",
                {
                    "source_task_id": source_task_id,
                    "artifact_count": len(imported),
                    "file_count": len(imported_files),
                    "evidence_included": snapshot["evidence_included"],
                },
            )
        return self.recovery(task_id)

    def deny_recovery(self, task_id):
        if (
            self.get(task_id).status not in ACTIVE_STATUSES
            or self.recovery(task_id)["status"] != "requested"
        ):
            raise ValueError("Recovery request is no longer pending")
        with self._db:
            self._db.execute(
                "UPDATE task_recoveries SET status = 'denied' WHERE task_id = ?",
                (task_id,),
            )
            self._append(task_id, "recovery.denied", {})

    def recovery_effects(self, task_id, request):
        self.get(task_id)
        digest = request.artifact.sha256 if request.artifact else ""
        return self._db.execute(
            "SELECT source_task_id, request_id FROM recovery_effects WHERE task_id = ? AND connector = ? AND tool = ? AND effect = ? AND destination = ? AND (sha256 = '' OR sha256 = ?)",
            (
                task_id,
                request.connector,
                request.tool,
                request.effect,
                request.destination,
                digest,
            ),
        ).fetchall()

    def clear_shared_app_results(self, source_task_id, request_ids):
        """Scrub saved summaries in the caller's receipt-clearing transaction."""
        self.get(source_task_id)
        for row in self._db.execute(
            "SELECT task_id FROM task_recoveries WHERE source_task_id = ?",
            (source_task_id,),
        ):
            task_id = row["task_id"]
            try:
                context = self.recovery(task_id)["context"]
            except (ValueError, TypeError) as error:
                raise RuntimeError("Invalid saved recovery context") from error
            receipts = context.get("app_receipts", [])
            if not isinstance(receipts, list) or len(receipts) > 50:
                raise RuntimeError("Invalid saved app receipt summaries")
            changed = False
            for receipt in receipts:
                if not isinstance(receipt, dict):
                    raise RuntimeError("Invalid saved app receipt summary")
                if (
                    receipt.get("task_id") == source_task_id
                    and receipt.get("request_id") in request_ids
                ):
                    receipt["reference"], receipt["text"] = "", ""
                    receipt["text_truncated"] = False
                    changed = True
            if changed:
                encoded = json.dumps(context, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode("utf-8")) > MAX_RECOVERY_BYTES:
                    raise RuntimeError("Invalid saved recovery size")
                self._db.execute(
                    "UPDATE task_recoveries SET snapshot = ? WHERE task_id = ?",
                    (encoded, task_id),
                )
                self._append(
                    task_id,
                    "recovery.results_cleared",
                    {"source_task_id": source_task_id},
                )

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

    def store_artifact(
        self, task_id, title, content, format="markdown", source_events=()
    ):
        if self.get(task_id).status not in ACTIVE_STATUSES:
            raise ValueError("Task is no longer active")
        title, size, digest, sources = artifact_input(
            title, content, format, source_events
        )
        for sequence in sources:
            row = self._db.execute(
                "SELECT kind FROM events WHERE task_id = ? AND sequence = ?",
                (task_id, sequence),
            ).fetchone()
            if row is None or row["kind"] != "page.inspected":
                raise ValueError(
                    "Artifact source is not this task's recorded page inspection"
                )
        count, total = self._db.execute(
            "SELECT count(*), coalesce(sum(byte_count), 0) FROM artifacts WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if count >= MAX_TASK_ARTIFACTS or total + size > MAX_TASK_ARTIFACT_BYTES:
            raise ValueError("Task artifact storage limit reached")
        artifact_id, now = uuid4().hex, timestamp()
        with self._db:
            self._db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    task_id,
                    title,
                    format,
                    size,
                    digest,
                    json.dumps(sources),
                    now,
                    content,
                ),
            )
            self._append(
                task_id,
                "artifact.stored",
                {
                    "artifact_id": artifact_id,
                    "byte_count": size,
                    "sha256": digest,
                    "source_events": sources,
                },
            )
        return self.artifact(task_id, artifact_id, include_content=False)

    def artifact(self, task_id, artifact_id, *, include_content=True):
        self.get(task_id)
        columns = (
            "*"
            if include_content
            else "artifact_id, task_id, title, format, byte_count, sha256, source_events, created_at"
        )
        row = self._db.execute(
            f"SELECT {columns} FROM artifacts WHERE task_id = ? AND artifact_id = ?",
            (task_id, artifact_id),
        ).fetchone()
        if row is None:
            raise KeyError("Artifact does not belong to this task")
        values = dict(row)
        values["source_events"] = tuple(json.loads(values["source_events"]))
        return (TaskArtifact if include_content else ArtifactSummary)(**values)

    def artifacts(self, task_id):
        self.get(task_id)
        rows = self._db.execute(
            "SELECT artifact_id FROM artifacts WHERE task_id = ? ORDER BY created_at, artifact_id",
            (task_id,),
        ).fetchall()
        return [self.artifact(task_id, row[0], include_content=False) for row in rows]

    def delete_artifact(self, task_id, artifact_id):
        """Trusted user removal; keep a content-free deletion receipt."""
        artifact = self.artifact(task_id, artifact_id, include_content=False)
        with self._db:
            self._db.execute(
                "DELETE FROM artifacts WHERE task_id = ? AND artifact_id = ?",
                (task_id, artifact_id),
            )
            self._append(
                task_id, "artifact.deleted", {"artifact_id": artifact.artifact_id}
            )

    def recent_events(self, task_id, *, limit=200, kinds=()):
        """Trusted UI tail, bounded and scoped without changing client cursors."""
        self.get(task_id)
        if not 1 <= limit <= 1000 or len(kinds) > 20:
            raise ValueError("Invalid event tail limit")
        condition = (
            " AND kind IN (" + ",".join("?" for _ in kinds) + ")" if kinds else ""
        )
        rows = self._db.execute(
            "SELECT * FROM events WHERE task_id = ?"
            + condition
            + " ORDER BY sequence DESC LIMIT ?",
            (task_id, *kinds, limit),
        ).fetchall()
        return [
            TaskEvent(
                row["sequence"],
                row["task_id"],
                row["kind"],
                json.loads(row["payload"]),
                row["created_at"],
            )
            for row in reversed(rows)
        ]

    def close(self):
        if self._closed:
            return
        self._check()
        self._db.close()
        self._closed = True
