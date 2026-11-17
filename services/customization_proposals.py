"""Agent-proposed user scripts and panel apps; applied only after a native review.

An agent submits one exact declaration. Steed validates it with the same rules
as steed:userscripts and steed:apps, stores it against the task, and shows the
exact code in a review popup. Only the user's Apply adds it, to a profile the
user picks, as a new entry: proposals never read, replace or remove existing
scripts or apps, and grant no browser data, files or network access.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from browser.data_panel_format import stable_id
from services.command_broker import require_owner_thread
from services.task_policy import TaskPermissionError

CUSTOMIZATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS customization_proposals (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), request_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('user_script','panel_app')),
 sha256 TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_approval','applying','applied','denied','cancelled','interrupted','failed')),
 created_at TEXT NOT NULL, PRIMARY KEY(task_id,request_id)
);
"""
MAX_SOURCE_BYTES = 320 * 1024
KINDS = ("user_script", "panel_app")
FIELDS = {
    "user_script": {"version", "kind", "name", "sites", "css", "js", "when"},
    "panel_app": {"version", "kind", "name", "html"},
}
EXAMPLES = {
    "user_script": {
        "version": 1,
        "kind": "user_script",
        "name": "Wider reading column",
        "sites": ["example.org"],
        "css": "main { max-width: 900px !important; }",
        "js": "",
        "when": "ready",
    },
    "panel_app": {
        "version": 1,
        "kind": "panel_app",
        "name": "Scratch notes",
        "html": "<textarea id=n style='width:100%;height:90vh'></textarea>"
        "<script>n.value=localStorage.n||'';n.oninput=()=>localStorage.n=n.value</script>",
    },
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate fields are not allowed")
        result[key] = value
    return result


@dataclass(frozen=True)
class Definition:
    kind: str
    name: str
    sha256: str
    document: dict
    source: str


def parse(source) -> Definition:
    """Validate one declaration exactly; ValueError explains what is wrong."""
    from browser.user_scripts import clean
    from services.start_pages import clean_html

    try:
        if not isinstance(source, str) or len(source.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("Use a declaration of at most 320 KB UTF-8")
        document = json.loads(
            source,
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Use a valid UTF-8 JSON declaration") from error
    if (
        not isinstance(document, dict)
        or document.get("kind") not in KINDS
        or type(document.get("version")) is not int
        or document["version"] != 1
        or set(document) - FIELDS[document["kind"]]
    ):
        raise ValueError(
            "Use version 1, a kind of user_script or panel_app, and only its fields"
        )
    kind = document["kind"]
    if kind == "user_script":
        sites = document.get("sites")
        if not isinstance(sites, list) or not all(isinstance(s, str) for s in sites):
            raise ValueError("List the sites as an array of names such as example.org")
        for field in ("name", "css", "js", "when"):
            if field in document and not isinstance(document[field], str):
                raise ValueError(f"{field} must be text")
        entry = clean({**document, "id": None, "enabled": True})
        cleaned = {k: entry[k] for k in ("name", "sites", "css", "js", "when")}
    else:
        name = document.get("name")
        name = " ".join(name.split()) if isinstance(name, str) else ""
        if not 1 <= len(name) <= 60 or not name.isprintable():
            raise ValueError("Give the app a name of 1 to 60 characters.")
        cleaned = {"name": name, "html": clean_html(document.get("html"))}
    digest = hashlib.sha256(source.encode()).hexdigest()
    return Definition(kind, cleaned["name"], digest, cleaned, source)


def capabilities():
    from browser.user_scripts import MAX_CSS, MAX_JS, WHEN

    return {
        "kinds": list(KINDS),
        "user_script": {
            "fields": sorted(FIELDS["user_script"]),
            "sites": "Array of site names such as example.org (subdomains included), "
            "*.example.org, or * for every site; at most 50.",
            "when": list(WHEN),
            "limits": {"css_bytes": MAX_CSS, "js_bytes": MAX_JS},
            "runs": "CSS from Steed's isolated world at document start; JavaScript "
            "in the page's own world, so it has that page's permissions only.",
            "example": EXAMPLES["user_script"],
        },
        "panel_app": {
            "fields": sorted(FIELDS["panel_app"]),
            "limits": {"html_bytes": 256 * 1024},
            "runs": "One HTML page in the App Panel at its own steed://app-<id>/ "
            "origin with its own storage; Steed data only through its API under "
            "the user's Local pages access settings.",
            "example": EXAMPLES["panel_app"],
        },
        "max_source_bytes": MAX_SOURCE_BYTES,
        "review": "The user sees the exact code, picks a profile and must press "
        "Apply. Proposals only add new entries; none are read, replaced or removed.",
    }


@dataclass(frozen=True)
class CustomizationProposal:
    task_id: str
    request_id: str
    kind: str
    sha256: str
    name: str
    state: str
    created_at: str

    @property
    def request_key(self):
        return "customization:" + self.task_id + ":" + self.request_id


class CustomizationProposals:
    def __init__(self, service):
        self.service = service
        for row in self.db.execute(
            "SELECT task_id,request_id FROM customization_proposals WHERE state IN ('awaiting_approval','applying')"
        ).fetchall():
            self.update(row[0], row[1], "interrupted")

    @property
    def db(self):
        require_owner_thread(self.service)
        return self.service.store._db

    @property
    def available(self):
        return hasattr(self.service.profiles, "user_scripts")

    def get(self, task_id, request_id):
        self.service.store.get(task_id)
        row = self.db.execute(
            "SELECT task_id,request_id,kind,sha256,name,state,created_at FROM customization_proposals WHERE task_id=? AND request_id=?",
            (task_id, request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Customization proposal does not belong to this task")
        return CustomizationProposal(**dict(row))

    def list(self, task_id):
        self.service.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.db.execute(
                "SELECT request_id FROM customization_proposals WHERE task_id=? ORDER BY created_at,request_id",
                (task_id,),
            ).fetchall()
        ]

    def pending(self, task_id):
        return [p for p in self.list(task_id) if p.state == "awaiting_approval"]

    def definition(self, proposal):
        row = self.db.execute(
            "SELECT source FROM customization_proposals WHERE task_id=? AND request_id=?",
            (proposal.task_id, proposal.request_id),
        ).fetchone()
        if row is None:
            raise KeyError("Customization proposal is unavailable")
        definition = parse(row[0])
        if definition.sha256 != proposal.sha256 or definition.kind != proposal.kind:
            raise ValueError("The source no longer matches the proposal")
        return definition

    def propose(self, task_id, source, request_id):
        self.service._usable()
        self.service._active(task_id)
        if not self.available or not stable_id(request_id):
            raise ValueError("Review is unavailable or the request ID is invalid")
        definition = parse(source)
        try:
            previous = self.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.sha256 != definition.sha256:
                raise ValueError("Request ID is already bound to a different proposal")
            return previous
        if (
            len(self.list(task_id)) >= 20
            or len(self.pending(task_id)) >= 3
            or self.db.execute(
                "SELECT COUNT(*) FROM customization_proposals WHERE state='awaiting_approval'"
            ).fetchone()[0]
            >= 32
        ):
            raise TaskPermissionError("customization_proposal_limit")
        with self.db:
            self.db.execute(
                "INSERT INTO customization_proposals VALUES (?,?,?,?,?,?,'awaiting_approval',?)",
                (
                    task_id,
                    request_id,
                    definition.kind,
                    definition.sha256,
                    definition.name,
                    source,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.service.store._append(
                task_id,
                "customization.requested",
                {
                    "request_id": request_id,
                    "kind": definition.kind,
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
            raise ValueError("The proposal is no longer awaiting this decision")
        with self.db:
            self.db.execute(
                "UPDATE customization_proposals SET state=? WHERE task_id=? AND request_id=?",
                (state, task_id, request_id),
            )
            self.service.store._append(
                task_id,
                "customization." + state,
                {"request_id": request_id, "sha256": previous.sha256},
            )
        self.service.changed.emit()
        return self.get(task_id, request_id)

    def resolve(
        self, task_id, request_id, *, approve=False, profile=None, profile_key=None
    ):
        """Trusted UI only: add the reviewed entry to an existing normal profile."""
        self.service._usable()
        self.service._active(task_id)
        if type(approve) is not bool:
            raise ValueError("Provide an explicit decision")
        proposal = self.get(task_id, request_id)
        if not approve:
            return self.update(task_id, request_id, "denied")
        registry = self.service.profiles
        if (
            not self.available
            or profile == "search"
            or not isinstance(profile, str)
            or not profile_key
            or profile_key != registry.start_pages.profile_key(profile, create=False)
        ):
            raise ValueError("Review an existing normal profile")
        definition = self.definition(proposal)
        self.update(task_id, request_id, "applying")
        try:
            document = definition.document
            if definition.kind == "user_script":
                registry.user_scripts(profile).save({**document, "enabled": True})
            else:
                registry.start_pages.save_app(
                    profile, document["name"], document["html"]
                )
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
