"""Passkeys kept in Steed's password vault.

Qt WebEngine has no platform authenticator, so a page's
navigator.credentials.create/get (for publicKey) would only reach security
keys. Steed puts a small script in each page (top frame, the page's world,
before the page's own scripts) that passes WebAuthn requests to Steed through
the console with a token the page cannot learn; Steed asks you, then creates or
uses a P-256 key kept in the profile's encrypted vault and answers in the
formats sites check (attestation "none", ES256). Steed decides the origin from
the tab, never from the page's message, and only for HTTPS sites (and
localhost). "Use a security key" passes the request to the engine instead.
"""

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, QTimer

import webengine

PASSKEY_PREFIX = "\x01steedpasskey "
# Flags in authenticator data: user present, user verified, credential data.
UP, UV, AT = 0x01, 0x04, 0x40
ES256 = -7
ANSWER_TIMEOUT_SECONDS = 300


# ------------------------------------------------------------------ encoding


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    text = str(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _head(major, value):
    if value < 24:
        return bytes([major << 5 | value])
    for code, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if value < 1 << (8 * size):
            return bytes([major << 5 | code]) + value.to_bytes(size, "big")
    raise ValueError("Too large for CBOR")


def cbor(value) -> bytes:
    """The CBOR (RFC 8949) WebAuthn needs: ints, bytes, text, lists, maps."""
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        return _head(0, value) if value >= 0 else _head(1, -1 - value)
    if isinstance(value, bytes):
        return _head(2, len(value)) + value
    if isinstance(value, str):
        data = value.encode()
        return _head(3, len(data)) + data
    if isinstance(value, list):
        return _head(4, len(value)) + b"".join(cbor(item) for item in value)
    if isinstance(value, dict):
        return _head(5, len(value)) + b"".join(
            cbor(key) + cbor(item) for key, item in value.items()
        )
    raise TypeError(f"No CBOR for {type(value).__name__}")


# --------------------------------------------------------------------- keys


def new_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


def private_bytes(key) -> str:
    from cryptography.hazmat.primitives import serialization

    return b64url(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def load_key(text):
    from cryptography.hazmat.primitives import serialization

    return serialization.load_der_private_key(unb64url(text), None)


def cose_key(key) -> bytes:
    numbers = key.public_key().public_numbers()
    return cbor(
        {
            1: 2,  # EC2
            3: ES256,
            -1: 1,  # P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )


def spki(key) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def sign(key, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    return key.sign(data, ec.ECDSA(hashes.SHA256()))


# ----------------------------------------------------------- the ceremonies


def page_origin(url: str) -> tuple[str, str] | None:
    """(origin, host) of a page passkeys may be used on, or None."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return None
    if parts.scheme == "https" or (
        parts.scheme == "http" and (host == "localhost" or host.endswith(".localhost"))
    ):
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}", host
    return None


def relying_party(requested, host) -> str:
    """The site a passkey belongs to: the page's host or a parent domain of it."""
    rp_id = str(requested or host).lower().strip(".")
    if rp_id == host or (
        host.endswith("." + rp_id)
        and "." in rp_id
        and not re.fullmatch(r"[\d.]+", host)
    ):
        return rp_id
    raise PasskeyError("SecurityError", "This page cannot use passkeys of that site.")


class PasskeyError(Exception):
    def __init__(self, name, message):
        super().__init__(message)
        self.name = name


def client_data(kind, challenge, origin) -> bytes:
    return json.dumps(
        {
            "type": kind,
            "challenge": challenge,
            "origin": origin,
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode()


def register(options: dict, origin: str, host: str) -> tuple[dict, dict]:
    """A new passkey (to keep) and the page's answer to create()."""
    rp = options.get("rp") or {}
    rp_id = relying_party(rp.get("id"), host)
    algorithms = [p.get("alg") for p in options.get("pubKeyCredParams") or []]
    if algorithms and ES256 not in algorithms:
        raise PasskeyError(
            "NotSupportedError", "The site needs a key type Steed lacks."
        )
    user = options.get("user") or {}
    key = new_key()
    credential = secrets.token_bytes(16)
    data = client_data("webauthn.create", options.get("challenge", ""), origin)
    authenticator = (
        hashlib.sha256(rp_id.encode()).digest()
        + bytes([UP | UV | AT])
        + (0).to_bytes(4, "big")
        + bytes(16)  # AAGUID: none.
        + len(credential).to_bytes(2, "big")
        + credential
        + cose_key(key)
    )
    record = {
        "id": b64url(credential),
        "rp_id": rp_id,
        "rp_name": str(rp.get("name") or rp_id)[:200],
        "user_handle": str(user.get("id") or ""),
        "user_name": str(user.get("name") or "")[:200],
        "user_display": str(user.get("displayName") or "")[:200],
        "key": private_bytes(key),
        "created": time.time(),
        "last_used": 0.0,
    }
    answer = {
        "id": record["id"],
        "clientDataJSON": b64url(data),
        "attestationObject": b64url(
            cbor({"fmt": "none", "attStmt": {}, "authData": authenticator})
        ),
        "authenticatorData": b64url(authenticator),
        "publicKey": b64url(spki(key)),
        "publicKeyAlgorithm": ES256,
        "transports": ["internal", "hybrid"],
    }
    return record, answer


def assert_with(record: dict, options: dict, origin: str) -> dict:
    """The page's answer to get() with a kept passkey."""
    data = client_data("webauthn.get", options.get("challenge", ""), origin)
    authenticator = (
        hashlib.sha256(record["rp_id"].encode()).digest()
        + bytes([UP | UV])
        + (0).to_bytes(4, "big")
    )
    signature = sign(
        load_key(record["key"]), authenticator + hashlib.sha256(data).digest()
    )
    return {
        "id": record["id"],
        "clientDataJSON": b64url(data),
        "authenticatorData": b64url(authenticator),
        "signature": b64url(signature),
        "userHandle": record["user_handle"],
    }


# --------------------------------------------------------- the page's script


SHIM = r"""
(() => {
  const credentials = navigator.credentials;
  if (!credentials || !window.isSecureContext || window.top !== window) return;
  const TOKEN = __TOKEN__;
  const post = console.debug.bind(console);
  const nativeCreate = credentials.create.bind(credentials);
  const nativeGet = credentials.get.bind(credentials);
  const pending = new Map();
  let asks = 0;
  const view = (source) =>
    source instanceof ArrayBuffer ? new Uint8Array(source)
      : new Uint8Array(source.buffer, source.byteOffset, source.byteLength);
  const encode = (source) => {
    let text = "";
    for (const byte of view(source)) text += String.fromCharCode(byte);
    return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  };
  const decode = (text) => {
    const binary = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4));
    return Uint8Array.from(binary, (c) => c.charCodeAt(0)).buffer;
  };
  const plain = (key, value) =>
    value instanceof ArrayBuffer || ArrayBuffer.isView(value) ? encode(value) : value;
  Object.defineProperty(window, "__steedPasskey", {
    value: (ask, reply) => {
      const asking = pending.get(ask);
      pending.delete(ask);
      if (asking) asking(reply);
    },
  });
  const own = (target, values) => {
    for (const [name, value] of Object.entries(values)) {
      Object.defineProperty(target, name, { value, enumerable: true });
    }
    return target;
  };
  const made = (reply, attestation) => {
    const proto = (name) => (typeof window[name] === "function" ? window[name].prototype : Object.prototype);
    const response = attestation
      ? own(Object.create(proto("AuthenticatorAttestationResponse")), {
          clientDataJSON: decode(reply.clientDataJSON),
          attestationObject: decode(reply.attestationObject),
          getTransports: () => reply.transports.slice(),
          getAuthenticatorData: () => decode(reply.authenticatorData),
          getPublicKey: () => decode(reply.publicKey),
          getPublicKeyAlgorithm: () => reply.publicKeyAlgorithm,
        })
      : own(Object.create(proto("AuthenticatorAssertionResponse")), {
          clientDataJSON: decode(reply.clientDataJSON),
          authenticatorData: decode(reply.authenticatorData),
          signature: decode(reply.signature),
          userHandle: reply.userHandle ? decode(reply.userHandle) : null,
        });
    const credential = own(Object.create(proto("PublicKeyCredential")), {
      id: reply.id,
      rawId: decode(reply.id),
      type: "public-key",
      authenticatorAttachment: "platform",
      response,
      getClientExtensionResults: () => (attestation ? { credProps: { rk: true } } : {}),
    });
    return own(credential, {
      toJSON: () => ({
        id: reply.id,
        rawId: reply.id,
        type: "public-key",
        authenticatorAttachment: "platform",
        clientExtensionResults: credential.getClientExtensionResults(),
        response: attestation
          ? {
              clientDataJSON: reply.clientDataJSON,
              attestationObject: reply.attestationObject,
              authenticatorData: reply.authenticatorData,
              transports: reply.transports,
              publicKey: reply.publicKey,
              publicKeyAlgorithm: reply.publicKeyAlgorithm,
            }
          : {
              clientDataJSON: reply.clientDataJSON,
              authenticatorData: reply.authenticatorData,
              signature: reply.signature,
              userHandle: reply.userHandle || null,
            },
      }),
    });
  };
  const ask = (kind, options, fallback) =>
    new Promise((resolve, reject) => {
      const id = ++asks;
      const signal = options.signal;
      if (signal && signal.aborted) {
        reject(new DOMException("The operation was aborted.", "AbortError"));
        return;
      }
      pending.set(id, (reply) => {
        if (reply.fallback) fallback().then(resolve, reject);
        else if (reply.error) reject(new DOMException(reply.message || "Not allowed.", reply.error));
        else resolve(made(reply, kind === "create"));
      });
      if (signal) {
        signal.addEventListener("abort", () => {
          if (pending.delete(id)) reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      }
      post("\u0001steedpasskey " + JSON.stringify({ token: TOKEN, ask: id, kind, options: options.publicKey }, plain));
    });
  credentials.create = function create(options) {
    if (!options || !options.publicKey) return nativeCreate(options);
    return ask("create", options, () => nativeCreate(options));
  };
  credentials.get = function get(options) {
    if (!options || !options.publicKey || options.mediation === "conditional") return nativeGet(options);
    return ask("get", options, () => nativeGet(options));
  };
  if (typeof PublicKeyCredential === "function") {
    PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = () => Promise.resolve(true);
    PublicKeyCredential.isConditionalMediationAvailable = () => Promise.resolve(false);
  }
})();
"""


class Passkeys(QObject):
    """Passkey requests from the pages of every profile."""

    def __init__(self, registry):
        super().__init__(registry)
        self.registry = registry
        # The page's script carries this; a page cannot read it from there.
        self.token = secrets.token_hex(16)

    def install(self, profile):
        webengine.add_profile_script(
            profile,
            "steed-passkeys",
            SHIM.replace("__TOKEN__", json.dumps(self.token)),
            world=webengine.PAGE_WORLD,
            when="start",
        )

    def request(self, tab, text):
        try:
            message = json.loads(text[len(PASSKEY_PREFIX) :])
        except ValueError:
            return
        if not isinstance(message, dict) or message.get("token") != self.token:
            return
        # Answer after the console callback, outside the engine's call.
        QTimer.singleShot(0, lambda: self._handle(tab, message))

    def _answer(self, tab, url, ask, reply):
        from PyQt6 import sip

        if sip.isdeleted(tab) or tab.url() != url:
            return  # The page moved on.
        webengine.run_script(
            tab.page(),
            f"window.__steedPasskey && window.__steedPasskey({json.dumps(ask)}, "
            f"{json.dumps(reply)})",
            world=webengine.PAGE_WORLD,
        )

    def _handle(self, tab, message):
        from PyQt6 import sip

        if sip.isdeleted(tab):
            return
        url = tab.url()
        ask = message.get("ask")
        options = (
            message.get("options") if isinstance(message.get("options"), dict) else {}
        )
        window = tab.browser_window

        def refuse(name="NotAllowedError", text="The request was not allowed."):
            self._answer(tab, url, ask, {"error": name, "message": text})

        found = page_origin(url.toString())
        config = self.registry.config
        if found is None or not config.get("steed_passkeys", True):
            self._answer(tab, url, ask, {"fallback": True})
            return
        origin, host = found
        vault = window.password_assistant._vault(ask=True)
        if vault is None:
            refuse()
            return
        from ui.passkey_prompt import choose_passkey, confirm_new_passkey

        try:
            if message.get("kind") == "create":
                rp_id = relying_party((options.get("rp") or {}).get("id"), host)
                excluded = {
                    c.get("id") for c in options.get("excludeCredentials") or []
                }
                if any(p["id"] in excluded for p in vault.passkeys(rp_id)):
                    refuse("InvalidStateError", "A passkey for this account is saved.")
                    return
                choice = confirm_new_passkey(window, rp_id, options.get("user") or {})
                if choice == "key":
                    self._answer(tab, url, ask, {"fallback": True})
                    return
                if choice != "steed":
                    refuse()
                    return
                record, answer = register(options, origin, host)
                vault.save_passkey(record)
                self.registry.passwords.notify(
                    window.profile_manager.current_profile_name
                )
                self._answer(tab, url, ask, answer)
                return
            rp_id = relying_party(options.get("rpId"), host)
            allowed = {c.get("id") for c in options.get("allowCredentials") or []}
            saved = [
                p for p in vault.passkeys(rp_id) if not allowed or p["id"] in allowed
            ]
            if not saved:
                self._answer(tab, url, ask, {"fallback": True})
                return
            chosen = choose_passkey(window, rp_id, saved)
            if chosen == "key":
                self._answer(tab, url, ask, {"fallback": True})
                return
            if chosen is None:
                refuse()
                return
            vault.touch_passkey(chosen["id"])
            self._answer(tab, url, ask, assert_with(chosen, options, origin))
        except PasskeyError as error:
            refuse(error.name, str(error))
