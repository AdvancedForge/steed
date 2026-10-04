"""Manual, read-only account refreshes under reusable native panel permission."""

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import uuid4

from services.app_accounts import AccountError
from services.connectors import ConnectorJob, ConnectorRequest
from services.panel_mapping import empty_content, project_result
from services.task_policy import TaskPermissionError


class PanelReadGrant:
    """Immutable worker scope; Qt revocation flips only a thread-safe event."""

    def __init__(self, request):
        self.request = request
        self.ended = threading.Event()

    def check(self, action, **scope):
        if (
            self.ended.is_set()
            or action != "app"
            or scope
            != {
                "connector": self.request.connector,
                "tool": self.request.tool,
                "effect": "read",
                "destination": self.request.destination,
            }
        ):
            raise TaskPermissionError("panel_read_revoked")


class PanelAccounts:
    def __init__(self, panels):
        self.panels, self.service = panels, panels.service
        self.results, self.jobs, self.waiting = {}, {}, {}
        self.inflight = {}
        self.service.changed.connect(self.revalidate)

    def binding(self, widget):
        """Resolve metadata only: proposal/review/view never invokes a provider."""
        source = widget["account"]
        definition = self.service.connectors.get(source["connector"], source["tool"])
        if definition.effect != "read" or definition.requires_artifact:
            raise ValueError("Panels accept configured read tools only")
        if definition.validate_destination:
            definition.validate_destination(source["destination"])
        inputs = (
            definition.validate_inputs(source["inputs"])
            if definition.validate_inputs
            else None
        )
        if source["inputs"] and inputs is None:
            raise ValueError("This read tool does not accept inputs")
        connections = self.service.recipes.connections
        if connections is None or connections.store is None or connections.failure:
            raise ValueError("Account connections are unavailable")
        account = next(
            (a for a in connections.store.list() if a.connector == source["connector"]),
            None,
        )
        if account is None:
            raise ValueError("The selected account is unavailable")
        connections.store.active(account)
        descriptor = next(
            t
            for t in self.service.connectors.describe()
            if t["connector"] == source["connector"] and t["tool"] == source["tool"]
        )
        digest = hashlib.sha256(
            json.dumps(descriptor, sort_keys=True).encode()
        ).hexdigest()
        return {
            "account_id": account.account_id,
            "subject": account.subject,
            "label": account.label,
            "tool_sha256": digest,
            "inputs": inputs,
        }

    def allowed(self, key):
        self.service._usable()
        task_id, request_id, widget_id = key
        if not self.panels.source_access(task_id, request_id):
            raise ValueError("Linked read permission is disabled")
        widget = self.panels.definition(task_id, request_id).account_widgets[widget_id]
        binding = json.loads(self.panels._row(task_id, request_id)["bindings"])[
            widget_id
        ]
        if binding != self.binding(widget):
            raise ValueError("Account or tool metadata changed; review a new panel")
        source = widget["account"]
        if (
            self.panels.db.execute(
                "SELECT 1 FROM connector_access WHERE connector=? AND tool=? AND effect='read'",
                (source["connector"], source["tool"]),
            ).fetchone()
            is None
        ):
            raise ValueError("Enable this account's read access in App Connections")
        return (
            widget,
            binding,
            self.service.connectors.get(source["connector"], source["tool"]),
        )

    def close(self):
        for job, grant in list(self.inflight.items()):
            grant.ended.set()
            job.cancelled.set()
            job.timer.stop()
            job.deleteLater()
        self.jobs.clear()
        self.inflight.clear()
        self.waiting.clear()
        self.results.clear()

    def revalidate(self):
        if self.service._closed or self.service.failure:
            self.close()
            return
        for key in set(self.results) | set(self.jobs) | set(self.waiting):
            try:
                self.allowed(key)
            except (ValueError, KeyError, AccountError, OSError, sqlite3.Error):
                self.results.pop(key, None)
                self.waiting.pop(key, None)
                if key in self.jobs:
                    job, grant = self.jobs.pop(key)
                    grant.ended.set()
                    job.cancelled.set()
                    job.timer.stop()
        self._advance()

    def view(self, task_id, request_id, widget):
        key = (task_id, request_id, widget["id"])
        value = dict(
            widget,
            source_kind="account",
            available=False,
            content=empty_content(widget),
            artifact_title=widget["account"]["tool"],
            fetched_at="",
        )
        try:
            self.allowed(key)
        except (ValueError, KeyError, AccountError, OSError):
            self.results.pop(key, None)
            value["unavailable"] = (
                "Linked account unavailable; check panel permission and account read access"
            )
            return value
        if key in self.jobs or key in self.waiting:
            value["unavailable"] = (
                "Refreshing account…"
                if key in self.jobs
                else "Waiting to refresh account…"
            )
        elif key in self.results:
            value.update(self.results[key])
        else:
            value["unavailable"] = (
                "Click Refresh linked sources to fetch this account read"
            )
        return value

    def refresh(self, task_id, request_id, *, profile_key):
        row = self.panels._row(task_id, request_id)
        if not profile_key or profile_key != row["profile_key"]:
            raise ValueError("Choose this panel's current display profile")
        keys = [
            (task_id, request_id, wid)
            for wid in self.panels.definition(task_id, request_id).account_widgets
        ]
        # Validate the entire request before enqueuing any provider operation.
        for key in keys:
            self.allowed(key)
        if len(self.waiting) + len(keys) > 16:
            raise ValueError("Too many pending panel refreshes; wait for current reads")
        for key in keys:
            if key not in self.jobs and key not in self.waiting:
                self.results.pop(key, None)
                self.waiting[key] = uuid4().hex
        self._advance()
        self.service.changed.emit()

    def _advance(self):
        if self.service._closed or self.service.failure:
            return
        while self.waiting and len(self.inflight) < 2:
            key = next(iter(self.waiting))
            request_id = self.waiting.pop(key)
            try:
                widget, binding, definition = self.allowed(key)
            except (ValueError, KeyError, AccountError, OSError, sqlite3.Error):
                continue
            source = widget["account"]
            request = ConnectorRequest(
                request_id,
                source["connector"],
                source["tool"],
                "read",
                source["destination"],
                None,
                inputs=binding["inputs"],
            )
            grant = PanelReadGrant(request)
            job = self._new_job(key, definition, request, grant)
            self.jobs[key] = job, grant
            self.inflight[job] = grant
            job.timer.timeout.connect(lambda key=key, job=job: self._timeout(key, job))
            job.timer.start(self.service.connector_timeout_ms)
            job.start()

    def _new_job(self, key, definition, request, grant):
        # This call owns its closure, so each queued callback pins its exact job.
        job = ConnectorJob(
            definition,
            request,
            grant,
            lambda reply: self._finished(key, reply, job),
            self.service,
        )
        return job

    def _timeout(self, key, job):
        current = self.jobs.get(key)
        if current is None or current[0] is not job:
            return
        self.jobs.pop(key)
        current[1].ended.set()
        job.cancelled.set()
        job.timer.stop()
        self.results[key] = {
            "available": False,
            "fetched_at": "",
            "unavailable": "Account refresh timed out; no automatic retry",
        }
        # A cancelled worker still occupies its slot until it actually returns.
        self.service.changed.emit()

    def _finished(self, key, reply, job=None):
        if job not in self.inflight:
            return
        self.inflight.pop(job)
        job.timer.stop()
        job.deleteLater()
        current = self.jobs.get(key)
        if current is None or current[0] is not job:
            self._advance()
            self.service.changed.emit()
            return
        self.jobs.pop(key)
        _, grant = current
        grant.ended.set()
        job.cancelled.set()
        result, _ = reply
        try:
            widget, _, _ = self.allowed(key)
            if result is None or result.state != "succeeded":
                raise ValueError("Account read failed")
            # Results are already bounded/redacted by the trusted adapter.
            result.validate()
            self.results[key] = {
                "available": True,
                **project_result(widget, result.text),
                "unavailable": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self.service.store.record_event(
                key[0], "panel.refreshed", {"request_id": key[1], "widget_id": key[2]}
            )
        except (ValueError, KeyError, AccountError, OSError):
            self.results[key] = {
                "available": False,
                "fetched_at": "",
                "unavailable": "Refresh failed; check account connection, permissions and service availability",
            }
        except sqlite3.Error:
            self.results.pop(key, None)
            self.service._fatal()
        self._advance()
        self.service.changed.emit()
