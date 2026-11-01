"""Vaults per profile and the page scripts that capture and fill logins.

All page code runs in Qt's ApplicationWorld, isolated from page scripts. The
site a login belongs to always comes from the engine (the frame's URL), never
from anything a page reports.
"""

import json
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineScript

from password_vault import KeyStore, PasswordVault, VaultError, origin_of

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld.value

# Remembers the login a form is about to submit; values stay in the isolated
# world until Steed collects them.
CAPTURE_SCRIPT = """
(() => {
    if (window.__steedLoginWatch) return;
    window.__steedLoginWatch = true;
    const visible = el => el instanceof HTMLInputElement && !el.disabled &&
        el.getClientRects().length > 0;
    const record = root => {
        const scope = root && root.querySelectorAll ? root : document;
        const inputs = Array.from(scope.querySelectorAll('input')).filter(visible);
        const passwords = inputs.filter(el => el.type === 'password' && el.value);
        if (!passwords.length) return;
        const fresh = passwords.filter(el => (el.autocomplete || '').includes('new-password'));
        const field = fresh[0] ||
            passwords.find(el => (el.autocomplete || '').includes('current-password')) ||
            passwords[passwords.length - 1];
        const text = inputs.filter(el => ['text', 'email', 'tel', ''].includes(el.type) && el.value);
        const user = text.find(el => (el.autocomplete || '').includes('username')) ||
            text.find(el => el.type === 'email') ||
            text.find(el => /user|mail|login|name|account/i.test(el.name + ' ' + el.id)) ||
            text[0];
        window.__steedLoginCapture = {
            username: user ? user.value.slice(0, 4096) : '',
            password: field.value.slice(0, 4096),
            fresh: fresh.length > 0,
        };
    };
    document.addEventListener('submit', e => record(e.target), true);
    document.addEventListener('click', e => {
        const button = e.target.closest &&
            e.target.closest('button, input[type=submit], input[type=button], [role=button]');
        if (button) record(button.form || document);
    }, true);
    document.addEventListener('keydown', e => {
        if (e.key === 'Enter' && e.target instanceof HTMLInputElement)
            record(e.target.form || document);
    }, true);
})();
"""


def take_script(force):
    """Hand over a captured login once its form is gone (or on navigation)."""
    return """
(() => {
    const capture = window.__steedLoginCapture;
    if (!capture) return null;
    const present = Array.from(document.querySelectorAll('input[type=password]'))
        .some(el => el.value === capture.password && el.getClientRects().length > 0);
    if (present && !FORCE) return {pending: true};
    window.__steedLoginCapture = null;
    return {username: capture.username, password: capture.password, fresh: capture.fresh};
})()
""".replace("FORCE", "true" if force else "false")


_FIELDS = """
    const visible = el => el instanceof HTMLInputElement && !el.disabled &&
        !el.readOnly && el.getClientRects().length > 0 &&
        getComputedStyle(el).visibility !== 'hidden';
    const inputs = Array.from(document.querySelectorAll('input')).filter(visible);
    const set = (el, value) => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        el.focus();
        setter.call(el, value);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    };
"""


def fill_script(username, password, *, only_empty=True):
    """Fill the sign-in form of a frame; returns whether anything was filled."""
    return (
        (
            "(() => {"
            + _FIELDS
            + """
    const passwords = inputs.filter(el => el.type === 'password' &&
        !(el.autocomplete || '').includes('new-password'));
    const password = passwords.find(el => (el.autocomplete || '').includes('current-password')) ||
        passwords[0];
    const scope = password && password.form ? inputs.filter(el => el.form === password.form) : inputs;
    const code = el => (el.autocomplete || '').includes('one-time-code') ||
        /otp|totp|2fa|mfa|one.?time|verification|auth.?code|security.?code/i.test(
            el.name + ' ' + el.id + ' ' + (el.placeholder || ''));
    const text = scope.filter(el => ['text', 'email', 'tel', ''].includes(el.type) && !code(el));
    const explicit = text.find(el => (el.autocomplete || '').includes('username')) ||
        text.find(el => el.type === 'email');
    // Without a password field (a username-first step) only a field marked as
    // the username or email is filled.
    const user = explicit || (password ? (
        text.find(el => /user|mail|login|name|account/i.test(el.name + ' ' + el.id)) ||
        (text.length === 1 ? text[0] : null)) : null);
    let filled = false;
    if (user && USERNAME && (!ONLY_EMPTY || !user.value)) { set(user, USERNAME); filled = true; }
    if (password && (!ONLY_EMPTY || !password.value)) { set(password, PASSWORD); filled = true; }
    return {filled, hasPassword: !!password, hasUser: !!user};
})()"""
        )
        .replace("USERNAME", json.dumps(username))
        .replace("PASSWORD", json.dumps(password))
        .replace("ONLY_EMPTY", "true" if only_empty else "false")
    )


