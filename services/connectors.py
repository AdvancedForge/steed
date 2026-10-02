"""Trusted connector definitions and bounded worker jobs."""

import threading
from dataclasses import dataclass

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from services.connector_policy import EFFECTS, connector_name
from services.task_artifacts import TaskArtifact


@dataclass(frozen=True)
class ConnectorReview:
    before: str
    after: str
    context: str

    def validate(self):
        for value, maximum in (
            (self.before, 16384),
            (self.after, 16384),
            (self.context, 4096),
        ):
            if (
                not isinstance(value, str)
                or "\x00" in value
                or len(value.encode("utf-8")) > maximum
            ):
                raise ValueError("Invalid bounded app review")
        if not self.after or not self.context:
            raise ValueError("App review needs content and provider context")
        return self


@dataclass(frozen=True)
class ConnectorRequest:
    request_id: str
    connector: str
    tool: str
    effect: str
    destination: str
    artifact: TaskArtifact | None
    operation_id: str = ""
    review: ConnectorReview | None = None


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
    validate_destination: object = None
    prepare_review: object = None


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
            or (
                definition.validate_destination is not None
                and not callable(definition.validate_destination)
            )
            or (
                definition.prepare_review is not None
                and (
                    not callable(definition.prepare_review)
                    or not definition.requires_artifact
                    or definition.effect != "write"
                )
            )
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

    def unregister(self, connector):
        """Trusted account settings only; existing job definitions remain immutable."""
        connector_name(connector)
        self._tools = {k: v for k, v in self._tools.items() if k[0] != connector}

    def describe(self):
        return [
            {
                "connector": t.connector,
                "tool": t.tool,
                "effect": t.effect,
                "description": t.description,
                "requires_artifact": t.requires_artifact,
                "can_reconcile": t.reconcile is not None,
                "reconcile_effect": "read" if t.reconcile is not None else None,
                "requires_review": t.prepare_review is not None,
            }
            for _, t in sorted(self._tools.items())
        ]

    def check_scope(self, scope):
        for permission in scope.connector_permissions:
            definition = self.get(permission.connector, permission.tool)
            read_probe = (
                permission.effect == "read" and definition.reconcile is not None
            )
            if definition.effect != permission.effect and not read_probe:
                raise ValueError("App effect differs from trusted tool configuration")
            if (
                definition.requires_artifact
                and not read_probe
                and "artifact" not in scope.actions
            ):
                raise ValueError("This app tool needs artifact permission")
            if definition.validate_destination is not None:
                definition.validate_destination(permission.destination)


class ConnectorCancellation(threading.Event):
    """Cooperative providers recheck authority before each HTTP request."""

    def __init__(self, grant, request, *, artifact=False):
        super().__init__()
        self.grant, self.request, self.artifact = grant, request, artifact

    def is_set(self):
        if not super().is_set():
            try:
                self.grant.check(
                    "app",
                    connector=self.request.connector,
                    tool=self.request.tool,
                    effect=self.request.effect,
                    destination=self.request.destination,
                )
                if self.artifact:
                    self.grant.check("artifact")
            except Exception:
                self.set()
        return super().is_set()


class ConnectorJob(QObject):
    finished = pyqtSignal(object)

    def __init__(
        self,
        definition,
        request,
        grant,
        callback,
        parent,
        *,
        probe=False,
        probe_request=None,
        preparing=False,
    ):
        super().__init__(parent)
        self.definition, self.request, self.grant = definition, request, grant
        self.cancelled = ConnectorCancellation(
            grant, request, artifact=definition.requires_artifact and not probe
        )
        self.probe = probe
        self.probe_request = probe_request
        self.preparing = preparing
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
                    self.definition.prepare_review(self.request, self.cancelled)
                    if self.preparing
                    else (
                        self.definition.reconcile(
                            self.probe_request
                            or ConnectorProbe(
                                self.request.request_id,
                                self.request.destination,
                                self.request.operation_id,
                            ),
                            self.cancelled,
                        )
                        if self.probe
                        else self.definition.execute(self.request, self.cancelled)
                    )
                )
                expected = ConnectorReview if self.preparing else ConnectorOutcome
                if not isinstance(result, expected):
                    raise ValueError("Invalid connector adapter result")
                reply = (result.validate(), False)
            except Exception:
                reply = (None, False)
        try:
            self.finished.emit(reply)
        except RuntimeError:
            pass  # Browser teardown already disposed the Qt recipient.
