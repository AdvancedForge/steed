"""Immutable, bounded JSON templates; bindings substitute strings without parsing."""

import math
from dataclasses import dataclass

MAX_TEMPLATE_DEPTH = 8
MAX_TEMPLATE_NODES = 100
MAX_TEMPLATE_ITEMS = 20
BINDING_SOURCES = frozenset(
    ("artifact.title", "artifact.content", "destination", "operation_id")
)


@dataclass(frozen=True)
class JsonTemplate:
    kind: str
    value: object

    def materialize(self, sources):
        if self.kind == "binding":
            return sources[self.value]
        if self.kind == "object":
            return {key: child.materialize(sources) for key, child in self.value}
        if self.kind == "array":
            return [child.materialize(sources) for child in self.value]
        return self.value

    def source(self):
        if self.kind == "binding":
            return {"$bind": self.value}
        return self.materialize_source()

    def materialize_source(self):
        if self.kind == "object":
            return {key: child.source() for key, child in self.value}
        if self.kind == "array":
            return [child.source() for child in self.value]
        return self.value

    def bindings(self):
        if self.kind == "binding":
            return frozenset((self.value,))
        if self.kind in {"object", "array"}:
            children = (
                (child for _, child in self.value)
                if self.kind == "object"
                else self.value
            )
            return frozenset().union(*(child.bindings() for child in children))
        return frozenset()


def json_template(value, *, bindings=True):
    """Compile recipe data or validate a materialized request before HTTP entry."""
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_TEMPLATE_DEPTH or nodes > MAX_TEMPLATE_NODES:
            raise ValueError("JSON template exceeds its depth or node limit")
        if isinstance(item, dict):
            if bindings and "$bind" in item:
                if set(item) != {"$bind"} or item["$bind"] not in BINDING_SOURCES:
                    raise ValueError("Use one supported explicit JSON binding")
                return JsonTemplate("binding", item["$bind"])
            if len(item) > MAX_TEMPLATE_ITEMS or any(
                not isinstance(key, str)
                or not 1 <= len(key) <= 80
                or not key.isprintable()
                for key in item
            ):
                raise ValueError("Use bounded JSON object fields")
            return JsonTemplate(
                "object",
                tuple((key, visit(child, depth + 1)) for key, child in item.items()),
            )
        if isinstance(item, list):
            if len(item) > MAX_TEMPLATE_ITEMS:
                raise ValueError("JSON array exceeds its item limit")
            return JsonTemplate(
                "array", tuple(visit(child, depth + 1) for child in item)
            )
        if item is None or type(item) is bool:
            return JsonTemplate("literal", item)
        if type(item) is int and abs(item) <= 2**53 - 1:
            return JsonTemplate("literal", item)
        if type(item) is float and math.isfinite(item):
            return JsonTemplate("literal", item)
        if isinstance(item, str) and "\x00" not in item:
            return JsonTemplate("literal", item)
        raise ValueError("Use JSON literals and supported explicit string bindings")

    if not isinstance(value, dict) or not 1 <= len(value) <= 10:
        raise ValueError("Use 1–10 top-level JSON request fields")
    return visit(value, 1)
