"""Strict, immutable connector recipes; data never imports executable code."""

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from services.connector_policy import connector_name
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
        and value.count("{destination}") != 1
    ):
        raise RecipeError(
            "Use a fixed API path; tool paths need one {destination} segment"
        )
    return value


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


@dataclass(frozen=True)
class RecipeTool:
    name: str
    description: str
    path: str
    response: tuple


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

    @property
    def provider(self):
        return "recipe_" + self.sha256[:20]

    def metadata(self):
        return {
            "provider": self.provider,
            "sha256": self.sha256,
            "name": self.name,
            "origin": self.origin,
            "authentication": "bearer",
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "effect": "read",
                    "method": "GET",
                    "path": t.path,
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
        if type(data["version"]) is not int or data["version"] != 1:
            raise RecipeError("Unsupported connector recipe version")
        fields(data["authentication"], ("type",))
        if data["authentication"]["type"] != "bearer":
            raise RecipeError("This recipe version supports user-entered bearer tokens")
        identity = data["identity"]
        fields(identity, ("path", "subject", "label"))
        if not isinstance(data["tools"], list) or not 1 <= len(data["tools"]) <= 10:
            raise RecipeError("Provide 1–10 read tools")
        tools, names = [], set()
        for item in data["tools"]:
            fields(item, ("name", "description", "method", "path", "response"))
            name = connector_name(item["name"])
            if name in names or item["method"] != "GET":
                raise RecipeError(
                    "Recipe tools need unique names and reviewed GET requests"
                )
            names.add(name)
            if (
                not isinstance(item["response"], list)
                or not 1 <= len(item["response"]) <= 10
            ):
                raise RecipeError("Choose 1–10 response fields per tool")
            outputs = []
            for output in item["response"]:
                fields(output, ("label", "pointer"))
                label = printable(output["label"], 80)
                if label in {p[0] for p in outputs}:
                    raise RecipeError("Response labels must be unique")
                outputs.append((label, pointer(output["pointer"])))
            tools.append(
                RecipeTool(
                    name,
                    printable(item["description"], 300),
                    request_path(item["path"], destination=True),
                    tuple(outputs),
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
