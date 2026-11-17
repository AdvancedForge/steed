"""Watches: Steed keeps an eye on a page for you, and your agent judges.

You pick a page (or a part of it, by selecting it) and say what matters in
your own words: "tell me when it's under $50", "when grades are posted", or
just any change. Steed reads it in the background on a schedule, in its
profile (so your sign-ins apply), and compares the text itself. Only when the
text changed, and you said what matters, does it ask your agent (Claude Code
or Codex, run once from its command line, with no browser access) whether
this change is what you asked about. Then a desktop notification.

Watches are kept in <config>/watches.json; never for incognito. The page
text is kept only as the last version seen (bounded), to compare with.
"""

import difflib
import json
import re
import time
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from browser import webengine
from browser.utils import load_json, save_json
from services.agent_once import AgentOnce, agent_command

INTERVALS = {"15m": 900, "hourly": 3600, "6h": 6 * 3600, "daily": 24 * 3600}
# What happens to a "then" action: start it from the notification (ask) or
# at once (now). Either way the agent still asks before it acts on a site.
THEN_MODES = ("ask", "now")
INTERVAL_LABELS = {
    "15m": "Every 15 minutes",
    "hourly": "Every hour",
    "6h": "Every 6 hours",
    "daily": "Every day",
}
MAX_WATCHES = 50
MAX_TEXT = 8000
LOAD_TIMEOUT_MS = 45000
SETTLE_MS = 2500
JUDGE_TIMEOUT_MS = 120000
TICK_MS = 60000

# Reads the watched part (a CSS path from the selection) or the page's text,
# where the page ended up, and whether it asks for a password.
EXTRACT = r"""
(function (selector) {
  let found = null;
  if (selector) { try { found = document.querySelector(selector); } catch (error) {} }
  const source = found || (selector ? null : document.body);
  const password = [...document.querySelectorAll("input[type=password]")]
    .some((field) => field.offsetParent !== null);
  return {text: source ? source.innerText : null, password, url: location.href};
})(%s)
"""
# Addresses that are sign-in pages.
SIGN_IN = re.compile(
    r"(^|[/._-])(log-?in|sign-?in|auth|sso|oauth|session|account/login|cas)([/._?-]|$)",
    re.IGNORECASE,
)

# Where the selection is: a CSS path to the element that holds it.
SELECTION_PATH = r"""
(function () {
  const selection = getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  let node = selection.getRangeAt(0).commonAncestorContainer;
  if (node.nodeType !== 1) node = node.parentElement;
  const parts = [];
  while (node && node.nodeType === 1 && node !== document.body && parts.length < 8) {
    if (node.id && /^[A-Za-z][\w-]*$/.test(node.id)) { parts.unshift("#" + node.id); break; }
    let index = 1, sibling = node;
    while ((sibling = sibling.previousElementSibling)) if (sibling.tagName === node.tagName) index++;
    parts.unshift(node.tagName.toLowerCase() + ":nth-of-type(" + index + ")");
    node = node.parentElement;
  }
  if (!parts.length) return null;
  const path = (parts[0].startsWith("#") ? "" : "body > ") + parts.join(" > ");
  const target = document.querySelector(path);
  return target ? {selector: path, text: target.innerText.slice(0, 8000)} : null;
})()
"""

PROMPT = """You are helping someone keep an eye on a web page.
They asked: "{instruction}"

Page: {title} ({url})
Before:
<<<
{before}
>>>
Now:
<<<
{after}
>>>

Does this change match what they asked about? Answer with exactly one line:
NOTIFY: <one short sentence for a desktop notification, saying what happened>
or
QUIET
"""


def clean_text(text):
    if not isinstance(text, str):
        return ""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_TEXT]


def change_summary(before, after):
    """A short "what changed" line for a notification."""
    old, new = before.split(), after.split()
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = " ".join(old[i1:i2])[:60]
        added = " ".join(new[j1:j2])[:60]
        if removed and added:
            return f"“{removed}” is now “{added}”."
        if added:
            return f"New: “{added}”."
        return f"Gone: “{removed}”."
    return "The page changed."


