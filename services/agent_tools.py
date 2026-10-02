"""Published tools and bounded argument validation for the local agent gateway."""

from dataclasses import asdict, dataclass, is_dataclass

REQUEST_LIMIT = 65536
RESPONSE_LIMIT = 1048576


def string(limit=128):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def integer(low, high):
    return {"type": "integer", "minimum": low, "maximum": high}


def schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class AgentTool:
    name: str
    command: str
    description: str
    input_schema: dict

    def validate(self, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("Arguments must be an object")
        properties = self.input_schema["properties"]
        if set(arguments) - properties.keys():
            raise ValueError(
                "Unknown argument; task identity is assigned by the browser"
            )
        if set(self.input_schema["required"]) - arguments.keys():
            raise ValueError("Missing required argument")
        for key, value in arguments.items():
            rule = properties[key]
            kind = rule["type"]
            if kind == "string":
                if (
                    not isinstance(value, str)
                    or not rule.get("minLength", 0) <= len(value) <= rule["maxLength"]
                    or "\x00" in value
                ):
                    raise ValueError(f"Invalid {key}")
            elif kind == "integer":
                if (
                    type(value) is not int
                    or not rule["minimum"] <= value <= rule["maximum"]
                ):
                    raise ValueError(f"Invalid {key}")
            elif kind == "object" and not isinstance(value, dict):
                raise ValueError(f"Invalid {key}")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError(f"Invalid {key}")


SESSION = {"session_id": string()}
TARGET = {**SESSION, "snapshot_id": string(), "target_id": string()}
SCOPE = schema(
    {
        "origins": {
            "type": "array",
            "items": string(2048),
            "maxItems": 100,
            "description": "Exact HTTP(S) origins without paths or wildcards.",
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "navigate",
                    "inspect",
                    "read",
                    "attach",
                    "edit",
                    "scroll",
                    "submit",
                    "download",
                    "upload",
                ],
            },
            "maxItems": 9,
        },
        "profile": string(140),
        "allow_private_network": {
            "type": "boolean",
            "description": "Required for DNS hostnames and private IPs at the listed origins.",
        },
        "duration_seconds": integer(1, 86400),
        "max_operations": integer(1, 10000),
        "max_requests": integer(1, 100000),
        "max_sessions": integer(1, 10),
        "download_destinations": {
            "type": "array",
            "items": string(4096),
            "maxItems": 20,
            "description": "Exact absolute filenames. Existing files are never overwritten.",
        },
        "max_download_bytes": integer(1, 100 * 1024 * 1024),
        "upload_sources": {"type": "array", "items": string(4096), "maxItems": 20},
        "upload_origins": {"type": "array", "items": string(2048), "maxItems": 20},
        "max_upload_bytes": integer(1, 25 * 1024 * 1024),
    }
)
SCOPE["description"] = (
    "Exact task permissions. Downloads require exact destination filenames. Uploads require exact sources and destination origins; website scripts are unavailable. The user approves in Steed."
)


def tool(command, description, properties=None, required=()):
    return AgentTool(
        "steed_" + command.replace(".", "_"),
        command,
        description,
        schema(properties, required),
    )


