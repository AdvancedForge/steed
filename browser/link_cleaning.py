"""Remove tracking from links: on every page load (Settings → Remove tracking
from addresses), Copy Clean Link and Send to Your Phone.

Rule sets, chosen in Settings (link_rules):
- "steed": a short list of parameters that only ever track (utm_*, fbclid…),
  the same on every site. The most cautious.
- "clearurls": the ClearURLs project's rules (browser/data/clearurls.json,
  LGPL-3.0): hundreds of site rules (Amazon's /ref=…, YouTube's si=, Google's
  ved=…), and tracking redirects (google.com/url?q=…) go straight to where
  they lead. Sites a rule would break are listed as its exceptions.
- "file": your own rules file in the ClearURLs format (link_rules_file).
Your own parameter names (link_rules_extra) are removed with any of them.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

# Parameters that only identify a campaign, click or sharer, never the page.
TRACKING = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "yclid",
        "twclid",
        "ttclid",
        "li_fat_id",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "mkt_tok",
        "oly_anon_id",
        "oly_enc_id",
        "vero_id",
        "rb_clickid",
        "s_cid",
        "wickedid",
        "ns_mchannel",
        "ns_source",
        "ns_campaign",
        "ref_src",
        "ref_url",
        "spm",
    }
)
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "__hs", "ga_")
RULE_SETS = ("steed", "clearurls", "file")
CLEARURLS = Path(__file__).with_name("data") / "clearurls.json"
MAX_RULES_FILE = 4 * 1024 * 1024
_loaded = {}  # path -> (mtime, providers)


def is_tracking(name: str) -> bool:
    name = name.lower()
    return name in TRACKING or name.startswith(TRACKING_PREFIXES)


def _compile(pattern):
    try:
        return re.compile(pattern, re.I)
    except (re.error, TypeError):
        return None


def load_rules(path):
    """A ClearURLs-format rules file as compiled providers; [] if unusable."""
    path = Path(path)
    try:
        stamp = path.stat().st_mtime
        if path.stat().st_size > MAX_RULES_FILE:
            return []
    except OSError:
        return []
    cached = _loaded.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        providers = data["providers"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    compiled = []
    for provider in providers.values() if isinstance(providers, dict) else []:
        if not isinstance(provider, dict):
            continue
        site = _compile(provider.get("urlPattern"))
        if site is None:
            continue

        def patterns(key, provider=provider):
            values = provider.get(key) or []
            return [
                p
                for p in map(_compile, values if isinstance(values, list) else [])
                if p
            ]

        names = [
            p
            for p in (
                _compile(f"(?:{rule})")
                for rule in (provider.get("rules") or [])
                + (provider.get("referralMarketing") or [])
                if isinstance(rule, str)
            )
            if p
        ]
        compiled.append(
            {
                "site": site,
                "names": names,
                "raw": patterns("rawRules"),
                "exceptions": patterns("exceptions"),
                "redirections": patterns("redirections"),
            }
        )
    _loaded[path] = (stamp, compiled)
    return compiled


def _extra(config):
    text = str(config.get("link_rules_extra", "") or "") if config else ""
    return {word.lower() for word in re.split(r"[\s,]+", text) if word}


def _providers(config):
    kind = config.get("link_rules", "steed") if config else "steed"
    if kind == "clearurls":
        return load_rules(CLEARURLS)
    if kind == "file":
        own = str(config.get("link_rules_file", "") or "")
        return load_rules(own) if own else []
    return []


def _drop(url, removable):
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not removable(name)
    ]
    return urlunsplit(parts._replace(query=urlencode(kept, doseq=True)))


def clean_link(url: str, config=None, *, follow_redirects=True) -> str:
    """The link without tracking; other parameters keep their order. With
    the ClearURLs rules, a tracking redirect becomes the address it leads to."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return url
    extra = _extra(config)
    for provider in _providers(config):
        if not provider["site"].search(url) or any(
            e.search(url) for e in provider["exceptions"]
        ):
            continue
        if follow_redirects:
            for redirect in provider["redirections"]:
                found = redirect.search(url)
                target = unquote(found.group(1)) if found and found.groups() else ""
                if target.startswith(("http://", "https://")):
                    return clean_link(target, config, follow_redirects=False)
        for raw in provider["raw"]:
            url = raw.sub("", url)
        names = provider["names"]
        url = _drop(
            url, lambda name, names=names: any(n.fullmatch(name) for n in names)
        )
    return _drop(url, lambda name: is_tracking(name) or name.lower() in extra)
