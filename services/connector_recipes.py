"""Strict, immutable connector recipes; data never imports executable code."""

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from services.connector_policy import connector_name
from services.recipe_templates import JsonTemplate, json_template
from services.task_workspace import unique_object
from utils import save_json

MAX_RECIPE_BYTES = 16384

RECIPE_EXAMPLE = {
    "version": 1,
    "name": "Example Notes",
    "origin": "https://api.fixture.example",
    "authentication": {"type": "bearer"},
    "identity": {"path": "/me", "subject": "/id", "label": "/email"},
    "tools": [
        {
            "name": "read_note",
            "description": "Read one selected note",
            "method": "GET",
            "path": "/notes/{destination}",
            "response": [
                {"label": "title", "pointer": "/title"},
                {"label": "body", "pointer": "/body"},
            ],
        }
    ],
}

RECIPE_OAUTH_EXAMPLE = {
    **RECIPE_EXAMPLE,
    "name": "Example OAuth Notes",
    "authentication": {
        "type": "oauth_pkce",
        "authorization_endpoint": "https://auth.fixture.example/authorize",
        "token_endpoint": "https://auth.fixture.example/token",
        "scopes": ["profile", "notes.read"],
    },
    "tools": [
        *RECIPE_EXAMPLE["tools"],
        {
            "name": "list_notes",
            "description": "Read this account's notes",
            "method": "GET",
            "path": "/notes",
            "response": [{"label": "notes", "pointer": "/items"}],
        },
    ],
}

RECIPE_PAGINATED_EXAMPLE = {
    **RECIPE_OAUTH_EXAMPLE,
    "name": "Example Paginated Notes",
    "tools": [
        {
            **RECIPE_OAUTH_EXAMPLE["tools"][1],
            "pagination": {"cursor": "/next", "parameter": "cursor", "max_pages": 3},
        }
    ],
}

RECIPE_WRITE_EXAMPLE = {
    **RECIPE_EXAMPLE,
    "version": 2,
    "name": "Example Notes and Messages",
    "tools": [
        *RECIPE_EXAMPLE["tools"],
        {
            "name": "create_note",
            "description": "Create a note after reviewing its payload",
            "method": "POST",
            "effect": "write",
            "path": "/notes",
            "destination": "account",
            "request": {
                "title": "artifact.title",
                "body": "artifact.content",
                "operation_id": "operation_id",
            },
            "idempotency_header": "Idempotency-Key",
            "response": [{"label": "id", "pointer": "/id"}],
            "reconciliation": {
                "path": "/operations/{operation_id}",
                "operation_id": "/operation_id",
                "status": "/status",
                "success": "succeeded",
            },
        },
        {
            "name": "send_message",
            "description": "Send a message to one reviewed recipient",
            "method": "POST",
            "effect": "send",
            "path": "/messages",
            "destination": "email",
            "request": {
                "to": "destination",
                "subject": "artifact.title",
                "body": "artifact.content",
                "operation_id": "operation_id",
            },
            "response": [{"label": "id", "pointer": "/id"}],
        },
    ],
}

RECIPE_NESTED_EXAMPLE = {
    **RECIPE_EXAMPLE,
    "version": 3,
    "name": "Example Nested Notes",
    "tools": [
        {
            **RECIPE_WRITE_EXAMPLE["tools"][1],
            "request": {
                "data": {
                    "title": {"$bind": "artifact.title"},
                    "body": {"text": {"$bind": "artifact.content"}, "format": "text"},
                    "operation_id": {"$bind": "operation_id"},
                    "tags": ["created-in-steed"],
                    "published": False,
                }
            },
        }
    ],
}


class RecipeError(ValueError):
    pass


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RecipeError("Unsupported or missing connector recipe fields")


def printable(value, maximum):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or not value.isprintable()
    ):
        raise RecipeError("Provide bounded, printable recipe text")
    return value


