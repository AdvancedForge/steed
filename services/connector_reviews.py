"""Durable trusted-user review content, separate from agent receipt results."""

from dataclasses import dataclass

from services.connectors import ConnectorReview

REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_reviews (
    task_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    before_text TEXT NOT NULL,
    after_text TEXT NOT NULL,
    context TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','cancelled')),
    PRIMARY KEY(task_id,request_id),
    FOREIGN KEY(task_id,request_id) REFERENCES connector_calls(task_id,request_id)
);
"""


@dataclass(frozen=True)
class AppReview:
    task_id: str
    request_id: str
    before_text: str
    after_text: str
    context: str
    status: str

    @property
    def request_key(self):
        return f"review:{self.task_id}:{self.request_id}"

    def payload(self):
        return ConnectorReview(
            self.before_text, self.after_text, self.context
        ).validate()


class ConnectorReviews:
    def __init__(self, store):
        self.store = store
        with store._db:
            store._db.execute(
                "UPDATE connector_reviews SET status='cancelled', before_text='', after_text='', context='' WHERE status='pending'"
            )

    def get(self, task_id, request_id):
        self.store._check()
        row = self.store._db.execute(
            "SELECT * FROM connector_reviews WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("App review does not belong to this task")
        return AppReview(**dict(row))

    def pending(self, task_id):
        self.store._check()
        return [
            AppReview(**dict(row))
            for row in self.store._db.execute(
                "SELECT * FROM connector_reviews WHERE task_id=? AND status='pending' ORDER BY request_id",
                (task_id,),
            )
        ]

    def create(self, task_id, request_id, payload):
        payload.validate()
        self.store._check()
        with self.store._db:
            self.store._db.execute(
                "INSERT INTO connector_reviews VALUES (?,?,?,?,?,'pending')",
                (task_id, request_id, payload.before, payload.after, payload.context),
            )
            self.store._append(
                task_id, "app.review_requested", {"request_id": request_id}
            )

    def resolve(self, task_id, request_id, status):
        if status not in {"approved", "denied", "cancelled"}:
            raise ValueError("Invalid app review decision")
        if self.get(task_id, request_id).status != "pending":
            raise ValueError("App review has already been resolved")
        with self.store._db:
            self.store._db.execute(
                "UPDATE connector_reviews SET status=?, before_text='', after_text='', context='' WHERE task_id=? AND request_id=?",
                (status, task_id, request_id),
            )
            self.store._append(
                task_id, "app.review_" + status, {"request_id": request_id}
            )
