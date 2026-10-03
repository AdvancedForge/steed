"""Trusted, revocable offers of existing user tabs to one task."""

import weakref
from urllib.parse import urlsplit

from PyQt6 import sip

from services.command_broker import require_owner_thread
from services.task_policy import (
    TaskPermissionError,
    TaskScope,
    network_needs_permission,
    origin_url,
)


class LiveTabs:
    def __init__(self, service):
        self.service = service
        self.offers = {}

    def request(self, task_id, views):
        """Trusted UI only: select actual tabs before requesting a live scope."""
        service = self.service
        require_owner_thread(service)
        service._usable()
        service._active(task_id)
        if service.profiles is None or not hasattr(service.backend, "adopt_session"):
            raise ValueError("Live delegation requires a browser window")
        views = list(views)
        if not 1 <= len(views) <= 10 or len({id(view) for view in views}) != len(views):
            raise ValueError("Choose between one and ten tabs")
        profiles, origins = set(), set()
        for view in views:
            if sip.isdeleted(view):
                raise ValueError("Tab closed before delegation")
            offer = self.offers.get(view.agent_tab_id)
            if offer is not None and offer[0] != task_id:
                raise ValueError("This tab is already shared with another task")
            window = view.browser_window
            owner = window.profile_manager
            if owner not in service.profiles.owners or view not in window.tabs:
                raise ValueError("Tab does not belong to this browser")
            profile = "existing:" + owner.current_profile_name
            service.profiles.validate_task_profile(profile)
            profiles.add(profile)
            url = urlsplit(view.url().toString())
            origins.add(origin_url(f"{url.scheme}://{url.netloc}"))
            if service.backend.live_session(view) is not None:
                raise ValueError("Take back this tab before delegating it again")
            if view.page().devToolsPage() is not None:
                raise ValueError("Close this tab's Developer Tools before delegating")
        if len(profiles) != 1:
            raise ValueError("Choose tabs from one named profile")
        profile = profiles.pop()
        previous = service.store.grants(task_id)
        if any(record.status == "requested" for record in previous):
            raise ValueError("Resolve this task's pending permission request first")
        live = service._grants.get(task_id)
        values = live[1].scope.payload() if live else {}
        if values.get("profile") == profile:
            origins.update(values["origins"])
        values.update(
            profile=profile,
            origins=sorted(origins),
            actions=sorted(
                set(values.get("actions", ()))
                | {
                    "live",
                    "navigate",
                    "inspect",
                    "read",
                    "attach",
                    "edit",
                    "scroll",
                    "submit",
                    "visual",
                }
            ),
            allow_private_network=values.get("allow_private_network", False)
            or any(network_needs_permission(origin) for origin in origins),
            max_sessions=max(len(views), values.get("max_sessions", 1)),
        )
        # Validate everything before retiring prior authority. Replacement is
        # a fresh scope review, not an implicit privilege upgrade.
        scope = TaskScope.parse(values)
        service.connectors.check_scope(scope)
        if live:
            live[1].end("replaced")
            service._discard_sessions(task_id)
            service._grants.pop(task_id, None)
            service.store.resolve_grant(task_id, live[0], "ended", reason="replaced")
        record = service._request_grant(task_id, scope.payload())
        for view in views:
            view.browser_window._bind_live_tasks(service)
        for view in views:
            self.offers[view.agent_tab_id] = (
                task_id,
                record.grant_id,
                weakref.ref(view),
            )
            # BrowserWindow also returns authority before disposing a tab.
            view.destroyed.connect(
                lambda _=None, key=view.agent_tab_id: self.release_id(key)
            )
        self.activate(task_id)
        service.changed.emit()
        return record

    def activate(self, task_id):
        live = self.service._grants.get(task_id)
        if live is None:
            return
        for tab_id, (owner, grant_id, _) in list(self.offers.items()):
            if owner == task_id and grant_id == live[0]:
                try:
                    self.adopt(task_id, tab_id)
                except (ValueError, RuntimeError, KeyError):
                    # Closed/navigated tabs need a new explicit user handoff.
                    self.release_id(tab_id)

    def _view(self, task_id, tab_id):
        offer = self.offers.get(tab_id)
        if offer is None or offer[0] != task_id:
            raise TaskPermissionError("live_tab_not_shared")
        live = self.service._grants.get(task_id)
        if live is None or live[0] != offer[1]:
            raise TaskPermissionError("approval_required")
        view = offer[2]()
        if view is None or sip.isdeleted(view):
            raise TaskPermissionError("live_tab_closed")
        profile = "existing:" + view.browser_window.profile_manager.current_profile_name
        live[1].check("live", profile=profile, url=view.url().toString())
        live[1].check("read", url=view.url().toString())
        return view, profile

    def list(self, task_id):
        self.service._authorize(task_id, "live").check("read")
        tabs = []
        for key, offer in self.offers.items():
            if offer[0] != task_id:
                continue
            try:
                view, profile = self._view(task_id, key)
            except TaskPermissionError:
                continue
            tabs.append(
                {
                    "tab_id": key,
                    "profile": profile,
                    "window_id": view.browser_window.browser_window_id,
                    "url": view.url().toString(),
                    "title": view.title()[:512],
                    "session_id": self.service.backend.live_session(view),
                }
            )
        return {"tabs": tabs, "source": "user_shared_live_tabs"}

    def adopt(self, task_id, tab_id):
        service = self.service
        service._usable()
        service._active(task_id)
        view, profile = self._view(task_id, tab_id)
        existing = service.backend.live_session(view)
        if existing is not None:
            service._owned(task_id, existing)
            return service.backend.inspect(existing)
        grant = service._authorize(task_id, "live", profile=profile)
        if (
            sum(owner == task_id for owner in service._sessions.values())
            >= grant.scope.max_sessions
        ):
            raise TaskPermissionError("session_budget_exceeded")
        snapshot = service.backend.adopt_session(
            task_id, view, grant=grant, profile=profile
        )
        try:
            service.store.transition(task_id, "running")
            service.store.record_event(
                task_id, "session.live_adopted", service._metadata_evidence(snapshot)
            )
        except Exception:
            service.backend.close_session(snapshot.session_id)
            raise
        service._sessions[snapshot.session_id] = task_id
        service.changed.emit()
        return snapshot

    def release_id(self, tab_id):
        """Take Back is unconditional, including expiry, pending input and failure."""
        offer = self.offers.pop(tab_id, None)
        if offer is None:
            return
        service = self.service
        view = offer[2]()
        session_id = service.backend.live_session(view) if view is not None else None
        if session_id is not None:
            service._sessions.pop(session_id, None)
            service.backend.close_session(session_id)
            if not service._closed and not service.failure:
                try:
                    service.store.record_event(
                        offer[0], "session.live_returned", {"session_id": session_id}
                    )
                except Exception:
                    service._fatal()
        service.changed.emit()

    def discard(self, task_id=None):
        for key, offer in list(self.offers.items()):
            if task_id is None or offer[0] == task_id:
                self.offers.pop(key)
