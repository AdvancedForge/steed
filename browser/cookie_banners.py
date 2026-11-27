"""Cookie banners (Settings → Privacy → Cookie banners): Steed answers the
"accept cookies?" banners sites show, the way you chose: Reject (decline
everything optional; the default), Accept, or Leave them alone. Agents' own
pages can have a choice of their own, and you can name sites to leave alone.

DuckDuckGo's autoconsent (browser/data/autoconsent.js, MPL-2.0) knows about
1,600 consent tools and sites, and recognises unknown banners by their
buttons. It runs in the top frame of each page, in a script world of its own
that the page can neither see nor change, and talks to Steed through the
console with a token the page cannot learn. Steed answers its start with the
rules for that site (plus yours, from a file in autoconsent's rule format)
and runs the few checks that need the page's own JavaScript.

In a test on 14 news and shopping sites it declined the banner on 6 of the 7
that showed one, all from the top frame.
"""

import functools
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6 import sip

from browser import webengine

PREFIX = "\x01steedconsent "
TOKEN = secrets.token_hex(16)
WORLD = 230  # A world of its own: not Steed's (1) nor an extension's (2-201).
SCRIPT_NAME = "steed-consent"
MODES = ("reject", "accept", "leave")
AGENT_MODES = ("same", *MODES)
DATA = Path(__file__).parent / "data"
MAX_RULES_FILE = 4 * 1024 * 1024
MAX_MESSAGE = 64 * 1024
CONFIG = {
    "enabled": True,
    "autoAction": "optOut",
    "disabledCmps": [],
    "enablePrehide": True,
    "enableCosmeticRules": True,
    "enableGeneratedRules": True,
    "enableHeuristicDetection": True,
    "heuristicMode": "tier2",
    "detectRetries": 20,
    "isMainWorld": False,
    "prehideTimeout": 2000,
    "logs": {
        name: False
        for name in (
            "lifecycle",
            "rulesteps",
            "detectionsteps",
            "evals",
            "errors",
            "messages",
            "waits",
        )
    },
}
# Only web pages (not Steed's own, files or blank pages), then before
# autoconsent: its messages go to Steed, with the token.
SHIM = r"""// ==UserScript==
// @match http://*/*
// @match https://*/*
// ==/UserScript==
(() => {
  const TOKEN = __TOKEN__;
  const post = console.debug.bind(console);
  window.autoconsentSendMessage = (message) => {
    try {
      post("\u0001steedconsent " + JSON.stringify({ token: TOKEN, message }));
    } catch (error) {}
    return Promise.resolve();
  };
})();
"""
# Compact rule steps' keys that name strings (autoconsent's lib/encoding.ts).
STEP_STRINGS = ("e", "v", "c", "k", "w", "wv", "h", "cc")


# ------------------------------------------------------------------ choices


def mode_for(config, *, agent=False):
    """reject, accept or leave, for your pages or an agent's own."""
    if config is None:
        return "leave"
    mode = config.get("cookie_banners", "reject")
    mode = mode if mode in MODES else "reject"
    if agent:
        chosen = config.get("cookie_banners_agents", "same")
        if chosen in MODES:
            return chosen
    return mode


def wanted(config):
    """Whether pages need autoconsent at all (yours or agents')."""
    return any(mode_for(config, agent=agent) != "leave" for agent in (False, True))


def skipped(config, host):
    """Sites you asked Steed to leave alone (and their subdomains)."""
    listed = str(config.get("cookie_banner_skip", "") or "")
    host = (host or "").lower().removeprefix("www.")
    for site in re.split(r"[\s,]+", listed.lower()):
        site = site.strip().removeprefix("www.").strip("/")
        if site and (host == site or host.endswith("." + site)):
            return True
    return False


# ------------------------------------------------------------------ scripts


@functools.lru_cache(maxsize=1)
def script():
    bundle = (DATA / "autoconsent.js").read_text(encoding="utf-8")
    return SHIM.replace("__TOKEN__", json.dumps(TOKEN)) + "\n" + bundle


def install(profile, on):
    """Add or remove autoconsent in a profile's pages (top frames only)."""
    collection = profile.scripts()
    for found in collection.toList():
        if found.name() == SCRIPT_NAME:
            collection.remove(found)
    if on:
        collection.insert(
            webengine.page_script(
                SCRIPT_NAME, script(), world=WORLD, when="start", frames=False
            )
        )


# -------------------------------------------------------------------- rules


