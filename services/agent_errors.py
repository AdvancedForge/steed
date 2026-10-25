"""Public diagnostics use maintained messages, never arbitrary exception text."""


class AgentRequestError(ValueError):
    def __init__(self, code, message, field=None):
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.field = field


def scope_error(error):
    message = str(error)
    known = {
        "Unsupported task action": (
            "actions",
            "Use actions listed by task.capabilities; actions are permission names, not tool names.",
        ),
        "Unknown scope fields": (
            None,
            "Use the published scope fields; task identity belongs to this connection.",
        ),
        "Private addresses and DNS hostnames require explicit network permission": (
            "allow_private_network",
            "Set allow_private_network=true for private IPs or for named sites when explicitly disabling the inferred network permission. The user reviews this permission.",
        ),
        "Use private or existing:<profile name>": (
            "profile",
            "Use profile='private' for a new isolated session, or the exact existing:<name> selector from browser.context.",
        ),
        "Choose an existing normal profile other than search": (
            "profile",
            "Use profile='private' for isolated browsing. Do not use 'search' or a guessed profile name.",
        ),
        "Download permission requires explicit destinations": (
            "download_destinations",
            "The download action needs exact absolute destination filenames; omit download if you do not need files.",
        ),
        "Upload permission requires exact sources and origins": (
            "upload_sources",
            "The upload action needs exact absolute upload_sources and upload_origins included in origins.",
        ),
        "Upload origins must be allowed task origins": (
            "upload_origins",
            "Include each upload origin in origins as well.",
        ),
        "App permission requires exact tools, effects and destinations": (
            "connector_permissions",
            "Pair the app action with exact connector_permissions from app.list_tools.",
        ),
    }
    if message in known:
        field, guidance = known[message]
    elif message.startswith("Invalid ") and message[8:] in {
        "duration_seconds",
        "max_operations",
        "max_requests",
        "max_sessions",
        "max_download_bytes",
        "max_upload_bytes",
    }:
        field, guidance = (
            message[8:],
            "Use an integer within the published bound for this scope field.",
        )
    elif (
        "origin" in message.lower()
        or "hostname" in message.lower()
        or "IP address" in message
    ):
        field, guidance = (
            "origins",
            "Use exact HTTP(S) origins such as https://example.com, without paths, credentials, query strings or wildcards.",
        )
    else:
        field, guidance = (
            None,
            "Invalid permission scope. Use the published types and bounds; discover supported actions and configured app tools before requesting access.",
        )
    return AgentRequestError("invalid_scope", guidance, field)


OPERATION_GUIDANCE = {
    "request_in_progress": "Another call is pending. This call was not dispatched; wait for it to finish.",
    "invalid_request": "Use the published tool schema. Discovery tools take {} without task_id; task identity belongs to this connection. Discovery needs no grant or separate enable switch.",
    "approval_required": "Request the exact scope with steed_grant_request and wait for an approved steed_grant_list entry. Discovery tools need no grant.",
    "task_inactive": "This task has ended. Start a new chat or task; enabling discovery cannot restore an ended task.",
    "page_not_ready": "The page is still loading or failed to load. Check session.inspect, then page.wait before inspecting controls.",
    "session_not_owned": "This session closed, was returned or was replaced by a new grant. Use browser.tabs or open a new approved session; do not replay previous effects.",
    "user_control_active": "The user has control. Wait for the user to return it; agent operations cannot override this choice.",
    "native_frontend_in_use": "Close this tab's Developer Tools before using Steed screenshots or native input.",
    "native_frontend_unavailable": "Qt's native page bridge is unavailable or incompatible. Close Developer Tools and show the page, then check task.capabilities for engine versions. This is a browser integration failure, not a denied grant.",
    "native_input_unavailable": "The browser could not confirm native input. Inspect a fresh screenshot before another attempt; the website may already have received part of the action.",
    "capture_unavailable": "The browser could not capture this viewport. Show it with session.watch and close Developer Tools; this is an image/engine failure, not a denied grant.",
    "capture_required": "Capture the visible page before computer input; use the returned capture_id once.",
    "expired": "The grant expired. Start a new task with suitable duration and fresh access; check previous effects before continuing.",
    "operation_budget_exhausted": "The task reached max_operations. Start a new task with a larger reviewed limit; do not replay completed effects.",
    "request_budget_exhausted": "The task reached max_requests. Start a new task with a larger reviewed limit; do not replay completed effects.",
    "stale_capture": "Capture the page again. The previous image was used, expired, resized, scrolled or navigated; each computer action needs a new capture_id.",
    "live_session_required": "Computer input needs a normal live website. Request live/read/visual/computer permissions, then open session.open(mode=live) or adopt a shared live tab.",
    "invalid_coordinates": "Use x/y pixels inside the returned screenshot dimensions, starting at its top-left corner.",
    "native_input_timeout": "Input outcome is uncertain. Capture and inspect the website before another action; do not automatically resend clicks, text or keys.",
    "stale_snapshot": "The page changed. Inspect or capture it again and use the new references; do not replay a completed action.",
    "control_unsupported": "This control does not support the requested DOM operation. Inspect its operations/constraints; a new grant cannot add an unsupported action. Use approved visual/computer input when appropriate.",
    "target_not_editable": "The control is disabled, readonly, hidden or no longer editable. Inspect the current page before another attempt.",
    "focus_unavailable": "The page refused or redirected input focus. Inspect the intended control and current page; no automatic retry is performed.",
    "visible_view_required": "Show the page with session.watch before capturing its viewport.",
    "action_out_of_scope": "This action is missing from the active grant. Read grant.list and request an updated scope; keep waiting for approval before using it.",
    "origin_out_of_scope": "This site's origin is missing from the approved scope. Request an updated scope with the exact new origin.",
    "profile_out_of_scope": "Use the exact profile selector in grant.list. For a new isolated session request profile='private'.",
    "live_tab_not_shared": "The user must select Share browser tabs in the sidebar before this tab can be adopted. Approval alone does not share tabs.",
    "session_budget_exceeded": "The grant's max_sessions limit was reached. Close an unused session or request a larger limit.",
}


def operation_guidance(code):
    return OPERATION_GUIDANCE.get(
        code, "Task operation failed; check scope, references, and approval status."
    )
