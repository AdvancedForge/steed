"""Extensions' dynamic and session network rules (chrome.declarativeNetRequest).

Qt WebEngine 6.11 never finishes updateDynamicRules and ends the browser on
updateSessionRules, so Steed keeps these rules itself (extension_shim.js sends
them through the extension's bridge) and applies them in the profile's
request filter, ahead of ad blocking. Static rulesets stay with the engine.

Supported, as in Chrome: urlFilter (with ||, |, ^ and *) or regexFilter;
request and initiator domains (and their exclusions); resource types (by
default every type but main_frame); request methods; first or third party.
Actions: block, allow, allowAllRequests (allows everything on a page whose
address matches), upgradeScheme, redirect (url, extensionPath,
regexSubstitution, or a transform of scheme, host, port, path, query,
fragment and query parameters) and modifyHeaders for request headers that are
set. Response headers and appended or removed request headers cannot be
changed from a request filter in Qt, so those operations are skipped.

Dynamic rules are kept in <profile extensions>/steed_rules.json and last
until the extension removes them or is uninstalled; session rules last until
Steed quits or the extension is switched off.
"""

import json
import re
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PyQt6.QtCore import QUrl

MAX_DYNAMIC = 30000
MAX_SESSION = 5000
ACTIONS = (
    "allow",
    "allowAllRequests",
    "block",
    "upgradeScheme",
    "redirect",
    "modifyHeaders",
)
# Within one extension at the same priority, as Chrome orders them.
RANK = {name: index for index, name in enumerate(ACTIONS)}
RESOURCE_TYPES = {
    "main_frame",
    "sub_frame",
    "stylesheet",
    "script",
    "image",
    "font",
    "object",
    "xmlhttprequest",
    "ping",
    "csp_report",
    "media",
    "websocket",
    "webtransport",
    "webbundle",
    "other",
}
METHODS = {"connect", "delete", "get", "head", "options", "patch", "post", "put"}


def suffixes(host):
    """example host a.b.test: a.b.test, b.test, test."""
    labels = host.lower().split(".")
    return [".".join(labels[i:]) for i in range(len(labels))]


def _domain_in(host, domains):
    return any(part in domains for part in suffixes(host))


def _registrable(host):
    """A rough site key: the last two labels (three for short second levels)."""
    labels = host.lower().split(".")
    if len(labels) >= 3 and len(labels[-2]) <= 3 and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def url_filter(pattern, case_sensitive=False):
    """Chrome's urlFilter as a compiled regular expression."""
    start = end = ""
    body = pattern
    if body.startswith("||"):
        start = r"^[a-z][a-z0-9+.-]*://(?:[^/?#]*\.)?"
        body = body[2:]
    elif body.startswith("|"):
        start = "^"
        body = body[1:]
    if body.endswith("|"):
        end = "$"
        body = body[:-1]
    parts = []
    for char in body:
        if char == "*":
            parts.append(".*")
        elif char == "^":
            parts.append(r"(?:[^A-Za-z0-9_.%-]|$)")
        else:
            parts.append(re.escape(char))
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(start + "".join(parts) + end, flags)


def _strings(value, field, lower=True):
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{field} must be a list of strings.")
    return [v.lower() for v in value] if lower else list(value)


def _domains(value, field):
    found = _strings(value, field)
    return frozenset(found) if found is not None else None


# The longest plain run in a urlFilter: a request without it cannot match.
_RUNS = re.compile(r"[A-Za-z0-9%_-]{3,}")
# An address's words, as the index files rules under them.
_WORDS = re.compile(r"[a-z0-9%_-]{3,}")


