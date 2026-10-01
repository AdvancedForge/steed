"""Configuration management for the custom browser."""

from pathlib import Path
from typing import Any

from utils import load_json, save_json


class Config:
    """Manages browser configuration."""

    def __init__(self) -> None:
        self.config_dir = Path.home() / ".custom_browser"
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
            "default_profile": "default",
            "home_page": "https://www.duckduckgo.com",
            "search_engine": "https://www.duckduckgo.com/?q=",
            "dark_mode": False,
            "restore_tabs_on_startup": True,
            "window_width": 1200,
            "window_height": 800,
            "global_cookie_sharing_mode": "on_switch",  # "on_switch" or "all_profiles"
            "global_cookie_sharing_apply_on_startup": True,
        }

        user_config = load_json(self.config_file, default={})
        if isinstance(user_config, dict):
            default_config.update(user_config)

        return default_config

    def save_config(self) -> None:
        """Save configuration to file."""
        save_json(self.config_file, self._config)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        self._config[key] = value
        self.revision += 1
        self.save_config()

    def update(self, values: dict[str, Any]) -> None:
        """Save a set of preference changes with one disk write."""
        self._config.update(values)
        self.revision += 1
        self.save_config()

    @property
    def default_profile(self) -> str:
        """Get the default profile name."""
        return self.get("default_profile", "default")

    @property
    def dark_mode(self) -> bool:
        """Check if dark mode is enabled."""
        return self.get("dark_mode", False)

    @dark_mode.setter
    def dark_mode(self, value: bool) -> None:
        """Set dark mode."""
        self.set("dark_mode", value)
