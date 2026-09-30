"""Event-driven cookie inheritance; journals contain hashes, not values.

Qt exposes no enumeration-complete signal. Never assume an old store is empty
after a timer: unobserved cookies require a fresh store or explicit domain opt-in.
"""

import hashlib
import json

from PyQt6.QtCore import QDateTime, QObject, QUrl
from PyQt6.QtNetwork import QNetworkCookie

from utils import load_json, save_json


def cookie_key(cookie):
    domain = cookie.domain().strip().lower()
    if not domain:
        return None  # Never infer origin from whichever tab is active.
    return json.dumps([bytes(cookie.name()).hex(), domain, cookie.path() or "/"])


def cookie_site(cookie):
    return cookie.domain().lower().lstrip(".")


def related_domains(left, right):
    return left == right or left.endswith("." + right) or right.endswith("." + left)


def fingerprint(cookie):
    return hashlib.sha256(bytes(cookie.toRawForm())).hexdigest()


def delete_exact(store, cookie):
    """Qt's deleteCookie filters by name+URL, potentially matching other paths.

    Expire the exact canonical name/domain/path instead, preserving host-only
    scope. This does not modify Chromium's SQLite files directly.
    """
    expired = QNetworkCookie(cookie)
    expired.setExpirationDate(QDateTime.fromSecsSinceEpoch(1))
    domain = cookie_site(cookie)
    if not cookie.domain().startswith("."):
        expired.setDomain("")
    origin = QUrl(
        f"{'https' if cookie.isSecure() else 'http'}://{domain}{cookie.path() or '/'}"
    )
    store.setCookie(expired, origin)


