"""Replay what an agent did: its steps in plain words, with a picture of the
page after each one when you turned that on.

Settings → Agents → "Keep a picture of each agent step" (config
"agent_replay", off by default). Pictures are taken only of pages on screen
(a tab you lent the agent, or a page shown with session.watch): Steed never
renders a hidden page just to photograph it. They stay on this computer, in
task_replays/<task> under Steed's settings, readable only by you, and are
deleted with Delete pictures or when the task is removed. The journal itself
never holds page text; the pictures are the only record of what pages showed.
"""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer

PICTURES_PER_TASK = 300
WIDTH = 960
SETTLE_MS = 400
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

# What each step was, in words.
OPERATIONS = {
    "enter": "Typed into a field",
    "activate": "Clicked",
    "select": "Chose an option",
    "adjust": "Adjusted a control",
    "hover": "Pointed at something",
    "drag": "Dragged something",
    "scroll": "Scrolled",
    "submit": "Sent a form",
    "upload": "Chose a file to upload",
}
STEPS = {
    "session.opened": "Opened a page",
    "session.navigation_requested": "Went to {url}",
    "page.navigation_finished": "Followed a link to {url}",
    "page.inspected": "Read the page",
    "page.captured": "Looked at the screen",
    "page.action_failed": "Tried something that did not work ({reason})",
    "session.control_taken": "You took control",
    "session.control_returned": "You gave control back",
    "session.closed": "Closed a page",
    "signin.filled": "Signed in with your saved login",
    "permission.denied": "Was refused: {reason}",
    "agents.paused": "Agents were paused",
    "download.finished": "Downloaded a file",
}
# After these, the page is photographed (when pictures are on).
# No picture while a filled password or card field is showing.
SECRETS_SHOWN = r"""
(() => [...document.querySelectorAll("input")].some(el => el.value &&
    el.getClientRects().length && (el.type === "password" ||
    (el.getAttribute("type") || "").toLowerCase() === "password" ||
    (el.getAttribute("autocomplete") || "").toLowerCase().includes("cc-") ||
    /card.?(number|num)|cvv|cvc|security.?code/i.test(el.name + " " + el.id))))()
"""
PICTURE_AFTER = frozenset(
    {"page.action_finished", "page.navigation_finished", "session.opened"}
)


def _words(value):
    return str(value or "").replace("_", " ")[:120]


def describe(event, operations):
    """A plain sentence for a journal event, or None for bookkeeping."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.kind == "page.action_finished":
        operation = operations.get(payload.get("action_id"))
        return OPERATIONS.get(operation, "Did something on the page")
    if event.kind == "agents.paused":
        return "Agents were paused" if payload.get("paused") else "Agents resumed"
    template = STEPS.get(event.kind)
    if template is None:
        return None
    return template.format(
        url=_words(payload.get("url")) or "a page",
        reason=_words(payload.get("reason")) or "no reason given",
    )


class TaskReplay(QObject):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service

    def _config(self):
        profiles = getattr(self.service, "profiles", None)
        return getattr(profiles, "config", None)

    def enabled(self):
        config = self._config()
        return bool(config is not None and config.get("agent_replay", False))

    def folder(self, task_id=None):
        config = self._config()
        base = getattr(config, "config_dir", None)
        if base is None:
            return None
        root = Path(base) / "task_replays"
        if task_id is None:
            return root
        if not TASK_ID.fullmatch(task_id):
            raise ValueError("Invalid task")
        return root / task_id

    # ---------- Taking pictures ----------

    def recorded(self, task_id, kind, payload):
        """Called after each journal event; photographs the page when due."""
        if kind not in PICTURE_AFTER or not self.enabled():
            return
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        if not isinstance(session_id, str):
            return
        events = self.service.store.recent_events(task_id, limit=1)
        if not events:
            return
        sequence = events[-1].sequence
        QTimer.singleShot(SETTLE_MS, lambda: self._snap(task_id, session_id, sequence))

    def _view(self, session_id):
        backend = self.service.backend
        session = getattr(backend, "_sessions", {}).get(session_id)
        if session is None or getattr(session, "control_owner", "agent") != "agent":
            return None  # While you have control, what you do is yours.
        views = [session.borrowed_view, *getattr(session, "views", ())]
        return next(
            (
                view
                for view in views
                if view is not None and not sip.isdeleted(view) and view.isVisible()
            ),
            None,
        )

    def _snap(self, task_id, session_id, sequence):
        if self.service._closed or self.service.failure or not self.enabled():
            return
        view = self._view(session_id)
        if view is None:
            return
        from browser import webengine

        webengine.run_script(
            view.page(),
            SECRETS_SHOWN,
            lambda shown: shown is False and self._keep(task_id, session_id, sequence),
            world=webengine.STEED_WORLD,
        )

    def _keep(self, task_id, session_id, sequence):
        view = self._view(session_id)
        folder = self.folder(task_id)
        if view is None or folder is None or self.service._closed:
            return
        try:
            folder.parent.mkdir(mode=0o700, exist_ok=True)
            os.chmod(folder.parent, 0o700)
            folder.mkdir(mode=0o700, exist_ok=True)
            if len(list(folder.glob("*.jpg"))) >= PICTURES_PER_TASK:
                return
            image = view.grab().toImage()
            if image.isNull() or image.width() < 2:
                return
            if image.width() > WIDTH:
                image = image.scaledToWidth(
                    WIDTH, Qt.TransformationMode.SmoothTransformation
                )
            path = folder / f"{sequence:010d}.jpg"
            partial = path.with_suffix(".part")
            if not image.save(str(partial), "JPEG", 72):
                partial.unlink(missing_ok=True)
                return
            os.chmod(partial, 0o600)
            partial.replace(path)
        except OSError:
            return

    # ---------- Reading back ----------

    def pictures(self, task_id):
        """{journal sequence: picture path} for a task."""
        folder = self.folder(task_id)
        if folder is None or not folder.is_dir():
            return {}
        found = {}
        for path in folder.glob("*.jpg"):
            if path.stem.isdigit():
                found[int(path.stem)] = path
        return found

    def steps(self, task_id, limit=1000):
        """[(time, sentence, picture path or None)], oldest first."""
        events = self.service.store.recent_events(task_id, limit=limit)
        pictures = self.pictures(task_id)
        operations, steps = {}, []
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.kind == "page.action_requested":
                operations[payload.get("action_id")] = payload.get("operation")
                continue
            sentence = describe(event, operations)
            if sentence is None:
                continue
            try:
                when = datetime.fromisoformat(event.created_at).strftime("%H:%M:%S")
            except ValueError:
                when = event.created_at[11:19]
            steps.append((when, sentence, pictures.get(event.sequence)))
        return steps

    def forget(self, task_id):
        folder = self.folder(task_id)
        if folder is not None and folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