def recipe_origin(value):
    printable(value, 253)
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or value != "https://" + host
        or not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host
        )
        or host.endswith((".localhost", ".local", ".internal", ".invalid"))
    ):
        raise RecipeError("Use an exact public HTTPS domain without ports or paths")
    return value


def request_path(value, *, destination=False):
    printable(value, 512)
    # Every character is reviewed; IDs are single segments, never URLs or queries.
    template = value.replace("{destination}", "resource") if destination else value
    if (
        not template.startswith("/")
        or template.startswith("//")
        or not re.fullmatch(r"/[A-Za-z0-9/_.,:@=-]*", template)
        or any(p in {".", "..", ""} for p in template[1:].split("/"))
        or destination
        and (
            value.count("{destination}") > 1
            or "{destination}" in value
            and "{destination}" not in value.split("/")
        )
    ):
        raise RecipeError(
            "Use a fixed API path with at most one complete {destination} segment"
        )
    return value


def recipe_endpoint(value):
    printable(value, 768)
    parsed = urlsplit(value)
    origin = recipe_origin("https://" + (parsed.hostname or ""))
    path = request_path(parsed.path)
    if value != origin + path:
        raise RecipeError("Use an exact public HTTPS authentication endpoint")
    return value


@dataclass(frozen=True)
class RecipeAuthentication:
    kind: str = "bearer"
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    scopes: tuple = ()


def authentication(value):
    if not isinstance(value, dict):
        raise RecipeError("Choose recipe authentication")
    if value.get("type") == "bearer":
        fields(value, ("type",))
        return RecipeAuthentication()
    fields(value, ("type", "authorization_endpoint", "token_endpoint", "scopes"))
    if value["type"] != "oauth_pkce" or not isinstance(value["scopes"], list):
        raise RecipeError("Choose bearer or native OAuth PKCE authentication")
    scopes = value["scopes"]
    if (
        not 1 <= len(scopes) <= 20
        or any(
            not isinstance(s, str)
            or not re.fullmatch(r"[\x21\x23-\x5b\x5d-\x7e]{1,200}", s)
            for s in scopes
        )
        or len(set(scopes)) != len(scopes)
    ):
        raise RecipeError("Choose 1–20 unique bounded OAuth scopes")
    return RecipeAuthentication(
        "oauth_pkce",
        recipe_endpoint(value["authorization_endpoint"]),
        recipe_endpoint(value["token_endpoint"]),
        tuple(scopes),
    )


def pointer(value):
    printable(value, 128)
    if (
        not value.startswith("/")
        or len(value.split("/")) > 9
        or re.search(r"~(?![01])", value)
    ):
        raise RecipeError("Use a bounded JSON pointer")
    return value


def select(data, path):
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(data, dict):
            data = data[key]
        elif isinstance(data, list) and re.fullmatch(r"0|[1-9][0-9]{0,5}", key):
            data = data[int(key)]
        else:
            raise RecipeError("API response does not match the reviewed fields")
    return data


def resource_id(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", value)
        or ".." in value
    ):
        raise RecipeError("Use one exact resource ID, not a URL or path")
    return value


def query_parameter(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,31}", value
    ):
        raise RecipeError("Use one bounded pagination parameter name")
    return value


def page_cursor(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or not all(33 <= ord(c) < 127 for c in value)
    ):
        raise RecipeError("The API returned an invalid pagination cursor")
    return value


@dataclass(frozen=True)
class RecipePagination:
    cursor: str
    parameter: str
    max_pages: int


@dataclass(frozen=True)
class RecipeReconciliation:
    path: str
    operation_id: str
    status: str
    success: str


def idempotency_header(value):
    if value not in ("Idempotency-Key", "X-Idempotency-Key", "X-Request-ID"):
        raise RecipeError("Choose a supported operation ID header")
    return value


