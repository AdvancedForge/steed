"""Chrome extensions (Manifest V3) per profile, through Qt WebEngine.

Qt WebEngine installs an extension into the profile's storage folder and loads
it again at every start, but always switched off; Steed remembers which ones
you switched on (<profile>/extensions.json) and switches them back on.
Incognito windows run none: Qt WebEngine has no extensions there.

Extensions come from unpacked folders, .zip archives, .crx files (the zip
inside them; the store's signature is not checked) or the Chrome Web Store,
downloaded from the address Chrome itself uses. Before installing, Steed reads
the manifest so you can see what an extension asks for.

Extensions from the store are also listed by store id as the ones a profile
should have ("store" in extensions.json); sync shares that list, and each
computer installs, removes or switches extensions to match it.
"""

import io
import json
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from extension_build import (
    build,
    clean_site,
    crx_zip,
    ensure_key,
    keep_key,
    unpack,
)
from utils import load_json, save_json

STATE_FILE = "extensions.json"
RESTORE_ATTEMPTS = 20
RESTORE_INTERVAL_MS = 100
MAX_DOWNLOAD = 200 << 20
STORE = "https://chromewebstore.google.com/"
STORE_DOWNLOAD = (
    "https://clients2.google.com/service/update2/crx?response=redirect"
    "&prodversion={version}&acceptformat=crx2,crx3"
    "&x=id%3D{id}%26installsource%3Dondemand%26uc"
)
STORE_UPDATE = (
    "https://clients2.google.com/service/update2/crx?response=updatecheck"
    "&prodversion={version}&acceptformat=crx2,crx3"
    "&x=id%3D{id}%26v%3D{current}%26uc"
)
SOURCES = "extension-sources"
UPDATES = "extension-updates"
UPDATE_TIMER_MS = 60 * 60 * 1000
UPDATE_EVERY_SECONDS = 24 * 3600
SETTLE_PORTS_MS = 200
_STORE_ID = re.compile(r"^[a-p]{32}$")

# What common permissions mean, in words. Harmless ones are left out.
PERMISSIONS = {
    "tabs": "See the addresses and titles of your tabs",
    "history": "Read and change your browsing history",
    "bookmarks": "Read and change your bookmarks",
    "cookies": "Read and change cookies",
    "downloads": "Manage your downloads",
    "webRequest": "Watch your network requests",
    "webRequestBlocking": "Block or change your network requests",
    "declarativeNetRequest": "Block or change network requests",
    "declarativeNetRequestWithHostAccess": "Block or change network requests",
    "scripting": "Run scripts in pages",
    "activeTab": "Read and change the page when you use it",
    "clipboardRead": "Read what you copy",
    "clipboardWrite": "Change what you copy",
    "notifications": "Show notifications",
    "identity": "Know your Google account",
    "nativeMessaging": "Talk to programs on this computer",
    "debugger": "Control pages with the debugger",
    "management": "Manage your other extensions",
    "proxy": "Change your proxy settings",
    "privacy": "Change privacy settings",
    "geolocation": "Know your location",
    "topSites": "See your most visited sites",
    "pageCapture": "Save pages",
    "desktopCapture": "Capture your screen",
    "tabCapture": "Capture tab sound and video",
    "unlimitedStorage": "Store unlimited data",
}
ALL_SITES = {"<all_urls>", "*://*/*", "http://*/*", "https://*/*"}
# The chrome.* APIs Qt WebEngine 6.11 gives extensions (measured: an extension
# page listing Object.keys(chrome) with every permission below granted).
ENGINE_APIS = {
    "alarms",
    "declarativeNetRequest",
    "i18n",
    "management",
    "offscreen",
    "runtime",
    "storage",
    "tabs",
    "webRequest",
}
# Permissions that name a chrome.* API; the others (activeTab, hosts) do not.
API_PERMISSIONS = ENGINE_APIS | {
    "bookmarks",
    "contextMenus",
    "cookies",
    "debugger",
    "desktopCapture",
    "downloads",
    "history",
    "identity",
    "notifications",
    "pageCapture",
    "privacy",
    "proxy",
    "scripting",
    "sidePanel",
    "tabCapture",
    "topSites",
    "userScripts",
    "webNavigation",
}


