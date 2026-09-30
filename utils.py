"""Utility functions for the browser."""
from pathlib import Path
import json
from typing import Dict, Any, Optional


def load_json(file_path: Path, default: Any = None) -> Any:
    """Load JSON from file, returning default if file doesn't exist."""
    if file_path.exists():
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def save_json(file_path: Path, data: Any):
    """Save data as JSON to file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)


def get_profile_path(profiles_dir: Path, profile_name: str) -> Path:
    """Get the path for a profile directory."""
    return profiles_dir / profile_name


def sanitize_profile_name(name: str) -> str:
    """Sanitize profile name for filesystem use."""
    # Remove invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name.strip()
