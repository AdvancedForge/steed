"""Site apps: a website in its own window, with its own icon and menu entry.

Tabs → Install This Site as an App… keeps the site in <config>/site_apps.json
and writes a desktop entry (~/.local/share/applications/steed-app-<id>.desktop)
that starts `steed --app=<address>`. An app window has no tab strip or
address bar; links to other sites open in a normal Steed window. It uses the
profile that is current when it opens, with that profile's sign-ins.
"""

import hashlib
import json
from pathlib import Path

from PyQt6.QtCore import QUrl

from browser import default_browser

APP_PREFIX = "app:"
MAX_APPS = 100


def app_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def site_key(url: QUrl) -> str:
    """The site an app stays on: its host without www."""
    return url.host().lower().removeprefix("www.")


class SiteApps:
    def __init__(self, config_dir: Path):
        self.path = Path(config_dir) / "site_apps.json"
        self.icons = Path(config_dir) / "site_apps"

    def list(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        return [a for a in data if isinstance(a, dict) and "url" in a][:MAX_APPS]

    def _write(self, apps):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(apps, indent=1))

    def entry_path(self, identity) -> Path:
        return default_browser.applications_dir() / f"steed-app-{identity}.desktop"

    def install(self, url: str, name: str, icon=None) -> dict:
        """Remember the app and add it to the applications menu. icon: a
        QIcon or QPixmap (saved as PNG), or None for Steed's own."""
        found = QUrl(url)
        if found.scheme() not in ("http", "https") or not found.host():
            raise ValueError("Only websites can be installed as apps.")
        name = " ".join((name or found.host()).split())[:80] or found.host()
        if any(c in name for c in "\n\r"):
            raise ValueError("That name cannot be used.")
        identity = app_id(found.toString())
        icon_path = ""
        if icon is not None:
            self.icons.mkdir(parents=True, exist_ok=True)
            target = self.icons / f"{identity}.png"
            pixmap = icon.pixmap(128, 128) if hasattr(icon, "pixmap") else icon
            if not pixmap.isNull() and pixmap.save(str(target), "PNG"):
                icon_path = str(target)
        apps = [a for a in self.list() if a.get("id") != identity]
        if len(apps) >= MAX_APPS:
            raise ValueError(f"Keep at most {MAX_APPS} apps.")
        app = {"id": identity, "name": name, "url": found.toString(), "icon": icon_path}
        apps.append(app)
        self._write(apps)
        self._write_entry(app)
        return app

    def _write_entry(self, app):
        from browser.install_launcher import desktop_entry

        project = Path(__file__).resolve().parents[1]
        base = desktop_entry(project)
        argument = "--app=" + app["url"]
        # Desktop-entry Exec escaping: quote, escape \\ " ` $ and double %.
        quoted = "".join("\\" + c if c in '\\"`$' else c for c in argument)
        quoted = quoted.replace("\\", "\\\\").replace("%", "%%")
        lines = []
        for line in base.splitlines():
            if line.startswith("Name="):
                line = "Name=" + app["name"]
            elif line.startswith("Comment="):
                line = "Comment=" + app["url"].replace("\n", "")
            elif line.startswith("Exec="):
                line = line.replace(" %U", f' "{quoted}"')
            elif line.startswith("Icon=") and app["icon"]:
                line = "Icon=" + app["icon"]
            elif line.startswith(("MimeType=", "Categories=")):
                continue
            lines.append(line)
        lines.append("Categories=Network;")
        path = self.entry_path(app["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def remove(self, identity: str):
        apps = self.list()
        kept = [a for a in apps if a.get("id") != identity]
        if len(kept) == len(apps):
            return False
        self._write(kept)
        for path in (self.entry_path(identity), self.icons / f"{identity}.png"):
            try:
                path.unlink()
            except OSError:
                pass
        return True


def launch_app(argument: str) -> str | None:
    """The address in a --app=<address> argument, or None."""
    if not argument.startswith("--app="):
        return None
    url = QUrl(argument.removeprefix("--app="))
    if url.scheme() in ("http", "https") and url.host():
        return url.toString()
    return None
