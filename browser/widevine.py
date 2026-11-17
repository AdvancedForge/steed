"""Protected streaming (Netflix, Spotify, Disney+): the Widevine module.

Qt WebEngine can play DRM video with Google's Widevine module but does not
ship it. It finds one on its own where Google Chrome or Chromium keep theirs
(~/.config/google-chrome/WidevineCdm, ~/.config/chromium/WidevineCdm,
/opt/google/chrome/WidevineCdm). Firefox keeps the same module elsewhere,
so Steed looks there too and hands it to the engine (--widevine-path). Steed
never downloads the module itself.
"""

import re
from pathlib import Path

# Where the engine looks by itself, relative to the home folder or absolute.
ENGINE_PLACES = (
    ".config/google-chrome/WidevineCdm",
    ".config/chromium/WidevineCdm",
    "/opt/google/chrome/WidevineCdm",
)
# Firefox's copies: <profile>/gmp-widevinecdm/<version>/libwidevinecdm.so.
FIREFOX_ROOTS = (
    ".mozilla/firefox",
    "snap/firefox/common/.mozilla/firefox",
    ".var/app/org.mozilla.firefox/.mozilla/firefox",
)
LIBRARY = "libwidevinecdm.so"


def _version(path: Path):
    found = re.findall(r"\d+", path.parent.name)
    return tuple(int(n) for n in found)


def engine_module(home: Path) -> Path | None:
    """A module the engine finds by itself (Chrome's or Chromium's)."""
    for place in ENGINE_PLACES:
        root = Path(place) if place.startswith("/") else home / place
        try:
            found = sorted(root.glob(f"*/_platform_specific/*/{LIBRARY}"))
        except OSError:
            continue
        if found:
            return found[-1]
    return None


def firefox_module(home: Path) -> Path | None:
    """The newest module Firefox downloaded, in any of its profiles."""
    found = []
    for root in FIREFOX_ROOTS:
        try:
            found += [
                p
                for p in (home / root).glob(f"*/gmp-widevinecdm/*/{LIBRARY}")
                if p.is_file()
            ]
        except OSError:
            continue
    return max(found, key=_version) if found else None


def find(home: Path | None = None) -> dict:
    """{"path": module or None, "source": "chrome", "firefox" or ""}."""
    home = home or Path.home()
    module = engine_module(home)
    if module is not None:
        return {"path": str(module), "source": "chrome"}
    module = firefox_module(home)
    if module is not None:
        return {"path": str(module), "source": "firefox"}
    return {"path": None, "source": ""}


def flags(home: Path | None = None) -> list[str]:
    """The engine flag Steed adds (only for Firefox's module)."""
    found = find(home)
    if found["source"] == "firefox":
        return [f"--widevine-path={found['path']}"]
    return []
