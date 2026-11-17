"""Sync between computers through a folder you choose (General → Sync…).

Each computer writes its own file, <folder>/steed-sync/device-<id>.json, and
reads the others'; a folder kept in step by Syncthing, Nextcloud, Dropbox or a
USB stick carries them. Nothing goes to a Steed server.

Every item (a bookmark by address, a saved group, a reading-list page by
address, a search keyword, a Chrome Web Store extension by id, a login by site
and username) is a record with the
time it last changed; deleting one leaves a dated marker. Merging keeps each
item's newest record, so changes and deletions on any computer reach the
others. On a computer's first sync its items count as oldest, so they never
overwrite newer changes made elsewhere. Markers older than 90 days are dropped.

Profiles sync by name, between profiles that exist on both computers; incognito
never syncs. Passwords and passkeys sync only with a sync passphrase: each
profile's logins and passkeys are sealed with a key stretched from it (Argon2id, then XSalsa20-Poly1305), so
the folder holds no readable password, and a computer syncs them only while
that profile's vault is unlocked.
"""

import base64
import json
import secrets
import socket
import time
from pathlib import Path

import nacl.exceptions
import nacl.pwhash
import nacl.secret
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from browser import extensions, search_keywords
from browser.password_vault import MEMLIMIT, OPSLIMIT, VaultError, master_key
from browser.reading_list import ReadingList, _entry
from browser.tab_manager import _saved_group
from browser.utils import load_json, save_json

VERSION = 1
FOLDER = "steed-sync"
KEY_FILE = "passwords.json"
TOMBSTONE_SECONDS = 90 * 24 * 3600
MAX_FILE_BYTES = 64 << 20
PERIOD_MS = 5 * 60 * 1000
SETTLE_MS = 5000
KEYRING_ATTRIBUTES = {"application": "steed", "purpose": "sync-passwords"}
CHECK = b"steed sync passphrase"


# ------------------------------------------------------------- merging records


def _valid(record):
    return (
        isinstance(record, dict)
        and isinstance(record.get("m"), (int, float))
        and "d" in record
    )


def own_records(previous, local, now, *, first=False, known=()):
    """This computer's records: unchanged items keep their time, changes get now.

    previous is what this computer wrote last time. On its first sync it has
    none, and items another computer already knows get time 0, so the other
    computer's version (or deletion) wins."""
    records = {}
    for key, data in local.items():
        old = previous.get(key)
        if _valid(old) and old["d"] == data:
            records[key] = old
        else:
            stale = first and key in known
            records[key] = {"m": 0.0 if stale else now, "d": data}
    for key, old in previous.items():
        if key not in local and _valid(old):
            records[key] = old if old["d"] is None else {"m": now, "d": None}
    return records


def merge(sources, now):
    """The newest record of each item from [(device, records), ...]."""
    best = {}
    for device, records in sources:
        for key, record in records.items() if isinstance(records, dict) else []:
            if not isinstance(key, str) or not _valid(record):
                continue
            rank = (float(record["m"]), device)
            if key not in best or rank > best[key][0]:
                best[key] = (rank, record)
    return {
        key: record
        for key, (_, record) in best.items()
        if record["d"] is not None or now - record["m"] < TOMBSTONE_SECONDS
    }


def items(records, clean):
    """key -> data of the live records whose data is still valid."""
    found = {}
    for key, record in records.items():
        if record["d"] is not None:
            data = clean(record["d"])
            if data is not None:
                found[key] = data
    return found


# ------------------------------------------------------- what each item looks like


def _bookmark(data):
    if (
        isinstance(data, dict)
        and isinstance(data.get("url"), str)
        and isinstance(data.get("title"), str)
        and isinstance(data.get("folder", ""), str)
    ):
        return {"url": data["url"], "title": data["title"], "folder": data["folder"]}
    return None


def _extension(data):
    if (
        isinstance(data, dict)
        and isinstance(data.get("name"), str)
        and isinstance(data.get("enabled"), bool)
    ):
        return {"name": data["name"][:100], "enabled": data["enabled"]}
    return None


def _keyword(data):
    try:
        _, name, url = search_keywords.clean("k", *data)
    except (TypeError, ValueError):
        return None
    return [name, url]


