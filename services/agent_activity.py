"""What agents did on one day, from the task journal: for steed:activity.

The journal already records each step (with site origins, never page text);
this counts them per task: sites visited, pages read, actions and forms,
downloads, times an agent asked you, permissions, and the tokens and cost the
agent's own runtime reported (never an estimate).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit

READS = {"page.inspected", "page.captured", "tab_read.approved"}
OPENS = {"session.opened", "session.live_adopted", "page.navigation_finished"}
ASKED = {"grant.requested", "tab_read.requested", "signin.requested"}
APPROVED = {"grant.approved", "permission.auto_approved", "tab_read.approved"}
REFUSED = {"grant.denied", "tab_read.denied", "permission.denied"}


@dataclass
class TaskDay:
    task_id: str
    objective: str
    agent: str = ""
    first: str = ""
    last: str = ""
    sites: set = field(default_factory=set)
    pages_opened: int = 0
    pages_read: int = 0
    actions: int = 0
    forms: int = 0
    downloads: int = 0
    asked_you: int = 0
    permissions_asked: int = 0
    permissions_approved: int = 0
    permissions_refused: int = 0
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    turns: int = 0
    turns_with_cost: int = 0

    @property
    def tokens(self):
        return self.input_tokens + self.output_tokens


def day_bounds(day: date):
    """The local day as UTC ISO bounds, matching the journal's timestamps."""
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


def _host(url):
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def summarize(store, day: date, *, objectives=None):
    """Per-task activity for one local day, busiest first."""
    start, end = day_bounds(day)
    events = store.events_between(start, end)
    objectives = objectives if objectives is not None else {}
    tasks = {}
    operations = {}
    user_messages = set()
    for event in events:
        if event.task_id not in tasks:
            if event.task_id not in objectives:
                try:
                    objectives[event.task_id] = store.get(event.task_id).objective
                except KeyError:
                    objectives[event.task_id] = "Task"
            tasks[event.task_id] = TaskDay(event.task_id, objectives[event.task_id])
        day_ = tasks[event.task_id]
        day_.first = day_.first or event.created_at
        day_.last = event.created_at
        kind, payload = event.kind, event.payload
        url = payload.get("url") if isinstance(payload, dict) else None
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            host = _host(url)
            if host:
                day_.sites.add(host)
        if kind in READS:
            day_.pages_read += 1
        if kind in OPENS:
            day_.pages_opened += 1
        if kind == "page.action_requested":
            operations[payload.get("action_id")] = payload.get("operation")
        elif kind == "page.action_finished":
            day_.actions += 1
            if operations.get(payload.get("action_id")) == "submit":
                day_.forms += 1
        elif kind in ("page.click", "page.type", "page.key"):
            day_.actions += 1
        elif kind == "download.finished":
            day_.downloads += 1
        elif kind == "handoff.requested":
            day_.asked_you += 1
        elif kind == "conversation.updated":
            if payload.get("role") == "user":
                user_messages.add((event.task_id, payload.get("message_id")))
        elif kind == "agent.usage":
            day_.agent = str(payload.get("agent") or day_.agent)[:80]
            day_.turns += 1
            for name in ("input_tokens", "output_tokens", "cached_tokens"):
                value = payload.get(name)
                if isinstance(value, int) and value > 0:
                    setattr(day_, name, getattr(day_, name) + value)
            cost = payload.get("cost_usd")
            if isinstance(cost, (int, float)) and cost >= 0:
                day_.cost_usd += float(cost)
                day_.turns_with_cost += 1
        if kind in ASKED:
            day_.permissions_asked += 1
        if kind in APPROVED:
            day_.permissions_approved += 1
        if kind in REFUSED:
            day_.permissions_refused += 1
    for task_id, _ in user_messages:
        tasks[task_id].messages += 1
    return sorted(
        tasks.values(),
        key=lambda t: (-(t.actions + t.pages_read + t.messages + t.turns), t.first),
    )


def plural(count, word, many=None):
    return f"{count:,} {word if count == 1 else many or word + 's'}"


def tokens_text(count):
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M tokens"
    if count >= 10_000:
        return f"{count / 1000:.0f}k tokens"
    return plural(count, "token")


def task_line(day: TaskDay):
    """What one task did, in a sentence of short parts."""
    parts = []
    if day.messages:
        parts.append(plural(day.messages, "message") + " from you")
    if day.sites:
        shown = sorted(day.sites)
        parts.append(
            "visited "
            + ", ".join(shown[:3])
            + (f" and {len(shown) - 3} more" if len(shown) > 3 else "")
        )
    if day.pages_read:
        parts.append("read " + plural(day.pages_read, "page"))
    if day.actions:
        parts.append(
            "took "
            + plural(day.actions, "action")
            + (f" ({plural(day.forms, 'form')} sent)" if day.forms else "")
        )
    if day.downloads:
        parts.append("downloaded " + plural(day.downloads, "file"))
    if day.asked_you:
        parts.append("asked you for help " + plural(day.asked_you, "time"))
    return "; ".join(parts) or "No browser steps"


def permissions_line(day: TaskDay):
    if not (
        day.permissions_asked or day.permissions_approved or day.permissions_refused
    ):
        return ""
    parts = [f"{day.permissions_approved} allowed"]
    if day.permissions_refused:
        parts.append(f"{day.permissions_refused} refused")
    return f"Asked {plural(day.permissions_asked, 'time')}: " + ", ".join(parts)


def usage_line(day: TaskDay):
    if not day.turns:
        return ""
    text = tokens_text(day.tokens)
    if day.turns_with_cost:
        text += f", ${day.cost_usd:.2f}" + (
            " (where reported)" if day.turns_with_cost < day.turns else ""
        )
    return text


def totals_line(days):
    if not days:
        return ""
    reads = sum(d.pages_read for d in days)
    actions = sum(d.actions for d in days)
    asked = sum(d.permissions_asked for d in days)
    refused = sum(d.permissions_refused for d in days)
    tokens = sum(d.tokens for d in days)
    cost = sum(d.cost_usd for d in days)
    parts = [
        plural(len(days), "conversation or task", "conversations and tasks"),
        "read " + plural(reads, "page"),
        "took " + plural(actions, "action"),
    ]
    if asked:
        parts.append(
            f"asked for permission {plural(asked, 'time')}"
            + (f" ({refused} refused)" if refused else "")
        )
    if tokens:
        parts.append(
            "used "
            + tokens_text(tokens)
            + (
                f" (${cost:.2f} reported)"
                if any(d.turns_with_cost for d in days)
                else ""
            )
        )
    return parts[0] + ": " + ", ".join(parts[1:]) + "."
