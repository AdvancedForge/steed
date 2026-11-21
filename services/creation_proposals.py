"""Pages and automations an agent proposes, added only after review.

In a chat, the person says "make me a dashboard of my price watches" or
"every Friday, check these three sites and tell me what changed". The agent
proposes a page of the person's own (steed:<name>, HTML and JavaScript) or an
automation (a trigger and steps). Steed checks it as it checks anything the
person makes, and shows the HTML or every step in a review popup; only the
person's Add saves it. A proposal reads nothing from the browser and grants
no access. Proposals are kept in memory.
"""

import time
from dataclasses import dataclass, field

from browser.data_panel_format import stable_id
from services.task_policy import TaskPermissionError

KINDS = ("page", "automation")


@dataclass(frozen=True)
class CreationProposal:
    task_id: str
    request_id: str
    kind: str
    document: dict = field(default_factory=dict)
    state: str = "awaiting_approval"

    @property
    def request_key(self):
        return "creation:" + self.task_id + ":" + self.request_id


def capabilities():
    from services.automations import STEP_WORDS, TRIGGERS

    return {
        "page": {
            "what": "A page of the person's own, opened as steed:<name>: one HTML "
            "body with inline <style> and <script> (no external scripts). Its "
            "scripts can fetch('/api/...') for: fetch?url=, tabs, history, "
            "bookmarks, watches, routines, skills, automations, run-skill?id=, "
            "run-routine?id=, run-automation?id=, open?url=, notify?title=&text= "
            "(JSON; each allowed by the person). Use var(--bg), var(--surface), "
            "var(--ink), var(--muted), var(--accent), var(--border) for colours.",
            "fields": {
                "name": "lowercase-letters-digits-dashes",
                "title": "text",
                "html": "the page body",
            },
        },
        "automation": {
            "what": "A trigger and steps run in order; each step can use {last}, "
            "what the step before produced.",
            "triggers": {
                "manual": {},
                "startup": {},
                "schedule": {
                    "at": "HH:MM",
                    "days": "[0-6], 0 is Monday",
                    "every": "hours instead of at/days",
                },
                "watch": {"watch": "a watch id"},
                "download": {"match": "optional text in the file name"},
                "visit": {"site": "example.com (and its subdomains)"},
                "shortcut": {"keys": "Ctrl+Alt+K (needs Ctrl, Alt or Meta)"},
                "page": {},
            },
            "steps": {name: words for name, words in STEP_WORDS.items()},
            "example": {
                "name": "Friday check",
                "trigger": {"kind": "schedule", "at": "17:00", "days": [4]},
                "steps": [
                    {
                        "do": "gather",
                        "value": ["https://a.example/", "https://b.example/"],
                    },
                    {"do": "changed"},
                    {"do": "ask", "value": "In two lines, what changed? {last}"},
                    {"do": "notify", "value": "{last}"},
                ],
            },
            "valid_triggers": list(TRIGGERS),
        },
        "notes": "The person reviews and adds it; nothing is saved before that "
        "and no access is granted. Use a stable request_id and poll the status.",
    }


class CreationProposals:
    def __init__(self, service):
        self.service = service
        self.requests = {}  # (task, request id) -> dict

    @staticmethod
    def capabilities():
        return capabilities()

    def _check(self, kind, document):
        if kind == "page":
            from services.start_pages import clean_your_page

            return clean_your_page(document)
        from services.automations import check

        return check(document)

    def propose(self, task_id, request_id, kind, document):
        self.service._usable()
        self.service._active(task_id)
        if not stable_id(request_id) or kind not in KINDS:
            raise ValueError("Invalid request ID or kind")
        cleaned = self._check(kind, document)
        key = (task_id, request_id)
        if key in self.requests:
            entry = self.requests[key]
            if entry["document"] != cleaned or entry["kind"] != kind:
                raise ValueError("Request ID is already bound to a different proposal")
            return self._receipt(entry)
        pending = [
            e
            for (t, _), e in self.requests.items()
            if t == task_id and e["state"] == "awaiting_approval"
        ]
        if len(pending) >= 3:
            raise TaskPermissionError("creation_proposal_limit")
        entry = {
            "task_id": task_id,
            "request_id": request_id,
            "kind": kind,
            "document": cleaned,
            "state": "awaiting_approval",
            "created": time.time(),
        }
        self.requests[key] = entry
        self.service.store.record_event(
            task_id,
            kind + ".requested",
            {"request_id": request_id, "name": cleaned["name"]},
        )
        self.service.changed.emit()
        return self._receipt(entry)

    def _receipt(self, entry):
        receipt = {"request_id": entry["request_id"], "state": entry["state"]}
        if entry.get("saved_as"):
            receipt["saved_as"] = entry["saved_as"]
        return receipt

    def status(self, task_id, request_id):
        self.service._usable()
        entry = self.requests.get((task_id, request_id))
        if entry is None:
            raise KeyError("Proposal does not belong to this task")
        return self._receipt(entry)

    def pending(self, task_id):
        return [
            CreationProposal(
                task_id, e["request_id"], e["kind"], e["document"], e["state"]
            )
            for (task, _), e in self.requests.items()
            if task == task_id and e["state"] == "awaiting_approval"
        ]

    def resolve(self, task_id, request_id, *, approve=False, profile="global"):
        """Trusted UI only: the person's Add or Decline."""
        entry = self.requests.get((task_id, request_id))
        if entry is None or entry["state"] != "awaiting_approval":
            raise ValueError("This proposal is no longer waiting.")
        if approve:
            registry = self.service.profiles
            document = entry["document"]
            if entry["kind"] == "page":
                name = registry.start_pages.save_your_page(
                    profile, document["name"], document["title"], document["html"]
                )
                entry["saved_as"] = "steed:" + name
            else:
                saved = registry.automations.add(document, profile)
                entry["saved_as"] = saved["id"]
        entry["state"] = "applied" if approve else "denied"
        self.service.store.record_event(
            task_id,
            entry["kind"] + "." + entry["state"],
            {"request_id": request_id},
        )
        self.service.changed.emit()
        return self._receipt(entry)

    def cancel_task(self, task_id, *, interrupted=False):
        for key in [k for k in self.requests if k[0] == task_id]:
            self.requests.pop(key)["state"] = "cancelled"
