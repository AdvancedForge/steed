"""Utility functions for the browser."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_json(file_path: Path, default: Any = None) -> Any:
    """Load JSON from file, returning default if file doesn't exist."""
    try:
        with file_path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:
        logger.warning("Could not load JSON from %s: %s", file_path, error)
    return default if default is not None else {}


def save_json(file_path: Path, data: Any) -> None:
    """Atomically replace a JSON file so interrupted writes preserve old data."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(file_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def get_profile_path(profiles_dir: Path, profile_name: str) -> Path:
    """Get the path for a profile directory."""
    return profiles_dir / profile_name


def sanitize_profile_name(name: str) -> str:
    """Sanitize profile name for filesystem use."""
    # Remove invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "_")
    name = name.strip()
    # These are directory traversal components even though they contain no slash.
    return name.replace(".", "_") if name in (".", "..") else name
