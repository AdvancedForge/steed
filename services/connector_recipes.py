"""Strict, immutable connector recipes; data never imports executable code."""

import hashlib
import json
import re
import threading
from dataclasses import dataclass, replace
from datetime import date
from urllib.parse import urlsplit

from browser.utils import save_json
from services.connector_policy import connector_name
from services.recipe_templates import JsonTemplate, json_template
from services.task_workspace import unique_object

MAX_RECIPE_BYTES = 16384
MAX_QUERY_INPUTS = 8
MAX_INPUT_STRING = 512
MAX_GRAPHQL_DOCUMENT = 4096
MAX_GRAPHQL_VARIABLES = 8
STATIC_HEADER_FORMATS = {
    "Notion-Version": "YYYY-MM-DD",
    "X-GitHub-Api-Version": "YYYY-MM-DD",
    "Zotero-API-Version": "integer 1–99",
    "OCS-APIRequest": "true",
}

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

RECIPE_PLAN_EXAMPLE = {
    **RECIPE_NESTED_EXAMPLE,
    "version": 4,
    "name": "Example Sequential Notes",
    "tools": [
        {
            **{
                key: value
                for key, value in RECIPE_NESTED_EXAMPLE["tools"][0].items()
                if key != "reconciliation"
            },
            "plan": [
                {
                    "name": "append_note",
                    "description": "Append reviewed text to the newly created note",
                    "method": "POST",
                    "path": "/notes/{step.create_note.id}/comments",
                    "request": {
                        "note_id": {"$bind": "step.create_note.id"},
                        "comment": {"$bind": "artifact.content"},
                        "operation_id": {"$bind": "operation_id"},
                    },
                    "response": [{"label": "id", "pointer": "/id"}],
                }
            ],
        }
    ],
}

RECIPE_HEADERS_EXAMPLE = {
    **RECIPE_PLAN_EXAMPLE,
    "version": 5,
    "name": "Example Versioned Notes",
    "request_headers": {"Notion-Version": "2025-09-03"},
}

RECIPE_IDENTITY_EXAMPLE = {
    **RECIPE_HEADERS_EXAMPLE,
    "version": 6,
    "name": "Example Nullable Identity Notes",
    "identity": {
        **RECIPE_HEADERS_EXAMPLE["identity"],
        "label": "/name",
        "label_alternatives": ["/email", "/id"],
    },
}

RECIPE_QUERY_EXAMPLE = {
    **RECIPE_IDENTITY_EXAMPLE,
    "version": 7,
    "name": "Example Search Notes",
    "tools": [
        {
            "name": "search_notes",
            "description": "Search this account's notes with reviewed inputs",
            "method": "POST",
            "effect": "read",
            "path": "/notes/search",
            "inputs": {
                "query": {"type": "string", "max_length": 200},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
                "archived": {"type": "boolean", "default": False},
            },
            "request": {
                "text": {"$bind": "input.query"},
                "limit": {"$bind": "input.limit"},
                "include_archived": {"$bind": "input.archived"},
                "cursor": None,
            },
            "response": [{"label": "notes", "pointer": "/items"}],
            "pagination": {
                "cursor": "/cursor",
                "parameter": "cursor",
                "max_pages": 3,
                "location": "body",
            },
            "errors": {"pointer": "/error", "code": "/reason", "message": "/summary"},
        },
        {
            "name": "read_note_graphql",
            "description": "Read one reviewed GraphQL note document",
            "method": "POST",
            "effect": "read",
            "path": "/graphql",
            "destination": "resource_id",
            "query": {
                "operation": "NoteById",
                "document": (
                    "query NoteById($id: ID!) {\n"
                    "  note(id: $id) {\n"
                    "    id\n"
                    "    title\n"
                    "    body\n"
                    "  }\n"
                    "}"
                ),
                "variables": {"id": {"$bind": "destination"}},
                "data_pointer": "/data",
                "errors_pointer": "/errors",
            },
            "response": [{"label": "note", "pointer": "/note"}],
        },
    ],
}