def store_id(url) -> str | None:
    """The extension id in a Chrome Web Store address (or a bare id)."""
    text = str(url).strip()
    if _STORE_ID.match(text):
        return text
    match = re.match(
        r"^https://(chromewebstore\.google\.com|chrome\.google\.com/webstore)"
        r"/detail/(?:[^/?#]+/)?([a-p]{32})(?:[/?#]|$)",
        text,
    )
    return match.group(2) if match else None


class _Files:
    """Read files from an extension folder or archive by relative path."""

    def __init__(self, path: Path):
        self.folder = self.archive = None
        if path.is_dir():
            self.folder = path
            return
        data = path.read_bytes()
        if data[:4] == b"Cr24":
            data = crx_zip(data)
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("Choose an extension folder, .zip or .crx file.") from None

    def read(self, name, limit=2 << 20) -> bytes | None:
        name = str(name).lstrip("/")
        if not name or ".." in Path(name).parts:
            return None
        try:
            if self.folder is not None:
                path = self.folder / name
                if path.is_file() and path.stat().st_size <= limit:
                    return path.read_bytes()
                return None
            info = self.archive.getinfo(name)
            return self.archive.read(info) if info.file_size <= limit else None
        except (KeyError, OSError, zipfile.BadZipFile):
            return None


def _localized(text, manifest, files):
    """Resolve __MSG_name__ from the extension's default locale."""
    match = re.fullmatch(r"__MSG_(\w+)__", str(text or ""))
    if not match:
        return str(text or "")
    locale = manifest.get("default_locale") or "en"
    raw = files.read(f"_locales/{locale}/messages.json")
    try:
        messages = json.loads(raw.decode("utf-8-sig")) if raw else {}
    except ValueError:
        messages = {}
    wanted = match.group(1).lower()
    for key, value in messages.items() if isinstance(messages, dict) else []:
        if key.lower() == wanted and isinstance(value, dict):
            return str(value.get("message", ""))
    return match.group(1)


def _icon_path(manifest):
    """The largest manifest icon up to 128 pixels, or the toolbar icon."""
    choices = {}
    action = manifest.get("action")
    for value in (
        manifest.get("icons"),
        action.get("default_icon") if isinstance(action, dict) else None,
    ):
        if isinstance(value, str):
            choices.setdefault(32, value)
        elif isinstance(value, dict):
            for size, path in value.items():
                if str(size).isdigit() and isinstance(path, str):
                    choices.setdefault(int(size), path)
    fitting = [size for size in choices if size <= 128] or list(choices)
    return choices[max(fitting)] if fitting else None


def _strings(value):
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def permission_lines(manifest) -> list[str]:
    """What an extension asks for, in plain words."""
    asked = _strings(manifest.get("permissions"))
    hosts = _strings(manifest.get("host_permissions"))
    hosts += [p for p in asked if "/" in p or p == "<all_urls>"]
    scripts = manifest.get("content_scripts")
    for script in scripts if isinstance(scripts, list) else []:
        if isinstance(script, dict):
            hosts += _strings(script.get("matches"))
    lines = []
    if ALL_SITES & set(hosts):
        lines.append("Read and change everything on all websites")
    else:
        sites = sorted({h.split("://")[-1].split("/")[0] for h in hosts if h})
        if sites:
            shown = ", ".join(sites[:5]) + (" and more" if len(sites) > 5 else "")
            lines.append(f"Read and change pages on {shown}")
    for name in dict.fromkeys(asked):
        if name in PERMISSIONS:
            lines.append(PERMISSIONS[name])
    return lines


