"""Your own CSS and JavaScript for the sites you choose (steed:userscripts).

Each profile keeps its list in <profile>/user_scripts.json and runs it in every
matching http(s) page and frame: styles from Steed's own script world as soon
as the document exists, scripts in the page's world (so they can work with the
site's code) when the page is ready, or at the start or after loading. Changes
apply to pages loaded afterwards. Incognito windows run none.

A script set to run "when I choose it" is a command instead: it runs on the
current page only when picked from View → Your Commands, the command palette
or its own keyboard shortcut (still only on the sites it lists).
"""

import json
import re
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal

from browser import webengine
from browser.utils import save_json

MAX_ENTRIES = 50
MAX_SITES = 50
MAX_CSS = 100_000
MAX_JS = 200_000
WHEN = ("start", "ready", "loaded", "command")
PREFIX = "steed-user-"
_HOST = re.compile(r"^(\*|(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*)$")
_MATCH = """const host = location.hostname.toLowerCase();
  if (!/^https?:$/.test(location.protocol)) return;
  if (!%s.some(site => site === "*" || host === site.replace(/^\\*\\./, "")
      || host.endsWith("." + site.replace(/^\\*\\./, "")))) return;"""


def parse_sites(text) -> list[str]:
    """Hosts from text: one per line or comma separated; "*" means every site."""
    sites = []
    for part in re.split(r"[\s,]+", str(text).strip().lower()):
        if not part:
            continue
        part = re.sub(r"^https?://", "", part).split("/")[0].split(":")[0]
        bare = part.removeprefix("*.")
        if (
            not _HOST.match(part)
            or len(part) > 253
            or (part not in ("*", "localhost") and "." not in bare)
        ):
            raise ValueError(f"“{part}” is not a site name such as example.org.")
        if part not in sites:
            sites.append(part)
    if not sites:
        raise ValueError("List at least one site, or * for every site.")
    if len(sites) > MAX_SITES:
        raise ValueError(f"List at most {MAX_SITES} sites.")
    return sites


def matches(host: str, sites: list[str]) -> bool:
    """The same rule the injected scripts use: a site includes its subdomains."""
    host = host.lower()
    return any(
        site == "*"
        or host == site.removeprefix("*.")
        or host.endswith("." + site.removeprefix("*."))
        for site in sites
    )


def clean(entry: dict) -> dict:
    name = str(entry.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("Give it a name of up to 60 characters.")
    sites = entry.get("sites")
    sites = parse_sites(sites if isinstance(sites, str) else " ".join(sites or []))
    css, js = str(entry.get("css", "")), str(entry.get("js", ""))
    if len(css) > MAX_CSS or len(js) > MAX_JS:
        raise ValueError("Styles are limited to 100 KB and scripts to 200 KB.")
    if not css.strip() and not js.strip():
        raise ValueError("Add some CSS, some JavaScript, or both.")
    when = entry.get("when", "ready")
    if when not in WHEN:
        raise ValueError("Choose when the script runs.")
    identity = entry.get("id")
    if not (isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{32}", identity)):
        identity = uuid4().hex
    return {
        "id": identity,
        "name": name,
        "sites": sites,
        "css": css,
        "js": js,
        "when": when,
        "enabled": entry.get("enabled", True) is not False,
    }


def style_source(entry) -> str:
    return (
        "(() => {"
        + _MATCH % json.dumps(entry["sites"])
        + f"""
  const style = document.createElement("style");
  style.dataset.steedUserStyle = {json.dumps(entry["name"])};
  style.textContent = {json.dumps(entry["css"])};
  const add = () => (document.head || document.documentElement).appendChild(style);
  if (document.documentElement) add();
  else new MutationObserver((_, watch) => {{
    if (document.documentElement) {{ watch.disconnect(); add(); }}
  }}).observe(document, {{childList: true}});
}})();"""
    )


def script_source(entry) -> str:
    # The code runs as written; an error is reported without stopping the page.
    return (
        "(() => {"
        + _MATCH % json.dumps(entry["sites"])
        + "\n  try {\n"
        + entry["js"]
        + f"\n  }} catch (error) {{ console.error({json.dumps('Steed user script ' + entry['name'])}, error); }}\n}})();"
    )


def run_command(entry, page) -> None:
    """Run a command's style and script on a page, now (sites still apply)."""
    if entry["css"].strip():
        page.runJavaScript(style_source(entry), webengine.STEED_WORLD)
    if entry["js"].strip():
        page.runJavaScript(script_source(entry), webengine.PAGE_WORLD)


class UserScripts(QObject):
    changed = pyqtSignal()

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.entries = self._load()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = []
        for item in (data.get("entries") if isinstance(data, dict) else None) or []:
            try:
                entries.append(clean(item))
            except (ValueError, TypeError, AttributeError):
                continue
        return entries[:MAX_ENTRIES]

    def commands(self) -> list[dict]:
        return [e for e in self.entries if e["enabled"] and e["when"] == "command"]

    def get(self, identity):
        return next((e for e in self.entries if e["id"] == identity), None)

    def save(self, entry: dict) -> str:
        entry = clean(entry)
        existing = self.get(entry["id"])
        if existing is not None:
            self.entries[self.entries.index(existing)] = entry
        elif len(self.entries) >= MAX_ENTRIES:
            raise ValueError(f"Keep at most {MAX_ENTRIES} user scripts and styles.")
        else:
            self.entries.append(entry)
        self._write()
        return entry["id"]

    def set_enabled(self, identity, enabled: bool) -> None:
        entry = self.get(identity)
        if entry is None:
            raise ValueError("That entry no longer exists.")
        entry["enabled"] = bool(enabled)
        self._write()

    def remove(self, identity) -> None:
        entry = self.get(identity)
        if entry is not None:
            self.entries.remove(entry)
            self._write()

    def _write(self):
        save_json(self.path, {"version": 1, "entries": self.entries})
        self.changed.emit()

    def install(self, profile) -> None:
        """Replace this list's scripts in an engine profile."""
        collection = profile.scripts()
        for script in collection.toList():
            if script.name().startswith(PREFIX):
                collection.remove(script)
        for entry in self.entries:
            if not entry["enabled"] or entry["when"] == "command":
                continue
            if entry["css"].strip():
                collection.insert(
                    webengine.page_script(
                        PREFIX + entry["id"] + "-css",
                        style_source(entry),
                        world=webengine.STEED_WORLD,
                        when="start",
                        frames=True,
                    )
                )
            if entry["js"].strip():
                collection.insert(
                    webengine.page_script(
                        PREFIX + entry["id"] + "-js",
                        script_source(entry),
                        world=webengine.PAGE_WORLD,
                        when=entry["when"],
                        frames=True,
                    )
                )
