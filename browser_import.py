"""Import bookmarks, history and open tabs from Chrome-family browsers and Firefox.

Each browser's files are copied to a temporary folder before reading, so the
browser may stay open and its own files are never changed. Chromium's open tabs
are kept in an undocumented binary format, so only Firefox's are imported.
"""

import configparser
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

MAX_HISTORY = 10000
MAX_TABS = 200
# (browser name, configuration folder below the home folder)
CHROMIUM_FAMILY = (
    ("Google Chrome", ".config/google-chrome"),
    ("Chromium", ".config/chromium"),
    ("Chromium (Snap)", "snap/chromium/common/chromium"),
    ("Brave", ".config/BraveSoftware/Brave-Browser"),
    ("Microsoft Edge", ".config/microsoft-edge"),
    ("Vivaldi", ".config/vivaldi"),
    ("Google Chrome (Flatpak)", ".var/app/com.google.Chrome/config/google-chrome"),
)
FIREFOX_ROOTS = (
    ("Firefox", ".mozilla/firefox"),
    ("Firefox (Snap)", "snap/firefox/common/.mozilla/firefox"),
    ("Firefox (Flatpak)", ".var/app/org.mozilla.firefox/.mozilla/firefox"),
)


def discover(home: Path | None = None) -> list[dict]:
    """Browser profiles with something to import: browser, name, kind, path."""
    home = Path(home or Path.home())
    found = []
    for browser, relative in CHROMIUM_FAMILY:
        root = home / relative
        names = {}
        try:
            state = json.loads((root / "Local State").read_text(encoding="utf-8"))
            for folder, info in state["profile"]["info_cache"].items():
                names[folder] = info.get("name") or folder
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        for folder in sorted(root.glob("*")) if root.is_dir() else []:
            if (folder / "Bookmarks").is_file() or (folder / "History").is_file():
                found.append(
                    {
                        "browser": browser,
                        "name": names.get(folder.name, folder.name),
                        "kind": "chromium",
                        "path": str(folder),
                    }
                )
    for browser, relative in FIREFOX_ROOTS:
        root = home / relative
        parser = configparser.ConfigParser()
        try:
            parser.read(root / "profiles.ini", encoding="utf-8")
        except (configparser.Error, UnicodeError):
            continue
        for section in parser.sections():
            if not section.startswith("Profile"):
                continue
            path = parser.get(section, "Path", fallback="")
            relative_path = parser.get(section, "IsRelative", fallback="1") == "1"
            folder = root / path if relative_path else Path(path)
            if (folder / "places.sqlite").is_file():
                found.append(
                    {
                        "browser": browser,
                        "name": parser.get(section, "Name", fallback=folder.name),
                        "kind": "firefox",
                        "path": str(folder),
                    }
                )
    return found


def _copy_database(path: Path, workdir: Path) -> Path:
    """A private copy (with its write-ahead log) that a running browser cannot lock."""
    target = workdir / path.name
    shutil.copy2(path, target)
    for suffix in ("-wal", "-shm"):
        extra = path.with_name(path.name + suffix)
        if extra.exists():
            shutil.copy2(extra, workdir / extra.name)
    return target


def _query(path: Path, sql: str):
    with tempfile.TemporaryDirectory() as workdir:
        copy = _copy_database(path, Path(workdir))
        connection = sqlite3.connect(copy)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()


def _web(url):
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def chromium_bookmarks(folder: Path) -> list[dict]:
    data = json.loads((folder / "Bookmarks").read_text(encoding="utf-8"))
    entries = []

    def walk(node, label):
        if node.get("type") == "url":
            if _web(node.get("url")):
                entries.append(
                    {
                        "url": node["url"],
                        "title": node.get("name") or node["url"],
                        "folder": label,
                    }
                )
            return
        for child in node.get("children", []):
            # The top folder below a root becomes the bookmark's label.
            walk(
                child,
                label or (node.get("name") if node.get("type") == "folder" else ""),
            )

    for key in ("bookmark_bar", "other", "synced"):
        root = data.get("roots", {}).get(key)
        if isinstance(root, dict):
            for child in root.get("children", []):
                walk(
                    child,
                    child.get("name", "") if child.get("type") == "folder" else "",
                )
    return entries


def chromium_history(folder: Path) -> list[dict]:
    rows = _query(
        folder / "History",
        "SELECT url, title FROM urls WHERE hidden = 0 "
        f"ORDER BY last_visit_time DESC LIMIT {MAX_HISTORY * 2}",
    )
    return [{"url": u, "title": t or u} for u, t in rows if _web(u)][:MAX_HISTORY]


