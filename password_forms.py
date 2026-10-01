"""Explicit login-form inspection and filling in an isolated JavaScript world."""

import json

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEngineScript

PASSWORD_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld.value


def login_url(url: QUrl):
    if (
        not url.isValid()
        or not url.host()
        or url.userName()
        or url.password()
        or (
            url.scheme() != "https"
            and not (
                url.scheme() == "http"
                and url.host() in ("localhost", "127.0.0.1", "::1")
            )
        )
    ):
        return None
    return url.adjusted(
        QUrl.UrlFormattingOption.RemoveQuery | QUrl.UrlFormattingOption.RemoveFragment
    ).toString()


def inspect_form_script(token, read_values=False):
    return """
    (() => {
        const visible = el => el instanceof HTMLInputElement && !el.disabled &&
            !el.readOnly && el.getClientRects().length > 0 &&
            getComputedStyle(el).visibility !== 'hidden';
        const inputs = Array.from(document.querySelectorAll('input')).filter(visible);
        const active = inputs.includes(document.activeElement) ? document.activeElement : null;
        const passwords = inputs.filter(el => el.type === 'password' &&
            (READ_VALUES || el.autocomplete !== 'new-password') &&
            !/confirm|repeat/i.test(el.name + el.id));
        let password = (active && passwords.includes(active)) ? active :
            (passwords.find(el => el.autocomplete === 'current-password') ||
             (passwords.length === 1 ? passwords[0] : null));
        const form = (active && active.form) || (password && password.form);
        const pool = form ? inputs.filter(el => el.form === form) : inputs;
        if (form && password && password.form !== form) password = null;
        const text = pool.filter(el => ['text', 'email', 'tel'].includes(el.type));
        const username = text.find(el => el.autocomplete.split(' ').includes('username')) ||
            text.find(el => el.type === 'email') ||
            text.find(el => /user|email|login/i.test(el.name + el.id)) ||
            (active && text.includes(active) ? active : null) ||
            (text.length === 1 ? text[0] : null);
        const submitUrl = form ? form.action : location.href;
        if (new URL(submitUrl, location.href).origin !== location.origin)
            return {error: 'Cross-site login forms are not supported.'};
        window.__steedPasswordForm = {
            token: TOKEN, url: location.href, username, password, form
        };
        return {
            token: TOKEN, url: location.href, submitUrl,
            hasUsername: !!username, hasPassword: !!password,
            login: READ_VALUES && username ? username.value : '',
            password: READ_VALUES && password ? password.value : ''
        };
    })()
    """.replace("TOKEN", json.dumps(token)).replace(
        "READ_VALUES", json.dumps(read_values)
    )


def fill_form_script(token, url, login, password):
    data = json.dumps(
        {"token": token, "url": url, "login": login, "password": password}
    )
    return """
    (() => {
        const data = DATA;
        const ctx = window.__steedPasswordForm;
        if (!ctx || ctx.token !== data.token || ctx.url !== data.url ||
            location.href !== data.url) return {error: 'The page changed. Retry filling.'};
        if (ctx.form && (!ctx.form.isConnected ||
            new URL(ctx.form.action, location.href).origin !== location.origin))
            return {error: 'The login form changed. Retry filling.'};
        const valid = el => !el || (el.isConnected && !el.disabled && !el.readOnly &&
            el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden');
        if (!valid(ctx.username) || !valid(ctx.password) ||
            (ctx.password && (ctx.password.type !== 'password' || ctx.password.form !== ctx.form)))
            return {error: 'The login fields changed. Retry filling.'};
        let filled = 0;
        const set = (el, value) => {
            if (!el || !value) return;
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            filled++;
        };
        set(ctx.username, data.login);
        // Input handlers can navigate or replace the form; recheck before releasing a password.
        if (location.href !== data.url || !valid(ctx.password) ||
            (ctx.password && (ctx.password.type !== 'password' || ctx.password.form !== ctx.form)) ||
            (ctx.form && (!ctx.form.isConnected ||
                new URL(ctx.form.action, location.href).origin !== location.origin)))
            return {error: 'The login form changed. Retry filling.'};
        set(ctx.password, data.password);
        if (!filled) return {error: 'No visible login fields found. Click a login field and retry.'};
        (ctx.password || ctx.username).focus();
        return {filled};
    })()
    """.replace("DATA", data)