def _bounded_word(body, anchored_start, anchored_end):
    """The longest word in a urlFilter that must be a whole word of any
    address it matches (separators on both sides, not *), lower case; or
    None. Such a rule is looked up by that word instead of tried on every
    request."""
    best = None
    for match in _RUNS.finditer(body):
        before = body[match.start() - 1] if match.start() else None
        after = body[match.end()] if match.end() < len(body) else None
        left = (before is None and anchored_start) or (
            before is not None
            and before not in "*"
            and not before.isalnum()
            and before not in "%_-"
        )
        right = (after is None and anchored_end) or (
            after is not None
            and after not in "*"
            and not after.isalnum()
            and after not in "%_-"
        )
        if left and right and (best is None or len(match.group()) > len(best)):
            best = match.group()
    return best.lower() if best else None


class Rule:
    """One checked rule, ready to match."""

    def __init__(self, raw, extension_id, checked=True):
        """checked: compile its filter now (rules from an extension's call);
        packaged rulesets compile each filter the first time it is needed."""
        if not isinstance(raw, dict):
            raise ValueError("Each rule must be an object.")
        rule_id = raw.get("id")
        if type(rule_id) is not int or rule_id < 1:
            raise ValueError("Rule id must be a positive integer.")
        priority = raw.get("priority", 1)
        if type(priority) is not int or priority < 1:
            raise ValueError(f"Rule with id {rule_id}: priority must be at least 1.")
        action = raw.get("action")
        condition = raw.get("condition", {})
        if not isinstance(action, dict) or action.get("type") not in ACTIONS:
            raise ValueError(f"Rule with id {rule_id} has an unsupported action.")
        if not isinstance(condition, dict):
            raise ValueError(f"Rule with id {rule_id}: condition must be an object.")
        self.raw, self.id, self.priority = raw, rule_id, priority
        self.extension_id = extension_id
        self.action = action
        self.kind = action["type"]
        url_text, regex_text = condition.get("urlFilter"), condition.get("regexFilter")
        if url_text is not None and regex_text is not None:
            raise ValueError(
                f"Rule with id {rule_id} cannot use both urlFilter and regexFilter."
            )
        sensitive = bool(condition.get("isUrlFilterCaseSensitive", False))
        if not (
            (url_text is None or (isinstance(url_text, str) and url_text))
            and (regex_text is None or (isinstance(regex_text, str) and regex_text))
        ):
            raise ValueError(
                f"Rule with id {rule_id} has an invalid url or regex filter."
            )
        self.filter = (url_text, regex_text, sensitive)
        self._pattern = False  # Not compiled yet.
        self.regex = regex_text if isinstance(regex_text, str) else None
        # A quick test before any regular expression, and an index key.
        self.token, self.key, self.word = None, None, None
        if isinstance(url_text, str):
            body = url_text[2:] if url_text.startswith("||") else url_text
            body = body.removeprefix("|")
            anchored_end = body.endswith("|")
            body = body.removesuffix("|")
            self.word = _bounded_word(
                body, url_text.startswith(("||", "|")), anchored_end
            )
            runs = _RUNS.findall(body)
            if runs:
                token = max(runs, key=len)
                self.token = token if sensitive else token.lower()
            if url_text.startswith("||"):
                rest = url_text[2:].lower()
                host = re.match(r"[a-z0-9.-]*", rest).group(0)
                after = rest[len(host) : len(host) + 1]
                if "." in host and after in ("", "^", "/", ":", "|"):
                    self.key = host
        if checked:
            try:
                _ = self.pattern
            except (re.error, ValueError):
                raise ValueError(
                    f"Rule with id {rule_id} has an invalid url or regex filter."
                ) from None
        self.domains = _domains(
            condition.get("requestDomains", condition.get("domains")),
            "requestDomains",
        )
        self.not_domains = _domains(
            condition.get("excludedRequestDomains", condition.get("excludedDomains")),
            "excludedRequestDomains",
        )
        self.initiators = _domains(
            condition.get("initiatorDomains"), "initiatorDomains"
        )
        self.not_initiators = _domains(
            condition.get("excludedInitiatorDomains"), "excludedInitiatorDomains"
        )
        types = _strings(condition.get("resourceTypes"), "resourceTypes")
        not_types = _strings(
            condition.get("excludedResourceTypes"), "excludedResourceTypes"
        )
        for value in (types or []) + (not_types or []):
            if value not in RESOURCE_TYPES:
                raise ValueError(f"Rule with id {rule_id}: unknown type {value}.")
        if types is None and not_types is None:
            not_types = ["main_frame"]
        if self.kind == "allowAllRequests" and not set(types or []) <= {
            "main_frame",
            "sub_frame",
        }:
            raise ValueError(
                f"Rule with id {rule_id}: allowAllRequests is only for frames."
            )
        self.types = set(types) if types is not None else None
        self.not_types = set(not_types or [])
        methods = _strings(condition.get("requestMethods"), "requestMethods")
        not_methods = _strings(
            condition.get("excludedRequestMethods"), "excludedRequestMethods"
        )
        for value in (methods or []) + (not_methods or []):
            if value not in METHODS:
                raise ValueError(f"Rule with id {rule_id}: unknown method {value}.")
        self.methods = set(methods) if methods is not None else None
        self.not_methods = set(not_methods or [])
        party = condition.get("domainType")
        if party not in (None, "firstParty", "thirdParty"):
            raise ValueError(f"Rule with id {rule_id}: unknown domainType.")
        self.party = party
        if self.kind == "redirect":
            redirect = action.get("redirect")
            if not isinstance(redirect, dict) or not (
                set(redirect)
                & {"url", "extensionPath", "regexSubstitution", "transform"}
            ):
                raise ValueError(f"Rule with id {rule_id}: redirect needs a target.")
            if "regexSubstitution" in redirect and self.regex is None:
                raise ValueError(
                    f"Rule with id {rule_id}: regexSubstitution needs a regexFilter."
                )

    @property
    def pattern(self):
        if self._pattern is False:
            url_text, regex_text, sensitive = self.filter
            if url_text:
                self._pattern = url_filter(url_text, sensitive)
            elif regex_text:
                self._pattern = re.compile(
                    regex_text, 0 if sensitive else re.IGNORECASE
                )
            else:
                self._pattern = None
        return self._pattern

    def keys(self):
        """Where the index files it: its request domains, or its ||host."""
        if self.domains is not None:
            return list(self.domains)
        return [self.key] if self.key else []

    def matches(self, request):
        url, host = request["url"], request["host"]
        if self.token is not None and self.token not in (
            url if self.filter[2] else request["lower"]
        ):
            return False
        try:
            pattern = self.pattern
        except re.error:
            return False
        if pattern is not None and not pattern.search(url):
            return False
        if self.domains is not None and not _domain_in(host, self.domains):
            return False
        if self.not_domains and _domain_in(host, self.not_domains):
            return False
        initiator = request["initiator"]
        if self.initiators is not None and not (
            initiator and _domain_in(initiator, self.initiators)
        ):
            return False
        if (
            self.not_initiators
            and initiator
            and _domain_in(initiator, self.not_initiators)
        ):
            return False
        kind = request["type"]
        if self.types is not None and kind not in self.types:
            return False
        if kind in self.not_types:
            return False
        method = request["method"]
        if self.methods is not None and method not in self.methods:
            return False
        if method in self.not_methods:
            return False
        if self.party is not None:
            first = not initiator or _registrable(initiator) == _registrable(host)
            if (self.party == "firstParty") != first:
                return False
        return True

    def redirect_target(self, url):
        redirect = self.action.get("redirect", {})
        if isinstance(redirect.get("url"), str):
            return redirect["url"]
        if isinstance(redirect.get("extensionPath"), str):
            path = redirect["extensionPath"].lstrip("/")
            return f"chrome-extension://{self.extension_id}/{path}"
        if isinstance(redirect.get("regexSubstitution"), str):
            substitution = re.sub(r"\\(\d)", r"\\g<\1>", redirect["regexSubstitution"])
            match = re.search(self.regex, url, re.IGNORECASE)
            return match.expand(substitution) if match else None
        transform = redirect.get("transform")
        if isinstance(transform, dict):
            return _transform(url, transform)
        return None


