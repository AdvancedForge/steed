"""Agent-proposed watches; added only after the person reviews them.

In a chat, the person says "let me know when tickets go on sale". The agent
proposes a watch: the page, what matters, how often, and optionally what to
do then. Steed stores the proposal against the task and shows it in a review
popup; only the person's Add creates the watch, in a profile they pick, and
only they can let a "then" action start without asking. A proposal reads
nothing from the browser and grants no access.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from PyQt6.QtCore import QUrl

from browser.data_panel_format import stable_id
from services.command_broker import require_owner_thread
from services.task_policy import TaskPermissionError
from services.watches import INTERVAL_LABELS, INTERVALS

WATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_proposals (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), request_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, url TEXT NOT NULL, name TEXT NOT NULL,
 instruction TEXT NOT NULL, every TEXT NOT NULL, then_action TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','applied','denied','cancelled','interrupted','failed')),
 created_at TEXT NOT NULL, PRIMARY KEY(task_id,request_id)
);
"""
MAX_TEXT = 400


@dataclass(frozen=True)
class WatchProposal:
    task_id: str
    request_id: str
    sha256: str
    url: str
    name: str
    instruction: str
    every: str
    then_action: str
    state: str
    created_at: str

    @property
    def request_key(self):
        return "watch:" + self.task_id + ":" + self.request_id


def _text(value, field, limit=MAX_TEXT):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"Invalid {field}")
    return " ".join(value.split())


def capabilities():
    return {
        "intervals": dict(INTERVAL_LABELS),
        "limits": {"instruction": MAX_TEXT, "then": MAX_TEXT, "title": 200},
        "example": {
            "url": "https://tickets.example/tour",
            "title": "Tour tickets",
            "instruction": "tell me when tickets go on sale",
            "every": "hourly",
            "then": "open the page and tell me the cheapest seats",
            "request_id": "tour-tickets-1",
        },
        "notes": "The person reviews the watch, picks the profile and decides "
        "whether a then action starts without asking. Steed reads the page in "
        "the background with that profile's sign-ins; your agent judges changes "
        "from the before and after text only.",
    }


class WatchProposals:
    def __init__(self, service):
        self.service = service
        for row in self.db.execute(
            "SELECT task_id,request_id FROM watch_proposals WHERE state='awaiting_approval'"
        ).fetchall():
            self.update(row[0], row[1], "interrupted")

    @property
    def db(self):
        require_owner_thread(self.service)
        return self.service.store._db

    @property
    def available(self):
        return hasattr(self.service.profiles, "watches")

    def get(self, task_id, request_id):
        self.service.store.get(task_id)
        row = self.db.execute(
            "SELECT task_id,request_id,sha256,url,name,instruction,every,then_action,"
            "state,created_at FROM watch_proposals WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Watch proposal does not belong to this task")
        return WatchProposal(**dict(row))

    def list(self, task_id):
        self.service.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.db.execute(
                "SELECT request_id FROM watch_proposals WHERE task_id=? "
                "ORDER BY created_at,request_id",
                (task_id,),
            ).fetchall()
        ]

    def pending(self, task_id):
        return [p for p in self.list(task_id) if p.state == "awaiting_approval"]

    def propose(
        self,
        task_id,
        url,
        request_id,
        instruction="",
        every="hourly",
        then="",
        title="",
    ):
        self.service._usable()
        self.service._active(task_id)
        if not self.available or not stable_id(request_id):
            raise ValueError("Watches are unavailable or the request ID is invalid")
        found = QUrl(url) if isinstance(url, str) and len(url) <= 2048 else QUrl()
        if found.scheme() not in ("http", "https") or not found.host():
            raise ValueError("Only web pages (http or https) can be watched")
        if every not in INTERVALS:
            raise ValueError("every must be one of: " + ", ".join(INTERVALS))
        fields = {
            "url": found.toString(),
            "name": _text(title, "title", 200) or found.host(),
            "instruction": _text(instruction, "instruction"),
            "every": every,
            "then_action": _text(then, "then"),
        }
        digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
        try:
            previous = self.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.sha256 != digest:
                raise ValueError("Request ID is already bound to a different watch")
            return previous
        if (
            len(self.list(task_id)) >= 20
            or len(self.pending(task_id)) >= 3
            or self.db.execute(
                "SELECT COUNT(*) FROM watch_proposals WHERE state='awaiting_approval'"
            ).fetchone()[0]
            >= 32
        ):
            raise TaskPermissionError("watch_proposal_limit")
        with self.db:
            self.db.execute(
                "INSERT INTO watch_proposals VALUES (?,?,?,?,?,?,?,?,'awaiting_approval',?)",
                (
                    task_id,
                    request_id,
                    digest,
                    fields["url"],
                    fields["name"],
                    fields["instruction"],
                    fields["every"],
                    fields["then_action"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.service.store._append(
                task_id,
                "watch.requested",
                {"request_id": request_id, "sha256": digest},
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
            raise ValueError("Watch proposal is no longer awaiting this decision")
        with self.db:
            self.db.execute(
                "UPDATE watch_proposals SET state=? WHERE task_id=? AND request_id=?",
                (state, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "watch." + state,
                {"request_id": request_id, "sha256": previous.sha256},
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def resolve(
        self,
        task_id,
        request_id,
        *,
        approve=False,
        profile=None,
        instruction=None,
        every=None,
        then=None,
        then_mode="ask",
    ):
        """Trusted UI only. The person may adjust the wording before adding."""
        self.service._usable()
        self.service._active(task_id)
        if type(approve) is not bool:
            raise ValueError("Provide an explicit watch decision")
        proposal = self.get(task_id, request_id)
        if not approve:
            return self.update(task_id, request_id, "denied")
        if not isinstance(profile, str) or not profile or profile == "search":
            raise ValueError("Choose a normal profile")
        if proposal.state != "awaiting_approval":
            raise ValueError("Watch proposal is no longer awaiting this decision")
        try:
            self.service.profiles.watches.add(
                proposal.url,
                profile,
                proposal.name,
                proposal.instruction if instruction is None else instruction,
                None,
                "",
                proposal.every if every is None else every,
                then=proposal.then_action if then is None else then,
                then_mode=then_mode,
            )
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