RECIPE_CONTINUATION_EXAMPLE = {
    **RECIPE_QUERY_EXAMPLE,
    "version": 8,
    "name": "Example Continued Notes",
    "tools": [
        {
            **RECIPE_QUERY_EXAMPLE["tools"][0],
            "pagination": {
                **RECIPE_QUERY_EXAMPLE["tools"][0]["pagination"],
                "has_more": "/has_more",
                "continuation": {
                    "path": "/notes/search/continue",
                    "request": {"cursor": {"$bind": "cursor"}},
                },
            },
            "success": {"pointer": "/ok", "value": True, "required": True},
        },
        RECIPE_QUERY_EXAMPLE["tools"][1],
    ],
}


class RecipeError(ValueError):
    pass


def static_request_headers(value):
    """Compile a bounded, non-secret header set; names use canonical casing."""
    if not isinstance(value, dict) or not 1 <= len(value) <= 4:
        raise RecipeError("Provide 1–4 supported non-secret request headers")
    for name, item in value.items():
        if name not in STATIC_HEADER_FORMATS or not isinstance(item, str):
            raise RecipeError("Use a supported non-secret header and string value")
        if STATIC_HEADER_FORMATS[name] == "YYYY-MM-DD":
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item):
                raise RecipeError("Use a canonical API version date")
            try:
                date.fromisoformat(item)
            except ValueError:
                raise RecipeError("Use a valid API version date") from None
        elif name == "Zotero-API-Version":
            if not re.fullmatch(r"[1-9][0-9]?", item):
                raise RecipeError("Use an API version from 1 to 99")
        elif item != "true":
            raise RecipeError("Use true for the reviewed OCS request flag")
    return tuple(sorted(value.items()))


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
class RecipeContinuation:
    path: str
    request: JsonTemplate


@dataclass(frozen=True)
class RecipeSuccess:
    pointer: str
    value: bool
    required: bool


def success_rule(value):
    fields(value, ("pointer", "value", "required"))
    if type(value["value"]) is not bool or type(value["required"]) is not bool:
        raise RecipeError(
            "Success rules require explicit boolean value and requiredness"
        )
    return RecipeSuccess(pointer(value["pointer"]), value["value"], value["required"])


@dataclass(frozen=True)
class RecipePagination:
    cursor: str
    parameter: str
    max_pages: int
    location: str = "query"
    has_more: str = ""
    continuation: RecipeContinuation | None = None


@dataclass(frozen=True)
class RecipeInput:
    """One reviewed declarative read input; values arrive per call, never code."""

    name: str
    kind: str
    maximum_length: int = 0
    minimum: int = 0
    maximum: int = 0
    default: object = None
    has_default: bool = False

    def coerce(self, value):
        """Accept only this input's declared type and bounds."""
        if self.kind == "string":
            if (
                not isinstance(value, str)
                or len(value) > self.maximum_length
                or value
                and not value.isprintable()
            ):
                raise RecipeError(f"{self.name} must be printable text")
            return value
        if self.kind == "integer":
            if type(value) is not int or not self.minimum <= value <= self.maximum:
                raise RecipeError(f"{self.name} must be within its reviewed range")
            return value
        if type(value) is not bool:
            raise RecipeError(f"{self.name} must be true or false")
        return value


@dataclass(frozen=True)
class RecipeErrors:
    """Application-level error contract; an HTTP 200 failure is reported, not hidden."""

    pointer: str
    code: str
    message: str


@dataclass(frozen=True)
class RecipeQuery:
    """One fixed reviewed GraphQL read; no variables document is agent-authored."""

    operation: str
    document: str
    variables: JsonTemplate
    data_pointer: str
    errors_pointer: str


@dataclass(frozen=True)
class RecipeReconciliation:
    path: str
    operation_id: str
    status: str
    success: str


INPUT_TYPES = ("boolean", "integer", "string")


def input_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value):
        raise RecipeError("Use a lowercase bounded input name")
    return value


