"""Prepare an extension for installing: unpack, give it a stable id, adapt it.

Steed keeps each extension's own files (its "source") and installs a copy
built from them, so it can install again for an update or a change of sites
without losing the extension's data:

- A "key" in the manifest fixes the extension's id. Chrome Web Store
  extensions keep their store id (the key is in the .crx header); others get a
  key Steed makes. Qt WebEngine keeps an extension's storage under its id, so
  data survives installing again.
- extension_shim.js runs first in the background worker and every page, with
  stand-ins for Chrome APIs Qt WebEngine lacks.
- With chosen sites, the extension's site access and content scripts are
  narrowed to those sites.
"""

import base64
import hashlib
import io
import json
import re
import shutil
import struct
import zipfile
from pathlib import Path

from PyQt6.QtCore import QLocale

SHIM = Path(__file__).with_name("extension_shim.js")
SHIM_NAME = "steed-shim.js"
BACKGROUND_NAME = "steed-background.js"
# The extension's messages, for the stand-in i18n.getMessage in its background
# (Qt WebEngine's ends the background worker there).
MESSAGES_NAME = "steed-messages.js"
BRIDGE_PAGE = "steed-bridge.html"
# The bridge page: a hidden page of the extension that Steed loads to reach
# its background. Steed reads what it logs and calls __steedBridge to send.
BRIDGE_SCRIPT = """\
const relay = (message) => console.debug("\\u0001steed " + JSON.stringify(message));
let port = null;
const queue = [];
function connect() {
  port = chrome.runtime.connect({ name: "steed-bridge" });
  port.onMessage.addListener(relay);
  port.onDisconnect.addListener(() => {
    port = null;
    setTimeout(connect, 500);
  });
  for (const message of queue.splice(0)) port.postMessage(message);
}
window.__steedBridge = (message) => (port ? port.postMessage(message) : queue.push(message));
connect();
"""
MAX_FILES = 20_000
MAX_UNPACKED = 500 << 20


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


def extension_id(public_key: bytes) -> str:
    """Chrome's id for a public key: the first 128 bits of its SHA-256, a–p."""
    digest = hashlib.sha256(public_key).hexdigest()[:32]
    return "".join(chr(ord("a") + int(digit, 16)) for digit in digest)


def _varint(data, index):
    value = shift = 0
    while True:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, index


def _fields(data):
    """(field, value) of a protobuf message; length-delimited values as bytes."""
    index = 0
    while index < len(data):
        key, index = _varint(data, index)
        field, kind = key >> 3, key & 7
        if kind == 2:
            size, index = _varint(data, index)
            yield field, data[index : index + size]
            index += size
        elif kind == 0:
            value, index = _varint(data, index)
            yield field, value
        elif kind == 5:
            index += 4
        elif kind == 1:
            index += 8
        else:
            raise ValueError("Unexpected protobuf field")


def crx_header(data: bytes) -> tuple[list[bytes], str | None]:
    """A version 3 .crx header's public keys and the id it declares."""
    if len(data) < 12 or data[:4] != b"Cr24":
        return [], None
    if struct.unpack("<I", data[4:8])[0] != 3:
        return [], None
    size = struct.unpack("<I", data[8:12])[0]
    keys, declared = [], None
    try:
        for field, value in _fields(data[12 : 12 + size]):
            if field in (2, 3) and isinstance(value, bytes):  # RSA, ECDSA proofs
                keys += [v for f, v in _fields(value) if f == 1]
            elif field == 10000 and isinstance(value, bytes):  # Signed data.
                for f, crx_id in _fields(value):
                    if f == 1 and isinstance(crx_id, bytes) and len(crx_id) == 16:
                        declared = "".join(
                            chr(ord("a") + int(d, 16)) for d in crx_id.hex()
                        )
    except (IndexError, ValueError):
        return [], None
    return keys, declared


