# Third-party data and scripts bundled with Steed

| File | What | Version | License |
|---|---|---|---|
| `clearurls.json` | ClearURLs tracking rules (providers only) | data.min.json, 2026-11-26 | LGPL-3.0 (`CLEARURLS-LICENSE.txt`) |
| `defuddle.js` | Defuddle article extractor, full browser bundle (with Markdown) | 0.19.13 | MIT (`DEFUDDLE-LICENSE.txt`) |
| `readability.js` | Mozilla Readability (Firefox's reader mode) | 0.6.0 | Apache-2.0 (`READABILITY-LICENSE.txt`) |

These run only in Steed's own script world (pages cannot see or change them)
and only when a feature asks: Reader View, Read Aloud, the reading list,
agents reading a tab, and link cleaning. Update them by replacing the file and
the version here.
