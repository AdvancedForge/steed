"""Agent-proposed routines; added only after the person reviews them.

In a chat, the person says "every morning, tell me what's on my calendar".
The agent proposes a routine: a name, what to ask, when, and optionally
pages to read first. Steed checks it, stores it against the task and shows it
in a review popup where it can be changed; only the person's Add creates the
routine. A proposal reads nothing from the browser and grants no access.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from browser.data_panel_format import stable_id
from services.command_broker import require_owner_thread
from services.routines import DAYS, EVERY_HOURS, check, describe
from services.task_policy import TaskPermissionError

ROUTINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS routine_proposals (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), request_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','applied','denied','cancelled','interrupted','failed')),
 created_at TEXT NOT NULL, PRIMARY KEY(task_id,request_id)
);
"""


@dataclass(frozen=True)
class RoutineProposal:
    task_id: str
    request_id: str
    sha256: str
    name: str
    source: str
    state: str
    created_at: str

    @property
    def request_key(self):
        return "routine:" + self.task_id + ":" + self.request_id

    @property
    def routine(self):
        return json.loads(self.source)


def capabilities():
    return {
        "kinds": {
            "daily": "at (HH:MM) on days (0=Mon … 6=Sun)",
            "hours": f"every N hours, N in {list(EVERY_HOURS)}",
        },
        "days": list(DAYS),
        "limits": {"pages": 5, "ask": 4000, "name": 80},
        "example": {
            "name": "Morning brief",
            "ask": "Read these pages and tell me what needs my attention today.",
            "kind": "daily",
            "at": "08:00",
            "days": [0, 1, 2, 3, 4],
            "pages": ["https://calendar.example.org/"],
            "request_id": "morning-brief-1",
        },
        "steps": {
            "what": "Optional saved steps Steed runs itself, with no model, before "
            "reading: open, click (by label), type (label + text), choose (label + "
            "option), wait (for text), read. Prefer steps_from_this_chat=true after "
            "doing the task once here: Steed saves exactly the page actions that "
            "worked. If a step later breaks, the run comes to you with where it "
            "stopped.",
            "example": [
                {"open": "https://shop.example.org/orders"},
                {"click": "Open orders"},
                {"read": True},
            ],
        },
        "tell": "agent (default: each run comes to you) or changes (no assistant: "
        "Steed only tells the person when what it read changed).",
        "notes": "The person reviews and can change the routine before adding "
        "it. Each run starts a new conversation with you, containing the ask and "
        "the pages' text (read with the person's sign-ins). Anything more needs "
        "the usual permissions.",
    }


class RoutineProposals:
    def __init__(self, service):
        self.service = service
        for row in self.db.execute(
            "SELECT task_id,request_id FROM routine_proposals WHERE state='awaiting_approval'"
        ).fetchall():
            self.update(row[0], row[1], "interrupted")

    @property
    def db(self):
        require_owner_thread(self.service)
        return self.service.store._db

    @property
    def available(self):
        return hasattr(self.service.profiles, "routines")

    def get(self, task_id, request_id):
        self.service.store.get(task_id)
        row = self.db.execute(
            "SELECT task_id,request_id,sha256,name,source,state,created_at "
            "FROM routine_proposals WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Routine proposal does not belong to this task")
        return RoutineProposal(**dict(row))

    def list(self, task_id):
        self.service.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.db.execute(
                "SELECT request_id FROM routine_proposals WHERE task_id=? "
                "ORDER BY created_at,request_id",
                (task_id,),
            ).fetchall()
        ]

    def pending(self, task_id):
        return [p for p in self.list(task_id) if p.state == "awaiting_approval"]

    def propose(self, task_id, request_id, **fields):
        self.service._usable()
        self.service._active(task_id)
        if not self.available or not stable_id(request_id):
            raise ValueError("Routines are unavailable or the request ID is invalid")
        if fields.pop("steps_from_this_chat", False):
            # Exactly what worked in this chat, as Steed saw it: not a
            # description the agent could make up.
            done = list(self.service.step_logs.get(task_id, ()))
            if not done:
                raise ValueError(
                    "No page actions to save yet: do the steps in this chat first."
                )
            fields["steps"] = done + [{"read": True}]
        routine = check(fields)
        source = json.dumps(routine, sort_keys=True)
        digest = hashlib.sha256(source.encode()).hexdigest()
        try:
            previous = self.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.sha256 != digest:
                raise ValueError("Request ID is already bound to a different routine")
            return previous
        if (
            len(self.list(task_id)) >= 20
            or len(self.pending(task_id)) >= 3
            or self.db.execute(
                "SELECT COUNT(*) FROM routine_proposals WHERE state='awaiting_approval'"
            ).fetchone()[0]
            >= 32
        ):
            raise TaskPermissionError("routine_proposal_limit")
        with self.db:
            self.db.execute(
                "INSERT INTO routine_proposals VALUES (?,?,?,?,?,'awaiting_approval',?)",
                (
                    task_id,
                    request_id,
                    digest,
                    routine["name"],
                    source,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.service.store._append(
                task_id,
                "routine.requested",
                {"request_id": request_id, "sha256": digest, "when": describe(routine)},
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def update(self, task_id, request_id, state):
        previous = self.get(task_id, request_id)
        if previous.state != "awaiting_approval" or state not in (
            "applied",
            "denied",
            "cancelled",
            "interrupted",
            "failed",
        ):
            raise ValueError("Routine proposal is no longer awaiting this decision")
        with self.db:
            self.db.execute(
                "UPDATE routine_proposals SET state=? WHERE task_id=? AND request_id=?",
                (state, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "routine." + state,
                {"request_id": request_id, "sha256": previous.sha256},
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def resolve(
        self, task_id, request_id, *, approve=False, profile=None, routine=None
    ):
        """Trusted UI only: the person's (possibly changed) routine."""
        self.service._usable()
        self.service._active(task_id)
        if type(approve) is not bool:
            raise ValueError("Provide an explicit routine decision")
        proposal = self.get(task_id, request_id)
        if not approve:
            return self.update(task_id, request_id, "denied")
        if not isinstance(profile, str) or not profile or profile == "search":
            raise ValueError("Choose a normal profile")
        if proposal.state != "awaiting_approval":
            raise ValueError("Routine proposal is no longer awaiting this decision")
        try:
            self.service.profiles.routines.add(routine or proposal.routine, profile)
        except ValueError:
            self.update(task_id, request_id, "failed")
            raise
        return self.update(task_id, request_id, "applied")

    def cancel_task(self, task_id, *, interrupted=False):
        for proposal in self.pending(task_id):
            self.update(
                task_id,
                proposal.request_id,
                "interrupted" if interrupted else "cancelled",
            )