def unsupported(manifest) -> list[str]:
    """Chrome APIs the extension asks for that Qt WebEngine does not have."""
    asked = dict.fromkeys(_strings(manifest.get("permissions")))
    return [p for p in asked if p in API_PERMISSIONS and p not in ENGINE_APIS]


def inspect(path) -> dict:
    """An extension's name, version, icon and permissions, before installing."""
    path = Path(path)
    if not path.exists():
        raise ValueError("Choose an extension folder, .zip or .crx file.")
    files = _Files(path)
    raw = files.read("manifest.json")
    if raw is None:
        raise ValueError("This has no manifest.json, so it is not an extension.")
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        raise ValueError("The extension's manifest.json is damaged.") from None
    if not isinstance(manifest, dict):
        raise ValueError("The extension's manifest.json is damaged.")
    if manifest.get("manifest_version") != 3:
        raise ValueError(
            "This extension uses Manifest V2, which Qt WebEngine (like current "
            "Chrome) no longer runs; look for a Manifest V3 version."
        )
    icon_name = _icon_path(manifest)
    description = _localized(manifest.get("description"), manifest, files)
    return {
        "name": _localized(manifest.get("name"), manifest, files)[:100] or "?",
        "version": str(manifest.get("version", ""))[:40],
        "description": description[:300],
        "permissions": permission_lines(manifest),
        "unsupported": unsupported(manifest),
        "icon": files.read(icon_name, 1 << 20) if icon_name else None,
    }


def icon_from(data) -> QIcon:
    pixmap = QPixmap()
    if data and pixmap.loadFromData(data):
        return QIcon(pixmap)
    return QIcon()


def describe(info) -> dict:
    popup = info.actionPopupUrl()
    return {
        "id": info.id(),
        "name": info.name(),
        "description": info.description(),
        "enabled": info.isEnabled(),
        "popup": popup.toString() if popup.isValid() else "",
        "error": info.error(),
        "path": info.path(),
    }


# ------------------------------------------------------------ profile state


def _dict(value):
    return value if isinstance(value, dict) else {}


def read_state(folder) -> dict:
    """What Steed keeps per profile in extensions.json.

    enabled: ids switched on; store: wanted store ids (synced); ids: store id
    -> installed id; pinned: ids with a toolbar button; sites: id -> the only
    sites it may use; updates: store id -> waiting version; checked: time of
    the last update check; menus and scripts: id -> its context menu items
    and registered content scripts (extension_bridge).
    """
    raw = _dict(load_json(Path(folder) / STATE_FILE, {}))
    store = {}
    for key, value in _dict(raw.get("store")).items():
        if _STORE_ID.match(str(key)) and isinstance(value, dict):
            store[key] = {
                "name": str(value.get("name", ""))[:100],
                "enabled": value.get("enabled") is not False,
            }
    sites = {}
    for key, value in _dict(raw.get("sites")).items():
        if isinstance(key, str) and isinstance(value, list):
            sites[key] = [s for s in _strings(value) if clean_site(s) == s][:200]
    checked = raw.get("checked")
    lists = {}
    for field in ("menus", "scripts"):
        lists[field] = {
            key: [item for item in value if isinstance(item, dict) and "id" in item]
            for key, value in _dict(raw.get(field)).items()
            if isinstance(key, str) and isinstance(value, list)
        }
    return {
        **lists,
        "enabled": _strings(raw.get("enabled")),
        "store": store,
        "ids": {
            key: value
            for key, value in _dict(raw.get("ids")).items()
            if _STORE_ID.match(str(key)) and isinstance(value, str)
        },
        "pinned": _strings(raw.get("pinned")),
        "sites": sites,
        "updates": {
            key: str(value)[:40]
            for key, value in _dict(raw.get("updates")).items()
            if _STORE_ID.match(str(key))
        },
        "checked": float(checked) if isinstance(checked, (int, float)) else 0.0,
    }


def write_state(folder, state) -> None:
    save_json(Path(folder) / STATE_FILE, state)


