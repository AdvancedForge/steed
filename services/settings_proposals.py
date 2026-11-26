"""Agent-proposed changes to Steed's settings, applied only after review.

The person asks "make Ctrl+K open the command palette, add an 'mdn' search
keyword and hide that cookie banner on example.com". The agent proposes the
changes as one document: preferences (an allow-list; nothing that weakens
security or privacy, or touches agents), keyboard shortcuts, search keywords,
ad-blocking rules and sites, and per-site settings (zoom, JavaScript,
autoplay, pop-ups). Steed checks it and shows every change as "now → after"
in a popup; only the person's Apply changes anything. A proposal reads
nothing from the browser and grants no access. Proposals are kept in memory.
"""

import re
import time
from dataclasses import dataclass, field

from PyQt6.QtGui import QKeySequence

from browser import search_keywords as keywords_module
from browser.ad_blocker import MAX_OWN_RULES, MAX_RULE_LENGTH, own_rules
from browser.data_panel_format import stable_id
from browser.site_settings import CHOICES as SITE_CHOICES
from browser.site_settings import DEFAULTS as SITE_DEFAULTS
from browser.site_settings import _valid as site_value_ok
from services.task_policy import TaskPermissionError

MAX_CHANGES = 100
HOST = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def _page(value):
    return isinstance(value, str) and (
        re.match(r"^https?://\S{1,2000}$", value) is not None
        or re.match(r"^steed:[a-z-]{1,40}$", value) is not None
    )


def _search(value):
    return (
        isinstance(value, str) and re.match(r"^https?://\S{1,500}$", value) is not None
    )


# What a proposal may set, with a check and a label for the review. Settings
# that weaken security or privacy (safe browsing, HTTPS-first, fingerprinting,
# tracking removal, what Steed remembers, cookie sharing, the user agent) or
# change agents are left to the person in Settings.
PREFERENCES = {
    "home_page": ("Home page", _page),
    "new_tab_page": ("New tab page", _page),
    "search_engine": ("Search engine address", _search),
    "restore_tabs_on_startup": ("Reopen tabs on startup", bool),
    "dark_mode": ("Dark mode", bool),
    "dark_pages": ("Dark websites", bool),
    "spellcheck_language": (
        "Spell check language",
        lambda v: isinstance(v, str) and re.match(r"^([a-z]{2}-[A-Z]{2})?$", v),
    ),
    "open_again": ("When Steed is opened again", ("front", "window", "tab")),
    "devtools_side": ("Developer tools", ("right", "bottom", "window")),
    "save_before_closing": ("Ask before closing pages with unsaved work", bool),
    "search_suggestions": ("Search suggestions", bool),
    "address_autocomplete": ("Complete addresses as you type", bool),
    "tab_previews": ("Tab previews on hover", bool),
    "visual_effects": ("Motion and light", ("full", "balanced", "minimal")),
    "tab_card_fixed_width": ("Same width for every tab card", bool),
    "auto_zoom_small_text": ("Enlarge small text", bool),
    "translate_language": (
        "Translate into",
        lambda v: (
            isinstance(v, str) and re.match(r"^([a-z]{2,3}(-[A-Za-z]{2,4})?)?$", v)
        ),
    ),
}


# How Steed treats agents themselves stays with the person (access levels and
# approve-everything are not preferences at all). Every other setting can be
# proposed; one that weakens security or privacy is marked in the review.
AGENT_SETTINGS = frozenset({"keep_agent_chats", "agent_replay"})
WEAKENS = {
    "safe_browsing": False,
    "https_first": False,
    "strip_tracking": False,
    "resist_fingerprinting": False,
    "remember_pages": True,
    "secure_dns": "off",
    "global_cookie_sharing_mode": "all_profiles",
}


def weakens(key, value):
    if key == "user_agent_override":
        return bool(value)
    return key in WEAKENS and WEAKENS[key] == value


def proposable():
    """Every preference a proposal may set: (label, check) by key."""
    from ui.settings_dialog import DEFAULTS

    found = dict(PREFERENCES)
    for key, default in DEFAULTS.items():
        if key in found or key in AGENT_SETTINGS:
            continue
        kind = type(default)
        found[key] = (
            key.replace("_", " ").capitalize(),
            lambda v, kind=kind: (
                type(v) is kind and (not isinstance(v, str) or len(v) <= 4096)
            ),
        )
    return found


def _preference_ok(key, value):
    check = proposable()[key][1]
    if check is bool:
        return isinstance(value, bool)
    if isinstance(check, tuple):
        return value in check
    return bool(check(value))


