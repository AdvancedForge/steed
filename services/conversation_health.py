"""Presentation-only connection facts and maintained, value-free diagnostics."""

import json
import time

from services.agent_tools import TOOLS
from services.task_store import ACTIVE_STATUSES

_COMMANDS = {tool.command for tool in TOOLS}
_GUIDANCE = {
    "control_unsupported": (
        "Unsupported control action",
        "Inspect the control's available operations and constraints. A new grant cannot add an unsupported action; approved screenshot/computer input may help.",
        "receipts",
    ),
    "target_not_editable": (
        "Control is not editable",
        "The control is disabled, readonly, hidden or changed. Inspect the current page before another attempt.",
        "receipts",
    ),
    "focus_unavailable": (
        "Input focus changed",
        "The website refused or redirected focus. Inspect the intended control and current page before retrying.",
        "receipts",
    ),
    "approval_required": (
        "Access needs approval",
        "Review Permissions, then let the agent check its approved grant.",
        "permissions",
    ),
    "invalid_scope": (
        "Invalid permission request",
        "The agent must correct its permission fields using task capabilities. Discovery does not need a grant.",
        "permissions",
    ),
    "action_out_of_scope": (
        "Action outside approved access",
        "Ask the agent to request the missing action and wait for approval.",
        "permissions",
    ),
    "origin_out_of_scope": (
        "Site outside approved access",
        "The agent needs approval for this exact site's origin.",
        "permissions",
    ),
    "profile_out_of_scope": (
        "Profile outside approved access",
        "Use the profile selected in the approved grant.",
        "permissions",
    ),
    "live_tab_not_shared": (
        "Tabs have not been shared",
        "Use Give tab control to choose the tabs this agent may adopt.",
        "permissions",
    ),
    "user_control_active": (
        "You have tab control",
        "Return control when you want the agent to continue using that tab.",
        "permissions",
    ),
    "expired": (
        "Permission expired",
        "Continue in a new task with a suitable reviewed duration. Check earlier effects before continuing.",
        "permissions",
    ),
    "operation_budget_exhausted": (
        "Operation limit reached",
        "Continue in a new task with a suitable reviewed limit. Completed actions must not be repeated.",
        "permissions",
    ),
    "request_budget_exhausted": (
        "Request limit reached",
        "Continue in a new task with a suitable reviewed limit. Completed actions must not be repeated.",
        "permissions",
    ),
    "task_inactive": (
        "Task has ended",
        "Prepare a continuation for a new chat or another connected agent. Old permissions stay ended.",
        "receipts",
    ),
    "native_input_timeout": (
        "Input outcome is uncertain",
        "Check the website and Receipts before another attempt; input may already have reached the site.",
        "receipts",
    ),
    "native_input_unavailable": (
        "Input could not be confirmed",
        "Inspect a fresh screenshot and Receipts before retrying; part of the action may have reached the site.",
        "receipts",
    ),
    "capture_unavailable": (
        "Screenshot unavailable",
        "Show the task page and close its Developer Tools, then ask for a fresh capture. This is not a denied permission.",
        "receipts",
    ),
    "native_frontend_unavailable": (
        "Browser integration unavailable",
        "Show the page, close Developer Tools and check engine capabilities. A new grant will not repair an engine failure.",
        "receipts",
    ),
    "native_frontend_in_use": (
        "Developer Tools is using the page bridge",
        "Close Developer Tools on that tab before requesting screenshots or native input.",
        "receipts",
    ),
    "stale_capture": (
        "Screenshot is out of date",
        "The agent needs a fresh capture before another computer action.",
        "receipts",
    ),
    "stale_snapshot": (
        "Page controls changed",
        "The agent needs a fresh inspection and new control references.",
        "receipts",
    ),
    "session_not_owned": (
        "Page session is no longer available",
        "The page was closed, returned or replaced. Inspect available tabs or open a new approved session.",
        "permissions",
    ),
    "page_not_ready": (
        "Page is not ready",
        "Check whether the site loaded, then wait or reload before inspecting its controls.",
        "receipts",
    ),
    "visible_view_required": (
        "Page must be visible",
        "Show the task page before requesting a screenshot.",
        "receipts",
    ),
    "invalid_request": (
        "Invalid tool request",
        "The agent must use the published tool schema. Task identity is supplied by its connection.",
        "permissions",
    ),
}


def latest_failure(store, task_id, dismissed=0):
    """Read only bounded journal metadata; never expose exception/request values."""
    row = store._db.execute(
        "SELECT sequence, payload FROM events WHERE task_id=? AND sequence>? "
        "AND kind IN ('client.rejected','permission.denied') "
        "ORDER BY sequence DESC LIMIT 1",
        (task_id, dismissed),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload"])
    code = payload.get("code")
    command = payload.get("command")
    if not isinstance(code, str) or not isinstance(command, str):
        return None
    title, guidance, action = _GUIDANCE.get(
        code,
        (
            "Tool operation failed",
            "Check Permissions and Receipts before continuing. The operation's effects may need verification.",
            "receipts",
        ),
    )
    return {
        "sequence": row["sequence"],
        "code": code if code in _GUIDANCE else "operation_failed",
        "command": command if command in _COMMANDS else "Agent operation",
        "title": title,
        "guidance": guidance,
        "action": action,
    }


def connection_state(status, connected, report, resume_until=None):
    if report and report["state"] == "connecting":
        return {"state": "connecting", "label": "Connecting", "tone": "neutral"}
    if connected and status in ACTIVE_STATUSES:
        return {"state": "connected", "label": "Connected", "tone": "success"}
    if status in ACTIVE_STATUSES and resume_until is not None:
        return {
            "state": "reconnecting",
            "label": "Waiting to reconnect until "
            + time.strftime("%H:%M", time.localtime(resume_until)),
            "tone": "warning",
        }
    if status in ACTIVE_STATUSES:
        return {"state": "disconnected", "label": "Disconnected", "tone": "warning"}
    return {"state": "ended", "label": "Task ended", "tone": "neutral"}


def delivery_label(message, read_sequence, *, active, provider=None, task_id=None):
    if message["role"] != "user":
        return ""
    if message["sequence"] <= read_sequence:
        return "Read by agent"
    if not active:
        return "Not read · connection unavailable"
    if provider is not None and hasattr(provider, "delivery"):
        label = provider.delivery(task_id, message["message_id"])
        if label:
            return label
    return "Waiting to be read"
