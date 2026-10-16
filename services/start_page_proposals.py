"""Task-owned page proposals; application requires an explicit native review."""

from dataclasses import dataclass
from datetime import datetime, timezone

from data_panel_format import stable_id
from services.command_broker import require_owner_thread
from services.task_policy import TaskPermissionError
from start_page_format import parse_page, state_digest

PAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS start_page_proposals (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), request_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','applying','applied','denied','cancelled','interrupted','failed')),
 created_at TEXT NOT NULL, PRIMARY KEY(task_id,request_id)
);
"""


@dataclass(frozen=True)
class PageProposal:
    task_id: str
    request_id: str
    sha256: str
    name: str
    state: str
    created_at: str

    @property
    def request_key(self):
        return "start-page:" + self.task_id + ":" + self.request_id


class StartPageProposals:
    def __init__(self, service):
        self.service = service
        for row in self.db.execute(
            "SELECT task_id,request_id FROM start_page_proposals WHERE state IN ('awaiting_approval','applying')"
        ).fetchall():
            self.update(row[0], row[1], "interrupted")

    @property
    def db(self):
        require_owner_thread(self.service)
        return self.service.store._db

    @property
    def available(self):
        return hasattr(self.service.profiles, "start_pages")

    def get(self, task_id, request_id):
        self.service.store.get(task_id)
        row = self.db.execute(
            "SELECT task_id,request_id,sha256,name,state,created_at FROM start_page_proposals WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Page proposal does not belong to this task")
        return PageProposal(**dict(row))

    def list(self, task_id):
        self.service.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.db.execute(
                "SELECT request_id FROM start_page_proposals WHERE task_id=? ORDER BY created_at,request_id",
                (task_id,),
            ).fetchall()
        ]

    def pending(self, task_id):
        return [p for p in self.list(task_id) if p.state == "awaiting_approval"]

    def definition(self, proposal):
        row = self.db.execute(
            "SELECT source FROM start_page_proposals WHERE task_id=? AND request_id=?",
            (proposal.task_id, proposal.request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Page proposal is unavailable")
        definition = parse_page(row[0])
        if definition.sha256 != proposal.sha256:
            raise ValueError("Page source no longer matches the proposal")
        return definition

    def propose(self, task_id, source, request_id):
        self.service._usable()
        self.service._active(task_id)
        if not self.available or not stable_id(request_id):
            raise ValueError("Page review is unavailable or request ID is invalid")
        definition = parse_page(source)
        try:
            previous = self.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.sha256 != definition.sha256:
                raise ValueError("Request ID is already bound to a different page")
            return previous
        if (
            len(self.list(task_id)) >= 20
            or len(self.pending(task_id)) >= 3
            or self.db.execute(
                "SELECT COUNT(*) FROM start_page_proposals WHERE state='awaiting_approval'"
            ).fetchone()[0]
            >= 32
        ):
            raise TaskPermissionError("page_proposal_limit")
        with self.db:
            self.db.execute(
                "INSERT INTO start_page_proposals VALUES (?,?,?,?,?,'awaiting_approval',?)",
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
                "start_page.requested",
                {"request_id": request_id, "sha256": definition.sha256},
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
            raise ValueError("Page proposal is no longer awaiting this decision")
        with self.db:
            self.db.execute(
                "UPDATE start_page_proposals SET state=? WHERE task_id=? AND request_id=?",
                (state, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "start_page." + state,
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
        profile_key=None,
        base=None,
        all_profiles=False,
    ):
        """Trusted UI only: check the exact profile identity and reviewed state."""
        self.service._usable()
        self.service._active(task_id)
        if type(approve) is not bool or type(all_profiles) is not bool:
            raise ValueError("Provide an explicit page decision")
        proposal = self.get(task_id, request_id)
        if not approve:
            return self.update(task_id, request_id, "denied")
        registry = self.service.profiles
        if (
            not self.available
            or profile == "search"
            or not isinstance(profile, str)
            or profile_key != registry.start_pages.profile_key(profile, create=False)
            or not profile_key
        ):
            raise ValueError("Review an existing normal profile")
        pages = registry.start_pages
        definition = self.definition(proposal)
        current = (
            registry.page_style.get(profile)
            if definition.page == "style"
            else pages.state(profile)
        )
        if not base or state_digest(current) != base:
            raise ValueError("The page changed; review it again before applying")
        candidate = definition.candidate(current)
        self.update(task_id, request_id, "applying")
        try:
            if definition.page == "style":
                registry.page_style.save(
                    profile, candidate, expected=current, all_profiles=all_profiles
                )
            else:
                pages._commit(profile, candidate)
        except Exception:
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
