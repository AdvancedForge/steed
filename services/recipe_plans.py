"""Compile bounded sequential creates/sends and materialize one approved step."""

import json
import re
from dataclasses import replace

from services.recipe_templates import BINDING_SOURCES, json_template

MAX_PLAN_STEPS = 5
STEP_PATH = re.compile(r"\{(step\.[a-z][a-z0-9_-]{0,63}\.[a-z][a-z0-9_-]{0,63})\}")


def compile_plan(first, following, source):
    from services.connector_recipes import (
        RecipeError,
        fields,
        parse_recipe,
        request_path,
    )

    if (
        not isinstance(following, list)
        or not 1 <= len(following) < MAX_PLAN_STEPS
        or first.reconciliation
    ):
        raise RecipeError("Use 2–5 reviewed POST steps without reconciliation")
    steps, names = [first], {first.name}
    available = set(BINDING_SOURCES)
    for item in following:
        previous = steps[-1]
        available.update(
            f"step.{previous.name}.{label}"
            for label, _ in previous.response
            if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", label)
        )
        if not isinstance(item, dict):
            raise RecipeError("Use declarative POST step objects")
        fields(
            item,
            (
                "name",
                "description",
                "method",
                "path",
                "request",
                "response",
                *(["idempotency_header"] if "idempotency_header" in item else []),
            ),
        )
        if item["method"] != "POST" or item["name"] in names:
            raise RecipeError("Plan steps need unique names and POST requests")
        template = json_template(item["request"], binding_sources=frozenset(available))
        path = item["path"]
        if not isinstance(path, str):
            raise RecipeError("Use a reviewed plan path")
        variables = STEP_PATH.findall(path)
        if (
            any(value not in available for value in variables)
            or len(variables) + path.count("{destination}") > 1
        ):
            raise RecipeError(
                "Bind at most one complete path segment from an earlier selected field"
            )
        normalized = STEP_PATH.sub("resource", path)
        request_path(normalized, destination=True)
        if variables and "{" + variables[0] + "}" not in path.split("/"):
            raise RecipeError("Plan output must occupy a complete path segment")
        if "{destination}" in path and first.destination_type != "resource_id":
            raise RecipeError("This plan does not have a resource destination")
        # Reuse the single-step effect, destination, response and header validators.
        # External bindings become harmless draft-title bindings during this pass;
        # real content/recipient bindings must still be present independently.
        placeholder = {
            key: "artifact.title" for key in available if key.startswith("step.")
        }

        def normal(node, placeholder=placeholder):
            if isinstance(node, dict):
                if set(node) == {"$bind"} and node["$bind"] in placeholder:
                    return {"$bind": "artifact.title"}
                return {k: normal(v) for k, v in node.items()}
            if isinstance(node, list):
                return [normal(v) for v in node]
            return node

        packet = {
            **item,
            "effect": first.effect,
            "destination": "email"
            if first.effect == "send"
            else "resource_id"
            if "{destination}" in normalized
            else "account",
            "path": normalized,
            "request": normal(item["request"]),
        }
        compiled = parse_recipe(
            json.dumps({**source, "version": 3, "tools": [packet]}).encode()
        ).tools[0]
        steps.append(
            replace(
                compiled,
                path=path,
                request_template=template,
                destination_kind=first.destination_type,
            )
        )
        names.add(compiled.name)
    return tuple(steps)


def transport_path(path):
    return STEP_PATH.sub("{destination}", path)


class RecipePlan:
    def __init__(self, connector, tool):
        self.connector, self.tool, self.steps = connector, tool, tool.steps

    def materialize(self, index, parent_id, values):
        from services.connector_recipes import resource_id
        from services.connectors import ConnectorTool
        from services.recipe_writes import RecipeWrites

        selected = self.steps[index]
        path = STEP_PATH.sub(lambda match: resource_id(values[match[1]]), selected.path)
        step = replace(
            selected,
            name=self.tool.name,
            path=path,
            request_template=selected.request_template.resolve(values),
            plan_context=(
                ("plan_request_id", parent_id),
                ("step", index + 1),
                ("steps", len(self.steps)),
                ("step_name", selected.name),
            ),
        )
        writes = RecipeWrites(self.connector)
        return ConnectorTool(
            self.connector.account.connector,
            self.tool.name,
            self.tool.effect,
            f"Step {index + 1}/{len(self.steps)}: {selected.description}"[:512],
            True,
            lambda request, cancelled: writes.execute(step, request, cancelled),
            validate_destination=self.tool.destination,
            prepare_review=lambda request, cancelled: writes.prepare(
                step, request, cancelled
            ),
            review_kind="payload",
        )
