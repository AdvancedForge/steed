"""Data-only declarations for reviewed start-page changes from external agents."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass

from layout_preferences import clean_name
from services.page_style import DEFAULTS, clean_style, style_guide
from services.start_pages import CUSTOM_EXAMPLE, MODULES, clean_html, clean_settings

MAX_SOURCE_BYTES = 32768
EXAMPLE = {
    "version": 1,
    "name": "Quiet start",
    "page": "newtab",
    "mode": "builtin",
    "settings": {"modules": ["search", "bookmarks"], "feed": ""},
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate page fields are not allowed")
        result[key] = value
    return result


@dataclass(frozen=True)
class PageDefinition:
    source: str
    sha256: str
    name: str
    page: str
    document: dict

    def candidate(self, state):
        if self.page == "style":
            return clean_style({**state, **self.document["style"]})
        result = deepcopy(state)
        key = "html" if self.page == "newtab" else "welcome_html"
        result[key] = self.document.get("html", "")
        if "settings" in self.document:
            result["settings"] = clean_settings(
                {**state["settings"], **self.document["settings"]}
            )
        return result


def parse_page(source):
    try:
        if not isinstance(source, str) or len(source.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("Use a page declaration of at most 32 KB UTF-8")
        document = json.loads(source, object_pairs_hook=_object)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Use a valid UTF-8 JSON page declaration") from error
    base = {"version", "name", "page", "mode"}
    if (
        not isinstance(document, dict)
        or not base <= document.keys()
        or type(document["version"]) is not int
        or document["version"] != 1
        or not clean_name(document["name"])
        or document["page"] not in ("newtab", "welcome", "style")
        or document["mode"] not in ("builtin", "custom", "theme")
    ):
        raise ValueError("Use the published version, name, page and mode")
    if document["page"] == "style":
        if (
            document["mode"] != "theme"
            or set(document) != base | {"style"}
            or not isinstance(document["style"], dict)
            or not document["style"]
            or set(document["style"]) - set(DEFAULTS)
        ):
            raise ValueError("Use page=style, mode=theme and a nonempty style patch.")
        clean_style({**DEFAULTS, **document["style"]})
        return PageDefinition(
            source,
            hashlib.sha256(source.encode()).hexdigest(),
            clean_name(document["name"]),
            "style",
            document,
        )
    if document["mode"] == "theme":
        raise ValueError("Theme mode is only supported for page=style.")
    allowed = base | (
        {"html"}
        if document["mode"] == "custom"
        else {"settings"}
        if document["page"] == "newtab"
        else set()
    )
    if set(document) - allowed:
        raise ValueError("Unexpected page fields")
    if document["mode"] == "custom":
        if not clean_html(document.get("html", "")).strip():
            raise ValueError("Provide the complete self-contained HTML page")
    elif "settings" in document:
        settings = document["settings"]
        if not isinstance(settings, dict) or set(settings) not in (
            {"modules", "feed"},
            {"modules", "feed", "minimal"},
        ):
            raise ValueError(
                "Provide section order, feed address and optional minimal-page preference"
            )
        clean_settings(settings)
    return PageDefinition(
        source,
        hashlib.sha256(source.encode()).hexdigest(),
        clean_name(document["name"]),
        document["page"],
        document,
    )


def state_digest(state):
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def capabilities():
    return {
        "version": 1,
        "max_source_bytes": MAX_SOURCE_BYTES,
        "pages": ["newtab", "welcome", "style"],
        "modes": ["builtin", "custom", "theme"],
        "style_guide": style_guide(),
        "style_fields": deepcopy(DEFAULTS),
        "style_example": {
            "version": 1,
            "name": "Ocean palette",
            "page": "style",
            "mode": "theme",
            "style": style_guide()["example"],
        },
        "modules": list(MODULES),
        "settings_fields": {
            "modules": "ordered enabled section IDs",
            "feed": "HTTP(S) RSS/Atom address or empty",
            "minimal": "optional boolean; hide built-in heading/header/footer",
        },
        "example": deepcopy(EXAMPLE),
        "custom_example": {
            "version": 1,
            "name": "My page",
            "page": "newtab",
            "mode": "custom",
            "html": CUSTOM_EXAMPLE,
        },
        "review": "User chooses a normal profile and reviews exact source. No existing page content, bookmarks, files or accounts are disclosed. Every change requires review; stale previews are refused. Inline scripts run only after applying, without network reads or native Steed actions.",
    }
