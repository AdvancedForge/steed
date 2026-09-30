"""Configuration management for the custom browser."""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """Manages browser configuration."""
    
    def __init__(self):
        self.config_dir = Path.home() / ".custom_browser"
        self.config_file = self.config_dir / "config.json"
        self.profiles_dir = self.config_dir / "profiles"
        self._ensure_directories()
        self._config = self._load_config()
    
    def _ensure_directories(self):
        """Create necessary directories if they don't exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
    
    def _load_config(self) -> Dict[str, Any]:
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
        
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
            except Exception:
                pass
        
        return default_config
    
    def save_config(self):
        """Save configuration to file."""
        with open(self.config_file, 'w') as f:
            json.dump(self._config, f, indent=2)
    
    def onetime_save_config(self, configur):
        """Save configuration to file."""
        with open(self.config_file, 'w') as f:
            json.dump(configur, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set a configuration value."""
        self._config[key] = value
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
    def dark_mode(self, value: bool):
        """Set dark mode."""
        self.set("dark_mode", value)