def _sequence(value):
    """A portable shortcut, "" for none; or ValueError."""
    if value == "":
        return ""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Write shortcuts like Ctrl+Shift+K.")
    sequence = QKeySequence(value)
    text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if sequence.isEmpty() or sequence.count() != 1 or not text:
        raise ValueError(f"{value!r} is not a shortcut Steed understands.")
    return text


def check(document):
    """A cleaned proposal, or ValueError saying what is wrong."""
    allowed = {
        "summary",
        "preferences",
        "shortcuts",
        "search_keywords",
        "blocking",
        "sites",
    }
    if not isinstance(document, dict) or set(document) - allowed:
        raise ValueError(
            "Use only summary, preferences, shortcuts, search_keywords, "
            "blocking and sites."
        )
    summary = " ".join(str(document.get("summary", "")).split())[:300]
    preferences = document.get("preferences", {})
    if not isinstance(preferences, dict):
        raise ValueError("preferences is an object of setting: value.")
    for key, value in preferences.items():
        if key not in proposable():
            raise ValueError(f"{key!r} cannot be proposed; see settings.capabilities.")
        if not _preference_ok(key, value):
            raise ValueError(f"{value!r} is not a valid value for {key}.")
    shortcuts = {}
    raw = document.get("shortcuts", {})
    if not isinstance(raw, dict):
        raise ValueError("shortcuts is an object of command name: shortcut.")
    for name, value in raw.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError("Name commands as the menus do.")
        shortcuts[name] = _sequence(value)
    if len(set(v for v in shortcuts.values() if v)) != len(
        [v for v in shortcuts.values() if v]
    ):
        raise ValueError("Give each shortcut to one command only.")
    search = document.get("search_keywords", {})
    if not isinstance(search, dict) or set(search) - {"add", "remove"}:
        raise ValueError('search_keywords has "add" and "remove".')
    added = {}
    for keyword, value in (search.get("add") or {}).items():
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError('Add keywords as {"mdn": ["MDN", "https://…{q}"]}.')
        keyword, name, url = keywords_module.clean(keyword, *value)
        added[keyword] = [name, url]
    removed = [str(k).strip().lower() for k in search.get("remove") or []]
    blocking = document.get("blocking", {})
    lists = ("add_rules", "remove_rules", "allow_sites", "block_sites")
    if not isinstance(blocking, dict) or set(blocking) - set(lists):
        raise ValueError("blocking has " + ", ".join(lists) + ".")
    cleaned_blocking = {}
    for key in lists:
        values = blocking.get(key) or []
        if not isinstance(values, list):
            raise ValueError(f"blocking.{key} is a list.")
        if key.endswith("rules"):
            for rule in values:
                if (
                    not isinstance(rule, str)
                    or not rule.strip()
                    or len(rule) > MAX_RULE_LENGTH
                    or "\n" in rule
                ):
                    raise ValueError("Write each blocking rule on its own line.")
            cleaned_blocking[key] = [rule.strip() for rule in values]
        else:
            hosts = [str(h).strip().lower().lstrip(".") for h in values]
            if not all(HOST.match(h) for h in hosts):
                raise ValueError("Name sites by host, like example.com.")
            cleaned_blocking[key] = hosts
    sites = document.get("sites", {})
    if not isinstance(sites, dict):
        raise ValueError("sites is an object of host: settings.")
    cleaned_sites = {}
    for host, settings in sites.items():
        host = str(host).strip().lower()
        if not HOST.match(host) or not isinstance(settings, dict):
            raise ValueError("Name sites by host, like example.com.")
        for key, value in settings.items():
            if (
                key not in SITE_DEFAULTS
                or key == "forget"
                or not site_value_ok(key, value)
            ):
                raise ValueError(f"{value!r} is not a valid {key} for {host}.")
        cleaned_sites[host] = dict(settings)
    changes = (
        len(preferences)
        + len(shortcuts)
        + len(added)
        + len(removed)
        + sum(len(v) for v in cleaned_blocking.values())
        + sum(len(v) for v in cleaned_sites.values())
    )
    if not changes:
        raise ValueError("The proposal changes nothing.")
    if changes > MAX_CHANGES:
        raise ValueError(f"Propose at most {MAX_CHANGES} changes at once.")
    return {
        "summary": summary,
        "preferences": dict(preferences),
        "shortcuts": shortcuts,
        "search_keywords": {"add": added, "remove": removed},
        "blocking": cleaned_blocking,
        "sites": cleaned_sites,
    }


@dataclass(frozen=True)
class SettingsProposal:
    task_id: str
    request_id: str
    document: dict = field(default_factory=dict)
    state: str = "awaiting_approval"

    @property
    def request_key(self):
        return "settings:" + self.task_id + ":" + self.request_id


def _show(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value) if value not in ("", None) else "(none)"


