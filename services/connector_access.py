"""User-owned account switches pin trusted tools, never wildcard task grants."""

from services.connector_policy import connector_name
from services.task_policy import TaskScope

ACCESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_access (
    connector TEXT NOT NULL,
    access_group TEXT NOT NULL,
    tool TEXT NOT NULL,
    effect TEXT NOT NULL,
    PRIMARY KEY(connector,access_group,tool,effect)
);
"""
GROUP_LABELS = {
    "read": "Read supported app data and check recorded results",
    "write": "Create and add content; tools may require payload or replacement review",
    "direct": "Replace content directly, without per-change review",
    "send": "Send messages to requested recipients; tools may require payload review",
    "delete": "Delete supported app data",
    "spend": "Spend money using supported app tools",
}


class ConnectorAccess:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def groups(self, connector):
        self.store._check()
        connector_name(connector)
        groups = {}
        for tool in self.registry.describe():
            if tool["connector"] != connector:
                continue
            definition = self.registry.get(connector, tool["tool"])
            group = definition.access_group or definition.effect
            groups.setdefault(group, set()).add((definition.tool, definition.effect))
            if definition.reconcile is not None:
                groups.setdefault("read", set()).add((definition.tool, "read"))
        return groups

    def enabled(self, connector, group):
        return self.state(connector, group) == "all"

    def state(self, connector, group):
        self.store._check()
        pairs = self.groups(connector).get(group, set())
        saved = {
            tuple(row)
            for row in self.store._db.execute(
                "SELECT tool,effect FROM connector_access WHERE connector=? AND access_group=?",
                (connector, group),
            )
        }
        return (
            "all" if pairs and pairs <= saved else "some" if pairs & saved else "none"
        )

    def set(self, connector, group, enabled):
        self.store._check()
        if group not in GROUP_LABELS or type(enabled) is not bool:
            raise ValueError("Choose a supported account access switch")
        pairs = self.groups(connector).get(group)
        if not pairs:
            raise ValueError("This account has no tools for that access switch")
        with self.store._db:
            self.store._db.execute(
                "DELETE FROM connector_access WHERE connector=? AND access_group=?",
                (connector, group),
            )
            if enabled:
                self.store._db.executemany(
                    "INSERT INTO connector_access VALUES (?,?,?,?)",
                    [
                        (connector, group, tool, effect)
                        for tool, effect in sorted(pairs)
                    ],
                )
        return pairs

    def covers(self, scope):
        self.store._check()
        # This setting never grants browsing, shared profile, file or execution access.
        if (
            set(TaskScope.__dataclass_fields__)
            != {
                "origins",
                "actions",
                "profile",
                "allow_private_network",
                "duration_seconds",
                "max_operations",
                "max_requests",
                "max_sessions",
                "download_destinations",
                "max_download_bytes",
                "upload_sources",
                "upload_origins",
                "max_upload_bytes",
                "connector_permissions",
            }
            or not scope.connector_permissions
            or not set(scope.actions) <= {"app", "artifact"}
            or scope.origins
            or scope.allow_private_network
            or scope.profile != "private"
            or scope.duration_seconds > 600
            or scope.max_operations > 1000
            or scope.max_requests > 1000
            or scope.max_sessions > 1
        ):
            return False
        self.registry.check_scope(scope)
        return all(
            self.store._db.execute(
                "SELECT 1 FROM connector_access WHERE connector=? AND tool=? AND effect=?",
                (p.connector, p.tool, p.effect),
            ).fetchone()
            is not None
            for p in scope.connector_permissions
        )

    def forget(self, connector):
        self.store._check()
        with self.store._db:
            self.store._db.execute(
                "DELETE FROM connector_access WHERE connector=?", (connector,)
            )
