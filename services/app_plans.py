"""Qt-owned plan sequencing; each mutation uses an ordinary durable app receipt."""

import hashlib
import json
from uuid import uuid4

from services.connector_store import request_fingerprint
from services.connectors import ConnectorRequest
from services.task_policy import TaskPermissionError
from services.task_workspace import unique_object

PLAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_plans (
    task_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    step_count INTEGER NOT NULL,
    active INTEGER NOT NULL,
    PRIMARY KEY(task_id, request_id),
    FOREIGN KEY(task_id, request_id) REFERENCES connector_calls(task_id, request_id)
);
CREATE TABLE IF NOT EXISTS connector_plan_steps (
    task_id TEXT NOT NULL,
    parent_request_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    child_request_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(task_id, parent_request_id, step_index),
    UNIQUE(task_id, child_request_id),
    FOREIGN KEY(task_id, parent_request_id) REFERENCES connector_plans(task_id, request_id),
    FOREIGN KEY(task_id, child_request_id) REFERENCES connector_calls(task_id, request_id)
);
"""


class AppPlans:
    def __init__(self, service):
        self.service, self.store = service, service.store
        rows = self.db.execute(
            "SELECT * FROM connector_plans WHERE active=1"
        ).fetchall()
        for row in rows:
            self.halt(row["task_id"], row["request_id"], "plan_restart_interrupted")

    @property
    def db(self):
        self.store._check()
        return self.store._db

    def parent(self, task_id, request_id):
        self.service.connector_store.get(task_id, request_id)
        return self.db.execute(
            "SELECT * FROM connector_plans WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()

    def link(self, task_id, request_id):
        return self.db.execute(
            "SELECT * FROM connector_plan_steps WHERE task_id=? AND child_request_id=?",
            (task_id, request_id),
        ).fetchone()

    def children(self, task_id, request_id):
        return self.db.execute(
            "SELECT * FROM connector_plan_steps WHERE task_id=? AND parent_request_id=? ORDER BY step_index",
            (task_id, request_id),
        ).fetchall()

    def reserved(self, task_id):
        """Keep future child slots available while other requests await review."""
        return self.db.execute(
            "SELECT COALESCE(SUM(p.step_count - (SELECT COUNT(*) FROM connector_plan_steps s WHERE s.task_id=p.task_id AND s.parent_request_id=p.request_id)),0) FROM connector_plans p WHERE p.task_id=? AND p.active=1",
            (task_id,),
        ).fetchone()[0]

    def summary(self, task_id, request_id):
        return json.dumps(
            {
                "steps": [
                    {
                        "name": row["step_name"],
                        "request_id": child.request_id,
                        "operation_id": child.operation_id,
                        "state": child.state,
                        "payload_sha256": row["payload_sha256"],
                    }
                    for row in self.children(task_id, request_id)
                    for child in (
                        self.service.connector_store.get(
                            task_id, row["child_request_id"]
                        ),
                    )
                ]
            },
            ensure_ascii=True,
        )

    def values(self, task_id, parent_id, index):
        result = {}
        rows = self.children(task_id, parent_id)
        for row in rows[:index]:
            child = self.service.connector_store.get(task_id, row["child_request_id"])
            if child.state != "succeeded" or child.result_cleared_at:
                raise ValueError("An earlier plan result is unavailable")
            data = json.loads(child.text, object_pairs_hook=unique_object)
            if not isinstance(data, dict):
                raise ValueError("Plan outputs must be selected JSON fields")
            result.update(
                (f"step.{row['step_name']}.{key}", value) for key, value in data.items()
            )
        return result

    def definition(self, task_id, request_id, base):
        link = self.link(task_id, request_id)
        if link is None:
            return base
        if base.plan is None:
            raise ValueError("The configured plan is unavailable")
        return base.plan.materialize(
            link["step_index"],
            link["parent_request_id"],
            self.values(task_id, link["parent_request_id"], link["step_index"]),
        )

    def check_review(self, task_id, request):
        link = self.link(task_id, request.request_id)
        if link is None:
            return
        parent = self.parent(task_id, link["parent_request_id"])
        receipt = self.service.connector_store.get(task_id, request.request_id)
        original = self.service.connector_store.get(task_id, link["parent_request_id"])
        if (
            not parent["active"]
            or request.review is None
            or hashlib.sha256(request.review.before.encode("utf-8")).hexdigest()
            != link["payload_sha256"]
            or receipt.operation_id != request.operation_id
            or receipt.fingerprint != original.fingerprint
        ):
            raise ValueError("This plan step no longer matches its durable review")

    def start(self, task_id, receipt, definition):
        with self.db:
            self.db.execute(
                "INSERT INTO connector_plans VALUES (?,?,?,1)",
                (task_id, receipt.request_id, len(definition.plan.steps)),
            )
        self.advance(task_id, receipt.request_id)

    def advance(self, task_id, request_id):
        row = self.parent(task_id, request_id)
        if not row or not row["active"]:
            return
        parent = self.service.connector_store.get(task_id, request_id)
        children = self.children(task_id, request_id)
        if (
            children
            and self.service.connector_store.get(
                task_id, children[-1]["child_request_id"]
            ).state
            != "succeeded"
        ):
            return
        index = len(children)
        if index == row["step_count"]:
            self.halt(task_id, request_id, "plan_completed")
            return
        try:
            base = self.service.connectors.get(parent.connector, parent.tool)
            if not base.plan or len(base.plan.steps) != row["step_count"]:
                raise ValueError("Plan source changed")
            self.service._app_capacity(task_id)
            artifact = self.store.artifact(task_id, parent.artifact_id)
            if (
                request_fingerprint(
                    parent.connector,
                    parent.tool,
                    parent.effect,
                    parent.destination,
                    artifact,
                )
                != parent.fingerprint
            ):
                raise ValueError("Plan draft changed")
            grant = self.service._app_grant(task_id, parent, consume=True)
            grant.check("artifact")
            definition = base.plan.materialize(
                index, request_id, self.values(task_id, request_id, index)
            )
        except (KeyError, ValueError, TaskPermissionError):
            self.halt(task_id, request_id, "plan_inputs_or_authority_changed")
            return
        # Identity/source/live authority are checked again by the actual adapter.
        # Journal and link the child before any worker can enter HTTP.
        child = ConnectorRequest(
            "plan_" + uuid4().hex,
            parent.connector,
            parent.tool,
            parent.effect,
            parent.destination,
            artifact,
            uuid4().hex,
        )
        try:
            self.service.connector_store.create(task_id, child, parent.fingerprint)
        except ValueError:
            self.halt(task_id, request_id, "plan_receipt_limit")
            return
        with self.db:
            self.db.execute(
                "INSERT INTO connector_plan_steps(task_id,parent_request_id,step_index,step_name,child_request_id) VALUES (?,?,?,?,?)",
                (
                    task_id,
                    request_id,
                    index,
                    base.plan.steps[index].name,
                    child.request_id,
                ),
            )
        self.service.connector_store.update(
            task_id,
            request_id,
            "requested",
            reference=child.request_id,
            text=self.summary(task_id, request_id),
            reason="awaiting_step_review",
        )
        self.service._queue_app_job(task_id, definition, child, grant, preparing=True)

    def changed(self, task_id, request_id, *, advance=False):
        link = self.link(task_id, request_id)
        if link is None:
            return
        parent_id = link["parent_request_id"]
        parent = self.parent(task_id, parent_id)
        if not parent["active"]:
            return
        child = self.service.connector_store.get(task_id, request_id)
        if not advance or child.state not in {"requested", "dispatched", "succeeded"}:
            self.halt(task_id, parent_id, "plan_step_stopped")
            return
        if child.state == "succeeded":
            self.advance(task_id, parent_id)
        else:
            if child.state == "requested" and child.reason == "awaiting_review":
                review = self.service.app_reviews.get(task_id, request_id)
                digest = hashlib.sha256(review.before_text.encode("utf-8")).hexdigest()
                with self.db:
                    self.db.execute(
                        "UPDATE connector_plan_steps SET payload_sha256=? WHERE task_id=? AND child_request_id=?",
                        (digest, task_id, request_id),
                    )
            self.service.connector_store.update(
                task_id,
                parent_id,
                child.state,
                reference=request_id,
                text=self.summary(task_id, parent_id),
                reason="awaiting_step_review"
                if child.state == "requested"
                else "plan_step_dispatched",
            )

    def halt(self, task_id, request_id, reason):
        parent = self.parent(task_id, request_id)
        states = [
            self.service.connector_store.get(task_id, row["child_request_id"]).state
            for row in self.children(task_id, request_id)
        ]
        state = (
            "uncertain"
            if any(s in {"uncertain", "dispatched", "reconciling"} for s in states)
            else "succeeded"
            if len(states) == parent["step_count"]
            and all(s == "succeeded" for s in states)
            else "failed"
            if "succeeded" in states
            else "not_applied"
            if states and states[-1] == "not_applied"
            else "cancelled"
        )
        with self.db:
            self.db.execute(
                "UPDATE connector_plans SET active=0 WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            )
        self.service.connector_store.update(
            task_id,
            request_id,
            state,
            text=self.summary(task_id, request_id),
            reason=reason,
        )

    def cancel(self, task_id, request_id):
        parent = self.parent(task_id, request_id)
        if not parent or not parent["active"]:
            return
        with self.db:
            self.db.execute(
                "UPDATE connector_plans SET active=0 WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            )
        for row in self.children(task_id, request_id):
            child = self.service.connector_store.get(task_id, row["child_request_id"])
            if child.state in {"requested", "dispatched", "reconciling"}:
                self.service._cancel_app_job(task_id, child.request_id)
        self.halt(task_id, request_id, "plan_cancelled")
