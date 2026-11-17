"""Steed as the desktop's default browser (Linux, freedesktop tools).

Links from other apps open in the default browser named by
`xdg-settings get default-web-browser`. Steed registers its desktop entry
(the same one install_launcher.py writes) with the web link and page types,
then asks xdg-settings to make it the default, or xdg-mime where xdg-settings
cannot (some desktops).
"""

import os
import shutil
import subprocess
from pathlib import Path

ENTRY = "steed.desktop"
# Web links, and pages or documents opened from a file manager.
MIME_TYPES = (
    "x-scheme-handler/http",
    "x-scheme-handler/https",
    "text/html",
    "application/xhtml+xml",
)
TIMEOUT = 5


def applications_dir() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(data) / "applications"


def entry_path() -> Path:
    return applications_dir() / ENTRY


def ensure_entry(project: Path | None = None) -> Path:
    """Write (or update) Steed's desktop entry; its path."""
    from browser.install_launcher import desktop_entry

    project = project or Path(__file__).resolve().parents[1]
    path = entry_path()
    contents = desktop_entry(project)
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != contents:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        tool = shutil.which("update-desktop-database")
        if tool:
            _run([tool, str(path.parent)])
    return path


def _run(command) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def current() -> str | None:
    """The default browser's desktop entry, or None when it cannot be told."""
    tool = shutil.which("xdg-settings")
    if tool:
        found = _run([tool, "get", "default-web-browser"])
        if found is not None and found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    tool = shutil.which("xdg-mime")
    if tool:
        found = _run([tool, "query", "default", "x-scheme-handler/https"])
        if found is not None and found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return None


def is_default() -> bool:
    return current() == ENTRY


def make_default() -> str:
    """Make Steed the default browser; what happened, in words. Raises
    OSError when the desktop offers no way to do it."""
    ensure_entry()
    settings = shutil.which("xdg-settings")
    if settings:
        done = _run([settings, "set", "default-web-browser", ENTRY])
        if done is not None and done.returncode == 0:
            return "Steed is now your default browser."
    mime = shutil.which("xdg-mime")
    if mime:
        done = _run([mime, "default", ENTRY, *MIME_TYPES])
        if done is not None and done.returncode == 0:
            return "Steed now opens web links and pages from other apps."
    raise OSError(
        "Your desktop did not accept the change. Choose Steed in your system "
        "settings under Default applications."
    )
