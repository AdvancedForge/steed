"""Steed's built-in password manager: an encrypted vault per profile.

Each profile's logins live in one file, sealed with XSalsa20-Poly1305
(PyNaCl SecretBox). The 256-bit key is kept in the system keyring through the
Secret Service (GNOME Keyring, KWallet); without one, a master password is
stretched with Argon2id instead. Nothing is written in plain text, and the
incognito profile has no vault.
"""

import base64
import csv
import hmac
import io
import json
import os
import secrets
import string
import struct
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import nacl.exceptions
import nacl.pwhash
import nacl.secret
import nacl.utils

VERSION = 1
MAX_ENTRIES = 5000
MAX_FIELD = 4096
KEYRING_LABEL = "Steed password vault"
KEYRING_ATTRIBUTES = {"application": "steed", "purpose": "password-vault"}


class VaultError(Exception):
    """A problem the user can act on; the message is safe to show."""


class VaultLocked(VaultError):
    pass


def origin_of(url):
    """scheme://host[:port] for http(s) addresses, else None."""
    try:
        parts = urlsplit(url.strip())
    except (AttributeError, ValueError):
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname.lower()
    default = {"http": 80, "https": 443}[parts.scheme]
    port = parts.port if parts.port not in (None, default) else None
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def site_of(origin):
    return urlsplit(origin).hostname or origin


def _text(value, name, *, empty=True):
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > MAX_FIELD or "\x00" in value:
        raise VaultError(f"The {name} is too long or invalid.")
    if not empty and not value:
        raise VaultError(f"Enter a {name}.")
    return value


@dataclass(frozen=True)
class Login:
    id: str
    origin: str
    username: str
    password: str
    title: str = ""
    totp: str = ""
    notes: str = ""
    created: float = 0.0
    updated: float = 0.0
    last_used: float = 0.0

    @property
    def site(self):
        return site_of(self.origin)


@dataclass
class Contents:
    logins: list = field(default_factory=list)
    never: list = field(default_factory=list)
    passkeys: list = field(default_factory=list)  # dicts, see passkeys.py
    addresses: list = field(default_factory=list)  # dicts, ADDRESS_FIELDS
    cards: list = field(default_factory=list)  # dicts, CARD_FIELDS
    # Sections a newer Steed wrote: kept as they are, so they survive a write.
    extra: dict = field(default_factory=dict)


# Addresses and payment cards for filling forms. A card's security code is
# never kept: sites ask for it each time, as other browsers do.
ADDRESS_FIELDS = (
    "name",
    "organization",
    "street",
    "city",
    "region",
    "postal",
    "country",
    "phone",
    "email",
)
CARD_FIELDS = ("name", "number", "exp_month", "exp_year", "nickname")
AUTOFILL_FIELDS = {"addresses": ADDRESS_FIELDS, "cards": CARD_FIELDS}
_KNOWN_SECTIONS = {"logins", "never", "passkeys", "addresses", "cards"}


def luhn(number):
    """True for a card number whose check digit is right."""
    total = 0
    for position, digit in enumerate(reversed(number)):
        value = int(digit)
        if position % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def card_number(value):
    digits = "".join(ch for ch in str(value or "") if ch not in " -")
    if not digits.isdigit() or not 12 <= len(digits) <= 19 or not luhn(digits):
        raise VaultError("That card number is not valid.")
    return digits


def card_label(card):
    """How a card is named on screen: never more than its last four digits."""
    ending = f"ending {card['number'][-4:]}"
    return (
        f"{card['nickname']} ({ending})" if card.get("nickname") else (f"Card {ending}")
    )


def address_label(address):
    first = address.get("street", "").splitlines()[0] if address.get("street") else ""
    return ", ".join(part for part in (address.get("name"), first) if part) or (
        address.get("email") or "Address"
    )


