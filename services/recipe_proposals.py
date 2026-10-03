"""Content-free task receipts for installing one immutable connector recipe."""

from dataclasses import dataclass
from datetime import datetime, timezone

from services.connector_recipes import MAX_RECIPE_BYTES, RecipeError, parse_recipe
from services.task_policy import TaskPermissionError

RECIPE_SCHEMA = """
CREATE TABLE IF NOT EXISTS recipe_proposals (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), request_id TEXT NOT NULL,
 file_id TEXT NOT NULL REFERENCES workspace_files(file_id), sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','installed','denied','cancelled','interrupted')),
 provider TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(task_id,request_id)
);
"""


@dataclass(frozen=True)
class RecipeProposal:
    task_id: str
    request_id: str
    file_id: str
    sha256: str
    state: str
    provider: str
    created_at: str

    @property
    def request_key(self):
        return "recipe:" + self.task_id + ":" + self.request_id


class RecipeProposals:
    def __init__(self, service, connections=None):
        self.service, self.connections = service, connections
        for row in service.store._db.execute(
            "SELECT * FROM recipe_proposals WHERE state='awaiting_approval'"
        ).fetchall():
            self.update(row["task_id"], row["request_id"], "interrupted")

    def get(self, task_id, request_id):
        self.service.store.get(task_id)
        row = self.service.store._db.execute(
            "SELECT * FROM recipe_proposals WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Recipe proposal does not belong to this task")
        return RecipeProposal(**dict(row))

    def list(self, task_id):
        self.service.store.get(task_id)
        return [
            RecipeProposal(**dict(r))
            for r in self.service.store._db.execute(
                "SELECT * FROM recipe_proposals WHERE task_id=? ORDER BY created_at,request_id",
                (task_id,),
            )
        ]

    def pending(self, task_id):
        return [r for r in self.list(task_id) if r.state == "awaiting_approval"]

    def recipe(self, proposal):
        item = self.service.workspace.get(proposal.task_id, proposal.file_id)
        if (
            item.status != "sealed"
            or item.expected_bytes > MAX_RECIPE_BYTES
            or item.sha256 != proposal.sha256
        ):
            raise ValueError("The proposed recipe source is unavailable or changed")
        recipe = parse_recipe(
            self.service.workspace.bytes(proposal.task_id, proposal.file_id)
        )
        if recipe.sha256 != proposal.sha256 or recipe.provider != proposal.provider:
            raise ValueError("The proposed recipe identity changed")
        return recipe

    def propose(self, task_id, file_id, request_id):
        import re

        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", request_id
        ):
            raise ValueError("Use a stable recipe request ID")
        self.service._authorize(task_id, "connector")
        self.service._authorize(task_id, "workspace")
        if (
            self.connections is None
            or self.connections.recipes is None
            or self.connections.failure
        ):
            raise ValueError("Connector recipe installation is unavailable")
        try:
            previous = self.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.file_id != file_id:
                raise ValueError("Recipe request ID is bound to another file")
            return previous
        rows = self.list(task_id)
        if len(rows) >= 20 or len(self.pending(task_id)) >= 3:
            raise TaskPermissionError("recipe_proposal_limit")
        item = self.service.workspace.get(task_id, file_id)
        if item.status != "sealed" or item.expected_bytes > MAX_RECIPE_BYTES:
            raise ValueError("Choose a sealed recipe JSON file of at most 16 KiB")
        recipe = parse_recipe(self.service.workspace.bytes(task_id, file_id))
        with self.service.store._db:
            self.service.store._db.execute(
                "INSERT INTO recipe_proposals VALUES (?,?,?,?,'awaiting_approval',?,?)",
                (
                    task_id,
                    request_id,
                    file_id,
                    recipe.sha256,
                    recipe.provider,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.service.store._append(
                task_id,
                "recipe.requested",
                {"request_id": request_id, "sha256": recipe.sha256},
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def validate(self, task_id, file_id):
        self.service._authorize(task_id, "connector")
        self.service._authorize(task_id, "workspace")
        item = self.service.workspace.get(task_id, file_id)
        if item.status != "sealed" or item.expected_bytes > MAX_RECIPE_BYTES:
            raise ValueError("Choose a sealed owned recipe JSON file of at most 16 KiB")
        source = self.service.workspace.bytes(task_id, file_id)
        try:
            recipe = parse_recipe(source)
        except RecipeError as error:
            return {
                "valid": False,
                "reason": str(error),
                "file_id": file_id,
                "sha256": item.sha256,
            }
        return {
            "valid": True,
            "file_id": file_id,
            "sha256": recipe.sha256,
            "recipe": recipe.metadata(),
            "installation_required": True,
            "provider_semantics_verified": False,
        }

    def update(self, task_id, request_id, state):
        previous = self.get(task_id, request_id)
        if previous.state != "awaiting_approval":
            return previous
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE recipe_proposals SET state=? WHERE task_id=? AND request_id=?",
                (state, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "recipe." + state,
                {"request_id": request_id, "sha256": previous.sha256},
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def resolve(self, task_id, request_id, *, approve=False):
        if type(approve) is not bool:
            raise ValueError("Use an explicit installation decision")
        proposal = self.get(task_id, request_id)
        if proposal.state != "awaiting_approval":
            raise ValueError("Choose a pending recipe installation")
        if not approve:
            return self.update(task_id, request_id, "denied")
        self.service._active(task_id)
        self.service._authorize(task_id, "connector")
        self.service._authorize(task_id, "workspace")
        recipe = self.recipe(proposal)
        self.connections.install_recipe(recipe)
        # Catalog publication is idempotent. If journaling fails, no account or
        # task permission has been granted and restart never replays this request.
        return self.update(task_id, request_id, "installed")

    def cancel_task(self, task_id, file_id=None):
        for proposal in self.pending(task_id):
            if file_id is None or proposal.file_id == file_id:
                self.update(task_id, proposal.request_id, "cancelled")
