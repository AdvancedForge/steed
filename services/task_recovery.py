"""User-selected follow-up context; historical data never restores authority."""

import json
from dataclasses import asdict

RECOVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_recoveries (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    status TEXT NOT NULL CHECK(status IN ('requested', 'approved', 'denied', 'ended')),
    source_task_id TEXT REFERENCES tasks(task_id),
    snapshot TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recovery_effects (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    source_task_id TEXT NOT NULL REFERENCES tasks(task_id),
    request_id TEXT NOT NULL,
    connector TEXT NOT NULL,
    tool TEXT NOT NULL,
    effect TEXT NOT NULL,
    destination TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (task_id, source_task_id, request_id)
);
CREATE INDEX IF NOT EXISTS recoveries_by_source ON task_recoveries(source_task_id);
"""
MAX_RECOVERY_BYTES = 128 * 1024
MAX_RECOVERY_ARTIFACTS = 10
MAX_RECOVERY_DEPTH = 32


def recovery_snapshot(store, connector_store, source_task_id, artifacts, evidence):
    """A bounded snapshot explicitly disclosed by the user; original IDs stay original."""
    task = store.get(source_task_id)
    sources = sorted(
        {sequence for artifact in artifacts for sequence in artifact.source_events}
    )
    grants = store.grants(source_task_id)
    snapshot = {
        "source_task": asdict(task),
        "evidence_included": evidence,
        "previous_scope": grants[-1].scope if evidence and grants else None,
        "activity": [
            {"sequence": e.sequence, "kind": e.kind, "created_at": e.created_at}
            for e in store.recent_events(source_task_id, limit=50)
        ]
        if evidence
        else [],
        "source_events": [
            asdict(e) for e in store.events_by_sequence(source_task_id, sources)
        ]
        if evidence
        else [],
        "app_receipts": [
            {
                **asdict(receipt),
                "text": receipt.text[:512],
                "text_truncated": len(receipt.text) > 512,
            }
            for receipt in connector_store.list(source_task_id)
        ]
        if evidence
        else [],
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RECOVERY_BYTES:
        raise ValueError(
            "Recovery evidence exceeds 128 KiB; share fewer drafts or omit evidence"
        )
    return snapshot