def _login(data):
    names = ("origin", "username", "password", "title", "totp", "notes")
    if not isinstance(data, dict) or not all(
        isinstance(data.get(name), str) for name in names
    ):
        return None
    if not all(isinstance(data.get(n), (int, float)) for n in ("created", "updated")):
        return None
    return {
        **{name: data[name] for name in names},
        **{n: float(data[n]) for n in ("created", "updated")},
    }


def _passkey(data):
    from browser.password_vault import _passkey as clean

    record = clean({**data, "last_used": 0.0}) if isinstance(data, dict) else None
    if record is not None:
        record.pop("last_used")  # Each computer keeps its own.
    return record


class Collection:
    """One kind of item in one place: read() and write() its key -> data."""

    def __init__(self, read, write, clean):
        self.read, self.write, self.clean = read, write, clean


def profile_collections(registry, name, directory):
    """Bookmarks, saved groups and the reading list of one profile."""
    pages = registry.start_pages
    groups_file = directory / "saved_groups.json"
    reading = ReadingList(directory)

    def read_groups():
        raw = load_json(groups_file, [])
        found = [_saved_group(item) for item in raw] if isinstance(raw, list) else []
        return {group["group_id"]: group for group in found if group is not None}

    def write_extensions(found):
        # Store extensions to have; the profile, if open, installs them now.
        extensions.set_synced(directory, found)
        registry.extensions.reconcile(name)

    def write_groups(groups):
        ordered = sorted(groups.values(), key=lambda g: g["saved_at"], reverse=True)
        save_json(groups_file, ordered)

    return {
        "bookmarks": Collection(
            lambda: {b["url"]: _bookmark(b) for b in pages.state(name)["bookmarks"]},
            lambda found: pages.replace_bookmarks(name, list(found.values())),
            _bookmark,
        ),
        "saved_groups": Collection(read_groups, write_groups, _saved_group),
        "reading_list": Collection(
            lambda: {entry["url"]: entry for entry in reading.entries()},
            lambda found: reading.replace_all(list(found.values())),
            _entry,
        ),
        "extensions": Collection(
            lambda: extensions.synced(directory),
            lambda found: write_extensions(found),
            _extension,
        ),
    }


def keyword_collection(config):
    def read():
        custom = config.get("search_keywords", {})
        return {
            key: _keyword(value)
            for key, value in (custom.items() if isinstance(custom, dict) else [])
            if _keyword(value) is not None
        }

    return Collection(
        read, lambda found: config.set("search_keywords", dict(found)), _keyword
    )


# ------------------------------------------------------------------ passwords


class SyncKey:
    """The key that seals synced passwords: in the system keyring, or memory."""

    def __init__(self, keyring=True):
        self.keyring = keyring
        self._key = None

    def _collection(self):
        import secretstorage

        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        return collection

    def get(self):
        if self._key is None and self.keyring:
            try:
                found = list(self._collection().search_items(KEYRING_ATTRIBUTES))
                if found:
                    key = base64.b64decode(found[0].get_secret())
                    if len(key) == nacl.secret.SecretBox.KEY_SIZE:
                        self._key = key
            except Exception:
                pass
        return self._key

    def store(self, key):
        self._key = key
        if self.keyring:
            try:
                collection = self._collection()
                for item in collection.search_items(KEYRING_ATTRIBUTES):
                    item.delete()
                collection.create_item(
                    "Steed sync passphrase key",
                    KEYRING_ATTRIBUTES,
                    base64.b64encode(key),
                )
            except Exception:
                pass  # Kept for this session only.

    def forget(self):
        self._key = None
        if self.keyring:
            try:
                for item in self._collection().search_items(KEYRING_ATTRIBUTES):
                    item.delete()
            except Exception:
                pass


def passphrase_key(folder, passphrase, opslimit=OPSLIMIT, memlimit=MEMLIMIT):
    """The sealing key from a passphrase, checked against the folder's.

    The first computer to set one writes the salt and a sealed check value to
    <folder>/steed-sync/passwords.json; the others must use the same one."""
    if len(passphrase) < 8:
        raise VaultError("Choose a sync passphrase of at least 8 characters.")
    path = Path(folder) / FOLDER / KEY_FILE
    header = load_json(path, {})
    if header:
        try:
            salt = base64.b64decode(header["salt"])
            opslimit, memlimit = int(header["opslimit"]), int(header["memlimit"])
            check = base64.b64decode(header["check"])
        except (KeyError, TypeError, ValueError):
            raise VaultError("The sync folder's passphrase file is damaged.") from None
        key = master_key(passphrase, salt, opslimit, memlimit)
        try:
            if nacl.secret.SecretBox(key).decrypt(check) != CHECK:
                raise nacl.exceptions.CryptoError
        except nacl.exceptions.CryptoError:
            raise VaultError(
                "That is not the passphrase the other computers use."
            ) from None
        return key
    salt = secrets.token_bytes(nacl.pwhash.argon2id.SALTBYTES)
    key = master_key(passphrase, salt, opslimit, memlimit)
    check = nacl.secret.SecretBox(key).encrypt(CHECK)
    save_json(
        path,
        {
            "version": VERSION,
            "salt": base64.b64encode(salt).decode(),
            "opslimit": opslimit,
            "memlimit": memlimit,
            "check": base64.b64encode(bytes(check)).decode(),
        },
    )
    return key


