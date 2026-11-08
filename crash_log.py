"""A short log of tabs whose page crashed (General → Crash Log…).

Each entry keeps the time, the site (host only; "incognito" for private
windows), why the page process ended and its exit code. Nothing about the
page's content. The newest MAX_ENTRIES are kept in <config>/crash_log.json.
"""

import time
from pathlib import Path

from utils import load_json, save_json

MAX_ENTRIES = 200
REASONS = {
    "abnormal": "The page's process ended unexpectedly.",
    "crashed": "The page's process crashed.",
    "killed": "The page's process was stopped (for example, out of memory).",
}


def path(config_dir) -> Path:
    return Path(config_dir) / "crash_log.json"


def entries(config_dir) -> list[dict]:
    raw = load_json(path(config_dir), [])
    return [
        e
        for e in (raw if isinstance(raw, list) else [])
        if isinstance(e, dict) and isinstance(e.get("time"), (int, float))
    ]


def record(config_dir, site, reason, code) -> dict:
    entry = {
        "time": time.time(),
        "site": str(site or "")[:200],
        "reason": reason if reason in REASONS else "abnormal",
        "code": int(code) if isinstance(code, int) else 0,
    }
    save_json(path(config_dir), [*entries(config_dir), entry][-MAX_ENTRIES:])
    return entry


def clear(config_dir) -> None:
    save_json(path(config_dir), [])


def describe(entry) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["time"]))
    return f"{stamp}  {entry['site'] or '(no site)'}  {REASONS[entry['reason']]}" + (
        f" (code {entry['code']})" if entry["code"] else ""
    )