def recipe_inputs(value):
    """Compile 0-8 bounded typed inputs; every declared input has exact semantics."""
    if value is None:
        return ()
    if not isinstance(value, dict) or len(value) > MAX_QUERY_INPUTS:
        raise RecipeError(f"Declare 1-{MAX_QUERY_INPUTS} typed read inputs")
    compiled = []
    for name, rule in value.items():
        input_name(name)
        if not isinstance(rule, dict) or rule.get("type") not in INPUT_TYPES:
            raise RecipeError("Each read input needs a string, integer or boolean type")
        kind = rule["type"]
        optional = rule.get("default", ...) is not ...
        fields(
            rule,
            (
                "type",
                "default",
                *(("max_length",) if kind == "string" else ()),
                *(("minimum", "maximum") if kind == "integer" else ()),
            )
            if optional
            else (
                "type",
                *(("max_length",) if kind == "string" else ()),
                *(("minimum", "maximum") if kind == "integer" else ()),
            ),
        )
        compiled_input = RecipeInput(name, kind)
        if kind == "string":
            length = rule["max_length"]
            if type(length) is not int or not 1 <= length <= MAX_INPUT_STRING:
                raise RecipeError("Use a reviewed string length from 1 to 512")
            compiled_input = RecipeInput(name, kind, maximum_length=length)
        elif kind == "integer":
            low, high = rule["minimum"], rule["maximum"]
            if (
                type(low) is not int
                or type(high) is not int
                or abs(low) > 2**53 - 1
                or abs(high) > 2**53 - 1
                or low > high
            ):
                raise RecipeError("Use ordered reviewed integer bounds")
            compiled_input = RecipeInput(name, kind, minimum=low, maximum=high)
        if optional:
            compiled_input = replace(
                compiled_input,
                default=compiled_input.coerce(rule["default"]),
                has_default=True,
            )
        compiled.append(compiled_input)
    return tuple(compiled)


