"""Trusted connector definitions and bounded worker jobs; no real providers bundled."""

import threading
from dataclasses import dataclass

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from services.connector_policy import EFFECTS, connector_name
from services.task_artifacts import TaskArtifact


@dataclass(frozen=True)
class ConnectorRequest:
    request_id: str
    connector: str
    tool: str
    effect: str
    destination: str
    artifact: TaskArtifact | None
    operation_id: str = ""


@dataclass(frozen=True)
class ConnectorProbe:
    request_id: str
    destination: str
    operation_id: str = ""


@dataclass(frozen=True)
class ConnectorOutcome:
    state: str = "succeeded"
    reference: str = ""
    text: str = ""

    def validate(self):
        if self.state not in {"succeeded", "not_applied", "uncertain"}:
            raise ValueError("Invalid connector outcome")
        if (
            not isinstance(self.reference, str)
            or len(self.reference) > 2048
            or not all(c.isprintable() for c in self.reference)
        ):
            raise ValueError("Invalid connector reference")
        if (
            not isinstance(self.text, str)
            or "\x00" in self.text
            or len(self.text.encode("utf-8")) > 16384
        ):
            raise ValueError("Connector result exceeds the text limit")
        return self


@dataclass(frozen=True)
class ConnectorTool:
    connector: str
    tool: str
    effect: str
    description: str
    requires_artifact: bool
    execute: object
    reconcile: object = None


class ConnectorRegistry:
    """Configured by trusted browser code, never populated from remote annotations."""

    def __init__(self):
        self._tools = {}

    def register(self, definition):
        connector_name(definition.connector)
        connector_name(definition.tool)
        if (
            definition.effect not in EFFECTS
            or type(definition.requires_artifact) is not bool
            or not callable(definition.execute)
            or (definition.reconcile is not None and not callable(definition.reconcile))
        ):
            raise ValueError("Invalid trusted connector tool")
        if (
            not isinstance(definition.description, str)
            or len(definition.description) > 512
            or not all(c.isprintable() for c in definition.description)
        ):
            raise ValueError("Provide a printable connector description")
        key = (definition.connector, definition.tool)
        if key in self._tools:
            raise ValueError("Connector tool is already registered")
        self._tools[key] = definition

    def get(self, connector, tool):
        connector_name(connector)
        connector_name(tool)
        if (connector, tool) not in self._tools:
            raise ValueError("Connector tool is not configured")
        return self._tools[connector, tool]

    def describe(self):
        return [
            {
                "connector": t.connector,
                "tool": t.tool,
                "effect": t.effect,
                "description": t.description,
                "requires_artifact": t.requires_artifact,
                "can_reconcile": t.reconcile is not None,
            }
            for _, t in sorted(self._tools.items())
        ]

    def check_scope(self, scope):
        for permission in scope.connector_permissions:
            definition = self.get(permission.connector, permission.tool)
            if definition.effect != permission.effect:
                raise ValueError("App effect differs from trusted tool configuration")
            if definition.requires_artifact and "artifact" not in scope.actions:
                raise ValueError("This app tool needs artifact permission")


class ConnectorJob(QObject):
    finished = pyqtSignal(object)

    def __init__(self, definition, request, grant, callback, parent, *, probe=False):
        super().__init__(parent)
        self.definition, self.request, self.grant = definition, request, grant
        self.cancelled = threading.Event()
        self.probe = probe
        self.dispatched = False
        self.finished.connect(callback, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            if self.cancelled.is_set():
                raise RuntimeError("Cancelled before adapter entry")
            self.grant.check(
                "app",
                connector=self.request.connector,
                tool=self.request.tool,
                effect=self.request.effect,
                destination=self.request.destination,
            )
            if not self.probe and self.definition.requires_artifact:
                self.grant.check("artifact")
        except Exception:
            reply = (None, True)
        else:
            try:
                result = (
                    self.definition.reconcile(
                        ConnectorProbe(
                            self.request.request_id,
                            self.request.destination,
                            self.request.operation_id,
                        ),
                        self.cancelled,
                    )
                    if self.probe
                    else self.definition.execute(self.request, self.cancelled)
                )
                if not isinstance(result, ConnectorOutcome):
                    raise ValueError("Invalid connector adapter result")
                reply = (result.validate(), False)
            except Exception:
                reply = (None, False)
        try:
            self.finished.emit(reply)
        except RuntimeError:
            pass  # Browser teardown already disposed the Qt recipient.
