"""Reviewed profile-bound native panels; publication never creates data authority."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from data_panel_format import form_values, parse_panel, stable_id, widget_content
from services.command_broker import require_owner_thread
from services.task_artifacts import artifact_input
from services.task_messages import MESSAGE_BYTES
from services.task_policy import TaskPermissionError

PANEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_panels (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), request_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','applied','denied','cancelled','interrupted','removed')),
 created_at TEXT NOT NULL, profile_key TEXT NOT NULL DEFAULT '',
 profile_name TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
 bindings TEXT NOT NULL, PRIMARY KEY(task_id,request_id)
);
CREATE TABLE IF NOT EXISTS data_panel_updates (
 task_id TEXT NOT NULL, request_id TEXT NOT NULL, update_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(task_id,request_id,update_id),
 FOREIGN KEY(task_id,request_id) REFERENCES data_panels(task_id,request_id)
);
"""


@dataclass(frozen=True)
class PanelReceipt:
    task_id: str
    request_id: str
    sha256: str
    name: str
    state: str
    created_at: str
    profile_name: str
    revision: int

    @property
    def request_key(self):
        return "panel:" + self.task_id + ":" + self.request_id


@dataclass(frozen=True)
class PanelUpdate:
    task_id: str
    request_id: str
    update_id: str
    sha256: str
    revision: int