def seal(key, records):
    raw = json.dumps(records).encode()
    return base64.b64encode(bytes(nacl.secret.SecretBox(key).encrypt(raw))).decode()


def unseal(key, sealed):
    """The records in a sealed passwords section, or None if it will not open."""
    try:
        raw = nacl.secret.SecretBox(key).decrypt(base64.b64decode(sealed))
        records = json.loads(raw)
    except (nacl.exceptions.CryptoError, TypeError, ValueError):
        return None
    return records if isinstance(records, dict) else None


def _logins_of(vault):
    return Collection(
        vault.synced_logins,
        lambda found: vault.replace_synced(list(found.values())),
        _login,
    )


def _passkeys_of(vault):
    return Collection(
        vault.synced_passkeys,
        lambda found: vault.replace_synced_passkeys(list(found.values())),
        _passkey,
    )


# ----------------------------------------------------------------- one sync


def device_files(folder):
    return sorted((Path(folder) / FOLDER).glob("device-*.json"))


def read_device(path):
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not (
        isinstance(data, dict)
        and data.get("version") == VERSION
        and isinstance(data.get("device"), str)
    ):
        return None
    return data


def sync_folder(folder, device, name, sources, *, key=None, now=None):
    """Merge this computer's items with the other computers' files.

    sources: {"global": {kind: Collection}, "profiles": {profile: {kind:
    Collection}}, "vaults": {profile: vault or None}}. Returns a summary."""
    now = time.time() if now is None else now
    folder = Path(folder)
    if not folder.is_dir():
        raise OSError(f"The sync folder {folder} is not available.")
    own_path = folder / FOLDER / f"device-{device}.json"
    previous = read_device(own_path) if own_path.exists() else None
    previous = previous or {}
    others = [
        data
        for path in device_files(folder)
        if path != own_path
        for data in [read_device(path)]
        if data is not None and data["device"] != device
    ]
    changed = []

    def one(label, collection, mine, theirs):
        # Never synced here before: local items count as oldest.
        local = collection.read()
        first = not isinstance(mine, dict)
        known = {key for _, records in theirs for key in _dict(records)}
        records = own_records(_dict(mine), local, now, first=first, known=known)
        merged = merge([(device, records), *theirs], now)
        result = items(merged, collection.clean)
        if result != local:
            collection.write(result)
            changed.append(label)
        return merged

    out = {"version": VERSION, "device": device, "name": name, "written": now}
    out["global"] = {}
    for kind, collection in sources.get("global", {}).items():
        mine = _dict(previous.get("global")).get(kind)
        theirs = [
            (d["device"], d.get("global", {}).get(kind, {}))
            for d in others
            if isinstance(d.get("global"), dict)
        ]
        out["global"][kind] = one(kind, collection, mine, theirs)
    out["profiles"] = {}
    old_profiles = _dict(previous.get("profiles"))
    for profile, collections in sources.get("profiles", {}).items():
        section = out["profiles"][profile] = {}
        mine_all = _dict(old_profiles.get(profile))
        theirs_all = [
            (d["device"], _dict(_dict(d.get("profiles")).get(profile))) for d in others
        ]
        for kind, collection in collections.items():
            theirs = [(dev, _dict(s.get(kind))) for dev, s in theirs_all]
            section[kind] = one(
                f"{profile}: {kind}", collection, mine_all.get(kind), theirs
            )
        vault = sources.get("vaults", {}).get(profile)
        for kind, collection in (
            ("passwords", _logins_of),
            ("passkeys", _passkeys_of),
        ):
            sealed = mine_all.get(kind)
            if key is None or vault is None or not vault.unlocked:
                if isinstance(sealed, str):
                    section[kind] = sealed  # Carried until it can be opened.
                continue
            mine = unseal(key, sealed) if isinstance(sealed, str) else None
            theirs = []
            for dev, s in theirs_all:
                if isinstance(s.get(kind), str):
                    opened = unseal(key, s[kind])
                    if opened is not None:
                        theirs.append((dev, opened))
            merged = one(f"{profile}: {kind}", collection(vault), mine, theirs)
            # Sealing again would differ (a new nonce); keep it while unchanged.
            section[kind] = sealed if merged == mine else seal(key, merged)
    if {**out, "written": None} != {**previous, "written": None}:
        save_json(own_path, out)
    return {"changed": changed, "devices": [d.get("name", "") for d in others]}