def _autofill_record(kind, item):
    """A checked address or card read from the vault (or a form), or None."""
    if not isinstance(item, dict):
        return None
    record = {"id": item.get("id") if isinstance(item.get("id"), str) else ""}
    try:
        for name in AUTOFILL_FIELDS[kind]:
            record[name] = _text(item.get(name, ""), name).strip()
        if kind == "cards":
            record["number"] = card_number(record["number"])
            month = record["exp_month"]
            if month and not (month.isdigit() and 1 <= int(month) <= 12):
                raise VaultError("Enter the expiry month as 1 to 12.")
            record["exp_month"] = f"{int(month):02d}" if month else ""
            year = record["exp_year"]
            if year and not (year.isdigit() and len(year) in (2, 4)):
                raise VaultError("Enter the expiry year as 2 or 4 digits.")
            record["exp_year"] = f"20{year}" if len(year) == 2 else year
        elif not any(record[name] for name in ADDRESS_FIELDS):
            raise VaultError("Enter at least one part of the address.")
    except VaultError:
        return None
    for name in ("created", "updated", "last_used"):
        value = item.get(name, 0.0)
        record[name] = float(value) if isinstance(value, (int, float)) else 0.0
    return record


PASSKEY_FIELDS = {
    "id": str,
    "rp_id": str,
    "rp_name": str,
    "user_handle": str,
    "user_name": str,
    "user_display": str,
    "key": str,
    "created": float,
    "last_used": float,
}


def _passkey(item):
    """A passkey record read from the vault, or None."""
    if not isinstance(item, dict):
        return None
    record = {}
    for name, kind in PASSKEY_FIELDS.items():
        value = item.get(name)
        if kind is float and isinstance(value, (int, float)):
            value = float(value)
        if not isinstance(value, kind) or (kind is str and len(value) > MAX_FIELD):
            return None
        record[name] = value
    return record if record["id"] and record["rp_id"] and record["key"] else None


# ---------------------------------------------------------------- one-time codes


def totp_parameters(value):
    """(secret bytes, digits, period, hash name) from an otpauth URI or secret."""
    value = (value or "").strip()
    digits, period, algorithm = 6, 30, "sha1"
    if value.lower().startswith("otpauth://"):
        parts = urlsplit(value)
        if parts.netloc.lower() != "totp":
            raise VaultError("Only time-based (TOTP) codes are supported.")
        query = {k.lower(): v[0] for k, v in parse_qs(parts.query).items()}
        value = query.get("secret", "")
        try:
            digits = int(query.get("digits", 6))
            period = int(query.get("period", 30))
        except ValueError:
            raise VaultError("Unsupported one-time code settings.") from None
        algorithm = query.get("algorithm", "sha1").lower()
    if digits not in (6, 7, 8) or not 10 <= period <= 300:
        raise VaultError("Unsupported one-time code settings.")
    if algorithm not in ("sha1", "sha256", "sha512"):
        raise VaultError("Unsupported one-time code algorithm.")
    cleaned = unquote(value).replace(" ", "").replace("-", "").upper()
    try:
        secret = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (ValueError, TypeError):
        raise VaultError("The one-time code secret is not valid base32.") from None
    if not secret:
        raise VaultError("Enter the one-time code secret.")
    return secret, digits, period, algorithm


def totp_code(value, now=None):
    """(code, seconds left) for an otpauth URI or base32 secret (RFC 6238)."""
    secret, digits, period, algorithm = totp_parameters(value)
    now = time.time() if now is None else now
    counter = int(now // period)
    digest = hmac.new(secret, struct.pack(">Q", counter), algorithm).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = str(number % 10**digits).zfill(digits)
    return code, int(period - now % period)


# ------------------------------------------------------------------ generator


def generate_password(length=20):
    """A random password with upper, lower, digits and symbols."""
    if not 12 <= length <= 128:
        raise VaultError("Choose a length from 12 to 128.")
    symbols = "!@#$%^&*()-_=+[]{};:,.?"
    pools = (string.ascii_lowercase, string.ascii_uppercase, string.digits, symbols)
    alphabet = "".join(pools)
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(c in pool for c in password) for pool in pools):
            return password


