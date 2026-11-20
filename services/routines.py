"""Routines: your agent does something on a schedule.

"Every weekday at 8:00, read my inbox and calendar and tell me what matters."
A routine has what to ask, when (daily at a time on chosen days, or every few
hours), and optionally pages to read first: Steed opens each in the
background with the profile's sign-ins and puts its text in the message, so
the agent needs no browser access for them. Each run starts a new
conversation in the agent sidebar (Agents → Routines… lists them); the
agent's reply raises the usual notification. Anything more the agent wants to
do still asks for your permission. A run missed while Steed was closed
happens once when Steed next starts, if it is within a day.

Kept in <config>/routines.json.
"""

import time
from datetime import datetime, timedelta
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from browser import webengine
from browser.utils import load_json, save_json

TICK_MS = 30000
MAX_ROUTINES = 30
MAX_PAGES = 5
PAGE_TEXT = 6000
READ_TIMEOUT_MS = 45000
SETTLE_MS = 2500
CATCH_UP = 24 * 3600
EVERY_HOURS = (1, 2, 3, 4, 6, 8, 12)
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def check(routine):
    """A routine from what you entered; ValueError says what is wrong."""
    name = " ".join(str(routine.get("name", "")).split())[:80]
    ask = str(routine.get("ask", "")).strip()[:4000]
    if not name or not ask:
        raise ValueError("Give the routine a name and say what to ask.")
    kind = routine.get("kind", "daily")
    found = {"name": name, "ask": ask, "kind": kind}
    if kind == "daily":
        at = str(routine.get("at", "08:00"))
        try:
            hour, minute = (int(n) for n in at.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except ValueError:
            raise ValueError("Write the time as hours:minutes, like 08:30.") from None
        try:
            raw = list(routine.get("days", range(7)))[:7]
            days = sorted({int(d) for d in raw if 0 <= int(d) < 7})
        except (TypeError, ValueError):
            raise ValueError(
                "Days are numbers from 0 (Monday) to 6 (Sunday)."
            ) from None
        if not days:
            raise ValueError("Choose at least one day.")
        found.update(at=f"{hour:02d}:{minute:02d}", days=days)
    elif kind == "hours":
        try:
            every = int(routine.get("every", 4))
        except (TypeError, ValueError):
            every = 0
        if every not in EVERY_HOURS:
            raise ValueError("Choose how many hours apart.")
        found["every"] = every
    else:
        raise ValueError("Choose when the routine runs.")
    pages = []
    listed = routine.get("pages", [])
    if not isinstance(listed, list):
        raise ValueError("Pages are a list of web addresses.")
    if len(listed) > MAX_PAGES:
        raise ValueError(f"A routine reads at most {MAX_PAGES} pages.")
    for page in listed:
        url = QUrl(str(page).strip())
        if url.scheme() in ("http", "https") and url.host():
            pages.append(url.toString())
        elif str(page).strip():
            raise ValueError(f"Not a web address: {page}")
    found["pages"] = pages
    return found


def last_due(routine, now: datetime):
    """The latest time at or before now the routine was meant to run."""
    if routine["kind"] == "hours":
        created = datetime.fromtimestamp(routine.get("created", now.timestamp()))
        period = timedelta(hours=routine["every"])
        steps = (now - created) // period
        return created + steps * period if steps >= 1 else None
    hour, minute = (int(n) for n in routine["at"].split(":"))
    for back in range(8):
        day = (now - timedelta(days=back)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if day <= now and day.weekday() in routine["days"]:
            return day
    return None


def describe(routine):
    if routine["kind"] == "hours":
        return f"Every {routine['every']} hour{'s' * (routine['every'] != 1)}"
    days = routine["days"]
    which = (
        "Every day"
        if len(days) == 7
        else "Weekdays"
        if days == [0, 1, 2, 3, 4]
        else ", ".join(DAYS[d] for d in days)
    )
    return f"{which} at {routine['at']}"


# The page's text, and whether it asks for a sign-in instead.
READ_SCRIPT = """(() => ({
  text: document.body ? document.body.innerText : "",
  url: location.href,
  password: [...document.querySelectorAll("input[type=password]")]
    .some(field => field.getClientRects().length > 0),
}))()"""
SIGNED_OUT = (
    "(this page asked you to sign in; Steed told you, so say nothing more about it)"
)


def asks_to_sign_in(url, found):
    """Sent to a sign-in address, or showing a password field."""
    from services.watches import SIGN_IN

    landed = QUrl(found.get("url") or "")
    asked = QUrl(url)
    moved = landed.isValid() and (
        landed.host() != asked.host() or landed.path() != asked.path()
    )
    return bool(found.get("password")) or (
        moved and bool(SIGN_IN.search(landed.host() + landed.path()))
    )


def message(routine, pages):
    """What the agent gets: the routine's ask, and the pages' text."""
    parts = [f"Routine “{routine['name']}”: {routine['ask']}"]
    for title, url, text in pages:
        body = " ".join((text or "(could not be read)").split())[:PAGE_TEXT]
        parts.append(f"### {title or url}\n{url}\n\n{body}")
    return "\n\n".join(parts)


class Routines(QObject):
    changed = pyqtSignal()
    ran = pyqtSignal(dict, str)  # routine, message for the agent
    needs_you = pyqtSignal(dict, str)  # routine, a page that wants a sign-in

    def __init__(self, registry):
        super().__init__(registry)
        self.registry = registry
        self.path = registry.config.config_dir / "routines.json"
        raw = load_json(self.path, [])
        self.routines = (
            [r for r in raw if isinstance(r, dict) and "id" in r][:MAX_ROUTINES]
            if isinstance(raw, list)
            else []
        )
        self.running = None  # (routine, [pages read], pending urls, page)
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QTimer.singleShot(8000, self.tick)

    def list(self):
        return [dict(r) for r in self.routines]

    def _save(self):
        save_json(self.path, self.routines)
        self.changed.emit()

    def add(self, routine, profile="global"):
        if profile == "search":
            raise ValueError("Routines do not run in incognito.")
        if len(self.routines) >= MAX_ROUTINES:
            raise ValueError(f"Keep at most {MAX_ROUTINES} routines.")
        found = check(routine)
        found.update(
            id=uuid4().hex,
            profile=str(profile),
            created=time.time(),
            last_run=time.time(),  # Starts with its next time, not now.
            paused=False,
        )
        self.routines.append(found)
        self._save()
        return dict(found)

    def update(self, routine_id, routine):
        for item in self.routines:
            if item["id"] == routine_id:
                item.update(check(routine))
                self._save()
                return dict(item)
        raise KeyError(routine_id)

    def remove(self, routine_id):
        self.routines = [r for r in self.routines if r["id"] != routine_id]
        self._save()

    def pause(self, routine_id, paused=True):
        for item in self.routines:
            if item["id"] == routine_id:
                item["paused"] = bool(paused)
                if not paused:
                    item["last_run"] = time.time()
        self._save()

    def due(self, now=None):
        now = now or datetime.now()
        for routine in self.routines:
            if routine.get("paused"):
                continue
            when = last_due(routine, now)
            if (
                when is not None
                and when.timestamp() > routine.get("last_run", 0)
                and now.timestamp() - when.timestamp() <= CATCH_UP
            ):
                return routine
        return None

    def tick(self):
        if self.closed or self.running is not None:
            return
        tasks = getattr(self.registry, "_tasks", None)
        if tasks is not None and getattr(tasks, "agents_paused", False):
            return  # Paused agents: a due routine runs after you resume.
        routine = self.due()
        if routine is not None:
            self.run(routine)

    def run(self, routine):
        """Run now: read its pages, then hand the message to the agent."""
        if self.running is not None:
            return False
        routine = next((r for r in self.routines if r["id"] == routine["id"]), routine)
        routine["last_run"] = time.time()
        self._save()
        self.running = [routine, [], list(routine.get("pages", [])), None]
        self._next_page()
        return True

    def _next_page(self):
        routine, read, pending, _page = self.running
        if not pending:
            self.running = None
            self.ran.emit(dict(routine), message(routine, read))
            return
        url = pending.pop(0)
        try:
            profile = self.registry.get(routine["profile"])
        except (ValueError, OSError):
            read.append((url, url, ""))
            self._next_page()
            return
        page = webengine.background_page(profile, self)
        self.running[3] = page
        done = []

        def finish(found):
            if done or self.closed or self.running is None:
                return
            done.append(True)
            title = page.title() if not sip.isdeleted(page) else url
            found = found if isinstance(found, dict) else {}
            text = found.get("text") if isinstance(found.get("text"), str) else ""
            if asks_to_sign_in(url, found):
                # Hand it to you rather than the agent: it can't sign in here.
                text = SIGNED_OUT
                self.needs_you.emit(dict(routine), url)
            read.append((title, url, text))
            if not sip.isdeleted(page):
                page.deleteLater()
            self.running[3] = None
            self._next_page()

        def loaded(ok):
            if not ok:
                finish({})
                return
            QTimer.singleShot(
                SETTLE_MS,
                lambda: (
                    not sip.isdeleted(page)
                    and webengine.run_script(
                        page,
                        READ_SCRIPT,
                        finish,
                        world=webengine.STEED_WORLD,
                    )
                ),
            )

        page.loadFinished.connect(loaded)
        QTimer.singleShot(READ_TIMEOUT_MS, lambda: finish({}))
        page.load(QUrl(url))

    def close(self):
        """Before the profiles go."""
        self.closed = True
        self.timer.stop()
        if self.running is not None and self.running[3] is not None:
            page = self.running[3]
            if not sip.isdeleted(page):
                sip.delete(page)
        self.running = None
