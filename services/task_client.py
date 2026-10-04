"""Task-bound facade for clients; management/approval stays with the controller."""

from concurrent.futures import Future
from dataclasses import dataclass, field

from services.command_broker import CommandBroker

CLIENT_COMMANDS = frozenset(
    {
        "task.status",
        "task.capabilities",
        "browser.context",
        "layout.capabilities",
        "layout.propose",
        "layout.status",
        "browser.tabs",
        "session.adopt",
        "task.events",
        "conversation.read",
        "conversation.reply",
        "task.finish",
        "task.recover",
        "task.recovery",
        "artifact.store",
        "artifact.list",
        "artifact.read",
        "workspace.begin",
        "workspace.write",
        "workspace.seal",
        "workspace.list",
        "workspace.read",
        "workspace.export",
        "workspace.stage_extension",
        "library.folders",
        "library.list",
        "library.search",
        "library.read",
        "library.mkdir",
        "library.write_begin",
        "library.write_chunk",
        "library.write_commit",
        "library.remove",
        "library.restore",
        "library.import_workspace",
        "library.save_workspace",
        "worker.run",
        "worker.capabilities",
        "worker.status",
        "worker.list",
        "worker.cancel",
        "recipe.propose",
        "recipe.validate",
        "recipe.status",
        "recipe.list",
        "recipe.capabilities",
        "app.list_tools",
        "app.call_tool",
        "app.status",
        "app.cancel",
        "app.reconcile",
        "app.reconcile_history",
        "grant.request",
        "grant.list",
        "session.open",
        "session.navigate",
        "session.inspect",
        "page.inspect",
        "page.capture",
        "page.target",
        "page.navigate",
        "page.act",
        "page.submit",
        "page.scroll",
        "page.wait",
        "page.download",
        "page.upload",
        "download.status",
        "download.cancel",
        "session.attach",
        "session.watch",
        "session.stop",
        "session.close",
    }
)


@dataclass(frozen=True)
class TaskClient:
    """A dispatcher handle, not a sandbox for arbitrary in-process Python."""

    task_id: str
    _broker: CommandBroker = field(repr=False)

    def submit(self, command, **arguments) -> Future:
        if not isinstance(command, str) or command not in CLIENT_COMMANDS:
            return self._broker.submit(
                "client.denied",
                task_id=self.task_id,
                reason="client_command_unavailable",
            )
        provided = arguments.pop("task_id", self.task_id)
        if provided != self.task_id:
            return self._broker.submit(
                "client.denied", task_id=self.task_id, reason="task_identity_mismatch"
            )
        return self._broker.submit(command, task_id=self.task_id, **arguments)
