"""Memory modes: presets over individual memory/performance optimizations.

Each optimization is its own config key, so steed:memory can adjust one at a
time. Choosing a mode writes that preset's value for every key; any later edit
leaves the mode reported as "custom".
"""

import os
import re

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

MODES = ("saver", "balanced", "performance")
MODE_LABELS = {
    "saver": "Memory saver",
    "balanced": "Balanced",
    "performance": "Performance",
    "custom": "Custom",
}

# key -> label, help, choices [(value, text)], preset per mode.
OPTIMIZATIONS = {
    "cookie_idle_seconds": {
        "label": "Cookie inspection after activity",
        "help": "Cookie sharing and the cookie viewer use a hidden inspector (about "
        "60 MB per profile while awake). It sleeps after this long without cookie "
        "changes and wakes on the next one.",
        "choices": [
            (5, "Sleep after 5 seconds"),
            (30, "Sleep after 30 seconds"),
            (300, "Sleep after 5 minutes"),
            (0, "Stay awake"),
        ],
        "presets": {"saver": 5, "balanced": 30, "performance": 0},
    },
    "cookie_wake_interval_seconds": {
        "label": "Cookie inspection wake-ups",
        "help": "While browsing, sites change cookies constantly. Waiting between "
        "wake-ups keeps the inspector asleep most of the time; shared cookies then "
        "reach other profiles up to this much later. Opening the cookie viewer "
        "always wakes it at once.",
        "choices": [
            (0, "Wake on every change"),
            (20, "At most every 20 seconds"),
            (60, "At most every minute"),
        ],
        "presets": {"saver": 20, "balanced": 0, "performance": 0},
    },
    "tab_freeze_minutes": {
        "label": "Freeze background tabs",
        "help": "A frozen tab keeps its page but stops running scripts and timers, "
        "saving CPU and battery. It resumes instantly when you return, but chat "
        "and mail tabs stop receiving updates while frozen.",
        "choices": [(2, "After 2 minutes"), (10, "After 10 minutes"), (0, "Never")],
        "presets": {"saver": 2, "balanced": 0, "performance": 0},
    },
    "tab_discard_minutes": {
        "label": "Unload background tabs",
        "help": "An unloaded tab frees its renderer's memory and reloads when you "
        "return to it. Unsaved form text in it can be lost. Tabs playing audio, "
        "shared with an agent, with DevTools open or still loading are kept.",
        "choices": [
            (15, "After 15 minutes"),
            (60, "After 1 hour"),
            (240, "After 4 hours"),
            (0, "Never"),
        ],
        "presets": {"saver": 15, "balanced": 240, "performance": 0},
    },
    "native_interface": {
        "label": "Interface",
        "help": "HTML interface parts (tab strip, right-click menus, palette, "
        "dialogs) each run a renderer of about 12 MB. Native Qt widgets need none "
        "but drop tab-strip styling and dragging tabs between windows. The tab "
        "strip changes in new windows and after a restart.",
        "choices": [(False, "Customizable HTML"), (True, "Lighter native widgets")],
        "presets": {"saver": True, "balanced": False, "performance": False},
    },
    "palette_keep_seconds": {
        "label": "Command palette",
        "help": "How long the palette stays loaded after you close it. Preloading "
        "builds it when a window opens so the first Ctrl+K is instant.",
        "choices": [
            (0, "Free immediately"),
            (60, "Keep for a minute"),
            (-1, "Preload and keep"),
        ],
        "presets": {"saver": 0, "balanced": 60, "performance": -1},
    },
    "auto_saver": {
        "label": "When the computer runs low on memory",
        "help": "Switch Steed to Memory saver while available system memory is "
        "below 10%, unloading background tabs that can safely reload, and return "
        "to your settings once it is above 20% again. Your saved choices are not "
        "changed.",
        "choices": [(True, "Switch to Memory saver"), (False, "Keep my settings")],
        "presets": {"saver": True, "balanced": True, "performance": False},
    },
    "trim_freed_memory": {
        "label": "Return freed memory",
        "help": "After tabs or windows close, hand memory Steed no longer uses back "
        "to the system (Linux/glibc). Costs a few milliseconds.",
        "choices": [(True, "On"), (False, "Off")],
        "presets": {"saver": True, "balanced": True, "performance": False},
    },
}

DEFAULT_MODE = "balanced"


def preset(mode):
    return {key: spec["presets"][mode] for key, spec in OPTIMIZATIONS.items()}


def values(config):
    """Effective optimization values; unknown or invalid ones fall back."""
    defaults = preset(DEFAULT_MODE)
    result = {}
    for key, spec in OPTIMIZATIONS.items():
        value = config.get(key, defaults[key])
        allowed = [choice for choice, _ in spec["choices"]]
        result[key] = value if _valid(value, allowed) else defaults[key]
    return result


def _valid(value, allowed):
    return any(type(value) is type(choice) and value == choice for choice in allowed)


def mode(config):
    """The preset these values match, or "custom"."""
    current = values(config)
    for name in MODES:
        if current == preset(name):
            return name
    return "custom"


def apply_mode(config, name):
    if name not in MODES:
        raise ValueError("Choose memory saver, balanced or performance.")
    for key, value in preset(name).items():
        config.set(key, value)


def set_value(config, key, value):
    if key not in OPTIMIZATIONS:
        raise ValueError("Unknown memory setting.")
    allowed = [choice for choice, _ in OPTIMIZATIONS[key]["choices"]]
    if not _valid(value, allowed):
        raise ValueError("Choose one of the listed values.")
    config.set(key, value)