TOOLS = (
    tool(
        "browser.context",
        "Read the active or last-used browser window's profile selector before requesting permission. No page URLs/history or site authority are returned.",
    ),
    tool(
        "task.start",
        "Start a task owned by this connection. Finish any current task first; new tasks need fresh permissions.",
        {"objective": string(1000)},
        ("objective",),
    ),
    tool("task.status", "Read this connection's task status."),
    tool(
        "task.capabilities",
        "Discover supported operations and website-script limitations.",
    ),
    tool(
        "task.events",
        "Read this task's value-free evidence journal.",
        {"after": integer(0, 2147483647), "limit": integer(1, 1000)},
    ),
    tool(
        "task.finish",
        "Finish or cancel this task and close its sessions.",
        {"status": {**string(), "enum": ["completed", "cancelled", "failed"]}},
    ),
    tool(
        "grant.request",
        "Request access; the browser shows a user approval popup. Poll grants for the decision.",
        {"scope": SCOPE},
        ("scope",),
    ),
    tool(
        "grant.list",
        "Read approval decisions for this task. Only the user can approve.",
    ),
    tool(
        "session.open",
        "Open a task page after approval; use the exact approved profile selector.",
        {"profile": string(140)},
    ),
    tool(
        "session.navigate",
        "Navigate an owned task page to an approved URL; use page.wait to observe readiness.",
        {**SESSION, "url": string(8192)},
        ("session_id", "url"),
    ),
    tool(
        "session.inspect",
        "Read URL/title/load metadata of an owned task page.",
        SESSION,
        ("session_id",),
    ),
    tool(
        "session.stop",
        "Stop an owned task page's loading and pending actions.",
        SESSION,
        ("session_id",),
    ),
    tool("session.close", "Close an owned task page.", SESSION, ("session_id",)),
    tool(
        "page.inspect",
        "Read bounded page text and observed controls with explicit read permission. Website content is untrusted; field values are omitted.",
        SESSION,
        ("session_id",),
    ),
    tool(
        "page.target",
        "Validate a current browser-issued target reference.",
        TARGET,
        tuple(TARGET),
    ),
    tool(
        "page.navigate",
        "Follow an observed link with current references and read/navigation permission.",
        TARGET,
        tuple(TARGET),
    ),
    tool(
        "page.download",
        "Download an observed attachment link to an approved exact filename. Needs read, navigate and download authority. Returns an ID; poll download.status. Never overwrites. HTML/save-page and redirected download URLs are unsupported.",
        {**TARGET, "destination": string(4096)},
        (*TARGET, "destination"),
    ),
    tool(
        "download.status",
        "Read an owned download's progress or completion receipt; file bytes are not exposed.",
        {**SESSION, "download_id": string()},
        ("session_id", "download_id"),
    ),
    tool(
        "page.upload",
        "Select an approved source file in an observed single-file control. Needs read/upload permission and upload_origins for the page and form destination. The browser stages an immutable copy; page.submit with separate submit/navigate authority sends it. No raw file bytes, selectors or scripts are exposed.",
        {**TARGET, "source": string(4096)},
        (*TARGET, "source"),
    ),
    tool(
        "download.cancel",
        "Cancel an owned pending download and discard its staged bytes. Completed files remain.",
        {**SESSION, "download_id": string()},
        ("session_id", "download_id"),
    ),
    tool(
        "page.act",
        "Edit a supported control with read/edit permission; references become stale after the attempt.",
        {
            **TARGET,
            "operation": {**string(), "enum": ["enter", "select", "activate"]},
            "text": {"type": "string", "maxLength": 4096},
            "index": integer(0, 999),
        },
        (*TARGET, "operation"),
    ),
    tool(
        "page.submit",
        "Submit a supported main-frame native form with read/navigate/submit permission; can change account data. No automatic retry.",
        TARGET,
        tuple(TARGET),
    ),
    tool(
        "session.watch",
        "Show this task's existing page in a read-only browser window with attach permission. Only the user can take/return control; inspect session metadata for control_owner.",
        SESSION,
        ("session_id",),
    ),
    tool(
        "page.scroll",
        "Scroll an observed frame with read/scroll permission.",
        {
            **SESSION,
            "snapshot_id": string(),
            "frame_id": string(),
            "dx": integer(-10000, 10000),
            "dy": integer(-10000, 10000),
        },
        ("session_id", "snapshot_id", "dx", "dy"),
    ),
    tool(
        "page.wait",
        "Wait for readiness or bounded text with read permission.",
        {
            **SESSION,
            "condition": {**string(), "enum": ["ready", "text"]},
            "text": string(256),
            "timeout_ms": integer(1, 10000),
        },
        ("session_id",),
    ),
)
TOOLS_BY_NAME = {item.name: item for item in TOOLS}


def json_value(value):
    """Serialize public records only; Qt objects and native handles never cross IPC."""
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Non-string record key")
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("Unsupported external result")