class WatchService(QObject):
    """Watches for every window of this registry; one check at a time."""

    changed = pyqtSignal()
    fired = pyqtSignal(dict, str)  # the watch, what to say
    signed_out = pyqtSignal(dict)  # the watch, which now meets a sign-in page

    def __init__(self, registry):
        super().__init__(registry)
        self.registry = registry
        self.path = registry.config.config_dir / "watches.json"
        raw = load_json(self.path, [])
        self.watches = (
            [w for w in raw if isinstance(w, dict) and "id" in w][:MAX_WATCHES]
            if isinstance(raw, list)
            else []
        )
        self.page = None
        self.current = None
        self.judge = None
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.load_timer = QTimer(self, singleShot=True, interval=LOAD_TIMEOUT_MS)
        self.load_timer.timeout.connect(lambda: self._read_failed("timeout"))
        QTimer.singleShot(5000, self.tick)

    # ----- The list -----

    def list(self):
        return [dict(w) for w in self.watches]

    def add(
        self,
        url,
        profile,
        title,
        instruction="",
        selector=None,
        text="",
        every="hourly",
        then="",
        then_mode="ask",
    ):
        if profile == "search":
            raise ValueError("Incognito pages are not watched.")
        found = QUrl(url)
        if found.scheme() not in ("http", "https") or not found.host():
            raise ValueError("Only web pages can be watched.")
        if every not in INTERVALS:
            raise ValueError("Choose how often to look.")
        if then_mode not in THEN_MODES:
            raise ValueError("Choose when the agent acts.")
        if len(self.watches) >= MAX_WATCHES:
            raise ValueError(f"Keep at most {MAX_WATCHES} watches.")
        watch = {
            "id": uuid4().hex,
            "url": found.toString(),
            "profile": str(profile),
            "title": str(title or found.host())[:200],
            "instruction": str(instruction or "").strip()[:400],
            "selector": selector
            if isinstance(selector, str) and len(selector) < 1000
            else None,
            "every": every,
            "then": " ".join(str(then or "").split())[:400],
            "then_mode": then_mode,
            "created": time.time(),
            "last_checked": time.time() if text else 0.0,
            "last_text": clean_text(text),
            "last_change": 0.0,
            "last_summary": "",
            "paused": False,
            "failures": 0,
        }
        self.watches.append(watch)
        self._save()
        return dict(watch)

    def remove(self, watch_id):
        self.watches = [w for w in self.watches if w["id"] != watch_id]
        self._save()

    def pause(self, watch_id, paused=True):
        for watch in self.watches:
            if watch["id"] == watch_id:
                watch["paused"] = bool(paused)
        self._save()

    def check_now(self, watch_id):
        for watch in self.watches:
            if watch["id"] == watch_id:
                watch["last_checked"] = 0.0
        self.tick()

    def _save(self):
        save_json(self.path, self.watches)
        self.changed.emit()

    # ----- Looking -----

    def due(self, now=None):
        now = now or time.time()
        waiting = [
            w
            for w in self.watches
            if not w["paused"]
            and now - w["last_checked"] >= INTERVALS.get(w["every"], 3600)
        ]
        return min(waiting, key=lambda w: w["last_checked"], default=None)

    def tick(self):
        if self.closed or self.current is not None:
            return
        watch = self.due()
        if watch is None:
            return
        try:
            profile = self.registry.get(watch["profile"])
        except (ValueError, OSError):
            watch["paused"] = True  # Its profile is gone.
            self._save()
            return
        self.current = watch
        watch["last_checked"] = time.time()
        self.page = webengine.background_page(profile, self)
        self.page.loadFinished.connect(self._loaded)
        self.load_timer.start()
        self.page.load(QUrl(watch["url"]))

    def _loaded(self, ok):
        if self.current is None:
            return
        if not ok:
            self._read_failed("load")
            return
        # Let late content (prices, replies) arrive.
        QTimer.singleShot(SETTLE_MS, self._read)

    def _read(self):
        if self.current is None or self.page is None:
            return
        webengine.run_script(
            self.page,
            EXTRACT % json.dumps(self.current["selector"]),
            self._got,
            world=webengine.STEED_WORLD,
        )

    def _got(self, found):
        watch = self.current
        self._end_page()
        if watch is None or self.closed:
            return
        found = found if isinstance(found, dict) else {}
        if self._signed_out(watch, found):
            # The page wants a sign-in: keep what was seen, say so once.
            watch["failures"] = 0
            first = not watch.get("signed_out")
            watch["signed_out"] = True
            self._save()
            if first:
                self.signed_out.emit(dict(watch))
            self._next()
            return
        text = found.get("text")
        if not isinstance(text, str):
            self._failure(watch)
            return
        if watch.get("signed_out"):
            watch["signed_out"] = False
        watch.setdefault("password", bool(found.get("password")))
        text = clean_text(text)
        watch["failures"] = 0
        before, watch["last_text"] = watch["last_text"], text
        if not before or before == text:
            self._save()
            self._next()
            return
        watch["last_change"] = time.time()
        self._save()
        if (
            watch["instruction"]
            and not _any_change(watch["instruction"])
            and self.agent()
        ):
            self._ask_agent(watch, before, text)
            return
        self._fire(watch, change_summary(before, text))

    @staticmethod
    def _signed_out(watch, found):
        """Sent to a sign-in page, or a password field that was not there before."""
        landed = QUrl(found.get("url") or "")
        watched = QUrl(watch["url"])
        if landed.isValid() and (
            landed.host() != watched.host() or landed.path() != watched.path()
        ):
            if SIGN_IN.search(landed.host() + landed.path()):
                return True
        if found.get("password") and watch.get("password") is False:
            return True
        return False

    def _read_failed(self, why):
        watch = self.current
        self._end_page()
        if watch is not None:
            self._failure(watch)

    def _failure(self, watch):
        watch["failures"] = watch.get("failures", 0) + 1
        if watch["failures"] >= 5:
            watch["paused"] = True  # Five misses in a row: stop and show it.
        self._save()
        self._next()

    def _end_page(self):
        self.load_timer.stop()
        page, self.page = self.page, None
        if page is not None and not sip.isdeleted(page):
            page.loadFinished.disconnect()
            page.deleteLater()

    def _next(self):
        self.current = None
        QTimer.singleShot(1000, self.tick)

    def _fire(self, watch, summary):
        watch["last_summary"] = summary
        self._save()
        self.fired.emit(dict(watch), summary)
        self._next()

    # ----- Asking the agent -----

    def agent(self):
        """The agent command line to ask, or None."""
        return agent_command(self.registry.config)

    def _ask_agent(self, watch, before, after):
        prompt = PROMPT.format(
            instruction=watch["instruction"],
            title=watch["title"],
            url=watch["url"],
            before=before[:3500],
            after=after[:3500],
        )
        judge = self.judge = AgentOnce(
            self.agent(), prompt, self, timeout_ms=JUDGE_TIMEOUT_MS
        )

        def finished(reply):
            self.judge = None
            judge.deleteLater()
            if self.closed:
                return
            verdict = verdict_of(reply)
            if verdict is None:  # No clear answer: say what changed instead.
                self._fire(watch, change_summary(before, after))
            elif verdict:
                self._fire(watch, verdict)
            else:
                self._next()

        judge.answered.connect(finished)
        judge.failed.connect(lambda _why: finished(""))

    def close(self):
        """Steed is closing: before its profiles go."""
        self.closed = True
        self.timer.stop()
        self._end_page()
        if self.judge is not None:
            self.judge.stop()
        self.current = None


def _any_change(instruction):
    return instruction.strip().lower() in ("", "any change", "anything", "any changes")


def verdict_of(reply):
    """ "summary" to notify, "" to stay quiet, None when the reply is unclear."""
    for line in reply.splitlines():
        line = line.strip().strip("*").strip()
        match = re.match(r"(?i)^notify\s*:\s*(.+)$", line)
        if match:
            return match.group(1).strip()[:200]
        if re.match(r"(?i)^quiet\b", line):
            return ""
    return None


def then_prompt(watch, summary):
    """The agent's task when a watch with a "then" action fires."""
    return (
        f"A watch on “{watch['title']}” ({watch['url']}) just fired: {summary}\n\n"
        f"When this happens, I asked you to: {watch['then']}\n\n"
        "Open the page and do it. Ask me for any access you need, and tell me "
        "what you did."
    )
