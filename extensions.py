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
import struct
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

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


def crx_zip(data: bytes) -> bytes:
    """The zip archive inside a .crx file (versions 2 and 3)."""
    if data[:4] != b"Cr24" or len(data) < 16:
        raise ValueError("This is not a Chrome extension (.crx) file.")
    version = struct.unpack("<I", data[4:8])[0]
    if version == 3:
        start = 12 + struct.unpack("<I", data[8:12])[0]
    elif version == 2:
        key, signature = struct.unpack("<II", data[8:16])
        start = 16 + key + signature
    else:
        raise ValueError("This .crx file version is not supported.")
    return data[start:]


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
    """enabled: Qt ids switched on; store: wanted store ids; ids: store -> Qt."""
    raw = _dict(load_json(Path(folder) / STATE_FILE, {}))
    store = {}
    for key, value in _dict(raw.get("store")).items():
        if _STORE_ID.match(str(key)) and isinstance(value, dict):
            store[key] = {
                "name": str(value.get("name", ""))[:100],
                "enabled": value.get("enabled") is not False,
            }
    return {
        "enabled": _strings(raw.get("enabled")),
        "store": store,
        "ids": {
            key: value
            for key, value in _dict(raw.get("ids")).items()
            if _STORE_ID.match(str(key)) and isinstance(value, str)
        },
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


class StoreDownload(QObject):
    """Fetch one extension's .crx from the Chrome Web Store."""

    finished = pyqtSignal(str, str, str)  # store id, file path, error

    def __init__(self, network, extension_id, parent=None):
        super().__init__(parent)
        from webengine import versions

        url = STORE_DOWNLOAD.format(
            version=quote(versions()["chromium"]), id=extension_id
        )
        self.extension_id = extension_id
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
        error = path = ""
        if reply.error() != QNetworkReply.NetworkError.NoError:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            error = (
                "The Chrome Web Store has no such extension."
                if status in (204, 404)
                else f"Could not download it: {reply.errorString()}"
            )
        else:
            # A failed reply can return None (see search_suggestions).
            data = bytes(reply.readAll() or b"")
            if data[:4] != b"Cr24":
                error = "The Chrome Web Store sent no extension for that id."
            else:
                with tempfile.NamedTemporaryFile(
                    prefix=f"steed-{self.extension_id}-", suffix=".crx", delete=False
                ) as handle:
                    handle.write(data)
                path = handle.name
        reply.deleteLater()
        self.finished.emit(self.extension_id, path, error)
        self.deleteLater()


class Extensions(QObject):
    """Installed extensions of each stored profile."""

    changed = pyqtSignal(str)  # profile name
    failed = pyqtSignal(str, str)  # profile name, message
    downloaded = pyqtSignal(str, str, str, str)  # profile, store id, path, error

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles = {}  # name -> (engine profile, folder)
        self._pending = {}  # name -> [(store id or None, switch on), ...]
        self._downloads = {}  # (name, store id) -> started by sync
        self._network = None

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
        self.profiles.pop(name, None)

    def _manager(self, name):
        found = self.profiles.get(name)
        return found[0].extensionManager() if found else None

    def folder(self, name):
        return self.profiles[name][1]

    def state(self, name):
        return read_state(self.folder(name))

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
        stores = {qt: store for store, qt in self.state(name)["ids"].items()}
        found = []
        for info in manager.extensions():
            if info.isInstalled():
                entry = describe(info)
                entry["store"] = stores.get(entry["id"], "")
                found.append(entry)
        return sorted(found, key=lambda e: e["name"].lower())

    def details(self, entry) -> dict | None:
        """Version, icon and permissions of an installed extension."""
        try:
            return inspect(entry["path"])
        except (OSError, ValueError):
            return None

    def from_store(self, name, extension_id) -> dict | None:
        """The installed extension that came from this store id."""
        qt_id = self.state(name)["ids"].get(extension_id)
        return next((e for e in self.installed(name) if e["id"] == qt_id), None)

    def install(self, name, path, *, store=None, enabled=True):
        """Install from a folder, .zip or .crx; finishes with changed or failed."""
        manager = self._manager(name)
        if manager is None:
            raise ValueError("Incognito windows have no extensions.")
        path = Path(path)
        inspect(path)  # Refuses what is not a Manifest V3 extension.
        if path.is_file():
            data = path.read_bytes()
            if data[:4] == b"Cr24":
                folder = Path(tempfile.mkdtemp(prefix="steed-crx-"))
                path = folder / (path.stem + ".zip")
                path.write_bytes(crx_zip(data))
        self._pending.setdefault(name, []).append((store, enabled))
        manager.installExtension(str(path.resolve()))

    def _installed(self, name, info):
        pending = self._pending.get(name) or [(None, True)]
        store, enabled = pending.pop(0)
        if info.error() or not info.isInstalled():
            self.failed.emit(name, info.error() or "The extension did not install.")
            return
        if store and name in self.profiles:
            state = self.state(name)
            state["ids"][store] = info.id()
            state["store"][store] = {"name": info.name(), "enabled": bool(enabled)}
            write_state(self.folder(name), state)
        self.set_enabled(name, info.id(), enabled)

    def set_enabled(self, name, extension_id, enabled):
        info = self._info(name, extension_id)
        if info is None:
            return
        self._manager(name).setExtensionEnabled(info, bool(enabled))
        state = self.state(name)
        state["enabled"] = [i for i in state["enabled"] if i != extension_id]
        if enabled:
            state["enabled"].append(extension_id)
        for store, qt_id in state["ids"].items():
            if qt_id == extension_id and store in state["store"]:
                state["store"][store]["enabled"] = bool(enabled)
        write_state(self.folder(name), state)
        self.changed.emit(name)

    def remove(self, name, extension_id):
        info = self._info(name, extension_id)
        state = self.state(name)
        state["enabled"] = [i for i in state["enabled"] if i != extension_id]
        for store in [s for s, q in state["ids"].items() if q == extension_id]:
            state["ids"].pop(store)
            state["store"].pop(store, None)
        write_state(self.folder(name), state)
        if info is not None:
            self._manager(name).uninstallExtension(info)

    def _uninstalled(self, name, info):
        if info.error():
            self.failed.emit(name, info.error())
        self.changed.emit(name)

    def popups(self, name) -> list[dict]:
        """Switched-on extensions with a toolbar popup."""
        return [e for e in self.installed(name) if e["enabled"] and e["popup"]]

    # -- the Chrome Web Store

    def download(self, name, extension_id, *, for_sync=False):
        """Fetch from the store; ends with downloaded(profile, id, path, error)."""
        key = (name, extension_id)
        if key in self._downloads:
            return
        if self._network is None:
            self._network = QNetworkAccessManager(self)
        self._downloads[key] = for_sync
        job = StoreDownload(self._network, extension_id, self)
        job.finished.connect(lambda i, p, e, n=name: self._fetched(n, i, p, e))

    def _fetched(self, name, extension_id, path, error):
        if not self._downloads.pop((name, extension_id), False):
            self.downloaded.emit(name, extension_id, path, error)
            return
        if error:
            self.failed.emit(name, error)
            return
        wanted = (
            self.state(name)["store"].get(extension_id)
            if (name in self.profiles)
            else None
        )
        if wanted is not None:
            try:
                self.install(name, path, store=extension_id, enabled=wanted["enabled"])
            except (OSError, ValueError) as problem:
                self.failed.emit(name, str(problem))

    # -- sync

    def reconcile(self, name):
        """Install, remove or switch store extensions to match the wanted list."""
        if self._manager(name) is None:
            return
        state = self.state(name)
        installed = {e["id"]: e for e in self.installed(name)}
        for store, wanted in state["store"].items():
            qt_id = state["ids"].get(store)
            if qt_id not in installed:
                if not any(p[0] == store for p in self._pending.get(name, [])):
                    self.download(name, store, for_sync=True)
            elif installed[qt_id]["enabled"] != wanted["enabled"]:
                self.set_enabled(name, qt_id, wanted["enabled"])
        for store, qt_id in list(state["ids"].items()):
            if store not in state["store"]:
                if qt_id in installed:
                    self.remove(name, qt_id)
                else:
                    state = self.state(name)
                    state["ids"].pop(store, None)
                    write_state(self.folder(name), state)
