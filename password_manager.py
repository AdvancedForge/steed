"""KeePassXC's encrypted local browser protocol; no local password vault."""

import base64
import json
import os
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import (
    QObject,
    QRunnable,
    QStandardPaths,
    Qt,
    QThreadPool,
    pyqtSignal,
)

from utils import load_json, sanitize_profile_name, save_json

try:
    from nacl.public import Box, PrivateKey, PublicKey
except ImportError:
    Box = PrivateKey = PublicKey = None

MAX_MESSAGE = 1024 * 1024
SERVER_NAME = "org.keepassxc.KeePassXC.BrowserServer"


class PasswordManagerError(Exception):
    """A fixed, credential-free message suitable for the browser UI."""


def encode(data):
    return base64.b64encode(data).decode("ascii")


def decode(value):
    return base64.b64decode(value, validate=True)


def increment_nonce(nonce):
    return ((int.from_bytes(nonce, "little") + 1) % (1 << 192)).to_bytes(24, "little")


def socket_paths():
    runtime = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    )
    return [
        runtime / "app/org.keepassxc.KeePassXC" / SERVER_NAME,
        runtime / SERVER_NAME,
    ]


class KeePassXCClient:
    """A fresh encryption session per operation, authenticated to a local peer.

    Protocol reference:
    https://github.com/keepassxreboot/keepassxc-browser/blob/develop/keepassxc-protocol.md
    Only association identifiers/keys are persisted, as in the official client.
    """

    def __init__(self, path, memory, paths, cancel=None, timeout=90, client_id=None):
        self.path = path
        self.associations = memory if path is None else load_json(path, {})
        if not isinstance(self.associations, dict):
            self.associations = {}
        self.paths = paths
        self.cancel = cancel or threading.Event()
        self.timeout = timeout
        self.sock = None
        self.buffer = b""
        self.box = None
        self.client_id = client_id or encode(os.urandom(24))

    def _check_cancelled(self):
        if self.cancel.is_set():
            raise PasswordManagerError("Password request cancelled.")

    def __enter__(self):
        if Box is None:
            raise PasswordManagerError(
                "Install the project's requirements to enable KeePassXC integration."
            )
        if not hasattr(socket, "SO_PEERCRED"):
            raise PasswordManagerError(
                "KeePassXC integration currently requires Linux."
            )
        for path in self.paths:
            self._check_cancelled()
            candidate = None
            try:
                info = path.stat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    continue
                candidate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                candidate.settimeout(0.25)
                candidate.connect(str(path))
                _, uid, _ = struct.unpack(
                    "3i",
                    candidate.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
                )
                if uid != os.getuid():
                    candidate.close()
                    continue
                self.sock = candidate
                break
            except OSError:
                if candidate is not None:
                    candidate.close()
        if self.sock is None:
            raise PasswordManagerError(
                "Open KeePassXC and enable Tools → Settings → Browser Integration."
            )
        try:
            self.key = PrivateKey.generate()
            nonce = os.urandom(24)
            reply = self._exchange(
                {
                    "action": "change-public-keys",
                    "publicKey": encode(bytes(self.key.public_key)),
                    "nonce": encode(nonce),
                    "clientID": self.client_id,
                }
            )
            if reply.get("success") != "true" or decode(
                reply["nonce"]
            ) != increment_nonce(nonce):
                raise PasswordManagerError("KeePassXC key exchange failed.")
            self.box = Box(self.key, PublicKey(decode(reply["publicKey"])))
            return self
        except Exception:
            self.sock.close()
            raise

    def __exit__(self, *_):
        self.sock.close()
        self.box = None
        self.key = None

    def _exchange(self, request):
        action = request["action"]
        deadline = time.monotonic() + self.timeout
        self._check_cancelled()
        self.sock.sendall(json.dumps(request).encode("utf-8"))
        while time.monotonic() < deadline:
            self._check_cancelled()
            if self.buffer:
                try:
                    source = self.buffer.decode("utf-8")
                    reply, end = json.JSONDecoder().raw_decode(source.lstrip())
                    consumed = len(source) - len(source.lstrip()) + end
                    self.buffer = source[consumed:].encode("utf-8")
                    if not isinstance(reply, dict):
                        raise PasswordManagerError("Invalid KeePassXC response.")
                    # The socket also carries unsolicited vault lock/unlock events.
                    if reply.get("action") == action:
                        return reply
                    if reply.get("action") not in (
                        "database-locked",
                        "database-unlocked",
                    ):
                        raise PasswordManagerError("Unexpected KeePassXC response.")
                    continue
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                raise PasswordManagerError("KeePassXC disconnected. Try again.")
            self.buffer += chunk
            if len(self.buffer) > MAX_MESSAGE:
                raise PasswordManagerError("KeePassXC response is too large.")
        raise PasswordManagerError(
            "KeePassXC timed out. Unlock the vault and try again."
        )

    def request(self, action, *, trigger_unlock=True, **data):
        nonce = os.urandom(24)
        payload = json.dumps({"action": action, **data}).encode("utf-8")
        reply = self._exchange(
            {
                "action": action,
                "clientID": self.client_id,
                "nonce": encode(nonce),
                "message": encode(self.box.encrypt(payload, nonce).ciphertext),
                "triggerUnlock": "true" if trigger_unlock else "false",
            }
        )
        if "message" not in reply:
            code = str(reply.get("errorCode", ""))
            if code == "15" and action == "get-logins":
                return {"entries": []}
            messages = {
                "1": "Unlock your KeePassXC vault and try again.",
                "6": "KeePassXC denied the request.",
                "8": "Connect this profile to KeePassXC again.",
            }
            raise PasswordManagerError(
                messages.get(code, "KeePassXC could not complete the request.")
            )
        try:
            response_nonce = decode(reply["nonce"])
            if response_nonce != increment_nonce(nonce):
                raise ValueError
            result = json.loads(
                self.box.decrypt(decode(reply["message"]), response_nonce)
            )
            if (
                not isinstance(result, dict)
                or result.get("nonce") != encode(response_nonce)
                or result.get("success") != "true"
            ):
                raise ValueError
        except Exception as error:
            raise PasswordManagerError(
                "KeePassXC response authentication failed."
            ) from error
        return result

    def associate(self, allow_pair=False):
        db_hash = self.request("get-databasehash").get("hash")
        if not isinstance(db_hash, str) or not db_hash:
            raise PasswordManagerError("KeePassXC did not identify an unlocked vault.")
        pairing = self.associations.get(db_hash)
        if isinstance(pairing, dict) and pairing.get("id") and pairing.get("key"):
            try:
                self.request("test-associate", **pairing)
                return pairing
            except PasswordManagerError:
                if not allow_pair:
                    raise
        if not allow_pair:
            raise PasswordManagerError(
                "Click Connect KeePassXC once to pair this profile."
            )
        id_key = encode(bytes(PrivateKey.generate().public_key))
        result = self.request(
            "associate", key=encode(bytes(self.key.public_key)), idKey=id_key
        )
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise PasswordManagerError("KeePassXC pairing failed.")
        pairing = {"id": result["id"], "key": id_key}
        self._check_cancelled()
        self.associations[db_hash] = pairing
        if self.path is not None:
            save_json(self.path, self.associations)
            self.path.chmod(0o600)
        return pairing

    def execute(self, operation, data):
        pairing = self.associate(allow_pair=operation == "connect")
        if operation == "connect":
            return True
        if operation == "get-logins":
            result = self.request(operation, id=pairing["id"], keys=[pairing], **data)
            entries = result.get("entries", [])
            if not isinstance(entries, list):
                raise PasswordManagerError("Invalid KeePassXC login list.")
            return [
                entry
                for entry in entries
                if isinstance(entry, dict)
                and isinstance(entry.get("login"), str)
                and isinstance(entry.get("password"), str)
            ]
        if operation == "set-login":
            result = self.request(operation, id=pairing["id"], **data)
            if result.get("error") not in (None, "", "success"):
                raise PasswordManagerError("KeePassXC did not save the login.")
            return True
        raise PasswordManagerError("Unknown password operation.")