class SettingsProposals:
    def __init__(self, service):
        self.service = service
        self.requests = {}  # (task, request id) -> dict

    @property
    def config(self):
        return self.service.profiles.config

    def _window(self):
        registry = self.service.profiles
        owner = getattr(registry, "last_active_owner", None) or next(
            iter(getattr(registry, "owners", ()) or ()), None
        )
        return owner.parent() if owner is not None else None

    def capabilities(self):
        from browser import shortcuts

        window = self._window()
        commands = (
            {
                name: shortcuts.overrides(self.config).get(name, "")
                or ", ".join(shortcuts._defaults(action))
                for name, action in shortcuts.commands(window)
            }
            if window is not None
            else {}
        )
        from ui.settings_dialog import DEFAULTS as defaults

        return {
            "preferences": {
                key: {
                    "label": label,
                    "values": list(test)
                    if isinstance(test, tuple)
                    else "true/false"
                    if test is bool or isinstance(defaults.get(key), bool)
                    else "text"
                    if isinstance(defaults.get(key), str)
                    else "see label",
                    "now": self.config.get(key, defaults.get(key)),
                    **({"weakens_when": WEAKENS[key]} if key in WEAKENS else {}),
                }
                for key, (label, test) in proposable().items()
            },
            "shortcuts": commands,
            "search_keywords": {
                keyword: name
                for keyword, (name, _) in keywords_module.keywords(self.config).items()
            },
            "blocking": {
                "own_rules": len(own_rules(self.config)),
                "rule_limit": MAX_OWN_RULES,
                "syntax": "EasyList/Adblock Plus: ||ads.example^ blocks, "
                "example.com##.selector hides, @@||example.com^ allows.",
            },
            "sites": {
                key: list(values)
                for key, values in SITE_CHOICES.items()
                if key != "forget"
            }
            | {"zoom": "0.25 to 5.0"},
            "example": {
                "summary": "Palette on Ctrl+K, an MDN keyword, no cookie banner",
                "shortcuts": {"Command Palette…": "Ctrl+K"},
                "search_keywords": {
                    "add": {
                        "mdn": ["MDN", "https://developer.mozilla.org/search?q={q}"]
                    }
                },
                "blocking": {"add_rules": ["example.com##.cookie-banner"]},
                "sites": {"example.com": {"zoom": 1.25}},
                "request_id": "settings-1",
            },
            "notes": "The person sees every change as now → after and must press "
            "Apply. Any setting can be proposed except how Steed treats agents; "
            "a change that weakens security or privacy is marked in the review.",
        }

    def changes(self, document):
        """Every change as (area, what, now, after), for the review."""
        from browser import shortcuts

        rows = []
        labels = proposable()
        for key, value in document["preferences"].items():
            rows.append(
                (
                    "Setting",
                    labels[key][0]
                    + (" (weakens security or privacy)" if weakens(key, value) else ""),
                    _show(self.config.get(key)),
                    _show(value),
                )
            )
        mine = shortcuts.overrides(self.config)
        for name, value in document["shortcuts"].items():
            rows.append(
                ("Shortcut", name, _show(mine.get(name, "(Steed's own)")), _show(value))
            )
        current = keywords_module.keywords(self.config)
        for keyword, (name, url) in document["search_keywords"]["add"].items():
            before = current.get(keyword)
            rows.append(
                (
                    "Search keyword",
                    keyword,
                    f"{before[0]}" if before else "(none)",
                    f"{name}: {url}",
                )
            )
        for keyword in document["search_keywords"]["remove"]:
            rows.append(("Search keyword", keyword, "kept", "removed"))
        labels = {
            "add_rules": ("Blocking rule", "(none)", "added"),
            "remove_rules": ("Blocking rule", "kept", "removed"),
            "allow_sites": ("Blocking on", "on", "off for this site"),
            "block_sites": ("Blocking on", "off for this site", "on"),
        }
        for key, values in document["blocking"].items():
            area, now, after = labels[key]
            rows += [(area, value, now, after) for value in values]
        for host, settings in document["sites"].items():
            for key, value in settings.items():
                rows.append(("Site " + host, key, "", _show(value)))
        return rows

    def propose(self, task_id, request_id, document):
        self.service._usable()
        self.service._active(task_id)
        if not stable_id(request_id):
            raise ValueError("Invalid request ID")
        cleaned = check(document)
        if cleaned["preferences"]:
            # The same checks as the Settings page, on the settings as they
            # would be (a search address, a DNS server, a choice of rules).
            from ui.settings_dialog import DEFAULTS, SettingsDialog

            after = {k: self.config.get(k, d) for k, d in DEFAULTS.items()}
            after.update(cleaned["preferences"])
            for k, d in DEFAULTS.items():
                if type(after[k]) is not type(d):
                    after[k] = d  # An odd stored value is not this proposal's.
            SettingsDialog._validate_values(after)
        key = (task_id, request_id)
        if key in self.requests:
            entry = self.requests[key]
            if entry["document"] != cleaned:
                raise ValueError("Request ID is already bound to a different proposal")
            return self._receipt(entry)
        pending = [
            e
            for (t, _), e in self.requests.items()
            if t == task_id and e["state"] == "awaiting_approval"
        ]
        if len(pending) >= 3:
            raise TaskPermissionError("settings_proposal_limit")
        entry = {
            "task_id": task_id,
            "request_id": request_id,
            "document": cleaned,
            "state": "awaiting_approval",
            "created": time.time(),
        }
        self.requests[key] = entry
        self.service.store.record_event(
            task_id,
            "settings.requested",
            {"request_id": request_id, "changes": len(self.changes(cleaned))},
        )
        self.service.changed.emit()
        return self._receipt(entry)

    def _receipt(self, entry):
        receipt = {"request_id": entry["request_id"], "state": entry["state"]}
        if entry.get("skipped"):
            receipt["skipped"] = entry["skipped"]
        return receipt

    def status(self, task_id, request_id):
        self.service._usable()
        entry = self.requests.get((task_id, request_id))
        if entry is None:
            raise KeyError("Settings proposal does not belong to this task")
        return self._receipt(entry)

    def pending(self, task_id):
        return [
            SettingsProposal(task_id, e["request_id"], e["document"], e["state"])
            for (task, _), e in self.requests.items()
            if task == task_id and e["state"] == "awaiting_approval"
        ]

    def resolve(self, task_id, request_id, *, approve=False, profile=None):
        """Trusted UI only. profile: whose per-site settings change."""
        entry = self.requests.get((task_id, request_id))
        if entry is None or entry["state"] != "awaiting_approval":
            raise ValueError("This proposal is no longer waiting.")
        if approve:
            entry["skipped"] = self._apply(entry["document"], profile)
        entry["state"] = "applied" if approve else "denied"
        self.service.store.record_event(
            task_id, "settings." + entry["state"], {"request_id": request_id}
        )
        self.service.changed.emit()
        return self._receipt(entry)

    def _apply(self, document, profile):
        """Apply a reviewed proposal; returns what could not be applied."""
        from browser import shortcuts

        skipped = []
        config, registry = self.config, self.service.profiles
        if document["preferences"]:
            config.update(dict(document["preferences"]))
        window = self._window()
        if document["shortcuts"]:
            known = (
                {name for name, _ in shortcuts.commands(window)} if window else set()
            )
            for name, value in document["shortcuts"].items():
                if name in known:
                    shortcuts.change(window, name, value)
                else:
                    skipped.append(f"shortcut for {name!r}: no such command")
        search = document["search_keywords"]
        if search["add"] or search["remove"]:
            custom = dict(config.get("search_keywords", {}) or {})
            custom.update(search["add"])
            for keyword in search["remove"]:
                custom.pop(keyword, None)
            config.set("search_keywords", custom)
        blocking = document["blocking"]
        if any(blocking.values()):
            rules = [
                r
                for r in own_rules(config)
                if r not in blocking.get("remove_rules", [])
            ]
            rules += [r for r in blocking.get("add_rules", []) if r not in rules]
            if len(rules) > MAX_OWN_RULES:
                skipped.append("blocking rules past the limit")
                rules = rules[:MAX_OWN_RULES]
            exceptions = set(config.get("adblock_exceptions", []) or [])
            exceptions |= set(blocking.get("allow_sites", []))
            exceptions -= set(blocking.get("block_sites", []))
            config.update(
                {"adblock_rules": rules, "adblock_exceptions": sorted(exceptions)}
            )
            blocker = getattr(registry, "adblock", None)
            if blocker is not None:
                blocker.refresh_settings()
                blocker.refresh_rules()
        if document["sites"]:
            settings = registry.site_settings(profile) if profile else None
            if settings is None:
                skipped.append("site settings: choose a profile")
            else:
                for host, values in document["sites"].items():
                    for key, value in values.items():
                        settings.set(host, key, value)
        for owner in list(getattr(registry, "owners", ()) or ()):
            refresh = getattr(owner, "refresh_settings", None)
            if refresh is not None:
                try:
                    refresh()
                except (OSError, RuntimeError, ValueError):
                    skipped.append("some engine settings until Steed restarts")
        changed = getattr(registry, "settings_changed", None)
        if changed is not None:
            changed.emit()
        return skipped

    def cancel_task(self, task_id, *, interrupted=False):
        for key in [k for k in self.requests if k[0] == task_id]:
            self.requests.pop(key)["state"] = "cancelled"
