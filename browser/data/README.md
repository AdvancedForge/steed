# Third-party data and scripts bundled with Steed

| File | What | Version | License |
|---|---|---|---|
| `clearurls.json` | ClearURLs tracking rules (providers only) | data.min.json, 2026-11-26 | LGPL-3.0 (`CLEARURLS-LICENSE.txt`) |
| `defuddle.js` | Defuddle article extractor, full browser bundle (with Markdown) | 0.19.13 | MIT (`DEFUDDLE-LICENSE.txt`) |
| `readability.js` | Mozilla Readability (Firefox's reader mode) | 0.6.0 | Apache-2.0 (`READABILITY-LICENSE.txt`) |
| `autoconsent.js` | DuckDuckGo autoconsent, `dist/autoconsent.playwright.js` unchanged | 18.12.0 | MPL-2.0 (`AUTOCONSENT-LICENSE.txt`) |
| `autoconsent-rules.json` | autoconsent's `rules/compact-rules.json` (opt-out rules) | 18.12.0 | MPL-2.0 |
| `autoconsent-optin.json` | autoconsent's `rules/rules.json`, only rules with `optIn`, without `optOut`, `test`, `vendorUrl`, `comment` and `_metadata` | 18.12.0 | MPL-2.0 |

These run only in Steed's own script worlds (pages cannot see or change them).
Reader View, Read Aloud, the reading list, agents reading a tab and link
cleaning use theirs when asked; autoconsent runs in the top frame of web pages
while Cookie banners is not "Leave them" (`browser/cookie_banners.py`). Update
them by replacing the file and the version here. autoconsent's source is at
https://github.com/duckduckgo/autoconsent.