def synced(folder) -> dict:
    """store id -> {"name", "enabled"}: the store extensions a profile wants."""
    return read_state(folder)["store"]


def set_synced(folder, wanted) -> None:
    """Change the wanted list (from sync); Extensions.reconcile follows it."""
    state = read_state(folder)
    state["store"] = {
        key: {
            "name": str(value.get("name", ""))[:100],
            "enabled": bool(value["enabled"]),
        }
        for key, value in wanted.items()
        if _STORE_ID.match(str(key))
    }
    write_state(folder, state)


def version_key(text) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", str(text))[:4])


def update_version(xml: bytes, extension_id: str) -> str | None:
    """The newer version an update check offers, if any."""
    import xml.etree.ElementTree as ElementTree

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    for app in root.iter():
        if app.tag.split("}")[-1] == "app" and app.get("appid") == extension_id:
            for check in app:
                if check.tag.split("}")[-1] == "updatecheck":
                    if check.get("status") == "ok" and check.get("version"):
                        return check.get("version")
    return None


class Fetch(QObject):
    """One GET request; finished(data, error), data None when it failed."""

    finished = pyqtSignal(object, str)

    def __init__(self, network, url, parent=None):
        super().__init__(parent)
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        self.reply = network.get(request)
        self.reply.downloadProgress.connect(self._progress)
        self.reply.finished.connect(self._done)

    def _progress(self, received, total):
        if received > MAX_DOWNLOAD or total > MAX_DOWNLOAD:
            self.reply.abort()

    def _done(self):
        reply = self.reply
        if reply.error() != QNetworkReply.NetworkError.NoError:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            error = (
                "The Chrome Web Store has no such extension."
                if status in (204, 404)
                else f"Could not reach the Chrome Web Store: {reply.errorString()}"
            )
            self.finished.emit(None, error)
        else:
            # A failed reply can return None (see search_suggestions).
            self.finished.emit(bytes(reply.readAll() or b""), "")
        reply.deleteLater()
        self.deleteLater()


def settle_ports():
    """Let Chromium close extensions' message ports (content scripts, bridge
    pages) after their pages went: one closed after its profile stops Steed."""
    from PyQt6.QtCore import QCoreApplication, QElapsedTimer, QEventLoop

    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < SETTLE_PORTS_MS:
        QCoreApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 20
        )


def _chromium():
    from webengine import versions

    return quote(versions()["chromium"])