class CookieSharing(QObject):
    def __init__(self, config, global_profile, parent=None, observe_global=True):
        super().__init__(parent)
        self.config = config
        self.global_profile = global_profile
        self.global_cookies = {}
        self.profiles = {}
        self.states = {}
        self.observed = {}
        self.pending = {}
        self.fresh = set()
        self.enabled = set()
        self.paths = {}
        self.closed = False
        self.deletions_file = config.config_dir / "global_cookie_deletions.json"
        deleted = load_json(self.deletions_file, [])
        self.deleted = (
            set(deleted)
            if isinstance(deleted, list)
            and all(isinstance(key, str) for key in deleted)
            else set()
        )
        store = global_profile.cookieStore()
        if observe_global:
            store.cookieAdded.connect(self.global_added)
            store.cookieRemoved.connect(self.global_removed)
        store.loadAllCookies()

    def register(self, name, profile, directory, fresh):
        self.profiles[name] = profile
        self.paths[name] = directory / "cookie_inheritance.json"
        raw = load_json(self.paths[name], {})
        raw = raw if isinstance(raw, dict) else {}
        self.states[name] = {
            "inherited": raw.get("inherited", {}),
            "local_sites": raw.get("local_sites", []),
            "opt_in": raw.get("opt_in", []),
            "block_all": bool(raw.get("block_all", False)),
        }
        if not isinstance(self.states[name]["inherited"], dict):
            self.states[name]["inherited"] = {}
        for field in ("local_sites", "opt_in"):
            values = self.states[name][field]
            self.states[name][field] = (
                [v for v in values if isinstance(v, str)]
                if isinstance(values, list)
                else []
            )
        self.observed[name] = {}
        self.pending[name] = {}
        if fresh:
            self.fresh.add(name)
        store = profile.cookieStore()
        store.cookieAdded.connect(lambda cookie, n=name: self.target_added(n, cookie))
        store.cookieRemoved.connect(
            lambda cookie, n=name: self.target_removed(n, cookie)
        )
        store.loadAllCookies()

    def save(self, name):
        save_json(self.paths[name], self.states[name])

    def enable(self, name):
        if name in self.profiles:
            self.enabled.add(name)
            self.reconcile(name)

    def local(self, name, domain):
        state = self.states[name]
        blocked = state["block_all"] and not any(
            related_domains(domain, site) for site in state["opt_in"]
        )
        return blocked or any(
            related_domains(domain, site) for site in state["local_sites"]
        )

    def mark_local(self, name, domain):
        if domain and domain not in self.states[name]["local_sites"]:
            self.states[name]["local_sites"].append(domain)
            self.save(name)

    def global_added(self, cookie):
        if self.closed or (key := cookie_key(cookie)) is None:
            return
        self.global_cookies[key] = QNetworkCookie(cookie)
        if key in self.deleted:
            self.deleted.discard(key)
            save_json(self.deletions_file, sorted(self.deleted))
        for name in tuple(self.enabled):
            self.copy(name, cookie)

    def global_removed(self, cookie):
        if self.closed or (key := cookie_key(cookie)) is None:
            return
        self.global_cookies.pop(key, None)
        self.deleted.add(key)
        save_json(self.deletions_file, sorted(self.deleted))
        for name in tuple(self.enabled):
            observed = self.observed[name].get(key)
            inherited = self.states[name]["inherited"].get(key)
            if (
                observed is not None
                and inherited == fingerprint(observed)
                and not self.local(name, cookie_site(cookie))
            ):
                self.pending[name][key] = None
                delete_exact(self.profiles[name].cookieStore(), observed)

    def target_added(self, name, cookie):
        if self.closed or (key := cookie_key(cookie)) is None:
            return
        self.observed[name][key] = QNetworkCookie(cookie)
        digest = fingerprint(cookie)
        pending = self.pending[name].get(key)
        if pending == digest:
            self.pending[name].pop(key, None)
            self.states[name]["inherited"][key] = digest
            self.save(name)
            if (
                name in self.enabled
                and key in self.deleted
                and not self.local(name, cookie_site(cookie))
            ):
                self.pending[name][key] = None
                delete_exact(self.profiles[name].cookieStore(), cookie)
            return
        inherited = self.states[name]["inherited"].get(key)
        if inherited != digest:
            self.mark_local(name, cookie_site(cookie))
        elif (
            name in self.enabled
            and key in self.deleted
            and not self.local(name, cookie_site(cookie))
        ):
            self.pending[name][key] = None
            delete_exact(self.profiles[name].cookieStore(), cookie)
        elif name in self.enabled and key in self.global_cookies:
            self.copy(name, self.global_cookies[key])

    def target_removed(self, name, cookie):
        if self.closed or (key := cookie_key(cookie)) is None:
            return
        self.observed[name].pop(key, None)
        if key in self.pending[name]:
            if self.pending[name][key] is None:
                self.pending[name].pop(key)
                self.states[name]["inherited"].pop(key, None)
                self.save(name)
            return
        self.mark_local(name, cookie_site(cookie))
        self.states[name]["inherited"].pop(key, None)
        self.save(name)

    def copy(self, name, cookie):
        key, domain = cookie_key(cookie), cookie_site(cookie)
        if key is None or name not in self.enabled or self.local(name, domain):
            return
        current = self.observed[name].get(key)
        digest = fingerprint(cookie)
        if current is not None:
            existing = fingerprint(current)
            if (
                existing != self.states[name]["inherited"].get(key)
                or existing == digest
            ):
                return
        elif name not in self.fresh and not any(
            related_domains(domain, site) for site in self.states[name]["opt_in"]
        ):
            return
        if self.pending[name].get(key) == digest:
            return
        self.pending[name][key] = digest
        self.states[name]["inherited"][key] = digest
        self.save(name)
        origin = QUrl(f"{'https' if cookie.isSecure() else 'http'}://{domain}/")
        copy = QNetworkCookie(cookie)
        if not cookie.domain().startswith("."):
            # setCookie adds a dot to nonempty domains. Empty + explicit origin
            # retains a host-only cookie instead of widening it to subdomains.
            copy.setDomain("")
        self.profiles[name].cookieStore().setCookie(copy, origin)

    def reconcile(self, name):
        for key, cookie in list(self.observed[name].items()):
            if (
                key in self.deleted
                and self.states[name]["inherited"].get(key) == fingerprint(cookie)
                and not self.local(name, cookie_site(cookie))
            ):
                self.pending[name][key] = None
                delete_exact(self.profiles[name].cookieStore(), cookie)
        for cookie in list(self.global_cookies.values()):
            self.copy(name, cookie)

    def protect_deletion(self, name, cookies=None):
        if name not in self.states:
            return
        if cookies is None:
            self.states[name]["block_all"] = True
            self.states[name]["opt_in"] = []
            self.save(name)
        else:
            for cookie in cookies:
                self.mark_local(name, cookie_site(cookie))

    def use_global(self, name, domain):
        domain = domain.lower().strip().lstrip(".")
        state = self.states[name]
        state["local_sites"] = [
            s for s in state["local_sites"] if not related_domains(s, domain)
        ]
        if domain not in state["opt_in"]:
            state["opt_in"].append(domain)
        self.enabled.add(name)
        for key, cookie in list(self.observed[name].items()):
            if related_domains(cookie_site(cookie), domain):
                self.pending[name][key] = None
                delete_exact(self.profiles[name].cookieStore(), cookie)
                self.observed[name].pop(key, None)
                state["inherited"].pop(key, None)
        self.save(name)
        self.reconcile(name)
