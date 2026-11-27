"""Configuration management for the custom browser."""

import os
from pathlib import Path
from typing import Any

from browser.utils import load_json, save_json

DEFAULT_SEARCH_ENGINE = "https://www.duckduckgo.com/?q="
DATA_ENV = "STEED_DATA_DIR"


def data_dir() -> Path:
    """Where Steed keeps everything: ~/.custom_browser, or another folder
    (steed --data-dir FOLDER, or STEED_DATA_DIR), so a second Steed, such as
    a copy you or your agent changed, runs beside yours with its own data."""
    chosen = os.environ.get(DATA_ENV, "").strip()
    if chosen:
        return Path(chosen).expanduser().resolve()
    return Path.home() / ".custom_browser"


def own_data_dir() -> bool:
    """True when this Steed runs on a folder other than the usual one."""
    return bool(os.environ.get(DATA_ENV, "").strip())


class Config:
    """Manages browser configuration."""

    def __init__(self) -> None:
        self.config_dir = data_dir()
        self.config_file = self.config_dir / "config.json"
        self.profiles_dir = self.config_dir / "profiles"
        self._ensure_directories()
        self._config = self._load_config()
        # Bumped on every write so hot paths can cache derived values and still
        # notice a change without re-reading the whole configuration.
        self.revision = 0

    def _ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict[str, Any]:
        """Load configuration from file."""
        default_config = {
            "default_profile": "global",
            "home_page": "steed:newtab",
            "new_tab_page": "steed:newtab",
            "search_engine": DEFAULT_SEARCH_ENGINE,
            "dark_mode": False,
            "restore_tabs_on_startup": True,
            "google_login_compatibility": True,
            "window_width": 1200,
            "window_height": 800,
            "global_cookie_sharing_mode": "on_switch",  # "on_switch" or "all_profiles"
            "global_cookie_sharing_apply_on_startup": True,
        }

        user_config = load_json(self.config_file, default={})
        if isinstance(user_config, dict):
            default_config.update(user_config)
        # The old built-in Default selection becomes Global. Its directory is
        # deliberately retained: an existing profile remains ordinary user data.
        if default_config.get("default_profile") == "default":
            default_config["default_profile"] = "global"
        if default_config.get("home_page") in (
            "https://www.duckduckgo.com",
            "https://www.google.com",
        ):
            default_config["home_page"] = "steed:newtab"

        return default_config

    def save_config(self) -> None:
        """Save configuration to file."""
        save_json(self.config_file, self._config)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        self.update({key: value})

    def update(self, values: dict[str, Any]) -> None:
        """Save a set of preference changes with one disk write."""
        previous = self._config
        self._config = {**previous, **values}
        try:
            self.save_config()
        except Exception:
            self._config = previous
            raise
        self.revision += 1

    @property
    def default_profile(self) -> str:
        """Get the default profile name."""
        return self.get("default_profile", "global")

    @property
    def dark_mode(self) -> bool:
        """Check if dark mode is enabled."""
        return self.get("dark_mode", False)

    @dark_mode.setter
    def dark_mode(self, value: bool) -> None:
        """Set dark mode."""
        self.set("dark_mode", value)
