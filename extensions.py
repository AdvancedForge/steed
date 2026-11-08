"""Chrome extensions (Manifest V3) per profile, through Qt WebEngine.

Qt WebEngine installs an extension into the profile's storage folder and loads
it again at every start, but always switched off; Steed remembers which ones
you switched on (<profile>/extensions.json) and switches them back on.
Incognito windows run none: Qt WebEngine has no extensions there.

Supported: unpacked folders, .zip archives and .crx files (the zip inside
them; the store's signature is not checked). Content scripts, background
service workers, storage and toolbar popups work; Qt WebEngine implements only
part of Chrome's extension APIs, so some extensions do not.
"""

import struct
import tempfile
import zipfile
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from utils import load_json, save_json

STATE_FILE = "extensions.json"
RESTORE_ATTEMPTS = 20
RESTORE_INTERVAL_MS = 100


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


def describe(info) -> dict:
    popup = info.actionPopupUrl()
    return {
        "id": info.id(),
        "name": info.name(),
        "description": info.description(),
        "enabled": info.isEnabled(),
        "popup": popup.toString() if popup.isValid() else "",
        "error": info.error(),
    }


class Extensions(QObject):
    """Installed extensions of each stored profile."""

    changed = pyqtSignal(str)  # profile name
    failed = pyqtSignal(str, str)  # profile name, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles = {}  # name -> (engine profile, folder)
        self._pending = {}  # name -> id to switch on once installed

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

    def _state_path(self, name):
        return self.profiles[name][1] / STATE_FILE

    def _enabled_ids(self, name):
        raw = load_json(self._state_path(name), {})
        ids = raw.get("enabled", []) if isinstance(raw, dict) else []
        return [i for i in ids if isinstance(i, str)]

    def _remember(self, name, extension_id, enabled):
        ids = [i for i in self._enabled_ids(name) if i != extension_id]
        if enabled:
            ids.append(extension_id)
        save_json(self._state_path(name), {"enabled": ids})

    def _restore(self, name, attempts):
        """Switch remembered extensions back on once Qt has loaded them."""
        manager = self._manager(name)
        if manager is None:
            return
        wanted = set(self._enabled_ids(name))
        installed = {i.id(): i for i in manager.extensions() if i.isInstalled()}
        for extension_id in wanted & set(installed):
            if not installed[extension_id].isEnabled():
                manager.setExtensionEnabled(installed[extension_id], True)
        if wanted - set(installed) and attempts > 1:
            QTimer.singleShot(
                RESTORE_INTERVAL_MS, lambda: self._restore(name, attempts - 1)
            )
        elif wanted & set(installed):
            self.changed.emit(name)

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
        found = [describe(i) for i in manager.extensions() if i.isInstalled()]
        return sorted(found, key=lambda e: e["name"].lower())

    def install(self, name, path):
        """Install from a folder, .zip or .crx; finishes with changed or failed."""
        manager = self._manager(name)
        if manager is None:
            raise ValueError("Incognito windows have no extensions.")
        path = Path(path)
        if not path.exists():
            raise ValueError("Choose an extension folder, .zip or .crx file.")
        if path.is_file() and path.suffix.lower() == ".crx":
            data = crx_zip(path.read_bytes())
            folder = Path(tempfile.mkdtemp(prefix="steed-crx-"))
            archive = folder / (path.stem + ".zip")
            archive.write_bytes(data)
            path = archive
        if path.is_file() and not zipfile.is_zipfile(path):
            raise ValueError("Choose an extension folder, .zip or .crx file.")
        if path.is_dir() and not (path / "manifest.json").is_file():
            raise ValueError("That folder has no manifest.json.")
        manager.installExtension(str(path.resolve()))

    def _installed(self, name, info):
        if info.error() or not info.isInstalled():
            self.failed.emit(name, info.error() or "The extension did not install.")
            return
        self.set_enabled(name, info.id(), True)

    def set_enabled(self, name, extension_id, enabled):
        info = self._info(name, extension_id)
        if info is None:
            return
        self._manager(name).setExtensionEnabled(info, bool(enabled))
        self._remember(name, extension_id, bool(enabled))
        self.changed.emit(name)

    def remove(self, name, extension_id):
        info = self._info(name, extension_id)
        if info is not None:
            self._remember(name, extension_id, False)
            self._manager(name).uninstallExtension(info)

    def _uninstalled(self, name, info):
        if info.error():
            self.failed.emit(name, info.error())
        self.changed.emit(name)

    def popups(self, name) -> list[dict]:
        """Switched-on extensions with a toolbar popup."""
        return [e for e in self.installed(name) if e["enabled"] and e["popup"]]
