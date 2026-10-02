"""Bounded, asynchronous DOM reads and opaque references to observed controls."""

import json
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWebEngineCore import QWebEngineScript

from services.command_broker import require_owner_thread

INSPECTION_SCRIPT = Path(__file__).with_name("page_inspection.js").read_text()
INSPECTION_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld.value


class PageInspectionError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Page inspection failed: {code}")


@dataclass(frozen=True)
class TargetDescriptor:
    target_id: str
    frame_id: str
    kind: str
    role: str
    label: str
    enabled: bool
    read_only: bool
    href: str | None
    operations: tuple[str, ...]
    constraints: tuple[str, ...]
    form_action: str | None = None
    form_method: str | None = None


@dataclass(frozen=True)
class FrameSnapshot:
    frame_id: str
    parent_frame_id: str | None
    path: str
    url: str
    origin: str
    text: str
    targets: tuple[TargetDescriptor, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class PageSnapshot:
    snapshot_id: str
    session_id: str
    task_id: str
    url: str
    title: str
    revision: int
    frames: tuple[FrameSnapshot, ...]
    limitations: tuple[str, ...]
    source: str = "rendered_dom"


@dataclass
class InspectionState:
    state_key: str = field(default_factory=lambda: "__steed_read_" + uuid4().hex)
    snapshot: PageSnapshot | None = None
    targets: dict = field(default_factory=dict)
    pending: set[Future] = field(default_factory=set)


class PageInspector(QObject):
    """No public script/selector input; policy is rechecked on every callback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._states: dict[str, InspectionState] = {}
        self.timeout_ms = 5000

    def invalidate(self, session_id, reason="stale_snapshot", *, discard=False):
        require_owner_thread(self)
        state = self._states.get(session_id)
        if state is None:
            return
        state.snapshot = None
        state.targets.clear()
        for future in tuple(state.pending):
            if not future.done():
                future.set_exception(PageInspectionError(reason))
        if discard:
            self._states.pop(session_id, None)

    def _run(self, session_id, page, request, validate, consume):
        state = self._states.setdefault(session_id, InspectionState())
        result = Future()
        state.pending.add(result)
        timer = QTimer(self)
        timer.setSingleShot(True)

        def cleanup(_):
            state.pending.discard(result)
            timer.stop()
            timer.deleteLater()

        result.add_done_callback(cleanup)

        def expire():
            if not result.done():
                self.invalidate(session_id, "inspection_timeout")

        timer.timeout.connect(expire)
        request["state_key"] = state.state_key
        script = INSPECTION_SCRIPT.replace("/* REQUEST */", json.dumps(request))

        def received(raw):
            # Qt also calls callbacks while destroying pages; do not access them
            # after invalidate() has completed the pending request.
            if result.done():
                return
            try:
                validate()
                if not isinstance(raw, str) or len(raw) > 2_000_000:
                    raise PageInspectionError("inspection_unavailable")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise PageInspectionError("inspection_unavailable")
                if "error" in data:
                    raise PageInspectionError(data["error"])
                value = consume(data)
            except Exception as error:
                if not result.done():
                    result.set_exception(error)
            else:
                if not result.done():
                    result.set_result(value)

        timer.start(self.timeout_ms)
        try:
            page.runJavaScript(script, INSPECTION_WORLD, received)
        except Exception as error:
            result.set_exception(error)
        return result

    def inspect(self, metadata, page, grant, validate):
        require_owner_thread(self)
        session_id = metadata.session_id
        self.invalidate(session_id, "inspection_superseded")
        state = self._states.setdefault(session_id, InspectionState())
        snapshot_id = uuid4().hex

        def consume(data):
            if data.get("snapshot_id") != snapshot_id:
                raise PageInspectionError("stale_snapshot")
            frames, targets = [], {}
            for frame in data["frames"]:
                descriptors = []
                for target in frame["targets"]:
                    target_id = uuid4().hex
                    descriptor = TargetDescriptor(
                        target_id,
                        frame["frame_id"],
                        target["kind"],
                        target["role"],
                        target["label"],
                        target["enabled"],
                        target["read_only"],
                        target["href"],
                        tuple(target["operations"]),
                        tuple(target["constraints"]),
                        target.get("form_action"),
                        target.get("form_method"),
                    )
                    targets[target_id] = (target["node_id"], descriptor)
                    descriptors.append(descriptor)
                frames.append(
                    FrameSnapshot(
                        frame["frame_id"],
                        frame["parent_frame_id"],
                        frame["path"],
                        frame["url"],
                        frame["origin"],
                        frame["text"],
                        tuple(descriptors),
                        tuple(frame["limitations"]),
                    )
                )
            snapshot = PageSnapshot(
                snapshot_id,
                session_id,
                metadata.task_id,
                metadata.url,
                data["title"],
                metadata.revision,
                tuple(frames),
                tuple(data["limitations"]),
            )
            state.snapshot, state.targets = snapshot, targets
            return snapshot

        return self._run(
            session_id,
            page,
            {
                "mode": "inspect",
                "snapshot_id": snapshot_id,
                "origins": grant.scope.origins,
            },
            validate,
            consume,
        )

    def target(self, session_id, page, snapshot_id, target_id, validate):
        require_owner_thread(self)
        node_id, descriptor = self._target_reference(session_id, snapshot_id, target_id)

        def consume(data):
            if data.get("valid") is not True:
                raise PageInspectionError("stale_snapshot")
            return descriptor

        return self._run(
            session_id,
            page,
            {"mode": "target", "snapshot_id": snapshot_id, "node_id": node_id},
            validate,
            consume,
        )

    def _snapshot_state(self, session_id, snapshot_id):
        state = self._states.get(session_id)
        if (
            state is None
            or state.snapshot is None
            or state.snapshot.snapshot_id != snapshot_id
        ):
            raise PageInspectionError("stale_snapshot")
        return state

    def _target_reference(self, session_id, snapshot_id, target_id):
        state = self._snapshot_state(session_id, snapshot_id)
        if not isinstance(target_id, str) or target_id not in state.targets:
            raise PageInspectionError("target_unknown")
        return state.targets[target_id]

    def submission_request(self, session_id, snapshot_id, target_id):
        node_id, descriptor = self._target_reference(session_id, snapshot_id, target_id)
        if not descriptor.enabled or "submit" not in descriptor.operations:
            raise PageInspectionError("form_unsupported")
        return {
            "mode": "form",
            "snapshot_id": snapshot_id,
            "node_id": node_id,
            "state_key": self._states[session_id].state_key,
        }

    def prepare_submission(self, session_id, page, request, validate):
        return self._run(session_id, page, request, validate, lambda data: data)

    def dispatch_submission(self, page, request, expected, received):
        # The intentional navigation invalidates inspection references. Its
        # receipt is owned by the backend's load observer, not the read queue.
        script = INSPECTION_SCRIPT.replace(
            "/* REQUEST */",
            json.dumps({**request, "mode": "submit", "expected": expected}),
        )
        page.runJavaScript(script, INSPECTION_WORLD, received)

    def act(self, session_id, page, snapshot_id, target_id, arguments, validate):
        request = self.input_request(session_id, snapshot_id, target_id, arguments)
        return self._mutation(session_id, page, {**request, "mode": "act"}, validate)

    def input_request(self, session_id, snapshot_id, target_id, arguments):
        node_id, descriptor = self._target_reference(session_id, snapshot_id, target_id)
        if (
            not descriptor.enabled
            or descriptor.read_only
            or arguments["operation"] not in descriptor.operations
            or descriptor.kind
            not in ("input", "textarea", "select", "checkbox", "radio")
        ):
            raise PageInspectionError("control_unsupported")
        return {"snapshot_id": snapshot_id, "node_id": node_id, **arguments}

    def input_phase(self, session_id, page, request, mode, validate):
        return self._run(
            session_id, page, {**request, "mode": mode}, validate, lambda data: data
        )

    def scroll(self, session_id, page, snapshot_id, frame_id, arguments, validate):
        state = self._snapshot_state(session_id, snapshot_id)
        if frame_id is None:
            frame_id = state.snapshot.frames[0].frame_id
        frame = next((f for f in state.snapshot.frames if f.frame_id == frame_id), None)
        if frame is None or frame.limitations:
            raise PageInspectionError("frame_unavailable")
        result = self._mutation(
            session_id,
            page,
            {
                "mode": "scroll",
                "snapshot_id": snapshot_id,
                "frame_id": frame_id,
                **arguments,
            },
            validate,
        )
        return frame_id, result

    def _mutation(self, session_id, page, request, validate):
        result = self._run(session_id, page, request, validate, lambda data: data)
        # Values/checked/selection properties do not necessarily create DOM
        # mutations. Every completed attempt invalidates references explicitly.
        result.add_done_callback(
            lambda _: self.invalidate(session_id, "action_completed")
        )
        return result
