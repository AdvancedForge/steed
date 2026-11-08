"""Search keywords: "w steed" in the address bar searches Wikipedia for "steed".

Built-in keywords cover common sites; your own (config "search_keywords") add
to them or replace one with the same keyword.
"""

import re
from urllib.parse import quote

BUILT_IN = {
    "w": ("Wikipedia", "https://en.wikipedia.org/w/index.php?search={q}"),
    "gh": ("GitHub", "https://github.com/search?q={q}"),
    "yt": ("YouTube", "https://www.youtube.com/results?search_query={q}"),
    "r": ("Reddit", "https://www.reddit.com/search/?q={q}"),
    "so": ("Stack Overflow", "https://stackoverflow.com/search?q={q}"),
    "maps": ("OpenStreetMap", "https://www.openstreetmap.org/search?query={q}"),
    "ddg": ("DuckDuckGo", "https://duckduckgo.com/?q={q}"),
    "g": ("Google", "https://www.google.com/search?q={q}"),
    "pypi": ("PyPI", "https://pypi.org/search/?q={q}"),
}
_KEYWORD = re.compile(r"^[a-z0-9]{1,12}$")
MAX_CUSTOM = 50


def clean(keyword, name, url):
    """A custom keyword, or ValueError explaining what is wrong."""
    keyword = str(keyword).strip().lower()
    name = " ".join(str(name).split())[:40]
    url = str(url).strip()
    if not _KEYWORD.match(keyword):
        raise ValueError("Use a keyword of 1 to 12 letters or digits.")
    if not name:
        raise ValueError("Give the search a name.")
    if not re.match(r"^https?://", url) or "{q}" not in url or len(url) > 500:
        raise ValueError("Use a web address with {q} where the search text goes.")
    return keyword, name, url


def keywords(config) -> dict:
    """keyword -> (name, url template); custom ones win."""
    found = dict(BUILT_IN)
    custom = config.get("search_keywords", {}) if config is not None else {}
    for keyword, value in custom.items() if isinstance(custom, dict) else []:
        try:
            name, url = value
            keyword, name, url = clean(keyword, name, url)
        except (TypeError, ValueError):
            continue
        found[keyword] = (name, url)
    return found


def match(text, config):
    """(keyword, name, search text) when text starts with a keyword and a space."""
    keyword, _, rest = (text or "").strip().partition(" ")
    rest = rest.strip()
    found = keywords(config).get(keyword.lower())
    if found is None or not rest:
        return None
    return keyword.lower(), found[0], rest


def search_url(text, config):
    """The address a keyword search goes to, or None for ordinary text."""
    matched = match(text, config)
    if matched is None:
        return None
    keyword, _, rest = matched
    return keywords(config)[keyword][1].replace("{q}", quote(rest, safe=""))
