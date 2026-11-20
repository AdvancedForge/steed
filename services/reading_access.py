"""Agents searching what you've read (services.page_memory), with permission.

reading.search asks with words; Steed answers with the matching pages you
read (title, address, when, a snippet and some of their text). Access &
Permissions → "Search what you've read" decides: Never refuses, Always
answers at once, Ask (the default) shows you the words first in a popup
(Allow, optionally always, or Deny), and the agent polls reading.status.
Only with Remember what I read on; never incognito. Answers stay in memory,
not in the task's records.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from browser import access_levels
from browser.data_panel_format import stable_id
from services.task_policy import TaskPermissionError

MAX_RESULTS = 10
RESULT_TEXT = 3000


@dataclass(frozen=True)
class ReadingRequest:
    task_id: str
    request_id: str
    query: str
    state: str

    @property
    def request_key(self):
        return "reading:" + self.task_id + ":" + self.request_id


class ReadingAccess:
    def __init__(self, service):
        self.service = service
        self.requests = {}  # (task, request id) -> dict

    def _memory(self):
        registry = self.service.profiles
        config = registry.config
        if not config.get("remember_pages", False):
            raise ValueError(
                "The person has not turned on Remember what I read "
                "(Settings → General), so there is nothing to search."
            )
        owner = getattr(registry, "last_active_owner", None) or next(
            iter(getattr(registry, "owners", ()) or ()), None
        )
        profile = getattr(owner, "current_profile_name", "global") or "global"
        if profile == "search":
            raise ValueError("Nothing is remembered in incognito.")
        return registry.get_history(profile).memory

    def _answer(self, entry):
        found = self._memory().search(entry["query"], limit=entry["limit"])
        entry["results"] = [
            {
                "title": r["title"],
                "url": r["url"],
                "read": datetime.fromtimestamp(r["visited"], timezone.utc).isoformat(),
                "snippet": r["snippet"],
                "text": r["text"][:RESULT_TEXT],
            }
            for r in found
        ]
        entry["state"] = "answered"

    def _receipt(self, entry):
        receipt = {"request_id": entry["request_id"], "state": entry["state"]}
        if entry["state"] == "answered":
            receipt["results"] = entry["results"]
        return receipt

    def search(self, task_id, query, request_id, limit=5):
        self.service._usable()
        self.service._active(task_id)
        if not stable_id(request_id):
            raise ValueError("Invalid request ID")
        query = " ".join(str(query).split())[:300]
        if not query:
            raise ValueError("Say what to search for.")
        key = (task_id, request_id)
        if key in self.requests:
            entry = self.requests[key]
            if entry["query"] != query:
                raise ValueError("Request ID is already bound to a different search")
            return self._receipt(entry)
        policy = access_levels.values(self.service.profiles.config, "agents")["reading"]
        if policy == "never":
            raise TaskPermissionError("reading_not_allowed")
        self._memory()  # Refuses at once when there is nothing to search.
        entry = {
            "task_id": task_id,
            "request_id": request_id,
            "query": query,
            "limit": max(1, min(MAX_RESULTS, int(limit or 5))),
            "state": "awaiting_approval",
            "created": time.time(),
        }
        self.requests[key] = entry
        remote = task_id in getattr(self.service, "remote_tasks", ())
        if not remote and (policy == "always" or self.service.auto_approve_requests):
            self._answer(entry)
        else:
            self.service.store._append(
                task_id, "reading.requested", {"request_id": request_id}
            )
            self.service.changed.emit()
        return self._receipt(entry)

    def status(self, task_id, request_id):
        self.service._usable()
        entry = self.requests.get((task_id, request_id))
        if entry is None:
            raise KeyError("Reading request does not belong to this task")
        return self._receipt(entry)

    def pending(self, task_id):
        return [
            ReadingRequest(task_id, e["request_id"], e["query"], e["state"])
            for (task, _), e in self.requests.items()
            if task == task_id and e["state"] == "awaiting_approval"
        ]

    def resolve(self, task_id, request_id, *, approve=False, always=False):
        """Trusted UI only."""
        entry = self.requests.get((task_id, request_id))
        if entry is None or entry["state"] != "awaiting_approval":
            raise ValueError("This search is no longer waiting.")
        if approve:
            if always:
                access_levels.set_policy(
                    self.service.profiles.config, "agents", "reading", "always"
                )
            self._answer(entry)
        else:
            entry["state"] = "denied"
        self.service.store._append(
            task_id, "reading." + entry["state"], {"request_id": request_id}
        )
        self.service.changed.emit()
        return self._receipt(entry)

    def cancel_task(self, task_id, *, interrupted=False):
        for key in [k for k in self.requests if k[0] == task_id]:
            entry = self.requests.pop(key)
            entry["state"] = "cancelled"
