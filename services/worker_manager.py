"""Owner-thread authority for isolated workers; source never controls approval."""

import re
import sqlite3

from PyQt6.QtCore import QObject

from services.agent_tools import TOOLS, json_value
from services.command_broker import require_owner_thread
from services.connector_policy import ConnectorPermission, connector_permissions
from services.local_worker import MAX_WORKERS, LocalWorkerJob, WorkerOutcome
from services.task_policy import TaskPermissionError
from services.worker_bundle import environment, require_installed
from services.worker_diagnostics import WorkerDiagnostics
from services.worker_store import WorkerStore

RPC_TOOLS = {
    tool.command: tool
    for tool in TOOLS
    if tool.command
    in {
        "workspace.begin",
        "workspace.write",
        "workspace.seal",
        "workspace.list",
        "workspace.read",
        "artifact.store",
        "artifact.list",
        "artifact.read",
        "app.list_tools",
        "app.call_tool",
        "app.status",
    }
}


class WorkerManager(QObject):
    def __init__(self, service):
        super().__init__(service)
        self.service = service
        self.store = WorkerStore(service.store)
        self.jobs = {}
        self.diagnostics = WorkerDiagnostics(self)

    def capabilities(self, *, recheck=False):
        require_owner_thread(self)
        self.service._usable()
        if recheck:
            if (
                self.diagnostics.future is not None
                and not self.diagnostics.future.done()
            ):
                raise ValueError("Wait for the current setup check to finish")
            self.diagnostics.close()
            self.diagnostics.deleteLater()
            self.diagnostics = WorkerDiagnostics(self)
        return self.diagnostics.check()

    def run(self, task_id, extension_id, request_id, runtime="stdlib"):
        require_owner_thread(self)
        self.service._active(task_id)
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", request_id
        ):
            raise ValueError(
                "Use a stable worker request ID of 1–64 letters/digits/_/-"
            )
        grant = self.service._authorize(task_id, "worker")
        try:
            previous = self.store.get(task_id, request_id)
        except KeyError:
            previous = None
        if previous is not None:
            if previous.extension_id != extension_id or previous.runtime != runtime:
                raise ValueError(
                    "Worker request ID is already bound to a package and runtime"
                )
            return previous
        env = environment(runtime)
        require_installed(env)
        package = self.service.workspace.package(task_id, extension_id)
        self.check_package(package, grant)
        if len(self.store.list(task_id)) >= 50:
            raise TaskPermissionError("worker_run_limit")
        try:
            record = self.store.create(task_id, request_id, package, env)
            grant_id = self.service._grants[task_id][0]
            if self.store.remembered(
                task_id, grant_id, package["package_sha256"], env.sha256
            ):
                record = self.resolve(task_id, request_id, approve=True)
        except TaskPermissionError:
            raise
        except (sqlite3.Error, OSError):
            self.service._fatal(defer_broker_close=True)
            raise
        self.service.changed.emit()
        return record

    def check_package(self, package, grant):
        grant.check("worker")
        if not package["manifest"]["entrypoint"].endswith(".py"):
            raise ValueError("The local worker supports Python entrypoints")
        for permission in connector_permissions(package["manifest"]["capabilities"]):
            grant.check(
                "app",
                connector=permission.connector,
                tool=permission.tool,
                effect=permission.effect,
                destination=permission.destination,
            )

    def resolve(self, task_id, request_id, *, approve=False, remember=False):
        """Trusted user decision only; there is no agent approval endpoint."""
        require_owner_thread(self)
        self.service._usable()
        if type(approve) is not bool or type(remember) is not bool:
            raise ValueError("Use explicit boolean package decisions")
        receipt = self.store.get(task_id, request_id)
        if receipt.state != "awaiting_approval":
            raise ValueError("Only a pending package can be approved or denied")
        if not approve:
            result = self.store.update(task_id, request_id, "cancelled", "user_denied")
            self.service.changed.emit()
            return result
        self.service._active(task_id)
        live = self.service._grants.get(task_id)
        if live is None:
            raise TaskPermissionError("approval_required")
        grant_id, grant = live
        grant.check("worker")
        package = self.service.workspace.package(task_id, receipt.extension_id)
        if package["package_sha256"] != receipt.package_sha256:
            raise ValueError("The captured package identity changed")
        self.check_package(package, grant)
        env = environment(receipt.runtime)
        if env.sha256 != receipt.environment_sha256:
            raise ValueError(
                "The captured worker environment changed; request a fresh approval"
            )
        require_installed(env)
        if len(self.jobs) >= MAX_WORKERS or any(
            owner == task_id for owner, _ in self.jobs
        ):
            raise TaskPermissionError("worker_capacity_reached")
        key = (task_id, request_id)
        job = LocalWorkerJob(
            package["files"],
            package["manifest"]["entrypoint"],
            lambda message, future: self.rpc(key, message, future),
            lambda result: self.finished(key, result),
            self,
            environment=env,
        )
        job.grant = grant
        job.package_sha256 = package["package_sha256"]
        job.file_ids = frozenset(package["file_ids"])
        job.capabilities = frozenset(
            connector_permissions(package["manifest"]["capabilities"])
        )
        job.app_requests = set()
        try:
            result = self.store.approve(task_id, request_id, grant_id, remember)
            self.jobs[key] = job
            job.start()
        except (sqlite3.Error, OSError):
            self.service._fatal(defer_broker_close=True)
            job.deleteLater()
            raise
        except Exception:
            self.jobs.pop(key, None)
            job.close()
            job.deleteLater()
            self.store.update(task_id, request_id, "failed", "local_worker_unavailable")
            raise
        self.service.changed.emit()
        return result

    def rpc(self, key, message, future):
        require_owner_thread(self)
        try:
            self.service._usable()
            job = self.jobs.get(key)
            if job is None or job.cancelled.is_set():
                raise TaskPermissionError("worker_ended")
            job.grant.check("worker")
            command, arguments = message["command"], message["arguments"]
            if not isinstance(command, str) or command not in RPC_TOOLS:
                raise TaskPermissionError("worker_command_unavailable")
            RPC_TOOLS[command].validate(arguments)
            if command == "app.call_tool":
                definition = self.service.connectors.get(
                    arguments["connector"], arguments["tool"]
                )
                permission = ConnectorPermission(
                    definition.connector,
                    definition.tool,
                    definition.effect,
                    arguments["destination"],
                )
                if permission not in job.capabilities:
                    raise TaskPermissionError("worker_capability_undeclared")
            elif (
                command == "app.status"
                and arguments["request_id"] not in job.app_requests
            ):
                raise TaskPermissionError("worker_receipt_not_owned")
            task_id = key[0]

            def check_dispatch():
                self.service._usable()
                if self.jobs.get(key) is not job or job.cancelled.is_set():
                    raise TaskPermissionError("worker_ended")
                job.grant.check("worker")

            result = self.service.broker.submit_guarded(
                command, check_dispatch, task_id=task_id, **arguments
            )

            def complete(done):
                try:
                    value = done.result()
                    if command == "app.call_tool":
                        job.app_requests.add(arguments["request_id"])
                        if (
                            job.cancelled.is_set()
                            and not self.service.failure
                            and not self.service._closed
                        ):
                            self.service._app_cancel(task_id, arguments["request_id"])
                    if command == "app.list_tools":
                        value = [
                            t
                            for t in value
                            if any(
                                p.connector == t["connector"] and p.tool == t["tool"]
                                for p in job.capabilities
                            )
                        ]
                    if not future.done():
                        future.set_result({"result": json_value(value)})
                except Exception as error:
                    if isinstance(error, (sqlite3.Error, OSError)) and not isinstance(
                        error, TaskPermissionError
                    ):
                        self.service._fatal(defer_broker_close=True)
                    if not future.done():
                        future.set_result(
                            {"error": getattr(error, "code", "worker_operation_failed")}
                        )

            result.add_done_callback(complete)
        except (TaskPermissionError, ValueError, KeyError, RuntimeError) as error:
            future.set_result(
                {"error": getattr(error, "code", "worker_request_invalid")}
            )
        except (sqlite3.Error, OSError):
            self.service._fatal(defer_broker_close=True)
            future.set_result({"error": "worker_journal_unavailable"})

    def finished(self, key, outcome):
        require_owner_thread(self)
        job = self.jobs.pop(key, None)
        if job is None:
            return
        job.timer.stop()
        if self.service.failure or self.service._closed:
            job.deleteLater()
            return
        try:
            receipt = self.store.get(*key)
            if job.cancelled.is_set() or receipt.state == "cancelling":
                outcome = WorkerOutcome(
                    "cancelled", receipt.reason or "cancelled", outcome.rpc_count
                )
            self.store.update(*key, outcome.state, outcome.reason, outcome.rpc_count)
        except (sqlite3.Error, OSError):
            self.service._fatal()
        finally:
            job.deleteLater()
        self.service.changed.emit()

    def status(self, task_id, request_id):
        record = self.store.get(task_id, request_id)
        self.service._authorize(task_id, "worker")
        return record

    def list(self, task_id):
        self.service._authorize(task_id, "worker")
        return self.store.list(task_id)

    def cancel(self, task_id, request_id, *, reason="cancelled"):
        require_owner_thread(self)
        key = (task_id, request_id)
        receipt = self.store.get(*key)
        job = self.jobs.get(key)
        if receipt.state == "awaiting_approval":
            receipt = self.store.update(*key, "cancelled", reason)
        if job is not None:
            job.cancelled.set()
            try:
                receipt = self.store.update(
                    *key, "cancelling", reason, receipt.rpc_count
                )
                if not self.service._closed and not self.service.failure:
                    for request in job.app_requests:
                        self.service._app_cancel(task_id, request)
            except TaskPermissionError:
                pass
            except (sqlite3.Error, OSError):
                self.service._fatal()
                raise
        self.service.changed.emit()
        return receipt

    def cancel_task(self, task_id):
        for record in self.store.list(task_id):
            if record.state in {"awaiting_approval", "running", "cancelling"}:
                self.cancel(task_id, record.request_id, reason="task_ended")

    def deleting(self, task_id, file_id):
        for record in self.store.list(task_id):
            if record.state in {
                "awaiting_approval",
                "running",
                "cancelling",
            } and file_id in self.service.workspace.package_file_ids(
                task_id, record.extension_id
            ):
                self.cancel(task_id, record.request_id, reason="package_source_deleted")

    def stop_all(self):
        self.diagnostics.close()
        for job in self.jobs.values():
            job.cancelled.set()
            job.timer.stop()

    def close(self):
        self.stop_all()
        for key, job in list(self.jobs.items()):
            job.close()
            if not self.service.failure:
                self.store.update(*key, "interrupted", "shutdown")
            job.deleteLater()
        self.jobs.clear()
