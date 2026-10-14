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
