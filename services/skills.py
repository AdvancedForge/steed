"""Skills: things you ask your assistant often, kept and run in one go.

A skill is a name and what to ask ("Summarize this page in five bullets",
"Draft a polite reply to this email"), with what it looks at: the page you
are on, the tabs you @-mention, or nothing. It can also carry saved steps
(see routine_steps) that Steed replays itself first, with no model, then
hands what they read to your assistant. Run one from Agents → Skills, the
command palette, or by typing / in the agent chat. Kept in skills.json.
"""

import json
import time
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal

from browser.utils import save_json
from services.routine_steps import check_steps

MAX_SKILLS = 60
CONTEXTS = ("page", "none")

STARTERS = (
    {
        "name": "Summarize this page",
        "ask": "Summarize this page in five short bullets, then say what, if "
        "anything, I should do about it.",
        "context": "page",
    },
    {
        "name": "Explain simply",
        "ask": "Explain what this page is about as if to a smart friend new to "
        "the subject: short, plain words, one example.",
        "context": "page",
    },
    {
        "name": "Draft a reply",
        "ask": "Draft a short, polite reply to the message on this page. Give me "
        "the text only, ready to paste.",
        "context": "page",
    },
    {
        "name": "Compare options",
        "ask": "Compare the options on this page in a small table (price, what "
        "you get, catches), then recommend one in a sentence.",
        "context": "page",
    },
)


def check(skill):
    """A skill as entered; ValueError says what is wrong."""
    if not isinstance(skill, dict):
        raise ValueError("Describe the skill with a name and what to ask.")
    name = " ".join(str(skill.get("name", "")).split())[:60]
    ask = str(skill.get("ask", "")).strip()[:4000]
    if not name or not ask:
        raise ValueError("Give the skill a name and say what to ask.")
    if name.startswith("/"):
        name = name.lstrip("/").strip()
    context = skill.get("context", "page")
    if context not in CONTEXTS:
        raise ValueError("Choose what the skill looks at.")
    found = {"name": name, "ask": ask, "context": context}
    steps = check_steps(skill.get("steps"))
    if steps:
        found["steps"] = steps
    return found


def message(skill, page=None, read=(), stopped=None):
    """What goes to the assistant: the ask, then what it looks at."""
    parts = [f"Skill “{skill['name']}”: {skill['ask']}"]
    if page is not None:
        title, url, text = page
        parts.append(f"[This page: {title[:160]} — {url[:400]}]\n{text}")
    for title, url, text in read:
        parts.append(
            f"[Read by the skill's steps: {title[:160]} — {url[:400]}]\n{text}"
        )
    if stopped is not None:
        from services.routine_steps import REASONS, describe_step

        number, step, reason = stopped
        parts.append(
            f"(The skill's saved steps stopped at step {number}, "
            f"“{describe_step(step)}”: {REASONS.get(reason, reason)}. Do this part "
            "yourself if you can.)"
        )
    return "\n\n".join(parts)


class Skills(QObject):
    changed = pyqtSignal()

    def __init__(self, config_dir, parent=None):
        super().__init__(parent)
        self.path = Path(config_dir) / "skills.json"
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raw = None
        if raw is None:  # First time: a few to start from, all editable.
            raw = [dict(s, id=uuid4().hex, created=time.time()) for s in STARTERS]
            self.skills = raw
            self._save()
        self.skills = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                skill = check(entry)
            except ValueError:
                continue
            skill["id"] = str(entry.get("id") or uuid4().hex)[:32]
            self.skills.append(skill)
        self.skills = self.skills[:MAX_SKILLS]

    def _save(self):
        save_json(self.path, self.skills)
        self.changed.emit()

    def list(self):
        return [dict(s) for s in self.skills]

    def get(self, skill_id):
        return next((dict(s) for s in self.skills if s["id"] == skill_id), None)

    def find(self, name):
        """By name, as typed after / (case and spaces don't matter)."""
        wanted = " ".join(str(name).lower().split())
        return next((dict(s) for s in self.skills if s["name"].lower() == wanted), None)

    def add(self, skill):
        skill = check(skill)
        if len(self.skills) >= MAX_SKILLS:
            raise ValueError(f"Keep at most {MAX_SKILLS} skills.")
        if self.find(skill["name"]):
            raise ValueError("A skill has that name already.")
        skill["id"] = uuid4().hex
        self.skills.append(skill)
        self._save()
        return dict(skill)

    def update(self, skill_id, skill):
        skill = check(skill)
        for index, current in enumerate(self.skills):
            if current["id"] == skill_id:
                other = self.find(skill["name"])
                if other and other["id"] != skill_id:
                    raise ValueError("A skill has that name already.")
                skill["id"] = skill_id
                self.skills[index] = skill
                self._save()
                return dict(skill)
        raise ValueError("That skill is gone.")

    def remove(self, skill_id):
        self.skills = [s for s in self.skills if s["id"] != skill_id]
        self._save()
