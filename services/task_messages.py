"""Bounded task conversations; message bodies stay out of the activity journal."""

import re
from uuid import uuid4

MESSAGE_BYTES = 8192
TASK_BYTES = 1024 * 1024
TASK_MESSAGES = 256
TASK_UPDATES = 2048
MESSAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_messages (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    message_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'agent')),
    text TEXT NOT NULL,
    revision INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('streaming', 'complete', 'interrupted', 'cleared')),
    created_sequence INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    PRIMARY KEY(task_id, message_id)
);
CREATE INDEX IF NOT EXISTS task_messages_cursor ON task_messages(task_id, sequence);
CREATE TABLE IF NOT EXISTS task_conversation_reads (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    sequence INTEGER NOT NULL
);
"""


class TaskMessages:
    def __init__(self, store):
        self.store = store

    def read(self, task_id, after=0, limit=16):
        self.store.get(task_id)
        if (
            type(after) is not int
            or after < 0
            or type(limit) is not int
            or not 1 <= limit <= 16
        ):
            raise ValueError("Invalid conversation cursor or limit")
        rows = self.store._db.execute(
            "SELECT * FROM task_messages WHERE task_id=? AND sequence>? "
            "ORDER BY sequence LIMIT ?",
            (task_id, after, limit + 1),
        ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {
            "messages": items,
            "cursor": items[-1]["sequence"] if items else after,
            "has_more": len(rows) > limit,
        }

    def post_user(self, task_id, text):
        return self._post(task_id, "user", "user_" + uuid4().hex, text, 0, "complete")

    def acknowledge(self, task_id, page):
        """Record only user messages actually returned to this task's client."""
        self.store.get(task_id)
        sequence = max(
            (
                m["sequence"]
                for m in page["messages"]
                if m["role"] == "user" and m["state"] != "cleared"
            ),
            default=0,
        )
        if sequence <= self.read_sequence(task_id):
            return False
        with self.store._db:
            self.store._db.execute(
                "INSERT INTO task_conversation_reads VALUES (?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                (task_id, sequence),
            )
        return True

    def read_sequence(self, task_id):
        self.store.get(task_id)
        row = self.store._db.execute(
            "SELECT sequence FROM task_conversation_reads WHERE task_id=?", (task_id,)
        ).fetchone()
        return row[0] if row else 0

    def summary(self, task_id):
        """Counts for the trusted UI; never expose file bodies or app results."""
        self.store.get(task_id)
        db = self.store._db
        pending = db.execute(
            "SELECT (SELECT COUNT(*) FROM grants WHERE task_id=? AND status='requested') + "
            "(SELECT COUNT(*) FROM connector_calls WHERE task_id=? AND state='awaiting_review') + "
            "(SELECT COUNT(*) FROM worker_runs WHERE task_id=? AND state='awaiting_approval') + "
            "(SELECT COUNT(*) FROM recipe_proposals WHERE task_id=? AND state='awaiting_approval') + "
            "(SELECT COUNT(*) FROM layout_proposals WHERE task_id=? AND state='awaiting_approval') + "
            "(SELECT COUNT(*) FROM data_panels WHERE task_id=? AND state='awaiting_approval') + "
            "(SELECT COUNT(*) FROM start_page_proposals WHERE task_id=? AND state='awaiting_approval')",
            (task_id,) * 7,
        ).fetchone()[0]
        files, artifacts, receipts = db.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM workspace_files WHERE task_id=?), "
            "(SELECT COUNT(*) FROM artifacts WHERE task_id=?), "
            "(SELECT COUNT(*) FROM connector_calls WHERE task_id=?)",
            (task_id,) * 3,
        ).fetchone()
        return {
            "pending": pending,
            "files": files,
            "artifacts": artifacts,
            "receipts": receipts,
        }

    def post_agent(self, task_id, message_id, text, revision=0, state="complete"):
        if not isinstance(message_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,64}", message_id
        ):
            raise ValueError("Provide a stable message ID")
        return self._post(task_id, "agent", message_id, text, revision, state)

    def _post(self, task_id, role, message_id, text, revision, state, *, audit=None):
        task = self.store.get(task_id)
        if task.status not in ("created", "running"):
            raise ValueError("Task is no longer active")
        if (
            not isinstance(text, str)
            or not text.strip()
            or any(ord(c) < 32 and c not in "\n\r\t" for c in text)
            or len(text.encode("utf-8")) > MESSAGE_BYTES
        ):
            raise ValueError("Messages need nonempty text of at most 8 KiB UTF-8")
        if (
            type(revision) is not int
            or not 0 <= revision <= TASK_UPDATES
            or state not in ("streaming", "complete")
        ):
            raise ValueError("Invalid message revision or state")
        db = self.store._db
        row = db.execute(
            "SELECT * FROM task_messages WHERE task_id=? AND message_id=?",
            (task_id, message_id),
        ).fetchone()
        if row:
            if row["role"] != role:
                raise ValueError("Message belongs to another sender")
            if (row["revision"], row["text"], row["state"]) == (revision, text, state):
                return dict(row)
            if (
                row["state"] != "streaming"
                or revision != row["revision"] + 1
                or not text.startswith(row["text"])
            ):
                raise ValueError("Replies must append at the next revision")
        elif revision != 0:
            raise ValueError("New messages start at revision zero")
        count, total = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(length(CAST(text AS BLOB))),0) "
            "FROM task_messages WHERE task_id=?",
            (task_id,),
        ).fetchone()
        updates = db.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='conversation.updated'",
            (task_id,),
        ).fetchone()[0]
        old_bytes = len(row["text"].encode("utf-8")) if row else 0
        if (
            not row
            and count >= TASK_MESSAGES
            or updates >= TASK_UPDATES
            or total - old_bytes + len(text.encode("utf-8")) > TASK_BYTES
        ):
            raise ValueError("Task conversation limit reached")
        with db:
            self.store._append(
                task_id,
                "conversation.updated",
                {
                    "message_id": message_id,
                    "role": role,
                    "revision": revision,
                    "state": state,
                },
            )
            sequence = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO task_messages VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(task_id,message_id) DO UPDATE SET text=excluded.text, "
                "revision=excluded.revision, state=excluded.state, sequence=excluded.sequence",
                (
                    task_id,
                    message_id,
                    role,
                    text,
                    revision,
                    state,
                    row["created_sequence"] if row else sequence,
                    sequence,
                ),
            )
            if audit is not None:
                # Trusted UI receipts commit with the message, never before delivery.
                audit()
        return dict(
            db.execute(
                "SELECT * FROM task_messages WHERE task_id=? AND message_id=?",
                (task_id, message_id),
            ).fetchone()
        )

    def end(self, task_id):
        """Keep unfinished reply text but mark it interrupted on task shutdown."""
        self.store.get(task_id)
        db = self.store._db
        for row in db.execute(
            "SELECT message_id FROM task_messages WHERE task_id=? AND state='streaming'",
            (task_id,),
        ).fetchall():
            self.store._append(
                task_id, "conversation.interrupted", {"message_id": row[0]}
            )
            sequence = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "UPDATE task_messages SET state='interrupted', sequence=? WHERE task_id=? AND message_id=?",
                (sequence, task_id, row[0]),
            )

    def clear(self, task_id):
        """Retain tombstones so cleared message IDs cannot be replayed."""
        self.store.get(task_id)
        db = self.store._db
        with db:
            for row in db.execute(
                "SELECT message_id FROM task_messages WHERE task_id=? AND state!='cleared'",
                (task_id,),
            ).fetchall():
                self.store._append(
                    task_id, "conversation.cleared", {"message_id": row[0]}
                )
                sequence = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                db.execute(
                    "UPDATE task_messages SET text='', state='cleared', sequence=? WHERE task_id=? AND message_id=?",
                    (sequence, task_id, row[0]),
                )
