"""Tab persistence per profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from utils import load_json, save_json


@dataclass
class TabState:
    url: str
    title: str
    group: str | None = None


class TabManager(QObject):
    """Saves/restores tabs to <profile_dir>/tabs.json"""

    tab_state_changed = pyqtSignal(int)
    group_data_updated = pyqtSignal()

    def __init__(self, profile_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.profile_dir = profile_dir
        self.tabs_file = self.profile_dir / "tabs.json"
        self.tab_states: list[TabState] = []

    def save_tabs(self, states: list[TabState]) -> None:
        self.tab_states = list(states)
        data = [asdict(s) for s in self.tab_states]
        save_json(self.tabs_file, data)
        self.group_data_updated.emit()

    def get_saved_tabs(self) -> list[TabState]:
        raw = load_json(self.tabs_file, default=[])
        states: list[TabState] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            states.append(
                TabState(
                    url=item.get("url", "https://www.google.com"),
                    title=item.get("title", "New Tab"),
                    group=item.get("group"),
                )
            )
        self.tab_states = states
        return states