def new_key() -> bytes:
    """A public key (DER) for an extension that has none."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def unpack(path: Path, destination: Path) -> bytes:
    """Copy or extract an extension into an empty folder; returns .crx bytes."""
    path = Path(path)
    destination.mkdir(parents=True)
    if path.is_dir():
        count = 0
        for item in path.rglob("*"):
            if item.is_symlink():
                continue
            count += 1
            if count > MAX_FILES:
                raise ValueError("That extension has too many files.")
        shutil.copytree(
            path,
            destination,
            dirs_exist_ok=True,
            ignore=lambda folder, names: [
                n for n in names if (Path(folder) / n).is_symlink()
            ],
        )
        return b""
    data = path.read_bytes()
    archive = crx_zip(data) if data[:4] == b"Cr24" else data
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile:
        raise ValueError("Choose an extension folder, .zip or .crx file.") from None
    members = bundle.infolist()
    if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_UNPACKED:
        raise ValueError("That extension is too large.")
    root = destination.resolve()
    for member in members:
        target = (destination / member.filename).resolve()
        if root != target and root not in target.parents:
            raise ValueError("That extension archive has unsafe file names.")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(member))
    return data if data[:4] == b"Cr24" else b""


def read_manifest(folder: Path) -> dict:
    try:
        manifest = json.loads((folder / "manifest.json").read_text("utf-8-sig"))
    except (OSError, ValueError):
        raise ValueError(
            "The extension's manifest.json is missing or damaged."
        ) from None
    if not isinstance(manifest, dict):
        raise ValueError("The extension's manifest.json is damaged.")
    return manifest


def write_manifest(folder: Path, manifest: dict) -> None:
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), "utf-8")


def ensure_key(folder: Path, crx: bytes = b"", store: str | None = None) -> str:
    """Give the unpacked extension a key and return the id it fixes."""
    manifest = read_manifest(folder)
    key = None
    if isinstance(manifest.get("key"), str):
        try:
            key = base64.b64decode(manifest["key"], validate=True)
        except ValueError:
            key = None
    if key is None:
        found, declared = crx_header(crx)
        # The key whose id is the store id, or the id the file declares.
        wanted = store or declared
        key = next((k for k in found if extension_id(k) == wanted), None)
        if key is None:
            key = new_key()
        manifest["key"] = base64.b64encode(key).decode()
        write_manifest(folder, manifest)
    return extension_id(key)


def keep_key(installed: Path, new: Path) -> None:
    """Give a new version the key of the installed one, if it has none."""
    manifest = read_manifest(new)
    key = read_manifest(installed).get("key")
    if "key" not in manifest and isinstance(key, str):
        manifest["key"] = key
        write_manifest(new, manifest)


# ------------------------------------------------------------- site access


def clean_site(text) -> str | None:
    """A host name from what someone typed ("https://www.x.org/a" -> x.org)."""
    text = str(text).strip().lower()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text).split("/")[0].split(":")[0]
    text = text.removeprefix("www.").strip(".")
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*", text or ""):
        return text
    return None


_PATTERN = re.compile(r"^(\*|https?|wss?|ftp|file|urn)://([^/]*)(/.*)?$")


def _within(host, site):
    host = host.removeprefix("*.")
    return host == site or host.endswith("." + site)


def _covers(host, site):
    if host == "*":
        return True
    if host.startswith("*."):
        return _within(site, host[2:])
    return False


def narrow(patterns, sites):
    """Match patterns limited to the sites (and their subdomains)."""
    result = []
    for pattern in patterns if isinstance(patterns, list) else []:
        if not isinstance(pattern, str):
            continue
        if pattern == "<all_urls>":
            scheme, host, path = "*", "*", "/*"
        else:
            match = _PATTERN.match(pattern)
            if not match:
                result.append(pattern)  # Not a site pattern.
                continue
            scheme, host, path = match.group(1), match.group(2), match.group(3) or "/*"
        for site in sites:
            if _within(host, site):
                narrowed = pattern
            elif _covers(host, site):
                narrowed = f"{scheme}://*.{site}{path}"
            else:
                continue
            if narrowed not in result:
                result.append(narrowed)
    return result


def limit_to_sites(manifest: dict, sites: list[str]) -> dict:
    manifest = json.loads(json.dumps(manifest))
    manifest["host_permissions"] = narrow(manifest.get("host_permissions", []), sites)
    if isinstance(manifest.get("permissions"), list):
        manifest["permissions"] = [
            p
            for p in manifest["permissions"]
            if not (isinstance(p, str) and ("://" in p or p == "<all_urls>"))
        ] + narrow(
            [
                p
                for p in manifest["permissions"]
                if isinstance(p, str) and ("://" in p or p == "<all_urls>")
            ],
            sites,
        )
    scripts = []
    for script in manifest.get("content_scripts", []) or []:
        if not isinstance(script, dict):
            continue
        matches = narrow(script.get("matches", []), sites)
        if matches:
            scripts.append({**script, "matches": matches})
    manifest["content_scripts"] = scripts
    return manifest


# ------------------------------------------------------------------ building


def _add_shim_to_page(path: Path):
    try:
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return
    tag = f'<script src="/{SHIM_NAME}"></script>'
    head = re.search(r"<head[^>]*>", text, re.IGNORECASE)
    if head:
        text = text[: head.end()] + tag + text[head.end() :]
    else:
        text = tag + text
    path.write_text(text, "utf-8")


def locale_messages(folder: Path, manifest: dict, ui_locale: str = "") -> dict:
    """The messages getMessage would use: the default locale's, then the UI
    language's, then the exact UI locale's, each overriding the one before."""
    default = manifest.get("default_locale")
    if not isinstance(default, str) or not default:
        return {}
    ui_locale = ui_locale or QLocale.system().name()
    order = [default, ui_locale.split("_")[0], ui_locale]
    found = {}
    for locale in dict.fromkeys(order):
        try:
            raw = json.loads(
                (folder / "_locales" / locale / "messages.json").read_text("utf-8-sig")
            )
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            found.update(
                {
                    str(key).lower(): value
                    for key, value in raw.items()
                    if isinstance(value, dict) and isinstance(value.get("message"), str)
                }
            )
    return found


def build(source: Path, destination: Path, sites: list[str] | None = None) -> None:
    """The copy Steed installs: the shim added, sites narrowed if chosen."""
    shutil.copytree(source, destination)
    manifest = read_manifest(destination)
    if sites is not None:
        manifest = limit_to_sites(manifest, sites)
    shutil.copyfile(SHIM, destination / SHIM_NAME)
    (destination / MESSAGES_NAME).write_text(
        "globalThis.__steedMessages = "
        + json.dumps(locale_messages(destination, manifest))
        + ";\n",
        "utf-8",
    )
    # The stand-ins' tab list lives in the background, so every extension
    # gets one; its own worker (if any) runs after the stand-ins.
    background = manifest.get("background")
    background = background if isinstance(background, dict) else {}
    worker = background.get("service_worker")
    if isinstance(worker, str):
        worker = "/" + worker.lstrip("/")
        if background.get("type") == "module":
            wrapper = (
                f'import "/{MESSAGES_NAME}";\nimport "/{SHIM_NAME}";\n'
                f'import "{worker}";\n'
            )
        else:
            wrapper = (
                f'importScripts("/{MESSAGES_NAME}", "/{SHIM_NAME}", '
                f"{json.dumps(worker)});\n"
            )
    else:
        background = {}
        wrapper = f'importScripts("/{MESSAGES_NAME}", "/{SHIM_NAME}");\n'
    (destination / BACKGROUND_NAME).write_text(wrapper, "utf-8")
    manifest["background"] = {**background, "service_worker": BACKGROUND_NAME}
    for script in manifest.get("content_scripts", []) or []:
        if isinstance(script, dict) and script.get("world") != "MAIN":
            script["js"] = [SHIM_NAME, *(script.get("js") or [])]
    for page in destination.rglob("*.htm*"):
        if page.suffix.lower() in (".html", ".htm"):
            _add_shim_to_page(page)
    (destination / "steed-bridge.js").write_text(BRIDGE_SCRIPT, "utf-8")
    (destination / BRIDGE_PAGE).write_text(
        '<!doctype html><script src="/steed-bridge.js"></script>', "utf-8"
    )
    write_manifest(destination, manifest)
