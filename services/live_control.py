"""Explicit live-tab takeover requests pinned to the user's selected window."""

import weakref
from dataclasses import dataclass, field

from PyQt6 import sip
from PyQt6.QtCore import QUrl

from services.agent_errors import AgentRequestError
from services.task_policy import origin_url


def view_origin(view):
    return origin_url(
        view.url()
        .adjusted(
            QUrl.UrlFormattingOption.RemovePath
            | QUrl.UrlFormattingOption.RemoveQuery
            | QUrl.UrlFormattingOption.RemoveFragment
            | QUrl.UrlFormattingOption.RemoveUserInfo
        )
        .toString()
    )


@dataclass
class ControlRequest:
    task_id: str
    request_id: str
    selection: str
    profile: str
    window: object = field(repr=False)
    views: tuple = field(repr=False)
    origins: tuple = field(repr=False)
    state: str = "requested"
    grant_id: str = ""

    @property
    def request_key(self):
        return "control:" + self.task_id + ":" + self.request_id

    def payload(self):
        return {
            "request_id": self.request_id,
            "state": self.state,
            "selection": self.selection,
            "grant_id": self.grant_id,
        }


def request(service, task_id, request_id, selection="current_tab"):
    service._active(task_id)
    if (
        not isinstance(request_id, str)
        or not request_id.isascii()
        or not 1 <= len(request_id) <= 64
        or not all(c.isalnum() or c in "_-" for c in request_id)
    ):
        raise AgentRequestError(
            "invalid_request_id",
            "Use a stable request_id with up to 64 letters, digits, underscores or hyphens.",
        )
    if selection not in ("current_tab", "profile_tabs"):
        raise AgentRequestError(
            "invalid_tab_selection", "Choose current_tab or profile_tabs."
        )
    key = (task_id, request_id)
    if key in service.control_requests:
        record = service.control_requests[key]
        if record.selection != selection:
            raise AgentRequestError(
                "request_id_conflict",
                "This request_id already names a different selection.",
            )
        return service._control_status(task_id, request_id)
    if service.profiles is None:
        raise AgentRequestError(
            "browser_unavailable",
            "Live takeover needs the desktop browser. Use a private task session instead.",
        )
    if any(
        owner == task_id and r.state == "requested"
        for (owner, _), r in service.control_requests.items()
    ):
        raise AgentRequestError(
            "control_request_pending",
            "Another takeover request is pending. Poll its control_status before requesting another selection.",
        )
    if sum(owner == task_id for owner, _ in service.control_requests) >= 100:
        raise AgentRequestError(
            "control_request_limit",
            "This task reached its takeover-request limit. Start a new task before requesting more handoffs.",
        )
    context = service.profiles.agent_context()
    window = next(
        (
            o.parent()
            for o in service.profiles.owners
            if getattr(o.parent(), "browser_window_id", None) == context["window_id"]
        ),
        None,
    )
    if window is None or window._closing:
        raise AgentRequestError(
            "browser_unavailable", "Open a browser window before requesting control."
        )
    profile = window.profile_manager.current_profile_name
    if profile == "search":
        raise AgentRequestError(
            "private_tab_not_shareable",
            "For isolated browsing use profile='private' and session.open(mode='live'). User incognito tabs are not implicitly shared.",
        )
    if selection == "profile_tabs":
        views = [
            v
            for o in service.profiles.owners
            if o.current_profile_name == profile
            for v in o.parent().tabs
            if not getattr(v, "agent_panel", False)
            and not getattr(v, "workspace_name", None)
            and v.url().scheme() in ("http", "https")
        ]
    else:
        view = window.tabs[window.current_tab_index]
        if getattr(view, "agent_panel", False):
            view = getattr(view, "agent_source", None)
        if getattr(view, "workspace_name", None):
            view = None
        views = (
            [view]
            if view is not None and view.url().scheme() in ("http", "https")
            else []
        )
    if not 1 <= len(views) <= 10:
        raise AgentRequestError(
            "no_shareable_tabs",
            "Select a loaded HTTP(S) browsing tab, or choose a profile with one to ten website tabs.",
        )
    record = ControlRequest(
        task_id,
        request_id,
        selection,
        profile,
        weakref.ref(window),
        tuple(weakref.ref(v) for v in views),
        tuple(view_origin(v) for v in views),
    )
    service.control_requests[key] = record
    service.store.record_event(
        task_id,
        "browser.control_requested",
        {"request_id": request_id, "selection": selection},
    )
    if service.auto_approve_requests and task_id not in getattr(
        service, "remote_tasks", ()
    ):
        resolve(service, record, approve=True)
    service.changed.emit()
    return record.payload()


def resolve(service, record, *, approve=False):
    service._active(record.task_id)
    if record.state != "requested":
        return record.payload()
    if not approve:
        record.state = "denied"
    else:
        window = record.window()
        views = [reference() for reference in record.views]
        if (
            window is None
            or sip.isdeleted(window)
            or window._closing
            or window.profile_manager.current_profile_name != record.profile
            or any(
                v is None
                or sip.isdeleted(v)
                or v not in v.browser_window.tabs
                or v.browser_window._closing
                or getattr(v, "native_panel", None) is not None
                or v.url().scheme() not in ("http", "https")
                or v.browser_window.profile_manager.current_profile_name
                != record.profile
                for v in views
            )
            or tuple(view_origin(v) for v in views) != record.origins
        ):
            record.state = "stale"
        else:
            for grant in service.store.grants(record.task_id):
                if grant.status == "requested":
                    service.deny_grant(record.task_id, grant.grant_id)
            grant = service.live_tabs.request(record.task_id, views)
            if grant.status == "requested":
                grant = service.approve_grant(record.task_id, grant.grant_id)
            record.grant_id = grant.grant_id
            record.state = "approved"
    service.store.record_event(
        record.task_id,
        "browser.control_resolved",
        {"request_id": record.request_id, "state": record.state},
    )
    service.changed.emit()
    return record.payload()
