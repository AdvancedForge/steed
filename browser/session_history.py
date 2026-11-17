"""A rolling history of a profile's open windows, to roll back to.

Snapshots record every window of the profile (only when something changed);
closed-window entries record a window closed while others stay open. Both live
in <profile>/session_history.json. Incognito keeps none.
"""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from browser.tab_manager import GROUP_COLORS, TabState, _parse_states
from browser.utils import load_json, save_json

MAX_SNAPSHOTS = 48
MAX_CLOSED_WINDOWS = 20
MAX_WINDOWS = 16
MAX_TABS = 500
KINDS = ("snapshot", "closed_window")


def _fingerprint(windows):
    return [[(s.url, s.title, s.group_id) for s in window] for window in windows]


def _groups(raw):
    groups = []
    for item in raw if isinstance(raw, list) else []:
        if (
            isinstance(item, dict)
            and isinstance(item.get("group_id"), str)
            and isinstance(item.get("name"), str)
            and item["name"].strip()
        ):
            color = item.get("color", "")
            groups.append(
                {
                    "group_id": item["group_id"],
                    "name": item["name"][:100],
                    "color": color if color in GROUP_COLORS else "",
                    "collapsed": item.get("collapsed") is True,
                }
            )
    return groups


def _entry(item):
    if not (
        isinstance(item, dict)
        and item.get("kind") in KINDS
        and isinstance(item.get("id"), str)
        and isinstance(item.get("windows"), list)
    ):
        return None
    windows = [
        states
        for states in (_parse_states(w) for w in item["windows"][:MAX_WINDOWS])
        if states
    ]
    if not windows:
        return None
    return {
        "id": item["id"],
        "kind": item["kind"],
        "saved_at": str(item.get("saved_at", ""))[:40],
        "windows": windows,
        "groups": _groups(item.get("groups")),
    }


class SessionHistory:
    def __init__(self, profile_dir: Path):
        self.private = str(profile_dir) == "."
        self.path = Path(profile_dir) / "session_history.json"

    def entries(self) -> list[dict]:
        """Newest first; windows are lists of TabState."""
        if self.private:
            return []
        raw = load_json(self.path, [])
        found = [_entry(item) for item in raw if isinstance(raw, list)]
        return [entry for entry in found if entry is not None]

    def record(self, kind, windows, groups=()) -> dict | None:
        """Add an entry; a snapshot identical to the last one is skipped."""
        if self.private or kind not in KINDS:
            return None
        windows = [list(w)[:MAX_TABS] for w in windows if w][:MAX_WINDOWS]
        if not windows:
            return None
        entries = self.entries()
        if kind == "snapshot":
            last = next((e for e in entries if e["kind"] == "snapshot"), None)
            if last is not None and _fingerprint(last["windows"]) == _fingerprint(
                windows
            ):
                return None
        entry = {
            "id": uuid4().hex,
            "kind": kind,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "windows": windows,
            "groups": _groups(list(groups)),
        }
        entries.insert(0, entry)
        limits = {"snapshot": MAX_SNAPSHOTS, "closed_window": MAX_CLOSED_WINDOWS}
        kept, counts = [], dict.fromkeys(KINDS, 0)
        for item in entries:
            counts[item["kind"]] += 1
            if counts[item["kind"]] <= limits[item["kind"]]:
                kept.append(item)
        self._write(kept)
        return entry

    def remove(self, entry_id) -> dict | None:
        entries = self.entries()
        found = next((e for e in entries if e["id"] == entry_id), None)
        if found is not None:
            self._write([e for e in entries if e is not found])
        return found

    def _write(self, entries):
        save_json(
            self.path,
            [
                {
                    **entry,
                    "windows": [
                        [asdict(s) if isinstance(s, TabState) else s for s in window]
                        for window in entry["windows"]
                    ],
                }
                for entry in entries
            ],
        )


def describe(entry, now=None) -> str:
    """ "Today 14:30 · 2 windows, 17 tabs" in local time."""
    try:
        when = datetime.fromisoformat(entry["saved_at"]).astimezone()
    except ValueError:
        when = None
    now = now or datetime.now().astimezone()
    if when is None:
        stamp = "Unknown time"
    elif when.date() == now.date():
        stamp = "Today " + when.strftime("%H:%M")
    elif (now.date() - when.date()).days == 1:
        stamp = "Yesterday " + when.strftime("%H:%M")
    else:
        stamp = when.strftime("%a %b %d %H:%M")
    windows = len(entry["windows"])
    tabs = sum(len(w) for w in entry["windows"])
    label = "Closed window" if entry["kind"] == "closed_window" else "Session"
    count = (
        f"{tabs} tab{'s' * (tabs != 1)}"
        if windows == 1
        else f"{windows} windows, {tabs} tabs"
    )
    return f"{stamp} · {label} · {count}"