# ---------------------------------------------------------------- import


COLUMNS = {
    "url": ("url", "login_uri", "origin", "website", "web site", "hostname"),
    "username": ("username", "login_username", "login", "user name", "user", "email"),
    "password": ("password", "login_password"),
    "title": ("title", "name"),
    "totp": ("totp", "login_totp", "otpauth", "otp"),
    "notes": ("notes", "note", "comments"),
}


def parse_csv(text):
    """Logins from a KeePassXC, Chrome, Firefox or Bitwarden CSV export."""
    if not isinstance(text, str) or len(text) > 16 * 1024 * 1024:
        raise VaultError("Choose a CSV export of at most 16 MB.")
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [cell.strip().lower() for cell in next(reader)]
    except StopIteration:
        raise VaultError("The file is empty.") from None
    where = {}
    for name, options in COLUMNS.items():
        for option in options:
            if option in header:
                where[name] = header.index(option)
                break
    if "password" not in where or "url" not in where:
        raise VaultError(
            "This does not look like a password export: it needs URL and "
            "password columns."
        )
    rows, skipped = [], 0
    for cells in reader:
        values = {
            name: cells[index] if index < len(cells) else ""
            for name, index in where.items()
        }
        url = values["url"].strip()
        origin = origin_of(url if "://" in url else "https://" + url) if url else None
        if not origin or not values["password"]:
            skipped += 1
            continue
        rows.append(
            {
                "origin": origin,
                "username": values.get("username", ""),
                "password": values["password"],
                "title": values.get("title", ""),
                "totp": values.get("totp", ""),
                "notes": values.get("notes", ""),
            }
        )
    return rows, skipped


# ----------------------------------------------------------------- key store


class KeyStore:
    """The vault key in the Secret Service, or None when there is none."""

    def __init__(self):
        self._key = None

    def key(self, create=True):
        if self._key is not None:
            return self._key
        try:
            import secretstorage

            connection = secretstorage.dbus_init()
            collection = secretstorage.get_default_collection(connection)
            if collection.is_locked():
                collection.unlock()
            items = list(collection.search_items(KEYRING_ATTRIBUTES))
            if items:
                if items[0].is_locked():
                    items[0].unlock()
                key = base64.b64decode(items[0].get_secret())
            elif create:
                key = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
                collection.create_item(
                    KEYRING_LABEL, KEYRING_ATTRIBUTES, base64.b64encode(key)
                )
            else:
                return None
        except Exception:
            return None
        if len(key) != nacl.secret.SecretBox.KEY_SIZE:
            return None
        self._key = key
        return key


# Argon2id cost for new master-password vaults; stored in each vault's header
# so it can be raised later without breaking existing vaults.
OPSLIMIT = nacl.pwhash.argon2id.OPSLIMIT_MODERATE
MEMLIMIT = nacl.pwhash.argon2id.MEMLIMIT_MODERATE
MIN_MASTER_LENGTH = 8


def master_key(password, salt, opslimit=OPSLIMIT, memlimit=MEMLIMIT):
    return nacl.pwhash.argon2id.kdf(
        nacl.secret.SecretBox.KEY_SIZE,
        password.encode(),
        salt,
        opslimit=opslimit,
        memlimit=memlimit,
    )


# --------------------------------------------------------------------- vault