class Extensions(QObject):
    """Installed extensions of each stored profile."""

    changed = pyqtSignal(str)  # profile name
    failed = pyqtSignal(str, str)  # profile name, message
    downloaded = pyqtSignal(str, str, str, str)  # profile, store id, path, error
    updates_checked = pyqtSignal(str, str)  # profile name, summary

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles = {}  # name -> (engine profile, folder)
        # name -> id -> {"store", "enabled", "build", "reinstall"}
        self._pending = {}
        self._downloads = {}  # (name, store id) -> "user", "sync" or "update"
        self._checks = {}  # name -> store ids still being checked
        self._network = None
        self.auto_update = QTimer(self)
        self.auto_update.setInterval(UPDATE_TIMER_MS)
        self.auto_update.timeout.connect(self._auto_update)
        self.auto_update.start()
        from extension_bridge import Bridges

        self.bridges = Bridges(self)
        self.changed.connect(self.bridges.sync)

    def attach(self, name, profile, folder):
        manager = profile.extensionManager()
        if manager is None:
            return
        self.profiles[name] = (profile, Path(folder))
        manager.installFinished.connect(lambda info, n=name: self._installed(n, info))
        manager.uninstallFinished.connect(
            lambda info, n=name: self._uninstalled(n, info)
        )
        self._restore(name, RESTORE_ATTEMPTS)

    def detach(self, name):
        """Before a profile goes: its bridge pages, then its message ports."""
        if name not in self.profiles:
            return
        busy = bool(self.installed(name))
        self.bridges.close(name)
        if busy:
            settle_ports()
        self.profiles.pop(name, None)

    def close(self):
        """Before every profile goes (Steed quits): likewise."""
        busy = any(self.installed(name) for name in self.profiles)
        self.bridges.close()
        if busy:
            settle_ports()
        self.profiles.clear()

    def _manager(self, name):
        found = self.profiles.get(name)
        return found[0].extensionManager() if found else None

    def folder(self, name):
        return self.profiles[name][1]

    def state(self, name):
        return read_state(self.folder(name))

    def _write(self, name, state):
        write_state(self.folder(name), state)

    def source(self, name, extension_id) -> Path:
        """The extension's own files, as Steed received them (with its key)."""
        return self.folder(name) / SOURCES / extension_id

    def _restore(self, name, attempts):
        """Switch remembered extensions back on once Qt has loaded them."""
        manager = self._manager(name)
        if manager is None:
            return
        state = self.state(name)
        wanted = set(state["enabled"])
        known = wanted | set(state["ids"].values())
        installed = {i.id(): i for i in manager.extensions() if i.isInstalled()}
        for extension_id in wanted & set(installed):
            if not installed[extension_id].isEnabled():
                manager.setExtensionEnabled(installed[extension_id], True)
        # Qt loads installed extensions just after start: wait for all of them,
        # so reconcile() does not fetch again what is merely still loading.
        if known - set(installed) and attempts > 1:
            QTimer.singleShot(
                RESTORE_INTERVAL_MS, lambda: self._restore(name, attempts - 1)
            )
            return
        if wanted & set(installed):
            self.changed.emit(name)
        self.reconcile(name)

    def _info(self, name, extension_id):
        manager = self._manager(name)
        for info in manager.extensions() if manager else []:
            if info.id() == extension_id:
                return info
        return None

    # -- what the interface uses

    def available(self, name):
        return self._manager(name) is not None

    def installed(self, name) -> list[dict]:
        """Extensions you added (Qt's built-in PDF viewer and others left out)."""
        manager = self._manager(name)
        if manager is None:
            return []
        state = self.state(name)
        stores = {qt: store for store, qt in state["ids"].items()}
        found = []
        for info in manager.extensions():
            if info.isInstalled():
                entry = describe(info)
                entry["store"] = stores.get(entry["id"], "")
                entry["pinned"] = entry["id"] in state["pinned"]
                entry["sites"] = state["sites"].get(entry["id"])
                entry["update"] = state["updates"].get(entry["store"], "")
                source = self.source(name, entry["id"])
                entry["source"] = str(source if source.is_dir() else entry["path"])
                manifest = _dict(self.bridges.manifest(entry))
                entry["action"] = isinstance(manifest.get("action"), dict)
                entry["commands"] = _dict(manifest.get("commands"))
                found.append(entry)
        return sorted(found, key=lambda e: e["name"].lower())

    def details(self, entry) -> dict | None:
        """Version, icon and permissions as the extension itself asks."""
        try:
            return inspect(entry["source"])
        except (OSError, ValueError):
            return None

    def from_store(self, name, extension_id) -> dict | None:
        """The installed extension that came from this store id."""
        qt_id = self.state(name)["ids"].get(extension_id)
        return next((e for e in self.installed(name) if e["id"] == qt_id), None)

    def install(self, name, path, *, store=None, enabled=True):
        """Install (or install again) from a folder, .zip or .crx.

        Finishes with changed or failed. The extension keeps its id, so an
        update keeps its data."""
        if self._manager(name) is None:
            raise ValueError("Incognito windows have no extensions.")
        path = Path(path)
        inspect(path)  # Refuses what is not a Manifest V3 extension.
        sources = self.folder(name) / SOURCES
        sources.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".new-", dir=sources))
        try:
            shutil.rmtree(staging)
            crx = unpack(path, staging)
            known = self.state(name)["ids"].get(store) if store else None
            if known and self.source(name, known).is_dir():
                # An update keeps the id (and data) of the one installed.
                keep_key(self.source(name, known), staging)
            extension_id = ensure_key(staging, crx, store)
            target = sources / extension_id
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        self._deploy(name, extension_id, store=store, enabled=enabled)
        return extension_id

    def _deploy(self, name, extension_id, *, store=None, enabled=True):
        """Build from the source and install it, replacing an installed copy."""
        state = self.state(name)
        build_root = Path(tempfile.mkdtemp(prefix="steed-extension-"))
        build_dir = build_root / extension_id
        build(
            self.source(name, extension_id),
            build_dir,
            state["sites"].get(extension_id),
        )
        current = self._info(name, extension_id)
        self._pending.setdefault(name, {})[extension_id] = {
            "store": store
            or next((s for s, q in state["ids"].items() if q == extension_id), None),
            "enabled": enabled,
            "build": build_root,
            "reinstall": current is not None,
        }
        if current is not None:
            # Qt does not replace an installed extension: remove, then install.
            self.bridges.drop(name, extension_id)
            self._manager(name).uninstallExtension(current)
        else:
            self._manager(name).installExtension(str(build_dir))

    def _installed(self, name, info):
        pending = self._pending.get(name, {}).pop(
            info.id(), {"store": None, "enabled": True, "build": None}
        )
        if pending["build"] is not None:
            shutil.rmtree(pending["build"], ignore_errors=True)
        if info.error() or not info.isInstalled():
            self.failed.emit(name, info.error() or "The extension did not install.")
            return
        if name not in self.profiles:
            return
        state = self.state(name)
        if pending["store"]:
            state["ids"][pending["store"]] = info.id()
            state["store"][pending["store"]] = {
                "name": info.name(),
                "enabled": bool(pending["enabled"]),
            }
            state["updates"].pop(pending["store"], None)
        self._write(name, state)
        self.set_enabled(name, info.id(), pending["enabled"])

    def _uninstalled(self, name, info):
        pending = self._pending.get(name, {}).get(info.id())
        if pending is not None and pending.get("reinstall"):
            pending["reinstall"] = False
            self._manager(name).installExtension(str(pending["build"] / info.id()))
            return
        if info.error():
            self.failed.emit(name, info.error())
        elif name in self.profiles:
            shutil.rmtree(self.source(name, info.id()), ignore_errors=True)
        self.changed.emit(name)

    def set_enabled(self, name, extension_id, enabled):
        info = self._info(name, extension_id)
        if info is None:
            return
        if not enabled:
            self.bridges.drop(name, extension_id)
        self._manager(name).setExtensionEnabled(info, bool(enabled))
        state = self.state(name)
        state["enabled"] = [i for i in state["enabled"] if i != extension_id]
        if enabled:
            state["enabled"].append(extension_id)
        for store, qt_id in state["ids"].items():
            if qt_id == extension_id and store in state["store"]:
                state["store"][store]["enabled"] = bool(enabled)
        self._write(name, state)
        self.changed.emit(name)

    def remove(self, name, extension_id):
        info = self._info(name, extension_id)
        state = self.state(name)
        state["enabled"] = [i for i in state["enabled"] if i != extension_id]
        state["pinned"] = [i for i in state["pinned"] if i != extension_id]
        state["sites"].pop(extension_id, None)
        state["menus"].pop(extension_id, None)
        state["scripts"].pop(extension_id, None)
        for store in [s for s, q in state["ids"].items() if q == extension_id]:
            state["ids"].pop(store)
            state["store"].pop(store, None)
            state["updates"].pop(store, None)
        self._write(name, state)
        if info is not None:
            self.bridges.drop(name, info.id())
            self._manager(name).uninstallExtension(info)

    def popups(self, name) -> list[dict]:
        """Switched-on extensions with a toolbar popup."""
        return [e for e in self.installed(name) if e["enabled"] and e["popup"]]

    # -- toolbar buttons and site access

    def clickable(self, name) -> list[dict]:
        """Switched-on extensions with a toolbar action (a popup or a click)."""
        return [
            e
            for e in self.installed(name)
            if e["enabled"] and (e["popup"] or e["action"])
        ]

    def pinned(self, name) -> list[dict]:
        """Pinned extensions, in the order they were pinned."""
        order = self.state(name)["pinned"] if self.available(name) else []
        found = {e["id"]: e for e in self.clickable(name)}
        return [found[i] for i in order if i in found]

    def set_pinned(self, name, extension_id, pinned):
        state = self.state(name)
        state["pinned"] = [i for i in state["pinned"] if i != extension_id]
        if pinned:
            state["pinned"].append(extension_id)
        self._write(name, state)
        self.changed.emit(name)

    def _adopt(self, name, entry) -> str:
        """Keep the files of an extension installed before Steed kept them."""
        if self.source(name, entry["id"]).is_dir():
            return entry["id"]
        info = self._info(name, entry["id"])
        new_id = self.install(
            name, entry["path"], store=entry["store"] or None, enabled=entry["enabled"]
        )
        if new_id != entry["id"] and info is not None:
            state = self.state(name)
            state["enabled"] = [i for i in state["enabled"] if i != entry["id"]]
            state["pinned"] = [
                new_id if i == entry["id"] else i for i in state["pinned"]
            ]
            self._write(name, state)
            self.bridges.drop(name, info.id())
            self._manager(name).uninstallExtension(info)
        return new_id

    def set_sites(self, name, extension_id, sites):
        """Limit an extension to these sites (None: all it asks for)."""
        entry = next((e for e in self.installed(name) if e["id"] == extension_id), None)
        if entry is None:
            return
        if sites is not None:
            sites = sorted({s for s in map(clean_site, sites) if s})
        state = self.state(name)
        if sites is None:
            state["sites"].pop(extension_id, None)
        else:
            state["sites"][extension_id] = sites
        self._write(name, state)
        if self.source(name, extension_id).is_dir():
            self._deploy(name, extension_id, enabled=entry["enabled"])
        else:
            new_id = self._adopt(name, entry)
            state = self.state(name)
            if sites is None:
                state["sites"].pop(new_id, None)
            else:
                state["sites"][new_id] = sites
            self._write(name, state)
            self._deploy(name, new_id, enabled=entry["enabled"])

    # -- the Chrome Web Store

    def _get(self, url, done):
        if self._network is None:
            self._network = QNetworkAccessManager(self)
        job = Fetch(self._network, url, self)
        job.finished.connect(done)

    def download(self, name, extension_id, *, purpose="user"):
        """Fetch from the store; for "user", ends with downloaded(...)."""
        key = (name, extension_id)
        if key in self._downloads:
            return
        self._downloads[key] = purpose
        url = STORE_DOWNLOAD.format(version=_chromium(), id=extension_id)
        self._get(
            url,
            lambda data, error, n=name, i=extension_id: self._fetched(
                n, i, data, error
            ),
        )

    def _fetched(self, name, extension_id, data, error):
        purpose = self._downloads.pop((name, extension_id), "user")
        path = ""
        if not error and data[:4] != b"Cr24":
            error = "The Chrome Web Store sent no extension for that id."
        if not error:
            with tempfile.NamedTemporaryFile(
                prefix=f"steed-{extension_id}-", suffix=".crx", delete=False
            ) as handle:
                handle.write(data)
            path = handle.name
        if purpose == "user":
            self.downloaded.emit(name, extension_id, path, error)
            return
        if error:
            self.failed.emit(name, error)
            return
        if name not in self.profiles:
            return
        if purpose == "update":
            self._update_downloaded(name, extension_id, Path(path))
            return
        wanted = self.state(name)["store"].get(extension_id)
        if wanted is not None:
            try:
                self.install(name, path, store=extension_id, enabled=wanted["enabled"])
            except (OSError, ValueError) as problem:
                self.failed.emit(name, str(problem))

    # -- updates

    def check_updates(self, name):
        """Ask the store about each store extension; ends with updates_checked."""
        if not self.available(name) or self._checks.get(name):
            return
        state = self.state(name)
        state["checked"] = time.time()
        self._write(name, state)
        installed = {e["id"]: e for e in self.installed(name)}
        asks = {
            store: installed[qt_id]
            for store, qt_id in state["ids"].items()
            if qt_id in installed and store in state["store"]
        }
        if not asks:
            self.updates_checked.emit(name, "No extensions from the store to update.")
            return
        self._checks[name] = {"waiting": set(asks), "newer": [], "failed": 0}
        for store, entry in asks.items():
            current = (self.details(entry) or {}).get("version") or "0"
            url = STORE_UPDATE.format(
                version=_chromium(), id=store, current=quote(current)
            )
            self._get(url, self._check_answer(name, store, current))

    def _check_answer(self, name, store, current):
        return lambda data, error: self._checked(name, store, current, data, error)

    def _checked(self, name, store, current, data, error):
        check = self._checks.get(name)
        if check is None:
            return
        check["waiting"].discard(store)
        offered = update_version(data, store) if data else None
        if error:
            check["failed"] += 1
        elif offered and version_key(offered) > version_key(current):
            check["newer"].append(store)
            self.download(name, store, purpose="update")
        if not check["waiting"]:
            del self._checks[name]
            if check["newer"]:
                summary = f"Updating {len(check['newer'])} extension(s)…"
            elif check["failed"]:
                summary = "Could not reach the Chrome Web Store to check for updates."
            else:
                summary = "Your extensions are up to date."
            self.updates_checked.emit(name, summary)

    def _update_downloaded(self, name, store, path):
        """Install an update, unless it asks for more than before."""
        entry = self.from_store(name, store)
        if entry is None:
            return
        try:
            new = inspect(path)
        except (OSError, ValueError) as error:
            self.failed.emit(name, str(error))
            return
        old = self.details(entry) or {"permissions": []}
        if set(new["permissions"]) <= set(old["permissions"]):
            self.install(name, path, store=store, enabled=entry["enabled"])
            return
        waiting = self.folder(name) / UPDATES
        waiting.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, waiting / f"{store}.crx")
        state = self.state(name)
        state["updates"][store] = new["version"]
        self._write(name, state)
        self.changed.emit(name)

    def waiting_update(self, name, store) -> Path | None:
        """A downloaded update that asks for more, waiting for your yes."""
        path = self.folder(name) / UPDATES / f"{store}.crx"
        return path if path.is_file() else None

    def apply_update(self, name, store):
        path = self.waiting_update(name, store)
        entry = self.from_store(name, store)
        if path is None or entry is None:
            return
        self.install(name, path, store=store, enabled=entry["enabled"])
        path.unlink(missing_ok=True)

    def _auto_update(self):
        for name in list(self.profiles):
            if time.time() - self.state(name)["checked"] > UPDATE_EVERY_SECONDS:
                self.check_updates(name)

    # -- sync

    def reconcile(self, name):
        """Install, remove or switch store extensions to match the wanted list."""
        if self._manager(name) is None:
            return
        state = self.state(name)
        installed = {e["id"]: e for e in self.installed(name)}
        pending = {p.get("store") for p in self._pending.get(name, {}).values()}
        for store, wanted in state["store"].items():
            qt_id = state["ids"].get(store)
            if qt_id not in installed:
                if store not in pending:
                    self.download(name, store, purpose="sync")
            elif installed[qt_id]["enabled"] != wanted["enabled"]:
                self.set_enabled(name, qt_id, wanted["enabled"])
        for store, qt_id in list(state["ids"].items()):
            if store not in state["store"]:
                if qt_id in installed:
                    self.remove(name, qt_id)
                else:
                    state = self.state(name)
                    state["ids"].pop(store, None)
                    self._write(name, state)
