"""Content-free execution receipts; restart interrupts workers without replay."""

from dataclasses import dataclass
from datetime import datetime, timezone

WORKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_runs (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    request_id TEXT NOT NULL,
    extension_id TEXT NOT NULL REFERENCES workspace_extensions(extension_id),
    package_sha256 TEXT NOT NULL,
    entrypoint TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('awaiting_approval','running','cancelling','succeeded','failed','cancelled','interrupted')),
    reason TEXT NOT NULL,
    rpc_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(task_id,request_id)
);
CREATE TABLE IF NOT EXISTS worker_package_approvals (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    grant_id TEXT NOT NULL REFERENCES grants(grant_id),
    package_sha256 TEXT NOT NULL,
    PRIMARY KEY(task_id,grant_id,package_sha256)
);
"""


@dataclass(frozen=True)
class WorkerReceipt:
    task_id: str
    request_id: str
    extension_id: str
    package_sha256: str
    entrypoint: str
    state: str
    reason: str
    rpc_count: int
    created_at: str
    updated_at: str

    @property
    def request_key(self):
        return "worker:" + self.task_id + ":" + self.request_id


class WorkerStore:
    def __init__(self, store):
        self.store = store
        with store._db:
            store._db.execute("DELETE FROM worker_package_approvals")
        for row in list(
            store._db.execute(
                "SELECT task_id,request_id FROM worker_runs WHERE state IN ('awaiting_approval','running','cancelling')"
            )
        ):
            self.update(row[0], row[1], "interrupted", "restart")

    def get(self, task_id, request_id):
        self.store.get(task_id)
        row = self.store._db.execute(
            "SELECT * FROM worker_runs WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Worker receipt does not belong to this task")
        return WorkerReceipt(**dict(row))

    def list(self, task_id):
        self.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.store._db.execute(
                "SELECT request_id FROM worker_runs WHERE task_id=? ORDER BY created_at,request_id",
                (task_id,),
            )
        ]

    def create(self, task_id, request_id, package):
        self.store.get(task_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.store._db:
            self.store._db.execute(
                "INSERT INTO worker_runs VALUES (?,?,?,?,?,'awaiting_approval','',0,?,?)",
                (
                    task_id,
                    request_id,
                    package["extension_id"],
                    package["package_sha256"],
                    package["manifest"]["entrypoint"],
                    now,
                    now,
                ),
            )
            self.store._append(
                task_id,
                "worker.requested",
                {"request_id": request_id, "package_sha256": package["package_sha256"]},
            )
        return self.get(task_id, request_id)

    def update(self, task_id, request_id, state, reason="", rpc_count=0):
        previous = self.get(task_id, request_id)
        if previous.state not in {"awaiting_approval", "running", "cancelling"}:
            return previous
        with self.store._db:
            self.store._db.execute(
                "UPDATE worker_runs SET state=?,reason=?,rpc_count=?,updated_at=? WHERE task_id=? AND request_id=?",
                (
                    state,
                    reason,
                    rpc_count,
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                    request_id,
                ),
            )
            self.store._append(
                task_id,
                "worker." + state,
                {"request_id": request_id, "reason": reason, "rpc_count": rpc_count},
            )
        return self.get(task_id, request_id)

    def pending(self, task_id):
        return [r for r in self.list(task_id) if r.state == "awaiting_approval"]

    def remembered(self, task_id, grant_id, digest):
        self.store.get(task_id)
        return (
            self.store._db.execute(
                "SELECT 1 FROM worker_package_approvals WHERE task_id=? AND grant_id=? AND package_sha256=?",
                (task_id, grant_id, digest),
            ).fetchone()
            is not None
        )

    def approve(self, task_id, request_id, grant_id, remember=False):
        record = self.get(task_id, request_id)
        if record.state != "awaiting_approval":
            raise ValueError("Only a pending package can be approved")
        with self.store._db:
            if remember:
                self.store._db.execute(
                    "INSERT OR IGNORE INTO worker_package_approvals VALUES (?,?,?)",
                    (task_id, grant_id, record.package_sha256),
                )
            self.store._db.execute(
                "UPDATE worker_runs SET state='running',updated_at=? WHERE task_id=? AND request_id=?",
                (datetime.now(timezone.utc).isoformat(), task_id, request_id),
            )
            self.store._append(
                task_id,
                "worker.approved",
                {
                    "request_id": request_id,
                    "package_sha256": record.package_sha256,
                    "remember": remember,
                },
            )
        return self.get(task_id, request_id)