@dataclass(frozen=True)
class RecipeTool:
    name: str
    description: str
    path: str
    response: tuple
    pagination: RecipePagination | None = None
    effect: str = "read"
    destination_kind: str = ""
    request: tuple = ()
    idempotency_header: str = ""
    reconciliation: RecipeReconciliation | None = None
    request_template: JsonTemplate | None = None

    @property
    def method(self):
        return "GET" if self.effect == "read" else "POST"

    @property
    def destination_type(self):
        return self.destination_kind or (
            "resource_id" if "{destination}" in self.path else "account"
        )

    def destination(self, value):
        if self.destination_type == "email":
            from services.google_connector import email_destination

            email_destination(value)
            return value
        if self.destination_type == "resource_id":
            return resource_id(value)
        if value != "account":
            raise RecipeError("Use destination account for this fixed collection path")
        return value


@dataclass(frozen=True)
class ConnectorRecipe:
    sha256: str
    raw: bytes
    name: str
    origin: str
    identity_path: str
    subject_pointer: str
    label_pointer: str
    tools: tuple
    authentication: RecipeAuthentication = RecipeAuthentication()
    version: int = 1

    @property
    def provider(self):
        return "recipe_" + self.sha256[:20]

    def metadata(self):
        return {
            "version": self.version,
            "provider": self.provider,
            "sha256": self.sha256,
            "name": self.name,
            "origin": self.origin,
            "authentication": self.authentication.kind,
            "identity": {
                "path": self.identity_path,
                "subject": self.subject_pointer,
                "label": self.label_pointer,
            },
            "oauth": (
                {
                    "authorization_endpoint": self.authentication.authorization_endpoint,
                    "token_endpoint": self.authentication.token_endpoint,
                    "scopes": list(self.authentication.scopes),
                }
                if self.authentication.kind == "oauth_pkce"
                else None
            ),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "effect": t.effect,
                    "method": t.method,
                    "path": t.path,
                    "destination": t.destination_type,
                    "request": t.request_template.source()
                    if t.request_template
                    else dict(t.request),
                    "request_format": "json_template"
                    if t.request_template
                    else "flat_bindings",
                    "idempotency_header": t.idempotency_header or None,
                    "requires_payload_review": t.effect != "read",
                    "reconciliation": (
                        {
                            "path": t.reconciliation.path,
                            "operation_id": t.reconciliation.operation_id,
                            "status": t.reconciliation.status,
                            "success": t.reconciliation.success,
                        }
                        if t.reconciliation
                        else None
                    ),
                    "pagination": (
                        {
                            "cursor": t.pagination.cursor,
                            "parameter": t.pagination.parameter,
                            "max_pages": t.pagination.max_pages,
                        }
                        if t.pagination
                        else None
                    ),
                    "response_fields": [
                        {"label": k, "pointer": v} for k, v in t.response
                    ],
                }
                for t in self.tools
            ],
        }