def _transform(url, transform):
    parts = urlsplit(url)
    scheme = transform.get("scheme", parts.scheme)
    host = transform.get("host", parts.hostname or "")
    port = transform.get("port", str(parts.port) if parts.port else "")
    netloc = host + (f":{port}" if port else "")
    path = transform.get("path", parts.path)
    query = parts.query
    if isinstance(transform.get("query"), str):
        query = transform["query"].lstrip("?")
    changes = transform.get("queryTransform")
    if isinstance(changes, dict):
        pairs = parse_qsl(query, keep_blank_values=True)
        remove = set(changes.get("removeParams") or [])
        pairs = [(k, v) for k, v in pairs if k not in remove]
        for item in changes.get("addOrReplaceParams") or []:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                continue
            key, value = item["key"], str(item.get("value", ""))
            found = [i for i, (k, _) in enumerate(pairs) if k == key]
            if found:
                pairs[found[0]] = (key, value)
            elif not item.get("replaceOnly"):
                pairs.append((key, value))
        query = urlencode(pairs)
    fragment = transform.get("fragment", "#" + parts.fragment if parts.fragment else "")
    return urlunsplit((scheme, netloc, path, query, fragment.lstrip("#")))


class Index:
    """A profile's rules from switched-on extensions, filed by domain, else
    by a whole word of their pattern; the rest are tried on every request."""

    def __init__(self, rules):
        self.by_domain, self.by_word, self.by_initiator = {}, {}, {}
        self.generic, self.count = [], 0
        for rule in rules:
            self.count += 1
            keys = rule.keys()
            if keys:
                for key in keys:
                    self.by_domain.setdefault(key, []).append(rule)
            elif rule.word:
                self.by_word.setdefault(rule.word, []).append(rule)
            elif rule.initiators:
                for key in rule.initiators:
                    self.by_initiator.setdefault(key, []).append(rule)
            else:
                self.generic.append(rule)

    def candidates(self, *hosts, url="", initiators=()):
        found = {}
        for host in initiators:
            for part in suffixes(host) if host else []:
                for rule in self.by_initiator.get(part, ()):
                    found[id(rule)] = rule
        for host in hosts:
            for part in suffixes(host) if host else []:
                for rule in self.by_domain.get(part, ()):
                    found[id(rule)] = rule
        for word in set(_WORDS.findall(url.lower())):
            for rule in self.by_word.get(word, ()):
                found[id(rule)] = rule
        for rule in self.generic:
            found[id(rule)] = rule
        return found.values()