def effective(config):
    """Values in force now: Memory saver's while the system is short of memory."""
    current = values(config)
    if current["auto_saver"] and monitor().low:
        saver = preset("saver")
        # The interface stays as chosen: rebuilding it mid-session is disruptive.
        saver["native_interface"] = current["native_interface"]
        return {**saver, "auto_saver": True}
    return current


def apply_process_policies(config):
    """Apply the app-wide parts; windows apply tab and palette behaviour."""
    import cookie_snapshot
    import memory_trim
    from ui.interface_mode import set_native

    current = effective(config)
    cookie_snapshot.set_policy(
        current["cookie_idle_seconds"] * 1000,
        current["cookie_wake_interval_seconds"] * 1000,
    )
    memory_trim.set_enabled(current["trim_freed_memory"])
    set_native(current["native_interface"])
    return current


def _pss_kb(pid):
    try:
        with open(f"/proc/{pid}/smaps_rollup") as stream:
            for line in stream:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0


def _process_tree(root):
    """(pid, type) for Steed and every helper it started (Linux /proc)."""
    parents = {}
    try:
        entries = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return []
    for entry in entries:
        try:
            with open(f"/proc/{entry}/stat") as stream:
                # The command name may contain spaces; the parent follows ") X ".
                parents[int(entry)] = int(stream.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
    found, frontier = [], [root]
    while frontier:
        parent = frontier.pop()
        for pid, owner in parents.items():
            if owner == parent:
                found.append(pid)
                frontier.append(pid)
    result = [(root, "browser")]
    for pid in found:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as stream:
                arguments = stream.read().split(b"\0")
        except OSError:
            continue
        # Chromium rewrites helper titles into one space-separated string.
        match = re.search(rb"--type=([a-z-]+)", b" ".join(arguments))
        kind = match.group(1).decode() if match else "helper"
        result.append((pid, kind))
    return result


def usage(windows):
    """Live memory figures for steed:memory; None where /proc is unavailable."""
    from PyQt6.QtWebEngineCore import QWebEnginePage

    import cookie_snapshot

    processes = _process_tree(os.getpid())
    by_kind = {}
    for pid, kind in processes:
        by_kind.setdefault(kind, [0, 0])
        by_kind[kind][0] += 1
        by_kind[kind][1] += _pss_kb(pid)
    State = QWebEnginePage.LifecycleState
    tabs = {"active": 0, "frozen": 0, "unloaded": 0}
    for window in windows:
        for tab in window.tabs:
            page = tab.page()
            state = page.lifecycleState() if page is not None else State.Active
            key = {State.Frozen: "frozen", State.Discarded: "unloaded"}.get(
                state, "active"
            )
            tabs[key] += 1
    snapshots = [s for s in list(cookie_snapshot._SNAPSHOTS) if not s.closed]
    return {
        "available": len(processes) > 1 or by_kind.get("browser", [0, 0])[1] > 0,
        "total_mb": sum(kb for _, kb in by_kind.values()) // 1024,
        "processes": {
            kind: {"count": count, "mb": kb // 1024}
            for kind, (count, kb) in sorted(by_kind.items())
        },
        "tabs": tabs,
        "inspectors": {
            "awake": sum(not s.sleeping for s in snapshots),
            "total": len(snapshots),
        },
    }


def free_now(windows):
    """Release what can be released immediately, whatever the mode."""
    from PyQt6.QtWebEngineCore import QWebEnginePage

    import cookie_snapshot
    import memory_trim

    State = QWebEnginePage.LifecycleState
    unloaded = 0
    for window in windows:
        for index, tab in enumerate(window.tabs):
            if index == window.current_tab_index or window._tab_keeps_running(tab):
                continue
            page = tab.page()
            if page.recommendedState() == State.Discarded and (
                page.lifecycleState() != State.Discarded
            ):
                page.setLifecycleState(State.Discarded)
                unloaded += 1
        palette = window._palette
        if palette is not None and not palette.isVisible():
            window._release_palette()
    slept = 0
    for snapshot in list(cookie_snapshot._SNAPSHOTS):
        if not snapshot.closed and not snapshot.sleeping and not snapshot.pending:
            snapshot._sleep()
            slept += snapshot.sleeping
    memory_trim.release_now()
    return {"unloaded": unloaded, "inspectors": slept}


# Below this share of system memory available, auto saver switches on; it
# switches off again above RECOVER_SHARE, so it does not flap.
LOW_SHARE = 0.10
RECOVER_SHARE = 0.20
PRESSURE_POLL_MS = 10000


def system_memory():
    """(available, total) bytes from /proc/meminfo, or None."""
    fields = {}
    try:
        with open("/proc/meminfo") as stream:
            for line in stream:
                name, _, rest = line.partition(":")
                if name in ("MemAvailable", "MemTotal"):
                    fields[name] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    if "MemAvailable" not in fields or not fields.get("MemTotal"):
        return None
    return fields["MemAvailable"], fields["MemTotal"]


_monitor = None


def monitor():
    global _monitor
    if _monitor is None:
        _monitor = PressureMonitor()
    return _monitor


class PressureMonitor(QObject):
    """Polls system memory; `changed` fires when low memory starts or ends."""

    changed = pyqtSignal(bool)

    def __init__(self, reader=system_memory):
        super().__init__()
        self.reader = reader
        self.low = False
        self.share = None
        self.timer = QTimer(self)
        self.timer.setInterval(PRESSURE_POLL_MS)
        self.timer.timeout.connect(self.check)

    def start(self):
        if not self.timer.isActive():
            self.timer.start()
            self.check()

    def check(self):
        reading = self.reader()
        if reading is None:
            return
        available, total = reading
        self.share = available / total
        low = self.share < (RECOVER_SHARE if self.low else LOW_SHARE)
        if low != self.low:
            self.low = low
            self.changed.emit(low)