class DataPanels:
    def __init__(self, service):
        self.service = service
        for row in self.db.execute(
            "SELECT task_id,request_id FROM data_panels WHERE state='awaiting_approval'"
        ).fetchall():
            self._state(row[0], row[1], "interrupted")

    @property
    def db(self):
        require_owner_thread(self.service)
        return self.service.store._db

    @property
    def available(self):
        return callable(getattr(self.service.profiles, "approval_profile_key", None))

    def _row(self, task_id, request_id):
        self.service.store.get(task_id)
        row = self.db.execute(
            "SELECT * FROM data_panels WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Data panel does not belong to this task")
        return row

    def get(self, task_id, request_id):
        row = self._row(task_id, request_id)
        return PanelReceipt(
            **{key: row[key] for key in PanelReceipt.__dataclass_fields__}
        )

    def list(self, task_id):
        self.service.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.db.execute(
                "SELECT request_id FROM data_panels WHERE task_id=? ORDER BY created_at,request_id",
                (task_id,),
            ).fetchall()
        ]

    def pending(self, task_id):
        return [p for p in self.list(task_id) if p.state == "awaiting_approval"]

    def definition(self, task_id, request_id):
        row = self._row(task_id, request_id)
        definition = parse_panel(row["source"])
        if definition.sha256 != row["sha256"]:
            raise ValueError("Panel source no longer matches its receipt")
        return definition

    def _authority(self, definition, task_id, *, consume=False):
        self.service._usable()
        self.service._active(task_id)
        if definition.artifact_widgets:
            if consume:
                self.service._authorize(task_id, "artifact")
            else:
                live = self.service._grants.get(task_id)
                if live is None:
                    raise TaskPermissionError("approval_required")
                live[1].check("artifact")

    def _artifact(self, task_id, artifact_id, kind, digest=None):
        artifact = self.service.store.artifact(task_id, artifact_id)
        _, size, actual, _ = artifact_input(
            artifact.title, artifact.content, artifact.format, artifact.source_events
        )
        if (
            size != artifact.byte_count
            or actual != artifact.sha256
            or (digest is not None and actual != digest)
        ):
            raise ValueError("Panel artifact no longer matches its pinned digest")
        widget_content(kind, artifact.content)
        return artifact

    def _bindings(self, definition, task_id, ids):
        result = {}
        for key, artifact_id in ids.items():
            artifact = self._artifact(
                task_id, artifact_id, definition.artifact_widgets[key]["kind"]
            )
            result[key] = {
                "artifact_id": artifact.artifact_id,
                "sha256": artifact.sha256,
            }
        return result

    def propose(self, task_id, source, request_id):
        definition = parse_panel(source)
        self._authority(definition, task_id)
        if not self.available or not stable_id(request_id):
            raise ValueError(
                "Data-panel review needs a browser and a stable request ID"
            )
        try:
            previous = self.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.sha256 != definition.sha256:
                raise ValueError("Panel request ID is already bound to another source")
            return previous
        count = self.db.execute(
            "SELECT COUNT(*) FROM data_panels WHERE state='awaiting_approval'"
        ).fetchone()[0]
        if (
            len(self.list(task_id)) >= 20
            or len(self.pending(task_id)) >= 3
            or count >= 32
        ):
            raise TaskPermissionError("panel_proposal_limit")
        bindings = self._bindings(
            definition,
            task_id,
            {key: w["artifact_id"] for key, w in definition.artifact_widgets.items()},
        )
        self._authority(definition, task_id, consume=True)
        with self.db:
            self.db.execute(
                "INSERT INTO data_panels (task_id,request_id,sha256,name,source,state,created_at,bindings) VALUES (?,?,?,?,?,'awaiting_approval',?,?)",
                (
                    task_id,
                    request_id,
                    definition.sha256,
                    definition.name,
                    source,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(bindings, sort_keys=True),
                ),
            )
            self.service.store._append(
                task_id,
                "panel.requested",
                {"request_id": request_id, "sha256": definition.sha256},
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def _state(self, task_id, request_id, state):
        receipt = self.get(task_id, request_id)
        allowed = {
            "awaiting_approval": {"applied", "denied", "cancelled", "interrupted"},
            "applied": {"removed"},
        }
        if state not in allowed.get(receipt.state, set()):
            raise ValueError("This panel no longer awaits that decision")
        with self.db:
            self.db.execute(
                "UPDATE data_panels SET state=? WHERE task_id=? AND request_id=?",
                (state, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "panel." + state,
                {"request_id": request_id, "sha256": receipt.sha256},
            )
        return self.get(task_id, request_id)

    def resolve(
        self, task_id, request_id, *, approve=False, profile=None, profile_key=None
    ):
        """Trusted user review only; publishing is a separate bounded agent command."""
        try:
            return self._resolve(
                task_id,
                request_id,
                approve=approve,
                profile=profile,
                profile_key=profile_key,
            )
        except sqlite3.Error:
            self.service._fatal()
            raise

    def _resolve(self, task_id, request_id, *, approve, profile, profile_key):
        self.service._usable()
        if type(approve) is not bool:
            raise ValueError("Provide an explicit panel decision")
        receipt = self.get(task_id, request_id)
        if receipt.state != "awaiting_approval":
            raise ValueError("This panel is no longer awaiting approval")
        if not approve:
            result = self._state(task_id, request_id, "denied")
        else:
            definition = self.definition(task_id, request_id)
            self._authority(definition, task_id)
            if not self.available or not isinstance(profile, str):
                raise ValueError("Choose a normal browser profile for this panel")
            key = self.profile_key(profile, create=True)
            if profile_key is not None and profile_key != key:
                raise ValueError("Display profile changed; review it again")
            # Revalidate the exact initial artifacts before sharing them with this profile.
            self.view(task_id, request_id)
            count = self.db.execute(
                "SELECT COUNT(*) FROM data_panels WHERE profile_key=? AND state='applied'",
                (key,),
            ).fetchone()[0]
            if count >= 8:
                raise TaskPermissionError("profile_panel_limit")
            self._authority(definition, task_id)
            with self.db:
                self.db.execute(
                    "UPDATE data_panels SET profile_key=?,profile_name=? WHERE task_id=? AND request_id=?",
                    (key, profile, task_id, request_id),
                )
                result = self._state(task_id, request_id, "applied")
        self.service.changed.emit()
        return result

    def publish(self, task_id, request_id, update_id, expected_revision, bindings):
        definition = self.definition(task_id, request_id)
        self._authority(definition, task_id)
        receipt = self.get(task_id, request_id)
        if receipt.state != "applied":
            raise ValueError("Review and enable the panel before publishing updates")
        if (
            not stable_id(update_id)
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            raise ValueError("Use a stable update ID and exact nonnegative revision")
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= 8:
            raise ValueError("Publish between one and eight owned artifact bindings")
        ids = {}
        for binding in bindings:
            if (
                not isinstance(binding, dict)
                or set(binding) != {"id", "artifact_id"}
                or not stable_id(binding["id"], 32)
                or not stable_id(binding["artifact_id"])
                or binding["id"] in ids
                or binding["id"] not in definition.artifact_widgets
            ):
                raise ValueError("Update distinct approved artifact widgets only")
            ids[binding["id"]] = binding["artifact_id"]
        digest = hashlib.sha256(
            json.dumps(
                {"revision": expected_revision, "bindings": ids}, sort_keys=True
            ).encode()
        ).hexdigest()
        row = self.db.execute(
            "SELECT sha256,revision FROM data_panel_updates WHERE task_id=? AND request_id=? AND update_id=?",
            (task_id, request_id, update_id),
        ).fetchone()
        if row is not None:
            if row[0] != digest:
                raise ValueError("Update ID is already bound to another publication")
            return PanelUpdate(task_id, request_id, update_id, digest, row[1])
        if expected_revision != receipt.revision:
            raise ValueError(
                "Panel revision changed; read panel.status before publishing"
            )
        if receipt.revision >= 256:
            raise TaskPermissionError("panel_update_limit")
        # The display profile identity must still exist; a recreated profile is different.
        if not self.for_profile(receipt.profile_name, match=(task_id, request_id)):
            # Renames retain identity, so search all current profiles before refusing.
            if not any(
                self.for_profile(name, match=(task_id, request_id))
                for name in self.profile_names()
            ):
                raise ValueError("The approved display profile is unavailable")
        updated = json.loads(self._row(task_id, request_id)["bindings"])
        updated.update(self._bindings(definition, task_id, ids))
        self._authority(definition, task_id, consume=True)
        revision = receipt.revision + 1
        with self.db:
            self.db.execute(
                "INSERT INTO data_panel_updates VALUES (?,?,?,?,?)",
                (task_id, request_id, update_id, digest, revision),
            )
            self.db.execute(
                "UPDATE data_panels SET bindings=?,revision=? WHERE task_id=? AND request_id=?",
                (json.dumps(updated, sort_keys=True), revision, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "panel.published",
                {
                    "request_id": request_id,
                    "update_id": update_id,
                    "sha256": digest,
                    "revision": revision,
                },
            )
        self.service.changed.emit()
        return PanelUpdate(task_id, request_id, update_id, digest, revision)

    def profile_key(self, name, *, create=False):
        if not isinstance(name, str) or name == "search":
            raise ValueError("Choose a normal display profile")
        if name == "global":
            directory = self.service.profiles.config.config_dir / "global_profile"
            if not directory.is_dir():
                raise ValueError("Display profile is unavailable")
            return self.service.profiles._permission_identity(directory, create=create)
        return self.service.profiles.approval_profile_key(
            "existing:" + name, create=create
        )

    def profile_names(self):
        if not self.available:
            return []
        names = {
            p.name
            for p in self.service.profiles.config.profiles_dir.iterdir()
            if p.is_dir()
            and not p.is_symlink()
            and p.name not in ("search", "global_profile")
        }
        if (self.service.profiles.config.config_dir / "global_profile").is_dir():
            names.add("global")
        return sorted(names)

    def for_profile(self, name, *, match=None):
        if not self.available or name == "search":
            return []
        try:
            key = self.profile_key(name)
        except ValueError:
            return []
        if not key:
            return []
        rows = self.db.execute(
            "SELECT task_id,request_id FROM data_panels WHERE state='applied' AND profile_key=? ORDER BY created_at,request_id",
            (key,),
        ).fetchall()
        return [
            self.get(*tuple(row))
            for row in rows
            if match is None or tuple(row) == match
        ]

    def view(self, task_id, request_id):
        """Trusted local UI reads saved drafts; no agent body-reading endpoint is added."""
        definition = self.definition(task_id, request_id)
        bindings = json.loads(self._row(task_id, request_id)["bindings"])
        result = []
        for widget in definition.widgets:
            value = dict(widget)
            if widget["id"] in definition.artifact_widgets:
                binding = bindings[widget["id"]]
                artifact = self._artifact(
                    task_id, binding["artifact_id"], widget["kind"], binding["sha256"]
                )
                value.update(
                    artifact_id=artifact.artifact_id,
                    artifact_title=artifact.title,
                    content=widget_content(widget["kind"], artifact.content),
                )
            result.append(value)
        return tuple(result)

    def form_payload(self, task_id, request_id, widget_id, values):
        """Build the exact user-reviewed message; no data authority is added."""
        definition = self.definition(task_id, request_id)
        widget = next(
            (
                w
                for w in definition.widgets
                if w["id"] == widget_id and w["kind"] == "form"
            ),
            None,
        )
        if widget is None:
            raise ValueError("Choose a reviewed form from this panel")
        values = form_values(widget, values)
        payload = json.dumps(
            {
                "type": "steed_panel_form",
                "panel": definition.name,
                "request_id": request_id,
                "widget_id": widget_id,
                "fields": [
                    {"id": f["id"], "label": f["title"], "value": values[f["id"]]}
                    for f in widget["fields"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        if len(payload.encode("utf-8")) > MESSAGE_BYTES:
            raise ValueError("The complete form message must fit within 8 KiB")
        return payload

    def form_available(self, task_id, request_id, profile_key):
        self.service._usable()
        self.service._active(task_id)
        row = self._row(task_id, request_id)
        if (
            row["state"] != "applied"
            or not profile_key
            or row["profile_key"] != profile_key
        ):
            raise ValueError("Form is not enabled in this profile")
        if task_id not in self.service.external_tasks:
            raise ValueError("The owning agent is disconnected")
        if not any(
            self.for_profile(p, match=(task_id, request_id))
            for p in self.profile_names()
        ):
            raise ValueError("The approved profile no longer exists")

    def submit_form(
        self, task_id, request_id, widget_id, submission_id, values, *, profile_key
    ):
        """Trusted user-only submission with an atomic, value-free retry receipt."""
        self.service._usable()
        if not stable_id(submission_id):
            raise ValueError("Use a stable submission ID")
        if (
            not profile_key
            or self._row(task_id, request_id)["profile_key"] != profile_key
        ):
            raise ValueError("Form receipt belongs to another display profile")
        payload = self.form_payload(task_id, request_id, widget_id, values)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        message_id = (
            "panel_"
            + hashlib.sha256(
                json.dumps([task_id, request_id, widget_id, submission_id]).encode()
            ).hexdigest()[:58]
        )
        receipt = {"message_id": message_id, "sha256": digest, "state": "queued"}
        # Retain the digest in the journal when the user clears message bodies.
        # A retry reports the original queue result and never re-notifies an agent.
        for row in self.db.execute(
            "SELECT payload FROM events WHERE task_id=? AND kind='panel.form_queued'",
            (task_id,),
        ):
            previous = json.loads(row[0])
            if previous["message_id"] == message_id:
                if previous["sha256"] != digest:
                    raise ValueError(
                        "Submission ID is already bound to different values"
                    )
                return receipt
        self.form_available(task_id, request_id, profile_key)

        def audit():
            self.service.store._append(task_id, "panel.form_queued", receipt)

        try:
            result = self.service.messages._post(
                task_id, "user", message_id, payload, 0, "complete", audit=audit
            )
        except sqlite3.Error:
            self.service._fatal()
            raise
        self.service.changed.emit()
        self.service.user_message_sent.emit(task_id, result)
        return receipt

    def remove(self, task_id, request_id):
        self.service._usable()
        try:
            result = self._state(task_id, request_id, "removed")
        except sqlite3.Error:
            self.service._fatal()
            raise
        self.service.changed.emit()
        return result

    def cancel_task(self, task_id, *, interrupted=False):
        for receipt in self.pending(task_id):
            self._state(
                task_id,
                receipt.request_id,
                "interrupted" if interrupted else "cancelled",
            )