class PasswordVault:
    """One profile's logins. Not thread-safe: use from the Qt thread."""

    def __init__(self, path, keys=None):
        self.path = Path(path)
        self.keys = keys or KeyStore()
        self._box = None
        self._header = None
        self._contents = None
        self._batch = False

    # -- unlocking

    def _read_header(self):
        if self._header is None:
            if self.path.exists():
                try:
                    header = json.loads(self.path.read_text("utf-8"))
                except (OSError, ValueError):
                    raise VaultError("The password vault file is damaged.") from None
                if not isinstance(header, dict) or header.get("version") != VERSION:
                    raise VaultError("The password vault file is not supported.")
                self._header = header
            else:
                self._header = {}
        return self._header

    def is_new(self):
        """No vault file yet: unlocking creates one."""
        return not self._read_header()

    def needs_master_password(self):
        """True when this vault is (or must be) sealed with a master password."""
        header = self._read_header()
        if header:
            return header.get("kdf") == "argon2id"
        return self.keys.key() is None

    @property
    def unlocked(self):
        return self._box is not None

    def unlock(self, master_password=None):
        header = self._read_header()
        if self.needs_master_password():
            if not master_password:
                raise VaultLocked("Enter your master password.")
            if not header and len(master_password) < MIN_MASTER_LENGTH:
                raise VaultLocked(
                    f"Choose a master password of at least {MIN_MASTER_LENGTH} characters."
                )
            try:
                salt = (
                    base64.b64decode(header["salt"])
                    if header
                    else nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
                )
                opslimit = int(header.get("opslimit", OPSLIMIT)) if header else OPSLIMIT
                memlimit = int(header.get("memlimit", MEMLIMIT)) if header else MEMLIMIT
            except (KeyError, ValueError, TypeError):
                raise VaultError("The password vault file is damaged.") from None
            if not (OPSLIMIT <= opslimit <= 64 and MEMLIMIT <= memlimit <= 4 << 30):
                raise VaultError("The password vault file is damaged.")
            box = nacl.secret.SecretBox(
                master_key(master_password, salt, opslimit, memlimit)
            )
            kdf = {
                "kdf": "argon2id",
                "salt": base64.b64encode(salt).decode(),
                "opslimit": opslimit,
                "memlimit": memlimit,
            }
        else:
            key = self.keys.key()
            if key is None:
                raise VaultLocked("The system keyring is locked or unavailable.")
            box = nacl.secret.SecretBox(key)
            kdf = {"kdf": "keyring"}
        if header:
            try:
                raw = box.decrypt(base64.b64decode(header["data"]))
            except (nacl.exceptions.CryptoError, KeyError, ValueError):
                raise VaultLocked(
                    "Wrong master password."
                    if kdf["kdf"] == "argon2id"
                    else "The keyring key does not open this vault."
                ) from None
            try:
                data = json.loads(raw)
                contents = Contents(
                    [Login(**entry) for entry in data.get("logins", [])],
                    [str(origin) for origin in data.get("never", [])],
                    [
                        found
                        for found in map(_passkey, data.get("passkeys", []))
                        if found is not None
                    ],
                    *(
                        [
                            found
                            for item in data.get(kind, [])
                            if (found := _autofill_record(kind, item)) is not None
                            and found["id"]
                        ]
                        for kind in ("addresses", "cards")
                    ),
                    {k: v for k, v in data.items() if k not in _KNOWN_SECTIONS},
                )
            except (ValueError, TypeError, AttributeError):
                raise VaultError("The password vault file is damaged.") from None
        else:
            contents = Contents()
        self._box, self._kdf, self._contents = box, kdf, contents
        if not header:
            self._write()

    def lock(self):
        self._box = self._contents = None

    def _need(self):
        if self._box is None:
            raise VaultLocked("Unlock the password vault first.")
        return self._contents

    def _write(self):
        if self._batch:
            return  # import_csv writes once at the end.
        contents = self._need()
        raw = json.dumps(
            {
                "logins": [asdict(login) for login in contents.logins],
                "never": contents.never,
                "passkeys": contents.passkeys,
                "addresses": contents.addresses,
                "cards": contents.cards,
                **contents.extra,
            }
        ).encode()
        sealed = self._box.encrypt(raw)
        header = {
            "version": VERSION,
            **self._kdf,
            "data": base64.b64encode(bytes(sealed)).decode(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(header, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self._header = header

    # -- reading

    def logins(self, origin=None, query=""):
        logins = self._need().logins
        if origin is not None:
            logins = [login for login in logins if login.origin == origin]
        if query:
            words = query.lower().split()
            logins = [
                login
                for login in logins
                if all(
                    word in f"{login.origin} {login.username} {login.title}".lower()
                    for word in words
                )
            ]
        return sorted(logins, key=lambda login: (-login.last_used, login.site))

    def get(self, login_id):
        for login in self._need().logins:
            if login.id == login_id:
                return login
        raise VaultError("That login no longer exists.")

    def never_saves(self, origin):
        return origin in self._need().never

    # -- changing

    def save(self, origin, username, password, *, title="", totp="", notes=""):
        """Add a login, or update the password of the same site and username."""
        origin = origin_of(origin) or ""
        if not origin:
            raise VaultError("Logins are saved for http and https sites only.")
        username = _text(username, "username")
        password = _text(password, "password", empty=False)
        contents = self._need()
        now = time.time()
        for index, login in enumerate(contents.logins):
            if login.origin == origin and login.username == username:
                updated = replace(login, password=password, updated=now)
                contents.logins[index] = updated
                self._write()
                return updated
        if len(contents.logins) >= MAX_ENTRIES:
            raise VaultError("The vault is full.")
        if totp:
            totp_parameters(totp)
        login = Login(
            uuid4().hex,
            origin,
            username,
            password,
            _text(title, "title"),
            _text(totp, "one-time code secret"),
            _text(notes, "notes"),
            now,
            now,
            0.0,
        )
        contents.logins.append(login)
        self._write()
        return login

    def update(self, login_id, **fields):
        allowed = {"username", "password", "title", "totp", "notes", "origin"}
        if set(fields) - allowed:
            raise VaultError("Unknown login field.")
        current = self.get(login_id)
        if "origin" in fields:
            fields["origin"] = origin_of(fields["origin"]) or ""
            if not fields["origin"]:
                raise VaultError("Enter an http or https address.")
        if fields.get("totp"):
            totp_parameters(fields["totp"])
        for name in ("username", "title", "totp", "notes"):
            if name in fields:
                fields[name] = _text(fields[name], name)
        if "password" in fields:
            fields["password"] = _text(fields["password"], "password", empty=False)
        updated = replace(current, **fields, updated=time.time())
        contents = self._need()
        contents.logins = [
            updated if login.id == login_id else login for login in contents.logins
        ]
        self._write()
        return updated

    def touch(self, login_id):
        """Remember when a login was last filled (for ordering)."""
        current = self.get(login_id)
        contents = self._need()
        contents.logins = [
            replace(login, last_used=time.time()) if login.id == login_id else login
            for login in contents.logins
        ]
        self._write()
        return current

    def delete(self, login_id):
        self.get(login_id)
        contents = self._need()
        contents.logins = [login for login in contents.logins if login.id != login_id]
        self._write()

    def never_save(self, origin, never=True):
        origin = origin_of(origin)
        if not origin:
            return
        contents = self._need()
        if never and origin not in contents.never:
            contents.never.append(origin)
        elif not never and origin in contents.never:
            contents.never.remove(origin)
        self._write()

    # -- passkeys (passkeys.py makes and uses them)

    def passkeys(self, rp_id=None):
        """Saved passkeys (of one site), most recently used first."""
        found = [
            dict(p)
            for p in self._need().passkeys
            if rp_id is None or p["rp_id"] == rp_id
        ]
        return sorted(found, key=lambda p: (-p["last_used"], -p["created"]))

    def save_passkey(self, record):
        record = _passkey(record)
        if record is None:
            raise VaultError("That passkey is damaged.")
        contents = self._need()
        if len(contents.passkeys) >= MAX_ENTRIES:
            raise VaultError("The vault is full.")
        contents.passkeys = [
            p
            for p in contents.passkeys
            # A site's new passkey for an account replaces the old one.
            if p["id"] != record["id"]
            and not (
                p["rp_id"] == record["rp_id"]
                and p["user_handle"] == record["user_handle"]
            )
        ] + [record]
        self._write()

    def touch_passkey(self, passkey_id):
        contents = self._need()
        for passkey in contents.passkeys:
            if passkey["id"] == passkey_id:
                passkey["last_used"] = time.time()
        self._write()

    def synced_passkeys(self):
        """id -> passkey for sync, without when it was last used here."""
        return {
            p["id"]: {k: v for k, v in p.items() if k != "last_used"}
            for p in self._need().passkeys
        }

    def replace_synced_passkeys(self, passkeys):
        """Make the vault hold exactly these synced passkeys."""
        contents = self._need()
        used = {p["id"]: p["last_used"] for p in contents.passkeys}
        result = []
        for data in passkeys:
            record = _passkey({**data, "last_used": used.get(data.get("id"), 0.0)})
            if record is not None and len(result) < MAX_ENTRIES:
                result.append(record)
        contents.passkeys = result
        self._write()

    def delete_passkey(self, passkey_id):
        contents = self._need()
        contents.passkeys = [p for p in contents.passkeys if p["id"] != passkey_id]
        self._write()

    # -- addresses and cards (for filling forms; see ui/form_autofill.py)

    def autofill(self, kind):
        """Saved addresses or cards, most recently used first."""
        items = getattr(self._need(), kind)
        return sorted(
            (dict(item) for item in items),
            key=lambda item: (-item["last_used"], -item["updated"]),
        )

    def autofill_item(self, kind, item_id):
        for item in getattr(self._need(), kind):
            if item["id"] == item_id:
                return dict(item)
        raise VaultError("That entry no longer exists.")

    def save_autofill(self, kind, fields, item_id=None):
        """Add an address or card, or change one. A card with the same number
        (or the same address) updates the saved one instead of adding a copy."""
        if kind not in AUTOFILL_FIELDS:
            raise VaultError("Unknown kind of entry.")
        record = _autofill_record(kind, {**fields, "id": item_id or ""})
        if record is None:
            # Run the checks again for a message the person can act on.
            for name in AUTOFILL_FIELDS[kind]:
                _text(fields.get(name, ""), name)
            if kind == "cards":
                card_number(fields.get("number"))
            raise VaultError(
                "Check the expiry date."
                if kind == "cards"
                else "Enter at least one part of the address."
            )
        items = getattr(self._need(), kind)
        now = time.time()
        same = (
            (lambda item: item["number"] == record["number"])
            if kind == "cards"
            else (
                lambda item: all(
                    item[name].casefold() == record[name].casefold()
                    for name in ("name", "street", "postal")
                )
            )
        )
        for index, item in enumerate(items):
            if item["id"] == item_id or (item_id is None and same(item)):
                record.update(
                    id=item["id"],
                    created=item["created"],
                    updated=now,
                    last_used=item["last_used"],
                )
                items[index] = record
                self._write()
                return dict(record)
        if item_id is not None:
            raise VaultError("That entry no longer exists.")
        if len(items) >= MAX_ENTRIES:
            raise VaultError("The vault is full.")
        record.update(id=uuid4().hex, created=now, updated=now, last_used=0.0)
        items.append(record)
        self._write()
        return dict(record)

    def touch_autofill(self, kind, item_id):
        for item in getattr(self._need(), kind):
            if item["id"] == item_id:
                item["last_used"] = time.time()
        self._write()

    def delete_autofill(self, kind, item_id):
        contents = self._need()
        setattr(
            contents,
            kind,
            [item for item in getattr(contents, kind) if item["id"] != item_id],
        )
        self._write()

    @staticmethod
    def autofill_key(kind, item):
        """What names one address or card on every computer (sync's key)."""
        if kind == "cards":
            return "card " + item["number"]
        return "address " + "|".join(
            item[name].casefold() for name in ("name", "street", "postal")
        )

    def synced_autofill(self, kind):
        """key -> the fields sync shares (not ids or when last used here)."""
        return {
            self.autofill_key(kind, item): {
                **{name: item[name] for name in AUTOFILL_FIELDS[kind]},
                "created": item["created"],
                "updated": item["updated"],
            }
            for item in getattr(self._need(), kind)
        }

    def replace_synced_autofill(self, kind, found):
        """Make the vault hold exactly these synced entries, keeping local ids."""
        contents = self._need()
        current = {self.autofill_key(kind, i): i for i in getattr(contents, kind)}
        result = []
        for data in found:
            record = _autofill_record(kind, {**data, "id": ""})
            if record is None or len(result) >= MAX_ENTRIES:
                continue
            old = current.get(self.autofill_key(kind, record))
            record.update(
                id=old["id"] if old else uuid4().hex,
                last_used=old["last_used"] if old else 0.0,
            )
            result.append(record)
        setattr(contents, kind, result)
        self._write()

    # -- sync

    SYNCED = ("origin", "username", "password", "title", "totp", "notes")

    def synced_logins(self):
        """origin + username -> the fields sync shares (not last use or ids)."""
        found = {}
        for login in self._need().logins:
            data = {name: getattr(login, name) for name in self.SYNCED}
            data["created"], data["updated"] = login.created, login.updated
            found[f"{login.origin} {login.username}"] = data
        return found

    def replace_synced(self, logins):
        """Make the vault hold exactly these synced logins, keeping local ids."""
        contents = self._need()
        current = {(login.origin, login.username): login for login in contents.logins}
        result = []
        for data in logins:
            try:
                origin = origin_of(data["origin"]) or ""
                fields = {
                    name: _text(data[name], name, empty=name != "password")
                    for name in self.SYNCED[1:]
                }
                if fields["totp"]:
                    totp_parameters(fields["totp"])
                created, updated = float(data["created"]), float(data["updated"])
            except (KeyError, TypeError, ValueError, VaultError):
                continue
            if not origin or len(result) >= MAX_ENTRIES:
                continue
            old = current.get((origin, fields["username"]))
            result.append(
                Login(
                    old.id if old else uuid4().hex,
                    origin,
                    **fields,
                    created=created,
                    updated=updated,
                    last_used=old.last_used if old else 0.0,
                )
            )
        contents.logins = result
        self._write()

    def import_csv(self, text):
        """(added, updated, skipped) from a password manager CSV export."""
        rows, skipped = parse_csv(text)
        added = updated = 0
        self._batch = True
        try:
            added, updated = self._import_rows(rows)
        finally:
            self._batch = False
        self._write()
        return added, updated, skipped

    def _import_rows(self, rows):
        added = updated = 0
        for row in rows:
            existing = [
                login
                for login in self.logins(row["origin"])
                if login.username == row["username"]
            ]
            try:
                totp = row["totp"]
                if totp:
                    totp_parameters(totp)
            except VaultError:
                totp = ""
            if existing:
                if existing[0].password != row["password"]:
                    self.update(existing[0].id, password=row["password"])
                    updated += 1
                continue
            self.save(
                row["origin"],
                row["username"],
                row["password"],
                title=row["title"],
                totp=totp,
                notes=row["notes"],
            )
            added += 1
        return added, updated

    def export_csv(self):
        """A Chrome-style CSV of every login (plain text: handle with care)."""
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["name", "url", "username", "password", "totp", "note"])
        for login in self.logins():
            writer.writerow(
                [
                    login.title or login.site,
                    login.origin,
                    login.username,
                    login.password,
                    login.totp,
                    login.notes,
                ]
            )
        return out.getvalue()