def parse_recipe(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_RECIPE_BYTES:
        raise RecipeError("Connector recipes must be 1–16384 UTF-8 JSON bytes")
    try:
        data = json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                RecipeError("Nonfinite recipe number")
            ),
        )
        fields(
            data, ("version", "name", "origin", "authentication", "identity", "tools")
        )
        if type(data["version"]) is not int or data["version"] not in (1, 2, 3):
            raise RecipeError("Unsupported connector recipe version")
        auth = authentication(data["authentication"])
        identity = data["identity"]
        fields(identity, ("path", "subject", "label"))
        if not isinstance(data["tools"], list) or not 1 <= len(data["tools"]) <= 10:
            raise RecipeError("Provide 1–10 tools")
        tools, names = [], set()
        for item in data["tools"]:
            if not isinstance(item, dict):
                raise RecipeError("Provide tool objects")
            writing = data["version"] >= 2 and item.get("method") == "POST"
            fields(
                item,
                (
                    "name",
                    "description",
                    "method",
                    "path",
                    "response",
                    *(["pagination"] if "pagination" in item else []),
                    *(["effect", "destination", "request"] if writing else []),
                    *(
                        ["idempotency_header"]
                        if writing and "idempotency_header" in item
                        else []
                    ),
                    *(
                        ["reconciliation"]
                        if writing and "reconciliation" in item
                        else []
                    ),
                ),
            )
            name = connector_name(item["name"])
            if name in names or item["method"] not in (
                ("GET", "POST") if writing else ("GET",)
            ):
                raise RecipeError(
                    "Recipe tools need unique names and supported requests"
                )
            names.add(name)
            path = request_path(item["path"], destination=True)
            effect, destination_kind, bindings, header, reconciliation = (
                "read",
                "",
                (),
                "",
                None,
            )
            template = None
            if writing:
                effect, destination_kind = item["effect"], item["destination"]
                if (
                    effect not in ("write", "send")
                    or destination_kind not in ("account", "resource_id", "email")
                    or "pagination" in item
                ):
                    raise RecipeError(
                        "POST tools need create/write or send, an exact destination, and no pagination"
                    )
                if (effect == "send") != (destination_kind == "email") or (
                    "{destination}" in path
                ) != (destination_kind == "resource_id"):
                    raise RecipeError(
                        "Send needs one email recipient; resource writes need a destination path segment"
                    )
                value = item["request"]
                if data["version"] == 3:
                    template = json_template(value)
                    sources = template.bindings()
                else:
                    if not isinstance(value, dict) or not 1 <= len(value) <= 10:
                        raise RecipeError("Choose 1–10 flat JSON request bindings")
                    for key, source in value.items():
                        query_parameter(key)
                        if source not in (
                            "artifact.title",
                            "artifact.content",
                            "destination",
                            "operation_id",
                        ):
                            raise RecipeError(
                                "Request fields must bind draft title/content, destination, or operation ID"
                            )
                    bindings = tuple(value.items())
                    sources = set(value.values())
                if (
                    "artifact.content" not in sources
                    or effect == "send"
                    and "destination" not in sources
                ):
                    raise RecipeError(
                        "Write the reviewed draft content; sends must bind the recipient"
                    )
                if "idempotency_header" in item:
                    header = idempotency_header(item["idempotency_header"])
                if "reconciliation" in item:
                    r = item["reconciliation"]
                    fields(r, ("path", "operation_id", "status", "success"))
                    printable(r["path"], 512)
                    if (
                        r["path"].count("{operation_id}") != 1
                        or "{destination}" in r["path"]
                    ):
                        raise RecipeError(
                            "Reconciliation needs one complete {operation_id} segment"
                        )
                    if not header and "operation_id" not in sources:
                        raise RecipeError(
                            "Reconciliation needs an operation ID in the request"
                        )
                    rpath = request_path(
                        r["path"].replace("{operation_id}", "{destination}"),
                        destination=True,
                    )
                    if "{destination}" not in rpath:
                        raise RecipeError(
                            "Reconciliation needs one complete {operation_id} segment"
                        )
                    reconciliation = RecipeReconciliation(
                        rpath.replace("{destination}", "{operation_id}"),
                        pointer(r["operation_id"]),
                        pointer(r["status"]),
                        printable(r["success"], 80),
                    )
            pagination = None
            if "pagination" in item:
                value = item["pagination"]
                fields(value, ("cursor", "parameter", "max_pages"))
                if (
                    "{destination}" in path
                    or type(value["max_pages"]) is not int
                    or not 1 <= value["max_pages"] <= 5
                ):
                    raise RecipeError(
                        "Pagination needs a fixed collection path and 1–5 pages"
                    )
                pagination = RecipePagination(
                    pointer(value["cursor"]),
                    query_parameter(value["parameter"]),
                    value["max_pages"],
                )
            if (
                not isinstance(item["response"], list)
                or not 1 <= len(item["response"]) <= 10
            ):
                raise RecipeError("Choose 1–10 response fields per tool")
            outputs = []
            for output in item["response"]:
                fields(output, ("label", "pointer"))
                label = printable(output["label"], 80)
                if pagination and label == "_pagination":
                    raise RecipeError("_pagination is reserved for collection progress")
                if label in {p[0] for p in outputs}:
                    raise RecipeError("Response labels must be unique")
                outputs.append((label, pointer(output["pointer"])))
            tools.append(
                RecipeTool(
                    name,
                    printable(item["description"], 300),
                    path,
                    tuple(outputs),
                    pagination,
                    effect,
                    destination_kind,
                    bindings,
                    header,
                    reconciliation,
                    template,
                )
            )
        return ConnectorRecipe(
            hashlib.sha256(raw).hexdigest(),
            raw,
            printable(data["name"], 80),
            recipe_origin(data["origin"]),
            request_path(identity["path"]),
            pointer(identity["subject"]),
            pointer(identity["label"]),
            tuple(tools),
            auth,
            data["version"],
        )
    except (KeyError, TypeError, UnicodeError, RecursionError, ValueError) as error:
        if isinstance(error, RecipeError):
            raise
        raise RecipeError("Invalid connector recipe") from None