EMPTY = Index([])


class ExtensionRules:
    """Every profile's extension rules; the request filter asks match().

    Three kinds per extension: its packaged (static) rulesets, read from its
    files when it is switched on (in the background: they can be large);
    dynamic rules, kept on disk; and session rules, kept in memory. The
    index the request filter reads is replaced whole, never changed in place.
    """

    def __init__(self, extensions):
        self.extensions = extensions
        self.dynamic = {}  # profile -> extension id -> [Rule]
        self.session = {}  # profile -> extension id -> [Rule]
        self.static = {}  # profile -> extension id -> {ruleset id: [Rule]}
        self.sets = {}  # profile -> extension id -> its rulesets' choices
        self.active = {}  # profile -> extension ids switched on
        self.paths = {}  # profile -> extension id -> installed folder
        self.index = {}  # profile -> Index
        self.loaded = set()
        self.lock = threading.RLock()

    # -- storage

    def _path(self, name, file="steed_rules.json"):
        return self.extensions.folder(name) / file

    def _load(self, name):
        if name in self.loaded:
            return
        self.loaded.add(name)
        rules = {}
        try:
            raw = json.loads(self._path(name).read_text())
        except (OSError, ValueError):
            raw = {}
        for extension_id, items in (raw if isinstance(raw, dict) else {}).items():
            kept = []
            for item in items if isinstance(items, list) else []:
                try:
                    kept.append(Rule(item, extension_id))
                except ValueError:
                    continue
            rules[extension_id] = kept
        self.dynamic[name] = rules
        try:
            sets = json.loads(self._path(name, "steed_rulesets.json").read_text())
        except (OSError, ValueError):
            sets = {}
        self.sets[name] = sets if isinstance(sets, dict) else {}

    def _write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data))
        temporary.replace(path)

    def _save(self, name):
        self._write(
            self._path(name),
            {
                extension_id: [rule.raw for rule in rules]
                for extension_id, rules in self.dynamic.get(name, {}).items()
                if rules
            },
        )

    def _save_sets(self, name):
        self._write(
            self._path(name, "steed_rulesets.json"),
            {e: v for e, v in self.sets.get(name, {}).items() if v},
        )

    def _reindex(self, name):
        with self.lock:
            active = self.active.get(name, set())
            rules = []
            for store in (self.dynamic, self.session):
                for extension_id, items in store.get(name, {}).items():
                    if extension_id in active:
                        rules.extend(items)
            for extension_id, rulesets in self.static.get(name, {}).items():
                if extension_id not in active:
                    continue
                disabled = (
                    self.sets.get(name, {})
                    .get(extension_id, {})
                    .get("disabledRules", {})
                )
                for ruleset_id, items in rulesets.items():
                    off = set(disabled.get(ruleset_id, []))
                    rules.extend(r for r in items if r.id not in off)
            self.index[name] = Index(rules) if rules else EMPTY

    # -- packaged rulesets

    def _manifest(self, name, extension_id):
        folder = self.paths.get(name, {}).get(extension_id)
        try:
            manifest = json.loads((Path(folder) / "manifest.json").read_text())
        except (OSError, ValueError, TypeError):
            return None, None
        return manifest, Path(folder)

    def rulesets(self, name, extension_id):
        """[(id, path, enabled by default)] from its manifest."""
        manifest, _folder = self._manifest(name, extension_id)
        section = (manifest or {}).get("declarative_net_request") or {}
        found = []
        for item in section.get("rule_resources") or []:
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("path"), str)
            ):
                found.append((item["id"], item["path"], item.get("enabled") is True))
        return found

    def enabled(self, name, extension_id):
        self._load(name)
        chosen = self.sets.get(name, {}).get(extension_id, {}).get("enabled")
        known = self.rulesets(name, extension_id)
        if isinstance(chosen, list):
            return [i for i, _path, _on in known if i in chosen]
        return [i for i, _path, on in known if on]

    def _read_static(self, name, extension_id):
        """Its enabled rulesets' rules (run in a background thread)."""
        _manifest, folder = self._manifest(name, extension_id)
        paths = {i: path for i, path, _on in self.rulesets(name, extension_id)}
        loaded = {}
        for ruleset_id in self.enabled(name, extension_id):
            try:
                raw = json.loads((folder / paths[ruleset_id].lstrip("/")).read_text())
            except (OSError, ValueError, KeyError, TypeError):
                continue
            rules = []
            for item in raw if isinstance(raw, list) else []:
                try:
                    rules.append(Rule(item, extension_id, checked=False))
                except ValueError:
                    continue
            loaded[ruleset_id] = rules
        return loaded

    def _load_static(self, name, extension_id, wait=False):
        def work():
            loaded = self._read_static(name, extension_id)
            with self.lock:
                if extension_id in self.active.get(name, set()):
                    self.static.setdefault(name, {})[extension_id] = loaded
                    self._reindex(name)

        if wait:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    def update_enabled(self, name, extension_id, options):
        self._load(name)
        if not isinstance(options, dict):
            raise ValueError("Ruleset options must be an object.")
        known = [i for i, _path, _on in self.rulesets(name, extension_id)]
        enable = options.get("enableRulesetIds") or []
        disable = options.get("disableRulesetIds") or []
        for ruleset_id in [*enable, *disable]:
            if ruleset_id not in known:
                raise ValueError(f"Invalid ruleset id: {ruleset_id}.")
        now = [i for i in self.enabled(name, extension_id) if i not in disable]
        now += [i for i in enable if i not in now]
        with self.lock:
            entry = self.sets.setdefault(name, {}).setdefault(extension_id, {})
            entry["enabled"] = now
            self._save_sets(name)
        self._load_static(name, extension_id, wait=True)

    def update_static(self, name, extension_id, options):
        self._load(name)
        if not isinstance(options, dict):
            raise ValueError("Options must be an object.")
        ruleset_id = options.get("rulesetId")
        if ruleset_id not in [i for i, _p, _o in self.rulesets(name, extension_id)]:
            raise ValueError(f"Invalid ruleset id: {ruleset_id}.")
        with self.lock:
            entry = self.sets.setdefault(name, {}).setdefault(extension_id, {})
            disabled = entry.setdefault("disabledRules", {})
            ids = set(disabled.get(ruleset_id, []))
            ids |= {i for i in options.get("disableRuleIds") or [] if type(i) is int}
            ids -= {i for i in options.get("enableRuleIds") or [] if type(i) is int}
            disabled[ruleset_id] = sorted(ids)
            self._save_sets(name)
            self._reindex(name)

    def disabled_rules(self, name, extension_id, ruleset_id):
        self._load(name)
        if ruleset_id not in [i for i, _p, _o in self.rulesets(name, extension_id)]:
            raise ValueError(f"Invalid ruleset id: {ruleset_id}.")
        entry = self.sets.get(name, {}).get(extension_id, {})
        return list(entry.get("disabledRules", {}).get(ruleset_id, []))

    # -- what extensions ask

    def get(self, name, extension_id, scope, rule_ids=None):
        self._load(name)
        store = self.dynamic if scope == "dynamic" else self.session
        rules = store.get(name, {}).get(extension_id, [])
        wanted = set(rule_ids) if isinstance(rule_ids, list) else None
        return [r.raw for r in rules if wanted is None or r.id in wanted]

    def update(self, name, extension_id, scope, options):
        """Remove, then add, as Chrome does; all or nothing."""
        self._load(name)
        if not isinstance(options, dict):
            raise ValueError("Rule options must be an object.")
        store = self.dynamic if scope == "dynamic" else self.session
        current = store.setdefault(name, {}).get(extension_id, [])
        remove = options.get("removeRuleIds") or []
        if not isinstance(remove, list) or not all(type(i) is int for i in remove):
            raise ValueError("removeRuleIds must be a list of integers.")
        kept = [r for r in current if r.id not in set(remove)]
        added = [Rule(raw, extension_id) for raw in options.get("addRules") or []]
        ids = {r.id for r in kept}
        for rule in added:
            if rule.id in ids:
                raise ValueError(f"Rule with id {rule.id} does not have a unique ID.")
            ids.add(rule.id)
        limit = MAX_DYNAMIC if scope == "dynamic" else MAX_SESSION
        if len(kept) + len(added) > limit:
            raise ValueError(f"The number of {scope} rules is over {limit}.")
        with self.lock:
            store[name][extension_id] = kept + added
            if scope == "dynamic":
                self._save(name)
            self._reindex(name)

    def set_active(self, name, extensions, wait=False):
        """Extensions switched on ({id: entry with its installed "path"}, or
        ids); session rules of the others go, packaged rules are read."""
        self._load(name)
        entries = extensions if isinstance(extensions, dict) else {}
        with self.lock:
            before = self.active.get(name, set())
            self.active[name] = set(extensions)
            paths = self.paths.setdefault(name, {})
            for extension_id, entry in entries.items():
                if isinstance(entry, dict) and entry.get("path"):
                    if paths.get(extension_id) != entry["path"]:
                        paths[extension_id] = entry["path"]
                        before = before - {extension_id}  # Installed again.
            session = self.session.get(name, {})
            for extension_id in [e for e in session if e not in self.active[name]]:
                session.pop(extension_id)
            static = self.static.setdefault(name, {})
            for extension_id in [e for e in static if e not in self.active[name]]:
                static.pop(extension_id)
            self._reindex(name)
        for extension_id in self.active[name] - before:
            if self.rulesets(name, extension_id):
                self._load_static(name, extension_id, wait=wait)

    def forget(self, name, extension_id):
        """Uninstalled: its rules go too."""
        self._load(name)
        with self.lock:
            self.session.get(name, {}).pop(extension_id, None)
            self.static.get(name, {}).pop(extension_id, None)
            if self.sets.get(name, {}).pop(extension_id, None) is not None:
                self._save_sets(name)
            if self.dynamic.get(name, {}).pop(extension_id, None) is not None:
                self._save(name)
            self._reindex(name)

    # -- requests

    def match(self, name, request):
        """What to do with a request: None, or ("block",), ("redirect", url),
        ("allow",), ("headers", {name: value})."""
        index = self.index.get(name, EMPTY)
        if not index.count:
            return None
        request = {**request, "lower": request["url"].lower()}
        page = None
        if request["page"]:
            page_url = request["page"]
            page = {
                **request,
                "url": page_url,
                "lower": page_url.lower(),
                "host": QUrl(page_url).host(),
                "type": "main_frame",
                "method": "get",
            }
        best, headers = {}, {}
        hosts = (request["host"], page["host"] if page else "")
        urls = request["url"] + " " + (page["url"] if page else "")
        initiators = (request["initiator"], page["host"] if page else "")
        for rule in index.candidates(*hosts, url=urls, initiators=initiators):
            if rule.kind == "allowAllRequests":
                hit = (page is not None and rule.matches(page)) or rule.matches(request)
            else:
                hit = rule.matches(request)
            if not hit:
                continue
            if rule.kind == "modifyHeaders":
                for change in rule.action.get("requestHeaders") or []:
                    if (
                        isinstance(change, dict)
                        and change.get("operation") == "set"
                        and isinstance(change.get("header"), str)
                    ):
                        headers[change["header"]] = str(change.get("value", ""))
                continue
            key = (rule.priority, -RANK[rule.kind])
            known = best.get(rule.extension_id)
            if known is None or key > known[0]:
                best[rule.extension_id] = (key, rule)
        decisions = [rule for _key, rule in best.values()]
        if any(r.kind == "block" for r in decisions):
            return ("block",)
        for kind in ("upgradeScheme", "redirect"):
            for rule in decisions:
                if rule.kind != kind:
                    continue
                if kind == "upgradeScheme":
                    if request["url"].startswith("http://"):
                        return ("redirect", "https://" + request["url"][7:])
                    continue
                target = rule.redirect_target(request["url"])
                if target and target != request["url"]:
                    return ("redirect", target)
        if decisions:  # Allowed: nothing more to do, and no ad filtering.
            return ("allow",)
        return ("headers", headers) if headers else None


def request_of(request):
    """What a rule looks at in a request (webengine.Request)."""
    url = request.url
    initiator = request.initiator
    page = request.page
    return {
        "url": url.toString(),
        "host": url.host(),
        "initiator": initiator.host() if initiator.isValid() else "",
        "type": request.resource if request.resource in RESOURCE_TYPES else "other",
        "method": request.method.decode("ascii", "replace").lower(),
        "page": page.toString() if page.scheme() in ("http", "https") else "",
    }


def apply(profile, request):
    """In a profile's request filter: True when an extension's rule decided."""
    holder = getattr(profile, "steed_rules", None)
    if holder is None:
        return False
    rules, name = holder
    if not rules.index.get(name, EMPTY).count:
        return False
    if request.url.scheme() not in ("http", "https", "ws", "wss"):
        return False
    outcome = rules.match(name, request_of(request))
    if outcome is None:
        return False
    if outcome[0] == "block":
        request.block()
        return True
    if outcome[0] == "redirect":
        request.redirect(QUrl(outcome[1]))
        return True
    if outcome[0] == "headers":
        for header, value in outcome[1].items():
            request.set_header(header.encode(), value.encode())
        return False
    return True  # allow
