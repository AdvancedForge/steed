"""Vaults per profile and the page scripts that capture and fill logins.

All page code runs in Qt's ApplicationWorld, isolated from page scripts. The
site a login belongs to always comes from the engine (the frame's URL), never
from anything a page reports.
"""

import json
import time
from urllib.parse import urlsplit

from PyQt6.QtCore import QMimeData, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication

import webengine
from password_vault import KeyStore, PasswordVault, VaultError, origin_of

WORLD = webengine.STEED_WORLD
# Unused vaults are locked (and their logins dropped from memory) after this.
IDLE_LOCK_SECONDS = 30 * 60
# Copied passwords and codes are cleared from the clipboard after this.
CLIPBOARD_SECONDS = 30


def copy_secret(text):
    """Copy a password or code, hidden from clipboard managers, then clear it."""
    data = QMimeData()
    data.setText(text)
    # KDE Klipper and similar managers skip entries carrying this hint.
    data.setData("x-kde-passwordManagerHint", b"secret")
    clipboard = QGuiApplication.clipboard()
    clipboard.setMimeData(data)

    def clear():
        if clipboard.text() == text:
            clipboard.clear()

    QTimer.singleShot(CLIPBOARD_SECONDS * 1000, clear)


# Remembers the login a form is about to submit; values stay in the isolated
# world until Steed collects them.
CAPTURE_SCRIPT = """
(() => {
    if (window.__steedLoginWatch) return;
    window.__steedLoginWatch = true;
    // Inputs in the page and inside web components' (open) shadow roots,
    // where some sign-in forms live; bounded on very large pages.
    const deepInputs = root => {
        const found = [], pending = [root];
        let seen = 0;
        while (pending.length && seen < 20000) {
            const node = pending.shift();
            node.querySelectorAll('input').forEach(el => found.push(el));
            for (const el of node.querySelectorAll('*')) {
                if (++seen >= 20000) break;
                if (el.shadowRoot) pending.push(el.shadowRoot);
            }
        }
        return found;
    };
    // The element really acted on, inside a shadow root if there is one.
    const actual = e => (e.composedPath && e.composedPath()[0]) || e.target;
    const visible = el => el instanceof HTMLInputElement && !el.disabled &&
        el.getClientRects().length > 0;
    const record = root => {
        const scope = root && root.querySelectorAll ? root : document;
        const inputs = deepInputs(scope).filter(visible);
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
    // Only the user's own actions count: pages can fire synthetic events.
    // A submit event is trusted even when a script clicked the button, so it
    // also needs the user's own recent input.
    document.addEventListener('submit', e => {
        if (e.isTrusted && navigator.userActivation && navigator.userActivation.isActive)
            record(actual(e));
    }, true);
    document.addEventListener('click', e => {
        if (!e.isTrusted) return;
        const target = actual(e);
        const button = target.closest &&
            target.closest('button, input[type=submit], input[type=button], [role=button]');
        if (button) record(button.form || document);
    }, true);
    document.addEventListener('keydown', e => {
        const target = actual(e);
        if (e.isTrusted && e.key === 'Enter' && target instanceof HTMLInputElement)
            record(target.form || document);
    }, true);
})();
"""


def _script(body, **arguments):
    """A script with its arguments as one JSON object, run only on expected.

    Arguments are never spliced into code, and the isolated world's own
    location (not page-controlled) must still be the site the engine reported
    when Steed chose what to fill.
    """
    return (
        "(() => { const ARGS = "
        + json.dumps(arguments)
        + "; if (location.origin !== ARGS.expected) return null;"
        + body
        + "})()"
    )


def take_script(expected, force):
    """Hand over a captured login once its form is gone (or on navigation)."""
    return _script(
        """
    // Inputs in the page and inside web components' (open) shadow roots,
    // where some sign-in forms live; bounded on very large pages.
    const deepInputs = root => {
        const found = [], pending = [root];
        let seen = 0;
        while (pending.length && seen < 20000) {
            const node = pending.shift();
            node.querySelectorAll('input').forEach(el => found.push(el));
            for (const el of node.querySelectorAll('*')) {
                if (++seen >= 20000) break;
                if (el.shadowRoot) pending.push(el.shadowRoot);
            }
        }
        return found;
    };
    // The element really acted on, inside a shadow root if there is one.
    const actual = e => (e.composedPath && e.composedPath()[0]) || e.target;
    const capture = window.__steedLoginCapture;
    if (!capture) return null;
    const present = deepInputs(document).filter(el => el.type === 'password')
        .some(el => el.value === capture.password && el.getClientRects().length > 0);
    if (present && !ARGS.force) return {pending: true};
    window.__steedLoginCapture = null;
    return {username: capture.username, password: capture.password, fresh: capture.fresh};
""",
        expected=expected,
        force=bool(force),
    )


