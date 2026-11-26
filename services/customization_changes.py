"""Agents seeing and changing your customizations, with your review.

customizations.list shows what you have made in the profile you are using
(your pages, automations, skills, watches, user scripts and styles, panel
apps; settings come with settings.capabilities), customizations.read returns
one in full (its HTML, steps or code), and customizations.change proposes
replacing or removing one. Each change opens a review with what it is now and
what it would be; only your Apply changes anything. New ones are still
proposed with custom_page/automation/customization/watch.propose.

Access & Permissions → "See and change your customizations": Never refuses
all three; otherwise agents may read them, and every change still asks you.
"""

import json
import time
from dataclasses import dataclass

from browser import access_levels
from browser.data_panel_format import stable_id
from services.task_policy import TaskPermissionError

KINDS = ("page", "automation", "skill", "watch", "script", "app")
ACTIONS = ("replace", "remove")
MAX_ITEM = 512 * 1024


@dataclass(frozen=True)
class ChangeRequest:
    task_id: str
    request_id: str
    kind: str
    name: str
    action: str
    state: str

    @property
    def request_key(self):
        return "custchange:" + self.task_id + ":" + self.request_id


class CustomizationChanges:
    def __init__(self, service):
        self.service = service
        self.requests = {}  # (task, request id) -> entry

    # ------------------------------------------------------------ helpers

    @property
    def registry(self):
        return self.service.profiles

    def _allowed(self, task_id):
        self.service._usable()
        self.service._active(task_id)
        policy = access_levels.values(self.registry.config, "agents")["customize"]
        if policy == "never":
            raise TaskPermissionError("customize_not_allowed")

    def _profile(self):
        context = self.registry.agent_context() or {}
        profile = context.get("profile") or "global"
        if profile == "search":
            raise TaskPermissionError("private_profile_unavailable")
        return profile

    def _items(self, kind, profile):
        """kind -> [(id, name, full record)] as they are now."""
        registry = self.registry
        if kind == "page":
            return [
                (p["name"], p["title"], p)
                for p in registry.start_pages.your_pages(profile)
            ]
        if kind == "automation":
            return [(a["id"], a["name"], a) for a in registry.automations.list()]
        if kind == "skill":
            return [(s["id"], s["name"], s) for s in registry.skills.list()]
        if kind == "watch":
            return [
                (w["id"], w.get("name") or w.get("url", ""), w)
                for w in registry.watches.list()
            ]
        if kind == "script":
            return [
                (e["id"], e.get("name", ""), e)
                for e in registry.user_scripts(profile).entries
            ]
        return [
            (a["id"], a["name"], a)
            for a in registry.start_pages.state(profile).get("apps", [])
        ]

    def _find(self, kind, item_id, profile):
        if kind not in KINDS:
            raise ValueError("kind is one of " + ", ".join(KINDS))
        for identity, name, record in self._items(kind, profile):
            if identity == item_id:
                return name, dict(record)
        raise KeyError(f"No {kind} with id {item_id!r} here.")

    # ------------------------------------------------------- agent commands

    def inventory(self, task_id):
        """What you have made in this profile: ids and names (no content)."""
        self._allowed(task_id)
        profile = self._profile()
        return {
            "profile": profile,
            **{
                kind + "s": [
                    {"id": identity, "name": name}
                    for identity, name, _ in self._items(kind, profile)
                ]
                for kind in KINDS
            },
            "notes": "customizations.read gives one in full; customizations.change "
            "proposes replacing or removing it (the person reviews it). Settings: "
            "settings.capabilities and settings.propose.",
        }

    def read(self, task_id, kind, item_id):
        self._allowed(task_id)
        name, record = self._find(kind, item_id, self._profile())
        return {"kind": kind, "id": item_id, "name": name, "item": record}

    def change(self, task_id, request_id, kind, item_id, action, item=None):
        """Propose replacing (with item) or removing one customization."""
        self._allowed(task_id)
        if not stable_id(request_id):
            raise ValueError("Invalid request ID")
        key = (task_id, request_id)
        if key in self.requests:
            return self._receipt(self.requests[key])
        if action not in ACTIONS:
            raise ValueError("action is replace or remove")
        profile = self._profile()
        name, before = self._find(kind, item_id, profile)
        after = None
        if action == "replace":
            if not isinstance(item, dict):
                raise ValueError("Give the whole new version as item.")
            if len(json.dumps(item)) > MAX_ITEM:
                raise ValueError("That is too large.")
            if kind == "watch":
                raise ValueError(
                    "Watches are replaced by removing one and proposing a new "
                    "one with watch.propose."
                )
            after = self._checked(kind, item, before)
        pending = [
            e
            for (t, _), e in self.requests.items()
            if t == task_id and e["state"] == "awaiting_approval"
        ]
        if len(pending) >= 5:
            raise TaskPermissionError("customization_change_limit")
        entry = {
            "task_id": task_id,
            "request_id": request_id,
            "kind": kind,
            "id": item_id,
            "name": name,
            "action": action,
            "profile": profile,
            "before": before,
            "after": after,
            "state": "awaiting_approval",
            "created": time.time(),
        }
        self.requests[key] = entry
        self.service.store._append(
            task_id,
            "customization.change_requested",
            {"request_id": request_id, "kind": kind, "action": action},
        )
        self.service.changed.emit()
        return self._receipt(entry)

    def status(self, task_id, request_id):
        self.service._usable()
        entry = self.requests.get((task_id, request_id))
        if entry is None:
            raise KeyError("Change request does not belong to this task")
        return self._receipt(entry)

    # ------------------------------------------------------------- checks

    @staticmethod
    def _checked(kind, item, before):
        """The new version, checked as Steed checks anything you make."""
        if kind == "page":
            from services.start_pages import clean_html

            title = " ".join(str(item.get("title", before["title"])).split())[:120]
            if not title:
                raise ValueError("Give the page a title.")
            return {
                "name": before["name"],
                "title": title,
                "html": clean_html(item.get("html", "")),
            }
        if kind == "automation":
            from services.automations import check

            return check(item)
        if kind == "skill":
            from services.skills import check

            return check(item)
        if kind == "script":
            from browser.user_scripts import clean

            return clean({**item, "id": before["id"]})
        from services.start_pages import clean_app

        return clean_app({**item, "id": before["id"]})

    # --------------------------------------------------------------- review

    def pending(self, task_id):
        return [
            ChangeRequest(
                task_id, e["request_id"], e["kind"], e["name"], e["action"], e["state"]
            )
            for (task, _), e in self.requests.items()
            if task == task_id and e["state"] == "awaiting_approval"
        ]

    def entry(self, task_id, request_id):
        return self.requests.get((task_id, request_id))

    def resolve(self, task_id, request_id, *, approve=False):
        """Trusted UI only: the person's Apply or Decline."""
        entry = self.requests.get((task_id, request_id))
        if entry is None or entry["state"] != "awaiting_approval":
            raise ValueError("This change is no longer waiting.")
        if approve:
            self._apply(entry)
            entry["state"] = "applied"
        else:
            entry["state"] = "declined"
        self.service.store._append(
            task_id,
            "customization.change_" + entry["state"],
            {"request_id": request_id},
        )
        self.service.changed.emit()
        return self._receipt(entry)

    def _apply(self, entry):
        registry, profile = self.registry, entry["profile"]
        kind, identity, after = entry["kind"], entry["id"], entry["after"]
        if entry["action"] == "remove":
            if kind == "page":
                registry.start_pages.remove_your_page(profile, identity)
            elif kind == "automation":
                registry.automations.remove(identity)
            elif kind == "skill":
                registry.skills.remove(identity)
            elif kind == "watch":
                registry.watches.remove(identity)
            elif kind == "script":
                registry.user_scripts(profile).remove(identity)
            else:
                registry.start_pages.remove_app(profile, identity)
            return
        if kind == "page":
            registry.start_pages.save_your_page(
                profile, identity, after["title"], after["html"], replacing=identity
            )
        elif kind == "automation":
            registry.automations.update(identity, after)
        elif kind == "skill":
            registry.skills.update(identity, after)
        elif kind == "script":
            registry.user_scripts(profile).save(after)
        else:
            registry.start_pages.save_app(
                profile, after["name"], after["html"], identity=identity
            )

    def _receipt(self, entry):
        return {
            "request_id": entry["request_id"],
            "kind": entry["kind"],
            "id": entry["id"],
            "action": entry["action"],
            "state": entry["state"],
        }

    def cancel_task(self, task_id, *, interrupted=False):
        for key in [k for k in self.requests if k[0] == task_id]:
            entry = self.requests.pop(key)
            if entry["state"] == "awaiting_approval":
                entry["state"] = "cancelled"