def _dict(value):
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------- the service


class SyncService(QObject):
    """Syncs on start, every few minutes and soon after bookmarks change."""

    synced = pyqtSignal(str)

    def __init__(self, registry, key=None):
        super().__init__(registry)
        self.registry = registry
        self.config = registry.config
        self.key = key or SyncKey()
        self.status = "Not set up."
        self.last = None
        self.timer = QTimer(self)
        self.timer.setInterval(PERIOD_MS)
        self.timer.timeout.connect(self.run)
        self.settle = QTimer(self)
        self.settle.setSingleShot(True)
        self.settle.setInterval(SETTLE_MS)
        self.settle.timeout.connect(self.run)
        self._running = False
        registry.start_pages.changed.connect(lambda _: self.soon())
        registry.passwords.changed.connect(lambda _: self.soon())
        registry.extensions.changed.connect(lambda _: self.soon())
        self.configure()

    @property
    def folder(self):
        value = str(self.config.get("sync_folder", "") or "").strip()
        return Path(value).expanduser() if value else None

    def device(self):
        device = self.config.get("sync_device", "")
        if not (isinstance(device, str) and len(device) == 32):
            device = secrets.token_hex(16)
            self.config.set("sync_device", device)
        return device

    def configure(self):
        """Start or stop syncing after the folder setting changes."""
        if self.folder is None:
            self.timer.stop()
            self.settle.stop()
            self.status = "Not set up."
        else:
            self.timer.start()
            self.settle.start()

    def soon(self):
        if self.folder is not None and not self._running:
            self.settle.start()

    def set_folder(self, folder):
        self.config.set("sync_folder", str(folder) if folder else "")
        self.configure()

    def set_passphrase(self, passphrase):
        """Check (or create) the folder's passphrase and keep its key."""
        if self.folder is None:
            raise VaultError("Choose a sync folder first.")
        self.key.store(passphrase_key(self.folder, passphrase))
        self.config.set("sync_passwords", True)

    def stop_passwords(self):
        self.key.forget()
        self.config.set("sync_passwords", False)

    def sources(self):
        config = self.config
        profiles = {"global": config.config_dir / "global_profile"}
        for path in sorted(config.profiles_dir.iterdir()):
            if (
                path.is_dir()
                and path.name != "search"
                and self.registry.storage.active(path.name, path)
            ):
                profiles[path.name] = path
        passwords = bool(config.get("sync_passwords", False))
        vaults = {}
        for name in profiles:
            vault = self.registry.passwords.vault(name) if passwords else None
            vaults[name] = vault if vault is not None and vault.unlocked else None
        return {
            "global": {"search_keywords": keyword_collection(config)},
            "profiles": {
                name: profile_collections(self.registry, name, path)
                for name, path in profiles.items()
            },
            "vaults": vaults,
        }

    def run(self):
        """Sync now; returns the status shown in General → Sync…."""
        folder = self.folder
        if folder is None or self._running:
            return self.status
        self._running = True
        try:
            key = self.key.get() if self.config.get("sync_passwords") else None
            result = sync_folder(
                folder, self.device(), socket.gethostname(), self.sources(), key=key
            )
        except (OSError, VaultError) as error:
            self.status = f"Not synced: {error}"
        else:
            self.last = time.time()
            others = ", ".join(n for n in result["devices"] if n) or "none yet"
            stamp = time.strftime("%H:%M", time.localtime(self.last))
            self.status = f"Synced at {stamp}. Other computers: {others}."
            if result["changed"]:
                self.status += " Updated " + ", ".join(result["changed"]) + "."
        finally:
            self._running = False
        self.synced.emit(self.status)
        return self.status