_FIELDS = """
    // Genuinely visible: not transparent, tiny or placed off the page.
    const visible = el => {
        if (!(el instanceof HTMLInputElement) || el.disabled || el.readOnly) return false;
        const box = el.getBoundingClientRect(), style = getComputedStyle(el);
        return box.width >= 8 && box.height >= 8 && style.visibility !== 'hidden' &&
            Number(style.opacity) > 0.1 && box.right > 0 && box.bottom > 0 &&
            box.left < document.documentElement.scrollWidth &&
            box.top < document.documentElement.scrollHeight;
    };
    // Inputs in the page and inside web components' (open) shadow roots,
    // where some sign-in forms live; bounded on very large pages.
    const deepInputs = root => {
        const found = [], pending = [root];
        let seen = 0;
        while (pending.length && seen < 20000) {
            const node = pending.shift();
            node.querySelectorAll('input').forEach(el => found.push(el));
            for (const el of node.querySelectorAll('*')) {
                if (++seen >= 20000) break;
                if (el.shadowRoot) pending.push(el.shadowRoot);
            }
        }
        return found;
    };
    // The element really acted on, inside a shadow root if there is one.
    const actual = e => (e.composedPath && e.composedPath()[0]) || e.target;
    const inputs = deepInputs(document).filter(visible);
    const set = (el, value) => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        el.focus();
        setter.call(el, value);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    };
    // A form that would send its fields to another site is never filled.
    const sameSite = el => !el.form ||
        new URL(el.form.action || location.href, location.href).origin === location.origin;
"""


def fill_script(expected, username, password, *, only_empty=True):
    """Fill a sign-in form; returns whether anything was filled."""
    return _script(
        _FIELDS
        + """
    const passwords = inputs.filter(el => el.type === 'password' && sameSite(el) &&
        !(el.autocomplete || '').includes('new-password'));
    const password = passwords.find(el => (el.autocomplete || '').includes('current-password')) ||
        passwords[0];
    const scope = password && password.form ? inputs.filter(el => el.form === password.form) : inputs;
    const code = el => (el.autocomplete || '').includes('one-time-code') ||
        /otp|totp|2fa|mfa|one.?time|verification|auth.?code|security.?code/i.test(
            el.name + ' ' + el.id + ' ' + (el.placeholder || ''));
    const text = scope.filter(el => ['text', 'email', 'tel', ''].includes(el.type) &&
        !code(el) && sameSite(el));
    const explicit = text.find(el => (el.autocomplete || '').includes('username')) ||
        text.find(el => el.type === 'email');
    // Without a password field (a username-first step) only a field marked as
    // the username or email is filled.
    const user = explicit || (password ? (
        text.find(el => /user|mail|login|name|account/i.test(el.name + ' ' + el.id)) ||
        (text.length === 1 ? text[0] : null)) : null);
    let filled = false;
    if (user && ARGS.username && (!ARGS.onlyEmpty || !user.value)) { set(user, ARGS.username); filled = true; }
    if (password && (!ARGS.onlyEmpty || !password.value)) { set(password, ARGS.password); filled = true; }
    return {filled, hasPassword: !!password, hasUser: !!user};
""",
        expected=expected,
        username=username,
        password=password,
        onlyEmpty=bool(only_empty),
    )


def code_script(expected, code):
    """Fill a one-time-code field that is still empty."""
    return _script(
        _FIELDS
        + """
    const field = inputs.find(el => (el.autocomplete || '').includes('one-time-code')) ||
        inputs.find(el => ['text', 'tel', 'number', ''].includes(el.type) &&
            /otp|totp|2fa|mfa|one.?time|verification|auth.?code|security.?code/i.test(
                el.name + ' ' + el.id + ' ' + (el.placeholder || '') + ' ' +
                (el.getAttribute('aria-label') || '')));
    if (!field || field.value || !sameSite(field)) return {filled: false};
    set(field, ARGS.code);
    return {filled: true};
""",
        expected=expected,
        code=code,
    )


def new_password_script(expected, password):
    """Put a generated password into a sign-up or change-password form."""
    return _script(
        _FIELDS
        + """
    const passwords = inputs.filter(el => el.type === 'password' && sameSite(el));
    let targets = passwords.filter(el => (el.autocomplete || '').includes('new-password') ||
        /new|confirm|repeat|again/i.test(el.name + ' ' + el.id));
    if (!targets.length && passwords.length === 1) targets = passwords;
    for (const el of targets) set(el, ARGS.password);
    return {filled: targets.length};
""",
        expected=expected,
        password=password,
    )


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
        self.used = {}
        self.idle = QTimer(self)
        self.idle.setInterval(60 * 1000)
        self.idle.timeout.connect(self._lock_idle)
        self.idle.start()

    def _lock_idle(self):
        now = time.monotonic()
        for profile, vault in self.vaults.items():
            if vault.unlocked and now - self.used.get(profile, now) > IDLE_LOCK_SECONDS:
                vault.lock()

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
        self.used[profile] = time.monotonic()
        if unlock and not vault.unlocked and not vault.needs_master_password():
            try:
                vault.unlock()
            except VaultError:
                return vault
        return vault

    def install(self, profile):
        """Watch for submitted logins in every page and frame of a profile."""
        webengine.add_profile_script(
            profile, "steed-password-capture", CAPTURE_SCRIPT, frames=True
        )

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


def old_pairings(config):
    """KeePassXC pairing files left by Steed versions before the vault."""
    found = []
    legacy = config.config_dir / "global_profile" / "password_manager.json"
    if legacy.is_file():
        found.append(legacy)
    if config.profiles_dir.is_dir():
        found.extend(
            path
            for path in sorted(config.profiles_dir.glob("*/password_manager.json"))
            if path.is_file()
        )
    return found