def code_script(code):
    """Fill a one-time-code field that is still empty."""
    return (
        "(() => {"
        + _FIELDS
        + """
    const field = inputs.find(el => (el.autocomplete || '').includes('one-time-code')) ||
        inputs.find(el => ['text', 'tel', 'number', ''].includes(el.type) &&
            /otp|totp|2fa|mfa|one.?time|verification|auth.?code|security.?code/i.test(
                el.name + ' ' + el.id + ' ' + (el.placeholder || '') + ' ' +
                (el.getAttribute('aria-label') || '')));
    if (!field || field.value) return {filled: false};
    set(field, CODE);
    return {filled: true};
})()"""
    ).replace("CODE", json.dumps(code))


def new_password_script(password):
    """Put a generated password into a sign-up or change-password form."""
    return (
        "(() => {"
        + _FIELDS
        + """
    const passwords = inputs.filter(el => el.type === 'password');
    let targets = passwords.filter(el => (el.autocomplete || '').includes('new-password') ||
        /new|confirm|repeat|again/i.test(el.name + ' ' + el.id));
    if (!targets.length && passwords.length === 1) targets = passwords;
    for (const el of targets) set(el, PASSWORD);
    return {filled: targets.length};
})()"""
    ).replace("PASSWORD", json.dumps(password))


def frames(page):
    """Every frame of a page, main frame first."""
    found, pending = [], [page.mainFrame()]
    while pending:
        frame = pending.pop(0)
        found.append(frame)
        pending.extend(frame.children())
    return found


class PasswordService(QObject):
    """One vault per regular profile; incognito has none."""

    changed = pyqtSignal(str)

    def __init__(self, config, parent=None, keys=None):
        super().__init__(parent)
        self.config = config
        self.keys = keys or KeyStore()
        self.vaults = {}

    def path(self, profile):
        if profile == "search":
            return None
        if profile == "global":
            return self.config.config_dir / "global_profile" / "passwords.vault"
        return self.config.profiles_dir / profile / "passwords.vault"

    def vault(self, profile, *, unlock=True):
        """The profile's vault, unlocked if it can be without asking; or None."""
        path = self.path(profile)
        if path is None:
            return None
        vault = self.vaults.get(profile)
        if vault is None:
            vault = self.vaults[profile] = PasswordVault(path, self.keys)
        if unlock and not vault.unlocked and not vault.needs_master_password():
            try:
                vault.unlock()
            except VaultError:
                return vault
        return vault

    def install(self, profile):
        """Watch for submitted logins in every page and frame of a profile."""
        script = QWebEngineScript()
        script.setName("steed-password-capture")
        script.setSourceCode(CAPTURE_SCRIPT)
        script.setWorldId(WORLD)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setRunsOnSubFrames(True)
        profile.scripts().insert(script)

    def busy(self, profile):
        """Vault work is synchronous; nothing is ever left running."""
        return False

    def notify(self, profile):
        self.changed.emit(profile)

    def forget(self, profile):
        vault = self.vaults.pop(profile, None)
        if vault is not None:
            vault.lock()

    def close(self):
        for vault in self.vaults.values():
            vault.lock()
        self.vaults.clear()


def login_origin(url):
    """Logins are offered on https sites and local development servers."""
    origin = origin_of(url.toString() if hasattr(url, "toString") else url)
    if origin is None:
        return None
    host = urlsplit(origin).hostname
    if origin.startswith("http://") and host not in ("localhost", "127.0.0.1"):
        return None
    return origin