def firefox_bookmarks(folder: Path) -> list[dict]:
    rows = _query(
        folder / "places.sqlite",
        "SELECT p.url, b.title, parent.title, grand.title FROM moz_bookmarks b "
        "JOIN moz_places p ON p.id = b.fk "
        "LEFT JOIN moz_bookmarks parent ON parent.id = b.parent "
        "LEFT JOIN moz_bookmarks grand ON grand.id = parent.parent "
        "WHERE b.type = 1 ORDER BY b.position",
    )
    roots = {
        "menu",
        "toolbar",
        "unfiled",
        "mobile",
        "",
        None,
        "Bookmarks Menu",
        "Bookmarks Toolbar",
        "Other Bookmarks",
        "Mobile Bookmarks",
    }
    entries = []
    for url, title, parent, grand in rows:
        if not _web(url):
            continue
        # The folder just below a root (menu, toolbar, other) is the label.
        label = "" if parent in roots else (parent if grand in roots else grand)
        entries.append({"url": url, "title": title or url, "folder": label or ""})
    return entries


def firefox_history(folder: Path) -> list[dict]:
    rows = _query(
        folder / "places.sqlite",
        "SELECT url, title FROM moz_places WHERE visit_count > 0 AND hidden = 0 "
        f"ORDER BY last_visit_date DESC LIMIT {MAX_HISTORY * 2}",
    )
    return [{"url": u, "title": t or u} for u, t in rows if _web(u)][:MAX_HISTORY]


def lz4_block(data: bytes, size: int) -> bytes:
    """Decompress one LZ4 block (the format of Firefox's .jsonlz4 files)."""
    out = bytearray()
    i = 0
    while i < len(data):
        token = data[i]
        i += 1
        length = token >> 4
        if length == 15:
            while True:
                extra = data[i]
                i += 1
                length += extra
                if extra != 255:
                    break
        out += data[i : i + length]
        i += length
        if i >= len(data):
            break
        offset = data[i] | (data[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(out):
            raise ValueError("Damaged session file")
        match = (token & 15) + 4
        if token & 15 == 15:
            while True:
                extra = data[i]
                i += 1
                match += extra
                if extra != 255:
                    break
        start = len(out) - offset
        for n in range(match):  # Overlapping copies repeat recent bytes.
            out.append(out[start + n])
        if len(out) > size:
            raise ValueError("Damaged session file")
    return bytes(out)


def read_jsonlz4(path: Path):
    raw = path.read_bytes()
    if raw[:8] != b"mozLz40\0":
        raise ValueError("Not a Firefox session file")
    size = int.from_bytes(raw[8:12], "little")
    if size > 64 * 2**20:
        raise ValueError("The session file is too large")
    return json.loads(lz4_block(raw[12:], size))


def firefox_tabs(folder: Path) -> list[dict]:
    """The open tabs of the newest Firefox session, across its windows."""
    candidates = [
        folder / "sessionstore-backups" / "recovery.jsonlz4",
        folder / "sessionstore.jsonlz4",
        folder / "sessionstore-backups" / "previous.jsonlz4",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        session = read_jsonlz4(path)
        tabs = []
        for window in session.get("windows", []):
            for tab in window.get("tabs", []):
                entries = tab.get("entries") or []
                index = max(
                    1, min(int(tab.get("index", len(entries)) or 1), len(entries))
                )
                if entries and _web(entries[index - 1].get("url")):
                    entry = entries[index - 1]
                    tabs.append(
                        {
                            "url": entry["url"],
                            "title": entry.get("title") or entry["url"],
                        }
                    )
        return tabs[:MAX_TABS]
    return []


def read_source(source: dict, *, bookmarks=True, history=True, tabs=True) -> dict:
    """What the chosen profile has, by kind; missing kinds come back empty."""
    folder = Path(source["path"])
    chromium = source["kind"] == "chromium"
    result = {"bookmarks": [], "history": [], "tabs": []}
    readers = {
        "bookmarks": (bookmarks, chromium_bookmarks if chromium else firefox_bookmarks),
        "history": (history, chromium_history if chromium else firefox_history),
        "tabs": (tabs and not chromium, firefox_tabs),
    }
    for key, (wanted, reader) in readers.items():
        if wanted:
            try:
                result[key] = reader(folder)
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                result[key] = []
    return result