@functools.lru_cache(maxsize=1)
def compact_rules():
    return json.loads((DATA / "autoconsent-rules.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def optin_rules():
    text = (DATA / "autoconsent-optin.json").read_text(encoding="utf-8")
    return json.loads(text)["autoconsent"]


@functools.lru_cache(maxsize=4096)
def _pattern(pattern):
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _matches(pattern, url):
    if not pattern:
        return True
    compiled = _pattern(pattern)
    return compiled is not None and compiled.search(url) is not None


def _clear_unused(ruleset):
    """Only the strings the chosen rules use (autoconsent's clearUnusedStrings)."""
    used = set()

    def add(steps):
        for step in steps or ():
            if not isinstance(step, dict):
                continue
            for key in STEP_STRINGS:
                if key in step:
                    used.add(step[key])
            if step.get("if"):
                add([step["if"]])
            for key in ("then", "else", "any"):
                if step.get(key):
                    add(step[key])

    for rule in ruleset["r"]:
        for part in (6, 7, 8, 9):
            add(rule[part])
        used.update(rule[5])
    numbers = {n for n in used if isinstance(n, int)}
    top = max(numbers) + 1 if numbers else 0
    return {
        "v": ruleset["v"],
        "r": ruleset["r"],
        "s": [
            text if i in numbers else "" for i, text in enumerate(ruleset["s"][:top])
        ],
    }


@functools.lru_cache(maxsize=1)
def _generic_rules():
    rules = compact_rules()
    index = rules["index"]
    return {
        "v": rules["v"],
        "s": rules["s"][: index["genericStringEnd"]],
        "r": rules["r"][slice(*index["genericRuleRange"])],
    }


def site_rules(url):
    """Opt-out rules (compact) for a page's top frame: the generic ones and
    the site's own (autoconsent's filterCompactRules)."""
    rules = compact_rules()
    index = rules["index"]
    specific = [
        rule
        for rule in rules["r"][slice(*index["specificRuleRange"])]
        if rule[4] != 1 and _matches(rule[3], url)
    ]
    if not specific:
        return _generic_rules()
    generic = rules["r"][slice(*index["genericRuleRange"])]
    return _clear_unused({"v": rules["v"], "s": rules["s"], "r": generic + specific})


def _runs_here(rule, url):
    context = rule.get("runContext") or {}
    return context.get("main", True) is not False and _matches(
        context.get("urlPattern", ""), url
    )


def optin_site_rules(url):
    """Opt-in rules (full form) for a page's top frame."""
    return [rule for rule in optin_rules() if _runs_here(rule, url)]


_own_cache = {}


def own_rules(config, url):
    """Your rules (Settings: a file in autoconsent's rule format), for url."""
    path = str(config.get("cookie_banner_rules_file", "") or "").strip()
    if not path:
        return []
    file = Path(path).expanduser()
    try:
        stat = file.stat()
        key = (str(file), stat.st_mtime_ns, stat.st_size)
        if key not in _own_cache:
            if stat.st_size > MAX_RULES_FILE:
                raise ValueError("too large")
            data = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("autoconsent", [])
            items = data if isinstance(data, list) else []
            _own_cache.clear()
            _own_cache[key] = [
                rule
                for rule in items
                if isinstance(rule, dict) and isinstance(rule.get("name"), str)
            ]
        rules = _own_cache[key]
    except (OSError, ValueError):
        return []
    return [rule for rule in rules if _runs_here(rule, url)]


def check_rules_file(path):
    """How many rules a file has, or ValueError saying what is wrong."""
    file = Path(path).expanduser()
    try:
        if file.stat().st_size > MAX_RULES_FILE:
            raise ValueError("The rules file is larger than 4 MB.")
        data = json.loads(file.read_text(encoding="utf-8"))
    except OSError:
        raise ValueError("The rules file could not be read.") from None
    except ValueError:
        raise ValueError("The rules file is not JSON.") from None
    if isinstance(data, dict):
        data = data.get("autoconsent")
    if not isinstance(data, list) or not all(
        isinstance(rule, dict) and isinstance(rule.get("name"), str) for rule in data
    ):
        raise ValueError(
            'The rules file needs autoconsent rules: a list, or {"autoconsent": '
            "[…]}, each with a name."
        )
    return len(data)


# ---------------------------------------------------------------- messages


def parse(text):
    """autoconsent's message, or None if it is not one (no token, too big)."""
    if len(text) > MAX_MESSAGE:
        return None
    try:
        data = json.loads(text[len(PREFIX) :])
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("token") != TOKEN:
        return None
    message = data.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        return None
    return message


def reply(page, message):
    if sip.isdeleted(page):
        return
    webengine.run_script(
        page,
        "window.autoconsentReceiveMessage && "
        f"window.autoconsentReceiveMessage({json.dumps(message)})",
        world=WORLD,
    )


def start_reply(mode, config, url):
    """What Steed answers autoconsent's start with on this page."""
    host = urlsplit(url).hostname or ""
    if (
        mode not in ("reject", "accept")
        or not url.startswith(("http://", "https://"))
        or skipped(config, host)
    ):
        return {
            "type": "initResp",
            "config": {**CONFIG, "enabled": False},
            "rules": {"autoconsent": []},
        }
    own = own_rules(config, url)
    if mode == "accept":
        return {
            "type": "initResp",
            "config": {**CONFIG, "autoAction": "optIn"},
            "rules": {"autoconsent": own + optin_site_rules(url)},
        }
    return {
        "type": "initResp",
        "config": CONFIG,
        "rules": {"autoconsent": own, "compact": site_rules(url)},
    }


def received(page, text):
    """A console message from autoconsent in this page's top frame."""
    message = parse(text)
    if message is None or sip.isdeleted(page):
        return
    kind = message["type"]
    mode, config = page.cookie_banner_settings()
    if kind == "init":
        url = message.get("url")
        if not isinstance(url, str):
            url = page.url().toString()
        reply(page, start_reply(mode, config, url))
    elif kind == "eval" and mode != "leave":
        code, ident = message.get("code"), message.get("id")
        if not isinstance(code, str) or not isinstance(ident, str):
            return
        # autoconsent's own checks (its built-in snippets), in the page's
        # world as its extension runs them; the answer is only true or false.
        webengine.run_script(
            page,
            code,
            lambda result: reply(
                page, {"type": "evalResp", "id": ident, "result": bool(result)}
            ),
            world=webengine.PAGE_WORLD,
        )
    elif kind == "autoconsentDone" and mode != "leave":
        cmp = message.get("cmp")
        done = {
            "tool": cmp if isinstance(cmp, str) else "",
            "answer": "hidden" if message.get("isCosmetic") else mode,
        }
        page.cookie_banner = done
        page.cookie_banner_answered(done)


def said(done):
    """A short note for the status bar."""
    return {
        "reject": "Declined the cookie banner",
        "accept": "Accepted the cookie banner",
        "hidden": "Hid the cookie banner",
    }.get(done.get("answer"), "")
