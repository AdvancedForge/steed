"""Task-bound facade for clients; management/approval stays with the controller."""

from concurrent.futures import Future
from dataclasses import dataclass, field

from services.command_broker import CommandBroker

CLIENT_COMMANDS = frozenset(
    {
        "task.status",
        "task.capabilities",
        "browser.context",
        "task.events",
        "task.finish",
        "grant.request",
        "grant.list",
        "session.open",
        "session.navigate",
        "session.inspect",
        "page.inspect",
        "page.target",
        "page.navigate",
        "page.act",
        "page.submit",
        "page.scroll",
        "page.wait",
        "session.attach",
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
