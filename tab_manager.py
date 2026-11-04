"""Tab persistence per profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal

from utils import load_json, save_json


@dataclass
class TabState:
    url: str
    title: str
    group: str | None = None
    tab_id: str = field(default_factory=lambda: uuid4().hex, compare=False)
    group_id: str | None = None
    pinned: bool = False
    # Exempt from freezing and unloading by memory modes.
    keep_loaded: bool = False
    # Where the page was scrolled to, restored after the next start.
    scroll: list[float] | None = None


@dataclass
class TabGroup:
    group_id: str
    name: str
    collapsed: bool = False


@dataclass
class ClosedTab:
    state: TabState
    index: int


class ClosedTabHistory:
    """Bounded, non-persistent histories; incognito records never reach disk."""

    def __init__(self, limit: int = 50) -> None:
        self.limit = limit
        self._profiles: dict[str, list[ClosedTab]] = {}

    def push(self, profile: str, state: TabState, index: int) -> None:
        history = self._profiles.setdefault(profile, [])
        history.append(ClosedTab(state, index))
        del history[: -self.limit]

    def pop(self, profile: str) -> ClosedTab | None:
        history = self._profiles.get(profile, [])
        return history.pop() if history else None

    def available(self, profile: str) -> bool:
        return bool(self._profiles.get(profile))

    def entries(self, profile: str) -> list[ClosedTab]:
        return list(reversed(self._profiles.get(profile, [])))

    def pop_id(self, profile: str, tab_id: str) -> ClosedTab | None:
        history = self._profiles.get(profile, [])
        for index in range(len(history) - 1, -1, -1):
            if history[index].state.tab_id == tab_id:
                return history.pop(index)
        return None


# Open windows per session file, shared by every window in this process. Keys are
# window IDs; "saved:N" keys hold saved windows nobody has reopened yet.
_OPEN: dict[str, dict[str, list[TabState]]] = {}


def _scroll(value) -> list[float] | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(
            isinstance(n, (int, float)) and not isinstance(n, bool) and 0 <= n < 1e7
            for n in value
        )
    ):
        return [float(value[0]), float(value[1])]
    return None


def _parse_states(raw) -> list[TabState]:
    states: list[TabState] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        states.append(
            TabState(
                url=item.get("url", "https://www.google.com"),
                title=item.get("title", "New Tab"),
                group=item.get("group"),
                tab_id=item.get("tab_id") or uuid4().hex,
                group_id=item.get("group_id"),
                pinned=item.get("pinned") is True,
                keep_loaded=item.get("keep_loaded") is True,
                scroll=_scroll(item.get("scroll")),
            )
        )
    return states


class TabManager(QObject):
    """Saves/restores each open window's tabs in <profile_dir>/tabs.json."""

    group_data_updated = pyqtSignal()

    def __init__(
        self,
        profile_dir: Path,
        parent: QObject | None = None,
        window_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.window_id = window_id or uuid4().hex
        self.profile_dir = profile_dir
        self.tabs_file = self.profile_dir / "tabs.json"
        self.tab_states: list[TabState] = []
        self.groups_file = self.profile_dir / "groups.json"
        self.groups: dict[str, TabGroup] = {}
        raw = load_json(self.groups_file, []) if str(profile_dir) != "." else []
        for item in raw if isinstance(raw, list) else []:
            if (
                isinstance(item, dict)
                and isinstance(item.get("group_id"), str)
                and isinstance(item.get("name"), str)
            ):
                group = TabGroup(
                    item["group_id"], item["name"], bool(item.get("collapsed", False))
                )
                self.groups[group.group_id] = group

    def save_groups(self):
        if str(self.profile_dir) != ".":
            save_json(
                self.groups_file, [asdict(group) for group in self.groups.values()]
            )

    def ensure_group(self, name):
        for group in self.groups.values():
            if group.name == name:
                return group
        group = TabGroup(uuid4().hex, name)
        self.groups[group.group_id] = group
        self.save_groups()
        return group

    def _windows(self) -> dict[str, list[TabState]]:
        return _OPEN.setdefault(str(self.tabs_file), {})

    def _write(self) -> None:
        windows = [w for w in self._windows().values() if w]
        save_json(
            self.tabs_file,
            {"version": 2, "windows": [[asdict(s) for s in w] for w in windows]},
        )

    def others_open(self) -> bool:
        """Whether another live window already shows this profile."""
        return any(
            key != self.window_id and not key.startswith("saved:")
            for key in self._windows()
        )

    def save_tabs(self, states: list[TabState]) -> None:
        self.tab_states = list(states)
        self._windows()[self.window_id] = list(states)
        self._write()
        self.group_data_updated.emit()

    def get_saved_windows(self) -> list[list[TabState]]:
        raw = load_json(self.tabs_file, default=[])
        if isinstance(raw, dict) and raw.get("version") == 2:
            windows = raw.get("windows")
            return [
                states for states in (_parse_states(w) for w in windows or []) if states
            ][:16]
        states = _parse_states(raw)
        return [states] if states else []

    def claim_saved(self) -> list[list[TabState]]:
        """First window of a profile: own the first saved window, hold the rest."""
        windows = self.get_saved_windows()
        open_windows = self._windows()
        for key in [k for k in open_windows if k.startswith("saved:")]:
            del open_windows[key]
        for index, states in enumerate(windows[1:], 1):
            open_windows[f"saved:{index}"] = states
        if windows:
            open_windows[self.window_id] = windows[0]
            self.tab_states = list(windows[0])
        return windows

    def drop_saved(self, index: int) -> None:
        """A reopened window now saves its own entry instead."""
        self._windows().pop(f"saved:{index}", None)

    def release(self, keep: bool = False) -> None:
        """Leave the session when the window closes or switches profile.

        A window closed while another window of this profile stays open loses
        its saved entry. When quitting (keep) or closing the profile's last
        window, the file keeps the tabs as the session to restore.
        """
        windows = self._windows()
        drop = not keep and self.others_open()
        windows.pop(self.window_id, None)
        if drop:
            self._write()

    def get_saved_tabs(self) -> list[TabState]:
        windows = self.get_saved_windows()
        self.tab_states = windows[0] if windows else []
        return list(self.tab_states)
