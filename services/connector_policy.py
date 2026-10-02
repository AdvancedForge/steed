"""Exact app permissions; trusted adapter configuration defines each effect."""

import re
from dataclasses import dataclass

EFFECTS = frozenset({"read", "write", "send", "delete", "spend"})


def connector_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise ValueError("Use a lowercase connector/tool identifier")
    return value


def destination_name(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 320
        or value != value.strip()
        or not all(c.isprintable() for c in value)
        or "*" in value
    ):
        raise ValueError(
            "Provide an exact printable app destination of at most 320 characters"
        )
    return value


@dataclass(frozen=True)
class ConnectorPermission:
    connector: str
    tool: str
    effect: str
    destination: str


def connector_permissions(values):
    if not isinstance(values, (list, tuple)) or len(values) > 20:
        raise ValueError("Provide at most 20 exact app permissions")
    result = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "connector",
            "tool",
            "effect",
            "destination",
        }:
            raise ValueError(
                "An app permission needs connector, tool, effect and destination"
            )
        if not isinstance(value["effect"], str) or value["effect"] not in EFFECTS:
            raise ValueError("Unsupported app effect")
        permission = ConnectorPermission(
            connector_name(value["connector"]),
            connector_name(value["tool"]),
            value["effect"],
            destination_name(value["destination"]),
        )
        if permission in result:
            raise ValueError("Duplicate app permission")
        result.append(permission)
    return tuple(result)
