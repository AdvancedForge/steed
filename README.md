# Steed

A browser for every day that can become whatever you need.

Steed is a Linux desktop browser (Python, PyQt6 and Qt WebEngine). Out of the
box it is a calm, ordinary browser: tabs, profiles, passwords, bookmarks,
history. Underneath it is a sandbox. Restyle every page, rearrange the browser,
add your own scripts and panel apps, and, if AI is part of how you work, bring
your agent into the browser to watch pages, sort tabs, compare products, fill
in forms and run whole workflows, always with your approval. Nothing of that
is in your way until you go looking for it.

## Everyday browsing

- Profiles kept apart, an incognito profile, and per-site permissions.
- Tab groups, saved groups, session history, sleeping tabs, split view.
- A built-in password manager (encrypted vault, generator, breach check,
  passkeys) and bookmark, history and password import.
- Site apps (a site in its own window), read aloud, keyboard link hints,
  search inside pages you've read, and your own shortcuts for everything.
- Printing, spell check, a bookmarks bar, reader view, translation, dictation
  (`Ctrl+Shift+Space`), dark websites, ad and tracker blocking, warnings for
  phishing and malware sites, Chrome (Manifest V3) extensions.

## Make it yours

- Lumen, Steed's look: the page floats in light that takes each site's colour.
  Motion, light, fonts, corners and colours are all settings.
- `steed:style` for themes and CSS on any site; user scripts and panel apps;
  editable New Tab and Welcome pages; a rearrangeable toolbar and menus.

## Bring your AI

- An agent sidebar for Claude Code, Codex or any Agent Client Protocol agent,
  and local MCP access for other clients.
- Routines: your agent on a schedule ("every morning, read my inbox and
  calendar and tell me what matters").
- Ask about a page or selection, watch a page and act when it changes,
  organize or compare tabs, hand a tab to your agent and take it back.
- You review what agents ask to do and choose what each may do (or, for agents
  you trust, let Steed approve for you).

## Quick start

Requires Python 3.10+ and a Linux desktop. From the project directory:

```bash
./install.sh            # or ./install.sh --default to make it your default browser
```

Then open Steed from your applications menu (or run `./steed`). Later,
`./install.sh --update` brings the newest version; Steed tells you when there
is one.

## Good to know

- Linux only, run from this folder (no package yet).
- The PyPI engine cannot play H.264/AAC video; `build_codecs.py` builds one that
  can ([details](DOCUMENTATION.md#installation)). Netflix-style protected video
  works when Chrome, Chromium or Firefox has already installed Google's Widevine
  module (see `steed:features`).
- [DOCUMENTATION.md](DOCUMENTATION.md): using and configuring Steed, plus its
  design. [ROADMAP.md](ROADMAP.md): progress and what is next.
  [RELEASE_PLAN.md](RELEASE_PLAN.md): the road to a public release.
