"""Agents handing one step to the person: a captcha, a sign-in, a code, a
payment or a confirmation only a person should do.

browser.ask_person gives the page to the person at once (the agent's page
commands pause, as with Take Control), tells them what the agent needs, and
waits. The person does the step in the page and clicks Done (or "I can't"),
or uses Return to Agent in the watch window; then control goes back and
browser.ask_person_status reports what happened. Asking needs no permission:
it only ever takes power away from the agent.
"""

import time
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal

from browser.data_panel_format import stable_id
from services.agent_errors import AgentRequestError
from services.task_policy import TaskPermissionError

# What the agent needs, in words the person reads first.
REASONS = {
    "captcha": "solve a check that you are a person",
    "sign_in": "sign in",
    "code": "enter a verification code",
    "payment": "confirm a payment",
    "confirm": "confirm something only you should",
    "other": "do one step",
}
MESSAGE_LIMIT = 300


class Handoffs(QObject):
    # A new request (to show and notify) or one that ended (to hide).
    asked = pyqtSignal(object)
    ended = pyqtSignal(object)

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.requests = {}  # (task, request id) -> dict
        service.changed.connect(self._check)

    @staticmethod
    def what(entry):
        return REASONS[entry["reason"]]

    def _receipt(self, entry):
        receipt = {
            "request_id": entry["request_id"],
            "session_id": entry["session_id"],
            "state": entry["state"],
            "reason": entry["reason"],
        }
        if entry["state"] == "done":
            receipt["note"] = (
                "The person finished and handed the page back. Inspect it again "
                "before acting: it has changed."
            )
        elif entry["state"] == "not_done":
            receipt["note"] = (
                "The person could not or chose not to do it. Do not retry the same "
                "step without asking them in the conversation."
            )
        return receipt

    def ask(
        self,
        task_id,
        session_id,
        request_id,
        reason,
        message="",
        snapshot_id=None,
        target_id=None,
    ):
        """Agent command: hand the page to the person for one step."""
        service = self.service
        service._usable()
        service._active(task_id)
        if not stable_id(request_id):
            raise ValueError("Invalid request ID")
        key = (task_id, request_id)
        if key in self.requests:
            return self._receipt(self.requests[key])
        if reason not in REASONS:
            raise ValueError("reason must be one of: " + ", ".join(REASONS))
        if not isinstance(message, str):
            raise ValueError("message must be text")
        message = " ".join(message.split())[:MESSAGE_LIMIT]
        service._owned(task_id, session_id)
        if session_id in service._controls:
            raise TaskPermissionError("user_control_active")
        if any(
            e["session_id"] == session_id and e["state"] == "waiting_for_person"
            for e in self.requests.values()
        ):
            raise TaskPermissionError("user_control_active")
        service.backend.check_control_change(session_id)
        # The person has to see the page: an adopted tab of theirs, or a task
        # page shown in the watch window (which needs attach permission).
        session = service.backend._session(session_id)
        if session.borrowed_view is None:
            try:
                session.grant.check("attach")
            except TaskPermissionError:
                raise AgentRequestError(
                    "attach_needed",
                    "The person cannot see this page yet. Request attach permission "
                    "with grant.request, then ask again.",
                ) from None
        # The thing the agent means, ringed on the page before it changes hands
        # (handing over clears the agent's references).
        label = ""
        if snapshot_id is not None or target_id is not None:
            descriptor = service.backend.highlight_target(
                session_id, snapshot_id, target_id
            )
            label = " ".join((descriptor.label or descriptor.role or "").split())[:80]
        control = "handoff:" + uuid4().hex
        entry = {
            "task_id": task_id,
            "request_id": request_id,
            "session_id": session_id,
            "reason": reason,
            "message": message,
            "target": label,
            "snapshot_id": snapshot_id,
            "target_id": target_id,
            "key": control,
            "state": "waiting_for_person",
            "created": time.time(),
        }
        service.store.record_event(
            task_id,
            "handoff.requested",
            {"request_id": request_id, "session_id": session_id, "reason": reason},
        )
        service._control_event(task_id, "session.control_taken", session_id)
        service.backend.change_control(session_id, "user")
        service._controls[session_id] = control
        self.requests[key] = entry
        service.changed.emit()
        self.asked.emit(dict(entry))
        return self._receipt(entry)

    def status(self, task_id, request_id):
        self.service._usable()
        entry = self.requests.get((task_id, request_id))
        if entry is None:
            raise KeyError("Handoff request does not belong to this task")
        return self._receipt(entry)

    def waiting(self):
        return [
            dict(e)
            for e in self.requests.values()
            if e["state"] == "waiting_for_person"
        ]

    def finish(self, task_id, request_id, *, done=True):
        """Trusted UI only: the person's Done or "I can't"."""
        entry = self.requests.get((task_id, request_id))
        if entry is None or entry["state"] != "waiting_for_person":
            raise ValueError("This request is no longer waiting.")
        session_id = entry["session_id"]
        entry["finishing"] = True  # Giving control back is not the answer.
        try:
            if self.service._controls.get(session_id) == entry["key"]:
                self.service.return_control(task_id, session_id, entry["key"])
        finally:
            entry.pop("finishing")
        self._end(entry, "done" if done else "not_done")

    def _end(self, entry, state):
        if entry["state"] != "waiting_for_person":
            return
        entry["state"] = state
        try:
            self.service.store.record_event(
                entry["task_id"],
                "handoff." + state,
                {"request_id": entry["request_id"]},
            )
        except Exception:
            pass  # The task may already be gone; the receipt still says why.
        self.service.changed.emit()
        self.ended.emit(dict(entry))

    def _check(self):
        """Control given back another way (the watch window), the page
        closed or the task ended: the request is over."""
        for entry in list(self.requests.values()):
            if entry["state"] != "waiting_for_person" or entry.get("finishing"):
                continue
            session_id = entry["session_id"]
            if session_id not in self.service._sessions:
                self._end(entry, "cancelled")
            elif self.service._controls.get(session_id) != entry["key"]:
                self._end(entry, "done")

    def cancel_task(self, task_id, *, interrupted=False):
        for key in [k for k in self.requests if k[0] == task_id]:
            entry = self.requests.pop(key)
            if entry["state"] == "waiting_for_person":
                entry["state"] = "cancelled"
                self.ended.emit(dict(entry))
