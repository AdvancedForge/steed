"""Immutable task layout proposals; only native user review can apply them."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from browser.layout_format import parse_layout
from services.command_broker import require_owner_thread
from services.task_policy import TaskPermissionError

LAYOUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS layout_proposals (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), request_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','applying','applied','denied','cancelled','interrupted','failed')),
 created_at TEXT NOT NULL, PRIMARY KEY(task_id,request_id)
);
"""


@dataclass(frozen=True)
class LayoutProposal:
    task_id: str
    request_id: str
    sha256: str
    name: str
    state: str
    created_at: str

    @property
    def request_key(self):
        return "layout:" + self.task_id + ":" + self.request_id


class LayoutProposals:
    def __init__(self, service):
        self.service = service
        for row in service.store._db.execute(
            "SELECT task_id,request_id FROM layout_proposals WHERE state IN ('awaiting_approval','applying')"
        ).fetchall():
            self.update(row["task_id"], row["request_id"], "interrupted")

    @property
    def available(self):
        return callable(getattr(self.service.profiles, "commit_layout", None))

    def get(self, task_id, request_id):
        require_owner_thread(self.service)
        self.service.store.get(task_id)
        row = self.service.store._db.execute(
            "SELECT task_id,request_id,sha256,name,state,created_at FROM layout_proposals WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Layout proposal does not belong to this task")
        return LayoutProposal(**dict(row))

    def list(self, task_id):
        require_owner_thread(self.service)
        self.service.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.service.store._db.execute(
                "SELECT request_id FROM layout_proposals WHERE task_id=? ORDER BY created_at,request_id",
                (task_id,),
            ).fetchall()
        ]

    def pending(self, task_id):
        return [p for p in self.list(task_id) if p.state == "awaiting_approval"]

    def definition(self, proposal):
        require_owner_thread(self.service)
        row = self.service.store._db.execute(
            "SELECT source FROM layout_proposals WHERE task_id=? AND request_id=?",
            (proposal.task_id, proposal.request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Layout proposal is unavailable")
        definition = parse_layout(row[0])
        if definition.sha256 != proposal.sha256:
            raise ValueError("Layout source no longer matches the reviewed proposal")
        return definition

    def propose(self, task_id, source, request_id):
        require_owner_thread(self.service)
        self.service._usable()
        self.service._active(task_id)
        if not self.available:
            raise ValueError("Browser layout review is unavailable")
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", request_id
        ):
            raise ValueError("Use a stable layout request ID")
        definition = parse_layout(source)
        try:
            previous = self.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.sha256 != definition.sha256:
                raise ValueError("Layout request ID is already bound to another source")
            return previous
        count = self.service.store._db.execute(
            "SELECT COUNT(*) FROM layout_proposals WHERE state='awaiting_approval'"
        ).fetchone()[0]
        if (
            len(self.list(task_id)) >= 20
            or len(self.pending(task_id)) >= 3
            or count >= 32
        ):
            raise TaskPermissionError("layout_proposal_limit")
        with self.service.store._db:
            self.service.store._db.execute(
                "INSERT INTO layout_proposals VALUES (?,?,?,?,?,'awaiting_approval',?)",
                (
                    task_id,
                    request_id,
                    definition.sha256,
                    definition.name,
                    source,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.service.store._append(
                task_id,
                "layout.requested",
                {
                    "request_id": request_id,
                    "sha256": definition.sha256,
                },
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def update(self, task_id, request_id, state):
        previous = self.get(task_id, request_id)
        allowed = {
            "awaiting_approval": {"applying", "denied", "cancelled", "interrupted"},
            "applying": {"applied", "failed", "interrupted"},
        }
        if state not in allowed.get(previous.state, set()):
            raise ValueError("Layout request is no longer awaiting this decision")
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE layout_proposals SET state=? WHERE task_id=? AND request_id=?",
                (state, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "layout." + state,
                {
                    "request_id": request_id,
                    "sha256": previous.sha256,
                },
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def resolve(self, task_id, request_id, *, approve=False, base=None):
        """Trusted UI only; the broker exposes no approval command."""
        from browser.layout_preferences import preferences_from_config

        require_owner_thread(self.service)
        self.service._usable()
        self.service._active(task_id)
        if type(approve) is not bool:
            raise ValueError("Provide an explicit layout decision")
        proposal = self.get(task_id, request_id)
        if not approve:
            return self.update(task_id, request_id, "denied")
        if not self.available or base is None:
            raise ValueError("Review the current browser layout before applying")
        current = preferences_from_config(self.service.profiles.config)
        if current.to_dict() != base.to_dict():
            raise ValueError("Browser layout changed; review the updated preview")
        candidate = self.definition(proposal).preferences(current)
        self.update(task_id, request_id, "applying")
        try:
            self.service.profiles.commit_layout(candidate, base=current)
        except Exception:
            self.update(task_id, request_id, "failed")
            raise
        return self.update(task_id, request_id, "applied")

    def cancel_task(self, task_id):
        for proposal in self.pending(task_id):
            self.update(task_id, proposal.request_id, "cancelled")
