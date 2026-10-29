"""Strict JSON/text exchanges for human-mediated chatbot tool use."""

import json
import math
import re
from dataclasses import dataclass, field

from services.agent_tools import REQUEST_LIMIT, TOOLS

PROTOCOL_VERSION = 1
MAX_CALLS = 4
MAX_EXCHANGES = 32
OUTBOUND_LIMIT = 1024 * 1024
RESULT_BUDGET = 600 * 1024
# Lifecycle belongs to Steed. Pixel input needs an image-capable transport.
UNAVAILABLE = frozenset(
    {
        "task.start",
        "task.finish",
        "conversation.reply",
        # Steed delivers relay turns itself; a blocking wait has no use there.
        "conversation.wait",
        "page.capture",
        "page.click",
        "page.type",
        "page.key",
    }
)
RELAY_TOOLS = {t.name: t for t in TOOLS if t.command not in UNAVAILABLE}


class RelayError(ValueError):
    """Maintained diagnostics only; raw parser/argument exceptions stay private."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RelayError("duplicate_key", "Duplicate JSON keys are not allowed.")
        result[key] = value
    return result


def _bounded(value, depth=0, budget=None):
    budget = [4096] if budget is None else budget
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise RelayError("request_too_complex", "Use a smaller, shallower request.")
    if isinstance(value, str):
        value.encode("utf-8")
        if "\x00" in value:
            raise RelayError("invalid_text", "NUL characters are not allowed.")
    elif isinstance(value, dict):
        for key, item in value.items():
            key.encode("utf-8")
            if len(key) > 128 or "\x00" in key:
                raise RelayError("invalid_key", "JSON object keys must be short text.")
            _bounded(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _bounded(item, depth + 1, budget)
    elif isinstance(value, float) and not math.isfinite(value):
        raise RelayError("invalid_number", "JSON numbers must be finite.")


def _schema(rule, value):
    kind = rule.get("type")
    valid = {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(kind, True)
    if not valid or "enum" in rule and value not in rule["enum"]:
        raise RelayError(
            "invalid_arguments", "Arguments do not match the published tool schema."
        )
    if kind == "object":
        properties = rule.get("properties", {})
        if (
            set(rule.get("required", ())) - value.keys()
            or rule.get("additionalProperties") is False
            and value.keys() - properties.keys()
        ):
            raise RelayError(
                "invalid_arguments",
                "A required argument is missing or an unknown field was supplied.",
            )
        for key, item in value.items():
            if key in properties:
                _schema(properties[key], item)
    elif kind == "array":
        if len(value) > rule.get("maxItems", 4096):
            raise RelayError(
                "invalid_arguments", "An argument list exceeds its published bound."
            )
        for item in value:
            _schema(rule.get("items", {}), item)
    elif kind == "string":
        if (
            not rule.get("minLength", 0)
            <= len(value)
            <= rule.get("maxLength", REQUEST_LIMIT)
        ):
            raise RelayError(
                "invalid_arguments", "A text argument exceeds its published bound."
            )
    elif kind == "integer" and not rule.get("minimum", -(10**100)) <= value <= rule.get(
        "maximum", 10**100
    ):
        raise RelayError("invalid_arguments", "A number exceeds its published bound.")


@dataclass(frozen=True)
class RelayCall:
    call_id: str
    tool: str
    arguments_json: str = field(repr=False)

    @property
    def arguments(self):
        return json.loads(self.arguments_json)


@dataclass(frozen=True)
class RelayProposal:
    exchange_id: str
    calls: tuple[RelayCall, ...]
    final: str | None
    canonical: str = field(repr=False)


def parse_response(source, expected_exchange, used_ids=()):
    try:
        if not isinstance(source, str) or len(source.encode("utf-8")) > REQUEST_LIMIT:
            raise RelayError(
                "request_too_large", "Response must be UTF-8 JSON of at most 64 KiB."
            )
        source = source.strip()
        if source.startswith("```json\n") and source.endswith("\n```"):
            source = source[8:-4].strip()
        value = json.loads(source, object_pairs_hook=_object)
        _bounded(value)
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value["version"] != PROTOCOL_VERSION
        ):
            raise RelayError("invalid_envelope", "Use a version 1 response object.")
        if not expected_exchange or value.get("exchange_id") != expected_exchange:
            raise RelayError(
                "stale_exchange",
                "This response belongs to an old or different exchange. Give the chatbot the current outbound message.",
            )
        final = value.get("final")
        if "final" in value:
            if (
                set(value) != {"version", "exchange_id", "final"}
                or not isinstance(final, str)
                or not final.strip()
                or len(final.encode("utf-8")) > 8192
                or any(ord(c) < 32 and c not in "\r\n\t" for c in final)
            ):
                raise RelayError(
                    "invalid_final",
                    "Use a final answer of at most 8 KiB, without tool calls.",
                )
            return RelayProposal(expected_exchange, (), final, encode(value))
        if (
            set(value) != {"version", "exchange_id", "calls"}
            or not isinstance(value.get("calls"), list)
            or not 1 <= len(value["calls"]) <= MAX_CALLS
        ):
            raise RelayError(
                "invalid_calls", "Use one to four tool calls, or a final answer."
            )
        calls, seen = [], set(used_ids)
        for call in value["calls"]:
            if not isinstance(call, dict) or set(call) != {"id", "tool", "arguments"}:
                raise RelayError(
                    "invalid_call", "Each call needs exactly id, tool and arguments."
                )
            identity = call["id"]
            if (
                not isinstance(identity, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity) is None
                or identity in seen
            ):
                raise RelayError(
                    "repeated_call",
                    "Call IDs must be new, unique short identifiers. Previously accepted calls cannot be replayed.",
                )
            seen.add(identity)
            spec = (
                RELAY_TOOLS.get(call["tool"]) if isinstance(call["tool"], str) else None
            )
            if spec is None:
                raise RelayError(
                    "unknown_tool",
                    "Choose a tool from this relay's published catalogue. Lifecycle management and pixel tools are unavailable in this text relay.",
                )
            _schema(spec.input_schema, call["arguments"])
            spec.validate(call["arguments"])
            calls.append(RelayCall(identity, spec.name, encode(call["arguments"])))
        return RelayProposal(expected_exchange, tuple(calls), None, encode(value))
    except RelayError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise RelayError(
            "invalid_json",
            "Use one complete JSON response, without prose, duplicate keys or invalid values.",
        ) from None


def catalogue():
    return [
        {"name": t.name, "description": t.description, "parameters": t.input_schema}
        for t in RELAY_TOOLS.values()
    ]