def read_inputs(value, declared):
    """Resolve one call's inputs: unknown names fail, absent optional ones default."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or any(
        not isinstance(k, str)
        or not isinstance(v, (str, int, bool))
        or type(v) is int
        and abs(v) > 2**53 - 1
        for k, v in value.items()
    ):
        raise RecipeError("Read inputs must be a bounded object of typed values")
    names = {item.name for item in declared}
    if set(value) - names:
        raise RecipeError("Use only this tool's reviewed read inputs")
    resolved = {}
    for item in declared:
        if item.name in value:
            resolved[item.name] = item.coerce(value[item.name])
        elif item.has_default:
            resolved[item.name] = item.default
        else:
            raise RecipeError(f"{item.name} is required")
    return resolved


def application_errors(value):
    """Bounded contract for HTTP-200 application failures, reported honestly."""
    if value is None:
        return None
    fields(value, ("pointer", "code", "message"))
    return RecipeErrors(
        pointer(value["pointer"]), pointer(value["code"]), pointer(value["message"])
    )


def read_request(value, *, bindings=True, binding_sources=frozenset()):
    """Read-only APIs may use an empty object or JSON null, unlike write drafts."""
    if value is None:
        return JsonTemplate("literal", None)
    if value == {}:
        return JsonTemplate("object", ())
    return json_template(value, bindings=bindings, binding_sources=binding_sources)


def graphql_query(value):
    """Compile one fixed read-only document; the agent supplies values, not queries."""
    fields(
        value, ("operation", "document", "variables", "data_pointer", "errors_pointer")
    )
    operation = value["operation"]
    if not isinstance(operation, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]{0,63}", operation
    ):
        raise RecipeError("Use one reviewed GraphQL operation name")
    document = value["document"]
    if (
        not isinstance(document, str)
        or not 1 <= len(document.encode("utf-8")) <= MAX_GRAPHQL_DOCUMENT
        or any(not c.isprintable() and c not in " \t\n" for c in document)
    ):
        raise RecipeError("Provide a bounded reviewed GraphQL document")
    from graphql import GraphQLError, OperationType, parse
    from graphql.language import Node, OperationDefinitionNode

    try:
        tree = parse(document, no_location=True, max_tokens=1024)
    except (GraphQLError, RecursionError):
        raise RecipeError("Provide a valid bounded GraphQL query") from None
    definition = tree.definitions[0] if len(tree.definitions) == 1 else None
    if (
        not isinstance(definition, OperationDefinitionNode)
        or definition.operation != OperationType.QUERY
        or definition.name is None
        or definition.name.value != operation
    ):
        raise RecipeError(
            "Provide exactly one named query matching the reviewed operation"
        )
    pending = [(tree, 0)]
    while pending:
        node, depth = pending.pop()
        if depth > 32 or node.kind in (
            "directive",
            "fragment_spread",
            "inline_fragment",
        ):
            raise RecipeError(
                "Queries cannot use directives, fragments or deep nesting"
            )
        for key in node.keys:
            child = getattr(node, key)
            if isinstance(child, Node):
                pending.append((child, depth + 1))
            elif isinstance(child, tuple):
                pending.extend(
                    (item, depth + 1) for item in child if isinstance(item, Node)
                )
    if not isinstance(value["variables"], dict):
        raise RecipeError("GraphQL variables must be a reviewed object")
    variables = read_request(
        value["variables"], binding_sources=frozenset({"destination"})
    )
    names = [item.variable.name.value for item in definition.variable_definitions]
    if (
        len(names) > MAX_GRAPHQL_VARIABLES
        or len(names) != len(set(names))
        or set(names) != set(value["variables"])
    ):
        raise RecipeError(
            "Provide exactly the query's at most eight declared variables"
        )
    return RecipeQuery(
        operation,
        document,
        variables,
        pointer(value["data_pointer"]),
        pointer(value["errors_pointer"]),
    )


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
    steps: tuple = ()
    plan_context: tuple = ()
    inputs: tuple = ()
    errors: RecipeErrors | None = None
    query: RecipeQuery | None = None
    http_method: str = ""
    success: RecipeSuccess | None = None

    @property
    def method(self):
        return self.http_method or ("GET" if self.effect == "read" else "POST")

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
    request_headers: tuple = ()
    label_alternatives: tuple = ()
    identity_method: str = "GET"
    identity_request: JsonTemplate | None = None
    identity_success: RecipeSuccess | None = None

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
            "request_headers": dict(self.request_headers),
            "identity": {
                "path": self.identity_path,
                "subject": self.subject_pointer,
                "label": self.label_pointer,
                "label_alternatives": list(self.label_alternatives),
                "method": self.identity_method,
                "request": (
                    self.identity_request.source()
                    if self.identity_request is not None
                    else None
                ),
                "success": vars(self.identity_success)
                if self.identity_success
                else None,
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
                            "location": t.pagination.location,
                            "has_more": t.pagination.has_more,
                            "continuation": (
                                {
                                    "path": t.pagination.continuation.path,
                                    "request": t.pagination.continuation.request.source(),
                                }
                                if t.pagination.continuation
                                else None
                            ),
                        }
                        if t.pagination
                        else None
                    ),
                    "response_fields": [
                        {"label": k, "pointer": v} for k, v in t.response
                    ],
                    "inputs": [
                        {
                            "name": i.name,
                            "type": i.kind,
                            **(
                                {"max_length": i.maximum_length}
                                if i.kind == "string"
                                else {}
                            ),
                            **(
                                {"minimum": i.minimum, "maximum": i.maximum}
                                if i.kind == "integer"
                                else {}
                            ),
                            **({"default": i.default} if i.has_default else {}),
                        }
                        for i in t.inputs
                    ],
                    "success": vars(t.success) if t.success else None,
                    "errors": (
                        {
                            "pointer": t.errors.pointer,
                            "code": t.errors.code,
                            "message": t.errors.message,
                        }
                        if t.errors
                        else None
                    ),
                    "query": (
                        {
                            "operation": t.query.operation,
                            "document": t.query.document,
                            "variables": t.query.variables.source(),
                            "data_pointer": t.query.data_pointer,
                            "errors_pointer": t.query.errors_pointer,
                        }
                        if t.query
                        else None
                    ),
                    "plan": [
                        {
                            "name": step.name,
                            "description": step.description,
                            "method": step.method,
                            "path": step.path,
                            "request": step.request_template.source(),
                            "response_fields": [
                                {"label": k, "pointer": v} for k, v in step.response
                            ],
                            "idempotency_header": step.idempotency_header or None,
                        }
                        for step in t.steps
                    ]
                    or None,
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
        if not isinstance(data, dict):
            raise RecipeError("Provide a connector recipe object")
        fields(
            data,
            (
                "version",
                "name",
                "origin",
                "authentication",
                "identity",
                "tools",
                *(
                    ["request_headers"]
                    if data.get("version") == 5
                    or data.get("version") in (6, 7, 8)
                    and "request_headers" in data
                    else []
                ),
            ),
        )
        if type(data["version"]) is not int or data["version"] not in (
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
        ):
            raise RecipeError("Unsupported connector recipe version")
        headers = (
            static_request_headers(data["request_headers"])
            if data["version"] >= 5 and "request_headers" in data
            else ()
        )
        auth = authentication(data["authentication"])
        identity = data["identity"]
        fields(
            identity,
            (
                "path",
                "subject",
                "label",
                *(
                    ["label_alternatives"]
                    if data["version"] == 6
                    or data["version"] > 6
                    and "label_alternatives" in identity
                    else []
                ),
                *(
                    ["method", "request"]
                    if data["version"] >= 7 and identity.get("method") == "POST"
                    else []
                ),
                *(
                    ["success"]
                    if data["version"] >= 8 and "success" in identity
                    else []
                ),
            ),
        )
        if data["version"] >= 7 and identity.get("method") not in (None, "POST"):
            raise RecipeError("Identity reads use GET or one reviewed POST")
        identity_method = identity.get("method", "GET")
        identity_request = None
        if identity_method == "POST":
            identity_request = read_request(
                identity["request"], binding_sources=frozenset()
            )
        alternatives = ()
        if "label_alternatives" in identity:
            values = identity["label_alternatives"]
            if not isinstance(values, list) or not 1 <= len(values) <= 3:
                raise RecipeError("Provide 1–3 reviewed identity label alternatives")
            alternatives = tuple(pointer(value) for value in values)
            if (
                len(set((pointer(identity["label"]), *alternatives)))
                != len(alternatives) + 1
            ):
                raise RecipeError("Identity label pointers must be distinct")
        if not isinstance(data["tools"], list) or not 1 <= len(data["tools"]) <= 10:
            raise RecipeError("Provide 1–10 tools")
        tools, names = [], set()
        for item in data["tools"]:
            if not isinstance(item, dict):
                raise RecipeError("Provide tool objects")
            typed = data["version"] >= 7 and item.get("effect") == "read"
            writing = (
                data["version"] >= 2 and item.get("method") == "POST" and not typed
            )
            fields(
                item,
                (
                    "name",
                    "description",
                    "method",
                    "path",
                    "response",
                    *(["pagination"] if "pagination" in item else []),
                    *(
                        ["success"]
                        if data["version"] >= 8 and not writing and "success" in item
                        else []
                    ),
                    *(["effect"] if typed else []),
                    *(["request"] if typed and "query" not in item else []),
                    *(["inputs"] if typed and "inputs" in item else []),
                    *(["errors"] if typed and "errors" in item else []),
                    *(["query"] if typed and "query" in item else []),
                    *(["destination"] if typed and "destination" in item else []),
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
                    *(
                        ["plan"]
                        if data["version"] >= 4 and writing and "plan" in item
                        else []
                    ),
                ),
            )
            name = connector_name(item["name"])
            if name in names or item["method"] not in (
                ("POST",) if typed else ("GET", "POST") if writing else ("GET",)
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
            inputs, errors, query = (), None, None
            if typed:
                if item["effect"] != "read":
                    raise RecipeError("Typed request tools are read-only")
                effect, destination_kind = "read", item.get("destination", "account")
                declared = recipe_inputs(item.get("inputs"))
                if destination_kind not in ("account", "resource_id"):
                    raise RecipeError(
                        "Typed reads take an account or one exact resource ID"
                    )
                if "query" in item:
                    if declared or "request" in item:
                        raise RecipeError(
                            "Fixed GraphQL reads take only their reviewed variables"
                        )
                    if destination_kind not in ("account", "resource_id"):
                        raise RecipeError(
                            "GraphQL reads take an account or one resource ID variable"
                        )
                    query = graphql_query(item["query"])
                    bound = query.variables.bindings()
                    if (destination_kind == "resource_id") != ("destination" in bound):
                        raise RecipeError(
                            "GraphQL variables and the reviewed destination must agree"
                        )
                    if "{destination}" in path:
                        raise RecipeError(
                            "GraphQL reads bind their variables, not a path segment"
                        )
                else:
                    template = read_request(
                        item["request"],
                        binding_sources=frozenset({f"input.{i.name}" for i in declared})
                        | (
                            {"destination"}
                            if destination_kind == "resource_id"
                            else set()
                        ),
                    )
                    used = template.bindings()
                    if (
                        used
                        - {f"input.{i.name}" for i in declared}
                        - (
                            {"destination"}
                            if destination_kind == "resource_id"
                            else set()
                        )
                        or (
                            destination_kind == "resource_id"
                            and "destination" not in used
                            and "{destination}" not in path
                        )
                        or (destination_kind == "account" and "{destination}" in path)
                    ):
                        raise RecipeError(
                            "Typed read requests bind only reviewed inputs"
                        )
                errors = application_errors(item.get("errors"))
                inputs = declared
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
                if data["version"] >= 3:
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
                fields(
                    value,
                    ("cursor", "parameter", "max_pages")
                    if data["version"] < 7
                    else (
                        "cursor",
                        "parameter",
                        "max_pages",
                        "location",
                        *(
                            ["has_more"]
                            if data["version"] >= 8 and "has_more" in value
                            else []
                        ),
                        *(
                            ["continuation"]
                            if data["version"] >= 8 and "continuation" in value
                            else []
                        ),
                    ),
                )
                location = value.get("location", "query")
                if (
                    "{destination}" in path
                    and (data["version"] < 8 or not typed)
                    or type(value["max_pages"]) is not int
                    or not 1 <= value["max_pages"] <= 5
                    or location not in ("body", "query")
                    or location == "body"
                    and not (typed and template is not None)
                    or typed
                    and location != "body"
                ):
                    raise RecipeError(
                        "Pagination needs a fixed collection path and 1–5 pages"
                    )
                continuation = None
                if "continuation" in value:
                    if not typed or query is not None or location != "body":
                        raise RecipeError(
                            "Continuation bodies belong only to typed POST reads"
                        )
                    spec = value["continuation"]
                    fields(spec, ("path", "request"))
                    continuation_path = request_path(spec["path"])
                    compiled = read_request(
                        spec["request"], binding_sources=frozenset({"cursor"})
                    )
                    if "cursor" not in compiled.bindings():
                        raise RecipeError(
                            "Continuation requests must bind the returned cursor"
                        )
                    continuation = RecipeContinuation(continuation_path, compiled)
                if (
                    location == "body"
                    and continuation is None
                    and query_parameter(value["parameter"])
                    not in (template.source() or {})
                ):
                    raise RecipeError(
                        "Body cursors need a reviewed request field of that name"
                    )
                pagination = RecipePagination(
                    pointer(value["cursor"]),
                    query_parameter(value["parameter"]),
                    value["max_pages"],
                    location,
                    pointer(value["has_more"]) if "has_more" in value else "",
                    continuation,
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
                    inputs=inputs,
                    errors=errors,
                    query=query,
                    http_method=item["method"] if typed else "",
                    success=success_rule(item["success"])
                    if "success" in item
                    else None,
                )
            )
            if "plan" in item:
                from dataclasses import replace

                from services.recipe_plans import compile_plan

                tools[-1] = replace(
                    tools[-1], steps=compile_plan(tools[-1], item["plan"], data)
                )
        from services.recipe_plans import transport_path

        read_paths = [
            tool.path
            for tool in tools
            if tool.effect == "read" and tool.method == "POST"
        ]
        read_paths += [
            tool.pagination.continuation.path
            for tool in tools
            if tool.pagination and tool.pagination.continuation
        ]
        if identity_request is not None:
            read_paths.append(identity["path"])
        write_paths = [
            transport_path(step.path)
            for tool in tools
            if tool.effect != "read"
            for step in (tool.steps or (tool,))
        ]
        for read_path in read_paths:
            for write_path in write_paths:
                left, right = read_path.split("/"), write_path.split("/")
                if len(left) == len(right) and all(
                    a == b or "{destination}" in (a, b)
                    for a, b in zip(left, right, strict=True)
                ):
                    raise RecipeError(
                        "Read and write POST paths must be distinguishable"
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
            headers,
            alternatives,
            identity_method,
            identity_request,
            success_rule(identity["success"]) if "success" in identity else None,
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