class _Result(QObject):
    finished = pyqtSignal(str, object, str)


class _PasswordTask(QRunnable):
    def __init__(self, client, operation, data):
        super().__init__()
        self.id = uuid4().hex
        self.client = client
        self.operation = operation
        self.data = data
        self.signals = _Result()

    def run(self):
        result, error = None, ""
        try:
            with self.client as client:
                result = client.execute(self.operation, self.data)
        except PasswordManagerError as failure:
            error = str(failure)
        except Exception:
            # Never include protocol data, credentials, or exception text in logs/UI.
            error = (
                "Could not communicate with KeePassXC. Check the vault and try again."
            )
        finally:
            self.data.clear()
        self.signals.finished.emit(self.id, result, error)


class PasswordManager(QObject):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.paths = socket_paths()
        self.private_pairings = {}
        self.client_ids = {}
        self.jobs = {}
        self.closed = False

    def path(self, profile):
        if profile == "search":
            return None
        directory = (
            self.config.config_dir / "global_profile"
            if profile == "global"
            else self.config.profiles_dir / sanitize_profile_name(profile)
        )
        return directory / "password_manager.json"

    def busy(self, profile):
        return any(record[0] == profile for record in self.jobs.values())

    def submit(self, owner, profile, operation, data, callback):
        if self.closed:
            return
        if self.busy(profile):
            callback(
                None, "Another password request is still running for this profile."
            )
            return
        client = KeePassXCClient(
            self.path(profile),
            self.private_pairings,
            self.paths,
            client_id=self.client_ids.setdefault(profile, encode(os.urandom(24))),
        )
        task = _PasswordTask(client, operation, dict(data))
        self.jobs[task.id] = [profile, owner, callback, task]
        task.signals.finished.connect(
            self._finished, Qt.ConnectionType.QueuedConnection
        )
        QThreadPool.globalInstance().start(task)

    def _finished(self, task_id, result, error):
        record = self.jobs.pop(task_id, None)
        if record is not None and record[2] is not None and not self.closed:
            record[2](result, error)

    def cancel(self, owner):
        for record in self.jobs.values():
            if record[1] is owner:
                record[2] = None
                record[3].client.cancel.set()

    def close(self):
        self.closed = True
        for record in self.jobs.values():
            record[2] = None
            record[3].client.cancel.set()
        self.private_pairings.clear()
