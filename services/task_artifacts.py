"""Bounded immutable task text; provenance refers to recorded page inspections."""

import hashlib
from dataclasses import dataclass

MAX_ARTIFACT_BYTES = 16 * 1024
MAX_TASK_ARTIFACT_BYTES = 512 * 1024
MAX_TASK_ARTIFACTS = 50


@dataclass(frozen=True)
class ArtifactSummary:
    artifact_id: str
    task_id: str
    title: str
    format: str
    byte_count: int
    sha256: str
    source_events: tuple[int, ...]
    created_at: str


@dataclass(frozen=True)
class TaskArtifact(ArtifactSummary):
    content: str


def artifact_input(title, content, format="markdown", source_events=()):
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 200
        or any(not c.isprintable() for c in title)
    ):
        raise ValueError("Provide a printable artifact title of at most 200 characters")
    if format not in ("text", "markdown"):
        raise ValueError("Artifact format must be text or markdown")
    if not isinstance(content, str) or not content or "\x00" in content:
        raise ValueError("Provide nonempty text content without null characters")
    try:
        encoded = content.encode("utf-8")
    except UnicodeError:
        raise ValueError("Artifact content must be valid UTF-8 text") from None
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact content exceeds 16 KiB")
    if (
        not isinstance(source_events, (tuple, list))
        or len(source_events) > 20
        or any(type(s) is not int or s <= 0 for s in source_events)
        or len(set(source_events)) != len(source_events)
    ):
        raise ValueError("Provide at most 20 distinct page-inspection event sequences")
    return (
        title.strip(),
        len(encoded),
        hashlib.sha256(encoded).hexdigest(),
        tuple(source_events),
    )