class RecipeStore:
    """User-reviewed installs survive tasks; each version gets immutable identity."""

    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()
        self._entries = {}
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return
        try:
            if len(raw) > 2 * 1024 * 1024:
                raise RecipeError("Recipe catalog exceeds its limit")
            data = json.loads(raw, object_pairs_hook=unique_object)
            fields(data, ("version", "recipes"))
            if (
                type(data["version"]) is not int
                or data["version"] != 1
                or not isinstance(data["recipes"], list)
                or len(data["recipes"]) > 50
            ):
                raise RecipeError("Invalid recipe catalog")
            for entry in data["recipes"]:
                fields(entry, ("sha256", "recipe", "enabled"))
                if not isinstance(entry["recipe"], str):
                    raise RecipeError("Invalid recipe catalog source")
                recipe = parse_recipe(entry["recipe"].encode("utf-8"))
                if (
                    recipe.sha256 != entry["sha256"]
                    or type(entry["enabled"]) is not bool
                    or recipe.provider in self._entries
                ):
                    raise RecipeError("Recipe catalog integrity failed")
                self._entries[recipe.provider] = (recipe, entry["enabled"])
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            raise RecipeError(
                "Connector recipe catalog is invalid; recipe providers are disabled"
            ) from None

    def list(self):
        with self.lock:
            return tuple(self._entries.values())

    def active(self, recipe):
        with self.lock:
            if self._entries.get(recipe.provider) != (recipe, True):
                raise RecipeError("This connector recipe was removed or changed")

    def _save(self, entries):
        payload = {
            "version": 1,
            "recipes": [
                {
                    "sha256": r.sha256,
                    "recipe": r.raw.decode("utf-8"),
                    "enabled": enabled,
                }
                for r, enabled in entries.values()
            ],
        }
        if len(json.dumps(payload, indent=2).encode("utf-8")) > 2 * 1024 * 1024:
            raise RecipeError("Recipe catalog exceeds its limit")
        save_json(self.path, payload)
        self.path.chmod(0o600)
        self._entries = entries

    def install(self, recipe):
        with self.lock:
            if parse_recipe(recipe.raw) != recipe:
                raise RecipeError("Recipe object differs from its immutable source")
            if len(self._entries) >= 50 and recipe.provider not in self._entries:
                raise RecipeError("At most 50 retained recipe versions are supported")
            previous = self._entries.get(recipe.provider)
            if previous and previous[0] != recipe:
                raise RecipeError("Recipe identity collision")
            self._save({**self._entries, recipe.provider: (recipe, True)})

    def disable(self, provider):
        with self.lock:
            recipe, _ = self._entries[provider]
            self._save({**self._entries, provider: (recipe, False)})
