"""User-owned reusable approvals; a single rule must contain the whole request."""

import json
from dataclasses import dataclass

from services.task_policy import TaskScope

RULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_rules (
    rule_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('global', 'profile')),
    profile_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_rule_grants (
    grant_id TEXT PRIMARY KEY REFERENCES grants(grant_id),
    rule_id TEXT NOT NULL
);
"""

SET_FIELDS = (
    "origins",
    "actions",
    "download_destinations",
    "upload_sources",
    "upload_origins",
    "connector_permissions",
)
LIMIT_FIELDS = (
    "duration_seconds",
    "max_operations",
    "max_requests",
    "max_sessions",
    "max_download_bytes",
    "max_upload_bytes",
)


@dataclass(frozen=True)
class ApprovalRule:
    rule_id: str
    mode: str
    profile_key: str
    scope: dict
    created_at: str

    @classmethod
    def from_row(cls, row):
        values = dict(row)
        values["scope"] = json.loads(values["scope"])
        if (
            values["mode"] not in ("global", "profile")
            or not isinstance(values["profile_key"], str)
            or (values["mode"] == "profile" and len(values["profile_key"]) != 32)
        ):
            raise ValueError("Invalid saved approval")
        TaskScope.parse(values["scope"])
        return cls(**values)

    def covers(self, scope, profile_key=""):
        # New authority fields need an explicit matching policy before reuse.
        if set(TaskScope.__dataclass_fields__) != {
            *SET_FIELDS,
            *LIMIT_FIELDS,
            "profile",
            "allow_private_network",
        }:
            return False
        if self.mode == "profile" and (
            not profile_key or profile_key != self.profile_key
        ):
            return False
        approved = TaskScope.parse(self.scope)
        if approved.profile == "private" and scope.profile != "private":
            # A throwaway private approval never covers your signed-in profiles.
            return False
        return (
            (not scope.allow_private_network or approved.allow_private_network)
            and all(
                set(getattr(scope, field)) <= set(getattr(approved, field))
                for field in SET_FIELDS
            )
            and all(
                getattr(scope, field) <= getattr(approved, field)
                for field in LIMIT_FIELDS
            )
        )
