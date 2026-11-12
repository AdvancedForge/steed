# Steed documentation

Setup, usage, configuration, development checks, and technical design reference.
See [README.md](README.md) for the project overview and [ROADMAP.md](ROADMAP.md)
for current progress and remaining work.

## Contents

- [Installation](#installation)
- [Desktop launcher](#launch-without-a-terminal)
- [Graphics troubleshooting](#graphics-troubleshooting)
- [Google sign-in](#google-sign-in)
- [Usage](#usage)
- [Welcome, New Tab and bookmarks](#welcome-new-tab-and-bookmarks)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Find in page and zoom](#find-in-page-and-zoom)
- [Website permissions](#website-permissions)
- [Passwords](#passwords)
- [Profiles and stored data](#profiles-and-stored-data)
- [Tabs and groups](#tabs-and-groups)
- [Cookies and global sharing](#cookies-and-global-sharing)
- [Downloads and ad blocking](#downloads-and-ad-blocking)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Engine and storage internals](#engine-and-storage-internals)
- [Internal task services](#internal-task-services)
- [Remembering agent approvals](#remembering-agent-approvals)
- [Continuing previous work](#continuing-previous-work)
- [Generated files and custom integration packages](#generated-files-and-custom-integration-packages)
- [Running an agent-built local worker](#running-an-agent-built-local-worker)
- [Worker dependency decision](#worker-dependency-decision)
- [Google app connections](#google-app-connections)
- [Local agent access (MCP)](#local-agent-access-mcp)
- [Development checks](#development-checks)
- [Verification record](#verification-record)
- [Current limitations](#current-limitations)
- [Planned architecture](#planned-architecture)
- [References](#references)

## Installation

Requires Python 3.10 or later and a graphical desktop session. Dependencies use PyQt6 and PyQt6-WebEngine 6.11.0 with Qt 6.11.x. Linux x86-64 wheels require glibc 2.34 or later; ARM64 wheels require glibc 2.39 or later.

From the project directory, create a virtual environment if one does not already exist:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade -r requirements.txt
```

Run the browser with that environment:

```bash
./steed
```

The executable launcher always uses the project's virtual environment. You can
also run `.venv/bin/python main.py` directly. Running `python3 main.py` without
activating the environment may use a different PyQt installation.

### Launch without a terminal

After installing dependencies, register this checkout in your Linux application
menu once:

```bash
.venv/bin/python install_launcher.py
```

Open **Steed** from the applications menu, and pin it to your panel if desired.
The launcher runs with `Terminal=false`, so no terminal needs to stay open.
It uses this checkout's `.venv` and works regardless of the desktop's working
directory. No root access or standalone executable build is needed.

The executable `steed` in the project directory also starts the browser directly.
Keep the checkout and `.venv` in place; if you move them, run the installer again
from the new location. To remove the menu entry, delete `steed.desktop` from
`${XDG_DATA_HOME:-$HOME/.local/share}/applications/`.

Launching Steed again while it is running opens a new window in the running
browser instead of a second copy; `./steed https://example.org` opens the link
as a tab in the most recently used window. Only HTTP/HTTPS addresses are passed
on, through a private socket in your runtime directory. Run the installer again
to add link arguments (`%U`) to an existing menu entry. If the running browser
does not answer within two seconds, a warning is shown instead.

### Graphics troubleshooting

The browser lets Qt select its graphics backend. Older versions forced `--use-gl=desktop`, which can abort with `--use-gl=desktop is not supported with the current configuration`.

If that option was also set in your shell environment, clear it before launching:

```bash
unset QTWEBENGINE_CHROMIUM_FLAGS
.venv/bin/python main.py
```

For a separate GPU-driver problem, try disabling GPU acceleration for that run:

```bash
QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" .venv/bin/python main.py
```

The browser preserves explicit Chromium options and enables PipeWire screen capture without forcing a graphics backend.

### Video codecs (H.264 and AAC) — optional

The PyPI Qt WebEngine plays WebM/VP8/VP9, AV1, Opus and MP3, but not the
patent-licensed H.264 video and AAC audio. Videos and "GIFs" (short MP4 files,
as on Discord, Tenor and Giphy) that come only in those formats do not play.
`build_codecs.py` builds the same Qt WebEngine version from source with
proprietary codecs and swaps it into `.venv`; Steed's code does not change.
The build is for use on this computer, not for redistribution.

```bash
.venv/bin/python build_codecs.py check     # prints the apt command for missing packages
.venv/bin/python build_codecs.py build     # hours; run again to continue after a stop
.venv/bin/python build_codecs.py install   # backs up the PyPI files, then swaps
.venv/bin/python build_codecs.py status    # which engine is in use and what plays
.venv/bin/python build_codecs.py restore   # put the PyPI engine back
.venv/bin/python build_codecs.py clean     # free the build's disk space
```

Steed works without it; only videos that come solely as H.264/AAC need it. The
build needs about 15 GB of disk while it runs and takes several hours (about
five on an 8-core, 16 GB machine). Afterwards `clean` removes the source,
compiled files, Qt's development files and build tools (about 13 GB), keeping
only the finished engine (`stage/`, under 0.5 GB) so `install` still works after
a pip reinstall. It lists what it removes and asks first (`--yes` skips the
question); a later build downloads and compiles everything again.

The build works in `~/src/steed-qtwebengine` (`--work-dir` to change; it checks
for 60 GB free to leave room) with one compile job per 4 GB of memory by default (`--jobs`;
Chromium's largest combined source files need about that much each, and six
jobs ran out of memory on a 16 GB machine). A stopped build continues where it
left off when `build` is run again. It downloads Qt's source for the exact `PyQt6-WebEngine-Qt6` version
in `.venv`, checks Qt's published checksum, and builds against Qt's matching
development files (installed there with aqtinstall, as are cmake and ninja).
`install` refuses a build of another version, or one whose libraries lack any
function the PyPI ones export. Reinstalling or upgrading `PyQt6-WebEngine-Qt6`
with pip puts the PyPI engine back; `status` says so, and `install` (or a new
`build` for a new version) restores codecs. `requirements.txt` pins the exact
version for this reason.

Measured on the development machine, the codec build uses the same memory as
the PyPI engine (383 against 386 MB with one tab once idle; the engine library's
resident pages are the same in every process) and the same speed, and plays
H.264 with AAC (a 3-second test video decoded all 90 frames). Its library has
three different internal libstdc++ exports (digit tables), which nothing in
Steed's environment uses; `install` refuses only missing names that PyQt6 or a
Qt library actually imports.

If that happens, Steed says so a few seconds after it starts (once per Qt
version if you choose **Don't remind me for this version**), with a button to
`steed:features`. The check compares the engine library's size and modification
time with the ones recorded at install, so it costs nothing measurable at
startup; `build_codecs.py status` compares the full checksum.

### Engine & features (`steed:features`)

**View → Engine & Features…** or `steed:features` shows the Qt WebEngine
version, the Chromium version its web features come from, the Chromium version
of its security fixes, and whether the video-codec build is installed. Below,
44 web features are tested in a real https page in a private throwaway profile
(video and audio formats, JavaScript, page features, CSS, devices, sign-in and
storage, built-in AI). Each missing one says why: newer than this engine (a Qt
WebEngine upgrade brings it), left out of Qt WebEngine on Linux (an upgrade will
not), or missing from the PyPI engine (`build_codecs.py`). **Check again**
repeats the test, for example after swapping the engine and restarting.

### Upgrading Qt WebEngine

`upgrade_engine.py` moves Steed to newer PyQt6 / Qt WebEngine releases
without risking the working setup:

```bash
.venv/bin/python upgrade_engine.py check      # installed and newest versions
.venv/bin/python upgrade_engine.py try        # .venv-upgrade: install and test
.venv/bin/python upgrade_engine.py try --quick --compare before.json
.venv/bin/python upgrade_engine.py apply      # pin and install what passed
.venv/bin/python upgrade_engine.py rollback   # versions from before apply
```

`try` installs the newest releases into `.venv-upgrade` (never `.venv`) and
reports the Chromium version and web-feature results (as on `steed:features`),
the startup smoke test, the test suite (`--quick` runs a subset of nine files)
and, with `--compare`, the timing check against an earlier result. Its report
is `.venv-upgrade/steed-trial.json`. `apply` refuses unless that trial passed,
records the current versions for `rollback`, pins the four packages exactly in
`requirements.txt` and installs them into `.venv`. A video-codec build is for one
version: after `apply`, run `build_codecs.py build` and `install` again.

### Google sign-in

Google sign-in compatibility is enabled by default in **General → Settings**. It sends a Firefox User-Agent to `https://accounts.google.com/`, following [qutebrowser's account-host workaround](https://github.com/qutebrowser/qutebrowser/issues/5182). Other sites use your configured/default User-Agent. Main-page GET redirects into or out of the account host are restarted to avoid Qt losing the per-site header; POST requests are never replayed.

Restart with `.venv/bin/python main.py`, then open `https://accounts.google.com/` and sign in normally. The compatibility setting takes precedence over the global User-Agent override for this host. Reload sign-in tabs after changing the setting.

Local HTTPS tests verify redirects, form bodies, and the opt-out on the pinned Qt 6.11 runtime. The user reported account sign-in working on October 1, 2026; persistence after restart and third-party sign-in remain unconfirmed. [Google can restrict embedded-browser sign-ins](https://support.google.com/accounts/answer/7675428), and this workaround cannot guarantee acceptance for every account. External-browser OAuth does not create a signed-in web session in this browser.

The same HTTPS fixture stalled on the older system Qt 6.4.2 installation. Use the project's virtual environment for Google sign-in; older-Qt startup compatibility does not establish login compatibility.

## Welcome, New Tab and bookmarks

Fresh installations start in **Global**, with no automatically created Default
profile. Existing Default data remains an ordinary profile you can rename or
archive through Profile; it is not deleted. Older Default startup selections
become Global, while other explicitly selected profiles are preserved.

`steed:welcome` introduces browsing, agent setup and enabled folders. Choose
**Start browsing** to complete onboarding. General → Welcome to Steed reopens it.
`steed:newtab` is both the initial Home and New Tab address. Change them separately
in **General → Settings**; Home uses the first setting, while Ctrl+T uses the
second. Saved sessions continue to restore normally. The earlier double-colon
spellings are accepted as aliases; the address bar shows one colon.

Open **General → Customize New Tab**, or click Customize on a built-in page:

- Check and drag sections to choose their presence and order: search, local
  background image, bookmarks, recently closed tabs, recent visits and news.
  Minimal page hides the heading/header/footer, allowing just a search bar.
- Choose a local image; Steed resizes and copies it, so moving the original file
  does not break the page. Input is bounded to 8 MB/32 megapixels and the saved
  PNG to 2 MB.
- News is optional. Enable News and save an RSS/Atom address to fetch it when
  the page opens. Feeds receive no browser cookies or stored authentication;
  redirects are not followed. Requests time out after ten seconds, responses
  are limited to 2 MB/20 entries, and the in-memory cache lasts ten minutes.
  Refresh retries explicitly. Removing News or its address stops future reads.
  Feed updates change only the news card, preserving search drafts and scroll.
- The **Custom HTML** tab edits either New Tab or Welcome. Check Replace to
  use your source, or import a UTF-8 HTML file; uncheck to restore the built-in
  page. Both pages save together. The copied source is limited to 256 KB.
  Inline CSS/JavaScript and embedded data/blob images work. External resources
  cannot be loaded directly; ordinary links and HTTP(S) search-form navigation
  still work. General → Customize New Tab remains available even when your
  replacement has no Customize link.
- For dashboards, a custom page can ask Steed for data with `fetch()`:
  `steed://newtab/api/fetch?url=<encoded https address>` (web data, no cookies,
  2 MB, 15 s), `/api/tabs`, `/api/history` (latest 200) and `/api/bookmarks`
  (on the Welcome page use `steed://welcome/api/…`). Each follows **Agents →
  Access & Permissions** (`steed:access`) → Local pages: Never (default),
  Ask (a question the first time each session; "Always allow" saves it) or
  Always. Refused requests answer `{"error": "denied"}`. Only the start page
  itself can call these addresses; websites cannot.

### Panel apps (`steed:apps`)

Small apps of your own — notes, a feed reader, a tracker, a dashboard — shown in
the **App Panel** beside the page (**View → App Panel**). Each is one HTML page
with its own CSS and JavaScript (up to 256 KB), kept per profile (up to 12).

- **steed:apps** (or **Manage…** in the panel): **New app** starts from an
  example with a clock, notes and your open tabs; edit the HTML, **Save** or
  **Save and open**; **Import HTML…** adds a file; **Delete** removes an app's
  HTML (data it stored stays in the profile's site data).
- Each app runs at its own `steed://app-<id>/` address, so apps keep separate
  local storage. They cannot load web resources directly; like custom New Tab
  pages they ask Steed with `fetch("steed://" + location.host + "/api/fetch?url=…")`
  (no cookies), `/api/tabs`, `/api/history` and `/api/bookmarks`, under the
  Local pages access settings below. An Ask answer applies to that app only.
- Links and pop-ups an app opens go to normal tabs; the panel itself stays on
  its app. Websites cannot load or call app addresses. Panel apps are off in
  incognito, and the panel follows the window's profile.

### Access & Permissions (`steed:access`)

One place to choose how much agents and your own pages may do. Each area has
presets — **Nothing**, **Ask** and **Everything** — over individual capabilities,
each Never, Ask or Always (changing one shows the area as Custom).

- **Agents** (default Ask): read and browse pages; type, click and submit;
  screenshots and mouse/keyboard; files and downloads; run generated code;
  connected apps; your signed-in profiles. A request needing any Never
  capability is refused with the reason, without a prompt; one needing only
  Always capabilities is approved without a prompt; anything else asks as
  before. Settings apply to new requests. YOLO mode (Agents menu, this run only)
  still approves everything except capabilities set to Never.
- **Local pages** (default Nothing): web data, open tabs, history and bookmarks
  for your custom New Tab and Welcome pages and panel apps, as above.

Ctrl+D saves an HTTP(S) page with an editable name and folder. **Tabs → Manage
Bookmarks** searches names/addresses/folders, filters by folder, and edits names,
addresses and folder labels while preserving bookmark IDs. Type a new folder name
when saving/editing; an empty folder means Unfiled. Changes from another window
refresh the manager. New Tab shows the first 24 bookmarks with **Manage all**.
Press **Ctrl+Shift+O** to open the manager directly.
Type in its search field and press **Enter** to open the first matching bookmark;
the current folder filter applies. If nothing matches, the manager stays open.
To choose a different match, focus the results list, select it with the arrow
keys, and press **Enter** (keypad Enter also works) to open the selected bookmark.
Select a bookmark and click **Open**, or double-click it, to open a new tab in
the current profile. Use **Edit…** to change its details. If the profile has
changed while the manager was open, reopen the manager before opening a link.

**Import HTML…** merges a UTF-8 browser bookmark export (up to 8 MiB and 2,000
entries per profile). Existing links keep their names/folders; duplicate and invalid
links are counted. Only HTTP(S) addresses without embedded credentials are accepted.
HTML is parsed as inert data; scripts and external entities are never executed or
fetched. Nested imported folders become readable labels such as `Work / Research`.
Folders contain assigned bookmarks; empty-folder trees are not retained. Malformed,
oversized, over-capacity or failed writes preserve the existing collection.
**Export HTML…** writes all bookmarks in the current profile to a portable Netscape
HTML file, including escaped names and folder labels; search filters do not limit
that export. Saving uses atomic replacement. Stale dialogs cannot write into a
renamed/deleted/recreated profile; reopen the manager after a profile change.

Bookmarks, sections, images and HTML belong to each profile
in `start_page.json`; incognito keeps them only in memory. Recently closed tabs
are bounded to 50 and shared by windows on that profile for the current browser
run. Reopening a listed tab preserves its group and saved identity. Internal
pages do not enter browsing history.

**External agents and website chats:** Share source with an agent previews a
`steed_page_source` message and lets you choose one connected task before Send.
It grants no file, profile or account access. Source exceeding the conversation's
8 KB limit can be exported to an enabled shared folder. Copy HTML supports
website chats such as ChatGPT, Claude and Gemini.

MCP exposes `steed_start_page_capabilities`, `steed_start_page_propose` and
`steed_start_page_status`. Agents can propose built-in section settings or a
self-contained HTML replacement for either page, without existing-data access.
The strict version-1 declaration is limited to 32 KB UTF-8. A native review shows
the exact source and lets the user choose a normal display profile. Decline is
the default. Every edit requires Apply; request IDs pin exact bytes and identical
retries return the original receipt. Changed pages/profile identities invalidate
a preview. Applied pages survive task completion; pending proposals are cancelled
or interrupted rather than replayed. Deferred reviews remain in **Task Permissions
→ Pages & Scripts**. Database schema 21 migrates existing journals in place.

Agents can also propose a **user script** (CSS and JavaScript for listed sites,
as on `steed:userscripts`) or a **panel app** (one HTML page for the App Panel,
as on `steed:apps`) with `steed_customization_capabilities`,
`steed_customization_propose` and `steed_customization_status`. The declaration
is strict version-1 JSON (`kind` `user_script` or `panel_app`, only that kind's
fields, at most 320 KB) and is checked with the same rules as those pages. A
native review shows the exact code without running it, says plainly what it
could do (a script runs with each listed page's permissions; `*` is called out
as every website), lets you choose a normal profile, and defaults to Decline.
**Add** creates a new entry there; proposals never read, replace or remove your
existing scripts or apps and grant no browser data, files or network access.
Receipts, retries, task cancellation and automatic mode work as for page
proposals.

Steed registers a host-based local URL scheme before application startup, following
the [Qt URL-scheme documentation](https://doc.qt.io/qt-6/qwebengineurlscheme.html).
Canonical engine URLs use `steed://newtab/` and `steed://welcome/` for reload and
session restore; the user-facing spelling remains `steed:newtab`/`steed:welcome`.
There is no general-purpose HTML app capability bridge in this milestone.

## Usage

### Keyboard Shortcuts

- `Ctrl+T`: New tab
- `Ctrl+W`: Close current tab; closing a window's last tab closes the window (and
  quits Steed if it was the last one, keeping its tabs for next start)
- `Alt+Left` / `Alt+Right`, or the mouse's back/forward side buttons: Back / Forward
- `Ctrl+Shift+T`: Restore the most recently closed tab for this profile
- `Ctrl+Shift+N`: Open search profile in a separate window (incognito)
- `Ctrl+Shift+0`: Switch the current window to search profile
- `Ctrl+Shift+1` … `Ctrl+Shift+9`: Switch to the Nth profile
- `Ctrl+J`: Downloads
- `Ctrl+D`: Bookmark the current page
- `Ctrl+Shift+O`: Manage bookmarks
- `Ctrl+R` or `F5`: Reload the current tab
- `Ctrl+L`: Focus and select the address bar
- `Ctrl+F`: Open find in page and select the current query
- `Enter`, `F3`, or `Ctrl+G`: Next match (`Enter` while the find input is focused)
- `Shift+Enter`, `Shift+F3`, or `Ctrl+Shift+G`: Previous match
- `Escape`: Close an open find bar
- `Ctrl++` or `Ctrl+=`: Zoom in
- `Ctrl+-`: Zoom out
- `Ctrl+0`: Reset zoom to 100%
- `Ctrl+Shift+L`: Fill the current site's saved login
- `Ctrl+,`: Settings
- `Ctrl+Q`: Close the window

### Menu Options

The menu bar has six categories, in this order:

- **General:** Passwords, Fill Login, Downloads, Ad Blocking, Website Permissions,
  Settings and Exit.
- **Profile:** Create, switch, pop out, rename or delete profiles, open private
  browsing and use Quick Switch shortcuts.
- **Tabs:** New Tab, Restore Closed Tab, Close Tab, Reload, Bookmark This Page
  and Manage Bookmarks.
  **Tabs → Tab Groups** assigns/removes the current tab's group, collapses/expands
  that group, and opens Manage Tab Groups. Right-click a tab for New Tab to the
  Right, Reload, Duplicate, Pin/Unpin, Mute, Copy Link Address, Add to Group / Remove from
  Group, Close, Close Other Tabs, Close Tabs to the Right and Reopen Closed Tab.
  Tab, group and page right-click menus use Steed's own menu style.
  Middle-click closes a tab, double-clicking empty strip space opens one, tabs
  drag to reorder, and the strip is keyboard navigable (arrow keys, Enter,
  Delete, Shift+F10). Pinned tabs show only their site icon, stay together at
  the start of the strip (a row at the top of side tabs), never join a group,
  and are saved with the session; new tabs never open inside the pinned block.
  **Windows:** each window's tabs are saved separately, and reopening Steed
  restores every window. A second window of a profile that is already open starts
  with a new tab instead of copying the session. Closing one window while another
  of the same profile stays open forgets that window's tabs (they remain in
  Reopen Closed Tab only when closed individually); quitting keeps all windows.
  Move a tab with **Move Tab to New Window** or **Move Tab to Window**, by
  dragging it onto another window's tab strip, or by dropping it outside every
  window to tear it off. Tabs move only between windows of the same profile and
  a window's last tab stays. The page itself moves, keeping its back/forward
  history, scroll position, form input and running scripts. Steed's own pages,
  tabs shared with an agent, and tabs with Developer Tools open instead reopen
  their address in the new window. A drag between windows is accepted
  only with a one-time token issued by the source strip.
- **Toolbar look:** the toolbar, menu bar and status bar share the tab strip's
  colours in light and dark mode. Buttons use Steed's own line icons, the address
  field is a rounded field with an accent focus ring, and an icon at its left
  shows whether the page is encrypted (lock), not encrypted (open lock, orange),
  a Steed page, or a search. Typing shows up to ten suggestions: first the
  action for what you typed (“Search DuckDuckGo for …”, using your search
  engine's name, or “Go to …” for an address), then up to three open tabs of
  this profile in any window (“Switch to tab” jumps to it instead of opening it
  again), up to four bookmarks, and history. Every typed word must appear in the
  title or address, and each address appears once. Suggestions are computed
  locally; nothing is sent to the search engine before you press Enter. The toolbar stays a native text field, so typing, input methods and
  undo work like any other field.
- **Command palette and tab keys:** Ctrl+Shift+P opens a palette that runs any
  menu command or jumps to any open tab of this profile, in any window, by
  typing part of its name (fuzzy matching; `>` shows only commands, `@` only
  tabs). It also finds this profile's bookmarks and history (newest first, up
  to 1,200 pages, skipping pages already open) and opens them in a new tab:
  `#` shows only history, `*` only bookmarks, and typing without a prefix ranks
  open tabs, then bookmarks, then history and commands. With nothing typed it
  lists tabs only. Ctrl+Shift+E opens it on tabs and Ctrl+Shift+Y on history
  (View → Search History…). Ctrl+Tab / Ctrl+Shift+Tab and
  Ctrl+PgDn / Ctrl+PgUp move to the next or previous tab, Ctrl+1–8 open that tab
  and Ctrl+9 the last one; tabs hidden in a collapsed group are skipped.
- **View:** Go to Address Bar, Find in Page, Find Next, Find Previous, Zoom In,
  Zoom Out and Reset Zoom. While you edit the address bar, the page changing its
  URL no longer replaces your text or its undo/redo history (Ctrl+Z, Ctrl+Shift+Z
  or Ctrl+Y); Escape restores the page's address.
  **View → Developer Tools** opens Chromium's DevTools for the current tab, like
  Chrome: F12 or Ctrl+Shift+I toggles them, Ctrl+Shift+J opens the Console,
  Ctrl+Shift+C starts the element picker, and **Inspect** in a page's right-click
  menu selects that element. Each tab keeps its own panel; dock it to the right,
  to the bottom or into a separate window (remembered, and also set in
  `steed:settings` → View). DevTools settings use a
  private, unsaved profile. A tab whose DevTools connection is held by an agent
  task cannot open DevTools until the task releases it, and a tab with DevTools
  open cannot be delegated to an agent.
- **Cookies:** Clear Browsing History, View Cookies, Clear All Cookies and Use
  Global Cookies for This Site. Clearing history affects the current profile's
  visits and address suggestions; it does not clear cookies.
- **Agents:** Task Permissions, App Connections, Agent File Library, Enable Local
  Agent Access, Agent Connection Setup, Agent Sidebar, Agent Chat Tab and Website
  Chats. Connecting an account, enabling files and approving task actions remain
  separate controls.

Existing keyboard shortcuts continue to work regardless of the menu location.

Dark mode is available in **General → Settings**.

### Website permissions

Website prompts name the exact site, requested capability and requesting tab's
profile. **Deny** remains the default. Choose a lifetime before clicking **Allow**:

- **Allow once:** approve this request without saving a choice. Access can last
  for the requesting page's lifetime; it does not end immediately after approval.
- **Allow every time:** remember this exact site and capability across regular
  profiles, including `global`. It excludes private browsing.
- **Allow every time on this profile:** remember the same permission only for
  this profile. Profile identity survives renaming; recreating a deleted profile
  does not inherit its approval. Saved lists retain the original profile label.

Private browsing offers **Allow every time in this private session** instead.
These choices remain in memory for the shared Search profile's lifetime and
disappear when Steed closes. Private browsing never inherits regular approvals.
Screen sharing still requires choosing a screen or window each time.

Use **General → Website Permissions** to forget a choice and make future requests
ask again. Forgetting does not promise to terminate an existing capture; close
the tab using your camera, microphone or screen to stop it. Website approvals
never authorize agents: task pages continue to deny engine permissions.

Regular choices live in owner-only `~/.custom_browser/site_permissions.json`.
On supported Qt versions Steed uses `AskEveryTime` so native persisted choices
cannot bypass these controls; older native approvals will prompt again. Read or
write failures deny access rather than granting an unsaved remembered permission.

### User scripts and styles (`steed:userscripts`)

**General → User Scripts & Styles…** or `steed:userscripts` keeps your own CSS
and JavaScript for chosen sites in this profile (`<profile>/user_scripts.json`,
up to 50 entries). Each has a name, its sites (one per line or comma separated,
such as `example.org`; a site includes its subdomains, `*.example.org` means
the same, and `*` means every site), CSS, JavaScript, when the script runs (as
the page starts, when it is ready, or after it has loaded) and an on/off
switch. They also run in frames from the listed sites.

Styles are added from Steed's own script world as soon as the document exists,
so pages do not flash unstyled. Scripts run in the page's world, like
userscripts, so they can use the site's own code; an error is reported in the
page's console (`Steed user script <name>`) without stopping the page. Scripts
have the page's permissions and none of Steed's data. Changes apply to pages
loaded or reloaded afterwards. Incognito windows run none.

### The new tab page

By default a new tab shows just **Steed** and an empty search box over a photo
that changes every six hours: free Unsplash photos from Lorem Picsum, with the
photographer credited at the bottom left. Steed keeps the current photo in
`~/.custom_browser/newtab/` and fetches the next in the background, so a new
tab never waits; until the first arrives, or offline, a soft gradient shows.
Picsum is only contacted while a profile uses the photo background. The gear
at the top right opens a panel on the page itself, and ☰ opens Steed's menus.
In the panel you can choose:

- **Background**: a photo, a plain colour (swatches, or the theme's gradient),
  or your own image.
- **Photos from**: *Online* (Unsplash photos via Lorem Picsum), *Your photos*
  (**Add photos…** copies them, resized, into `~/.custom_browser/newtab/mine/`),
  *A folder* (**Choose folder…**; read in place, so new photos in it join in)
  or *Favourites* (**♡ Save this photo** keeps the one shown). Your own photos
  are shown in a shuffled round, each once before any repeat. **Not this one**
  skips an online photo for good.
- **Style**: in colour or black & white.
- **New photo**: for every new tab, every hour, every 6 hours or every day,
  or **New photo now**.
- **Browser colours**: *Match the background* (the default) tints the tab
  strip, toolbar and highlights with the photo's (or your image's) main
  colour, in light and dark mode; *Theme colours* keeps Steed's usual ones.
  Custom colours set in Style always win.
- **Layout**: Simple (above) or Classic (the sections: search, bookmarks,
  recently closed, history, news).

- **Settings button**: the gear as its own button, or *In the ☰ menu*, where
  Customize New Tab is the first row of the menu and opens the same panel in
  place (the gear stays when the New Tab ☰ menu is turned off in Style).

**More options…** (also General → Customize New Tab…) opens the full dialog
with the classic sections, a feed, a custom colour and your own HTML.

### Split view and sites in a profile

**Show Beside Current Tab** (tab menu) puts a tab beside the current one;
clicking into either side makes it the current tab, and **View → Close Split
View** (or closing either tab) ends it. The tab beside stays running whatever
the memory mode. The site menu's **Always Open in Profile** sends a site (and
its subdomains) to a profile: following a link to it or typing its address
opens it in that profile's window instead (a new or blank tab that was opened
only for it closes). Incognito windows never send pages elsewhere. Tab search
(Ctrl+K, or `@` for tabs) also lists recently closed tabs; choosing one
reopens it where it was.

### Privacy on every page

Settings → General:

- **Remove tracking from addresses** (on): before a page loads, parameters that
  only follow you across sites (utm_…, fbclid, gclid and others, as Copy Clean
  Link removes) are dropped from its address.
- **HTTPS first** (on): http:// pages load over HTTPS. When a site has no
  HTTPS, Steed loads it unencrypted after all (the status bar says so) and
  remembers the site until it quits. Local addresses, IP addresses and
  addresses with a port are left alone.
- **Resist fingerprinting** (off): pages see common hardware values (4 cores,
  8 GB, no plugins), a generic graphics card name, and canvas images with tiny
  changes (stable for a site until Steed quits), so they cannot recognize this
  computer as easily. Some sites may misbehave; it applies to pages loaded
  afterwards.

The site menu's **Forget this site when its tabs close** deletes the site's
cookies (it and its subdomains) when its last tab in the profile closes or
moves to another site, along with everything else it stores.

### Clearing browsing data

**General → Clear Browsing Data…** (Ctrl+Shift+Delete) clears, for a time range
(last hour, 24 hours, 7 days, 4 weeks or all time) and the current profile:
browsing history, the download list (not the files), cookies and site data,
cached images and files, recently closed tabs and the crash log. Cookies and
site data for a range are those of the sites visited in it; for all time, of
every site in history or with cookies. Site data means everything a site
stores: cookies, local and session storage, IndexedDB, cache storage, service
workers and file systems. The cache can only be cleared whole. History and
downloads from before this version have no times, so only "All time" clears
them. **Also clear these whenever this profile closes** clears the chosen
kinds entirely each time the profile's last window closes (Steed waits up to
five seconds for site data).

The site menu's **Clear Data for This Site…** shows how much a site stores
and clears its cookies and data (it and www., and the addresses you visited
it at). **Forget this site when its tabs close** now clears its stored data
too, not only its cookies.

Steed asks Chromium for this through a hidden Developer Tools page (Storage
clearDataForOrigin), without visiting the sites. It never sends an address
Chromium could read as something wider: clearing the malformed origin
`https://www.127.0.0.1` was measured to clear another site's storage too.

### Saving pages

**View → Save Page As…** (Ctrl+S) saves the page as one file (`.mhtml`, with
its images and styles, opened again by Steed or Chrome), as HTML with a folder
of its files, or as PDF. Screenshots are under View → Take Screenshot.

### When a tab crashes

When a page's process ends unexpectedly (a crash, or the system stopping it,
for example when memory runs out), its tab shows **This tab crashed** with
**Reload** and **Crash Log…**. **General → Crash Log…** lists the last 200
crashes: when, which site (only the host; "incognito" for private windows) and
why, with no page content; **Clear** empties it.

### Browser colours

**Style → Browser colours** lists every colour of the browser around pages:
toolbar and current tab, tab strip, text and icons, other tabs' text, address
field, hover, pressed, lines and borders, accent, menus and the tab hover
card. Each shows its automatic value (from the theme, or from the New Tab
photo with *Match the background* on) and can be fixed with a colour picker;
**Automatic** under one returns it. Colours you leave alone keep following
the theme or photo, worked out from your fixed toolbar and accent. **Keep
these colours** fixes them all as they are now, so a new photo no longer
changes the browser; **All automatic** undoes that. The New Tab page uses
your fixed toolbar and accent too. Apply style saves, per profile (or for
all profiles). Page colours (backgrounds, cards and text inside Steed's own
pages) are the separate *Theme colors* above them. By default Steed's pages
(New Tab, Welcome, Settings, Style and the other built-in pages) take their
colours from the photo too; turn off *Steed pages take their colours from the
New Tab photo* in Style to keep the plain theme. Theme colors you pick and
custom CSS still win.

### Menus

The ☰ menus (toolbar and New Tab) and their sections use the toolbar's
colours, including colours taken from the new tab photo, and are compact:
about Chrome's menu width, as tall as their items (long sections scroll).
A section opened from the New Tab ☰ menu appears beside the menu (on its
left when there is no room on the right), never over it, and a sub-menu
(such as Extensions ›) opens as its own panel beside the section. Hovering
only opens sub-menus; everything else (Settings, Downloads, …) needs a click.
Rows have no boxes, and the thin scrollbar sits on the left. With Liquid
Glass both kinds of menu are rounded and see-through.

### Scroll bars

Steed's pages, menus and panels share one scroll bar: thin, rounded, in the
page's text colour, inset evenly from both sides, without arrows. Websites
get thin overlay scroll bars that take no space and show while you scroll or
point at them (sites that style their own keep theirs). To have websites use
classic scroll bars, set `"thin_scrollbars": false` in
`~/.custom_browser/config.json` and restart Steed.

### Liquid glass

**View → Liquid Glass** (also the new tab's gear panel under *Look*, or the
Style page's Browser bars section) switches the profile to a glass look: tabs
become frosted pills lit by the accent colour (the current one, along the top,
grows out of the toolbar with curved feet), the toolbar and address field
get a glossy, lit-from-above finish, hover cards turn translucent, and the new
tab's search box, buttons and panel become blurred glass. With a New Tab
photo or image (and *Match the background* on), the picture becomes the
background of the whole window: the menu bar, tab strip and toolbar show its
top as frosted glass and the New Tab page shows the rest, lined up (redone
when the window is resized; kept in `~/.custom_browser/frost/`). The current
tab and the toolbar share one glass tint over it; its outline runs straight
down its sides into a fine line along the bottom of the tab strip. A toolbar or
tab strip colour fixed in Browser colours turns this off. Glass menu panels
paint the browser behind them (frosted inside their smooth rounded shape),
so they need no see-through windows from the desktop; the toolbar's native
menus are glossy, square panels. The new tab's search box, buttons and panel are
blurred glass that bends the photo
behind it slightly, with a slow sheen. With *Match the background* on, the
glass takes the photo's colour too. Motion is reduced when the system asks
for it. Off by default; existing style settings are kept when it is added.

### Bars and hover cards

The top menu bar and the bottom status bar are hidden by default; the
toolbar's ☰ button and the new tab's ☰ open the same menus, and View → Show
Top Menu Bar (Ctrl+Shift+M) or Show Bottom Status Bar brings them back. With
the status bar hidden, its messages (not loading progress) show for a few
seconds at the bottom left of the window.

Hovering a tab shows one card: its title and site, its state (frozen,
unloaded, kept loaded, pinned) and, with previews on, a snapshot: live for
the tab you are on, and for others as they were last shown (taken when you
leave a tab and when a page finishes loading while shown). With Liquid Glass
the card is frosted over what is behind it. When a new photo changes the
browser's colours, they fade into the new ones instead of jumping. Cards are all one width, as in Chrome; Settings → **Same width for
every tab card** off makes each as wide as its title.

### Search keywords and completing addresses

Type a keyword, a space and your search in the address bar: `w steed browser`
searches Wikipedia, and the suggestion list leads with that search. Built in:
`w` Wikipedia, `gh` GitHub, `yt` YouTube, `r` Reddit, `so` Stack Overflow,
`maps` OpenStreetMap, `ddg` DuckDuckGo, `g` Google, `pypi` PyPI. **General →
Search Keywords…** adds your own (a keyword of 1–12 letters or digits, a name,
and an `https://` address with `{q}` where the search text goes; up to 50),
which can also replace a built-in keyword.

Typing the start of a site you visit fills in the rest of its name, selected,
so Enter goes there; keep typing to override it or press Backspace or Delete to
drop it. Sites are ranked by how many of your history's pages they have, then
by how recently. It completes site names only (not paths, and not text with a
space). **Complete addresses as you type** in Settings turns it off.

### Importing from another browser

**General → Import from Another Browser…** lists the Chrome, Chromium, Brave,
Edge, Vivaldi and Firefox profiles on this computer (including Snap and Flatpak
installs, by each browser's own profile names) and imports into the current
profile: **bookmarks** (top folders become labels; existing addresses are
skipped; the 2,000-bookmark limit applies), **history** (the latest 1,000 web
pages) and, from Firefox, **open tabs**, which become a saved group "Open tabs
from Firefox" (Tabs → Saved Groups). Chromium keeps open tabs in an
undocumented binary format, so they are not imported. Steed reads copies of the
other browser's files in a temporary folder, so that browser may stay open and
its files never change. Not available in incognito.

### Extensions

Steed runs Chrome extensions (Manifest V3). The puzzle button in the toolbar
(also **General → Extensions**) lists the switched-on extensions with their
icons; choosing one opens its popup under the button (Escape or clicking
elsewhere closes it). It also offers **Manage Extensions…**, **Browse Chrome
Web Store** and **Add from Chrome Web Store Link…**.

**Finding and adding.** Browse the Chrome Web Store in a tab: on an
extension's page a bar offers **Add to Steed** (the store's own "Add to
Chrome" button needs Chrome). Steed downloads the extension from the address
Chrome itself uses, then shows what it is and asks: its name, version and icon,
what it can do in plain words ("Read and change everything on all websites",
"See the addresses and titles of your tabs"…) and which Chrome features it
uses that Steed's engine lacks. You can also paste a store address, or add an
unpacked folder, a `.zip` or a `.crx` file in Manage Extensions. Manifest V2
extensions are refused (current Chrome no longer runs them either). The store's
signature on a `.crx` is not checked.

**What works.** Qt WebEngine 6.11 runs content scripts, popups and background
service workers, but gives extensions only part of Chrome's APIs (`alarms`,
`declarativeNetRequest`, `i18n`, `management`, `offscreen`, `runtime`,
`storage`, a nearly empty `tabs`, `webRequest`). Steed adds its own stand-ins
(`extension_shim.js`), run first in each extension's background, pages and
content scripts:

- **tabs**: each content script keeps a connection to its background, which
  keeps the list of tabs it runs in, so `tabs.query`, `tabs.get`,
  `tabs.sendMessage` (to the content script's `runtime.onMessage`),
  `tabs.update` (to navigate) and the tab events work for those tabs. Messages
  and ports from content scripts carry `sender.tab` and `sender.frameId`,
  which Qt leaves out. Popups ask the background. Tab ids last one page load.
- **storage.sync**: Qt's fails on every call; Steed keeps it in local storage
  (it does not travel between computers).
- **permissions**: answered from the extension's manifest (no prompts).
- **notifications**: shown through the web Notification API.
- **contextMenus**: items appear in the page's right-click menu (in a
  submenu named after the extension when it has several), for the contexts they
  ask for (page, selection with `%s`, link, image, video, audio, editable) and
  their URL patterns; checkboxes and radio items keep their state; clicks reach
  `contextMenus.onClicked` with the tab. Items are kept across restarts, as
  Chrome keeps them.
- **scripting**: `executeScript` (a function, also async, or files),
  `insertCSS`/`removeCSS` and `registerContentScripts` run in the tab, in a
  script world of the extension's own (or the page's, for `world: "MAIN"`).
  These worlds have no `chrome.*` APIs, unlike Chrome's, and only the top frame
  is reached.
- **action**: badge text, colour and title (for every tab or one) show on the
  extension's pinned button; an extension without a popup gets
  `action.onClicked` when its button or menu entry is clicked.
- **commands**: an extension's suggested shortcuts work in Steed's windows
  (unless Steed already uses the keys), reaching `commands.onCommand`;
  `_execute_action` opens its popup. Pinned buttons also have Alt+Shift+1–9.
- **userScripts, sidePanel, webNavigation, windows**: calls succeed without
  effect, so an extension using them still starts.

Scripts Steed injects for an extension (`executeScript` and registered
content scripts, outside `world: "MAIN"`) get a small `chrome` object:
`runtime.id`, `getURL`, `getManifest`, `runtime.sendMessage` to the extension's
background (which sees `sender.tab`, `sender.url` and `frameId` as in Chrome)
and `chrome.storage` (local, sync, session); `runtime.onMessage` receives
`tabs.sendMessage` (along with the extension's content script there; the first
answer from a listener wins). They reach Steed through the page's console with
a token only that script world holds, so the page cannot pose as the
extension.

- **tabs.create, tabs.remove, tabs.update (active), windows.create**: from the
  background they open, close or select tabs and windows in Steed, but only
  web pages and the extension's own pages.
- **runtime.openOptionsPage** and Manage Extensions' **Options** open the
  extension's options page in a tab.
- **webNavigation**: before-navigate, committed, DOM-content-loaded,
  completed, history-state and error events for the top frame of Steed's tabs,
  for extensions that ask for the permission.
- Errors: uncaught errors and `console.error` in the background, and errors in
  its pages, are kept (the latest 50); Manage Extensions shows them under
  **Errors…**.

These reach Steed through a hidden page of each extension
(`steed-bridge.html`, added when Steed installs it), which talks to the
extension's background and to Steed. Steed matches the stand-ins' tab ids to
its own tabs by the document's start time (`performance.timeOrigin`, the same
in every script world of a page), so two tabs showing the same page stay
apart; before a page has finished loading it falls back to address and
title.

Tested with real store extensions: Dark Reader darkens pages; uBlock Origin
Lite starts and its popup works, but cannot block, because Qt WebEngine does
not apply `declarativeNetRequest` rules (Steed's own ad blocking does that
job). A test checks the engine's API list, so it is updated when Qt changes.

**Managing.** Manage Extensions shows each extension's icon, version, source
and permissions; **Switch Off/On** and **Remove** manage them. **Pin to
Toolbar** (or the puzzle menu's **Pinned Buttons**) gives an extension with a
popup its own toolbar button. **Site Access…** limits an extension to the sites
you list (and their subdomains), or lets it use every site it asks for; Steed
installs it again with its site access and content scripts narrowed, keeping
its settings and data. Links a popup opens become tabs.

**Updates.** Steed checks the Chrome Web Store for newer versions once a day
(and with **Check for Updates**). An update asking for nothing more than the
installed version installs by itself; one asking for more waits, shown in
Manage Extensions, until you choose **Update…** and agree. Updates keep the
extension's settings and data.

**How Steed installs.** Each extension keeps its own files in the profile's
`extension-sources/<id>`, and Steed installs a copy built from them with a
fixed id (store extensions keep their store id, from the key in the `.crx`;
others get a key Steed makes), its stand-ins added and any site limit applied.
Qt WebEngine cannot replace an installed extension, so updates and site
changes remove and install again; the fixed id keeps its storage. Each profile has
its own extensions, kept in its storage folder; Qt loads them switched off at
every start, and Steed switches back on the ones you left on
(`extensions.json`). Incognito windows have none, and no puzzle button.

**Sync.** Extensions from the Chrome Web Store sync with your other computers
(General → Sync…): each computer downloads and installs the ones another
added, removes the ones another removed and follows switching on and off,
without asking again. A download that fails is tried again at the next sync or
start. Extensions added from a folder or file stay on their computer.

Earlier Steed versions could not enable extensions: Qt WebEngine 6.10/6.11
crashes when a profile's storage folder is set after the profile exists (the
profile's preferences are replaced under Chromium's extension code). Steed now
creates profiles with `QWebEngineProfileBuilder`, folders set first; PyQt 6.11
does not expose the builder's constructor, so `webengine.py` calls Qt's own.

### Sync between computers

**General → Sync…** keeps your computers in step through a folder you choose
that reaches all of them: one kept in step by Syncthing, Nextcloud or Dropbox,
or a USB stick. Nothing goes to a Steed server. It syncs **bookmarks**, **saved
groups**, the **reading list** (with its offline articles) and your **search
keywords**, between profiles with the same name on each computer (Global with
Global); incognito never syncs. Steed syncs when it starts, every five minutes,
soon after bookmarks or passwords change, and with **Sync Now**.

Each computer writes only its own file, `steed-sync/device-<id>.json`, and
reads the others'. Every item carries the time it last changed, and deletions
leave a dated marker, so the newest change to each item wins and deletions
reach every computer. On a computer's first sync, items another computer
already has count as older, so an old copy never undoes newer changes. Deletion
markers are kept for 90 days; a computer away longer than that can bring a
deleted item back. Two computers changing the same item between syncs keep the
later change.

**Passwords** sync once you set a **sync passphrase** (the same on every
computer; the first sets it and later ones are checked against it). Each
profile's logins are sealed in the folder with a key stretched from it
(Argon2id, then XSalsa20-Poly1305, as the vault uses), so the folder holds no
readable password or site; the key is kept in the system keyring, or for the
session without one. A profile's passwords sync while its vault is unlocked.
Logins match by site and username. **Stop Syncing Passwords** forgets the key
on this computer.

### Reading list

**View → Save to Reading List** (or the tab menu) keeps the page to read later.
When reader view finds an article, its text is kept too (not its images), so
**View → Reading List…** can open it with **Read Offline** in reader view even
without a connection or after the page changes; the list marks those
"(offline)". **Open** loads the live page and marks it read; **Mark
Read/Unread** and **Remove** tidy the list. Unread pages come first, newest
first. Saving a page again refreshes it and marks it unread. Each profile keeps
its own list (up to 500 pages) in `reading_list.json`; incognito keeps none.

In reader view (also when reading offline), select text in a paragraph and
press **Highlight**; click a highlight to add or change its note or to remove
it. Highlights are kept per article in `reader_notes.json` (incognito: while
reading only). **Export as E-book…** in the Reading List puts every article
kept for offline reading into one EPUB file, a chapter each.

### Screenshots

**View → Take Screenshot** saves (as PNG, asking where; Ctrl+Shift+S saves the
full page) or copies either the **visible area** or the **full page**. The full
page is captured by Chromium itself through a hidden Developer Tools page, so
the page is neither resized nor scrolled; pages longer than 16,384 pixels are
cut there (the status bar says so). Close Developer Tools for the tab first.
Steed pages and other non-web tabs capture their visible area.

**Edit Full Page… / Edit Visible Area…** open the screenshot in an editor first:
drag to **Crop**, **Highlight** (a yellow, see-through box) or **Hide** (a solid
black box, for names or numbers you do not want to share); **Undo** steps back,
then **Copy** or **Save…**.

### Translating pages

**View → Translate Page** translates the current page; choosing it again shows
the original. With a LibreTranslate-compatible service set in **Settings →
Translation** (for example one you run yourself), Steed collects only the
page's visible text in its own script world (not code, scripts or form fields),
sends that text in batches of up to 50 pieces or 5,000 characters, without
cookies and without the page's address, and writes the translations back in
place. **Translate into** defaults to your system language. With no service
set, Steed offers to open the page in Google Translate's page proxy, which
sends Google the page's address (Google loads it itself, without your
sign-ins). API keys are not supported: they would have to be stored like
passwords.

**A translation service on this computer (optional).** `setup_translation.py`
installs LibreTranslate (open-source, offline translation) in its own Python
environment under `~/src/steed-libretranslate`, with CPU-only packages, and
points Steed's setting at it:

```bash
.venv/bin/python setup_translation.py install --languages en,de,fr
.venv/bin/python setup_translation.py start     # http://127.0.0.1:5000, this computer only
.venv/bin/python setup_translation.py status
.venv/bin/python setup_translation.py stop
.venv/bin/python setup_translation.py uninstall # removes it and clears the setting
```

Each language's model downloads the first time it starts (about 100 MB each);
the packages themselves are a few gigabytes. Start it again after a restart.
The script's steps are tested; a full install has not yet been run on the
development machine.

### Tab quick actions

The tab menu also has **Copy Clean Link** (the address without tracking
parameters such as `utm_…`, `fbclid`, `gclid`, `mc_eid`; other parameters keep
their order), **Duplicate with History** (a copy whose Back and Forward lead
where the original's do) and **Send Tab to Profile ▸** (opens the page in
another profile's window, or a new one, or an incognito window, with that
profile's sign-ins, and closes it here unless it is the window's last tab).

### Media controls and Mini Player

While a tab plays sound, a **⏸ title** button appears in the address bar (a
`media` toolbar component; **▶** once paused). Its menu offers **Pause/Play**,
**Mute Tab**, **Go to Tab** and **Open Mini Player**; **View → Play or Pause
Media** and **View → Mini Player** do the same. The title comes from the site's
Media Session information when it gives one, otherwise the tab title.

Qt WebEngine turns off the web's picture-in-picture, so the **Mini Player**
moves the playing tab into a small window that stays on top and fills it with
the video (the page's largest or playing video, enlarged with Steed's own
styles); the main window shows another tab meanwhile. Selecting the tab in the
strip, closing the Mini Player or closing the tab brings it back.

Steed also registers as a desktop media player (MPRIS, as
`org.mpris.MediaPlayer2.steed.instance<pid>`), so keyboard media keys and the
desktop's sound menu can play and pause, and show what is playing. It controls
the tab that played sound most recently.

### Reader view

**View → Reader View** or **F9** shows the current page's article as plain,
calm text over the tab: site name, title, author, an estimated reading time,
then headings, paragraphs, lists, quotes, code and captions. **A− / A+** change
the text size (14–30 px) and **Light / Sepia / Dark** the colours; both are
remembered. **F9**, **Esc** or **Exit reader** returns to the page, as does
following a link in the tab.

The article is found by a script in Steed's own script world (the page cannot
see or change it): the element with the most paragraph text wins, with menus,
sidebars, related links, comments and hidden text left out. Only plain text
comes back; the reader page inserts it as text under strict content rules with
no network access, so nothing from the site runs or loads there.

Images the article shows at a useful size (up to 30) are downloaded by Steed
itself, without cookies, then given to the reader page as inline data: at most
2 MB each and 12 MB per article, and only PNG, JPEG, GIF, WebP or AVIF as
recognised from the file's own bytes (never SVG). **Images** in the reader's
toolbar turns them off and on; the choice is remembered. Pages with less than about 400 characters of article text say
"Reader view found no article on this page".

### Site settings

Click the lock (or open-lock) icon at the start of the address bar for the
current site's menu:

- **Allow JavaScript**, **Allow autoplay with sound**, **Allow pop-ups and new
  windows** — on by default. Turning one off reloads the page so it applies.
  With autoplay off, media waits for a click; with pop-ups off, Chromium's
  pop-up blocker stops windows a page opens without a click (links you click
  still open).
- **Reset zoom** — zoom (Ctrl+= / Ctrl+- / Ctrl+0) is remembered for each site
  and applies to every tab on it.
- **Allow enlarging small text** — when a page's typical text (the median size
  of its paragraphs, list items and table cells) is under 13 px and the site
  has no zoom of its own, Steed zooms in, up to 150%, so text is about 15 px,
  and says so in the status bar. Ctrl+0 on such a page turns it off for the
  site, as does this switch; **Enlarge small text** in Settings turns it off
  everywhere.
- **Clear settings for this site** and **Website permissions…** (camera,
  notifications and the like stay on `steed:permissions`).

`steed:permissions` (Website permissions) also lists every site with its own
settings in the current profile, with **Reset Selected Site**.

Settings belong to a host (`www.example.org` and `example.org` are separate)
and to a profile, in `<profile>/site_settings.json`; only values you changed
are stored. Incognito keeps them in memory until its session ends. Internal
`steed:` pages and local files have no site settings.

### Find in page and zoom

Press **Ctrl+F**, type a query, and use Enter or the Next/Previous buttons to
move through matches. The bar shows the current match and total count, or
**No matches**. Enable **Match case** for an exact-case search. Escape closes the
bar and clears page highlighting. Switching tabs searches the active document;
switching profiles clears the query. Searches refresh after page navigation.

Zoom applies to the active tab, from 25% to 500%. Use the View menu or the
shortcuts above. The percentage beside the address bar resets zoom when clicked.
New tabs start at 100%; zoom levels are not saved across restart.

### Address Suggestions

Type part of a visited URL or page title in the address bar, choose a suggestion
with the arrow keys, and press Enter. Clicking a suggestion also opens it.
Successful HTTP/HTTPS visits are stored newest first, with up to 1,000 unique
URLs per profile. Ordinary profile history survives restarts and is shared by
windows using that profile. Incognito suggestions stay in memory and do not
include ordinary profile history. Local files and URLs containing embedded
usernames/passwords are excluded.

**Search suggestions** (Settings → General, on by default): under "Search … for"
the bar shows up to four suggestions from your search engine (DuckDuckGo,
Google, Bing or Ecosia; other engines get none). Steed sends what you type to
that engine 150 ms after you pause, without cookies or profile identity, and
never sends text that looks like a web address. Incognito windows never send
anything. Answers are cached for repeated text in the session.

Use **Cookies → Clear Browsing History...** to clear the current profile's saved
visits and suggestions. Subsequent visits start a new history.

### Passwords

Steed has a built-in password manager. Each regular profile keeps its own logins
in `passwords.vault` (in the profile folder), encrypted with XSalsa20-Poly1305
under a random key stored in your system keyring (GNOME Keyring or KWallet,
through the Secret Service). Without a keyring Steed asks you to create a
master password instead (at least 8 characters, typed twice; it cannot be
recovered), stretched with Argon2id; that vault then asks for it once per
session. A vault unused for 30 minutes is locked and its logins dropped from
memory. Incognito never saves or fills passwords.

- **Saving:** sign in as usual. When you submit the form yourself (clicks and
  key presses made by page scripts do not count) and the page navigates or an
  app-style form disappears, a bar above the page offers **Save** (or
  **Update password** when the site and username are saved with another
  password), **Never for this site** and **Not now**. Typing without submitting
  offers nothing.
- **Filling:** when a page loads, the most recently used login for that exact
  site (scheme, host and port) is filled into its empty, visible sign-in
  fields. Embedded sign-in frames and one-time codes are filled only when you
  ask — `Ctrl+Shift+L`, or the key button beside the address bar — so a page
  that embeds another site cannot get it filled silently; frames always get
  their own site's logins. Forms that would send to another site are never
  filled. The key button lists the site's logins (and embedded frames') to fill
  or copy, copies a current one-time code, and **Generate strong password**
  fills a sign-up or change-password form and copies the password. Copied
  passwords and codes are marked as secret for clipboard managers and cleared
  from the clipboard after 30 seconds.
- **steed:passwords** (General → Passwords…): search; show a password (hidden
  again after 30 seconds); copy username, password or code; edit, add and delete
  logins; add a one-time-code secret (`otpauth://` link or base32); the
  never-save list; and the two settings above (offer to save, fill
  automatically).
- **Import:** **Import CSV…** reads exports from KeePassXC (Database → Export →
  CSV), Chrome, Firefox and Bitwarden; matching site+username entries are
  updated. **Export…** writes a Chrome-style CSV in plain text after a warning.

Logins are offered only on HTTPS sites and local development servers. Page
scripts cannot see the capture or fill code, which runs in an isolated world;
the site a login belongs to always comes from the engine, never from the page.

**Security keys (WebAuthn):** sites that sign in or register with a USB or NFC
security key (FIDO2/U2F, such as a YubiKey) work: Steed asks you to choose an
account when the key holds several, to enter (or set, twice) the key's PIN, and
to touch the key, and explains failures with **Try again**. Passkeys stored on
this computer or synced from a phone are not available in Qt WebEngine on
Linux, so sites offering only those need another sign-in method.

Steed versions before the built-in vault left KeePassXC connection files
(`password_manager.json`) in profile folders. On first start Steed offers once
to remove them; your KeePassXC database is never touched.

Desktop password managers' auto-type (for example
[KeePassXC Auto-Type](https://keepassxc.org/docs/KeePassXC_UserGuide#_auto_type))
also works: window titles include the active hostname,
so a window association such as `Custom Browser - * - accounts.google.com`
can match the site. General extension support remains pending. Loading the
unmodified official Bitwarden 2026.9.2 package crashed the tested Qt WebEngine
6.11 runtime with both install and unpacked-load APIs, so it is not exposed as
a supported password-manager option.

### Passkeys

Sites that offer passkeys can keep them in Steed: creating one asks **Create
Passkey**, **Use a Security Key** or **Cancel**, and signing in lists the
accounts Steed has for that site. Passkeys are P-256 keys kept in the
profile's encrypted password vault (the same seal as passwords), answered in
the formats sites check (attestation "none", ES256), and only for HTTPS sites
and localhost; Steed takes the site from the tab, never from the page. A page
script stands in for `navigator.credentials` and passes requests to Steed with
a token the page cannot read; iframes keep the engine's own WebAuthn (security
keys). **Use a Security Key** passes a request to the engine. steed:passwords
lists passkeys (Delete removes one; the site then needs another way in), and
**Keep passkeys in Steed** switches them off. When a sign-in page asks quietly
for a passkey (WebAuthn "conditional" requests, which Chrome answers from the
username field's autofill), Steed shows a bar, **Sign in to example.com with a
saved passkey?**; **Choose…** lists your accounts, and the bar goes when you
switch tabs, the page moves on or the site cancels. Passkeys sync with your
other computers along with passwords (General → Sync…, with the sync
passphrase), sealed in the sync folder. Not possible here: using a passkey from
a phone by QR code (that needs Bluetooth and Google's relay).

### Password Checkup

steed:passwords → **Check** marks logins whose password is **reused** or
**weak** (short, common, one repeated character, a run of digits or letters),
on this computer. **Check for breaches** also asks Have I Been Pwned's range
search: only the first 5 characters of each password's SHA-1 hash are sent
(padded, so the answer's size gives nothing away), and the matching happens
here. Logins found in breaches show how often; change those passwords. Results
last until the page closes.

## Profiles and stored data

- Ordinary profiles have separate cookies, tabs, and web storage; application preferences are shared
- Profiles are stored in `~/.custom_browser/profiles/{profile_name}/`
- The `global` profile is stored in `~/.custom_browser/global_profile/`; its cookies form a one-way shared baseline while separate profile logins remain local
- Windows share application-owned engine profiles; a second browser process cannot open the same storage directory
- Search profile acts as incognito mode (no persistence)
- Easy profile switching without losing current session

### Creating and Switching Profiles

1. Go to **Profile → New Profile** to open the HTML settings/profile workspace.
2. Enter a name under **A new space** and choose **Create & switch**.
3. Use **Switch** or **New window** beside a profile, or the existing quick-switch
   shortcuts. Reopening a profile menu action reuses this window's workspace.

Rename and archive show a review with the exact profile and proposed change.
Archiving moves its complete directory to `deleted_profiles` for recovery.
Built-in profiles cannot be renamed or archived; profiles in use by windows or
agents must be released first. To rename/archive the saved startup profile,
choose another startup profile and save first. These actions happen immediately;
**Cancel** in settings only discards unsaved preferences, not profile actions.

### Search Profile (Incognito)

The search profile is a special profile that:

- Doesn't save cookies
- Doesn't save browsing history
- Doesn't save tabs
- Keeps cookies, site storage and cache in memory only while an incognito window
  is open. Once no window uses it — you switch the last one away or close it —
  Steed discards that private profile, and the next incognito window starts
  empty, as when closing the last incognito window in other browsers. Downloads
  still running in incognito delay this until they finish. Sites can still
  recognize you by your network address.

Access it via **Profile → Pop Out Search Profile (Incognito)** or `Ctrl+Shift+N`.

**Profile → Quick Switch** lists existing profiles immediately at startup.
The numbered shortcuts work before the menu is opened. Ordinary profiles are
listed alphabetically, including `default` and `global`.

## Tabs and groups

**Tab previews.** Hovering a background tab for half a second shows a small
snapshot of it (taken when you last left it; about 15 KB in memory, never
saved), with its title and site, in both the HTML and native tab strips. Tabs
not shown since Steed started show only their title and site. **Tab previews
on hover** in Settings turns them off.

**Session history.** Every 10 minutes Steed records this profile's open windows
and tabs (only when they changed; also once when Steed quits), keeping the last
48, and records each window you close while another of the profile stays open,
keeping the last 20. **Tabs → Session History…** lists them with a preview of
their tabs; **Open** brings an entry back in new windows (tabs load when
selected, and tab groups the entry used return with their colours), **Delete**
removes it. **Tabs → Reopen Closed Window** brings back the last closed window.
Reopened closed windows leave the list; sessions stay. Stored in
`<profile>/session_history.json`; incognito keeps none.

Right-click a group's label for **Colour** (blue, red, yellow, green, teal,
purple, pink, orange, grey, or Automatic, which takes the next unused colour;
the choice is kept with the group) and **Save and Close Group**, which keeps
the group's name, colour and web pages in `<profile>/saved_groups.json` (up to
50 groups of 200 pages, newest first) and closes its tabs; if they were the
window's only tabs, a new tab keeps the window open. **Tabs → Saved Groups**
lists them: **Open** reopens one as a group again (named "Name 2" if the name
is taken; its tabs load when first selected, and it leaves the list), and
**Delete** removes it. **Tabs → Save All Tabs as Group…** saves the window's web
pages as a group the same way without closing them. Saved groups also appear in
the command palette (with bookmarks under `*`); choosing one reopens it.
Incognito windows do not save groups.

- Multiple tabs with tab bar interface
- Tabs are saved before closing
- Tabs are restored when reopening a profile. Only the tab you see loads at
  startup; each other one loads when you first select it (**Restored tabs** on
  `steed:memory`; Performance mode loads all at once)
- Tab state includes URL, title, scroll position (restored after the page
  loads, with a second try half a second later for pages that build their
  content late) and stable tab/group identities. Typed form text and the
  back/forward list are not written to disk
- `Ctrl+Shift+T` restores recently closed tabs for the current profile (up to 50, not saved across restart)

## Cookies and global sharing

- View all cookies for current profile
- Delete selected cookies, selected sites, or all cookies through the viewer/menu
- Filter cookies by domain or name
- Search profile has no cookie persistence

Existing profiles are migrated conservatively: pre-existing cookies remain local rather than being overwritten by the global profile. **Cookies → Use Global Cookies for This Site** explicitly resets the current site's baseline, after confirmation. Local changes/deletions prevent automatic reinheritance. Cookie sharing does not copy local storage or guarantee a complete signed-in session.

## Downloads and ad blocking

Open **General → Downloads** (`Ctrl+J`) for progress, cancellation, supported pause/resume, file opening, and history. Incognito downloads are excluded from disk history.

While anything downloads, a **↓ 42%** button in the address bar shows overall
progress (or **↓ …** while sizes are unknown) and opens Downloads; it reads
**↓ Done** for 30 seconds after the last one finishes, and the status bar names
the file. The button is a toolbar component (`downloads`) that a layout can
move or hide.

Chromium already retries a dropped connection a few times on its own. A
download that still fails, is cancelled, or was cut off when Steed closed can be
tried again with **Retry**: Steed downloads it again from its original address
into the same file, without asking where to save (Qt cannot continue a failed
download where it stopped, so it starts over). Retry starts from an open web tab
of that profile, or from a hidden blank page of it.

Local pages (`steed:` pages such as New Tab, and `file://` pages) may not reach
the web, so a local HTML file cannot read online content. The engine counts a
download a local page starts as reaching the web and refuses it, which made a
download link in a local file do nothing and Save Link As fail on New Tab. Steed
now opens a link clicked on a local page the way a typed address opens (ordinary
links behave as before; downloads work), and starts Save Link/Image/Media As
from those pages itself. Local pages' own scripts still cannot fetch from the
web. **Show Folder** selects the file in the
file manager (the `org.freedesktop.FileManager1` desktop interface used by
Nautilus, Nemo, Dolphin, Thunar and Caja) and otherwise opens its folder.

The Save Download picker starts in your system's Downloads directory, using the
suggested filename without any supplied directory components. If the desktop
does not publish a Downloads location, Steed uses `~/Downloads`. You can choose
another destination before saving.

Search filenames, profiles or destination paths in the Downloads search field;
matching ignores case and treats punctuation literally. Clear the search to show
all records. Progress updates retain the selected download; filtering it out clears
selection. Select a row and use **Copy Path** to copy its full destination path,
including when the file is still downloading or is no longer available.

Open **General → Ad Blocking** to download/update EasyList and EasyPrivacy, toggle filtering, or add host-name exceptions. Only a small bootstrap rule set ships with the browser; download the full lists for broader coverage. Rule compilation runs off the GUI thread, and a failed update keeps the last working rules. Network blocking applies immediately, while cosmetic (CSS) hiding is re-applied on page load — reload pages after changing exceptions. Same-origin frames, including nested frames and `srcdoc`, receive their own CSS rules; each pass covers up to 32 documents and eight frame levels. Cross-origin frames cannot be inspected. Scriptlets and resource redirects are not enabled, and unsupported filters are not presented as full uBlock compatibility.

## Configuration

Configuration is stored in `~/.custom_browser/config.json`. You can edit this file directly or use the browser's settings interface.

### Settings workspace

**General → Settings** (Ctrl+,) opens `steed:settings` in a browser tab, with **General**, **Profile**,
**Tabs**, **View**, **Cookies** and **Agents** sections. Browser preferences apply
across profiles, including when settings is opened from private browsing.
Home/search and website compatibility are in General; new-tab address and session
restore are in Tabs; appearance and layout are in View. Cookie sharing keeps its
existing profile/isolation rules. Agents links open the existing conversation or
connection setup; opening them does not grant browser access.

Choose **Save changes** to apply the preference draft, or **Cancel** to discard it.
Dark appearance previews inside settings first; Save updates open browser windows.
Independent changes from another settings window are merged. If the same setting
was changed to a different value, Save stops and keeps the draft; **Reload saved
values** explicitly discards preference edits and reloads saved values.

Interface reload preserves the controller-held preference draft and clears pending
profile reviews. It does not save preferences or repeat profile operations.

### Internal pages and shared styling

The browser's management tools now open HTML tabs rather than separate Qt
management windows. The desktop menus keep their usual click/shortcut behavior;
their commands open the corresponding page. New Tab's anchored HTML editors are
still available. These pages share `steed:style`, search/find, zoom, reload and
session restore:

| Address | Tools |
| --- | --- |
| `steed:cookies` | Filter, inspect and delete cookies; existing deletion protection applies |
| `steed:downloads` | Search downloads, progress, pause/resume/cancel, open/reveal/copy path |
| `steed:bookmarks` | Search and organize bookmarks, import/export, open links |
| `steed:history` | Search recent page names/addresses, open/copy a link, clear history |
| `steed:library` | Enabled folders, imports, files, versions and recovery |
| `steed:connections` | Accounts, connector recipes and reusable access settings |
| `steed:services` | Browse packaged connectors and open an exact recipe review |
| `steed:tasks` | Agent tasks, requests, grants, results, packages and reviews |
| `steed:panels` | Profile dashboards, linked-source refresh, reviewed forms and agent instructions |
| `steed:passwords` | Built-in password manager: search, edit, import and export logins |
| `steed:layout` | Preview/save/cancel browser placement and named layouts |
| `steed:newtab-settings` | Built-in start-page sections and custom New Tab/Welcome HTML |
| `steed:tab-groups` | Create, rename, reorder, collapse and remove groups |
| `steed:permissions` | Saved website permissions and their scope |
| `steed:agent-setup` | External-client setup and explicit local-access toggle |
| `steed:adblock` | Filter-list updates, blocking preference and exceptions |

The navigation rail groups browsing, customization and agent tools. Below 780 CSS
pixels, a **Browser page** dropdown replaces the rail to keep content usable.
Tabbed editors support Left/Right arrows and Home/End, skip unavailable sections,
and preserve focus on the selected tab. These selectors can be customized through
`steed:style`: `#workspace-nav`, `#workspace-picker` and `.compact-page` (HTML
sidebar controls). Opening an
existing page selects its tab. Headers distinguish profile-local data from
browser-wide management. History uses the existing most-recent-first collection;
opening internal pages does not add them to history. Bookmark Enter/double-click
opens a website while retaining the manager. Passwords binds to the selected
website tab and returns to it after successful filling. Library and persistent
start-page customization require a regular profile; private cookies/history and
bookmarks retain their existing session-only behavior.

Results are paged in groups of up to 500 rows, in addition to any existing
backend paging. Changing the results page clears selection; stale row targets
cannot act on replacement records. Repeated identical cookie snapshots preserve
rows and selection; changed cookie data clears selection before replacing rows.
Hidden tabs pause presentation polling and
refresh when shown. Source operations, such as an already-started file import,
retain their own lifecycle. Closing/navigating away cancels a layout preview;
Save keeps the existing conflict/merge rules. Reload retries a page whose backend
could not initialize, without repeating previous approvals or writes.

Browser confirmation dialogs, short text/choice prompts, website/media permission
requests, agent questions, connector/package/layout/page reviews and sharing
previews also use maintained HTML. The existing controllers still validate and
execute decisions. Secret field values are omitted from published state. Image
previews are bounded raster data; linked-review updates cover stale content before
rendering replacement state. Live task pages keep their separate website engine
below HTML controls, without receiving the management bridge. Website-chat paste
hides the presenter first, and closing the preview clears its private draft.

OS file/directory pickers remain native. Startup failures and the emergency
**Ctrl+Alt+Shift+R** appearance reset retain a native recovery path so broken CSS
or an unavailable HTML engine cannot prevent recovery. Browser menus and browser
chrome still use Qt; this milestone changes the management/dialog presentation,
not the engine or permission architecture.

Type `steed:newtab`, `steed:welcome`, `steed:settings`, `steed:agents` or
`steed:style` in the address bar. Settings, Agents and Style are maintained HTML
workspaces with isolated, off-the-record engines; their addresses are preserved
in saved tabs. Website pages and authored New Tab HTML never receive their native
bridge. Settings and Style support normal find, zoom and reload controls.

Open **View → Page Style** or **Settings → View → Page style**:

1. Choose a base theme and edit individual **Theme colors** for backgrounds,
   cards, text, borders, hover states, accents and feedback. **Restore base palette
   colors** removes color overrides. Optionally edit root CSS (up to 16,000 characters).
   All Steed pages start with `ui/web/root.css` and inherit this profile's overrides.
   Useful tokens include `--bg`, `--surface`, `--ink`, `--muted` and `--accent`;
   `[data-theme="dark"]` rules customize the dark palette. Custom page HTML may add
   its own CSS after the inherited stylesheet. Styles cannot enable external
   requests or grant browser/data permissions.
2. Show or hide the **top menu bar** and **bottom status bar**. The address bar's
   **☰** button always exposes all six browser menus. **Ctrl+Shift+M** toggles the
   top menu. Unsafe agent auto-approval remains marked on ☰ when enabled.
3. Enable the loading indicator below the address bar, choose its color/height
   (1–10 px), and optionally show a percentage. The default is a 3 px bar using
   Qt WebEngine's loading estimate. The percentage option uses an 18 px row for
   legibility. Progress follows the selected website tab and clears on completion,
   stop or failure; it is hidden for maintained workspaces and fullscreen video.
4. Click **Apply style** for this profile, or check **Apply to all profiles and
   use for new profiles** first. Synchronization replaces other appearance
   overrides in one atomic configuration write and updates open windows. It
   copies styling/bar preferences, not HTML, images, bookmarks, account data or
   toolbar layouts. Incognito edits last for this session and cannot sync outward.

Unsaved style fields stay in the form after an error. Conflicting saves from a
second window stop with an explanation; **Reload saved** explicitly discards that
form draft. Reloading the interface discards unsaved style fields and does not
repeat a save. **Reset form** fills defaults without applying them. If custom CSS
makes a page unusable, open **☰ → View → Reset Page Style** or press
**Ctrl+Alt+Shift+R** to recover with a native confirmation. The confirmation uses
native styling so custom colors cannot hide it. Existing authored HTML and
background images are preserved.

New Tab's image section is now a fixed, centered **cover background** behind the
content, with a tint to keep text readable. Choose it under **Customize New Tab →
Background image**. The optional **Browser menu** dropdown stays visible even in
minimal mode; `steed:style` can hide it independently. Click or pause over the
summary to open it; its six category links open HTML dropdowns beside the selected
link. Submenus and settings editors also open after a long hover. Ordinary commands
such as Close Tab run only on click. Editors use the shared root styling and appear
beside their trigger, clamped to the screen. **Open in Settings** at the bottom
opens the corresponding section in a full `steed:settings` tab. The desktop top
menu and address-bar menu retain their native behavior. Custom HTML remains isolated from these native
actions; agents can continue to propose/edit authored pages through existing
reviewed start-page tools. Home and New Tab addresses remain independently editable.

The toolbar's **☰** button starts at the left edge. Open **General → Layout Settings**
to move it, the navigation buttons, Passwords, zoom or the address field. The menu
button and address field cannot be hidden. Layout Settings also controls menu
order, sidebar side/width, section order and supported quick-action panel placement;
it does not provide unrestricted dragging of every browser control. Existing saved
layouts gain the menu button on the left while retaining their other relative
positions; new portable declarations publish its `menu` ID. Layout changes are
shared across windows; profile-scoped colors remain separate.

**CSS reference for users and agents** in `steed:style` lists supported tokens
and maintained selectors. Start with `--bg` (background), `--surface` (cards/menus),
`--ink` (text), `--muted`, `--border`, `--soft`, `--accent` and `--accent-hover`.
Feedback uses `--green`/`--green-soft`, `--warning`/`--warning-bg` and
`--error`/`--error-bg`. New Tab aliases `--card` to `--surface` and `--hover` to
`--soft`; `--shadow` controls shadows. Base theme colors apply first, then legacy
accent, explicit color overrides and finally custom CSS. For example:

```css
:root, :root[data-theme="dark"] { --bg: #18202b; --ink: #f1f5fb; }
.search { border-radius: 24px; }
```

Agents use `steed_start_page_capabilities` for the reference, then
`steed_start_page_propose` for a theme declaration:

```json
{"version":1,"name":"Ocean palette","page":"style","mode":"theme","style":{"colors":{"bg":"#18202b","surface":"#243044","ink":"#f1f5fb"},"css":".search { border-radius: 24px; }"}}
```

`style` is a nonempty patch; omitted fields keep their existing values. `colors`
replaces the explicit color map and uses token names without `--`, with six-digit
hex colors. Capabilities disclose defaults/reference only, not the current CSS.
The user reviews exact source and chooses the display profile; only the review's
visible checkbox can synchronize all profiles. Agents cannot choose their own
approval. Existing profile/data grants do not approve style changes. Stale previews,
reused request IDs with changed bytes, and private/recreated profiles retain the
existing proposal protections. Theme proposals grant no browser or data access.

The HTML editor host adapts the existing controllers; their validation,
persistence, cancellation and permission reviews remain authoritative. It renders
only enumerated controls through an isolated, offline HtmlSurface. No bridge is
installed in ordinary websites or authored start pages. Decisions still use the
existing trusted confirmation handlers. OS file choosers remain native; result
tables provide Previous/Next controls when more than 500 records are available.

Appearance lives in the shared `config.json` under `page_style`, with per-profile
overrides keyed by the stable profile identity. Renaming a profile preserves its
style; creating another profile with an archived name does not inherit its
override. A complete backup retains this configuration and the profile identity.

### Configuration Options

- `default_profile`: Default profile to use on startup
- `home_page`: URL to load for home button
- `new_tab_page`: URL for new tabs, independent of the Home button
- `search_engine`: URL prefix such as `https://www.duckduckgo.com/?q=` (search text is appended)
- `dark_mode`: Enable dark mode by default
- `page_style`: Shared stylesheet and profile-specific theme/bar/loading overrides;
  configure these through `steed:style` rather than editing identity keys manually
- `restore_tabs_on_startup`: Restore tabs when opening a profile
- `window_width`: Default window width
- `window_height`: Default window height
- `user_agent_override`: Replacement user agent; empty restores the engine default.
  The default is the engine's own Chrome user agent without the `QtWebEngine/…`
  token, so its Chrome version matches the client-hint headers the engine also
  sends. Settings warns when an override claims a different Chrome version:
  sites that compare the two (such as Discord and Google) may keep signing you out.

Regular profiles keep cookies, local storage, IndexedDB, service workers and
cache on disk, separately per profile, and switching profiles does not clear
them. While **Restore tabs on startup** is on, session cookies are kept across
restarts too (as Chrome does when continuing where you left off), so sites that
sign in with session cookies stay signed in; turning it off applies from the
next start.

Before a page goes away — closing its tab or window, switching the window's
profile, or unloading a sleeping tab — Steed lets it run its `beforeunload`,
`pagehide` and `unload` handlers, as a normal browser does when leaving a page.
Some sites save their sign-in there: Discord takes its login token out of local
storage while it is open and writes it back on unload, so without this it was
signed out after each profile switch or unloaded tab. A handler gets at most
1.5 seconds; a site that hangs cannot hold up closing. `steed:` and other
internal pages close at once. **Settings → Let sites save before closing** turns
this off for instant closing, at the cost of such sign-outs.
- `google_login_compatibility`: Account-host User-Agent workaround (enabled by default)
- `global_cookie_sharing_mode`: `on_switch` or `all_profiles`
- `global_cookie_sharing_apply_on_startup`: Whether global cookies are reconciled automatically
- `adblock_enabled`: Enable network blocking and cosmetic filtering
- `adblock_exceptions`: Host names excluded from filtering

The same directory also holds browser-managed state, which is not configuration:
`downloads.json`, `filter_lists.json`, `global_cookie_deletions.json`, `site_permissions.json`, `retired_profiles.json`, the
`profiles/` and `global_profile/` storage directories, `deleted_profiles/` for
archived profiles, and per-profile `tabs.json`, `groups.json`, `history.json`,
`password_manager.json`, and
`cookie_inheritance.json`. The inheritance journal stores hashes, never cookie
values. Internal task services create `tasks.sqlite` on first use to store task
metadata and their event journal.

Existing profiles retain their engine storage/cache locations. Fresh profiles
have a `.steed_profile.json` manifest selecting unique storage/cache subdirectories.
Qt can finish storage writes after a profile is destroyed; the retired-name index
prevents those markerless remnants from appearing as active profiles. Explicitly
recreating a retired name preserves remnants under `deleted_profiles` and uses a
fresh engine directory. Restore or back up the complete profile directory,
including its manifest; copying only an engine subdirectory is insufficient.

## Project Structure

```
Browser/
├── README.md               # Project overview and quick start
├── DOCUMENTATION.md        # Setup, usage, technical and design reference
├── ROADMAP.md              # Progress, acceptance gate, and planned work
├── steed                   # Executable launcher using the project environment
├── install_launcher.py     # Linux application-menu installer
├── main.py                 # Entry point
├── browser_window.py       # Main window UI (tab/profile/permission coordination)
├── profile_manager.py      # Profiles plus the application-owned engine registry
├── tab_manager.py          # Tab save/restore, closed-tab history, tab groups
├── browsing_history.py     # Bounded per-profile visits for address completion
├── password_vault.py       # Encrypted per-profile login vault, TOTP, CSV import
├── password_service.py     # Vaults per profile; capture and fill page scripts
├── password_forms.py       # Isolated-world form inspection and explicit filling
├── cookie_manager.py       # Cookie viewing and deletion for one profile
├── cookie_sharing.py       # Global-cookie baseline, provenance, re-inheritance guards
├── cookie_snapshot.py      # Private in-process DevTools cookie enumeration
├── download_manager.py     # Shared download lifetime and persistent history
├── ad_blocker.py           # Request interception and cosmetic filtering
├── config.py               # Configuration management
├── utils.py                # Atomic JSON, profile path helpers
├── webengine.py            # Shared engine settings
├── services/
│   ├── agent_gateway.py    # Opt-in private IPC, connection ownership and cleanup
│   ├── agent_tools.py      # Published tool schemas and browser-side validation
│   ├── local_worker.py     # Isolated subprocess, bounded pipe protocol and Qt bridge
│   ├── worker_bootstrap.py # In-sandbox limits/seccomp installed before source imports
│   ├── worker_manager.py   # Exact package decisions and live typed broker checks
│   ├── worker_store.py     # Durable worker receipts and task-bound package choices
│   ├── worker_diagnostics.py # Cached background sandbox setup check and limits
│   ├── task_store.py       # SQLite tasks, append-only events, restart recovery
│   ├── task_recovery.py    # Bounded follow-up snapshots and persistence schema
│   ├── command_broker.py   # Named commands dispatched on the Qt thread
│   ├── browser_backend.py  # Task pages, profile sharing, and observed navigation
│   ├── engine_capabilities.py # Implemented authority and native-policy prerequisites
│   ├── navigation_service.py # Shared navigation, find, and zoom operations
│   ├── permission_service.py # Modern/legacy permission responses and task denial
│   ├── site_permissions.py # Exact website approvals and private-session isolation
│   ├── approval_rules.py   # Bounded reusable agent approvals and matching
│   ├── task_policy.py      # Exact scopes, expiry, and thread-safe request budgets
│   ├── task_network.py     # Private per-target service-worker bypass adapter
│   ├── task_input.py       # Fixed native keys, file binding and task focus emulation
│   ├── task_client.py      # Task-bound client command facade
│   ├── page_inspector.py   # Asynchronous snapshots, validation, and fixed actions
│   ├── page_actions.py     # Bounded action inputs and value-free receipts
│   ├── form_submission.py  # Per-page submission tickets and journal URL privacy
│   ├── task_downloads.py   # Native downloads, safe destination publication and receipts
│   ├── task_uploads.py     # Bounded immutable copies of approved upload sources
│   ├── task_artifacts.py   # Retained task text, quotas and source-evidence validation
│   ├── connectors.py       # Trusted app tool mappings and bounded worker jobs
│   ├── connector_policy.py # Exact connector/tool/effect/destination permissions
│   ├── connector_store.py  # Durable app receipts and interrupted-write recovery
│   ├── page_inspection.js  # Fixed isolated-world DOM reader and mutation checks
│   └── task_service.py     # Application-owned task/session coordination
├── codec_test.html         # Local media/codec fixture
├── agent_mcp.py            # Optional stdio/HTTP adapter into the running browser
├── requirements-mcp.txt    # Optional official MCP SDK dependency
├── ui/
│   ├── address_bar.py     # Address bar widget
│   ├── find_bar.py        # Debounced page search and match navigation
│   ├── tab_strip.py       # Customizable HTML tab strip (web/tabs.*)
│   ├── cookie_dialog.py   # Cookie viewer
│   ├── download_dialog.py # Download list with pause/resume/cancel/open
│   ├── media_dialog.py    # One-shot screen/window share picker
│   ├── task_dialog.py     # Trusted task-grant review and cancellation
│   ├── task_approval.py   # Automatic queued approval popups
│   ├── password_assistant.py # Save bar, autofill and key menu
│   ├── passwords_page.py  # steed:passwords
│   ├── adblock_dialog.py  # Filtering toggle, exceptions, list updates
│   ├── group_dialog.py    # Tab group management
│   └── settings_dialog.py # Preferences dialog
├── tests/
│   ├── test_browser.py    # Regression suite (needs Qt)
│   ├── test_permissions.py # Permission API compatibility checks
│   ├── test_features.py   # Pure policy/filtering checks
│   ├── test_history.py    # History persistence and private-session checks
│   ├── test_task_store.py # Atomic task storage, journal, and recovery checks
│   ├── test_task_policy.py # Origin, action, network, and concurrent budget checks
│   ├── test_task_services.py # Deterministic client and real private-page checks
│   ├── test_task_profiles.py # Concurrent profiles, worker bypass, and revocation
│   ├── test_task_approval.py # Automatic popups, queueing, and explicit decisions
│   ├── test_page_inspection.py # Content consent, extraction, stale targets, and async cleanup
│   ├── test_page_actions.py # Edits, native text events, scrolling, waits, and receipts
│   ├── test_page_forms.py   # Form authority, validation, redirects, and outcomes
│   ├── qt_test_support.py # Shared QApplication and deferred fixture cleanup
│   ├── test_password_manager.py # Encrypted protocol and pairing regressions
│   ├── smoke_browser.py   # Real startup/render/shutdown check (manual)
│   ├── timing_check.py    # Startup, tab, typing and memory timings (manual)
│   ├── smoke_google_login.py # Local HTTPS login-compatibility check (manual)
│   └── profile_restart.py # Two-process cookie persistence check (manual)
├── pyproject.toml         # Ruff lint/format configuration
├── requirements-dev.txt
└── requirements.txt
```

The smoke and profile-restart files are runnable scripts rather than unittest modules, so
`unittest discover` does not collect them; run them directly as shown below.

### Timing check

`tests/timing_check.py` starts Steed fresh three times in a temporary profile
and reports the median of each figure; all pages are local files.

```bash
.venv/bin/python tests/timing_check.py --save before.json
# …make a change, swap the engine, or switch memory mode…
.venv/bin/python tests/timing_check.py --compare before.json
```

| Figure | Measured from → to |
| --- | --- |
| Start to window ready | process start (including Python's) → first event-loop turn with the window shown |
| Start to first page loaded | process start → the home page's load finished |
| Open a tab and load a page | `_new_tab` → load finished (median of 10) |
| Switch tabs | `_switch_tab` → Steed's events handled (median of 20) |
| Type 20 characters | key presses in the address bar, suggestions off |
| Scrolling: slowest 5% of frames / frames over 25 ms | a long page scrolled one step per frame for a second, timed with `requestAnimationFrame`; on a real screen this shows stutter, offscreen it mostly shows CPU load |
| Switch to a frozen tab until it responds | `_switch_tab` → the page answers a script |
| Switch to an unloaded tab until it reloads | `_switch_tab` → its load finished |
| Memory, one tab / 10 tabs | proportional set size (PSS) of Steed and its helper processes, after settling |
| Memory, one tab once idle | after cookie inspection has gone to sleep (30 s after the last cookie change in Balanced) |
| Restart with 11 tabs: first page / memory | a second start on the first session's saved tabs, to its first page loaded, and memory after settling |
| Close the window until it disappears | `close()` → the window hidden |
| Close the window and clean up | `close()` → returned, including unload handlers and page cleanup |

`--load-all-tabs` runs the same check with **Restored tabs** set to load all
at startup, to compare with the default.

**Cookie inspection: only when needed** (Memory saver's choice; Balanced and
Performance start it with Steed) starts inspection only when a window shows a
profile that receives cookies shared from the global profile, and then for that
profile and the global one. While you use just the global profile or incognito,
nothing is inspected (about 80 MB per profile saved) and the cookie viewer shows
live changes rather than a full reading. Switching back to "Start with Steed"
starts the waiting inspections at once. A setting added in a newer Steed and
never stored follows the mode your other settings match, so a Memory saver
setup stays Memory saver.

Where the memory goes, with one tab once idle (Balanced, offscreen, quiet
machine): the browser process (Python, Qt and Chromium's core) about 265 MB,
the tab's page process about 40 MB, the tab strip's interface page about 45 MB,
and helper processes about 35 MB — about 385 MB. Figures are lower when the
system is short of memory, because Linux then keeps less of each process
resident (the same build measured about 250 MB while a compile used 12 GB). For the first ~30 seconds,
cookie inspection adds about 160 MB: a hidden DevTools page (about 68 MB) and an
inspected page (about 12 MB) for each of the global and current profiles,
reading cookies so shared sign-ins stay in step. They sleep after 30 seconds
without cookie changes (5 seconds in Memory saver).

Figures compare runs on the same computer, and only when it is otherwise
quiet: the check warns when the load average is above half the core count. The
window opens on the current display; `--offscreen` runs without one and without
the GPU, which changes the numbers.

On the development machine (8 cores, 16 GB, X11 display with GPU, PyPI engine,
November 3, 2026): about 440 ms to window, 680 ms to first page, 95 ms per new
tab, 16 ms per switch, 20 ms typing, scrolling with the slowest frames at 17 ms
and none over 25 ms, 10 ms to wake a frozen tab and 115 ms to reload an
unloaded one, 465 MB with one tab and 730 MB with eleven. Restarting with
eleven saved tabs reached the first page in 730 ms with 540 MB, against
790 ms and 615 MB when all tabs load at startup. The window disappears 90 ms
after closing; its page cleanup takes about a second more out of sight (each
page gives back its GPU surface), and offscreen all of it takes about 45 ms.

### Engine boundary

`webengine.py` is Steed's boundary with Qt WebEngine: other code asks for
things in Steed's terms, so a later engine change, or a Qt API change, is a
contained edit there. It covers:

- **Tab lifecycle:** `tab_state(page)` returns `"active"`, `"frozen"` or
  `"unloaded"`, with `set_tab_state`, `wake`, `lightest_state` (the furthest the
  engine allows) and `deeper`.
- **Scripts:** `run_script(page_or_frame, source, callback, world=...)` in
  `PAGE_WORLD` (the page's own JavaScript) or `STEED_WORLD` (same document,
  separate globals the page cannot see); `page_script` and `add_profile_script`
  for scripts every page runs at `"start"`, `"ready"` or `"loaded"`.
- **Page actions:** `trigger(page, "back")` and the other `PAGE_ACTIONS`,
  `menu_action`, `find_text`, `view_for(page)`.
- **steed:// answers:** `fail_request(job, "denied")` and the other reasons.
- **Page options:** `set_options(settings, javascript=False, ...)`, `option`,
  `block_unknown_schemes` and `configure_web_settings`, with Steed names for
  each engine setting (`PAGE_OPTIONS`).
- **Profiles:** `private_profile(parent)` (nothing on disk),
  `stored_profile(name, parent, storage=..., cache=..., keep_session_cookies=...)`
  and `set_keep_session_cookies`.
- **Versions:** `versions()` gives the Qt WebEngine, Chromium-feature and
  Chromium-security versions.
- **Type hints:** `webengine.Page` and `webengine.Profile`.

`tests/test_engine_boundary.py` lists the 14 files that still import Qt
WebEngine directly. What remains is engine-shaped by nature: page and view
subclasses, URL-scheme handlers, request interceptors, permission and security-
key requests, and the feature probe. A new file fails the test unless
it uses `webengine.py` or is added to the list on purpose; a file that stops
using the engine must be removed from it. The engine names that have a helper
(lifecycle states, script worlds, injection points, page actions, find flags,
page settings, cache and cookie policies) may appear only in `webengine.py`.

## Engine and storage internals

**Memory & performance (`steed:memory`).** Open **View → Memory & Performance…**
or type `steed:memory`. Choose a mode on the axis from least memory to smoothest,
then change any setting on its own (the mode then shows as Custom):

| Setting | Memory saver | Balanced (default) | Performance |
| --- | --- | --- | --- |
| Cookie inspection | only when needed | starts with Steed | starts with Steed |
| Cookie inspection sleeps after activity | 5 s | 30 s | stays awake |
| Cookie inspection wake-ups | at most every 20 s | every change | every change |
| Freeze background tabs | after 2 min | never | never |
| Unload background tabs | after 15 min | after 4 h | never |
| Restored tabs | load when selected | load when selected | load all at startup |
| Interface | native Qt widgets | customizable HTML | customizable HTML |
| Command palette | freed on close | kept 1 min | preloaded and kept |
| When the computer runs low on memory | switch to Memory saver | switch to Memory saver | keep settings |
| Return freed memory | on | on | off |

A frozen tab keeps its page but stops scripts and timers (chat and mail tabs stop
updating until you return). An unloaded tab frees its renderer and reloads when
selected, at the same scroll position and with typed text kept (Chromium keeps
both in the tab's history entry). Before unloading, Steed wakes the page and
sends what Chrome sends (`freeze`, `beforeunload`, `pagehide`, `unload`) so the
site can save, waits up to 5 seconds and a further 0.3 seconds for storage
writes to land, and unloads only if the page answered; otherwise the tab stays
frozen and is retried at a later check (three tries per session). Discord, for
example, keeps its sign-in only by saving it at that moment. Tabs playing audio, shared with an agent,
with DevTools open or still loading, `steed:` pages and tabs you mark with
**Keep This Tab Loaded** (tab right-click menu; a marked frozen tab wakes at
once; the mark is saved with the session) are never frozen or unloaded, and Steed never goes further than Qt WebEngine recommends for a page
(a hidden page is frozen first and unloaded on a later check). Background tabs
are checked every 30 seconds. With delayed cookie wake-ups, cookies shared from
the global profile reach other profiles up to that much later; opening or
refreshing the cookie viewer always inspects immediately. The page also shows
live usage — Steed's total, the browser core, renderers, tabs active/frozen/
unloaded, awake cookie inspectors and available system memory — and **Free memory now**, which unloads
background tabs that can safely reload, puts idle cookie inspectors to sleep,
frees a closed palette and returns freed memory. In one measured session (one
window, four background tabs, some cookie activity) Memory saver used about
350 MB, Balanced about 520 MB and Performance about 575 MB.

Frozen tabs are dimmed in the tab strip and unloaded tabs are dimmed and
italic; hovering one says it resumes or reloads when selected. With **When the
computer runs low on memory** set to switch, Steed checks available system
memory every 10 seconds (Linux `/proc/meminfo`). Below 10% it follows Memory
saver — except the interface, which is not rebuilt mid-session — and unloads
background tabs that can safely reload; above 20% it returns to your settings.
Your saved choices are not changed, and `steed:memory` shows a notice while
this is active.

**Memory: idle cookie inspection.** Qt 6.11 cannot list stored cookies directly,
so each open profile keeps a hidden page with a private DevTools frontend to
enumerate them for the cookie viewer and global cookie sharing. Those two
renderer processes per profile were about a third of Steed's memory. After 30
seconds without cookie activity they are now released (the frontend is deleted
and the hidden page discarded) and rebuilt by the next cookie change or viewer
refresh. Measured with one window: about 316 MB at startup, 198 MB once idle;
a cookie write wakes only that profile. A change made while asleep reaches
global sharing a second or two later than when awake.

**Memory: interface surfaces.** Steed's HTML interface parts (the tab strip, right-click
menus, command palette, agent sidebar and dialogs) each run in their own renderer
process, about 12 MB apiece. Menus and dialogs free theirs when they close; the
command palette now frees its renderer after a minute hidden and rebuilds on the
next open. **Settings → View → Lighter native interface** switches the tab strip,
right-click menus, palette and dialogs to plain Qt widgets that need no renderer:
in a measured two-window session that was 2 renderers and about 350 MB instead
of 5 and about 390 MB. The native tab strip keeps tab placement, groups (shown
as colours), pinning, collapsed groups, reordering, closing and the tab menu,
but not tab-strip styling or dragging tabs between windows (use **Move to new
window** in the tab menu). Menus, the palette and dialogs switch immediately; the
tab strip changes in new windows and after a restart. The `steed:` pages and
the agent sidebar stay HTML. The largest remaining costs are the browser core
in the main process (about 185 MB) and the cookie inspectors described above
while they are awake (about 60 MB per profile).

**Memory: returning freed memory.** Closing tabs and windows frees their memory
inside Steed, but the C allocator keeps it reserved, so the process looked about
100 MB larger after closing 15 tabs although object counts were back to normal
(a repeated open/close test shows no growth per round). Five seconds after the
last tab or window closes in a burst, Steed asks glibc to return free memory to
the system (`malloc_trim`); in the same test the main process went back from
about 320 MB to about 230 MB. On non-glibc systems this does nothing.

- **GUI Framework**: PyQt6
- **Browser Engine**: PyQtWebEngine (Chromium-based)
- **Storage**: JSON files for configuration, SQLite for cookies (via Qt)
- **Profiles**: Isolated storage directories per profile

JSON preferences and tab files are replaced atomically. Settings are saved when
you click Save changes in the HTML preferences workspace.

High-DPI scaling uses Qt's pass-through rounding policy. Hardware acceleration follows Qt's platform defaults. Saved tabs are restored eagerly.

Qt 6.11 persisted-cookie enumeration uses a private in-process inspection adapter; no remote debugging port is opened. Partitioned cookies are omitted from viewing/sharing because Qt's cookie type cannot preserve their identity; bulk clear still operates on the engine store. Because that adapter depends on undocumented Chromium internals, a Qt upgrade can break it — `cookie_snapshot.VERIFIED_AGAINST_QT` records the version it was last confirmed against, and the regression suite fails if enumeration stops working.

### Cookie inspection and reconciliation

The global baseline and cookie viewer use `cookie_snapshot.py` because the
verified Qt runtime does not reliably enumerate persisted cookies through
`loadAllCookies`. Snapshot handling rejects concurrent-change races, coalesces
refreshes during an inspection, bounds retries/errors, and ignores stale
responses. A changing global snapshot is re-inspected until settled or the
retry bound is reached. Refreshes are debounced and the DevTools SDK module is
cached to reduce repeated load cost. Full enumeration still takes seconds.

Selected deletion expires the exact cookie identity rather than relying on
Qt's broad name/URL deletion filter. Explicit deletion blocks reinheritance;
resetting a site after Clear All opts in only that site. Inheritance records
contain hashes instead of additional plaintext cookie values.

`global_cookie_sharing_apply_on_startup` gates both sharing modes. When disabled,
sharing stays inactive until a profile switch or explicit site opt-in.

### Filter-list compilation

The browser starts with bootstrap rules while cached full lists compile on a
worker thread. Compilation starts after handlers and service state are ready,
so an immediate cached-worker result is not lost. Request interception does not
perform network updates or expensive list compilation. Failed updates preserve
the last working rules.

## Internal task services

Ordinary tabs and task pages use `services/navigation_service.py` for
navigation and `services/permission_service.py` for permission wiring and
lifetime checks. Browser prompts stay in the UI; task pages deny permissions.
The application-owned `DownloadManager` receives a destination-picker callback
from the registry instead of opening dialogs itself. A manager with no provider
declines downloads. Task downloads have their own explicit destination grants,
staging, cancellation, and receipts; they never invoke the ordinary file picker.
Single-file task uploads require exact source-file and upload-site approval.

The task service is available to trusted in-process Python controllers through
`window.profile_manager.registry.tasks`. It is initialized only on demand and
shared across windows using the same application registry. This is a developer
API; the optional [local MCP adapter](#local-agent-access-mcp) exposes a bounded
subset through private IPC. **Agents → Task Permissions**
provides user approval, denial, revocation, and cancellation. A client can propose
a scope but cannot approve it through the command broker.

### Remembering agent approvals

Task popups show the requested sites, actions, profile, app account/tool/effect
and destination, files, duration and budgets. Choose **Allow once** for this task,
**Allow every time** for all profiles (including private tasks), or **Allow every
time on this profile** for one existing named profile. Profile-specific choices
are unavailable for private task profiles. A saved approval can be used by any
connected agent; enabling local agent access is still required after startup.

A future request is approved automatically only when one saved rule covers its
whole scope: sites, actions, files, upload sites and exact app account/tool/effect/
destination must be subsets; duration and budgets cannot increase, and private
network access cannot be added. Separate rules are never combined. Each matching
task gets a fresh bounded grant and its own duration/budgets; rules are not an
aggregate lifetime spending limit. Expanded requests still show a popup.
If an automatic approval fails, for example because the profile cannot be
reserved, the request stays pending for review and the journal records
`permission.auto_approval_failed`; other tasks keep running.

Manage rules in **Agents → Task Permissions → Saved Approvals**. **Forget and Revoke
Access** removes the rule and revokes all active tasks approved through it.
Revoking one task leaves its saved rule intact. Disconnecting an app account
removes matching rules and revokes matching grants; reconnecting creates a new
account identity requiring fresh approval. Profile rules follow renames, but not
deletion/recreation under the same name. Saved lists retain their original labels.

Rules and their grant provenance are journaled atomically in `tasks.sqlite`
(schema 8). Restart preserves rules but interrupts tasks and never restores old
grants or retries operations. Approval management remains trusted browser UI;
agent tools cannot create, broaden or remove rules. Website choices are separate.

### Internal command API

`service.broker.submit(name, **arguments)` returns a `concurrent.futures.Future`.
Worker threads can submit commands; handlers and engine operations execute on
the Qt application thread. Never block that thread on `future.result()` before
completion. Use callbacks or wait from a worker while Qt's event loop runs.
Deferred handlers are flattened into that same Future; inspection completes
after the engine reply rather than returning a nested Future. Broker shutdown
rejects running deferred replies and cancels undispatched commands.
Cancellation discards queued work; stopping an already-dispatched navigation is
a separate command.

The application controller creates a task, then calls `service.client(task_id)`
on the Qt thread to issue a task-bound handle. `client.submit(name, **arguments)`
supplies the task ID automatically and rejects impersonation and management
commands. Give clients these handles; reserve the raw `service.broker` for trusted
application management. Neither interface is a sandbox for arbitrary Python
running inside the application. The local gateway authenticates adapter connections
and binds each to one task. Each HTTP MCP session has its own such connection;
its transport identity is also bound to the credential that initialized it.

| Command | Arguments / result |
| --- | --- |
| `task.create` | `objective`; returns a task record |
| `task.status`, `task.list` | `task_id` for status; records for list |
| `task.capabilities` | `task_id`; backend/engine versions, supported actions, and website-script availability |
| `task.events` | `task_id`, optional `after` sequence and `limit`; ordered events |
| `task.finish` | `task_id`, optional terminal `status`; closes owned sessions |
| `task.recover` | `task_id`; asks the user to select a finished/interrupted source and drafts before grants or work; returns recovery status |
| `task.recovery` | `task_id`; status and only the historical context explicitly shared by the user; does not restore authority |
| `artifact.store` | `task_id`, `title`, `content`, optional `format` and `source_events`; explicit `artifact` permission, returns immutable draft metadata |
| `artifact.list`, `artifact.read` | `task_id`, plus `artifact_id` for read; artifact permission, returns owned metadata or text |
| `worker.run` | `task_id`, `extension_id`, stable `request_id`; worker permission, requests trusted exact-package approval before isolated execution |
| `worker.capabilities` | `task_id`; fixed background sandbox self-check, cached availability/limits; no grant or task-data access |
| `worker.status`, `worker.list` | `task_id`, plus `request_id` for status; current worker permission, returns owned execution receipts |
| `worker.cancel` | `task_id`, `request_id`; stops an owned worker without spending a cleanup operation |
| `app.list_tools` | `task_id`; configured tool descriptions/effects, no grant or credentials |
| `app.call_tool` | `task_id`, `connector`, `tool`, `request_id`, `destination`, optional `artifact_id` and typed `inputs`; exact app permission, starts a call and returns its durable receipt |
| `app.status`, `app.cancel`, `app.reconcile` | `task_id`, `request_id`; owned receipt lookup, cooperative cancellation or read-only reconciliation |
| `grant.request` | `task_id`, `scope`; records a pending request, without authority |
| `grant.list` | `task_id`; task grant records, including decisions |
| `session.open` | `task_id`, optional `profile="private"`; requires approval, returns a session snapshot |
| `session.navigate` | `task_id`, `session_id`, `url`; dispatches GET navigation |
| `session.inspect` | `task_id`, `session_id`; URL/title/load metadata and `control_owner` (`agent` or `user`) |
| `page.inspect` | `task_id`, `session_id`; explicit `read` grant, returns a structured page snapshot |
| `page.target` | `task_id`, `session_id`, `snapshot_id`, `target_id`; validates an observed target and returns its description |
| `page.navigate` | `task_id`, `session_id`, `snapshot_id`, `target_id`; follows a validated link and reports the load outcome |
| `page.act` | `task_id`, `session_id`, `snapshot_id`, `target_id`, `operation`, optional `text`/`index`; requires `read` + `edit`, returns a control-change receipt |
| `page.submit` | `task_id`, `session_id`, `snapshot_id`, `target_id`; requires `read` + `navigate` + `submit`, returns an observed native-form outcome |
| `page.scroll` | `task_id`, `session_id`, `snapshot_id`, optional `frame_id`, `dx`, `dy`; requires `read` + `scroll`, returns observed offsets |
| `page.wait` | `task_id`, `session_id`, optional `condition`, `text`, `timeout_ms`; requires `read`, returns an observed match, timeout, or load failure |
| `page.download` | `task_id`, `session_id`, `snapshot_id`, `target_id`, `destination`; requires `read` + `navigate` + `download`, starts an observed attachment link and returns its download ID |
| `page.upload` | `task_id`, `session_id`, `snapshot_id`, `target_id`, `source`; requires `read` + `upload`, binds an approved source copy to an observed single-file input and returns metadata |
| `download.status` | `task_id`, `session_id`, `download_id`; download grant, returns progress/completion receipt without file bytes |
| `download.cancel` | `task_id`, `session_id`, `download_id`; cancels owned transfer and discards staged bytes without spending a cleanup operation |
| `session.stop`, `session.close` | `task_id`, `session_id`; stops or destroys the session |
| `session.attach` | `task_id`, `session_id`; internal read-only Qt view of the existing page |
| `session.watch` | `task_id`, `session_id`; opens the same-page watch window; returns JSON watch/control state |

An active task can call `client.submit("task.capabilities")` before requesting a
grant. This read-only query opens no page, consumes no operation budget, and
issues no authority. It reports `supported_actions`, `website_scripts`,
`script_unavailable_reason`, and engine versions. Queries retain the client's
task identity and reject inactive tasks. Alternate backends must provide their
own report; a missing implementation returns an error rather than claiming Qt
capabilities.

The Qt report currently returns `website_scripts: false`. Chromium below 152
reports `engine_upgrade_required`; a verified newer version reports
`native_policy_not_integrated`. An unrecognized version reports
`engine_version_unverified`. `native_policy_candidate` describes version
eligibility only: upgrading the engine never enables scripts or adds a grant.

For example, submit `task.create` with an objective, obtain its `task_id`, issue a
bound client, and use that client to submit `grant.request` with this scope:

```json
{
  "origins": ["https://example.org"],
  "actions": ["navigate", "inspect", "attach"],
  "profile": "private",
  "allow_private_network": true,
  "duration_seconds": 600,
  "max_operations": 100,
  "max_requests": 1000,
  "max_sessions": 1
}
```

A nonmodal **Allow task access?** popup opens automatically in a browser window.
Review the task, profile, sites, actions, and duration, then click **Approve** or
**Deny**. Requests queue one at a time. Closing/Escape leaves a request pending;
review it later in **Agents → Task Permissions**, which also provides revocation.
Requests made before a window opens wait for one. A client only submits
`grant.request`; approval is never available through its command handle.
Then submit `session.open` through the client. Use the returned `session_id` for
navigation and inspection; omit `task_id` when using the bound handle.
Navigation dispatch is not load completion: use `page.wait` with `condition="ready"`
under a read grant, or wait for metadata with `loading=False` and check
`load_success`. `tests/test_task_services.py` demonstrates
the complete workflow with a real local HTTP fixture, without AI or an account.

By default, each session owns a separate off-the-record profile. Its page survives closing
an attached view, and a new view attaches to the same page. Task completion or
the last browser window closing disposes pages before their profiles. File transfers
require the scoped download/upload operations described below. Site permissions,
document scripts, new workers, WebSockets, external URL
schemes, and the built-in PDF viewer are unavailable in current task pages.
Ordinary browser tabs retain their existing settings. Task browsing currently
supports document retrieval with GET/HEAD and explicitly authorized native form
submission. Even GET/HEAD can cause website effects; a grant does not establish
that a site is read-only. POST is allowed only through a bound, one-use form ticket.

Scopes match exact HTTP/HTTPS origins, including scheme and port; subdomains and
redirect destinations need their own entries. Credentials, wildcards, and origin
paths are rejected. `about:blank` is allowed without adding an origin.
Qt 6.11 can follow a subresource or frame redirect before its request
interceptor's block takes effect, and DevTools Fetch can neither hold nor
rewrite those requests. Restricted pages therefore also get a Content Security
Policy listing only the scope origins, added from an application-world script
as each document's head is created; Blink checks it before following every
redirect hop. Restricted pages run no page JavaScript that could remove it.
Scopes containing an IPv6 literal cannot be expressed in CSP and rely on the
interceptor alone. Live delegated tabs are unchanged. Exact file
destinations and upload sources/sites have separate permissions. Supported actions are
`navigate`, metadata `inspect`, content `read`, view `attach`, control `edit`,
`scroll`, form `submit`, `download`, `upload`, retained-text `artifact` and scoped
`app`. The default action list remains
`navigate`, `inspect`, and `attach`; content, edits, scrolling, and submission need
explicit approval.
An edit grant does not authorize submission, POST, active scripts, or file actions.

### Read-only page inspection

Add `"read"` to a proposed grant's `actions` before requesting approval. The
approval screen discloses that readable text and control labels will be shared
with the task client, including on signed-in pages. Existing metadata-only grants
cannot call `page.inspect`, `page.target`, `page.navigate`, `page.act`, `page.submit`, `page.scroll`,
or `page.wait`. No cloud provider receives this data
through a built-in classification/search provider. Local MCP clients receive
approved reads; their external model/provider use is outside browser enforcement.
Browser-managed provider disclosure remains future work.

After successful loading, call `page.inspect` through the task-bound client. It
returns a `PageSnapshot` with a unique snapshot ID, session/task IDs, URL/title,
navigation revision, source (`rendered_dom`), frame snapshots, and limitations.
Each readable frame contains bounded text and target descriptions: opaque target
ID, frame ID, kind/role, label, enabled/read-only state, link URL when supported,
candidate operations, and constraints. Candidate operations describe controls;
they do not authorize or execute actions. Supported native submit buttons report
`submit`, a query/fragment-free `form_action`, and `form_method`; custom controls,
unsupported forms, specialized input types, and multiple selection report constraints.
Option labels and values remain omitted; current selection uses an index.

Each frame also has an `outline`: visible page structure in document order, so
an agent can orient without reading all text. Entries are headings (with level),
landmarks (banner, navigation, main, complementary, contentinfo, search, and
named regions/forms), images with alt text or labels, tables (caption, up to 12
header columns and a row count), lists (ordered flag and item count), open
dialogs, alerts and status/live regions, `<details>` disclosures (summary and
open state) and tab lists. Labels come from author-provided names or bounded
visible text; field values and editable text are never included. At most 150
entries and 12,000 characters are returned per snapshot (`outline_truncated`),
and images without any text alternative add `unlabelled_images_present`.
Targets report ARIA states as `states`: `expanded`/`collapsed`, `pressed`,
`selected`, `current`, `checked`/`unchecked`/`mixed` (from `aria-checked`),
`has_popup` and `busy` (the control or a container is `aria-busy`). A target's
`role` is its explicit ARIA role, or else the role its native element exposes:
`link`, `button`, `textbox`, `searchbox`, `combobox` (single selects and inputs
with a suggestion list), `listbox` (multiple selects), `spinbutton`, `slider`,
`checkbox` or `radio`. Custom sliders and spin buttons report their
`aria-valuetext` or `aria-valuenow` as `value_text`; native control values stay
omitted. Custom textboxes, listboxes and grid cells are described but have no
DOM action (`custom_control_unsupported`); tree items and comboboxes join the
pointer-activated roles below (activate a combobox to open it, inspect again,
then activate its option).

In live sessions, a focusable custom slider or spin button reports `adjust`
with the `keyboard_adjustment` constraint. `page.act` with `operation: "adjust"`
and `steps` (−100 to 100, not 0; positive increases) focuses it and presses
ArrowRight/ArrowLeft (sliders) or ArrowUp/ArrowDown (spin buttons) that many
times through the same native keyboard path as checkboxes, checking focus
between batches. The receipt reports the widget's resulting `value_text` and
`applied` or `no_change`; the website decides the step size and limits. It needs
edit permission. During the adjustment the control's own markup may change, so
only its identity and focus are rechecked.

Frames from another origin cannot be read by the main document's script. When
such a frame's origin is in the task's scope, Steed runs the same read inside
that frame and returns its text, outline and scroll position. In restricted
sessions the frame is read-only (`cross_origin_frame_read_only`, no targets).
In live sessions it reports `cross_origin_frame` and its controls, each with the
`cross_origin_frame` constraint, and they work like main-page controls: text
entry, checkboxes, selects, buttons, custom widgets, sliders, hover and frame
scrolling (`page.scroll` with the frame's `frame_id`). Checks and actions run
inside that frame. Form submission and uploads stay main-document only
(`frame_form_unsupported`). Pointer actions (custom widgets, hover) are measured
inside the frame and mapped through its frame element, which must itself be the
topmost element at that point on the page; they are available only for frames
directly inside the page (`nested_frame_pointer_unavailable` otherwise).
Keyboard-based actions work at any depth. Frames outside the scope, sandboxed
frames and frames that do not answer within five seconds stay unread
(`frame_inaccessible_or_not_ready`).

Each frame also reports `scroll`: its scroll position, scrollable width/height
and viewport size in CSS pixels. When content continues below the viewport the
snapshot adds `more_content_below`. To read infinite feeds and lazily loaded or
virtualized lists, scroll the frame (`page.scroll`), call `page.wait` with
`condition: "settled"` — it reads the page every 250 ms and reports `matched`
once text length, controls and scrollable height stay unchanged for 750 ms (or
`timed_out`) — and inspect again. Restricted pages run no website scripts, so
script-driven lazy loading needs a live session.

Many web apps scroll an inner panel — a message list, sidebar or dialog body —
rather than the page. Give `page.scroll` a `target_id` (any control inside that
panel) instead of a `frame_id`: Steed scrolls the nearest scrollable container
around the target and reports its `scroll_position` and `container_size`
(scroll width and height, visible width and height). When only the page itself
scrolls, the result is `scroll_container_unavailable`; scroll the frame instead.

In live sessions, `page.act` with `operation: "hover"` moves the pointer over any
visible target without clicking, to open menus and tooltips that appear on
mouse-over; inspect again to see what appeared. Hover uses the same
visible/in-view/unobscured/stable checks as pointer activation and needs edit
permission. Restricted sessions return `live_session_required`.

`operation: "drag"` with `to_target_id` presses on the target, moves to the
other target in ten steps and releases there — for reordering lists, moving
cards between board columns or dragging handles. Both must be visible,
unobscured main-page targets from the same snapshot; each point is measured
twice about 60 ms apart and must not move. Pointer-event drag libraries and
native HTML5 drag-and-drop between page elements both work. Dropping files from
the computer is not supported. Drag needs submit permission, since moving items
can change data; inspect again to confirm the result.

For effects that arrive after an action — search results, validation messages,
a list that loads — call `page.wait` with `condition: "changed"`. It compares
reads every 150 ms with the agent's last `page.inspect` of the same page (or,
without one, with its own first read) and reports `matched` once the visible
text, any control's role, label, enabled state, states or value text, or the
outline differs, or once the page navigates. A change that landed before the
wait started therefore still matches.

In live sessions (shared tabs or live private pages), custom widgets in the main
document with an ARIA role of button, link, tab, menuitem (including checkbox and
radio items), switch, checkbox, radio, option or treeitem report `activate` with the
`pointer_activation` constraint. Activation needs `submit` permission, like other
live button activation, and is a single left click at the element's centre.
Before clicking, the page checks twice, about 60 ms apart, that the element is
enabled, visible, not inert, inside the viewport (after scrolling it into view)
and the topmost element at that point, including through open shadow roots; a
covered, off-screen or moving target fails with `target_obscured`,
`target_outside_viewport` or `target_moved` and nothing is clicked. Agents never
supply coordinates. Restricted pages and frames keep these
widgets observation-only.

Targets also expose `description`, `required`, `invalid`, and `error_message`.
Descriptions prefer visible `aria-describedby` references, then `aria-description`,
then a title differing from the label. Native form controls' `required` attribute
and `aria-required` contribute required markers. `invalid` reports author-declared
`aria-invalid`; it does not run browser validation or inspect current field values.
When invalid is declared, visible `aria-errormessage` references supply the error
hint. See [W3C guidance on invalid fields and associated messages](https://www.w3.org/WAI/WCAG22/Techniques/aria/ARIA21).
An error also associated through `aria-describedby` stays in the description;
agents should read both fields rather than treating absence of one as success.

Labels, descriptions and errors are bounded to 256 characters each and 16,000
characters combined across all targets. `target_text_truncated` signals omitted
target text; controls retain opaque IDs and constraints. This also keeps large
Unicode observations within the MCP response budget. References use at most eight
IDs per attribute and resolve within the same document or open shadow root. Hidden/editable ancestors and current field values remain
excluded, including when referenced by a visible field. This intentionally omits
hidden accessible descriptions and is not the browser's complete accessibility
name/description algorithm. `accessibility_tree_not_inspected` and
`hidden_accessible_references_omitted` make these coverage limits explicit.

Live references include associated label/help/error content and ancestor attributes
in their freshness fingerprint. A changed hint, error or visibility container
requires a new inspection; unrelated page updates remain permitted. Fingerprints
are bounded to 65,536 characters and 128 ancestor levels. A live target whose
context exceeds the bound has no candidate operations and reports
`live_target_context_limit`; it cannot be acted on through that stale reference.
Reference reading shares the overall node budget, with exceptionally deep ancestry
reported as `accessible_reference_depth_limit`. These fields add observation only;
they grant no new action, file, history or profile permission.

Pass a snapshot ID and its target ID to `page.target` to validate that reference.
Only the most recent inspection for a session is retained. Navigation, frame URL
or document changes, observed DOM mutations, removed/replaced nodes, and a newer inspection
invalidate old references. The navigation revision alone is not a DOM revision;
the snapshot ID and browser-side document/node checks enforce freshness.
Unknown targets and cross-task/session references fail. Every completed edit or
scroll attempt also invalidates references, including changes to value/checked
properties that do not produce DOM mutations. File actions remain pending.

Inspection uses fixed browser code in Qt's
[isolated application world](https://doc.qt.io/qt-6.11/qwebenginescript.html#ScriptWorldId-enum).
Clients cannot supply scripts or selectors. Restricted sessions keep document JavaScript disabled;
the reader changes no DOM content. It omits input/select/textarea values, editable
text, and hidden/script/style content. These omissions do not classify arbitrary
visible page text as safe: site text and labels remain untrusted data.

Results contain at most 32,000 text characters, 200 targets, and 16 frames, with a
12,000-node traversal budget and a five-second engine timeout. Labels and link
URLs are bounded too; links over 2,048 characters cannot be navigated rather than
executing a truncated destination. Same-origin frames and readable `srcdoc` frames are
inspected. Cross-origin/sandboxed or unready frames report
`frame_inaccessible_or_not_ready`. Open shadow roots contribute bounded text and
control names, including image alt descriptions. Up to 32 roots at eight levels
are inspected within the existing node/text/target budgets; exceeding them reports
`shadow_root_limit`. Closed roots and canvas pixels remain uninspected. Slot/light
DOM ordering is approximate and reported when slots are observed. Shadow controls
are observation-only and report `shadow_control_actions_unavailable`; inspect a
fresh snapshot after changes. Mutations inside inspected roots invalidate
restricted snapshots, and live targets recheck their own names and host ancestry.
Truncation and unsupported content are reported, not silently presented as a
complete page.

Visible rich-text editor boundaries now appear as `kind="editable"` with labels,
help/error metadata and `values_omitted`. Their text and nested controls stay out
of observations, including in open shadow roots. On a normal live page, an explicit
main-frame document-root `contenteditable` editor supports `page.act(operation="enter")` with
read/edit permission. Chromium replaces its selected contents as literal text;
multiline text, trusted input events and native undo are supported. The receipt
contains only changed/text-match facts. Existing formatting may be retained by the
browser. Live website input handlers may change data; Enter/Send is not pressed.
Readonly/disabled controls, redirected focus and changed targets are rejected.
Restricted, child-frame and shadow-root editors remain observation-only. Missing or
partly unsupported controls produce screenshot/computer-input guidance without
claiming the entire page was understood.
Static `contenteditable="false"` wrappers retain normal text/control discovery.
Their noneditable islands inside a real editor remain omitted with that editor,
including when another control references them as help text.

The read permission and live grant are checked at dispatch and before returning
content. Revocation, expiry, navigation, stop, renderer termination, supersession,
and session shutdown reject interrupted reads; late callbacks cannot revive them.
An inspection event records counts, identifiers, revision, and limitations before
content is returned. Page text, labels, form values, and full snapshots are not
written to the task journal. A failed evidence write rejects the result and
disables task commands.

### Target-based link navigation

After `page.inspect`, follow an observed link using its opaque identifiers:

```python
client.submit(
    "page.navigate",
    session_id=session_id,
    snapshot_id=snapshot.snapshot_id,
    target_id=link.target_id,
)
```

Both `read` and `navigate` must be approved. The browser revalidates document/node
identity and enabled state, checks the exact destination origin, and records the
request before issuing GET navigation in the task's main page. It does not click
site handlers, submit forms, or execute caller scripts. Links inside readable
frames can be followed, but their destination opens in the main task page.

The future returns `NavigationResult` with session ID, actual URL, revision, and
`status`: `loaded`, `same_document`, `load_failed`, or `timed_out`. Fragment jumps
report `same_document` after observing the destination without reloading the
document. A blocked redirect is a failed
load. Timeout stops the load and is not proof of absent website effects. Stop,
superseding navigation, renderer termination, revocation, expiry, or close reject
interrupted operations. New navigation invalidates old target references.
Request and outcome evidence must be saved before dispatch and return respectively;
failed journal writes disable task commands. Labels and returned page text remain
outside the journal. Loaded means document load completion, not application-level
success; scripts and full SPA/form workflows remain unsupported.

### Controlled edits and scrolling

Request `"read"` and `"edit"` to change supported controls; request `"read"` and
`"scroll"` to scroll. The approval popup and management dialog disclose these
actions. Existing grants keep their original authority. Example control entry:

```python
client.submit(
    "page.act",
    session_id=session_id,
    snapshot_id=snapshot.snapshot_id,
    target_id=field.target_id,
    operation="enter",
    text="Example text",
)
```

Use a fresh `page.inspect` after each completed attempt. The browser checks node
identity, document/frame state, visibility, enabled/read-only state, and live
authority before dispatch and receipt. Supported operations:

| Operation | Supported controls and input | Semantics |
| --- | --- | --- |
| `enter` | Text, search, email, URL, telephone inputs and textareas; `text` only, up to 4,096 UTF-16 units | Focus/select the resolved control, then Chromium's editing command; trusted input events, undo, and field length limits are verified on Qt 6.11.2 |
| `activate` | Checkbox/radio inputs; no additional input | Focus the resolved control and send native Space down/up; browser-generated keyboard/click/input/change events are trusted |
| `select` | Single-selection native selects; `index` from 0 to 999 | Native Home/ArrowDown keys skip unavailable choices; observe the actual index; absent/disabled/hidden options and multiple selection fail |

The text editor uses the deprecated `document.execCommand` API through fixed
browser code, verified on the pinned engine. See Chromium's
[editing implementation](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/editing/commands/editor_command.cc)
and the [HTML activation specification](https://html.spec.whatwg.org/multipage/interaction.html#activation-behavior).
No arbitrary commands, scripts, selectors, or protocol operations are exposed.
Checkbox/radio and selection receipts use `input_semantics="chromium_keyboard"`.
Fixed [`Input.dispatchKeyEvent`](https://chromedevtools.github.io/devtools-protocol/1-3/Input/#method-dispatchKeyEvent)
commands run through an in-process Qt frontend with separate off-the-record
storage. Shared-profile pages reuse their worker-bypass adapter; private pages
initialize it on first native control use. No public debug listener is opened.
Task-only focus emulation starts before focusing the resolved control and ends
after observation. It does not activate a desktop window or focus another tab.
An unattached page with a zero-sized viewport gets a fixed 1280×800 virtual
viewport, retained for the task page; a page with an existing viewport keeps it.
This changes the task's layout without creating a visible window. Readable child
frames use the focused document. Pointer gestures and custom widgets remain pending.

Selection uses at most 32 keys per batch and rechecks the target, focus, document,
and live grant between batches. It can generate events for intermediate choices;
an unchanged selection sends no selection keys. The complete native action is
bounded to ten seconds, including adapter setup; individual input batches have
five-second deadlines. Protocol acknowledgement alone is insufficient: the browser
observes checked/selected state before returning. A mismatch returns `unsupported`;
adapter loss, stale state, focus drift, timeout, and cancellation reject the action.
There is no synthetic-click/selection fallback or automatic retry.

These semantics do not establish compatibility with JS handlers, custom widgets,
or SPA workflows. Password/file fields, specialized inputs, generic buttons,
and contenteditable controls remain unavailable to restricted-page `page.act`.
Normal live-page rich-text entry is described above. Scriptless edits
change the task page's DOM; they do not submit the form or update another tab's
controls, even when those tabs share the profile.

`PageActionResult` reports action/session/reference IDs, operation, revision,
observed `changed` state, input semantics, and `status`: `applied`, `no_change`,
`constrained`, or `unsupported`. It can include checked state, selected index,
text-match confirmation, or scroll offsets; it never includes field text or
option values. Length limits or engine sanitization can return `constrained`;
unsupported editing can return `unsupported`, rather than claiming success.

To scroll a snapshot's main frame, submit `page.scroll` with `snapshot_id` and
`dy=400`; optional `frame_id` selects an inspected readable frame. `dx`/`dy` are
integer CSS-pixel offsets bounded to ±10,000. Fixed CSSOM scrolling returns the
observed position and whether it changed; an edge can return `no_change`.
Network requests caused by scrolling still use the original scope and budget.

Only one controlled action runs per session. Stop, superseding navigation,
renderer termination, close, revocation, expiry, or timeout reject interrupted
receipts. A request gets a journaled action ID before dispatch, and the outcome
must be saved before returning it. Entered text and option values never enter
these events. Already dispatched work may have happened despite an interrupted
receipt; inspect state before retrying. No rollback is promised.

### Explicit form submission

Request `"read"`, `"navigate"`, and `"submit"` before using `page.submit`.
The ordinary Approve/Deny popup explains that submission can change account data
and send current fields, including hidden site tokens. `edit` alone never grants
submission; `submit` alone never grants reading or editing. Existing grants retain
their original actions. The form destination must belong to the approved origins.

Inspect the loaded page, choose a target whose operations include `submit`, then:

```python
client.submit(
    "page.submit",
    session_id=session_id,
    snapshot_id=snapshot.snapshot_id,
    target_id=submit_button.target_id,
)
```

Steed resolves the browser-issued target, rechecks its document and live form,
then uses native [`requestSubmit`](https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#dom-form-requestsubmit).
Required-field validation and the chosen button's action, method, target,
encoding, and validation overrides apply. Native serialization includes successful
controls, hidden tokens, and the chosen submit button; field values are not returned
by inspection. Controls named `action`, `method`, or `checkValidity` cannot shadow
the browser's native form checks. Every completed attempt invalidates references.

Current support is main-document, same-window HTTP/HTTPS GET or POST with
`application/x-www-form-urlencoded` encoding and at most 200 native form controls.
Approved single-file uploads additionally support POST `multipart/form-data`.
Forms containing passwords, multiple/folder file inputs, child-frame forms,
plain-text forms, dialog methods, image submitters, and new-window/named targets
are unavailable. Multipart forms require the explicit upload grant described below.
Document scripts remain disabled, so script-handled forms and SPAs need later work.
Submission checks this setting before preparation and again before arming its
ticket. An enabled setting returns `scripted_submission_unavailable` without
dispatch: JavaScript submit handlers could change sensitive controls after DOM
preflight, and a route/method ticket does not bind the serialized body.
This does not add password-manager integration to tasks.

One per-page ticket binds the destination and method to a dispatch. The interceptor
checks main-frame resource type, grant lifetime, origin, and request budget; it never
exports the request body. General POST requests are denied. Main-frame redirects
after an authorized POST submission are not followed: Qt can apply an interceptor
block only after a 307/308 redirect has already resent the body, and the
navigation gate, which runs before the redirect, cannot tell 303 from 307. The
result reports `redirect_not_followed`; navigate to the destination separately.
GET form submissions keep ordinary redirect origin and budget checks. Qt retains its
form navigation classification on those redirects; the separate navigation gate
rejects unsolicited second form submissions.
User tabs on the same profile retain their ordinary submission behavior.

`FormSubmissionResult` includes action/session/target IDs, revision, destination
origin, `request_authorized`, and status: `validation_failed`, `loaded`, `load_failed`,
`redirect_not_followed`, or `timed_out`. Authorization means the network gate allowed a request, not that the
server received it. `site_effects` remains `unknown`: a loaded document does not
confirm an account change, and a failed/interrupted receipt does not prove nothing
happened. Stop, expiry, revocation, renderer failure, and journal failure interrupt
pending work. There is no automatic retry or rollback.

Requests commit their action ID before dispatch; receipts commit before disclosure.
Journal URL evidence now stores origins only and omits page titles, since result
paths, queries, fragments, and titles can reflect submitted values. Submission
receipts also expose origins only. Authorized live inspection and normal navigation/
wait receipts still return actual URLs and page content; sites may echo submitted
values there. Form values, hidden tokens, and request bodies are never copied into
action events. Existing journal records are not rewritten.

### Bounded state waits

`page.wait` needs `read`, spends one operation, and supports two conditions:

- `ready` (default): wait for current loading to finish; returns `matched` or
  `load_failed`. It reports the actual URL/revision, not application-level success.
- `text`: provide a literal, case-sensitive `text` of 1–256 characters; poll
  normalized readable frame text after successful loading. Field values and
  inaccessible content remain omitted. Returned limitations make incomplete
  observation explicit; a timeout does not prove text is absent from the whole page.

`timeout_ms` defaults to 5,000 and is bounded to 1–10,000. Both conditions return
`timed_out` on deadline. If the deadline expires before any text read completes,
`inspection_not_completed` distinguishes missing evidence from an observed miss.
`PageWaitResult` contains session, condition, status,
URL/revision, and limitations; the query and page text are excluded from receipts
and journal events. Text waits refresh inspection and invalidate old references.
They allow DOM changes while waiting; navigation, stop, a control action, renderer
termination, or session shutdown interrupts them. Revocation/expiry checks also
apply before returning results. Only one wait runs per session, and a wait cannot
start during a controlled action. Waiting does not spend network budget by itself.

### Scoped task downloads

Request `download` alongside `read` and `navigate`, with exact absolute filenames
in `download_destinations`. For example:

```json
{
  "origins": ["https://example.org"],
  "allow_private_network": true,
  "actions": ["navigate", "inspect", "read", "download"],
  "download_destinations": ["/home/yourname/Downloads/report.pdf"],
  "max_download_bytes": 26214400
}
```

The approval popup lists destinations and the per-file size limit. Parent
directories must already exist and cannot contain symlinks. Filenames must be
normalized absolute paths; there is no folder-wide, overwrite, or execution
authority. Up to 20 filenames may be approved. The default size limit is 25 MiB;
the maximum is 100 MiB.

Inspect a page and pass the attachment link's current `snapshot_id` and
`target_id`, plus an approved `destination`, to `steed_page_download`. It starts
navigation on that same task page/profile and returns a `download_id`.
Poll `steed_download_status` with its session/download IDs; terminal states are
`completed`, `cancelled`, or `failed`. Status polling spends operation budget,
so use a bounded polling interval, such as 500 ms. Completed receipts include
destination, observed received/total bytes, state, and a bounded error code.
The file's contents and URL query are excluded from journal evidence.

Steed accepts only the expected native attachment request. HTML/save-page
responses are unsupported; download URL redirects fail closed when they change
the request's URL. Unapproved origins are blocked before their request reaches
the server. Pending transfers have a 30-second deadline and block other page
actions/takeover. The endpoint and cookie tests cover private and concurrently
shared profiles; ordinary tabs keep their existing download picker/history.

The engine writes to a private temporary directory anchored by a Linux directory
descriptor. Before publishing, Steed rechecks authority, destination identity,
byte counts, and journal availability. Atomic hard-link publication fails if a
file or symlink already occupies the approved name. Completed files have mode
0600. Progress checks cancel oversized/unknown-length transfers; temporary disk
usage may exceed the limit briefly before an engine notification is processed.
The final file is never published over the size limit.

`steed_download_cancel`, stop/navigation, revocation, expiry, disconnect, and
session/task shutdown cancel pending transfers and remove staged bytes. Files
already published remain; cancellation cannot undo an HTTP request already sent.
No retry, pause/resume, file execution, or file-content API is supplied. There
are at most 100 download records per session. An evidence failure after physical
publication disables task access; reconcile the destination before retrying.

In **Agents → Task Permissions**, select a task, then open **Activity** for its
latest 200 journal events or **Downloads** for progress, approved destination,
byte counts and results. Select a pending transfer and click **Cancel Selected
Download** to stop that file while retaining the task. Completed/failed receipts
remain available after its pages close. The display is task-scoped and excludes
page/form contents. It refreshes while the dialog is open; closing stops polling.

Qt requires choosing the download path before accepting its request; see
[QWebEngineDownloadRequest](https://doc.qt.io/qt-6.11/qwebenginedownloadrequest.html).
Steed uses ordinary page navigation to retain page request enforcement, rather
than exposing an unrestricted engine download API.

### Retained task artifacts

Include `artifact` in a task's requested actions to save/read generated notes or
drafts locally. This permission is separate from page reading, uploads and app
connections; it grants no website access. The popup explains that drafts remain
after the task ends. Old grants gain no artifact authority.

Use `steed_artifact_store` with `title`, `content`, optional `format` (`text` or
`markdown`, default `markdown`) and optional `source_events`. Each source must be
the sequence number of this task's successful `page.inspected` journal event;
foreign, invented, duplicate and other event types are rejected. Up to 20 sources
are allowed. These are inspection references, not verification of generated claims.
Source URLs can be written into the draft by its author; Steed does not fetch them.

Storage returns an immutable ID, size, SHA-256 fingerprint and source references,
without echoing the body. `steed_artifact_list` returns owned metadata and
`steed_artifact_read` retrieves one owned draft. Calls spend the task's operation
budget and require current approved authority; completion, revocation, expiry and
restart end client access. A new task cannot adopt another task's drafts.

Limits are 16 KiB UTF-8 per draft, 50 drafts/512 KiB per task and a 200-character
printable title. The MCP transport's serialized-request size limit also applies.
Drafts are immutable through the API; save a new draft to revise one. Body storage
and its metadata receipt commit together. Titles/body stay out of journal events.
Content is retained as local plaintext in owner-only `tasks.sqlite`, not encrypted
or automatically sent to any provider, app or website.

In **Agents → Task Permissions → Artifacts**, choose a draft to review it as plain
text. Markdown/HTML do not run scripts, load images or follow links. Completed and
interrupted tasks retain drafts for user review after restart. **Delete Selected
Draft** asks for confirmation and removes the stored draft with a content-free
receipt; this trusted user action is unavailable to agents. It does not promise
forensic erasure from SQLite journals, free pages or external copies.

### Persistent agent file library

Use **Agents → Agent File Library…**, or **Conversation & settings → File library…** in the
agent sidebar, to keep reusable files outside individual tasks:

1. Choose **New library folder…** and name it, for example `Research`.
2. Use **Add files…** or **Add folder…**. Imports copy regular files, subfolders
   and empty directories into Steed; originals remain unchanged. Select a
   subfolder first to add files inside it. Symlinks and special files are rejected.
   Reading selected files happens in the background, and each import commits as
   one operation. Closing the window cancels pending publication.
3. Turn on **Full file access for all connected agents**. This gives every
   authenticated connected agent read/create/edit/remove access throughout that
   folder, including future contents and subfolders, across tasks and restarts.
   No repeated per-file or task-grant popup is needed for library operations.
   Folders start disabled. Enable only folders whose contents you intend agents
   and their model/process providers to receive.
   To keep something you downloaded, select it in **Downloads** and choose
   **Add to Library…**, then the library folder; a taken name becomes
   `name (2).ext`. The button is not offered in incognito windows.
4. Ask your agent: “Use my Research library folder, read the project notes and
   save your revised report back into it.” The agent discovers enabled folders
   through MCP; it does not need a local host path or another upload. Agents can
   also search inside text files (`library.search` with `content: true`, files up
   to 4 MiB, returning a short snippet) and list the newest additions first
   (`order: "recent"`), so “use the PDF I just added” or “find my notes about the
   budget” work without browsing every folder. Each result reports `modified`;
   files saved before this version report 0 until they next change.
5. Disable the same switch to stop further access immediately. Unfinished writes
   are cancelled and cannot resume simply because you re-enable the folder.
   Copies an agent has already read cannot be retracted.

This is an explicitly shared library across regular browser profiles, separate
from browsing cookies/history and per-task workspaces. Websites never receive
library access. The library UI is unavailable in incognito and closes/clears on
profile changes; independent agent access previously enabled on a library folder
remains in effect until you disable it. No library permission authorizes code
execution, browser actions or app-account access. External agents keep their own
host tools/permissions; the library does not limit those tools.

**Remove** moves files or complete subfolders to **Show Trash**. Choose an entry
there and **Restore** to recover it. **Versions…** restores an older file edit
as a new current revision. Agents cannot silently overwrite a newer edit: writes
require the listed revision, and directory revisions change with descendants.
**Export file…** copies a current file to a new chosen location. **Clear recovery
copies…** permanently clears the selected library folder's Trash/old versions
after confirmation; current files remain. These controls do not promise forensic
SQLite erasure or remove copies already exported/fetched.

Storage is local in `~/.custom_browser/agent_library.sqlite`, with private file
permissions and a separate schema from tasks. Current limits are 50 library root
folders, 64 MiB per file, 512 MiB total including versions and pending writes,
500 import entries at once, and bounded metadata/history counts. The UI can
filter the selected folder by name or path with the **Filter** box (a literal
substring, so `%` and `_` are ordinary characters). **Next page** and **Previous
page** navigate 200 entries at a time, including entries beyond the former 2,000
row limit. Paths remain visible when a filter or page omits their parent folder.
Agents can page with `steed_library_search` to find a file across every enabled
folder without listing them one at a time. Files are stored as data,
never rendered/executed automatically. Reads and uploads use 32 KiB chunks.
These are resource limits, not repeated approval requirements.

| MCP tool | Use |
| --- | --- |
| `steed_library_folders` | Discover enabled root IDs/names; disabled roots are hidden. |
| `steed_library_list` | List direct children with `folder_id`, optional `path`, `after`, `limit`; recurse into directories. |
| `steed_library_search` | Find files across enabled folders by name/path, optionally restricted to `folder_id`. Returns folder id/name, path and current revision; literal substring match, page with the returned cursor. `has_more`/`truncated` indicate that more matches can be retrieved on the next page. |
| `steed_library_read` | Read base64 bytes with `folder_id`, `path`, current `revision`, optional `offset`/`limit`; changed revisions fail. |
| `steed_library_mkdir` | Create a relative subfolder and missing parents. |
| `steed_library_write_begin` | Bind `request_id`, path, byte count, SHA-256 and expected revision; `0` creates a new name. |
| `steed_library_write_chunk` | Append canonical base64 at the next offset to an owned `write_id`; identical retries are harmless. |
| `steed_library_write_commit` | Verify length/hash and publish atomically; committed repeats return the original receipt without replay. |
| `steed_library_remove` | Move a file/subdirectory to recoverable Trash with its expected revision. |
| `steed_library_restore` | Restore a known removed file/subdirectory inside an enabled root. |
| `steed_library_import_workspace` | Copy a current library file into a sealed task input; requires workspace permission. |
| `steed_library_save_workspace` | Save a sealed task output into an enabled library root; requires workspace permission and expected revision. |

The workspace bridge removes repeated uploading for local generators. Import a
library file, use its returned `file_id` in the existing worker protocol, then
save the sealed output back to the library. Normal workspace/code/app permissions
still apply; library access alone does not execute a package. Task workspace
limits remain 8 MiB/file and 32 MiB/task. Pending library writes are task-owned;
cancelled tasks cannot continue them. Restart discards unfinished writes without
replaying edits. Files and enable switches remain available to later tasks.

Linked/live host directories, filesystem mounts, automatic indexing/content
search, drag-and-drop and larger configurable quotas remain possible follow-ups.
The first milestone is a persistent managed library with explicit folder access.

### Generated files and custom integration packages

Agents can supply arbitrary file formats through a task-owned workspace: PDFs,
spreadsheets, images, archives and source code all use the same byte interface.
Steed stores and exports those bytes. Agents can supply bytes they already generated,
or build a [local Python worker](#running-an-agent-built-local-worker) to create a
missing generator or integration using supported app tools.

For users, open **Agents → Task Permissions → Workspace**, then select the task:

1. **Share File With Task…** selects one regular local file. Its snapshot becomes
   readable by the connected agent while that task has approved `workspace`
   permission. Sharing may disclose its contents to the agent's model provider.
   Host folders, other task files and browser credentials are not included.
2. Select a generated or shared file to see its name, source, type, state, size
   and SHA-256. Contents are not rendered or executed in this panel.
3. **Export Selected File…** saves a sealed file to a new filename, including
   after completion or restart. Existing filenames and symlink paths are rejected.
4. **Delete Selected File** defaults to No and clears the retained bytes after
   confirmation. Packages depending on the file become unavailable. Exported
   copies and the content-free journal remain; forensic SQLite erasure is not promised.

For agents, request `workspace` in `steed_grant_request.scope.actions`. It is
independent of page reading, text artifacts, app connections and file transfers.
Each call consumes an operation. Use these tools in order:

| Tool | Arguments and behavior |
| --- | --- |
| `steed_workspace_begin` | `name`, `media_type`, `byte_count`, lowercase `sha256`; matching declarations resume the same ID. |
| `steed_workspace_write` | `file_id`, next byte `offset`, canonical `data_base64`; identical written chunks can be repeated safely. |
| `steed_workspace_seal` | `file_id`; verifies declared length/hash and makes bytes immutable. |
| `steed_workspace_list` | Owned metadata, including `generated`/`user_shared` provenance. |
| `steed_workspace_read` | `file_id`, optional `offset`/`limit`; bounded base64 plus `eof`. |
| `steed_workspace_export` | `file_id`, exact `destination`; also requires approved `download` permission for that filename and size. |
| `steed_workspace_stage_extension` | `manifest_id`; pins owned sealed source and reports requested access, granting and running nothing. |

Limits: **8 MiB/file, 50 names and 32 MiB reserved/task**, with 32 KiB decoded
chunks. Uploading the maximum file takes at least 258 operations, so request an
appropriate budget. Names are normalized relative paths, never host filesystem
paths. Deleted names remain reserved; a different revision needs a new name.
Data and metadata commit with content-free events in schema-10 `tasks.sqlite`.
Local bytes are plaintext in this owner-only database. Restart retains bytes but
ends all old task authority; retained-file export/deletion is a trusted user action.
Recovery can also copy explicitly selected sealed workspace files into a fresh
task; see [continuing previous work](#continuing-previous-work).

Export publication checks live permission again, avoids following parent symlinks
and never overwrites. A journal failure before publication prevents export; failure
to record completion can leave the published file, ends task service authority,
and must not be treated as proof of non-export. Sharing and exporting bounded
regular files currently use the Qt thread, so slow local storage can briefly pause UI.
Sending/uploading exported files still requires the corresponding independent
site/app permission. Gmail attachments are not implemented by this workspace.

To stage a custom integration, seal its source files and a UTF-8 JSON manifest
(maximum 16 KiB). For example:

```json
{
  "version": 1,
  "name": "custom-helper",
  "entrypoint": "main.py",
  "files": ["main.py", "README.md"],
  "capabilities": []
}
```

All listed source names must identify distinct sealed files in the same task;
the entrypoint must appear in that list. Up to 20 source files and 20 exact
requested capability records are allowed. Each capability declares `connector`,
`tool`, `effect` and `destination`, following existing app-scope syntax. These
are untrusted requests; the trusted adapter registry determines actual effects.
Duplicate manifest keys/capabilities, traversal, missing/unsealed sources and
changed hashes are rejected. Staging is repeatable by manifest ID and pins source
IDs/hashes. The Workspace panel shows package identity and requested access.
**Staging grants no execution or app authority.** Running a package requires the
separate task and exact-package approvals below. Packages cannot register trusted
tools or obtain credentials. Agents cannot import host paths, delete retained files
or approve their own code.

Meta's [Muse architecture](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)
describes an isolated agent runtime, separate credential services and privileged
brokers for user-data access. The useful pattern for Steed is to keep executable
agent code outside the browser while retaining typed, permission-checked operations
and user-selected data sharing. Steed has not implemented Muse's VM or taint tracking.

### Running an agent-built local worker

The user selected a local worker. Before uploading source, an active agent can
call `steed_worker_capabilities` without a grant. It runs one fixed, bounded
background setup check per browser run, checking the actual isolated Python,
read-only package mount, syscall restrictions and hard limits. It returns
`available`, a sanitized `reason`, requirements and resource limits. Its
`environments` list reports `stdlib` and the optional `documents` environment,
library versions, environment hashes and actual sandbox readiness. It opens
no page, reads no task/user files and grants no execution or account access.
Concurrent callers share the cached check; agents cannot force repeats or supply
probe code. Actual package runs independently recheck setup and permissions.

Users can click **Agents → Task Permissions → Workers → Check Worker Setup** to
retry after installing dependencies or correcting machine policy. The UI stays
responsive, reports readiness or setup guidance, and never exposes exception
details. Restart resets the support snapshot; shutdown ends a pending check.

It requires Linux, a root-owned `/usr/bin/bwrap`
supporting `--disable-userns`/`--assert-userns-disabled` (tested with 0.9.0), enabled
unprivileged user namespaces, `libseccomp.so.2`, and root-owned system Python under
`/usr`. Debian/Ubuntu packages are `bubblewrap` and `libseccomp2`. Browser launch and
dependency checks still use Steed's virtual environment; generated code uses isolated
system Python with its standard library and, when explicitly selected, the separate
maintained document bundle. Missing isolation support fails the run;
there is no host-execution fallback. A separate-kernel VM remains a possible future
deployment option.

1. Request `actions: ["workspace", "worker"]` with a sufficient operation budget.
   Add `artifact` or exact `app` capabilities only when needed. Ordinary task
   approval permits execution requests; it does not approve arbitrary packages.
2. Seal source and its manifest, then call `steed_workspace_stage_extension`.
3. Call `steed_worker_run` with `extension_id` and a stable `request_id`.
   Set `runtime: "documents"` for the installed optional bundle; the default is
   `"stdlib"`. No agent argument accepts dependency paths, URLs or installations.
4. The user sees **Run custom code?**, the pinned source/hash, declared app access
   and limits, plus the environment name and library versions. **Deny** is the
   default. **Run Package** approves that source in that environment. The optional
   checkbox remembers both hashes for this task and current grant; changed code,
   runtime, Python or bundle bytes need fresh approval. Decisions end on restart.
5. Poll `steed_worker_status`, then read/export the generated Workspace files.
   Users can review receipts, reopen pending reviews and cancel runs under
   **Agents → Task Permissions → Workers**. Closing the popup leaves review pending.

`steed_worker_list` returns owned receipts; `steed_worker_cancel` stops an owned run.
States are `awaiting_approval`, `running`, `cancelling`, `succeeded`, `failed`,
`cancelled` and `interrupted`. Repeating a run ID returns its receipt without
executing again, including failures and cancellation; changed package/runtime inputs are
rejected. Restart interrupts unresolved records without replay or restored grants.
Receipts describe script completion; external app outcomes have separate Apps
receipts and can remain uncertain after cancellation. Use a new reviewed package
and run ID for an intentional retry after inspecting existing effects.

Entry points must be `.py`. Source can import the mounted `steed_worker` helper:

```python
import io
import zipfile
from steed_worker import store_file

output = io.BytesIO()
with zipfile.ZipFile(output, "w") as archive:
    archive.writestr("report.csv", "name,value\nSteed,1\n")
store_file("report.zip", output.getvalue(), "application/zip")
```

`read_file(file_id)` reads an owned, explicitly shared/generated task file.
`call(command, **arguments)` supports typed `workspace.begin/write/seal/list/read`,
`artifact.store/list/read` and `app.list_tools/call_tool/status`. Every call still
needs live task authority and spends its existing budget. App calls additionally
need the exact manifest connector/tool/effect/destination; actual effects come from
the trusted registry. Only this run's app request IDs are available through status.
Generated code cannot approve grants/reviews, export host files, launch other
workers, register providers, use arbitrary HTTP, or access browser pages directly.
Credentials stay with Steed's trusted connector adapter.

Each run has **20 seconds wall time, 10 CPU seconds, 512 MiB address space,
32 MiB scratch, 8 MiB per scratch file, 64 file descriptors and 512 broker calls**.
There are at most two concurrent local workers, one per task, and fifty receipts
per task. Workspace file/byte quotas also apply. Package and helper mounts are read
only; home/profile/state/keyring/session-bus directories and parent environment
are absent. Network/PID/user namespaces are isolated. Before importing generated
code, an irreversible seccomp allowlist blocks sockets, subprocess/thread creation
and namespace changes, and hard resource limits are installed.

The [Bubblewrap maintainers](https://github.com/containers/bubblewrap/blob/main/README.md)
explain that callers define the isolation policy; Steed supplies those restrictions
and tests actual blocked operations. Kernel isolation shares the host kernel and
is not a VM. No package dependencies, shell/build tools or direct network are
available. Custom integrations can compose existing approved adapters; adding
new external services still needs a trusted adapter/credential policy.

Cancellation, task completion, revocation, expiry, source deletion and shutdown
stop the worker. Queued broker operations recheck authority immediately before
dispatch. Scratch and materialized source are removed after termination; abrupt
host crashes can leave private temporary directories. Retained outputs stay in
Workspace until user deletion. User code's stdout is reserved for the bounded
broker protocol; stderr is bounded and discarded. Receipts/events omit code,
file contents, stderr and credential text.

### Worker dependency decision

The user selected the optional maintained bundle. **`documents-v1` is implemented**:
[ReportLab 5.0.1](https://pypi.org/project/reportlab/5.0.1/) for PDFs,
[openpyxl 3.1.5](https://pypi.org/project/openpyxl/3.1.5/) for XLSX, and
[Pillow 12.3.0](https://pypi.org/project/pillow/12.3.0/) for images. Its pinned
dependencies are charset-normalizer 3.5.2, et-xmlfile 2.0.0 and defusedxml 0.7.1.
`worker_bundle.lock.json` records exact wheel URLs, sizes and hashes;
`requirements-worker.txt` provides the corresponding hash lock. Browser dependencies
and basic browsing remain unchanged; do not install that lock into Steed's venv.

Run once from the repository root:

```bash
.venv/bin/python install_worker_bundle.py
```

The command downloads only the locked wheels using pip's isolated configuration,
`--require-hashes`, `--only-binary=:all:` and `--no-deps`. It atomically publishes
them in the git-ignored `.worker-bundles/documents-v1/` directory, then tests real
PDF/XLSX/PNG generation inside Bubblewrap. Repeating it verifies the existing set.
There are no host package imports, setup hooks or source builds. For offline setup,
add `--wheel-directory /path/to/exact/wheels`. `--destination` is for packaging/tests;
Steed itself always discovers its repository's `.worker-bundles` directory.

Restart Steed or click **Task Permissions → Workers → Check Worker Setup**.
An agent calls `steed_worker_capabilities`, checks the `documents` entry's
`available` flag, then uses `runtime: "documents"` with `steed_worker_run`.
For example, a reviewed package can save a PDF with:

```python
from io import BytesIO
from reportlab.pdfgen import canvas
from steed_worker import store_file

output = BytesIO()
pdf = canvas.Canvas(output)
pdf.drawString(72, 720, "My report")
pdf.save()
store_file("report.pdf", output.getvalue(), "application/pdf")
```

Each run verifies wheel hashes off the Qt thread and extracts verified snapshots
into a separate read-only `/dependencies` mount. Python uses `-I -S` and an explicit
dependency path; `.pth` startup hooks stay inert. The same syscall/resource policy
applies to native Pillow code. The only added `ioctl` request is `FIOCLEX`, which
CPython needs to mark temporary-file descriptors close-on-exec; other requests
remain denied. Dependencies get no direct network, home, credentials or subprocesses.

The environment hash includes system Python/architecture, trusted worker code,
the entire maintained lock and selected wheel hashes. Receipts and remembered
decisions bind that hash to the source package and live task grant. An environment
change before approval is rejected; tampered wheels fail before generated code or
broker calls. Missing or failing document support leaves stdlib workers available.
Schema 13 preserves legacy receipt history, labels it stdlib with an unknown old
environment, and discards legacy package approvals; it never restores execution.

The maintained wheel selection covers CPython 3.10–3.14 on Linux glibc 2.28+
x86-64/ARM64. Actual native-library acceptance was on CPython 3.12/x86-64 here;
other selections need their own machine checks. Unsupported platforms fail closed.
The existing 20-second/512-MiB/32-MiB-scratch budget still applies, so this is for
small document generators, not large spreadsheets or unlimited image processing.

Updates must be maintained changes to the versioned lock, with reviewed hashes,
real sandbox acceptance and fresh approvals. To uninstall, stop workers/Steed and
remove only `.worker-bundles/documents-v1/` (make its directory writable first);
user Workspace files/receipts are independent and remain intact. Agents cannot
install dependencies. Arbitrary per-task dependencies/build tools and reusable
integration installation remain separate work. Google registration stays deferred.

For implementation background, pip's
[secure installation guidance](https://pip.pypa.io/en/stable/topics/secure-installs/)
documents hashed binary-only dependencies. Hashes identify selected bytes, not
trustworthiness; isolation continues to apply.

### Agent-designed connector recipes

Reviewed recipes are the first implemented stage of agent-designed providers. An
agent describes a bounded REST integration in JSON; Steed interprets it through
its trusted connector broker. Agents never receive account credentials or install
Python in the browser. Recipes support native OAuth/PKCE or bearer-token authentication,
fixed collection paths, resource reads and bounded cursor pagination. Version 2 adds
reviewed create/send POST tools and optional exact-operation read probes. Provider-specific
edits and multi-step workflows remain later milestones. The goal
is the same account and permission experience as Steed's built-in Google provider.
Shared Google registration remains deferred independently.

To add a service:

1. Ask an agent to design a recipe from the service's official API documentation.
   It calls `steed_recipe_capabilities` for the supported format and limits, requests
   `workspace` and `connector` permission and uploads/seals the JSON. Call
   `steed_recipe_validate` first to get grammar diagnostics and compiled domains,
   account selectors, tools and destinations. It makes no network requests, opens
   no popup and installs nothing. Correct invalid source in a new sealed file,
   then call `steed_recipe_propose` with `file_id` and a stable `request_id`.
   Successful preflight is not approval or verification of provider semantics.
   Alternatively, choose **Agents → App Connections → Add Connector Recipe…** and
   select a local JSON file.
2. Review **Add this service connection?**. It shows the actual HTTPS domain,
   identity endpoint, tool paths, selected returned fields and exact source hash.
   OAuth recipes also show both authorization/token endpoints and every scope.
   Check the actual service, declared effects and payload bindings against its API documentation;
   a method or declared effect is not proof of what that service does. Click **Add Connection Recipe** or **Deny**.
   Closing leaves an agent proposal pending. Reopen it in
   **Task Permissions → Connector Recipes → Review Connection Recipe…**.
3. Select the installed service in **App Connections**, choose its tools, and click
   **Connect**. For bearer recipes, enter that service's API token in the password
   field. For OAuth recipes, import a native client JSON once under **Set Up App
   Access…**, then sign in through your system browser. Future connections reuse
   client setup from the keyring. Steed verifies the account ID/label at the reviewed
   identity endpoint. Credentials are never workspace inputs.
4. Enable the account's **Read**, **Write** or **Send** switches if agents should
   request those tools without a permission popup for every app-only task.
   Recipe creates/sends still require an exact payload approval for every action. Each task still requests exact tool/resource
   destinations and gets normal budgets, cancellation and retained receipts.
   With switches off, approve each task separately. Recipe installation itself
   grants neither account access nor task permission.
5. Finish the recipe-design task and start a fresh task to use the connected
   account: Steed's task grants are fixed, so installation does not silently widen
   its existing grant. Discover the account/tool via `steed_app_list_tools`, then
   use `steed_app_call_tool` with an exact resource ID, or literal `account` for a
   fixed collection path, as `destination` and a stable
   `request_id`. Poll `steed_app_status`; the normal receipt keeps selected JSON
   fields and replay returns the same receipt without repeating the HTTP request.
   For creates/sends, request both `app` and `artifact` permission, store the draft
   with `steed_artifact_store`, and pass its owned `artifact_id`. Review **Review App
   Action**: account, exact URL, destination and **Request Payload**. Approve once
   with **Create This Item** or **Send This Message**, or choose **Deny**. The
   **Draft** tab displays the original text. Only the trusted browser UI can approve.

The [example recipe](examples/connector_recipes/notes.json) uses a synthetic
`.example` domain; it is a format reference, not a working external account.
The [OAuth collection example](examples/connector_recipes/notes-oauth.json) is
also synthetic. Version 1 declares one exact public HTTPS API origin, authentication,
an identity path with JSON pointers for subject and label, and 1–10 named GET tools.
A tool path is fixed, with literal destination `account`, or includes one complete
`{destination}` segment; resource IDs are single,
bounded ASCII segments, never URLs or queries. Each response selects 1–10 labeled
JSON pointers (objects and array indexes only). Source cannot contain credentials,
custom headers, executable expressions, arbitrary requests or API request bodies.
OAuth token forms are built exclusively by trusted Steed code for its exact reviewed
token endpoint, never invoked as agent tools.
Recipes are at most 16 KiB; API responses at most 128 KiB and retained tool results
at most 16 KiB. Each task retains at most 20 proposals with three pending at once.
A catalog retains at most 50 versions and is limited to 2 MiB.

Native OAuth recipes use this authentication object (all endpoints/scopes appear
in the installation popup):

```json
{
  "type": "oauth_pkce",
  "authorization_endpoint": "https://auth.fixture.example/authorize",
  "token_endpoint": "https://auth.fixture.example/token",
  "scopes": ["profile", "notes.read"]
}
```

Register a native/desktop client with the actual provider and supply a user-owned
JSON file containing `{"client_id":"YOUR_NATIVE_CLIENT_ID"}` and optionally
`client_secret`. Client files cannot change recipe endpoints. No registration or
credentials are provided by the synthetic example. The provider must support PKCE
S256, authorization-code exchange and an ephemeral loopback
`http://127.0.0.1:<port>/callback`. Steed opens the external system browser and
validates callback Host/path/state; it does not reuse a browser cookie session for
API authorization. It requests all reviewed scopes, with no Google-specific
`prompt` or `access_type` parameters. Provider-specific parameters, discovery,
device flows and hosted-only callback services are unsupported; bearer setup
remains available. See [native-app OAuth](https://www.rfc-editor.org/rfc/rfc8252.html).

The trusted worker verifies granted scopes and token lifetime, binds credentials
to exact recipe/source/account and saves access/refresh tokens plus client setup
only in Secret Service. Refresh is serialized per connected account, verifies
identity and persists rotated credentials before publishing data. The first use
after restart refreshes again when refresh access exists. A service that grants
only an access token works until expiration, then requires reconnection. Turning
access off, disconnecting or removing the source denies late publication and
credential restoration. Failed keyring saves publish no data. Provider failures
and revoked consent require reconnection rather than automatic operation retries.
The protocol follows the [OAuth authorization/refresh flow](https://www.rfc-editor.org/rfc/rfc6749.html).

Changing the provider in setup clears the selected client file to prevent
accidental reuse across services. Recipe provider names in account UI include the
actual API domain, and tools include the connected account label. Results redact
literal current access/refresh/client-secret values before JSON escaping, including
object keys; this is not a general information-flow/taint guarantee against a
service transforming or disclosing its own credentials.

For a fixed collection path, an optional pagination declaration supports at most
five pages in one approved tool operation:

```json
"pagination": {"cursor": "/next", "parameter": "cursor", "max_pages": 3}
```

The [paginated example](examples/connector_recipes/notes-paginated.json) uses this
format. The first request uses the exact fixed path with no query. Each response's
cursor pointer must exist; `null` or an empty string ends pagination. Otherwise it
must be an ASCII cursor of at most 512 bytes, encoded as the one reviewed query
parameter. Cursor URLs are never followed as destinations. Selected response
fields must be arrays; Steed concatenates each selected array and adds
`_pagination: {pages, more_available}` to the retained result. That output label is
reserved. At the reviewed page limit, `more_available` truthfully reports remaining
data without fetching it or exposing a cursor. This is bounded enumeration, not
an unlimited full-account export or arbitrary page selection.

Identity, catalog, account and cancellation are checked between pages. Repeated or
credential-reflecting cursors, wrong response shapes and oversized combined output
fail without publishing partial data. The transport accepts only the configured
path/parameter and canonical encoded value; extra/duplicate parameters, foreign
paths and redirects fail before credential dispatch. Each API response remains
bounded to 128 KiB and the entire result to 16 KiB. Task receipts count a single
operation with at most the reviewed number of page requests and identity checks.

The HTTPS transport rejects private, reserved, multicast, mapped and mixed DNS
answers, connects to a validated numeric public address without resolving again,
and verifies TLS/hostname against the reviewed domain. It ignores proxy settings,
follows no redirects and makes no automatic retries. Identity is verified again
before each tool read, write preview, write dispatch and reconciliation probe.
Changes to identity, catalog or account state deny entry/results. After write dispatch,
an interrupted or malformed response remains uncertain because the service may have committed.
Requests and account setup run off the Qt thread; agents cannot supply authorization
headers or invoke the trusted installation/account methods.

Each recipe hashes its exact source bytes into a new immutable provider identity.
Even a formatting-only change is a new version with a fresh account and switches
initially off. **Remove Selected Recipe** persists catalog denial first, revokes
matching tasks/saved permissions and disables accounts before deleting local
credentials and forgetting saved OAuth client setup. A failed cleanup cannot restore an old account upon reinstallation.
Existing workspace files, drafts and receipts remain independently manageable.
Catalog and account metadata live beside app settings in
`~/.custom_browser/connector_recipes.json` and `app_accounts.json`; secrets live
only in the keyring. An invalid recipe catalog disables recipe providers while
leaving the built-in Google provider available.

Task storage schema 14 adds content-free recipe proposal receipts. Restart
interrupts pending installations and never repeats them. Deleting a proposed
source or ending its task cancels the review. Catalog publication is idempotent
but separate from SQLite journaling: if journaling fails after installation, the
catalog can retain the reviewed recipe, with no account, credential or task access
created. A new trusted connection step is still required.

Recipe milestone acceptance: **707 regressions passed in 297.745 seconds**;
28 recipe checks passed in 4.567 seconds. SDK/raw clients both completed recipe
review, synthetic connection and selected-field reads over stdio and authenticated
HTTP MCP alongside actual sandbox PDF/XLSX/PNG generation. Startup/render/shutdown
and Ruff/format passed. Logs: `/tmp/steed-recipes-full.log`,
`/tmp/steed-recipes-focused.log`, `/tmp/steed-recipes-mcp-{stdio,http}.log`, and
`/tmp/steed-recipes-startup.log`. Review/settings previews were inspected. Real
service accounts and native desktop interaction remain separate acceptance checks.

OAuth/collection milestone acceptance: **723 regressions passed in 295.840
seconds**; 45 final recipe checks passed in 6.286 seconds, including subsequent
malformed-catalog and immediate UI-refresh fixes. Both external clients completed
bearer and OAuth collection workflows over both MCP transports alongside real
sandbox document generation. Ruff/format passed and review/setup previews were
inspected. Logs: `/tmp/steed-recipes-oauth-full.log`,
`/tmp/steed-recipes-oauth-focused.log`, and
`/tmp/steed-recipes-oauth-mcp-{stdio,http}.log`. Real-provider consent, refresh
compatibility and native desktop interaction remain separate acceptance checks.

Preflight/pagination milestone acceptance: **733 regressions passed in 283.392
seconds**; 55 final recipe checks passed in 6.169 seconds, including a subsequent
long-metadata discovery/registration fix. Both independent clients completed
preflight and bearer/OAuth/paginated reads through both MCP transports alongside
real sandbox document generation. Pagination previews, Ruff/format, doc links,
53-tool discovery and all three examples passed. Startup crashed while concurrent
offscreen processes initialized Vulkan; a standalone retry passed. The cause is
not proven. Logs: `/tmp/steed-recipes-pagination-{full,focused,mcp-stdio,mcp-http}.log`,
`/tmp/steed-recipes-pagination-startup.log` and
`/tmp/steed-recipes-pagination-startup-alone.log`. Native desktop and real-account
acceptance remain separate.

### Agent-designed write boundary

Latest static-header/menu acceptance (October 3, 2026): **938 checks pass in
375.207 seconds**. Both actual SDK and independent clients complete v5 reviewed
plans over stdio and authenticated HTTP MCP, alongside document workers and
persistent library reuse. Exact TLS wire, changed-source/account refusal,
OAuth refresh/paging, source/payload preview and lost-reply/no-replay checks pass.
Startup/render/shutdown passes on Qt 6.11.2. Source/menu previews, Ruff/format,
dependency, whitespace, seven recipe examples and local doc links pass.
Logs: `/tmp/steed-menus-headers-final-full.log`,
`/tmp/steed-headers-mcp-{stdio,http}.log`, `/tmp/steed-headers-startup.log`.
Live provider/native desktop acceptance remains separate.

Packaged service discovery: open **Agents → App Connections → Add a Service…**.
Steed loads bounded JSON recipes from `connectors/recipes/`; choosing a service
opens the same default-Deny source review as importing a recipe. This installs
only the reviewed source. Connect its account, enable its action switches, and
grant the task separately. Each create/send still needs its exact payload review.
Missing or malformed catalog entries are skipped; unavailable or changed selections
require reopening the picker. Discovery reads at most 50 matching regular files,
rejects symlinks and special files, and performs no network requests.

The [starter connector handoff](connectors/HANDOFF.md) assigns service data and
fixtures to another contributor. The reviewed starter branch is now integrated:
the picker offers **Todoist, GitLab.com, Asana, Raindrop.io and Microsoft 365**.
Microsoft 365 uses a supplied delegated token; native OAuth remains deferred.
GitHub (one repository), GitLab (one project) and Zotero (one library) are imported
templates whose placeholders must be replaced before source review. See
[connector setup](connectors/SETUP.md) for exact subsets and installation steps.
Every service still needs account connection and enabled actions; no account is
connected automatically. Synthetic acceptance is complete; live-account acceptance
is pending for all these services. Compatibility proofs remain reference data.
Six catalog/picker regressions passed, including changed-source rejection, safe
file discovery, and installation granting neither account nor task access.

### Reviewed API-version headers

**Recipe version 5** adds a required root `request_headers` object containing
1–4 supported non-secret headers. Versions 1–4 keep their existing grammar.
Version 5 retains nested JSON and sequential plans; use explicit `$bind` objects
for draft/destination bindings rather than version-2 flat string bindings.

| Canonical header name | Accepted value |
| --- | --- |
| `Notion-Version` | A valid date in `YYYY-MM-DD` form |
| `X-GitHub-Api-Version` | A valid date in `YYYY-MM-DD` form |
| `Zotero-API-Version` | A string containing an integer from `1` to `99` |
| `OCS-APIRequest` | The string `true` |

For example, add `"version": 5` and
`"request_headers": {"Notion-Version": "2025-09-03"}` to an otherwise supported
recipe. The [versioned sequential example](examples/connector_recipes/notes-headers.json)
is synthetic; its notes paths are not a Notion connector.
`steed_recipe_capabilities` publishes the accepted names/formats, supported
versions, count limit and `headers_example`. Recipe preflight/list metadata
returns these exact non-secret values.

Source review shows the headers before installation. They apply consistently to
API identity, each read/page, every reviewed POST/plan step and reconciliation.
Payload review includes them alongside the exact URL and source hash. A changed
value creates a different immutable source/provider, so the previous account,
capabilities and payload approval cannot authorize it. Old installed versions
remain usable until disabled; replacing a version does not silently change it.
OAuth authorization and token exchange/refresh never receive these API headers.

Only the four listed names and their constrained values are supported. Other
names, noncanonical casing, duplicate keys, newline/control injection, arbitrary
tokens, authentication/cookie/host/transport overrides and operation-ID headers
are rejected. Credential-derived headers require a separate future authentication
contract; credentials remain in the keyring. Exact-origin/DNS/TLS/redirect/body
checks and per-write previews remain in force. Header syntax acceptance does not
verify a provider's supported version or make a whole service compatible:
identity labels, pagination, POST reads and payload/response shapes still need
separate contracts and acceptance.

### Reviewed identity-label alternatives

**Recipe version 6** adds a required identity field `label_alternatives`: an
ordered list of 1–3 distinct JSON pointers, also distinct from the primary
`label`. The account `subject` remains one exact, validated pointer. For example:

```json
"identity": {
  "path": "/me",
  "subject": "/id",
  "label": "/name",
  "label_alternatives": ["/email", "/id"]
}
```

Only missing, null or empty-string labels advance to the next reviewed pointer,
including a missing/null parent. Invalid types, malformed containers, control
characters, whitespace-only or oversized labels and known credential echoes
fail instead of being hidden by a fallback. An explicitly selected subject
pointer may use the validated numeric-to-string account ID. No label field is
inferred, and no email or ID is used unless the source explicitly lists it.

Source review and preflight/catalog metadata show the alternatives in order.
Account connect, cached authorization, OAuth refresh and paged reads retain exact
subject and resolved-label checks; a changed resolved identity requires reconnect.
Access/refresh tokens and other known exchange secrets must not appear in either
identity value. Alternatives change the source hash/provider, so old accounts,
permissions and reviews cannot authorize the changed source.

Version 6 retains nested requests and sequential plans. Its `request_headers`
field is optional; if present it has the same strict v5 allowlist and limits.
Versions 1–5 keep their own strict grammar and do not accept label alternatives.
`steed_recipe_capabilities` exposes versions 1–8, the alternative limit/policy and
`identity_example`; [notes-identity.json](examples/connector_recipes/notes-identity.json)
is the corresponding synthetic plan, not a live provider integration.

This supports explicitly reviewed optional display-name contracts; for example,
Notion documents that only user `object` and `id` are always present
([official user reference](https://developers.notion.com/reference/user)). It does
not establish provider consent, request semantics or support for POST queries.
Those need separate contracts and real-account acceptance.

Integrated acceptance: **948 regressions pass in 385.504 seconds**. Actual SDK
and independent-client MCP journeys pass over stdio and authenticated HTTP with
v6 reviewed plans, document workers and persistent library reuse. Startup/local
render/shutdown passes on Qt 6.11.2; synthetic sidebar/menu/source previews were
inspected. Ruff lint/format, dependency, whitespace, eight recipe examples and
local document links pass. One HTTP fixture run hit the SDK's default 5s timeout;
its bounded timeout now matches the existing raw client's 15s, and the rerun passes.
Live-provider/native desktop acceptance remains separate. Logs:
`/tmp/steed-sidebar-identity-full.log`, `/tmp/steed-identity-mcp-stdio.log`,
`/tmp/steed-identity-mcp-http-final.log`, `/tmp/steed-sidebar-identity-startup.log`.

**Version 7 typed reads, query inputs and fixed GraphQL documents.** Version 7
keeps every v1-v6 grammar and adds bounded declarative read inputs. A v7 read tool
declares `"effect": "read"` explicitly with `method: "POST"`. Ordinary GET reads
keep the existing grammar:

```json
{
  "name": "search_notes",
  "description": "Search this account's notes with reviewed inputs",
  "method": "POST",
  "effect": "read",
  "path": "/notes/search",
  "destination": "account",
  "inputs": {
    "query": {"type": "string", "max_length": 200},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    "archived": {"type": "boolean", "default": false}
  },
  "request": {
    "text": {"$bind": "input.query"},
    "limit": {"$bind": "input.limit"},
    "include_archived": {"$bind": "input.archived"},
    "cursor": null
  },
  "response": [{"label": "notes", "pointer": "/items"}],
  "pagination": {
    "cursor": "/cursor",
    "parameter": "cursor",
    "max_pages": 3,
    "location": "body"
  },
  "errors": {"pointer": "/error", "code": "/reason", "message": "/summary"}
}
```

- **Inputs are typed and bounded.** At most 8 per tool, lowercase names, and
  `string` (with `max_length` up to 512), `integer` (with ordered `minimum`/
  `maximum`) or `boolean`. An input without `default` is required. Unknown names,
  wrong types and out-of-range values fail before any receipt or HTTP request.
- **The body is fixed by the recipe.** Only `input.<name>` and `destination`
  bindings are accepted in a typed read `request`; draft, credential and
  operation-ID bindings are rejected. The effect stays `read`, so there is no
  payload preview, no operation header, no reconciliation and no automatic retry.
  Literal bodies, empty objects and JSON null are supported. Review the actual
  service endpoint semantics; declaring a read cannot change an external API.
- **POST cursors travel in the body.** `pagination.location` is `body` for typed
  POST reads; ordinary GET reads retain query-string pagination. The named field
  must already exist in the reviewed request. The first body retains its reviewed
  value, and later bodies use the returned cursor. Identity is rechecked
  before every page and a repeated or credential-reflecting cursor fails closed.
- **Application errors are declared, not hidden.** `errors` names the pointer
  that reports a failure plus its code and message. An HTTP 200 body that
  reports an error there fails the read and surfaces the reported code/message
  instead of returning empty reviewed fields. Absent, null and empty error fields
  are ordinary successful responses.

**Fixed GraphQL reads.** A typed read tool may replace `request` with `query`:

```json
"query": {
  "operation": "NoteById",
  "document": "query NoteById($id: ID!) { note(id: $id) { id title body } }",
  "variables": {"id": {"$bind": "destination"}},
  "data_pointer": "/data",
  "errors_pointer": "/errors"
}
```

The document is a reviewed literal in the recipe, at most 4 KiB, and must be a
single named `query` matching `operation`. The pinned
[GraphQL-core parser](https://pypi.org/project/graphql-core/3.2.8/) validates syntax;
the AST is limited to 1,024 tokens, depth 32 and eight declared variables.
Mutations, subscriptions, fragments, schema
definitions, directives and multiple operations are rejected, and agents have no
argument that carries query text. Variables must match the declared names and bind
only the reviewed destination; an empty object works for a fixed viewer query.
A non-empty errors array fails the read and reports up to five bounded
code/message pairs; partial data is never returned as if it had succeeded, and a
missing or null `data` fails rather than yielding empty fields.

**Reviewed POST identity.** `identity` may declare `"method": "POST"` with a
literal bounded `request` body for services that require one (for example a
fixed GraphQL `me` document or a JSON-null identity request). The body accepts no
bindings at all. The same exact
body is used for connect, every authorization recheck, every pagination recheck
and OAuth refresh, and it is pinned to the same reviewed origin and headers.

**Agent surface.** `app.list_tools` publishes each read tool's `inputs` schema,
and `steed_app_call_tool` accepts an optional `inputs` object containing only
those names. Inputs are values, not authority: they cannot change the reviewed
endpoint, effect or destination, they are not part of any permission, and the
existing app permission still governs the call. The stable `request_id` is
fingerprinted over the resolved inputs, so a repeated request ID with different
inputs is rejected instead of silently reusing an earlier receipt. Calls without
typed values retain their earlier fingerprint so old receipts stay retryable.

Source review shows the identity method and body, each typed input with its type,
bounds and default, the exact request bindings, the full GraphQL document and
variables, the application-error pointers and the cursor location.
`steed_recipe_capabilities` reports version 8, the typed-read and GraphQL
policies, the new limits and `query_example`;
[notes-query.json](examples/connector_recipes/notes-query.json) is the same
synthetic source, not a live provider integration. Credential, origin, header,
cancellation, identity-recheck and no-write-replay rules are unchanged. Live
provider acceptance, credential-derived headers, revision guards, transforms and
gated providers remain separate.

**Version 8 continuation and success contracts.** Versions 1–7 retain their
source grammar. A read or identity can declare an exact boolean success rule:

```json
"success": {"pointer": "/ok", "value": true, "required": true}
```

A missing required flag, non-boolean flag or wrong value fails before selected
fields are returned. Optional flags may be absent but must match when present.
Read pagination can declare `"has_more": "/has_more"`. This must be a boolean:
false ends pagination even when the service retains a final cursor; true requires
a nonempty next cursor. Existing page/cursor bounds still apply and truncation
reports `more_available: true`.

A typed POST read can use a distinct reviewed continuation endpoint:

```json
"pagination": {
  "cursor": "/cursor", "parameter": "cursor", "max_pages": 3,
  "location": "body", "has_more": "/has_more",
  "continuation": {
    "path": "/notes/search/continue",
    "request": {"cursor": {"$bind": "cursor"}}
  }
}
```

Only the returned cursor can bind into this continuation body; the path is fixed
on the reviewed origin. The first request retains its own reviewed body, which
need not contain a cursor field when a continuation is declared. Version 8 also
supports body pagination on an exact resource POST path. GraphQL pagination
remains unsupported. Identity, account access, source identity and cancellation
are rechecked before every page. Read POST and write POST routes, including
resource-template overlaps, cannot share a transport path.

The native review displays success/stop rules and the continuation path/body.
`steed_recipe_capabilities` publishes `continuation_example`;
[notes-continuation.json](examples/connector_recipes/notes-continuation.json) is a
synthetic executable example. String inputs may be empty for service root paths;
reviewed maximum lengths and control-character rejection still apply. These
contracts enable provider implementation; they do not establish live-provider
acceptance or turn external write endpoints into reads.

**Version 3 nested JSON templates:** GET tools retain the existing format. POST
`request` can contain nested objects, arrays and JSON literals. Bind draft data
with an explicit single-key object such as `{"$bind": "artifact.content"}`;
supported bindings are `artifact.title`, `artifact.content`, `destination` and
`operation_id`. Plain strings are literals, even when they look like binding
names. Binding substitutes the string without interpreting JSON, HTML, code,
expressions or URLs inside it. Objects containing `$bind` must contain exactly
that supported binding; it is reserved template syntax.

Every write must bind draft content; every send must also bind its one approved
email recipient. Limit templates to 10 top-level fields, 20 fields/items per
object/array, 100 total nodes, depth 8 and a final preview/body of 16 KiB. Numbers
must be finite; integers must fit the exact JSON number range ±(2^53−1). Literal
strings cannot contain NUL. Steed freezes the parsed template and exposes a fresh
copy in recipe metadata. The transport permits nested bodies only for reviewed
nested POST rules; v2 bodies remain flat. User approval covers the actual
serialized body, URL, source hash, account, draft hash and operation ID.

The [nested example](examples/connector_recipes/notes-nested.json) is synthetic.
Nested payloads are the first part of declarative request plans. Version 4 adds
bounded result-dependent POST sequences; version 5 adds the reviewed static
headers above; version 6 adds explicit identity-label alternatives. Arbitrary
query inputs, root-array POSTs, revision guards and response conditions remain future work; provider sign-in and API semantics
still require separate acceptance.

Nested-template acceptance: **774 regressions passed in 299.980 seconds**;
71 final recipe checks passed in 14.370 seconds, including subsequent popup/fixture
refinements. Both SDK/raw clients completed nested creates, lost-reply exact-operation
reconciliation and no-replay checks over stdio and authenticated HTTP MCP alongside
actual PDF/XLSX/PNG workers. Source and payload previews were visually inspected.
Ruff/format and dependency checks passed. Logs:
`/tmp/steed-nested-recipes-{full,final-focused,mcp-stdio,mcp-http}.log`.
These are synthetic account/API checks; live provider/native desktop acceptance
remains separate.

**Implemented: reviewed declarative creates/sends.** Version 1 read recipes remain
compatible. Version 2 may mix the existing GET format with POST tools. The
[create/send example](examples/connector_recipes/notes-writes.json) is synthetic;
it is not a live service integration. `steed_recipe_capabilities` publishes all
supported versions, bindings, limits and examples. Installation review does not
connect an account, enable switches or authorize a task.

A POST tool declares the ordinary name/description/path/response fields plus:

```json
{
  "name": "create_note",
  "description": "Create a reviewed note",
  "method": "POST",
  "effect": "write",
  "path": "/notes",
  "destination": "account",
  "request": {
    "title": "artifact.title",
    "body": "artifact.content",
    "operation_id": "operation_id"
  },
  "idempotency_header": "Idempotency-Key",
  "response": [{"label": "id", "pointer": "/id"}],
  "reconciliation": {
    "path": "/operations/{operation_id}",
    "operation_id": "/operation_id",
    "status": "/status",
    "success": "succeeded"
  }
}
```

- `effect` is `write` (create/add) or `send`. Writes use destination `account` with
  a fixed path, or `resource_id` with one complete `{destination}` segment. Sends
  require `email` and one `email:recipient@example.com` destination; their path is
  fixed and their payload must bind `destination`. Email payload bindings omit the
  `email:` prefix. Recipient lists, display names and header injection are rejected.
- `request` has 1–10 flat fields with bounded ASCII names. Values bind only
  `artifact.title`, `artifact.content`, `destination` or `operation_id`.
  At least one field must use draft content. No literals, nested templates, raw
  JSON interpolation, expressions, cookies, arbitrary headers or credential bindings
  are supported. Trusted JSON serialization preserves quotes/newlines as data.
  The entire serialized payload, including title, operation ID and JSON escaping,
  is limited to 16 KiB; a maximum-size draft may exceed this and be rejected.
- Optional `idempotency_header` accepts `Idempotency-Key`, `X-Idempotency-Key` or
  `X-Request-ID`, populated only with the receipt's generated operation ID. This
  correlates requests; it does not prove that a service deduplicates them.
- Optional `reconciliation` uses one reviewed GET path with a complete
  `{operation_id}` segment. The POST must carry that ID in a bound field or supported
  header. A probe succeeds only when the returned ID exactly matches the receipt
  and the selected status equals the reviewed success value. It returns the same
  selected response fields. Missing results, mismatches, errors and pending statuses
  remain uncertain; absence never proves an action was not applied. The task needs
  a separate exact `read` permission for that tool to call `steed_app_reconcile`.

Every create/send requires the default-Deny payload popup even when account
switches or saved task permissions approve app access. Review binds source hash,
account/subject, exact destination, operation ID, draft hash, title and serialized
payload. Steed rechecks those inputs and live authority before the single POST.
Pending preview content is scrubbed after approval/denial/cancellation and on restart;
receipts remain durable. Agents and local workers cannot approve their own writes.

The trusted transport permits POST only on reviewed API path templates with exact
JSON/authentication headers and the configured operation header. The OAuth form
exchange remains separate and pinned to its reviewed token endpoint. TLS, public
numeric-address pinning, response limits, no proxies and no redirects apply to both.
Only JSON replies with HTTP 200/201 are accepted; the selected fields represent the
service's acknowledgement, not proof of eventual message delivery. Other statuses,
missing/oversized fields, disconnects and cancellation after HTTP entry remain
uncertain. A same-ID replay returns its receipt and never makes a second POST.
No automatic write retry occurs, consistent with [HTTP's non-idempotent retry
boundary](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2).

The user chose reviewed agent recipes instead of requiring a maintained Steed adapter
for each service's write support. Actual API behavior still depends on the service;
verify its semantics during source review. New source versions get fresh accounts
and switches. Real-account effects require their own explicit user tasks. Shared
Google registration remains deferred. Edits/replacements need a revision/conditional
write contract; delete/spend and executable connector adapters are outside this
implemented format.

Create/send acceptance: **755 regressions passed in 297.778 seconds**; 63 final
recipe checks passed in 12.762 seconds, including a subsequent shared-POST-path
operation-header fix. Both SDK/raw MCP transports passed, standalone startup/render/
shutdown passed, and Ruff/format, whitespace, four example parses and local doc links
passed. Source/create/send popup previews were inspected. Logs:
`/tmp/steed-recipe-writes-full.log`, `/tmp/steed-recipe-writes-final-recipes.log`,
`/tmp/steed-recipe-writes-mcp-{stdio,http}.log`, and
`/tmp/steed-recipe-writes-startup.log`. Real-provider and native desktop acceptance
remain separate. Focused tests
cover owned drafts, recipient scope, forged/missing approvals, exact preview bytes,
OAuth refresh/form separation, overflow, source/account identity changes, revocation
before/after dispatch, restart/non-replay and exact-operation probes. Both SDK/raw
clients have completed reviewed creates/sends and timeout/reconciliation through
stdio and authenticated HTTP, alongside reads and real sandboxed document workers.
No real accounts or desktop secrets were used.

### Sequential connector request plans

**Implemented: recipe version 4 sequential POST plans.** Version 1–3 recipes remain
compatible. A v4 POST tool uses the v3 nested request format for its first step;
optional `plan` contains 1–4 following POST step objects, giving 2–5 total steps.
Each following step has `name`, `description`, `method`, `path`, `request`,
`response` and optionally `idempotency_header`. It inherits the parent's declared
effect and destination. Reads, mixed effects, arbitrary code, loops, branches,
reconciliation and revision edits are outside this first subset.

The [sequential note example](examples/connector_recipes/notes-plan.json) creates a
note, then appends a comment using the first response's selected ID. Bind previous
selected fields with `{"$bind": "step.create_note.id"}`. Step and referenced field
names must be lowercase identifiers; forward/unknown references are rejected.
Outputs bound into JSON must be strings of at most 4096 UTF-8 bytes without NUL.
At most one full path segment can bind either `{destination}` or an earlier field
such as `{step.create_note.id}`; that field must pass the existing resource-ID
validator, never supply a URL, query, slash or traversal. Normal nested payload
limits and draft-content/recipient requirements apply to **every** mutation.

Calling the existing `app.call_tool` creates a parent receipt and a journaled child
receipt for each step. Each child has its own operation ID, payload hash and
default-Deny popup showing the actual URL/account/draft plus **Step N/M**. Previous
results are frozen into its payload before review. Approval is only for that child;
the next step requires another materialized preview. Source/account/live grants,
owned artifact and durable review hash are checked again before dispatch. Sequencing
and SQLite stay on the Qt owner thread; workers receive only the selected step.

Poll the parent with `app.status`. Its `reference` identifies the current child
while active; its JSON `text.steps` lists child request/operation IDs, states and
payload hashes. A completed child can be inspected through `app.status` under the
same exact task grant. Successful plans report `succeeded`; if later work is denied
or cannot run after a successful mutation, the parent reports `failed` with its
successful/failed child history. An uncertain child makes the parent `uncertain`.
Completed effects are not rolled back. No later step runs after uncertainty,
denial, cancellation, revocation, timeout or failed preparation, and repeated parent
request IDs return their existing receipt instead of starting again.

Task storage schema 15 retains parent/child links without storing request bodies in
the event journal. Restart interrupts active plans, scrubs pending previews and
preserves partial/uncertain receipts without resuming. Plan children reserve slots
within the existing 50-receipt task limit. Clearing a parent's results also clears
its child results and stops pending work; clearing a child stops its active plan.
Confirm uncertain effects manually; this first plan format has no automatic
retry or reconciliation.
Native sign-in and real-provider semantics remain separate acceptance checks.

Plan milestone acceptance: **791 final regressions passed in 284.704 seconds**;
88 final recipe checks passed in 19.453 seconds. Both independent SDK/raw clients
completed reviewed create→append sequences and uncertain-first-step stops over
stdio and authenticated HTTP MCP alongside real PDF/XLSX/PNG sandbox workers.
Four unchanged starter compatibility checks passed. Legacy migration, receipt
reservation, hostile/unknown bindings, payload hash changes, timeout/late reply,
revocation, result clearing and restart/partial-completion paths are covered.
Standalone startup/render/shutdown, Ruff/format, whitespace, 52 local link targets
and all six recipe examples passed. Source and Step N/M previews were visually
inspected. Logs:
`/tmp/steed-plans-{final-full,final-recipes,mcp-stdio,mcp-http,startup}.log`.
No live provider accounts or native desktop acceptance were tested.

### Complex provider workflow decision

The next architectural boundary is how agent-designed connectors handle provider
logic beyond a single reviewed JSON request. Steed's Google implementation uses
provider-specific destination checks, multipart creation, revision-aware edits
and marker-based reconciliation. Other workflows may need dependent mutations. Recipes from
version 4 represent nested payloads and bounded result-dependent POST sequences
with partial completion receipts. Read steps, provider-specific transformations,
revision-guarded edits and read-only per-step reconciliation remain future work.

The chosen design keeps source declarative: trusted Steed code compiles templates
and ordered steps. A parent owns a durable child receipt for each mutation and
each fully materialized payload needs review. Later writes stop after uncertain
completion. Credentials stay in the broker; generated workers can prepare drafts
but cannot execute arbitrary connector code there. Future read-only probes should
reconcile exact step IDs without automatically continuing a stopped plan.

The alternative is a broker-backed sandboxed adapter package: generated code computes
provider logic in the local worker, while typed broker calls own credentials,
authority, payload approvals and per-step receipts. That offers wider protocol and
transform support but adds an executable adapter trust boundary and ongoing sandbox
compatibility work. Existing attachment workers have no connector credential or
network access. No generated Python would run inside the browser under either design.
The user chose declarative request plans first on October 3, 2026. Implementation
continues in that direction. See the [connector implementation handoff](connectors/HANDOFF.md)
for independently owned provider recipes and verified current-runtime subsets.
The framework work owns shared catalog discovery and plan execution; service
contributors do not edit the shared broker or UI.

### Shared Google registration decision

**Deferred by the user.** Shared registration remains a goal; local engineering
continues using the existing developer-owned setup. Revisit this at distribution
time rather than requiring a registration decision for each engineering milestone.

To give new installations the same **Connect → sign in → toggle access** flow,
Steed needs a maintained shared Google Desktop OAuth client. The current remembered
developer-owned setup already gives subsequent connections that flow. These are
two distribution choices:

| Choice | User experience | Ownership |
| --- | --- | --- |
| Personal/developer setup | Import a client once, then sign in and reuse switches | Each user maintains their own Cloud project |
| Shared Steed registration (recommended for broader distribution) | Bundled public desktop client configuration; sign in and choose switches | Steed's publisher maintains the project, branding, support and verification |

The implementation proposal is to bundle only the desktop app registration,
retain developer import as an advanced option, and reuse the existing external
browser PKCE/loopback flow, desktop keyring and account switches. User access/refresh
tokens remain local; this needs no Steed cloud credential server and no broader
Google scopes. An installed app's client configuration is not a private user token;
Google's [native-app OAuth guidance](https://developers.google.com/identity/protocols/oauth2/native-app)
describes the installed-app model.

Before publication, the owner must provide a Cloud project, support contact and
public homepage/privacy identity, then complete the applicable production process.
Google's [brand verification guidance](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)
describes domain ownership and branding requirements. `gmail.send` is classified
as sensitive in the [Gmail scope list](https://developers.google.com/workspace/gmail/api/auth/scopes)
and public production access requires the applicable
[sensitive-scope review](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).
Personal-use exceptions exist; they do not establish public distribution readiness.

No shared registration, public policy or external project change has been made.
The decision is whether to keep Steed personal for now or accept maintaining that
publisher identity. Once chosen, client selection, onboarding and synthetic tests
can be implemented before any actual registration/publication.

### Browser-managed application operations

The gateway and recovery rules are implemented and tested against synthetic
document/email MCP services and Google API fixtures. Google account setup and
trusted Docs/Gmail mappings are bundled; [connect an account](#google-app-connections)
to enable them. `steed_app_list_tools` returns an empty list until accounts are configured.
It requires an active task, but no grant or page. External agents cannot register
connectors, choose endpoints or change credential configuration.

Trusted browser code can supply a `ConnectorRegistry` to `TaskService`, registering
immutable `ConnectorTool` definitions with a fixed effect (`read`, `write`, `send`,
`delete`, `spend`), description, artifact requirement, worker `execute` callback and
optional read-only `reconcile` and destination-validation callbacks. Remote tool annotations do not set risk;
the mock server deliberately claims writes are read-only to test this distinction.
Adapters receive a typed destination and owned draft, not arbitrary client JSON,
URLs, credential paths or recipient arrays. Concrete adapters must validate and
map provider-specific destinations and fields; trusted Python adapters are not
an OS sandbox. They must enforce their own bounded network timeouts.

Discover configured tools first, then request explicit permission, for example:

```python
scope = {
    "actions": ["artifact", "app"],
    "connector_permissions": [
        {
            "connector": "documents",
            "tool": "create",
            "effect": "write",
            "destination": "approved-workspace-id",
        },
        {
            "connector": "email",
            "tool": "send",
            "effect": "send",
            "destination": "approved-recipient@example.org",
        },
    ],
}
```

Each of up to 20 entries binds an exact connector, tool, trusted effect and
destination. Wildcards, unknown tools, risk downgrades and missing required draft
permission are rejected before approval. The popup displays each effect and
destination, disclosing external changes, retained results and cancellation limits.
Browser-page/profile permissions and app accounts are separate; app authority does
not enable website scripts or turn browser cookies into provider credentials.

Store an immutable draft, then call `steed_app_call_tool` with the matching tool,
destination, `artifact_id` when required and a stable `request_id` of 1–64 letters,
digits, underscores or hyphens. The browser assigns a separate globally unique
`operation_id` for provider deduplication/lookup; identical local IDs in different
tasks cannot collide there. Reusing a request ID with matching inputs returns its
receipt, including uncertain results, without redispatch or extra operation cost.
Changing its inputs is rejected. Deleting the draft before dispatch prevents sending
the queued copy; deleting it after dispatch cannot retract external effects.

Poll `steed_app_status`. States include `requested`, `dispatched`, `succeeded`,
`not_applied`, `failed`, `cancelled`, `uncertain`, and `reconciling`. Calls/status
queries spend operation budget; owned cleanup does not. A confirmed result comes
from the trusted adapter's observed response, not simply from dispatch. Provider
exception text is omitted from receipts/events. Outputs are bounded to 16 KiB
UTF-8 plus a reference of at most 2,048 characters. Text/reference stay out of
activity events; they are retained as local plaintext in private `tasks.sqlite`.

There are at most 50 receipts per task, one running worker per task and four
globally. A 30-second timeout requests cooperative cancellation and reports an
uncertain write or failed read. A worker still running retains its capacity slot;
another write or reconciliation cannot race it. `steed_app_cancel`, task endings,
revocation, expiry and shutdown stop queued operations and request cancellation
of dispatched ones. They do not promise rollback. A late observed response may
update the user's receipt after task cancellation, without restoring client access.

After an uncertain operation's worker ends, `steed_app_reconcile` invokes only the
trusted receipt-lookup callback with the original provider operation ID and
destination, without draft bytes or a second write. No automatic write retry occurs.
Initial requests and dispatch gates commit before adapter entry; failed evidence
writes disable access. Restart marks unresolved dispatched writes/reconciliation
uncertain and queued requests cancelled; it neither replays calls nor restores
grants. Recovery stays observable even if a provider acknowledgement was lost.

In **Agents → Task Permissions → Apps**, review owned destinations, states, request
IDs, references and result text, cancel a running operation, or reconcile an
uncertain result while its original task/grant remains active. Finished/interrupted
receipts remain readable by the user. Shared archived receipts can also be checked
under a fresh read-only grant; see [archived receipt checks](#checking-an-archived-app-receipt).
User-controlled [result clearing](#clearing-saved-app-results) is implemented;
automatic retention policies and additional real providers remain follow-ups.
A client cannot adopt another task's ledger.

### Clearing saved app results

Open **Agents → Task Permissions → Apps** and select a receipt. **Clear Saved Result**
removes its saved provider text and link after confirmation. A **Previous** row
clears the original receipt and its shared recovery summaries. **Clear This Task’s
Results** clears the selected task's own receipts (up to fifty); Previous rows
belong to their original tasks. The confirmation defaults to **No**.

Outcomes, accounts, destinations, request/operation IDs, fingerprints, draft IDs
and the append-only journal remain available for recovery and duplicate-write
protection. The preview shows when the result was cleared; owned MCP receipts
include `result_cleared_at`. Clearing and scrubbing shared summaries commit in one
transaction. Repeating a clear is a no-op. Only trusted browser UI can clear
results; agents have no clearing tool or management authority.

Clearing can also apply to queued/running operations or uncertain receipts.
Operations continue under their existing permissions and their outcome can still
be recorded. A persistent marker prevents later workers or reconciliation from
restoring text/links on the cleared receipt, including after restart. For an
uncertain receipt, a new separately approved read-only check owns its own result;
clear that receipt too if you want to remove its saved text. Completed receipts
with cleared links retain their outcome; use the provider UI to locate their data.
Matching historical writes remain blocked.

Provider-side files/emails, drafts and independent check receipts retain their
own lifetimes. Previously exported/copied data and backups are outside this local
removal; this is not a secure disk erase. Automatic expiry and broader retention
policies remain future work.

### Google app connections

**Agents → App Connections** now provides persistent switches for each connected
account: read/check results, create/add content, direct replacements, sending,
and any supported delete/spend tools. Sign-in alone grants no task authority.
Turn on only the actions you want to allow without another app-task prompt.
Reviewed replacements still show their before/after proposal; direct replacements
have a separate switch. Gmail remains send-only, with no inbox access.

These choices apply to all connected agents across browser profiles, but only
automatically approve **app-only tasks** using private task profiles, optionally
with text-draft permission. Each request keeps exact tools/effects/destinations,
at most ten minutes/1,000 operations/1,000 requests and one session. Browser sites,
shared cookies, binary workspace files and code execution require their own
approval. Follow-ups containing recovered material continue to require fresh task
approval. Larger or combined requests still show the ordinary permission popup.

Turning a switch off clears matching remembered approvals and stops matching
active tasks, including manually approved ones. Existing external changes remain.
Disconnecting clears all switches for that immutable account identity. Reconnecting
creates a new identity with switches off. Choices persist in schema-11 task storage;
restart ends old task authority. A newly added tool does not inherit a previous
switch: the UI shows partial access until you explicitly enable the group again.
The browser's trusted registry determines actual effects; remote tool annotations
or generated package declarations cannot change these choices.

The design borrows the separation of sign-in and persistent read/write policy
from [Muse's connector architecture](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse).
For example, Dropbox's [official Muse setup](https://blog.dropbox.com/topics/company/put-dropbox-project-context-to-work-meta-muse)
describes selecting the connector and signing in to authorize access. Steed's
Google flow is similarly reusable after its one-time app registration, but a
production shared OAuth client is still needed to remove Cloud setup for new users.

Google is the first real provider adapter. The same account store, provider factory,
approval gateway and receipt format can support Microsoft 365 and other trusted
MCP/API adapters. Those additional adapters are not bundled yet; a synthetic second
provider verifies that account setup and task policy do not depend on Google.

To configure Google:

1. Install the optional dependency in Steed's virtual environment:
   `.venv/bin/python -m pip install -r requirements-apps.txt`.
   Your Linux desktop needs a working Secret Service keyring, such as GNOME
   Keyring or a Secret Service provider you already use. Steed has no plaintext
   credential fallback. This is separate from the website password vault.
2. In your Google Cloud project, enable **Google Drive API** and **Google Docs API**
   for documents and/or
   **Gmail API** for sending. Configure the OAuth consent screen; for an external
   testing app, add your Google account as a test user. Follow Google's
   [consent setup](https://developers.google.com/workspace/guides/configure-oauth-consent).
3. Create an OAuth client with application type **Desktop app** and download its
   client JSON. Use a project/client you control; Steed does not bundle a shared
   production client. The import ignores endpoints supplied by the JSON and uses
   fixed Google HTTPS endpoints. Google's [desktop OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app)
   describes client creation and the system-browser/loopback flow.
4. Open **Agents → App Connections**, select **Google Docs and folders** and/or **Send
   Gmail**, expand **Set Up App Access…**, and choose the JSON once. **Open Google
   Setup** links to the Cloud client page. Click **Connect Google**. Steed saves
   the app configuration in your desktop keyring for subsequent connections;
   after this first setup, just select capabilities and click **Connect Google**.
   An existing connected installation can reuse its saved client configuration
   without the original file. Unlock the desktop keyring
   if prompted. Finish consent in your system browser, select the intended Google
   account, then return to Steed. Use a system browser supported by Google's OAuth
   flow; if Steed is your default and authorization is refused, temporarily select
   Firefox/Chrome as your default browser for setup. Steed does not extract cookies
   from your tabs or perform this consent inside its embedded WebEngine.
5. The connected account and enabled tools appear in App Connections. Enable
   local agent access as usual. Agents discover the account-specific connector
   through `steed_app_list_tools`, request exact app permissions, retain a draft
   with `steed_artifact_store`, and send its ID to `steed_app_call_tool`. Connecting
   an account alone never approves a task.
6. Enable the account access switches you want to reuse. Matching bounded app-only
   tasks proceed without a popup. Otherwise, approve the task popup only for the
   account, tool and destination you intend.
   Review results in **Agents → Task Permissions → Apps** and drafts in **Artifacts**.
   **Disconnect Account** disables the connection, denies pending
   requests and revokes active grants using it before attempting keyring deletion.
   If the keyring is locked, unlock it and retry removal; the connection stays disabled.

**Forget Saved App Setup** removes the reusable registration from the keyring;
existing accounts stay connected, but adding another account requires importing
the client JSON again. Disconnecting an account retains this reusable setup.
Importing a client saves configuration even if OAuth consent is later cancelled;
that configuration grants no account or task access. The setup controls collapse
after a successful connection. A brand-new installation still requires the
user-owned Cloud registration above: Steed has no shared production OAuth client.

The requested OAuth scopes are `openid` and `email` for account identity, plus
`drive.file` for documents and/or `gmail.send` for sending. Google defines
[Drive file access](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
as app-created/selected file access and [Gmail send](https://developers.google.com/workspace/gmail/api/auth/scopes)
as send-only access. Steed does not request inbox-reading or whole-Drive access.
Google external apps in Testing issue refresh tokens that expire after seven days
for these non-basic scopes; reconnect when needed. See Google's
[token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).

| Tool | Exact destination | Behavior |
| --- | --- | --- |
| `google_<connection-id>.create_document` | `drive:root` or `drive:folder:<id>` | Create a new Google Doc in My Drive or one exact Steed-created folder; draft title is its name and content is imported as plain text. |
| `google_<connection-id>.create_folder` | `drive:root` or `drive:folder:<id>` | Create one folder; the immutable draft title is its name. The required draft body is unused and is never uploaded. Result text contains the new exact destination. |
| `google_<connection-id>.inspect_folder` | `drive:folder:<id>` | Read that exact Steed-created folder's identity and name, without a draft or a listing of its contents. |
| `google_<connection-id>.inspect_document` | `doc:<id>` | Read one exact Steed-created Doc's title and tab identities, without returning body text or requiring a draft. |
| `google_<connection-id>.read_document` | `doc:<id>:tab:<tab-id>` | Read bounded plain text from one exact tab; no draft or write access is required. |
| `google_<connection-id>.append_document` | `doc:<id>:tab:<tab-id>` | Append the immutable draft body to one exact tab; its title is unused. Existing text is retained. Adds an operation named range visible to collaborators. |
| `google_<connection-id>.replace_document` | `doc:<id>:tab:<tab-id>` | Prepare a complete plain-text body replacement and show a before/after popup. Every change needs trusted user review, including under saved task grants. |
| `google_<connection-id>.replace_document_direct` | `doc:<id>:tab:<tab-id>` | Replace the complete plain-text body without another content popup. Requires its own exact named permission; reusable approvals are supported. |
| `google_<connection-id>.send_email` | `email:recipient@example.com` | Send one plain-text email; draft title becomes its subject and content becomes its body. |

Use the actual connector ID returned by discovery, not the placeholder above.
Mailbox destinations accept a single ASCII address with a lowercase domain;
display names, recipient lists and header injection are rejected before approval.
Local-part case, dots and plus tags remain exact. Arbitrary existing Drive files,
shared drives, editing arbitrary existing documents, rich-text writes, attachments and
mailbox reads are not implemented. Markdown drafts remain literal plain text. Drive
[upload conversion](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
creates the document and imports its text in one multipart request; no second
content write or broad Docs permission is needed.

To organize task output, approve `create_folder` on `drive:root`, retain a draft
whose title is the desired folder name, and call the tool with that draft ID.
Copy the `drive:folder:<id>` destination from its receipt. Request a separate
`create_document` or `create_folder` grant for that exact destination; a root
grant never includes its folders. Before a folder write, Steed checks that the
parent is untrashed, owned by the connected Google identity, writable, outside
shared drives, and carries Steed's account-bound creation metadata. Shortcuts and
unmarked folders are rejected before the mutation. Inspection reveals only the
approved folder's name and identity; it does not enumerate Drive. Reconnecting
the same Google identity and OAuth app retains folder eligibility but requires a new connector
grant. Editing/removing the private metadata can make a folder unavailable.
Google’s [folder API](https://developers.google.com/workspace/drive/api/guides/folder)
creates folders as metadata-only files; Steed performs one creation request.

For collaborative document updates:

1. Take `doc:<id>` from a successful `create_document` receipt. Request
   `inspect_document` with `read` access to that exact destination. Its result
   lists tab titles and exact `doc:<id>:tab:<tab-id>` destinations, including
   nested tabs. It returns no body text.
2. Request `read_document` with `read` access to the chosen tab to see its current
   plain text. Output is capped at 8 KiB of UTF-8 text and 16 KiB of serialized
   result; `truncated` reports shortening and `incomplete` reports omitted tables,
   non-text elements or inline suggested edits. Provider JSON responses have a
   separate 128 KiB limit. These are saved app results and can be cleared in
   **Task Permissions → Apps**.
3. To add content, request `append_document` with `write` access to that exact
   tab plus `artifact` permission. Store the intended body as an immutable draft
   and call the tool with its ID. Inspection does not grant `read_document` or
   `append_document` access, and a grant for one tab does not include another tab.
4. Steed verifies the file's Google account, ownership, untrashed Doc type and
   private creation metadata before reading its tabs. Appending also requires
   writeability, a valid current revision and a supported final paragraph. Docs
   created before the account marker was introduced remain eligible if their
   original private operation marker and ownership still validate.
5. The append inserts a newline followed by the literal draft body immediately
   before the chosen tab's final newline. Existing styles may be inherited from
   the adjacent paragraph. Text and a `steed_append_<operation-id>` named range
   are submitted in one atomic batch with `requiredRevisionId`. A concurrent
   revision change rejects the entire batch as `not_applied`; Steed does not
   reread and resend automatically. Characters Google strips during insertion
   are rejected before any API request so UTF-16 range offsets stay correct.
6. A lost/malformed response leaves an uncertain receipt. **Reconcile Uncertain
   Result**, or a fresh read-only [archived receipt check](#checking-an-archived-app-receipt),
   looks for the unique operation range in the original tab. It never calls the
   append operation. Missing, ambiguous or removed ranges remain uncertain;
   absence never proves the edit did not happen. A confirmed range establishes
   that the atomic append was applied, not that collaborators left its text unchanged.

Google documents the [atomic batch and revision precondition](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate)
and [tab-aware document structure](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents).
Named ranges are visible to every collaborator with access and can be changed
or removed. They contain an opaque operation identifier, never local task IDs,
credentials or draft metadata. This is recovery evidence, not server-side
idempotency. Reading/checking a document remains possible after it becomes
read-only; appending does not. Documents with more than 32 tabs, malformed tab
identity or unsupported final structures fail closed. Large tab-name results
report `tabs_truncated` if the result budget omits entries.

These tools use the existing `drive.file` scope; enabling Docs API does not add
whole-Drive or broad document access. Opening a file in a normal browser tab does
not authorize its connector use. Existing user-created documents/folders need
explicit selection through a future access flow. Google's
[desktop Picker flow](https://developers.google.com/workspace/drive/picker/guides/desktop-mobile-picker)
requires Drive-only consent, separate from Steed's current identity/Gmail consent;
it is not enabled by these tools. General replacement/editing policy also remains
handled through the reviewed/direct tools described below.

### Reviewed and direct document edits

Use `replace_document` for the default review workflow. It requires an immutable
draft and exact tab write permission, then reads the complete current plain-text
body and revision in a worker. Its receipt stays `requested` with reason
`awaiting_review`; no mutation has been sent. A popup shows the account,
destination, a diff and separate current/proposed text views. **Deny** is the
default. **Apply This Change** authorizes that captured draft and revision only.
Closing/Escape defers the popup; reopen it through **Task Permissions → Apps →
Review Proposed Change**. Cancel the app operation to discard the proposal.
The management window and automatic approval queue share the same user-only
decision method; no agent command can approve a content review.

To delegate edits without reviewing every body, request the separately named
`replace_document_direct` permission. The permission popup explicitly describes
direct replacement and possible formatting changes. **Allow every time** and
**Allow every time on this profile** can save that exact scope through the usual
approval controls. A saved `replace_document` permission still requires each
content review; it never covers `replace_document_direct`, another account or
another tab. Direct permission does not reveal account credentials.

Replacement supports a complete plain-text tab body of at most 16 KiB, with
validated contiguous UTF-16 indices. Tables, inline non-text objects and suggested
content are rejected rather than silently omitted. Body formatting may change;
the tab title and other tabs remain outside the edit. Deletion, insertion and a
`steed_replace_<operation-id>` range run in one revision-bound atomic batch. The
final mandatory newline is preserved. A changed revision/text between preview
and approval returns `not_applied`, requiring a fresh proposal; there is no silent
rebase or retry. Lost replies can be checked read-only using the operation range,
as with appends. Replacing body content can remove older operation ranges, so a
missing historical range cannot establish whether an older update happened.

Pending proposal previews live in a separate bounded schema-9 store, not agent
receipts or activity events. Decision, cancellation, grant termination and restart
scrub their before/after/provider-context text. The immutable agent draft remains
independently retained. Restart never resumes a pending review or approved write.
Clearing saved receipt results still follows the existing retention policy and
does not cancel an operation or pending review.

Connections use immutable provider/subject identities and a new opaque connector
ID on every reconnect, including the same account. Account names and capabilities
live in private `~/.custom_browser/app_accounts.json`; OAuth client credentials,
refresh/access tokens and scopes live in Linux Secret Service. No token/client
path is sent to agents or written to task events. On first use after startup and
on refresh, Steed checks Google's authenticated userinfo subject and email, saves rotated
credentials before writes and fails closed on mismatch or unavailable credentials.
Short-lived access tokens are cached in memory; locking the keyring does not erase
an already cached token. Disconnect disables its use in Steed. Disablement also
prevents refresh workers from restoring removed credentials. Disconnect does not
revoke Google's server-side OAuth consent or undo external effects; manage that
consent separately in your Google Account's third-party connections.

Setup, keyring prompts and API calls run in workers. OAuth uses a random loopback
port, state verification and PKCE; setup waits up to three minutes for consent.
Requests have ten-second socket timeouts, bounded JSON responses and no redirects
or automatic retries. Cooperative adapters check task authority/cancellation
before each request; a dispatched request can still finish after cancellation.
Provider API calls use the connector operation budget, not browser-page request
accounting. A fresh root creation/send needs token refresh, identity verification
and one mutation; a cached-token root creation/send needs one mutation. Folder
writes also verify parent metadata; document appends also fetch file metadata and
the current document revision. OAuth setup is user-owned,
outside task grants.

Google Docs and folders attach the browser's operation ID as a private Drive app property.
Read-only recovery searches that property in the original account/parent destination;
one unique result confirms success. Empty, multiple or incomplete results remain
uncertain: indexing delay, file moves/deletion and provider failure cannot prove
non-creation. Workspace documents cannot use Drive's pre-generated IDs; the
adapter therefore never retries an uncertain create. Gmail send-only scope cannot
verify a lost send acknowledgement, so its receipt remains uncertain and has no
automatic reconciliation. Check Gmail **Sent** before sending another email. The
stable `Message-ID` helps manual identification and is not an idempotency guarantee.
A fresh task can check a user-shared archived Google Doc receipt with exact
read-only permission using [archived receipt checks](#checking-an-archived-app-receipt).
The lookup uses the original account/parent/operation ID; it does not need draft
bytes or write permission. Gmail remains send-only and requires manual checking.
[Task recovery](#continuing-previous-work) keeps matching historical writes blocked.

Real Google consent and writes have not been exercised with your account. Synthetic
OAuth/API tests validate the request shapes, identity checks and uncertainty rules;
real-account acceptance needs your configured client and explicit task approval.

### Scoped task uploads

Request explicit source files and upload origins alongside ordinary task sites:

```python
scope = {
    "profile": "private",
    "origins": ["https://example.org"],
    "allow_private_network": True,
    "actions": ["navigate", "inspect", "read", "upload", "submit"],
    "upload_sources": ["/home/yourname/Documents/report.pdf"],
    "upload_origins": ["https://example.org"],
    "max_upload_bytes": 5 * 1024 * 1024,
}
```

The approval popup lists the exact local files, upload sites and staging budget.
Sources must be normalized absolute filenames; symlink components and nonregular
files are rejected. Upload origins must be a subset of the task's allowed origins.
Both the page and form destination need upload authority, even if the destination
already has navigation permission. Existing grants gain no file access.

Inspect the page, choose an input whose operations include `upload`, and call
`steed_page_upload` with its current `session_id`, `snapshot_id`, `target_id` and
approved `source`. Steed copies the source in a worker thread into private storage,
rejecting size violations or changes during copying. It binds that immutable copy
through a fixed native engine operation, without exposing selectors, script or
file bytes to the client. The receipt includes the filename, byte count and SHA-256
fingerprint. Later changes to the original file do not change the selected copy.

Selection does not submit the form. Inspect again for fresh references, then use
`steed_page_submit` with `read` + `navigate` + `submit` permission. Native validation,
hidden fields and the chosen submitter apply; the one-use POST ticket rejects body
replay through a redirect. A loaded response still does not establish account-level
success. File bytes and form values are excluded from journal events.

Support is limited to one ordinary main-document file control per POST multipart
form. Website scripts, multiple files, folder inputs, password controls, frames,
shadow-root file inputs and custom upload widgets remain unavailable. The private
frontend adapter is verified on Qt 6.11.2 and needs checking on engine upgrades.

The default staging budget is 5 MiB, capped at 25 MiB across retained copies in a
session, with at most 20 copies. Copying and selection have a 30-second deadline.
Pending selection checks live authority and is cancelled by stop, revocation or
teardown. Successful copies remain until their session closes; cleanup removes
them. A crash can leave private staging directories; automatic orphan recovery is
not implemented. There is no raw file-reading API or automatic submission retry.

During **Take Control**, select the file input, choose an approved **Source** and
click **Select File**. Return control to let the agent inspect fresh references and
continue; source and destination permissions stay the same.

### Existing signed-in task profiles

An internal client can request `"profile": "existing:work"` to use the existing
**work** profile. Use the identical selector in `session.open`; omitting it selects
`private` and fails a named-profile grant. The profile must already exist under
Steed's profiles directory. Arbitrary paths, aliases outside that directory,
the global-cookie source, and the incognito search profile are rejected.

Approval permits a task and the user to use the **same live Qt profile at the
same time**. Multiple approved tasks can share it too; every task retains its own
grant, pages, sites, budgets, and cancellation. Current tabs, pop-outs, downloads,
and password operations remain available. Rename/archive/delete are blocked
while a task holds the profile, including when it has no open sessions. Approval
failure rolls back the task's profile reference.

Cookies, cache, and site storage are shared, rather than copied. Requests can
send existing credentials; site changes persist and can change the shared
signed-in session. The popup discloses this. Cookie inspection, configured cookie
inheritance, and ordinary ad filtering remain active.

Private sessions use a profile request guard. Shared-profile task pages use a
page request guard after ordinary profile filtering. Task pages disable document
scripts and new workers. Before navigating a shared-profile page, a hidden,
in-process DevTools adapter enables that target's service-worker bypass. Existing
workers therefore cannot serve or fetch on behalf of its document requests;
ordinary user pages retain their service workers. Initialization failure, timeout,
frontend reload, or renderer termination disables task navigation. The adapter
uses fixed browser code and exposes no debug listener or client protocol access.
Its private frontend SDK names are verified against Qt 6.11.2 and require
rechecking on engine upgrades, as does the cookie snapshot adapter. See
[the protocol's worker-bypass command](https://chromedevtools.github.io/devtools-protocol/1-3/Network/#method-setBypassServiceWorker).

Task budgets apply to task-page requests. Unrelated user/worker traffic belongs
to ordinary browsing and does not spend those budgets. This is shared account
state, not isolation from the user's simultaneous changes. Revocation closes only
that task's pages; other tasks and user browsing continue without reselecting the
profile. Ending a grant cannot undo requests, account effects, or storage changes.

Qt shares persistent data among pages using a profile and applies profile
interception before page interception; see the
[Qt profile documentation](https://doc.qt.io/qt-6.11/qwebengineprofile.html) and
[page interception documentation](https://doc.qt.io/qt-6.11/qwebenginepage.html#setUrlRequestInterceptor).

### Grant enforcement and persistence

`allow_private_network=false` permits public literal IP addresses only. Private literal addresses require explicit `true`. DNS hostnames infer `true`
when the flag is omitted; this is disclosed in the approval dialog. Explicit
`false` remains enforced. The [Qt request-info API](https://doc.qt.io/qt-6.11/qwebengineurlrequestinfo.html)
provides URLs but no resolved peer address; Steed therefore treats a DNS name's
network route as unverified. Allowing this flag still restricts requests to the
listed origins; it does not add other sites or ports. There is no DNS pinning,
OS/network sandbox, or control over tools used outside this application.

Approval installs an immutable live grant only after its journal transaction
commits. Command checks happen at dispatch, and a profile or task-page
[Qt request interceptor](https://doc.qt.io/qt-6.11/qwebengineurlrequestinterceptor.html)
also checks page requests and request budgets. It uses live policy without
calling engine objects or SQLite from its callback. Denials record reason codes;
denied URL queries are excluded from those events. Broader access requires a new reviewed request. An approved replacement ends
prior authority and closes prior task pages; repeating an unchanged request is
idempotent. Stop, close, and terminal task transitions remain
available for cleanup without spending operation budget.

Revocation cancels the task and disposes its pages. Expiry and exhausted budgets
end only that grant: its pages close, running workers and queued app calls are
cancelled, and the task stays active so the agent (or a bound conversation) can
request fresh access. Pending reviews remain and recheck authority on approval.
Expiry uses a monotonic deadline, checked on requests/commands and by a Qt timer.
Queued mutations then fail their authority checks. Cancellation cannot undo a
request already sent to a website. Controller approval/denial/revocation methods
are Qt-thread methods deliberately absent from the client command surface.

`tasks.sqlite` has owner-only permissions. It persists objectives, task states,
URLs/titles, grant scopes/decisions, and event metadata; private engine
cookies/storage stay in memory. Schema 4 adds retained artifacts and app receipts to the task database
without replacing its journal.
Task state transitions and their events are transactional. SQL triggers prevent
updating/deleting journal entries. Active tasks become `interrupted` when the
service shuts down or starts after an interruption; pending and approved grants
end too. Pages, commands, and approved authority are never
restored/replayed. An asynchronous journal write failure disables further task
commands and emits `service.failed`; cleanup still disposes engine resources.

Active document execution and script-handled forms remain future
work. Watching and scoped user handoff use the existing task page.
Existing-profile grants support the same restricted
retrieval, metadata, content reads, control edits, and native scriptless forms as
private task pages, with the corresponding explicit permissions.

### Active-content networking audit

`tests/smoke_task_active_content.py` deliberately enables JavaScript in synthetic
task pages to identify missing boundaries. The application and grant API still
keep website scripts unavailable. This experiment uses temporary profiles,
loopback HTTP/WebSocket destinations, a UDP STUN receiver, and a TURN/TCP
connection listener, without real
accounts or external services. It requires working local TCP/UDP networking.

The Qt 6.11.2 / Chromium 140.0.7339.225 baseline results are:

| Probe | Private task with forced scripts | Existing-profile task with forced scripts |
| --- | --- | --- |
| Approved-origin GET | Reached fixture | Reached fixture |
| Unapproved-origin fetch | Blocked | Blocked |
| General POST | Blocked | Blocked |
| Blob dedicated-worker fetch to unapproved origin | Blocked | Blocked |
| Blob shared-worker fetch to unapproved origin | Blocked | **Reached fixture** |
| Shared worker constructed through a fresh `about:blank` iframe | Blocked | **Reached fixture** |
| Unapproved WebSocket handshake | Blocked | Blocked |
| WebRTC STUN packets to another port | **Reached UDP receiver** | **Reached UDP receiver** |
| WebRTC TURN/TCP connection to another port | **Reached TCP listener** | **Reached TCP listener** |

Both scriptless task controls produced no tested active-content requests, UDP
packets, or TURN/TCP connections. Ordinary user pages reached all positive-control destinations, including
while a task shared their profile. These measurements cover the specific fixtures,
not every transport, redirect, realm, or Chromium version.

A separate fixture-controlled CSP probe used `worker-src 'none'`, restricted
`connect-src`, and `webrtc 'block'`. It blocked the shared-worker request, but the
engine reported the `webrtc` directive as unrecognized and still sent STUN packets.
The fixture's meta policy is a capability probe, not shipped enforcement for
arbitrary documents. HTTP interceptors or a CSP worker policy alone therefore do
not establish the required active-script boundary. Main-world JavaScript wrappers
also need proof against fresh iframe realms and cannot serve as the sole boundary.

Before introducing a `scripts` grant, establish native enforcement for workers,
child contexts and non-HTTP transports without restricting concurrent user tabs.
Keep script-enabled forms denied until serialized-body/handler semantics have a
separate enforceable design. A new script authority must explicitly describe
website code's access to the selected profile's storage and account state.

### Native networking prototype

`tests/smoke_task_native_policy.py` reuses the audit with experimental per-target
Chromium controls in the isolated task frontend. It is not imported by the
application and does not introduce a website-script grant. Its results on the
bundled engine are:

| Candidate | Fresh-iframe/shared workers | STUN UDP | TURN/TCP connection |
| --- | --- | --- | --- |
| `Fetch.continueResponse` adding worker CSP, 100% packet loss, zero latency | Existing-profile worker reached fixture | Reached receiver | Reached listener |
| `Fetch.fulfillRequest` with worker CSP, 100% packet loss, zero latency | Blocked | Reached receiver | Reached listener |
| `Fetch.fulfillRequest` with worker CSP, 100% packet loss, 1 ms latency | Blocked | Blocked | **Reached listener** |
| Fulfillment with worker CSP and `Connection-Allowlist`, no packet loss | Blocked | Reached receiver | Reached listener |

Ordinary user positive controls, including concurrent browsing on the task's
profile, remained available. The prototype coordinates interception through the
frontend SDK because registering another Fetch dispatcher raced its default
response continuation. Private pages are initialized before attaching the
frontend so a protocol target exists. Fulfillment retrieves/replaces fixture
bodies; production acceptance still needs byte/encoding preservation, streaming,
existing response policy/cookies, redirects, cross-process frames, and adapter
failure/revocation tests. Its size check runs after native buffering and is not
a response-memory boundary.

The [Chromium protocol](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Network.pdl)
describes WebRTC packet-loss emulation, but the measured TCP connection proves
that dropping payloads is insufficient for Steed's exact-origin authority.
The newer [Connection Allowlist design](https://chromium.googlesource.com/chromium/src/+/main/docs/connection_allowlist_design.md)
includes native WebRTC restrictions. The prototype tries
`Connection-Allowlist: (response-origin);webrtc=block`; it did not contain RTC
on the bundled engine. A compatible engine/native hook or stronger isolation
must pass the networking gate before active-script support can proceed.

Run the native prototype and its comparisons from the activated virtual environment:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py --delivery continue --latency-ms 0
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py --connection-allowlist --packet-loss 0 --latency-ms 0
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py --require-containment
```

Normal runs exit zero when audit controls complete, even when containment fails.
`--require-containment` exits nonzero when a tested route escapes; it currently
fails because TURN/TCP reaches the unapproved listener. A future passing result
covers these fixtures only and does not establish full active-script acceptance.

### Active-script engine decision

Use native per-document connection enforcement in the same embedded engine and
actual profile as the user's tabs. The selected direction combines
`Connection-Allowlist` with `webrtc=block`, a worker-blocking CSP, and the existing
live-grant request checks. This preserves collaborative account/storage access
without changing ordinary tabs' networking.

Chrome documents stable Connection-Allowlist support starting in
[Chromium 152](https://developer.chrome.com/blog/fedcm-connection-allowlist).
The installed Steed engine is Qt 6.11.2 / Chromium 140.0.7339.225; its failed RTC
probes remain the embedded-backend baseline. The latest published
[PyQt6-WebEngine-Qt6 package](https://pypi.org/project/PyQt6-WebEngine-Qt6/) checked
October 1, 2026 is 6.11.2, so reinstalling the current dependencies does not
provide the required Chromium generation. A compatible Qt engine/native hook or
backport is required before production integration.

| Approach | Decision |
| --- | --- |
| Native policy inside the shared embedded engine | Selected; needs a compatible engine and trusted policy-delivery integration |
| A separate external Chromium process for tasks | Does not share the actual live `QWebEngineProfile`; rejected for current collaboration requirements |
| Disable RTC/networking across the shared profile | Would restrict the user's ordinary tabs; rejected |
| Packet loss or JavaScript constructor wrappers | Insufficient connection boundary; the bundled-engine probes expose TCP/realm gaps |

`tests/smoke_native_connection_policy.py` proves engine feasibility using the
locally installed Chromium 153.0.8010.36. It starts a separate headless test
browser through anonymous DevTools pipes, with a temporary synthetic profile
and loopback HTTP, UDP STUN, and TCP TURN listeners. It neither accesses Steed's
profile nor installs an application backend or public debugging endpoint.

```bash
.venv/bin/python tests/smoke_native_connection_policy.py
.venv/bin/python tests/smoke_native_connection_policy.py --chromium /usr/bin/chromium
```

The restricted document's fixture server supplies a native connection allowlist
covering two exact loopback origins, `webrtc=block`, and `worker-src 'none'` before
website code runs. Unapproved fetch/WebSocket requests, shared/dedicated workers
(including a fresh initial iframe realm), STUN UDP, and TURN/TCP connections were
blocked. An allowed cross-process iframe ran the same probes. Approved-origin
GET and synthetic POST reached the server; cross-origin response access still
follows ordinary CORS rules. Connection policy does not provide method/body
authority: production still needs the existing request filter, grant checks,
and a separate script-handled form design.

Ordinary pages sharing the synthetic profile reached all positive-control
destinations before and during restricted browsing. Both tabs shared cookies,
local storage, and a live storage update. Native policy survived detachment of
the control session; task closure disposed its owned frame targets while the
ordinary tab retained access to storage. The diagnostic exits nonzero if a
positive control is missing or the containment checks fail. A passing
`tested_connection_policy_contained` covers these fixtures, not all transports
or Steed's missing adapter.

Next, obtain a compatible embedded engine and implement trusted policy delivery
for every task document/frame before its code executes. Preserve response bytes,
encoding, streaming, cookies, and existing CSP/connection-policy restrictions;
policy merging must not broaden a server restriction. Cover redirects,
initial/child realms, service-worker bypass, unknown or malformed policy, adapter
failure, revocation, and exact grants in the real shared-profile backend.
Fail closed when delivery or verification fails. Keep website scripts and
script-handled forms unavailable until those acceptance checks pass.

### Bounded engine feasibility assessment

Decision on October 1, 2026: defer agent website-script support and continue
with the current supported operations. Keep the Qt browser and its shared-profile
model; no dependency replacement, engine fork, or profile migration was performed.

The installed engine and failed embedded RTC probes establish the current gap.
Qt's [separate-release announcement](https://www.qt.io/blog/qt-webengine-will-release-separate)
states that the first Qt WebEngine 6.140 release accompanying Qt 6.12 still uses
Chromium 140. A newer Chromium baseline is planned later, with its exact feature
support still requiring verification. No compatible packaged Qt/PyQt build was
identified in this assessment.

The inspected [Qt request API source](https://raw.githubusercontent.com/qt/qtwebengine/dev/src/core/api/qwebengineurlrequestinfo.h)
provides request blocking/header changes, without the required document-response
policy hook. Chromium's [native policy design](https://chromium.googlesource.com/chromium/src/+/main/docs/connection_allowlist_design.md)
requires context identifiers across browser, renderer, and network-service code,
frame/worker propagation, registration before resource loading, and restoration
after network-service failure. I infer that porting the complete feature and
exposing a trusted Qt policy hook is substantial native-engine work, rather than
a small Python adapter change. A bounded, maintainable backport was not established;
no custom engine compilation or complete patch/dependency audit was attempted.

Revisit this decision when a maintained build offers the required enforcement or
a reproducible, supportable integration is demonstrated. Verify the actual feature,
policy delivery, and shared-profile fixtures before granting scripts. A whole-browser
engine migration would require its own compatibility and maintenance proposal.

## Local agent access (MCP)

### Daily browser access and permission diagnostics

Restart Steed and start a new agent conversation after updating the tool catalogue.
This applies to native Codex and other MCP clients. Steed now exposes 85 tools.

Ordinary DNS names no longer need an extra flag just to request access. Omitting
`allow_private_network` with named origins includes that permission in the review;
the popup still explains that DNS can resolve to private addresses. Explicit
`false` remains enforced, and private literal IP addresses still need explicit
`true`. Origins remain exact, without paths, credentials or wildcards. Connector
HTTP/DNS protections are unchanged.

Grant errors report stable codes, maintained explanations and a field when known,
such as `invalid_scope` / `origins` or `invalid_app_scope` /
`connector_permissions`. They never echo arbitrary exceptions, submitted secrets
or storage paths. Exact repeated requests return the existing pending/approved
grant. A different scope can request replacement while the old grant remains
usable; approval atomically ends the old grant and closes its task sessions.
Reopen/adopt pages after replacement. A different pending request must finish
before another can be queued. The task database migrates to schema 21.

For an isolated, interactive website, a minimal screenshot-control request is:

```json
{
  "scope": {
    "profile": "private",
    "origins": ["https://example.com"],
    "actions": ["computer"]
  }
}
```

Send this to `steed_grant_request`, then poll `steed_grant_list`. `computer`
includes its required `read`, `visual`, `live`, `navigate`, `inspect` and `attach`
permissions in the same visible review. Open `steed_session_open` with
`profile="private"`, navigate and wait for readiness, then use
`steed_session_watch`. A live grant makes omitted session mode default to `live`;
explicit `mode="restricted"` retains script-disabled retrieval. Without `live`,
the default remains restricted. New private sessions use separate off-the-record
storage, with normal website JavaScript/background traffic in live mode. They do
not access the user's incognito tabs. In Watch, Take control pauses the agent and
allows direct interaction with a private live page; Return control resumes it.

To take over the user's current tab, call `steed_browser_request_control` with a
stable `request_id` and `selection="current_tab"` (or `profile_tabs`, up to ten
website tabs). One popup shows the task, profile, selected tabs and sites. Poll
`steed_browser_control_status`, then use `steed_browser_tabs` and
`steed_session_adopt` after approval. The selected profile/sites are pinned;
changed previews become `stale`. Take Back returns tabs immediately. Status becomes
`returned` after all selected tabs are returned, and repeating an old request ID
never retakes them. The sidebar's **Give agent tab control…** remains available.

When DOM actions are missing, inspection includes guidance distinguishing a
restricted page from custom/canvas controls. `steed_page_capture` returns an image
and `capture_id`; `steed_page_click` takes that ID plus `x`/`y` pixels measured
from the returned image's top-left corner. `steed_page_type` enters literal text
into the focused website control; `steed_page_key` supports a maintained key enum
including Enter, Tab, Escape, arrows, PageUp/Down and SelectAll. Each image ID is
usable once, expires after 60 seconds and is checked against navigation, scrolling
and viewport geometry. Capture again after each action. These controls operate
inside the website, including visible embedded content, and can submit data or
change accounts. They do not target desktop applications or browser chrome.
Native input uses the [Chromium Input protocol](https://chromedevtools.github.io/devtools-protocol/tot/Input/)
through Steed's existing private frontend; no debug listener or caller scripts are
exposed. A dispatched action is not proof of website success. Inspect its result;
timeouts may leave an uncertain outcome and must never trigger automatic replay.

### Unsafe automatic agent approval (YOLO mode)

Enable **Agents → YOLO mode: auto-approve agent requests (unsafe)** and explicitly
confirm the warning. This affects every connected agent and profile for the
current browser run. The status bar and sidebar mark the mode; it is off again
after restarting Steed. Agents cannot enable it through MCP. Disable it from the
same menu without another confirmation.

While enabled, Steed automatically resolves permission grants, tab takeover,
generated-worker execution, connector recipe installation, account payload
reviews and panel/layout/start-page proposals. Account writes and sends can occur
without payload review. This can be unsafe, especially with untrusted agents or
website instructions. Take Back ends the current handoff, but a new request can
be auto-approved while YOLO remains enabled; turn it off or cancel the task to
prevent that. Existing grants remain explicit and revocable in Task Permissions.

YOLO changes approval, not supported operations or validation. Account sign-in,
enabled library folders, exact destinations, ownership, sandbox/resource limits
and no-replay rules still apply. Website device permissions keep their separate
profile rules; external agents keep their own host permissions and tools. It never silently selects archived private task
data: an unspecified recovery source picker is declined, so choose historical
material with normal review before enabling the mode. Native file pickers/new
windows remain limited on task-owned pages; external agents retain their own tools
for work outside Steed. The read-only HTML data bridge stays deferred.

The optional `agent_mcp.py` adapter exposes the existing task operations over
stdio or loopback Streamable HTTP using the
[official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
It connects to the running Steed process; Qt pages and profiles stay in that
process. Local access is off when the browser starts.

1. Install the optional dependencies from the repository root:

   ```bash
   .venv/bin/python -m pip install -r requirements-mcp.txt
   ```

2. Start Steed and select **Agents → Enable Local Agent Access**. This enables
   local connections for the current browser run; it grants no site access.
3. Configure your MCP client to launch the adapter. For clients using an
   `mcpServers` configuration, substitute the absolute repository paths:

   ```json
   {
     "mcpServers": {
       "steed": {
         "command": "/absolute/path/Browser/.venv/bin/python",
         "args": ["/absolute/path/Browser/agent_mcp.py"]
       }
     }
   }
   ```

   The adapter normally reads `~/.custom_browser/agent-access.json`. A custom
   browser data directory can be selected with `--discovery /path/agent-access.json`.
   The browser must already be running and opted in. No terminal is needed for
   the GUI; the MCP client manages the adapter process.
4. Have the client call `steed_task_start` with an objective, then
   `steed_task_capabilities` and `steed_browser_context`. Context reports the
   active browser window's profile, or the last-used window when another app has
   focus. Use its `profile_selector` (for example `existing:work`) to request
   shared access through `steed_grant_request`; otherwise use `private`.
   A scope example is:

   ```json
   {
     "scope": {
       "origins": ["https://example.org"],
       "actions": ["navigate", "inspect", "read"],
       "profile": "existing:work",
       "allow_private_network": true,
       "duration_seconds": 600
     }
   }
   ```

   Hostname network permission is included in the review when its flag is omitted;
   explicit false remains enforced because Qt cannot pin DNS routes. The named
   profile must exist. Use private for a new isolated session.
5. Read the nonmodal approval popup and choose **Approve** or **Deny**. The client
   polls `steed_grant_list` for the decision, then opens a session with the exact
   approved profile. You can keep browsing on that same profile.
6. Revoke/cancel through **Agents → Task Permissions**, or disable local agent
   access to cancel connected tasks. Closing the adapter cancels its unfinished
   task. Use `steed_task_finish` after completing a workflow.
   The same connection can then start another task; it receives a new identity
   and must request fresh permissions. An active task cannot be replaced.

### External-agent conversation sidebar

**Agents → Agent Connection Setup…**, also available from the sidebar's
**Connect another agent** button (or **Conversation & settings → Connection setup**
during a conversation), opens a nonmodal setup screen. It enables local
access only when you click **Enable Local Agent Access**, then offers **Copy
Connection Settings** and **Copy Starter Prompt**. The settings use the running
Python interpreter, repository and browser data directory, including paths with
spaces. Add the Steed server entry to your client's existing configuration;
clients without `mcpServers` JSON can use the displayed command and arguments.
Opening the screen or copying settings creates no task, launches no agent and
grants no page/app access. The screen never reads or copies gateway tokens. If
the optional MCP SDK is absent, it shows a one-time installation command; it
does not run the installer. The enable state follows the existing Agents-menu
switch and resets on browser restart. Seven setup regressions plus existing
agent/sidebar checks passed together (53 tests in 13.277 seconds); the final
setup screen was visually inspected and ordinary startup/render/shutdown passed.

Steed uses external agents first. Codex chats run in a dedicated external Codex
CLI process; Steed sends turns through its App Server interface. Other clients
can still use the task mailbox directly. Steed does not store model credentials.

#### Sidebar setup and everyday use

The HTML sidebar shows a short welcome with **Start a Codex chat** and
**Connect another agent**; the header **+** also starts a new Codex chat. It hides
the empty transcript, inactive task controls and model/file/receipt administration
until needed. Starting or choosing a conversation
shows distinct message cards, a fixed message box, **Send**, **Stop task** and,
for connected agents, **Give tab control…**. Existing conversations appear in the
picker.

**Conversation & settings** is collapsed initially. It contains model selection
for a new chat, connection settings, the persistent file library, sidebar/tab presentation,
task permissions/files/receipts, detailed counts and chat clearing. Opening or
closing options preserves your local draft and creates no task or permission.
Pending approvals and unanswered agent questions stay visible above the chat
without expanding options. Private browsing hides these controls and clears
regular-profile text/drafts; conversation data remains owned by its original task.
Short chat tabs can scroll the expanded options. Hiding the sidebar preserves
running tasks; cancellation remains explicit.

The activity card shows **Sending**, **Thinking**, **Working**, **Replying**,
**Needs your input** or **Ready** for native Codex chats, with a thin busy indicator
and queued follow-up count. These are transient runtime states; they do not expose
reasoning text or establish that an external action succeeded. Other MCP clients
show **External agent connected** because Steed cannot observe their execution.
Messages have separate sender cards and a fixed composer with a primary Send
button. Light/dark appearance follows the browser; details stay under
Conversation & settings. Messages remain plain text, including code or markup
returned by an agent.

The card also shows **Model: … (reported by Codex)**, using the resolved model and
provider returned by the running App Server thread. A reported model reroute
updates the name and shows the previous model for that turn. **Model for new
chats** in Conversation & settings selects a future chat; changing it does not
change the selected conversation. Missing/invalid runtime metadata and other MCP clients
show **Model: not reported by the agent**, rather than guessing from the selector.
After a chat ends, its name is marked **last reported** for this browser run;
these transient reports are not restored from old chat history after restart.

The maintained interface lives in `ui/web/agents.html`, `agents.js` and
`theme.css`. There is no frontend installation/build step or localhost server.
`ui/html_surface.py` loads those assets into its own off-the-record profile,
with network/navigation/file access denied and a small QWebChannel command API.
Python validates commands against the current document and selected conversation;
all browser/task authority remains in the existing services. User/agent-editable
start pages and remote websites do not receive this bridge.

If the interface fails to load or its renderer exits, **Reload interface** restores
the selected conversation and local draft received by the controller. It does not
replay sent messages, start another task, or repeat browser/app actions. Browser
windows retain independent unsent drafts. Private-profile changes immediately
cover the previous view and clear conversation text and drafts before showing the
private state. Light/dark, keyboard, bridge, draft, failure, privacy and sidebar/tab
checks use temporary profiles and synthetic messages in
`tests/test_html_agents.py`.

#### Codex chats from Steed

1. Install a current [Codex CLI](https://developers.openai.com/codex/cli) and sign
   in once with `codex login`. The integration has offline checks with Codex 0.159.2 and 0.160.0 and
   uses its App Server protocol and bundled code runtime. Older runtimes
   may be incompatible; failed setup leaves a cancelled, read-only task.
2. Install Steed's optional adapter dependencies:
   `.venv/bin/python -m pip install -r requirements-mcp.txt`.
3. Open **Agents → Agent Sidebar** (`Ctrl+Shift+A`) and choose **Start a Codex chat**
   (or the header **+**).
   This explicitly enables local agent access for this browser run and starts a
   new task. No MCP client configuration or starter prompt is required.
   Before starting a chat, **Conversation & settings** contains the model selector. It can
   use your Codex default or request GPT-5.4 for this chat
   without changing Codex's saved configuration. Direct Steed tools were verified
   with GPT-5.4 and code-runtime Steed calls with GPT-6.1 Sol in offline native
   fixtures; your account must still provide the selected model.
4. When the status says **Codex is ready**, type a message and press **Send** or
   `Ctrl+Enter`. Codex starts a turn automatically. Follow-ups queue in order while
   a reply is running, then continue the same live Codex thread and Steed task.

Codex uses its own account/configuration and model selection. Its account
authentication stays with Codex; Steed neither copies credentials nor handles
login/logout. Model requests use that account and its limits/billing. Up to two
Steed-started Codex chats can run together; all browser windows share their task
state while keeping unsent drafts local to each window. Opening the sidebar or
setup screen alone never launches Codex.

Steed preserves Codex's normal tools, configured MCP services, sandbox and
approval policy. It enables the bundled code runtime used by newer models and
adds one task-bound Steed MCP connection. Codex can research, code and use its
own integrations independently of Steed. Its normal capabilities are governed by
Codex's settings; Steed is a communication and browser-tool provider, not an OS
sandbox around the external agent. An installed native runtime is a trusted local
program with the same access it normally has outside this browser.

The dedicated task-bound Steed server uses Codex's `approve` tool-admission mode.
The former `auto` setting could reject tools before they reached Steed, including
resource discovery and permission requests. Only this new Steed server entry is
changed; inherited server policies and Codex's sandbox/OS approvals remain native.
Admission does not grant browser, connector, worker or write authority: Steed's
scope checks, enabled-folder decisions, approval popups and payload reviews still
apply. The values are documented in the official OpenAI
[Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

Each chat uses a dedicated CLI process and new ephemeral thread. A private,
single-use task capability binds its Steed connection; Steed verifies that task
before enabling Send. Malformed/oversized output, foreign task/turn identities,
startup failure and disconnect stop delivery without automatic replay. Runtime
stderr, account details and message bodies are not added to activity events.

Native Codex questions and command/file/network approval requests appear as
nonmodal popups while the selected chat is visible. **Answer agent (N)…** opens
pending requests when the sidebar was hidden. **Allow once**, **Allow for this
chat** and **Deny** answer native requests; questions accept an option or text.
These answers do not grant any Steed browser or connector authority. Codex's own
policy determines when it needs approval; Steed does not request an extra prompt
for actions already allowed by that policy. Clearing Steed chat does not erase
Codex's active thread, local files or external effects.

Messages still grant no Steed page, profile, connector or worker access. Codex
requests the same Steed permissions and payload reviews as any other MCP agent;
expand **Conversation & settings** for **Permissions**, **Task files**, and **Receipts**. Replies
are streamed as plain text, coalesced and split at UTF-8 boundaries within existing
chat limits. No message bodies, account details or runtime stderr enter activity
events. **Clear Chat** removes queued prompts and suppresses the rest of an erased
reply; text already sent cannot be recalled from the external agent.

#### When an agent says discovery was rejected

There is no separate discovery switch for an active Steed connection. Agents can
call `steed_task_capabilities`, `steed_browser_context`, `steed_library_folders`
and `steed_app_list_tools` with `{}` before requesting a grant. Task identity is
bound to the connection; supplying `task_id` is invalid. Empty folder/connector
lists mean nothing is enabled/available. Enable the intended folder in **File
library** or connect the service in **App Connections**; discovery itself does
not connect accounts or expose disabled folders. Enabled library folders already
permit file access without repeated task grants.

`steed_browser_tabs` has a different requirement: use **Give agent tab control…** and
approve live/read access first. Rejection of that call does not disable library,
connector or capability discovery. Other browser/connector access uses
`steed_grant_request` with the exact scope, then `steed_grant_list` to check approval.
Failures report a known tool name and code where available; journal diagnostics
record only command, code and validation/execution stage, never argument values
or exception text. Restart Steed and start a new Codex chat to load changed native
connection settings/instructions. Existing conversations keep their original
runtime configuration.

Hiding the dock keeps the chat running. **Cancel Task**, disabling local agent
access, ending the task or closing the last browser window stops the dedicated
child and its adapter. This never controls another Codex instance or its daemon.
Restart/disconnect retains Steed history but does not reconnect, retry turns,
replay queued text or resume external writes. Start a new chat with fresh task
permissions; historical conversation is not automatically sent to it.

#### Claude Code chats from Steed

Install [Claude Code](https://docs.claude.com/en/docs/claude-code) and sign in by
running `claude` once. In **Conversation & settings**, set **Agent for new chats**
to **Claude Code**, optionally choose Opus, Sonnet or Haiku, then start a chat.
Steed runs `claude -p` with the stream-json protocol in a private temporary folder
and adds only a bound Steed MCP server; your Claude Code settings, other MCP
servers, tools and permission rules stay in effect, and sessions are not saved to
Claude Code's history. Steed's own tools are pre-allowed in Claude Code because
Steed authorizes each capability itself; starting another task and the
conversation-reply tool are disabled. When Claude Code asks permission for one of
its own tools (for example Bash or a file edit), Steed shows the request: **Allow
once**, **Allow for this chat** (applies Claude Code's suggested session rule) or
**Deny**. Claude Code's questions appear as answerable prompts. Replies stream into
the sidebar; text from subagents is not shown as the reply. A failed turn, an exit
or an MCP connection failure stops the chat and cancels its task without retrying.
Checked with Claude Code 2.1.312 and a synthetic offline runtime.

#### Codex connection troubleshooting

Restart Steed after updating this checkout and start a **new** Codex chat. Ended
chats retain history and do not reconnect or automatically replay work.

The adapter supports the current `item/tool/requestUserInput` method as well as
the older question name. It answers the installed CLI's `currentTime/read` with
whole Unix seconds, including before/after a turn and for background threads.
This clock reply grants no Steed access. Notifications from other native Codex
threads are excluded from this chat; malformed or mismatched events within the
owned turn still fail. Unknown optional host RPCs receive a method-not-found
protocol error instead of causing Steed to terminate the connection. This does
not implement or approve the unsupported request.

These contracts were checked against the installed CLI's generated schemas and
[official App Server documentation](https://learn.chatgpt.com/docs/app-server).
Questions and native approvals remain separate from Steed grants. Genuine
connection/protocol failure now identifies only the handling stage/event and
exception class, without request bodies, commands, account details or stderr.
If it persists, report that event name and whether the task was using a tool or
requesting input; check receipts before repeating an uncertain external action.

The user's exact live-account disconnection has not been reproduced. Isolated
native checks and synthetic tool/question/clock/child-event regressions validate
these compatibility fixes; they do not establish that every Codex task works.

Final behavior acceptance: **954 regressions pass in 385.787 seconds**. Final
palette/menu follow-up passes two checks in 1.518 seconds; revised light/dark
working previews were inspected. Startup/local render/shutdown passes on Qt 6.11.2.
Ruff lint/format, dependency, whitespace, eight recipe examples and local document
links pass. Both actual MCP transport workflows passed at the preceding v6
checkpoint; installed Codex 0.159.2 passes isolated direct-tool and code-runtime
follow-ups/tool calls. Live-account failure reproduction remains a separate check.
Logs: `/tmp/steed-codex-activity-full.log`,
`/tmp/steed-codex-activity-final-ui.log`, `/tmp/steed-codex-activity-startup.log`,
`/tmp/steed-codex-activity-native{,-direct}.log`.

#### Personal agents and website coverage

Steed's stdio MCP adapter works with local clients that can launch a process.
Its authenticated HTTP adapter binds to loopback; a cloud agent cannot reach
that address directly. The browser must be running for the HTTP transport;
the stdio adapter may start first and connects on its first call (until then
calls return `browser_unavailable`, meaning nothing was sent).
Connecting a third-party client grants no page access: it starts its own task,
requests an exact profile/site scope and uses the published inspection/action
and conversation tools. **Resuming after a disconnect:** `task.start` returns a
`resume_token`. If the client's connection drops (restart, crash, network), the
task's access ends at once — its pages close, like an expired grant — but the
task, conversation and files are kept for five minutes. A new connection calls
`task.resume` with the token to continue the same task and receives a fresh,
single-use token; it then requests permissions again. Tokens work only in the
same browser run and alongside the normal agent-access credential. Tasks not
resumed in time are cancelled, as are all held tasks when agent access is
turned off; a connection Steed drops itself (a stuck operation) is cancelled
immediately. Steed-started Codex and Claude Code chats are not resumable this
way. The MCP adapter does this itself: it keeps the latest token, and after a
lost connection the agent's next call reconnects, resumes the task and then
runs, with `connection.task_resumed` (or, after a browser restart or more than
five minutes, `task_resumed: false`) added to its result. The call that failed
is never sent again, and calls already queued behind it fail instead of
running on the new connection. While a task waits, its sidebar shows
"Waiting to reconnect until HH:MM". Generic MCP cannot start a new turn in an idle client,
so an agent that is waiting for you calls `conversation.wait`: it blocks for up
to 25 seconds and returns as soon as you send a message (`waited: "message"`),
on timeout (`timed_out`, call again) or when the task ends (`task_ended`). The
agent's own replies do not end a wait, one wait per task runs at a time, and
calls the agent makes during a wait queue behind it. Steed-started Codex and
Claude Code chats get messages as new turns and do not have this tool.

Compatibility research (October 3, 2026):

| Agent | Verified integration route | Remaining work |
| --- | --- | --- |
| Codex | Dedicated native App Server and task-bound local MCP, including newer code runtime | Real-account desktop acceptance |
| Muse | Official [custom connector guidance](https://www.meta.com/help/artificial-intelligence/1687253048996149/) supports service API access | Confirm custom HTTP/MCP authentication and transport in an actual account; do not assume direct local-process support |
| ChatGPT dots | [Dots documentation](https://learn.chatgpt.com/docs/dots) supports enabled plugins and a separately connected local computer | Package and verify a Steed plugin/local bridge or authenticated remote connection; sidebar message delivery is separate |
| Instinct | [Official product page](https://instinct.com/) describes app/device access | No public custom MCP client setup was verified; cannot claim Steed compatibility yet |

The published page actions do **not** capture everything a human sees. Reads are
bounded rendered DOM snapshots with opaque references to supported links,
fields and forms. Bounded open-root text/control-name reads improve component
coverage; controls inside open shadow roots of the main document can be typed
into, selected, checked and activated like other controls (constraint
`shadow_control`), but form submission and file upload through them stay
unavailable. Closed roots and exact slot ordering remain
limited. Snapshot limitations report canvas content, form values/editable text, inaccessible frames,
out-of-scope frame origins and truncation. Images, PDFs, video/audio, visual
layout, custom widgets and unloaded/virtualized content can be missed. Restricted task pages disable website JavaScript and can miss modern app content;
live task sessions and explicitly shared user tabs run normal website scripts. Agents should inspect the
capability report and snapshot limitations rather than claim a complete view.

Existing-profile permission shares cookies/login state with ordinary browsing and
creates a separate restricted task page. **Watch / Take control / Return control**
preserves that task page.

#### Delegate normal live tabs

1. Open the agent sidebar and select a connected task.
2. Click **Give agent tab control…**. Choose the current tab or all currently open
   tabs on this named profile, across its windows (maximum ten).
3. Review the popup's profile, sites, selected tabs and actions, then approve.
   Existing task authority is replaced by a fresh reviewed scope; prior task
   pages close. Resolve any pending permission request first.
4. The agent calls `steed_browser_tabs` to discover only those shared tabs and
   `steed_session_adopt` to obtain/reuse their session IDs. The selected tabs are
   also adopted automatically on approval. It uses the normal inspection/action
   tools on the same page, with the existing login, document state, JavaScript,
   cookies and ordinary background network activity.
5. A bar above the page identifies the controlling task. **Take Back** removes
   that tab's authority immediately, including during pending input or loading.
   The agent cannot re-adopt it without a new explicit handoff. Address-bar
   navigation, Back, Forward and Reload also take the tab back first.

New tabs and tabs from other profiles are never included automatically. You can
browse other tabs while the agent works. Direct input into a delegated tab is
shielded; Take Back restores its prior input policy. Closing the tab/window,
changing profile, task cancellation, expiry, access disable and shutdown all
release borrowed pages before ordinary browser cleanup. Task completion does
not close or reload the user's tab. Incognito tabs cannot be delegated.

The `live` action is separate approval for this mode, with clear script/network
wording; remembered restricted-page approvals do not cover it. Ordinary website
scripts can send requests or change account data following interaction. Their
background requests are not filtered or counted against restricted-page network
budgets. Native button activation and form dispatch report `dispatched`, not
confirmed website success; observe the result and never retry uncertain effects.
DOM actions on password/file forms and custom widgets, plus borrowed-tab
upload/download automation, remain limited. Separately approved computer input
can operate visible password/custom controls; use restricted task transfers or explicitly shared files instead.
Normal user download/permission dialogs keep their ordinary browser behavior.

Live targets are revalidated individually against their node, markup, labels,
base URI and ancestor attributes, so unrelated DOM updates do not invalidate
all controls. Replaced or changed controls still require a fresh inspection.
Restricted pages retain their whole-document staleness checks. Control references remain opaque. Separate screenshot-guided computer permission
adds native click/type/key tools; arbitrary caller scripts and selectors remain
unavailable.

#### Website observation coverage

With `visual` and `read` permission, `steed_page_capture` returns the current
viewport as a native MCP image plus metadata. It needs a visible borrowed tab or
watched task view. Hidden tabs need `session.watch` first. The browser checks the
page's origin and lifetime again before releasing pixels, and Take Back cancels
pending captures. No image bytes are written to the task journal.

Visual approval includes visible field values and embedded content from other
sites, even frames omitted from DOM reads. It is separate from text-read approval;
restricted-page permissions do not silently expand. The sidebar's live delegation
popup explicitly includes visual permission. The bounded JPEG is at most
1280×960 pixels and 256,000 bytes; metadata reports clipping and viewport-only
coverage. The native frontend preserves the original viewport, scroll, scripts
and normal service-worker behavior. It opens no debugging listener. Image pixels are untrusted site content. They confer no input authority without
separate computer/live approval; each computer operation needs a fresh capture ID.

`page.inspect` provides bounded rendered DOM text and native controls, with flags
for truncation, inaccessible/out-of-scope frames, observed open shadow roots,
canvas, images/video/SVG and custom widgets. It distinguishes live website
execution from disabled scripts. Field values and editable text are omitted.
These flags describe coverage gaps, not a guarantee of completeness.

“Important” depends on the user's task. A layered observer should first extract
text, labels, links, headings and frames deterministically; then use accessibility
semantics and viewport images where DOM coverage is insufficient. A lightweight
model can route the next observation and rank relevant evidence within a budget.
It cannot recover unloaded content or guarantee that nothing was missed. The implemented layers include coverage signals, approved viewport images and
bounded form descriptions/required/invalid/error hints. Complete accessibility
trees and broader frame/widget inspection remain future work. Native frame inspection is available in Qt's
[QWebEngineFrame API](https://doc.qt.io/qt-6.11/qwebengineframe.html), but frame
lifetime and each frame's authority must be checked before exposing its content.

#### Other external clients

1. Complete the local MCP setup above and connect your external agent client.
2. Ask the agent to call `steed_task_start` and keep that connection open. Give
   the task a recognizable objective so you can choose it in Steed.
3. Open **Agents → Agent Sidebar**, or press `Ctrl+Shift+A`. Select the connected
   task from the list. The dock can move between the left and right sides.
4. Type a message and click **Send**, or press `Ctrl+Enter`. Only the submitted
   text is shared. Unsent drafts remain in that window and are not sent when
   changing tasks or hiding the sidebar. Messages have an 8 KiB UTF-8 limit.
5. Have the agent call `steed_conversation_read`, reply with
   `steed_conversation_reply`, and check for additional messages while working
   or waiting for your input. The sidebar shows pending messages and when the
   agent's read tool has fetched them; this does not prove the agent understood
   or acted on the text.
6. Expand **Conversation & settings** for **Permissions**, **Task files**, and **Receipts**,
   which open the selected task's controls in a nonmodal window. The sidebar shows counts for pending
   grant, package, recipe and app reviews, files, drafts and app receipts.
   Website, task, package, recipe and payload approvals retain their existing
   separate controls. Conversation text never approves a request.
7. Hiding or closing the sidebar keeps the task running. **Stop task** ends
   it explicitly. Finish a final reply before calling `steed_task_finish`.
   Adapter disconnects still cancel unfinished tasks; restarting the browser
   retains history and marks active tasks and incomplete replies interrupted.

Suggested instruction for a connected external agent:

> Start a Steed task called “Browser collaboration”. Read its conversation
> mailbox, respond through conversation.reply, and check for follow-up messages
> while working. Request exact permissions for any page, file or app actions.

An ordinary MCP client may only run its agent after a prompt in that client's
own UI. **Send** queues a message for the task; it cannot wake such an idle
client. The agent must participate in the mailbox protocol, ideally by keeping
a `conversation.wait` call open while idle. Automatic turn delivery applies to
Steed-started Codex and Claude Code chats. Reconnect and
conversation replay into another task remain unavailable. Each new task needs
fresh permissions.

#### Chat tabs and website chats

##### Structured chatbot relay

Open **Agents → Website Chats → Chatbot relay…**, or **Use as agent…** in
website-chat sidebar controls. This lets a chatbot propose tool requests through
structured messages in **both directions**. It uses the existing task broker,
tool schemas, grant approvals and receipts. It does not give a website native MCP
or a native management bridge. Ordinary website chat and native external-agent
connections remain separate options.

1. Enter the task and choose **Copy/paste JSON manually** or **Enabled chat tab**.
   Tab mode requires explicit consent for a loaded HTTPS tab in this window's
   regular profile. The exact destination/profile/task remain visible. Incognito
   cannot start a persistent relay.
2. **Start relay task** prepares the first outbound JSON: your task, Steed's
   instructions, published tool schemas, exchange ID and queued user messages.
   Review the exact text. **Copy reviewed message** works with any chatbot UI;
   tab mode also offers **Paste reviewed message**. Steed focuses one unambiguous
   visible composer and uses native Paste. It never presses Send. If the composer
   is ambiguous, copy and paste into the intended field yourself.
3. Send in the chatbot. Paste its complete JSON response into Steed, or select
   only that response in the enabled tab and use **Read selected response**.
   Steed does not scrape the conversation or guess the latest assistant message.
4. **Review response** validates the complete batch and displays a canonical
   preview. **Run reviewed requests** dispatches that exact preview. Editing the
   response invalidates the review. Approve requested access through the normal
   Steed popup; a chatbot cannot approve itself. **Task permissions / details…**
   selects this task, including its pending approvals and recorded activity.
   If validation fails, **Prepare outbound message** includes structured
   `response_feedback` so the chatbot can correct its response; no requests from
   that rejected batch are dispatched.
5. Inspect **Results**, then **Prepare outbound message**. Review/copy/paste the
   new JSON back to the chatbot and repeat. Subsequent messages carry structured
   results/errors, the next exchange ID and new user messages. Tool schemas are
   omitted by default after the first exchange; the checkbox can include them
   again. Copy/paste sends the exact prepared snapshot, never an invisible refresh.
6. A `final` response becomes a reviewed answer in the task conversation; it does
   not prove external effects. **Stop relay** or closing the review cancels the task
   and its authority. No relay is resumed or replayed after restart.

Chatbots must return one JSON object (an exact `json` code fence is also accepted):

```json
{
  "version": 1,
  "exchange_id": "issued-by-Steed-for-this-exchange",
  "calls": [
    {
      "id": "inspect-1",
      "tool": "steed_page_inspect",
      "arguments": {"session_id": "previously-returned-session-id"}
    }
  ]
}
```

Use `{"version":1,"exchange_id":"current-ID","final":"Answer"}` for an answer
without tool calls. Each request needs a new short call ID, a published tool name
and schema-valid arguments. The trusted UI assigns task identity. Raw Python,
shell, JavaScript, approval and lifecycle commands are unavailable; generated code
uses existing reviewed worker/package tools. The parser rejects duplicate keys,
unknown fields/tools, nested schema mismatches, stale exchanges and reused IDs
before dispatch. Accepted IDs are consumed even when execution fails or later
calls are skipped. This prevents replay of the same IDs, not semantically duplicate
writes with invented new IDs: users and chatbots must still inspect outcomes.

Requests are limited to 64 KiB, four calls per batch and 32 exchanges per task.
Goals/final answers are limited to 8 KiB. The first transport is text/JSON; screenshot
and pixel-input tools, automatic image/file attachments and provider-specific
response extraction are not offered. Large results are explicitly omitted within
a 600 KiB result budget; outbound messages must fit 1 MiB. Any failure or omitted
result stops the remaining batch. Check outcomes before issuing further requests;
Steed never retries uncertain writes. A 35-second deadline ends an unresponsive
relay, without claiming that already-dispatched work had no effect.

Tab binding checks the exact view/page/profile/address (excluding the URL fragment)
at sharing and dispatch. It cannot detect an invisible provider conversation switch
that keeps the same URL; selected-response reading and request review remain required.
Before the first accepted response, a chat's initial conversation-address change
pauses exchange: **Enable this initial conversation** deliberately rebinds to a
loaded address on the same HTTPS origin. Subsequent navigation/reload, closing the
tab, profile changes, task/grant termination or reduced enabled-folder access
stops the relay and clears its unshared buffers. Clipboard text and data already
sent outside Steed cannot be recalled. Existing saved grant rules still apply
within their scope; reviewed requests never create an extra approval bypass.

Copy/paste acknowledges exported queued user messages; it does not establish that
the chatbot read them, is thinking or finished. Steed reports its own request
execution and result readiness. No real-account provider compatibility or
automatic Send/extract loop is claimed by synthetic page acceptance. Provider
adapters and image-capable exchange remain later milestones.

##### Current chat interfaces

The agent panel separates connection health, runtime activity and message delivery.
User messages show waiting/queued/awaiting acceptance/read states from actual
transport and runtime facts. A generic MCP fetch confirms a read, not execution
or completion. Pending permissions show **Approval requested** without asserting
that all other agent work has stopped. Disconnected and ended conversations keep
an editable local draft; Send stays disabled until a connected task is available.
Reloading the interface or reconnecting never resends messages automatically.

The **Last tool failure** card names the failed operation and a maintained cause
with a concrete next step. **Check Permissions** and **Check Receipts** open the
selected task's details; Dismiss hides that event locally until another failure.
Diagnostics contain no request values, raw exceptions or credentials. Uncertain
input outcomes direct you to inspect the website/receipts before another attempt.

For an interrupted or ended conversation, choose **Continue / hand off…**:

1. Write the new instruction; the original local draft is initially copied into
   the editor. **Use last unread message** is explicit and does not assume that
   earlier work had no effects.
2. Optionally check **Include the reviewed recent conversation** and edit that
   snapshot. History is unchecked by default. Long transcripts include a marked,
   UTF-8-safe recent excerpt; files, browser contents and tool results are never
   attached automatically. Review text for secrets.
3. Choose a fresh Codex chat, another connected external agent, or copy for an
   external agent/website chatbot. Inspect the exact message preview (8 KiB total).
4. **Send reviewed continuation** shares once. A fresh Codex chat receives a new
   Steed task and the message waits for its verified connection. **Copy reviewed
   message** only uses the clipboard; paste and send in your chosen client.

Other MCP clients must fetch queued messages through `conversation.read`. Steed
does not claim that sending a continuation automatically wakes an arbitrary client.

The old history/draft remains available. Old grants, live-tab handoffs and queued
messages are not restored or resent. This is a reviewed continuation, not resuming
the ephemeral native Codex thread. An active source task keeps running until you
explicitly stop it. Failed startup explains the problem and sends
nothing. Closing the review while connecting cancels its new, empty task. Profile
changes, private browsing, source selection changes and sidebar reload invalidate
the review. An uncertain send is never retried automatically; check the recipient's
conversation and Receipts before preparing another attempt. Interrupted work may
already have affected a website or external service.

Choose **Agents → Agent Chat Tab** or **Open as tab** in the agent sidebar to move
the same Steed conversation into a full browser tab. **Show sidebar** returns it
to the dock. Sent messages, selected task and unsent draft survive the move;
closing the tab hides the conversation and keeps its task running. Normal saved
tab restoration can reopen this local HTML interface. Its address is
`steed:agents`; it does not load a remote page or confer browser permission. Incognito still clears
and disables the Steed conversation.

For website subscriptions, choose **Agents → Website Chats → ChatGPT / Claude /
Gemini**. These open `chatgpt.com`, `claude.ai` and `gemini.google.com` in normal
tabs using the current profile's ordinary cookies and sign-in. Open another
HTTPS chat website normally to use it too. Choose **Current tab in chat sidebar**
to move that exact page beside your browsing. **Back to tab**, or selecting its
existing tab, returns it without reloading or copying its conversation. Changing
profiles closes the old profile's chat view. Its address, profile/loading status,
**Share instructions…** and **Back to tab** controls now use themed, offline HTML.
The website stays in its original profile's separate engine. Only its displayed
address/status enter the controls; its conversation and draft are not read.
Changing websites or reloading the controls invalidates queued control actions;
interface recovery never replays sharing or reloads the website. Private website chats use the
incognito profile; a provider's server-side retention still applies.

To pass instructions:

1. Open a website chat. While browsing the source page, choose **Agents → Website
   Chats → Share instructions with a chat…**, or **Share instructions…** in the
   website-chat sidebar.
2. Write the instructions in the preview dialog. Optionally include the current
   page's title, address and selected text. This starts unchecked; no full page,
   history, cookies, files, credentials or MCP tokens are automatically included.
   Context is labelled untrusted. URL user information, queries and fragments
   are removed; inspect selected text for anything you do not want to share.
3. Review the exact bounded prompt (at most 8 KiB UTF-8). **Copy instructions**
   puts it on your system clipboard for manual pasting. **Paste into chat**
   focuses one unambiguous visible textarea/contenteditable and uses native
   browser paste, so ordinary website input handlers run. If no unique composer
   is found, use Copy and choose the field yourself.
4. Review and send in the website. Steed does not press Enter, submit the form,
   scrape the reply or treat the message as permission approval. Closing the
   preview, navigating/reloading the destination or switching profiles blocks
   late paste. Profile changes clear the preview's unsent content. Clipboard
   copies remain subject to your desktop clipboard history.

Web chat and tool connection are separate. A prompt cannot make a website's
cloud model reach Steed's local socket/loopback MCP endpoint. Claude's remote
connectors originate in its cloud infrastructure and require a reachable server
([official network requirements](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).
OpenAI documents an outbound private-server tunnel, with its own workspace and
authentication controls
([Secure MCP Tunnel design](https://developers.openai.com/blog/connect-private-mcp-servers-to-openai-products));
Steed has not integrated or validated it. Google's documented remote MCP support
for its agent API
([agent API guide](https://ai.google.dev/gemini-api/docs/antigravity-agent))
does not establish arbitrary MCP support in an ordinary Gemini website chat.
Actual capabilities depend on the chosen product/account. Continue using native
Codex chats or a configured local external agent for current Steed tool access.
An authenticated cloud pairing/bridge is a separate pending milestone; no local
browser authority is exposed to website JavaScript.

#### Conversation tool protocol

`steed_conversation_read` accepts optional `after` (initially `0`) and `limit`
(1–16, default 16). It returns `messages`, `cursor`, and `has_more`. Save the
returned cursor and fetch again until `has_more` is false. Poll for follow-ups at
a modest interval, such as once a second, while the external agent is active.
The cursor represents message updates, not chronological display order: order
messages by `created_sequence` and replace the record with the same `message_id`
when its `sequence` changes. A streaming reply can therefore appear after a later
user message in a change page while retaining its original position in the UI.

A complete reply:

```json
{"message_id": "reply-1", "text": "I have checked the page."}
```

A streaming reply uses cumulative text and consecutive revisions:

```json
{"message_id": "reply-2", "text": "Checking", "state": "streaming", "revision": 0}
{"message_id": "reply-2", "text": "Checking the page…", "state": "streaming", "revision": 1}
{"message_id": "reply-2", "text": "Checking the page… Done.", "state": "complete", "revision": 2}
```

Default state is `complete`, default revision is `0`. Exact current-revision
retries return the same message without adding another activity event. Changed
repeats, skipped revisions, text replacements and edits after completion fail.
Start a new stable message ID for a longer reply. Limits are 256 message records,
1 MiB retained text and 2,048 post/update events per task; returned pages fit the
existing gateway response bound. Task identity is assigned by the browser, never
accepted from MCP arguments. Agents can neither post user messages nor clear a
conversation, approve requests or read another connection's task messages.

The trusted **Clear Chat** button removes message bodies from task rows in both
windows and subsequent tool reads, retaining content-free tombstones so old IDs
cannot be reused. It does not undo actions or remove copies already fetched by
an external client. It is not forensic secure erasure. Activity events retain
only message identity, role, revision and state. Conversation bodies live in the
private task database; schema 16 adds messages and read markers while preserving
older journals and receipts. Historical conversations are not automatically
shared by task recovery.

The incognito search profile disables the sidebar conversation, clears visible
text and unsent drafts, and requires selecting a task again after returning to
regular browsing. A task's existing approved profile stays pinned when you switch
regular browser profiles. Multiple windows can view the same task's sent messages;
unsent drafts stay local to each window. The sidebar loads only when opened, so
ordinary browser startup does not initialize agent task storage through this UI.

Automated sidebar validation (October 3, 2026): the full suite passed 813 tests
in 316.979 seconds, followed by 46 final agent/UI tests in 11.874 seconds and 12
conversation tests in 2.302 seconds. Independent SDK/raw clients exchanged actual
sidebar prompts and streamed replies over stdio and authenticated HTTP while
completing the existing shared browsing, recipe and document-worker workflows.
Startup/render/shutdown and the synthetic dock preview passed. The first full
run crashed during offscreen Vulkan initialization before any test ran; a
standalone retry passed. These fixtures do not demonstrate that a particular
idle agent client can wake or that real accounts have passed acceptance.

### HTTP connection

Enable local agent access in Steed, then start a separate adapter process:

```bash
.venv/bin/python agent_mcp.py --transport http --port 8765 --http-client codex --http-client other
```

It binds only `127.0.0.1`, with endpoint `http://127.0.0.1:8765/mcp`. Omit `--port`
to select an available port. Use a process supervisor to keep the adapter running
independently of a terminal. The GUI continues to use its existing launcher.

The adapter publishes `~/.custom_browser/agent-http.json` with mode 0600. Its
`url` is the endpoint, and `tokens` maps each configured client name to a generated
secret. Configure your HTTP MCP client with that URL and the header
`Authorization: Bearer <that client's token>`. Tokens are generated per adapter
run and are never printed or passed on its command line. Use distinct names and
credentials for different clients; up to four can be configured. Names contain
1–32 letters, digits, underscores or hyphens. Without `--http-client`, the name
is `default`. All methods require authentication; website Origin headers are
rejected. Client tokens use local pre-shared authentication; OAuth discovery is
not implemented, so clients must support custom authorization headers.

`--discovery` selects the browser's gateway metadata; `--http-discovery` selects
the separate HTTP endpoint file. An existing endpoint file is preserved and
startup fails instead of overwriting it. After a crash, confirm that its adapter
is stopped before removing a stale file. Normal shutdown removes the owned
file. Disabling agent access or closing Steed also shuts down this adapter;
restart it after enabling a fresh browser access run.

Each initialized MCP session owns a separate browser connection and task.
Its ID is bound to the credential that initialized it. Complete tasks with
`steed_task_finish`; send HTTP DELETE with `MCP-Session-Id` to end a session and
cancel any unfinished task. HTTP sessions survive network drops, so an ended or
expired HTTP session cancels its task instead of holding it for `task.resume`. Sessions expire after 30 minutes without an HTTP
request or open GET stream, which also cancels the task; `--idle-timeout`
changes this (10–86,400 seconds). The four-session limit bounds abandoned ones.
Closing a single HTTP connection does not cancel
its task. A cancellation notification during an operation closes its browser
connection, cancels its task, and prevents further operations on that connection.
There is no automatic reconnection, operation retry, event replay or restoration
of old grants. Effects of a cancelled/lost request can be uncertain; inspect
evidence instead of blindly retrying. These lifecycle rules follow the
[MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

HTTP uses the SDK's stateful session manager, JSON POST responses, optional GET
event streams and DELETE termination. Limits are four sessions, 64 KiB request
bodies, and one browser operation in flight per session. Browser connections
share the gateway's four-connection limit with stdio clients. Proxy headers are
ignored and access logging is disabled. Pages/profiles stay in the Qt process;
the HTTP adapter uses the same broker and popup approvals as stdio.

### Shared tools and permissions

Tool discovery publishes 73 input schemas. Names follow the internal command
table with `steed_` and underscores, for example `page.inspect` becomes
`steed_page_inspect`. `steed_task_start` creates the connection's task. Arguments
omit `task_id`; the browser assigns it. Opening pages, inspection, observed link
navigation, supported control edits, native form submission, scoped uploads and
downloads, scrolling, waits,
watching, retained drafts, configured app operations, evidence, and task lifecycle
are available. Approval, task enumeration,
raw cookies, JavaScript, arbitrary selectors, unshared file bytes, and raw Qt objects
are not exposed by this adapter. `steed_session_watch` needs explicit `attach`
permission and opens a browser-owned window for restricted task pages. On a live
session it brings the original tab/window forward. Taking/returning control and
live-tab selection are trusted user actions. See
[live delegation](#delegate-normal-live-tabs) and
[visual observation](#website-observation-coverage) for the separate normal-browser
mode and viewport image tool.

`steed_browser_context` needs an active task but no site grant. It returns
`window_id`, `profile`, `profile_selector`, `shared_profile_available`, and
`source`, without open URLs, tab contents, history or credentials. A focused
browser dialog resolves to its parent window. With no recorded focus, a sole
window is selected; multiple windows yield an unavailable context rather than a
guess. Closed windows are excluded. Incognito (`search`) cannot be selected for
shared task sessions, so its selector is null. Global now supports the explicit
`existing:global` selector with ordinary task review and stable identity. Discovery grants
no access, and switching profiles never retargets existing grants or sessions.
Restart Steed after updating the browser source to expose the new context tool.

### Watching and taking control

Restart Steed after updating its source. Enable local agent access and connect
the adapter as described above. To collaborate on an approved task page:

1. Have the agent request `attach` and `read` with its site/profile scope.
   Include `navigate`, `edit`, `scroll`, and `submit` only as needed; the usual
   approval popup applies.
2. Open **Agents → Task Permissions**, select the task and its session, then click
   **Watch Page**. The agent can also call `steed_session_watch` with its
   `session_id` to open this window.
3. Watching shows the same page and profile while the agent continues. The
   rendered page is read-only; opening the window never clones the session.
4. Click **Take Control** after the current page operation finishes. Choose an
   observed link or field and use **Open Link**, **Fill Field**, **Toggle**,
   **Choose Option**, **Select File** with an approved source, **Submit Form**, or
   the scroll buttons. The agent's page
   operations and content reads are blocked until you return control.
5. Click **Return to Agent** to continue delegation. The page and your edits
   remain intact. Old target references are invalidated; the agent must inspect
   again. `steed_session_inspect` reports `control_owner: "user"` or `"agent"`
   so clients can wait for the handoff.

This first takeover supports the same approved, scriptless actions as agent
restricted tasks. Direct clicking/typing inside the watched task page, website scripts,
password/multiple-file/folder forms, and custom controls remain unavailable. Site restrictions,
operation budgets, and grant deadlines still apply during user control;
ordinary browsing windows remain usable on the same profile.
Take Control pauses page operations; app operations belong to the task and use
their own cancellation controls in the Apps panel.

Closing an idle watch window, including Escape or closing its parent browser
window, returns control and leaves the task page alive while another browser
window remains. Closing during a pending user mutation cancels the task rather
than resuming the agent with an uncertain action. **Cancel Task**, revocation,
expiry, completion, disabling access, and last-window shutdown dispose owned
pages and close their watch windows. Cancellation cannot undo an effect already
sent to a website. Shared-profile cookies/storage follow the existing profile's
normal lifetime.

### Continuing previous work

Recovery starts a new follow-up task; it never reopens an old task, adopts its
connection, restores pages/grants or dispatches an old operation.

1. Have the agent call `steed_task_start`, then `steed_task_recover` before
   requesting a grant or doing work. It cannot enumerate archives or supply a
   source task ID. The browser opens a nonmodal **Continue previous work?** picker.
2. Select a finished, cancelled, failed or interrupted task. Check up to ten
   drafts under **Drafts** and up to ten sealed files under **Files** to share;
   neither list is selected by default. Files are bounded to 8 MiB each and
   32 MiB total. Drafts have plain-text previews; files show name/type/size/hash
   without rendering or running their contents. Sharing can disclose the selected
   material to the agent's model provider. The evidence checkbox shares prior permission scope, up to 50
   activity entries, the selected drafts' recorded source inspections and app
   receipt summaries. Receipt text is limited to 512 characters with an explicit
   truncation flag. Source profile/scope is historical information only.
3. Click **Share Selected Material**, or **Deny** to disclose nothing. Closing or
   Escape leaves the request pending; reopen it in **Agents → Task Permissions →
   Recovery → Choose Recovery Material…**. This tab also shows retained context.
4. The agent polls `steed_task_recovery`. After approval it receives only the
   selected context, new artifact IDs and `context.imported_files` mappings.
   Each file mapping has fresh `file_id`, `source_file_id`, name/type/size/hash;
   it contains no file bytes. Draft bodies still require a fresh
   `artifact` grant; use `steed_artifact_list`/`steed_artifact_read` after approval.
   File bytes separately require fresh `workspace` permission and
   `steed_workspace_list`/`steed_workspace_read`.
   Original draft IDs and source-event/target references belong to the old task
   and cannot be used as current observations.
5. Request the current sites, profile, actions and app destinations as usual.
   Recovered tasks always show a fresh access prompt, even when a saved rule
   would cover the request. Open fresh pages and inspect again before acting.

Copies have independent IDs and lifetime. Deleting an original draft/file does not
delete shared copies. Draft/file imports, source provenance, evidence and write protections
commit together in `tasks.sqlite` (current schema 14; file recovery itself added no schema migration). Copies are checked against their stored size/hash before sharing.
Only selected files enter the new workspace, marked `user_shared`; unselected,
deleted, building and foreign files are rejected. Imported source is data:
packages need fresh staging and exact code approval. Old package registrations,
worker receipts and remembered execution approvals are never copied. File provenance
is recorded in recovery mappings, value-free activity events and the Workspace panel.
Selection is bounded to ten drafts and ten files, the
context to 128 KiB and ancestry to 32 tasks. Oversized evidence can be omitted.
Restart interrupts follow-ups and ends pending pickers; another follow-up needs
another user selection and fresh approval.

Before a new app mutation, Steed compares its connector, tool, effect, exact
destination and body hash with writes recorded in the selected task's ancestry.
A matching completed or uncertain call raises `recovery_write_already_recorded`
before dispatch, even with a new request ID or a recreated identical draft.
Deleting the original draft or omitting evidence does not remove that protection.
Current receipt outcomes are checked; calls confirmed `not_applied` or cancelled
before dispatch can be attempted under a new write grant only after the original
worker has ended. Normal duplicate request-ID protection still applies within
the new task.

This is a bounded check for identical app requests, not general provider
idempotency: changed bodies, different connectors/destinations, independent tasks
and website form effects cannot be identified as the same intent. No historical
action is automatically replayed. Review uncertain outcomes in the original
**Apps** panel or provider UI. Shared archived receipts support the read-only
checks below; deliberate repeat-write overrides are not implemented. Gmail remains
send-only with manual verification. Historical text and provider results remain untrusted.

### Checking an archived app receipt

1. Start a new task and call `steed_task_recover` before requesting access. In the
   popup, choose the **original task** and include its evidence/receipt summaries.
   Draft selection is optional for a receipt check.
2. Poll `steed_task_recovery`. Pick an uncertain receipt from its shared
   `context.app_receipts`; callers cannot enumerate other archives or choose a
   foreign source task. Use `steed_app_list_tools` to check `can_reconcile` and
   `reconcile_effect` (`read`). Unsupported tools require manual verification.
3. Request `actions: ["app"]` with the source receipt's exact `connector`, `tool`
   and `destination`, and **`effect: "read"`**. Steed shows a fresh approval popup.
   Checking a previous write needs no draft, artifact or mutation permission.
4. Call `steed_app_reconcile_history` with `source_request_id` from the shared
   receipt and a new stable `request_id` for the check. Poll `steed_app_status`
   using the **new** request ID. Duplicate IDs return the same receipt. Cancel
   via `steed_app_cancel`; an uncertain check can be checked again with
   `steed_app_reconcile` after its worker ends.

The new task owns the check, its budget, live grant and receipt. The trusted
lookup callback receives the **original** provider operation ID, original request
ID and destination, with no draft body. Its original write callback is never
called. The old task stays finished/interrupted and its grants stay ended.

Confirmed outcomes update the new receipt and unresolved original receipt in
one transaction. A failed/empty lookup, timeout, cancellation or restart leaves
original uncertainty intact. A `not_applied` report while the original worker
is running stays uncertain until that worker ends. Late older workers cannot erase
independently confirmed evidence. Restart retains source mappings without replaying provider
requests. Google Drive's absent search result never proves `not_applied`.

**Agents → Task Permissions → Apps** shows shared receipts as **Previous** rows.
Select an uncertain row and use **Reconcile Uncertain Result** after fresh read
approval. The new check appears as a separate READ receipt. Archived receipts of
checks themselves are not original provider operations: choose the original task
when checking again. Gmail send has no read-only lookup; use **Sent** manually.

### Results and connection limits

Results include matching text JSON and `structuredContent`, wrapped in
`{"result": ...}`. Errors use `isError: true` and
`{"error": {"code": ..., "message": ...}}`. The SDK also rejects invalid tool
names/schemas. Browser errors omit exception strings and tracebacks. Page content
is untrusted, field values are omitted, references become stale after changes,
and existing read/edit/submit permissions still apply. Connection loss or a
cancelled request may follow a site effect; no operation is automatically retried.

The browser gateway uses a private Unix socket in a temporary directory (0700),
a 0600 discovery file, and an ephemeral token. It accepts at most four connections,
with one active task and one in-flight command per connection. Browser-side validation
rejects caller-selected task identities and unsupported tools even if the adapter
is bypassed. Requests are limited to 64 KiB and responses to 1 MiB. Authentication
times out after five seconds; an operation exceeding 30 seconds closes its
connection and cancels its task. Disabling access, journal failure, disconnect,
and last-window shutdown remove access and dispose owned pages before profiles.
The MCP adapter queues up to 32 active/waiting calls per connection, transmitting
one at a time. Parallel discovery therefore does not reject sibling requests.
Queue waiting is bounded to 35 seconds; overflow/queue-timeout errors explicitly
say that the affected call was not dispatched. Cancellation while waiting leaves
the active call alone. Cancellation/failure after dispatch closes the connection,
fails queued calls and never replays a potentially uncertain operation.
The dedicated Codex entry allows 75 seconds for a tool call, covering queue
admission plus its bounded dispatched response; the gateway's 30-second operation
timeout still applies. Other MCP clients choose their own transport deadlines.
Rejected tool/argument requests with an assigned task record a value-free denial
event; execution failures record a value-free `client.rejected` diagnostic.
Local processes running as your user can read the connection token and request
new tasks; they still cannot adopt another connection's task or approve grants.

Verified compatibility is MCP `2025-11-25` over stdio and Streamable HTTP, using
an SDK client and an independent JSON-RPC client on each transport. The full A4
milestone remains in progress: reconnect/resume semantics, additional protocol
revisions and broader client compatibility remain pending. Same-page watch and
scoped user handoff are implemented; broader collaboration UI is tracked in A7.

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
```

Tests that wait for engine work (renderers, helper processes, workers) give up
after `STEED_TEST_WAIT` seconds (default 30). Waits return as soon as their
condition holds, so passing runs are no slower; the limit only decides how long a
real failure takes to report. Raise it on a very busy machine. The full suite
passes with the default while every core is saturated (load average about 27 on
8 cores).

For headless Linux checks:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python tests/smoke_browser.py
QT_QPA_PLATFORM=offscreen python tests/smoke_google_login.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_active_content.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py --transport http
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py --documents --recipes
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py --transport http --documents --recipes
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_codex_provider.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_google_connector.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_google_documents.py
python tests/smoke_password_manager.py
```

Tests use temporary profiles and local content. Chromium still needs to initialize its process sandbox; a restricted container may require running the tests outside that container's sandbox.

Qt unittest classes use `tests/qt_test_support.py` to share one QApplication
throughout the process. Register profile fixture directories with
`keep_directory()` so they are removed only after final engine shutdown. Do not
destroy and recreate QApplication between test classes using WebEngine.

The Google compatibility smoke check also needs `openssl`. It uses a temporary loopback HTTPS server, maps the Google account host to that server for this test process, and trusts only its generated certificate key. It never contacts Google or uses an account.

Recipe TLS regressions also require `openssl` and use temporary certificates and a
local HTTPS server. Their socket fixture maps a validated public numeric address
to that server without external connections. Recipe account checks use an in-memory
synthetic keyring. `--recipes` adds source proposal, the actual default-Deny review
popup, trusted account setup, persistent access and selected-field reads to both
MCP clients. Version 2 also exercises exact payload previews, create/send receipts,
uncertain completion and read-only reconciliation without write replay. `--documents` additionally requires the installed optional bundle.

The active-content audit prints JSON with observed requests, STUN packet counts,
script execution results, and `tested_routes_contained`. Exit zero means its
positive controls and scriptless checks completed; it does not approve scripts.
The current forced-script probes report `tested_routes_contained: false`.

The optional MCP smoke requires `requirements-mcp.txt`. It runs an SDK client
and a separate JSON-RPC client against a temporary, synthetic signed-in profile.
The fixture alone clicks the approval popup and invokes trusted cancellation.
It checks native edits/submission/downloads/uploads, watch/user handoff/return, blocked agent
operations during takeover, stale handles, evidence without field values,
shared cookies/profile identity, concurrent ordinary user POST, ownership denial,
and task disposal. The trusted fixture alone uses the user controls to enter
synthetic text. It saves `/tmp/steed-watch-stdio-preview.png` or
`/tmp/steed-watch-http-preview.png` during takeover. It never accesses an existing
profile/account or proves that
a particular AI client/provider will plan a workflow successfully.
The HTTP mode additionally checks credential-bound transport identity, raw-client
document retrieval/completion, and DELETE cancellation of an owned task/page.
It starts the real HTTP adapter process and verifies shutdown and endpoint-file
removal when browser access is disabled. HTTP unit tests skip when the optional
SDK is absent.

The Google connector smoke uses synthetic desktop-client credentials, an in-memory
secret store and a local HTTP fixture. It exercises the real OAuth callback/PKCE
and API request code while routing its fixed Google endpoints only to that fixture.
It checks draft → Doc → email, lost TCP acknowledgements, empty read-only lookup,
stable receipts, restart without grants/replay and a fresh read-only archived Doc
check using the original operation property. It does
not contact Google or test HTTPS certificates, a real desktop keyring or consent UI.
It saves `/tmp/steed-google-connections.png`, `/tmp/steed-google-approval.png`,
`/tmp/steed-archived-read-approval.png`, `/tmp/steed-archived-receipts.png` and
`/tmp/steed-retention-receipts.png`. It also verifies user-cleared source/check
results while retaining their states, operation identities and no-replay behavior.
Neither optional SDK nor SecretStorage is needed for this synthetic smoke.

The Google documents smoke uses an in-memory credential store and the production
Google HTTP adapter routed to localhost. It checks folder → Doc → tab inspection
→ append, private text isolation between tabs, a lost append response, restart,
fresh read-only archived confirmation without draft copying, and atomic revision
rejection. It also shows the before/after popup, applies one user-reviewed replacement,
loses its TCP acknowledgement and confirms its operation range without replay.
It records one folder, Doc, append and replacement and sends no email.
It saves `/tmp/steed-google-document-receipts.png` and
`/tmp/steed-google-document-review.png`. It does not contact Google or
establish real account, consent, certificate or desktop-keyring acceptance.

## Verification record

### Daily agent reliability — October 13, 2026

The final full suite passes **1,405 tests in 454.935 seconds**; 46 final sidebar,
permission/takeover/watch checks pass in 17.583 seconds. Both independent SDK/raw
MCP clients pass stdio and authenticated HTTP with private screenshot click/type/key,
reviewed live takeover/return, connector recipes/documents and retained account
panels. Both installed Codex runtime paths pass with synthetic local responses;
startup/render/shutdown and light/dark previews pass. Ruff (247 files), dependencies,
whitespace, all 85 client routes and panel/recipe examples pass. User data, real
accounts and external model calls were not used. Desktop/live-provider acceptance
remains manual. See ROADMAP for logs and the separately recorded offscreen Qt
profile-startup crash; one transport retry reproduced it before task setup, then
subsequent transport/native/startup checks passed. No production crash fix is claimed.


Codex sidebar integration (October 3, 2026): the deterministic provider suite
exercises real child processes, Qt IPC and synthetic Steed tasks without models
or accounts. Coverage includes automatic turns, ordered/deduplicated follow-ups,
early notifications, UTF-8 chunks, clearing during a stream, cancellation,
incognito, task/connection isolation and failed sign-in/configuration/protocols.
The separate `tests/smoke_codex_provider.py` requires an installed Codex CLI and
uses a temporary authentication/configuration directory, a loopback model
endpoint and a harmless inherited MCP marker. It verifies a native newer-model
code-runtime call into the bound Steed task, streaming follow-ups, native
capability preservation and adapter shutdown without external model calls.
The native smoke also checks the reported model, concurrent capabilities/context/
enabled-folder/connector discovery, live-tab denial without a user handoff, and a
synthetic workspace grant explicitly approved by the fixture's user, under a
native never-ask policy. It uses no real files/accounts and opens no page or
external application. Pass `gpt-5.4` to check the direct-tool path. Real
authenticated model replies and desktop browsing acceptance remain separate user
checks. Validation totals are
tracked in ROADMAP.md.

Document worker bundle (October 2, 2026): **679 tests passed in 314.002 seconds**
(`/tmp/steed-documents-full-final.log`). Sixty-one final worker checks passed in
12.902 seconds, ten store/migration checks in 1.070 seconds and eleven upload
checks in 20.552 seconds (`/tmp/steed-documents-focused-final.log`,
`/tmp/steed-documents-store-final.log`, `/tmp/steed-documents-uploads.log`). Coverage
includes real PDF/XLSX/PNG generation, native Pillow imports, missing/unsupported
bundles, exact runtime/environment approvals, changed helper snapshots, wheel
tampering, traversal/symlink/device archives, expansion/collision limits, inert
`.pth` files, atomic offline install, restart and schema-12/partial migration.

The maintained bundle was downloaded and verified, with no host dependency
installation (`/tmp/steed-documents-install-verified.log`). Actual XLSX creation
initially exposed CPython's `FIOCLEX` requirement; a traced synthetic failure led
to that single argument-filtered syscall rule, with other ioctl/network/process
operations still denied. Initial external MCP acceptance caught a missing runtime
argument length bound; it was corrected and regression-covered. Final SDK and
independent raw clients generated/read real documents and completed existing
browser/app/recovery workflows over both transports
(`/tmp/steed-documents-mcp-stdio-verified.log`,
`/tmp/steed-documents-mcp-http-verified.log`). Approval previews were inspected
(`/tmp/steed-document-worker-approval.png`, `/tmp/steed-worker-stdio-preview.png`).
Browser startup/render/shutdown passed (`/tmp/steed-documents-startup.log`).

The earlier 677-test full run hit `native_input_unavailable` in the existing stale
upload-reference check (`/tmp/steed-documents-full.log`); the focused upload group
and final full suite passed. Keep this intermittent native-input observation with
desktop acceptance rather than claiming a browser-engine fix. Ruff/format,
dependency health, exact lock consistency, whitespace and local doc links passed.
Native acceptance here used Linux x86-64/CPython 3.12, temporary profiles/files and
mock account effects. Other platforms and real-account/desktop acceptance remain
pending; Google registration was not changed.

Worker setup diagnostics (October 2, 2026): **657 tests passed in 285.324 seconds**
(`/tmp/steed-worker-setup-full.log`). Forty focused worker checks passed in 7.464
seconds (`/tmp/steed-worker-setup-focused.log`), including eleven new checks for
actual sandbox readiness, no granted authority/data use, caching across callers,
background execution, sanitized failure, trusted retry, denied probe injection,
shutdown/join, restart reset, actual-run revalidation and UI guidance.
The SDK and independent clients discovered support before grants, generated an
attachment and recovered its selected bytes over stdio/HTTP
(`/tmp/steed-worker-setup-mcp-stdio-final.log`,
`/tmp/steed-worker-setup-mcp-http.log`). The first stdio attempt exited natively
with the tracked Qt startup 139 before the workflow; identical retry passed.
Setup preview was inspected (`/tmp/steed-worker-setup.png`) and browser startup/
render/shutdown passed (`/tmp/steed-worker-setup-startup.log`). Ruff/format,
whitespace, dependency and local doc link checks passed. Synthetic accounts/state
only; shared Google registration remains deferred. No extra worker dependencies
or direct networking were introduced.

Selected-file recovery (October 2, 2026): **646 tests passed in 274.034 seconds**
(`/tmp/steed-file-recovery-full-final.log`). Thirty-five focused recovery checks
passed in 4.097 seconds; seventeen final file-recovery checks passed in 2.045
seconds (`/tmp/steed-file-recovery-focused.log`,
`/tmp/steed-file-recovery-focused-final.log`). Coverage includes selected binary
fidelity, provenance/fresh IDs, independent deletion/restart lifetime, workspace
permission, fresh approval despite saved rules, inert copied source, corrupt/
foreign/building/deleted input denial, limits and complete transaction rollback.
Both SDK and independent clients recovered generated ZIP attachments over both
MCP transports (`/tmp/steed-file-recovery-mcp-stdio-final.log`,
`/tmp/steed-file-recovery-mcp-http-final.log`). The initial MCP run caught a missing
recovery-context lookup in the Workspace preview; it was corrected and covered
for ordinary tasks before the final suite. The superseded full run was stopped.
The Files picker screenshot was inspected (`/tmp/steed-workspace-recovery.png`),
and real-entry-point startup/render/shutdown passed
(`/tmp/steed-file-recovery-startup.log`). Ruff, format, whitespace and local doc
links passed. All files/accounts were synthetic; no Google registration changes.

Local-worker milestone (October 2, 2026): **629 tests passed in 256.190 seconds**
(`/tmp/steed-worker-full.log`). Twenty-nine focused worker tests passed in 5.877
seconds (`/tmp/steed-worker-focused.log`), including real Bubblewrap/seccomp
host-file/environment/socket/process denial, hard resource limits, memory/CPU/wall
exhaustion, protocol bounds and private scratch cleanup. Manager coverage includes
default-Deny source review, separate exact approval, remembered-hash/task isolation,
no replay, declared app/destination checks, typed broker boundaries, operation budgets,
enqueue/cancel races, lifecycle/source deletion, schema-11 migration and journal
failure before execution/after completion. Setup failure never falls back to host code.

The SDK and independent JSON-RPC client each built and ran a ZIP generator after
the actual trusted package popup, then read and verified its CSV attachment over
both stdio and HTTP (`/tmp/steed-worker-mcp-stdio.log`,
`/tmp/steed-worker-mcp-http.log`). Existing shared-profile browsing, watch/handoff,
Doc/email writes, archived read-only checks and retention workflows still passed.
Popup/results previews were visually inspected (`/tmp/steed-worker-approval.png`,
`/tmp/steed-worker-results.png`). Browser startup/render/shutdown, workspace picker/
export controls and synthetic Google HTTP reviewed replacement/recovery passed
(`/tmp/steed-worker-startup.log`, `/tmp/steed-worker-workspace-ui.log`,
`/tmp/steed-worker-google-review.log`). Ruff/format, dependency, whitespace and
local documentation-link checks passed. These used temporary state and synthetic
credentials/effects, not real account/keyring or desktop acceptance. At that
checkpoint, workers shared the host kernel and supported standard-library Python
only. The subsequent document bundle adds maintained dependencies; arbitrary new
providers remain separate work.

Standing connector access stage (October 2, 2026): **600 tests passed in 248.392
seconds** (`/tmp/steed-access-full.log`), including nine connector-switch
regressions (`/tmp/steed-access-focused.log`). Twenty-one existing account setup/
disconnect checks passed (`/tmp/steed-access-connections.log`). Coverage includes
new destinations under exact live grants, separate read/send/direct permissions,
revocation and saved-rule clearing, resource limits, no inherited file access,
immutable account identity, restart, new-tool partial state, trusted UI clicks,
and settings/approval journal failures. Accounts, credentials and effects were
synthetic; real Google consent and desktop keyring acceptance remain pending.

Generated-file/custom integration groundwork (October 2, 2026): **590 tests passed
in 255.599 seconds** (`/tmp/steed-workspace-full.log`), plus twenty-one final focused
workspace checks (`/tmp/steed-workspace-focused-final.log`). Coverage includes
arbitrary binary fidelity, chunk retries, bounded declarations/reads, integrity,
task isolation, live/revoked permission, exact export paths and size, symlink rejection,
user-selected snapshots, retained user export/deletion, schema-9 migration,
rollback and inert package declarations. Both independent MCP clients passed
binary upload/read/export and package staging over stdio and HTTP, alongside the
existing browser/connector/recovery workflows (`/tmp/steed-workspace-stdio.log`,
`/tmp/steed-workspace-http.log`). The first HTTP attempt caught a grant-schema
length limit; the schema was corrected. The first stdio attempt hit the tracked
native Qt startup exit 139; the identical retry passed.

The Workspace user-picker fixture passed (`/tmp/steed-workspace-ui.log`), and its
final plain-language layout was inspected (`/tmp/steed-task-workspace.png`).
Run it with the headless prefix and `.venv/bin/python tests/smoke_task_workspace.py`.
The Google HTTP review/recovery fixture and startup/render/shutdown passed
(`/tmp/steed-workspace-google.log`, `/tmp/steed-workspace-startup.log`). Ruff/format
and whitespace checks, dependencies and 74 local documentation links passed.
Files, account data and credentials were synthetic;
real-account/desktop acceptance remains pending. Bubblewrap 0.9.0 launched a
harmless isolated process with no home/network; no generated source was executed.
That groundwork did not execute source. The subsequent local-worker milestone
adds isolated execution with separate exact-package approval; see the current
record above.

Reviewed document-edit stage (October 2, 2026): **568 tests passed in 254.019
seconds** (`/tmp/steed-reviews-full.log`), plus fifteen final focused proposal regressions
(`/tmp/steed-reviews-focused.log`). Existing Google adapter/gateway checks and the
production-transport local HTTP workflow passed (`/tmp/steed-reviews-google.log`,
`/tmp/steed-reviews-gateway.log`, `/tmp/steed-reviews-http.log`). The review screenshot
was inspected: account/destination, current/proposed text, diff and default Deny
are visible. Tests cover user-only review, fresh/saved reviewed grants, separate
reusable direct grants, exact draft/revision binding, scrubbed previews, cancellation,
restart, storage failures, foreign tasks and uncertain replacement recovery.
All provider data and credentials were synthetic; real Google acceptance is pending.
Both independent MCP clients over stdio and HTTP, startup/render/shutdown,
Ruff/format and diff checks passed (`/tmp/steed-reviews-mcp-stdio.log`,
`/tmp/steed-reviews-mcp-http.log`, `/tmp/steed-reviews-startup.log`).

Google folder/document stages (October 2, 2026): folder milestone `e74dd12`
passed **531 tests in 246.031 seconds** (`/tmp/steed-folders-full.log`); the
following document milestone passed **555 tests in 252.809 seconds**
(`/tmp/steed-docs-full.log`). Fifty-two focused Google checks and 21 gateway
checks passed (`/tmp/steed-docs-focused.log`, `/tmp/steed-docs-gateway.log`).
Coverage includes exact parent/tab grants, account and ownership checks,
immutable drafts, UTF-16 offsets, nested tabs, bounded output, revision conflicts,
lost/malformed responses, unique-range checks, read-only archive confirmation,
and cancellation before mutation. The production-transport localhost workflow
passed with one folder/Doc/append and no repeated mutation
(`/tmp/steed-docs-http.log`). Its receipt screenshot was inspected.
The previous OAuth/Doc/email smoke, SDK and independent MCP clients over both
transports, startup/render/shutdown, Ruff/format, dependency checks and 33 local
documentation links also passed (`/tmp/steed-docs-google.log`,
`/tmp/steed-docs-mcp-stdio.log`, `/tmp/steed-docs-mcp-http.log`,
`/tmp/steed-docs-startup.log`). All account acceptance here is synthetic;
real Google consent/accounts, desktop keyring and hardware checks remain pending.

Provider result-retention stage (October 2, 2026): **518 tests passed in
255.304 seconds** (`/tmp/steed-retention-full.log`). Seventeen focused checks passed
in 1.486 seconds (`/tmp/steed-retention-focused.log`), covering owned/bulk clearing,
foreign IDs, idempotency, identity/state/draft preservation, historical-write guards,
shared-summary removal, queued/late writes, cleared source/probe confirmations,
restart, schema-7 migration, journal rollback, corrupt snapshots, agent API denial
and default-No UI confirmation. Clear markers suppress future text/link storage
while outcomes and independently confirmed evidence remain intact.

SDK and independent JSON-RPC clients observed user-cleared results and summaries
and still rejected matching historical writes over both transports
(`/tmp/steed-retention-mcp-stdio.log`, `/tmp/steed-retention-mcp-http.log`). Provider
writes stayed at exactly two documents and two emails across clients. Shared-profile
browsing, watch/handoff, uploads/downloads, cancellation and cleanup also passed.
The synthetic Google HTTP/OAuth fixture cleared source/check results and retained
states and operation identity, with exactly one Doc/email and no extra mutations
(`/tmp/steed-retention-google.log`). The clearing controls and timestamp preview were
visually inspected using `/tmp/steed-retention-receipts.png`. Startup/render/shutdown
passed on Qt 6.11.2 (`/tmp/steed-retention-startup.log`). Ruff, formatting, whitespace
and dependency checks passed. No real accounts, desktop keyrings or hardware were used.

Archived app receipt stage (October 2, 2026): the full **501-test suite passed
in 245.296 seconds** (`/tmp/steed-archived-final-full.log`). After the final guard
against an absence report while the original worker is running, all twenty-two
focused regressions passed in 2.250 seconds
(`/tmp/steed-archived-focused-final.log`). Coverage includes
original provider/request identity, fresh read-only authority without draft access,
exact destinations, unshared/foreign receipts, unsupported lookup methods, stable
request IDs, cancellation before/during dispatch, account disconnect, timeouts,
operation limits, late-worker confirmation, atomic rollback and schema-6 migration.
Restart preserves probe mappings without network activity, old grants or write replay.

SDK and independent JSON-RPC clients each checked archived uncertain receipts
under fresh read permissions over both MCP transports
(`/tmp/steed-archived-mcp-stdio.log`, `/tmp/steed-archived-mcp-http.log`). Mock writes
remained exactly two documents and two emails across the clients. Historical-write
denial, selected draft reads, shared-profile user browsing/POST, watch/handoff,
uploads/downloads, cancellation and cleanup checks also passed.

The synthetic Google HTTP/OAuth fixture passed lost acknowledgements, an empty
lookup that stays uncertain, restart with no provider I/O, fresh read-only lookup
using the original Drive property, and manual-only Gmail handling
(`/tmp/steed-archived-google.log`). It recorded exactly one document and one email.
The read approval and Previous/READ receipt UI were visually inspected.
Startup/render/shutdown passed on Qt 6.11.2 (`/tmp/steed-archived-startup.log`).
Ruff, formatting, whitespace, dependency and 32 local documentation-link checks
passed. No real accounts, desktop keyrings or hardware were exercised.

Safe task follow-up stage (October 2, 2026): **479 tests passed in 253.137 seconds**
(`/tmp/steed-recovery-final-full.log`), including nineteen recovery regressions,
the automatic recovery/access popup sequence and external connection isolation.
Coverage includes selected-draft disclosure, fresh access despite saved approvals,
source provenance, atomic rollback, checksum failures, schema-5 migration,
selection/context/ancestry limits, restart, deleted originals, recreated bodies,
current ancestral receipt outcomes, valid new work after a denied duplicate and
storage failures that freeze authority. No old pages or grants are resumed.

SDK and independent JSON-RPC clients each completed source selection, fresh
approval, retained-draft reads and historical-write denial over both transports
(`/tmp/steed-recovery-mcp-stdio-final.log`, `/tmp/steed-recovery-mcp-http-final.log`).
The mock provider still recorded exactly two documents and two emails across the
two clients; follow-ups added no writes. Shared-profile/user POST, watch/handoff,
uploads/downloads, cancellation and cleanup checks passed in those workflows.
The recovery picker was visually inspected using synthetic drafts and receipts.
Startup/render/shutdown passed on Qt 6.11.2 (`/tmp/steed-recovery-startup.log`);
Ruff, formatting, whitespace, dependency and 31 local documentation-link checks
passed. Real accounts/keyrings and hardware were not used. Historical provider
probes through fresh tasks remain outside this checkpoint.

Permission/setup usability stage (October 2, 2026): the complete **458-test suite
passed in 247.049 seconds** (`/tmp/steed-permissions-acceptance.log`). New regressions
cover exact site/action/account/destination matching, profile identity and
rename/recreation, non-union of rules, atomic approval/provenance, revocation,
restart and disconnect, corrupt-state failure, website scope/private isolation,
save failures, page-bound profile selection, and reusable Google client setup.
Thirteen app connection checks and seventeen saved-approval checks passed after
the final UI changes (`/tmp/steed-permissions-ui-final.log`,
`/tmp/steed-approval-final.log`); setup and permission dialogs were visually
inspected using synthetic accounts.

The real Chromium notification fixture passed one-time approval followed by a
fresh prompt after navigation, saved approval reuse, forgetting, and exclusion
from private browsing (`/tmp/steed-site-native.log`). Run it separately:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" \
  .venv/bin/python tests/smoke_site_permissions.py
```

It uses temporary profiles and a loopback HTTP page, with real Chromium permission
requests and synthetic choices. It does not test OS notification delivery or
camera, microphone, location or screen hardware. Synthetic OAuth/API workflow
acceptance passed (`/tmp/steed-permissions-google-http.log`), as did independent
MCP clients over stdio and HTTP (`/tmp/steed-permissions-mcp-stdio.log`,
`/tmp/steed-permissions-mcp-http.log`) and startup/render/shutdown on Qt 6.11.2
(`/tmp/steed-permissions-startup.log`). Chromium's startup smoke required execution
outside the tool filesystem sandbox; this is separate from the tracked native
startup crash. Lint, formatting, whitespace, dependency and local-link checks
passed. No real Google account/keyring or hardware acceptance was performed.

Earlier verification records follow.

Google connection stage (October 2, 2026): the full 413-test suite passed in
245.201 seconds (`/tmp/steed-google-full.log`). Thirty-nine new checks cover account
identity/storage, setup/PKCE/state, tokens/scopes/rotation, destination validation,
plain-text request mapping, uncertain writes, cancellation/disconnect, trusted UI
and provider extensibility. Final account/provider/gateway/UI checks passed 58
tests in 2.626 seconds (`/tmp/steed-google-focused-identity-final.log`). The local HTTP
workflow passed (`/tmp/steed-google-http-identity-final.log`), applying exactly one
synthetic document/email despite lost acknowledgements, with read-only Doc recovery
and no restart replay. Independent SDK/JSON-RPC MCP workflows passed over stdio
and HTTP (`/tmp/steed-google-mcp-stdio.log`, `/tmp/steed-google-mcp-http.log`), and
ordinary startup/render/shutdown passed (`/tmp/steed-google-startup.log`). Account/scope
UI previews were checked. Ruff, formatting, dependencies and whitespace passed.
Final metadata-size and account-label checks passed the focused suite too.
Real Google OAuth, account writes and desktop keyring acceptance remain pending;
no real account was connected.

Connector gateway stage (October 2, 2026): the full 374-test suite passed in 225.006
seconds (`/tmp/steed-connectors-full.log`). Nineteen focused checks passed in 1.142
seconds (`/tmp/steed-connectors-focused-final.log`), covering trusted effect mapping,
exact destinations, artifact/receipt ownership, independent provider IDs, duplicate
and changed requests, queued cancellation, live timeout/capacity, expiry/budgets,
revocation, late actual outcomes, journal failures, restart recovery, draft deletion
before dispatch and retained plain-text user receipt/reconciliation controls.
Both real MCP transports completed research/draft → mock document → mock email,
with exactly one document and one email in the synthetic provider database
(`/tmp/steed-connectors-stdio.log`, `/tmp/steed-connectors-http.log`). An intentionally
lost email acknowledgement was reconciled read-only without resending; an outside
recipient was denied. Remote tool annotations falsely claimed read-only operations
while trusted browser mappings still required write/send authority. Startup passed
(`/tmp/steed-connectors-startup.log`), and receipt/approval previews were inspected.
The final receipt text layout was checked separately by the connector/UI group
(`/tmp/steed-connectors-ui-final.log`). No real accounts or emails were used.
The final dispatch-status adjustment (app-only tasks become running) also passed
all nineteen connector checks in 1.148 seconds
(`/tmp/steed-connectors-status-final.log`). Lint, formatting, whitespace, dependency
consistency and 26 local documentation file links passed.
The expanded final smoke checks passed with an SDK client and an independently
implemented JSON-RPC client each completing the full workflow over both transports
(`/tmp/steed-connectors-independent-stdio.log`,
`/tmp/steed-connectors-independent-http.log`). Each fixture's provider database had
exactly two documents and two emails, one of each per client, including independent
reconciliation of lost send acknowledgements and task-specific provider IDs.

Task artifact stage (October 2, 2026): the full 353-test suite passed in 245.973
seconds (`/tmp/steed-artifacts-full.log`). Twelve final focused artifact checks
passed in 0.725 seconds (`/tmp/steed-artifacts-focused-final.log`), including
content/metadata transactions, UTF-8/count/total quotas, source-event ownership,
grants and budgets, revocation, restart retention, schema migration, plain-text
completed-task review, confirmed user deletion and denial of agent deletion.
Failed evidence writes prevent disclosure or roll back deletion. Both MCP
workflows stored/listed/read sourced synthetic drafts
(`/tmp/steed-artifacts-stdio.log`, `/tmp/steed-artifacts-http-retry.log`). The first
HTTP run hit the previously tracked native Qt startup exit 139; identical retry
passed. The native issue remains unresolved. Startup/render/shutdown passed
(`/tmp/steed-artifacts-startup.log`); the synthetic artifact panel was visually
checked. No real account or app provider was used.

Scoped upload stage (October 2, 2026): eleven focused checks passed in 15.751 seconds
(`/tmp/steed-upload-focused-release.log`), covering immutable copied bytes, exact
source/site authority, symlinks, size limits, enabled-script denial, mid-copy
revocation, remaining staging budget, stale references, unsupported multiple files,
user file selection,
redirect replay and actual shared-profile cookies. Both MCP workflows passed
with separate selection/submission and a changed original source after copying
(`/tmp/steed-upload-stdio-final.log`, `/tmp/steed-upload-http-final.log`); the submitted body
contained the original copy. No file bytes appeared in task events.
The full 343-test suite passed in 233.164 seconds
(`/tmp/steed-upload-complete.log`); startup/render/shutdown passed
(`/tmp/steed-upload-startup.log`). The first full run found one permission-dialog
wording regression; the dialog now distinguishes grants with and without upload
authority. The final wrapped-label adjustment was checked separately by six
popup checks, which passed in 2.034 seconds
(`/tmp/steed-upload-approval-final.log`), including exact file/site disclosure and
wrapped-label sizing. Long scopes scroll while Approve/Deny stay visible;
the synthetic upload popup was visually checked at the top and bottom.
Ruff, formatting, whitespace, dependency consistency and local file links passed.
These are synthetic local fixtures, not acceptance of real authenticated upload
sites or scripted widgets.

Task activity and retained download receipts stage (October 2, 2026): three
real-engine UI checks passed in 1.855 seconds (`/tmp/steed-activity-focused.log`),
covering retained results, per-task selection, hidden form values, user stream
cancellation, and close-time polling cleanup. Nine storage tests passed in
0.635 seconds (`/tmp/steed-activity-store.log`), including bounded, filtered,
chronological event tails without changing external client cursors.
Both MCP workflows passed with visible receipt previews
(`/tmp/steed-activity-stdio.log`, `/tmp/steed-activity-http.log`), including the
existing handoff/download/concurrent-user checks. The synthetic Downloads panel
was visually inspected from `/tmp/steed-activity-http-preview.png`.
The full 331-test suite passed in 207.015 seconds
(`/tmp/steed-activity-full.log`); startup/render/shutdown passed
(`/tmp/steed-activity-startup.log`). Ruff, formatting and whitespace passed.

Scoped download stage (October 2, 2026): the full 324-test suite passed in
219.159 seconds (`/tmp/steed-download-full.log`). Sixteen initial download
regressions passed in 73.306 seconds (`/tmp/steed-download-focused.log`), covering
exact paths/size scopes, existing files, symlinks, parent substitution, native
private/shared profile downloads, credential sharing, ordinary download
independence, forbidden redirects, HTML responses, known/unknown-size limits,
stream cancellation, late destination creation, revocation, and publication
journal failure. The final 19 focused checks passed in 72.304 seconds
(`/tmp/steed-download-focused-final.log`), adding active-transfer expiry,
cross-task ownership and post-publication receipt failure. Ruff, formatting,
whitespace, and optional dependency checks passed.

Both MCP workflows downloaded and inspected a synthetic output file through
the real adapter (`/tmp/steed-download-stdio.log`,
`/tmp/steed-download-http.log`); shared-profile form/handoff, concurrent user
POST, cancellation and independent-client ownership checks remained passing.
Startup/render/shutdown passed (`/tmp/steed-download-startup.log`). These are
isolated fixtures, not real-account download acceptance. Completed files survive
normal task teardown; crashes can leave private staged directories until explicit
cleanup. No upload, pause/replay or executable-content authority was added.

Same-page watch and user handoff stage (October 2, 2026): the final 16 watch
regressions passed in 3.448 seconds (`/tmp/steed-watch-focused-final.log`).
They cover explicit attach authority, page/profile identity, read-only rendered
input, user edits/navigation, queued and in-flight agent denial, stale targets
and ownership keys, unchanged scopes, journal failure, revocation, busy close,
Escape, direct widget deletion, and reopening before deferred deletion.
A separate shared-window regression verifies parent-window close returns control
without destroying the task's shared profile. Earlier combined gateway/service
checks passed 47 tests in 8.248 seconds (`/tmp/steed-watch-focused.log`).

Both MCP `2025-11-25` workflows passed
(`/tmp/steed-watch-stdio-smoke.log`, `/tmp/steed-watch-http-smoke.log`).
The trusted fixture took control, entered synthetic text through the user panel,
verified the agent was denied, and returned the same page. The agent rejected
its old references, inspected again, edited/submitted the native form, and
finished while ordinary shared-profile user POST remained available. Entered
values were absent from the journal. HTTP ownership and independent-client
DELETE cancellation checks also passed. These isolated fixtures establish
supported handoff behavior, not real-account or unrestricted native input
acceptance. Website scripts remain unavailable.

The full 308-test suite passed in 146.577 seconds
(`/tmp/steed-watch-full.log`). Startup/render/shutdown passed
(`/tmp/steed-watch-startup.log`), as did Ruff, formatting, whitespace, and
`pip check`; 47 local documentation links passed. The synthetic handoff window
was visually inspected from `/tmp/steed-watch-http-preview.png`.
No native startup exit occurred in this stage; the intermittent
Qt startup exit recorded below remains unresolved.

Authenticated HTTP MCP stage (October 2, 2026): 13 focused HTTP/adapter tests
passed in 1.434 seconds (`/tmp/steed-http-focused.log`). Coverage includes all
method authentication, Host/Origin rejection, credential-bound session ownership,
DELETE/shutdown cleanup, idle expiry, GET-stream lifetime, explicit cancellation,
request/session limits, private endpoint publication, browser-access epoch
changes, and rejected overlapping operations.

Both real-adapter synthetic Qt workflows passed on MCP `2025-11-25`
(`/tmp/steed-http-stdio-smoke.log`, `/tmp/steed-http-mcp-smoke.log`). The HTTP
SDK client completed the shared-profile native form while ordinary user POST
continued. An independent urllib client rejected foreign-credential access,
completed approved document retrieval, reused its task connection with fresh
approval, and deleted a session owning an active page. Its task was cancelled
without disrupting the SDK client's workflow. Disabling browser access stopped
the adapter and removed its private endpoint file. Only the trusted fixture
approved requests; production HTTP has no approval bypass.

The full 290-test suite passed in 142.947 seconds
(`/tmp/steed-http-full.log`). Real browser startup/render/shutdown passed
(`/tmp/steed-http-startup.log`), as did Ruff, formatting, whitespace, 46 local
documentation links and `pip check`. One HTTP smoke run exited natively (139)
during Qt startup before the HTTP checks; the identical retry passed. This
intermittent startup issue remains unresolved. These are isolated fixture
results, not real-account HTTP, arbitrary client/OAuth, other protocol revision,
reconnect/resume or watch/takeover acceptance. The broader A4 milestone stays
in progress; core transport checks passed.

Active-profile discovery and live desktop MCP check (October 1, 2026): an
official-SDK client connected to the opted-in running browser. The user approved
read/navigation access to `https://en.wikipedia.org` on `existing:codex test`.
The client inspected Python's article, followed its observed Guido van Rossum
link, inspected that article, and finished the task successfully. This verifies
a real client and public-site workflow on the user-selected profile; it does
not test authenticated sites, website scripts or HTTP transport. The running
browser predated profile discovery, so the user supplied its profile name;
the new context tool is verified separately with temporary profiles.

Nineteen focused gateway/UI tests passed in 4.589 seconds
(`/tmp/steed-context-focused.log`), including six new regressions for exact
profile names, grant stability during switches, focus/dialog selection,
multiple/closed windows, incognito/global exclusion, and bound-task lifetime.
The two-client MCP smoke passed with discovery supplying the shared-profile
grant/session selector (`/tmp/steed-context-mcp-smoke.log`). The full 280-test
suite passed in 135.526 seconds (`/tmp/steed-context-full.log`). Real-entry-point
startup/render/shutdown passed (`/tmp/steed-context-startup.log`), as did Ruff,
formatting, whitespace and 45 local documentation links. These checks used
isolated configuration and synthetic credentials.

A4 stdio stage and bounded engine assessment (October 1, 2026): no compatible
packaged embedded engine or maintainable small backport was established from the
installed engine probes, Qt release information, public request API source, and
Chromium policy design. Custom compilation/full patch auditing were not attempted.
Scripted tasks are deferred; the existing Qt browser and profile model remain.

Two external clients passed the synthetic delegated workflow on MCP 2025-11-25
(`/tmp/steed-agent-mcp-smoke.log`): the official-SDK client exercised capability
discovery, denial before approval, the nonmodal approval popup, observed link
navigation, native text/checkbox edits, stale-reference denial, form submission,
value-free evidence, cancellation and denied access afterward. A separate raw
JSON-RPC client initialized, discovered tools, created its own task, rejected
foreign-session/approval attempts, and finished. The task shared the user's
actual profile and fixture cookie; ordinary user POST remained available and
task pages were disposed. The fixture alone approved/cancelled; production code
provides no remote approval tool. This proves these local client/fixture paths,
not real-account, desktop, AI-provider, HTTP-transport or complete A4 acceptance.

Eleven focused gateway/UI regressions passed in 2.561 seconds
(`/tmp/steed-agent-focused.log`); last-owner shutdown coverage was then added.
Three adapter failure checks passed for private/symlinked discovery, malformed
metadata, and closing uncertain/cancelled requests without retry. The final
274-test suite passed on Qt 6.11.2 in 162.856 seconds
(`/tmp/steed-mcp-final.log`), including 16 new transport/UI/adapter checks and
sequential tasks requiring fresh identity/approval. The final two-client smoke
also passed reuse without carrying grants into a new task. Real-entry-point
startup/render/shutdown passed (`/tmp/steed-mcp-startup.log`). Ruff, formatting,
whitespace, 45 local documentation links, and optional MCP dependencies via
`pip check` passed. Historical native Qt startup exits remain
unresolved; this stage does not claim to fix them.

A3 engine decision and capability discovery (October 1, 2026): the native
connection-policy gate passed on separately installed Chromium 153.0.8010.36
(`/tmp/steed-native-connection-policy.log`). Unapproved HTTP/WebSocket, worker,
STUN UDP, and TURN/TCP probes were contained in the restricted document and its
cross-process iframe. Positive controls retained ordinary networking and
approved-origin GET/POST; shared cookies/storage/live updates, control-session
detachment, owned-frame disposal, and the surviving user tab passed. This is a
synthetic external-engine experiment, not embedded Qt acceptance or real-account
testing. Native policy inside the actual shared embedded engine is the selected
direction; obtaining compatible engine support and trusted policy delivery remain
next. Steed keeps website scripts unavailable.

Five capability regressions passed (`/tmp/steed-capabilities-focused.log`),
covering version eligibility without script authority, unverified versions,
discovery without grants/page creation, task identity/lifetime, and missing
alternate-backend reports. The complete 258-test suite passed on Qt 6.11.2 in
135.128 seconds (`/tmp/steed-engine-full.log`). Ruff, formatting, whitespace,
and local documentation links passed. This run does not resolve the historical
native Qt startup exits or establish interactive desktop acceptance.

A3 native networking prototype stage (October 1, 2026): the expanded baseline
audit passed (`/tmp/steed-native-policy-baseline.log`). Response continuation and
fulfillment comparisons, plus the connection-allowlist header probe, completed
on Qt 6.11.2 / Chromium 140.0.7339.225 (`/tmp/steed-native-policy-continue.log`,
`/tmp/steed-native-policy-fulfill.log`, `/tmp/steed-native-policy-allowlist.log`,
`/tmp/steed-native-policy-zero-latency.log`).
Fulfillment blocked shared workers, including a fresh iframe constructor, and
1 ms/100% packet loss stopped the observed STUN packets. Holding fulfillment
and packet loss fixed while removing latency restored the observed STUN traffic.
Every candidate still
allowed an unapproved TURN/TCP connection; the required-containment gate
therefore exited 1 as intended (`/tmp/steed-native-policy-gate.log`). Scriptless
controls and ordinary concurrent user networking passed. These are test-only
prototypes; runtime behavior and permissions were not expanded. The prior
253-regression full-suite result remains the production baseline; no production
code changed in this stage. Lint, formatting and whitespace checks passed.

A3 active-content boundary stage (October 1, 2026): the loopback audit completed
on Qt 6.11.2 (`/tmp/steed-active-audit.log`), identifying the shared-worker and
WebRTC gaps described above. CSP prevented the shared-worker request in its
controlled fixture but did not stop STUN. Website scripts remain unavailable;
no broader grant or profile-wide user restriction was added. All 20 form/ticket
checks passed (`/tmp/steed-active-forms.log`), including two new regressions for
script settings enabled before preparation or during its asynchronous callback.
Neither path armed a ticket or sent a form request. Ordinary browsing and earlier
native actions retain their existing behavior. The complete 253-test suite passed
in 143.111 seconds (`/tmp/steed-active-full.log`); Ruff, formatting and whitespace
checks passed. This stage does not establish interactive desktop or real-account
acceptance. Historical native Qt startup exits remain unresolved.

A3 native keyboard stage (October 1, 2026): all 251 regressions passed on Qt 6.11.2
in 141.614 seconds (`/tmp/steed-native-full.log`). Six new input checks and a new
shared-profile check verify trusted keyboard/click/input/change events, viewless
task rendering with a virtual viewport, readable child-frame focus, disabled
option/optgroup skipping, unchanged selections, focus checks between bounded
batches, timeout/stop with late responses, and adapter loss. Ordinary user values,
checkbox state, and focus stayed independent on the shared profile; revocation
destroyed the private frontend before its profile and left the user page alive.
Existing form, worker-bypass, permission, journal, and daily-browser tests passed.
Website JavaScript permission and physical pointer/custom-control behavior remain
unimplemented. No external-agent connection or cloud provider was introduced.
Startup/render/shutdown and local Google HTTPS redirects/POSTs/opt-out passed
(`/tmp/steed-native-startup.log`, `/tmp/steed-native-google.log`). Ruff, formatting,
whitespace, and thirteen local documentation links passed. The prior native Qt
startup/profile-creation exits below remain unresolved; these passing runs do not
establish a root cause or fix.

A3 form-submission stage (October 1, 2026): all 244 regressions passed on Qt 6.11.2
in 115.758 seconds (`/tmp/steed-form-full.log`). Eighteen new form/ticket checks
and new shared-profile/popup checks cover native GET/POST serialization, hidden
fields, submitter overrides, required-field validation, named-control shadowing,
exact one-use authority, redirect checks and POST replay denial, stale/last-moment
DOM changes, late expiry, stop/revocation, missing-load deadlines, and request/
receipt journal failures. Signed-in task POST and concurrent user POST retained
their cookies and ordinary user behavior. Form/result secrets stayed out of the
journal; native validation failures sent no request. The prior direct-POST
denial behavior also passed after separating general requests from form tickets.
These local fixtures do not establish real-account changes, script-driven forms,
SPA behavior, or interactive desktop compatibility. Historical native exits below
remain unresolved.

Startup/render/shutdown passed (`/tmp/steed-form-startup-smoke.log`). The form
approval popup was rendered and visually checked at 480×420 with readable scope,
site list, notices, and Approve/Deny buttons (`/tmp/steed-form-approval.png`).
Ruff, formatting, whitespace, and thirteen local documentation links passed.
The first local Google HTTPS smoke process exited natively (139) during the first
persistent `QWebEngineProfile` construction at `profile_manager.py:142`, before
task/form operations. The fault trace is `/tmp/steed-form-google-smoke.log`.
The identical diagnostic retry passed HTTPS redirects, POSTs, and compatibility
opt-out (`/tmp/steed-form-google-retry.log`). This adds another occurrence to the
intermittent headless startup issue; no root cause or fix is claimed.

A3 control/wait stage (October 1, 2026): the complete 224-test suite passed on
Qt 6.11.2. Nineteen new action/wait regressions plus explicit-permission and
shared-profile checks verify trusted text input and undo, field limits, clearing,
checkbox/radio state, selection by index, rejected sensitive/unsupported controls,
freshness after edits, readable child frames, observed scrolling, ready/text
waits, bounded timeout, superseding loads, interruption/revocation, delayed
callback expiry, and request/receipt journal failures. User controls and document
scripts stayed independent while sharing the task's signed-in profile. No entered
text or wait query was written to the journal. Existing browser checks passed.

The implementation uses Chromium editing commands and documented HTML/DOM
control semantics. It does not establish physical pointer/native-select gestures,
submission, script-enabled forms, SPA behavior, or real-account compatibility.
Startup/render/shutdown passed on a diagnostic rerun after one headless fixture
process exited natively (139); the cause remains unknown. Local Google HTTPS
compatibility, Ruff, formatting, whitespace, and local documentation-link checks
passed. This does not replace manual desktop acceptance.

An exploratory temporary DevTools focus-emulation probe also terminated a fixture
process natively; that path is absent from the implementation. No cause or fix
is claimed for either native exit. The shipped action paths passed the suite above.


Latest collaborative-profile/navigation update (October 1, 2026): the complete
198-test suite passed, followed by 44 focused checks after adding five further
regressions and fixing same-document anchor navigation. The suite now contains
203 tests. The focused run covered all current inspection, target-navigation,
shared-profile, and approval-popup tests. Startup/render/shutdown, local Google
HTTPS compatibility, Ruff, formatting, and whitespace checks passed. A synthetic
popup was rendered and visually inspected separately from desktop acceptance.

New coverage includes queued nonmodal approvals, explicit decisions, pending
requests without windows, plain-text objectives, failed approval writes, two tasks
and a user sharing one signed-in profile, selective revocation, retained user
POST/download/worker behavior, worker-protection loss, target scope and permission
checks, stale/unsupported/long links, anchor jumps, blocked redirects, observed
load results, timeout, stop/supersession, and request/receipt journal failures.
Real-account collaboration and interactive desktop usage remain user follow-ups.

Earlier verification records:

Before the shared-profile/popup/navigation update, all 188 browser/A1/A2/A3
regressions passed (October 1, 2026) on Qt
6.11.2, including four new task-storage checks and six real-Qt task-service
checks. The deterministic client exercised navigation without a view, attachment
to the same page, completion, task/cookie isolation, worker dispatch, queued
cancellation, restart recovery, shared-window lifetime, and write-failure
handling. The combined suite required one shared QApplication to avoid a native
WebEngine crash when a later test class recreated the application.
The latest checks also cover find match counts, direction and case, navigation,
profile-query isolation, stale callbacks, fullscreen restoration, zoom shortcuts
and bounds, injected download destinations, invalidated permissions, task
permission denial, and exactly-once screen-share cleanup. Ordinary tabs and
task pages use the shared navigation and permission services.
Startup/render/shutdown and local Google-login HTTPS smoke checks, desktop-entry
validation, Ruff, formatting, and whitespace checks passed. The user authorized
proceeding with remaining manual follow-ups recorded in the roadmap. Password
improvements are deferred.

Initial A2 verification added nine policy checks, four storage checks, twelve real-Qt service checks,
fourteen existing-profile checks, and a browser menu failure check to the previous
132-check suite. Coverage includes pending/denied
authority, client identity, origin/profile/action/file rejection, native redirect
and resource interception, POST blocking, cancelled task downloads, UI approval
and revocation, expiry without further commands, concurrent request budgets,
queued-work rejection, database migration, restart recovery, and SQLite approval
failure handling. The new approval dialog was rendered with a synthetic request
and visually inspected; this is separate from manual desktop acceptance.
Existing-profile checks use temporary storage and synthetic credentials. They
now verify cookie/local-storage continuity, concurrent user/task access,
switching/pop-outs, profile path restrictions, task downloads without destination
prompts, ordinary user downloads and POST requests, redirect/resource boundaries,
worker bypass for task pages with ordinary workers retained, selective revocation,
expiry, ad filtering, shared-window shutdown order, and journal/setup failures. These checks establish fixture behavior, not
real-account compatibility. Task document scripts and file operations remain
unavailable.

A3's read-only stage adds fourteen inspection/broker checks, one explicit-content
policy check, and one signed-in read/approval check to the 172-test baseline.
Real Qt fixtures verify extraction without a view, field-value/editable-text
omission, control descriptions, readable same-origin frames, inaccessible frame
limits, output bounds, unchanged DOM, stale/replaced/forged targets, cross-task
denial, timeout, supersession, revocation, callback-time expiry, deferred worker
replies, shutdown, and journal failure without content disclosure. The full
188-test suite, startup smoke, Google compatibility smoke, lint/format, and
whitespace checks passed. These results apply to the read-only stage; target-based navigation is added below.
Control activation and full SPA workflows remain unimplemented.
The content-access disclosure was rendered with a synthetic existing-profile
request and visually inspected. All fourteen focused inspection checks passed
again after tightening string and label traversal bounds.

During A1 verification, one full-suite attempt exited with a native WebEngine segmentation fault
during its first test. The same test passed with crash diagnostics enabled, and
the subsequent full 132-test suite and startup smoke passed. No cause was
established; this is recorded as an intermittent native failure, not a confirmed
fix.

### Earlier implementation checks

These are recorded checks from earlier implementation work, not a fresh run of
the entire suite. Live desktop checks still pending are tracked in the
[roadmap acceptance checklist](ROADMAP.md#browser-acceptance-checklist).

History and refresh additions: four history storage tests and six focused
Qt browser checks passed, covering reload shortcuts from page/address focus,
completion navigation, shared-window/private-profile isolation, successful
visit recording, hostname matching, and profile rename persistence. Five
existing profile-switching/shutdown checks also passed. Ruff lint/format and
whitespace checks passed. Desktop Auto-Type remains a manual check.

Password integration verification: seven encrypted local-protocol checks and nine
real Chromium form/UI checks passed, including replay/tamper rejection,
credential-free pairing persistence, denial/cancellation, separate login steps,
save requests, keyboard filling, navigation invalidation, vault locking after
listing, and form-action changes during input handling. Four existing profile
and shutdown checks also passed. An isolated real KeePassXC 2.7.6 vault passed
encrypted save/retrieve and vault-lock checks with a seeded fixture association.
The user still needs to verify interactive pairing and normal site filling on
the desktop. The official Bitwarden 2026.9.2 extension crashed this Qt WebEngine
runtime during both install and unpacked load; generic extension support has
not been exposed.

Verification: 71 tests passed, including real Chromium request/cosmetic filtering, nested/srcdoc frame hiding and reload exceptions, filter compilation and immediate cached-worker outcomes, fullscreen enter/exit and repeated requests, completed file downloads, cookie scope and exact-path deletion, global update/logout, shared-window lifetime, a two-process persistent-cookie restart, persisted-cookie enumeration through the DevTools adapter, the startup-sharing flag in all-profiles mode, abandoned-download-dialog cancellation, shutdown confirmation when active downloads are genuinely at risk, and cookie-snapshot refresh scheduling, bounded recovery, and stale-response rejection. Repeated runs were used to confirm the suite is stable rather than merely green once. Ruff lint/format checks, whitespace checks, and the real-entry-point startup/render/shutdown smoke test also passed with Qt 6.11.2.

The final audit's four findings are fixed: fullscreen requests now follow their requested state; cookie inspection retries timeouts, bounds target errors, and ignores obsolete responses; cached filter compilation starts only after its completion handlers and service state are ready; and cosmetic filtering injects document-specific CSS into accessible same-origin frames rather than only the parent page. Manual follow-ups are recorded in [ROADMAP.md](ROADMAP.md#browser-acceptance-checklist).

Two further defects were found and fixed while stabilizing that coverage, both affecting B2 in ways the previous suite did not observe:

- A stale enumeration of a destination profile arriving after the browser's own copy made `target_added` treat the old value as a local edit. That permanently marked the site locally controlled, which then suppressed the next global logout.
- A refresh requested while an inspection was still in flight was dropped. Qt reports a cookie *overwrite* while the previous run is pending, so overwriting a global cookie left the shared baseline permanently stale with nothing left to re-trigger a read. Queued refreshes are now recorded and re-run, and the global snapshot is re-inspected until two consecutive readings agree.

Refreshes are also debounced and the DevTools SDK module is cached on the frontend page, which removed the repeated module load that dominated each enumeration.

Startup and launcher fixes (October 1, 2026): six focused desktop regression
checks passed, covering the initial Quick Switch menu and shortcut, profile
switching, shared-window lifetime, reload, and shutdown. The executable was
checked from another working directory with a path containing spaces; desktop
entries passed `desktop-file-validate`, and Ruff and whitespace checks passed.
The Steed application-menu entry was installed on the development machine.

## Current limitations

- Google account sign-in was reported working on October 1, 2026; restart persistence, third-party sign-in, and the broader desktop acceptance checklist remain pending. Google can reject embedded browsers independently of the user-agent override.
- Global-cookie inheritance intentionally preserves unknown/local ownership and excludes partitioned identities; it is not full browser-session synchronization. With `global_cookie_sharing_apply_on_startup` disabled, sharing stays inactive until you switch profile or opt a site back in.
- Ad blocking requires downloading full lists for broad coverage and does not enable scriptlets or resource redirects.
- Active profiles must be switched away from in every window before rename/archive. Archived profile data is recoverable under `~/.custom_browser/deleted_profiles/`.
- Tab sessions are saved per profile; the most recently saved window supplies that profile's restored session.
- A window always keeps at least one tab, so the final tab cannot be closed or restored.
- Remembered website approvals affect future requests; forgetting cannot guarantee
  that an existing capture stops. Hardware camera/location/screen acceptance remains manual.

## Planned architecture

The following is the target design. The [internal task foundation](#internal-task-services)
is implemented; full agent features, generic extension support, and bookmarks remain
future work. Implementation order and status live in [ROADMAP.md](ROADMAP.md).

### Configurable browser layout — implemented (first milestone)

Layout settings change the presentation of the browser chrome and nothing else.
Open **General → Layout Settings…** to edit them, or **View → Reset Layout** to
return to today's defaults. The first milestone covers manual editing:

- Show, hide and reorder the address bar buttons (Back, Forward, Reload, Home,
  Reset zoom, Passwords). Each button has a stable identifier, so a saved layout
  does not depend on widget names. The address/search field can move within the
  order and is always visible. Defaults keep it between Home and Reset zoom.
- Reorder the menu bar between General, Profile, Tabs, View, Cookies and Agents.
  Today's order is the default.
- Place the agent sidebar on the left or right and remember its width.
- Choose compact or comfortable control spacing.
- **Tabs** (on the `steed:style` page, **Tabs & toolbar**, with control spacing
  and browser text; Layout Settings keeps button and menu order): put the tab strip at the top or bottom, or as a vertical list on the
  left or right (160–480 px wide; drag the inner edge to resize, saved for every
  window). Choose a style (Rounded, Pills, Underline,
  Boxed or Minimal), tab size, an accent colour, when close buttons show, where
  the new-tab button sits, and whether tabs that don't fit shrink, scroll or wrap
  onto more rows (up to four). Grouping shows your tab groups as coloured chips
  (click to collapse, right-click for Rename, Ungroup, New Tab in Group and Close
  Group), colour-codes tabs by website (vertical tabs list each site as a
  section), or turns grouping off. Site icons and a tab count are optional.
- Save the current appearance under a name, apply a saved layout later, or
  delete one. **Reset Layout** restores today's appearance and keeps the saved
  layouts available.

Edits preview in the current window; **Save** commits them to every window.
**Cancel** restores the latest committed layout. Unrelated edits from another
window are preserved; competing changes to the same setting are refused with a
message so you can reopen the editor. Reset keeps committed named layouts.
Sidebar width is saved through Layout Settings; dragging the dock only resizes
it for that window and session.

Settings stay reachable when controls are hidden. Menus cannot be hidden at all,
`Layout Settings…` sits beside `Settings…` in General, `Reset Layout` stays in
View, and `Home` was added to the Tabs menu so hiding the Home button does not
remove the only route to it. Reload, Reset zoom, Passwords and the address bar
keep their existing menu entries and keyboard shortcuts.

Layouts are stored under the `layout` key in the shared browser configuration
(`~/.custom_browser/config.json`), so they apply to every window and profile,
the same way as other browser preferences. Stored values are treated as
untrusted input: unknown button or menu identifiers are dropped, identifiers
missing from a stored order return in their default position, and a document
written by an unknown future schema version falls back to today's defaults.
Stored version 1 settings migrate without adding panels or changing text size.
Sidebar width is bounded to 300–800 px, at most 32 named layouts are kept, and a
name may not be empty, longer than 48 characters or contain control characters.

Applying a layout never changes browser, website, profile, folder, file or
account permissions, and never grants an agent access to anything.

Manual settings use one global layout with no per-window or per-profile override
and two densities. Agent proposals use the portable format below.

### Portable layouts and agent proposals — implemented

**General → Export Layout…** saves the committed appearance as a small JSON file.
Choose a new filename; existing files are preserved. **General → Import Layout…**
validates the file and opens a preview of the actual control changes. Click
**Apply layout** to commit, or Close to leave settings unchanged. Import changes
appearance only and preserves named layouts. Save As in Layout Settings can name
an imported appearance. **View → Recover Previous Layout** restores the previous
committed appearance across windows and retains current named layouts; subsequent
recovery swaps back. Preset-only edits do not erase the appearance recovery point.

Agents call `steed_layout_capabilities({})` for stable IDs and a copyable example,
then `steed_layout_propose` with `source` (the exact JSON string) and a stable
`request_id`. Steed opens a native popup with a plain-text change summary, an inert
address-bar preview and optional JSON source. The user chooses Apply, Decline or
Close. Close defers the request; reopen it in **Agents → Task Permissions… →
Layouts**. Poll `steed_layout_status` with the same request ID. No grant is needed
for these presentation proposals; they neither read browsing data nor authorize
websites, profiles, folders, accounts or execution. Agents cannot approve them.

The [Focus example](examples/layouts/focus.json) demonstrates portable version 1:
`version`, a printable `name`, and `layout` with complete toolbar/menu orders,
known hideable buttons, left/right sidebar width 300–800 px, and compact/comfortable
density. The address field, browser-menu button and all menus are mandatory.
Older portable layouts without the `menu` toolbar ID remain importable; it is
restored on the left. Unknown fields, callbacks,
commands, missing controls, duplicate JSON keys, invalid versions and oversized
source are refused before review. Limit: 16 KiB UTF-8, 20 proposals per task,
three pending per task and 32 pending globally. Source is immutable and task-bound;
receipts and journal entries contain IDs/hash/state, not proposal bodies.

An identical request retry returns its receipt; changed bytes with the same ID
are rejected. A stale preview must be reviewed again after another window saves.
Cancellation ends pending requests. Restart interrupts pending/in-flight requests
and never reapplies them; an interrupted receipt can mean the appearance changed
before its final receipt was saved. Inspect current settings and use recovery
rather than retrying an uncertain application. Configuration publication preserves
both disk and in-memory settings on failure.

### Native quick-action panels and browser text — implemented

Open **General → Layout Settings…**, scroll to **Quick-action panels**, and click
**Add panel**. Set a title, top/bottom placement and neutral/accent appearance;
choose one to eight actions and use Move action up/down to arrange them. Add up
to two panels. Changes preview in this window; Save commits to all windows,
Cancel discards the preview, and Reset removes panels while retaining named presets.
The Browser text selector provides normal or 15% larger menu, toolbar, tab,
find/status and panel text. It does not change website font sizes or page zoom.

The [Daily tools example](examples/layouts/daily-tools.json) uses portable version 2
with `text_size` and `panels`. Each panel has `kind: "quick_actions"`, a unique
ASCII ID of at most 32 characters, printable title of at most 48 characters,
`placement` (`top`/`bottom`), `emphasis` (`neutral`/`accent`) and distinct action IDs.
`steed_layout_capabilities` returns the current grammar and a panel example.
Version 1 imports/proposals remain supported and use normal text/no panels.
Unknown component kinds, arbitrary callbacks, executable source and unsupported
actions are rejected. Human review previews the actual native panels with inert
buttons before applying them through the existing task-bound receipt workflow.

Supported actions: New tab, Reload, Home, Address/search, Find in page, Zoom in,
Zoom out, Reset zoom, Agent sidebar, Downloads, Passwords and Layout settings.
Real panels reuse the browser's QAction instances and their disabled/checkable
state; applying a panel runs no action. Buttons execute only when the user clicks.
Reload, Find and Zoom disable on native agent-chat tabs and restore on webpages.
Narrow panels scroll horizontally and reveal keyboard-focused controls. Panels
remain native UI in private windows; they create no agent access or data grant.

### Native data panels — implemented (first milestone)

The maintained display is now HTML: open `steed:panels` for a full-page dashboard,
or **Agents → Data Panels** for the compact HTML sidebar. **Open full page** in the
sidebar opens the same profile's panels in a tab. Both inherit `steed:style` and
retain the existing reviewed panel format and backend controls. They have separate
offline presentation engines; generated text stays literal and cannot execute
scripts. Closing the sidebar hides it; closing its tab releases that presenter.
Linked contents are covered immediately on revocation/profile change and removed
from presentation state. Account reads still require **Refresh linked sources**;
opening or reloading either interface never fetches them. Form previews still
require **Send to agent** before any message is delivered.

Open **Agents → Data Panels** to view agent-designed dashboards in the current
normal profile. The dock starts empty and stays closed until requested or an
approved panel is enabled in that window. When the agent sidebar already uses the
same dock area, the two share tabs to keep the page usable. Panel selection and
removal are local UI controls; removing a panel stops publication in every window
of that profile and leaves its underlying drafts intact.

To start, ask a connected agent: “Create a dashboard with your task progress, a
summary, and a list of next steps; keep its drafts updated as you work.” The agent
uses the native panel tools below. Review the preview, choose the display profile,
and click **Enable panel and draft updates**. Close defers the request; reopen it
under **Agents → Task Permissions… → Data Panels**. Decline leaves the browser
unchanged. Artifact sources need the task's existing `artifact` permission;
sealed workspace sources need `workspace` permission. Panel
approval neither creates nor expands website, folder, file or account permission.
Linked library reads have their own explicit checkbox and revocable panel/profile
permission described below.

The current catalogue provides these native components:

| Kind | Source | Display |
| --- | --- | --- |
| `task` | Owning task's metadata | Objective and current task status |
| `text` | Owned artifact, sealed UTF-8 workspace file (v3), or selected library file (v4) | Selectable, read-only plain text |
| `list` | Owned artifact, sealed workspace JSON array (v3), or selected library JSON (v4) | Up to 64 plain-text items, 512 characters each |
| `table` (v2–v4) | Owned artifact, sealed workspace JSON (v3), or selected library JSON (v4), with `columns` and `rows` | Read-only comparison table, up to eight columns and 64 rows |
| `form` (v2) | Reviewed text fields and choices | Explicit user message to the owning connected agent |
| `image` (v3) | Sealed owned PNG/JPEG workspace file | Static native preview; no HTML/SVG or remote resource reads |

Generated text is labelled as a saved agent draft. HTML, URLs and commands stay
plain text; invisible controls are displayed as escapes. Task completion describes
task state, not proof that draft claims or external effects succeeded. Missing,
deleted or hash-mismatched sources make the panel unavailable; there is no
fallback to other files or profiles. Use the task's existing artifact tools to
create or read drafts; data panels add no body-reading endpoint for agents.

Agent protocol:

1. Call `steed_panel_capabilities({})` to discover the grammar and limits. It is
   grant-free discovery and returns no user content or profile identities.
2. Store text/list artifacts with `steed_artifact_store` under existing artifact
   permission. Build a source document like
   [the dashboard example](examples/data_panels/task-dashboard.json), replacing
   `OWNED_ARTIFACT_ID` with the returned ID. Submit `steed_panel_propose` with the
   exact UTF-8 `source` and a stable `request_id`; poll `steed_panel_status` until
   the user chooses a profile and enables the panel.
3. Version 3 also accepts `file_id` for text/list/table and requires it for images.
   Create/seal bytes through the workspace tools under existing workspace authority;
   user-shared files already copied into that task's workspace are eligible too.
   See [the image/file example](examples/data_panels/generated-chart.json). A file
   must belong to the proposing task, be sealed, and match its stored digest.
4. To revise drafts, store new immutable artifacts/files and call `steed_panel_publish`
   with that `request_id`, a stable `update_id`, the current `expected_revision`,
   and `bindings`, for example `[{"id":"summary","artifact_id":"NEW_ARTIFACT_ID"}]`.
   A file update uses `[{"id":"chart","file_id":"NEW_SEALED_FILE_ID"}]`.
   Only reviewed source widgets can change their bindings; an artifact widget
   cannot become a file widget or vice versa. New widgets, different
   kinds or another display profile need a new proposal and review.
5. Identical publication retries return their original revision receipt, even
   after newer publications; changed reuse of an ID is refused. A stale revision
   requires reading status before another publication. Changes are committed with
   their receipt and value-free journal event in one SQLite transaction.

Limits: 16 KiB source, eight widgets per panel, eight enabled panels per profile,
20 proposals per task, three pending per task, 32 pending globally, and 256 draft
publications per panel. Artifact limits also apply (16 KiB per artifact, 50 per
task). Workspace text/JSON is limited to 16 KiB and media types `text/plain`,
`text/markdown` and `application/json`. Images require matching PNG/JPEG bytes,
at most 8 MiB, four megapixels and 8192 pixels per dimension. Only those two
decoders run; previews shrink to fit 1600×1000 without enlarging small sources.
Eight decoded previews are cached, after integrity checks on every read. Normal
workspace file/task limits also apply. Mixed sources require both authorities but
one proposal/publication consumes one operation; metadata and retries do not.
Publication requires the corresponding artifact/workspace authority and the original active
task. Task end, revocation or expiry stops updates; task end cancels or interrupts
pending requests, while after expiry their approval needs fresh authority. Saved drafts remain visible to the user after task end/restart
until removed or their source is deleted. Recovered/new tasks get no automatic
publication authority over historical panels.

A stable profile identity binds the display. Renaming preserves it; deleting and
recreating the name does not transfer it. Receipt `profile_name` is the label at
approval, while the dock labels the current profile. Other profiles and private
browsing do not display these panels. Profile switches erase the previous native
content immediately. Multiple windows on the same profile receive committed
updates without rebuilding unchanged text selections. Panel templates, bindings
and receipts live in the task database (schema 21); personal draft bodies remain
in owned artifacts/sealed workspace files, not global appearance preferences, named layouts or layout
exports. Browser text sizing also applies to the dock.
Profile changes clear image pixels and widget source records as well as text and
forms. These are retained task snapshots: revocation stops agent updates, while
the user can still view previously enabled saved files. Deleting or corrupting a
source makes it unavailable even if a decoded preview was cached. This contract
does not authorize live reads. Version 4 library bindings use the separate contract
below. Version 5 account reads have their own manual-refresh contract.

Editable local Welcome/New Tab HTML is implemented; broader hosted mini-apps remain
a planned complementary option.
They will need their own host/lifecycle and a typed bridge to existing Steed
capabilities. The current native format does not execute extension code, and
external agents keep their independent tools.

### Reusable linked library panels — implemented

Ask an external MCP/Codex agent to build a panel using a file already in an enabled
library folder. Discover the folder/file through the existing library tools, then
use `steed_panel_capabilities` and propose a version 4 source such as
[the linked-notes example](examples/data_panels/library-notes.json):

```json
{
  "version": 4,
  "name": "Reusable notes",
  "widgets": [{
    "id": "notes", "kind": "text", "title": "Notes",
    "library": {"folder_id": "ENABLED_LIBRARY_FOLDER_ID", "path": "notes.txt"}
  }]
}
```

Review shows the exact selected files and display profile. Tick **Allow this
panel to read the selected linked sources in this profile, including after task
end** to give reusable read permission separately from enabling the panel. Leaving
it unchecked installs the panel with linked contents hidden. This gives neither
file edits nor new agent-task access. Agents cannot enable this permission or
change the selected paths through `panel.publish`.

In **Agents → Data Panels**, **Refresh linked sources** reads the latest file
revision; local library changes also refresh visible panels. **Disable linked
reads** immediately hides linked contents. **Allow linked reads…** opens a readable
confirmation with the exact files/profile. A disabled library folder, removed
file, malformed source or failed integrity check hides the corresponding widget;
restoring access permits refresh. Open review previews clear on folder revocation
as well. Independently saved reports keep their existing retention behavior.

Permission survives task completion and restart, remains bound to the panel and
stable profile identity, and cannot transfer to a recreated profile. Source data
is read locally when viewed/refreshed; it is not saved as a panel snapshot, sent
to a model automatically, or included in appearance exports/audit bodies. File
revision labels distinguish linked data from saved reports. Only text/list/table
files up to 16 KiB of valid UTF-8 are supported, with existing list/table bounds.
Linked images and live host-directory mounts remain separate future work.
Account sources use the version 5 contract below. Library imports remain managed copies.

### Manual account-linked panels — implemented

Version 5 adds a `text` widget bound to one configured account read tool, exact
destination and fixed typed inputs. Agents discover installed tools through
`steed_app_list_tools` and propose a source such as
[account-report.json](examples/data_panels/account-report.json), replacing the
placeholder connector/tool with the actual configured names:

```json
{
  "version": 5,
  "name": "Selected account report",
  "widgets": [{
    "id": "report", "kind": "text", "title": "Account report",
    "account": {
      "connector": "configured_account", "tool": "read_report",
      "destination": "account", "inputs": {}
    }
  }]
}
```

The native review lists the account, tool, destination and resolved inputs. The
separate linked-read checkbox grants reusable permission to this panel/display
profile. The account's **Read** switch in **App Connections** must also cover this
exact read tool. Neither permission substitutes for the other. Writes, sends,
artifact-consuming tools and arbitrary request callbacks are rejected.

**Refresh linked sources** explicitly fetches the read result. Proposal, approval,
view, task completion, reconnect and access restoration make no API calls. The
panel shows waiting/refreshing/unavailable state and a fetched-at timestamp; its
content is the trusted adapter's bounded/redacted text or JSON, displayed as plain
text. Version 7 adds explicit list/table projections below; richer cards remain
separate work. Results stay in memory and are
cleared on account/panel revocation, disconnect, source/tool mismatch, failure,
shutdown or restart. Restoration requires another manual Refresh. Revoking a
shared account clears every affected panel immediately, including open reviews.
Independently saved reports keep their existing retention behavior.

Panel permission survives task completion and restart, but grants no new agent
read, refresh, write or approval endpoint. Account ID/subject and trusted tool
metadata remain pinned; changed sources require a new panel review. Adapter
identity, source, credentials and cancellation checks run on each refresh/page.
Reads run outside the Qt thread, with at most two physical workers and 16 queued
requests. Timeout cancels without automatic retry; a cancelled worker keeps its
slot until it returns. Late results cannot replace a newer refresh or restore
revoked data. Audit entries contain IDs rather than result/query bodies. No
background polling or model calls are introduced. Provider setup/live-account
acceptance remains separate from synthetic panel acceptance.

### Account-backed lists and tables — implemented

Panel version 7 adds `list` and `table` widgets using the same exact `account`
source, reusable profile permission and manual Refresh behavior. Add a `mapping`
that selects a JSON array and scalar fields from the trusted read result. See
[account-table.json](examples/data_panels/account-table.json). A list uses:

```json
"mapping": {"items": "/items", "label": "/title"}
```

A table uses up to eight uniquely named columns:

```json
"mapping": {
  "items": "/items",
  "columns": [
    {"title": "Title", "value": "/title"},
    {"title": "State", "value": "/state"}
  ]
}
```

Pointers are exact bounded JSON pointers; an empty pointer selects the root
array or the whole scalar row. Use `~1` for a slash in a key and `~0` for a tilde.
Wildcards, callbacks, transforms, inferred fields and network destinations are
unavailable. The native review shows the projection alongside account/tool/inputs.
Choose actual fields from the configured tool's result; this feature needs a JSON
read result, and does not infer a structure from prose. Versions 1–6 retain their
exact grammars; version 5/6 account text widgets keep their raw text behavior.

Visible results contain at most 64 rows and 512 characters per cell. Captions show
**showing 64 of 70 rows** and the number of shortened cells; ellipses mark shortened
text. Strings are literal, numbers/booleans/null use JSON spelling, and nested
objects/arrays are refused as cells. Missing fields, non-array collections,
ambiguous/malformed JSON or non-finite values hide the widget until a successful
explicit Refresh. Empty arrays are valid. Failed projections never fall back to
raw JSON. Only projected fields are retained in the panel's memory cache; revocation
scrubs both native cells and that cache immediately. There is no extra API request,
model call or authority beyond each explicitly refreshed reviewed read widget.

### Reusable agent instruction buttons — implemented

Panel version 6 adds a native `instruction` widget. See
[daily-instructions.json](examples/data_panels/daily-instructions.json):

```json
{
  "version": 6, "name": "Daily research",
  "widgets": [{
    "id": "research", "kind": "instruction", "title": "Research suitable roles",
    "instruction": "Find suitable roles. Ask for access to my enabled files as needed."
  }]
}
```

An agent proposes the declaration through the existing `steed_panel_propose`
review. Enabling the panel sends nothing. In **Agents → Data Panels**, click
**Choose agent and review…**, select a connected conversation, inspect the exact
instruction and click **Send to agent**. The preview names the display profile
and recipient task; **Show message details** exposes the full queued message.
Cancel is the default. Start/connect a conversation in the agent sidebar first
if none is available. The button remains reusable after its creating task ends,
but each recipient must be active and connected when a new message is queued.

Only the fixed reviewed instruction (at most 4 KiB UTF-8) and its panel/widget
labels are sent. Linked files, account results, drafts, cookies and tabs are not
automatically attached. This message permits the selected agent to follow the
instruction with its own tools; Steed capabilities still use their existing
permissions and effect reviews. A source-read permission grants no agent access
or send authority. There is no automatic connector execution, provider launch,
background model call, declaration-selected recipient or executable callback.

Queued is not confirmation that an agent read or acted on the message. Native
Codex uses its existing delivery controller; generic MCP clients must read the
task conversation and reply. Website chatbots retain explicit copy/export/import
rather than being presented as MCP clients. Receipts pin the reviewed recipient
and payload atomically with delivery; retrying the same attempt cannot notify
twice or change its recipient, even after message bodies are cleared. A deliberate
later preview can send the reusable instruction again. Profile changes/removal
close or disable stale previews; renamed profiles retain their stable identity,
while recreated profiles cannot inherit the panel.

### Native panel inputs and comparison tables

Ask an agent to “add a preferences form and a comparison table to my dashboard.”
New controls require the same native panel preview and profile choice. Format
version 2 adds `form` and `table`; version 1 remains unchanged. Capabilities include
both supported versions, bounds and a
[form example](examples/data_panels/research-form.json).

Forms contain one to eight fields with exact `id`, `kind`, `title` and `required`
fields. `text` is a bounded single-line input; `choice` adds one to sixteen distinct
`options`. Each value is at most 512 characters, and the entire message must fit
within 8 KiB. Defaults, arbitrary callbacks and service destinations are not
supported. The installation preview's inputs and send button are inert.

To use an enabled form, enter values and click **Review and send to agent…**.
The popup shows the recipient task, profile and labeled values; **Show message
details** reveals the complete structured message. **Send to agent**
queues that message only for the panel's owning task; Cancel keeps unsent values.
The agent receives `type: steed_panel_form` JSON through `steed_conversation_read`
or its existing native chat delivery. Values appear in the owning task's chat.
The agent can act on instructions through its independent tools; Steed browser,
file and connector actions retain their existing permissions and effect reviews.
“Queued” describes delivery to the conversation, not proof of agent action.

Disconnected/ended tasks and unavailable/recreated profiles cannot receive new
submissions. Unrelated refreshes and artifact updates preserve form values and an
open preview. Switching panels/profiles, entering private browsing, removing the
panel or closing the window clears unsent native input and closes its preview;
unsent values are not saved to disk. Sent values use the existing task-message
storage and **Clear messages** control. A value-free digest receipt commits with
the message; identical retries never notify the agent twice, even after clearing
the conversation or ending the task. Changed reuse of a submission ID is refused.
Submitting again with unchanged fields is also blocked by the current form UI;
editing a field permits a new explicit submission. No agent-callable submit or
form-value enumeration tool is added.

Tables bind an owned artifact whose JSON is exactly
`{"columns":["Option","Notes"],"rows":[["A","Plain text"]]}`; see
[comparison data](examples/data_panels/comparison-table.json).
Column labels are distinct printable names, rows have exactly one text cell per
column, and each cell is at most 512 characters. Table cells cannot be edited and
HTML/links remain literal. Tables reuse artifact authority, pinned digests,
publication revisions and saved-draft retention. Other widgets update in place,
so replacing a summary or table does not discard a form being filled out.

### Customization direction (planned)

The user means configurable **browser UI**: the placement and appearance of
controls, rather than only saved agent workflows. The manual settings above are
implemented; the remaining increments extend the same Qt interface. A browser
rebuild is unnecessary for this scope.

1. **Manual layout settings — implemented** (see above). Per-window/profile
   overrides can follow if they are needed.
2. **Reviewed agent layout proposals — implemented:** stable component IDs and a versioned JSON
   layout schema, bounded sizes and valid placements, preview before Apply,
   export/import, migration, Reset and recovery to the last working layout. An
   agent can propose a layout through a typed interface without installing code.
   Changing layout never enables an account, folder, profile or task permission.
3. **Agent-designed native panels — task/text/list/form/table widgets implemented:**
   reviewed profile-bound panels, explicit inputs and artifact draft updates are
   available above. Next: images, richer sources and reviewed service effects, using
   existing capabilities and visible data sources. Per-update rollback remains
   future work; removing a panel stops publication and preserves its source drafts.
4. **Appearance and optional actions — bounded text/emphasis implemented:** more colors, fonts and spacing, plus
   shortcuts or named workflows attached to supported controls. Arbitrary styles
   must not hide approvals or make recovery controls unreadable.

Recipes, generated files, local workers and the task mailbox are foundations
already available, but they do not constitute configurable browser chrome.
External agents keep their own tools. Generated Python does not run inside the
browser process; arbitrary executable UI extensions need a separate contract.
See the [engineering queue](ROADMAP.md#current-engineering-queue) for sequencing.

### Agent framework

#### A1. Capability services and task storage — implemented

- Reusable profiles, sessions, navigation, downloads, permission handling, and persistence; dialogs remain in the UI.
- Keep engine operations on the Qt thread; dispatch through a command broker.
- Introduce a backend interface for session creation, navigation, inspection, actions, and view attachment; implement Qt only initially.
- Application-owned SQLite stores task records and an append-only event journal; retain existing profile/config compatibility.
- Acceptance: a deterministic test client operates a task session with no AI or external framework.

#### A2. Sessions and grants — in progress

Private and concurrently shared existing-profile grants, automatic user approval
popups, origin/action/budget
enforcement, cancellation, and durable decision records are implemented. Grants
for later file/active-page capabilities remain to be implemented alongside those
operations. The following describes the full target scope.

- Task-owned pages, grant, artifacts, history, and lifecycle exist independently of visible tabs.
- Default to a separate off-the-record task profile. Explicitly authorized persistent profiles share their existing cookies/storage; disclose that boundary.
- Task-scoped grants cover sites, profiles, app tools, file destinations, data disclosure, external effects, duration, and budgets. Standing permissions supply defaults, never self-expanding authority.
- A proposed task is not authorized until approved by the user. Ask again only when new authority is needed.
- All UI/test/MCP mutations use the same policy checks.
- Deny arbitrary shell, unrestricted filesystem, raw cookie export, and public unrestricted JavaScript evaluation.
- Private-network destinations require explicit permission. Page text, classification, and connector descriptions are untrusted, not authority.
- This is a browser capability boundary, not an OS sandbox for external agents; tools accessed outside it cannot be controlled.
- Acceptance: cross-task/profile/destination/grant attempts fail before controlled execution.

#### A3. Text-first inspection and deterministic actions — in progress

Read-only snapshots, explicit content grants, bounded extraction, and opaque
target validation, observed link navigation, scriptless control edits, form submission, scrolling,
and bounded waits are implemented. Text editing events/undo are verified on the
pinned engine. Native keyboard activation/selection are implemented. Pointer
gestures, active-page permissions and full SPA/form workflows remain pending.
Exact file grants and bounded uploads/downloads are implemented.
Website JavaScript remains ungranted: the next stage must cover background
network transports/workers and submit-handler mutations with tested enforcement
before enabling scripts. No JavaScript wrappers alone establish that boundary.
The following describes the complete milestone target.

Prefer authorized app/API connectors, then public document retrieval, then live JS-enabled pages. Visual inspection is optional or user-requested. A session owns QWebEnginePage independently of a view; attach the same page on watch/takeover. Invisible pages may still need JS, layout, and Chromium resources.

Snapshots include URL/title, frame identity, revision, readable content/provenance, and candidate actions with labels/roles/operations/enabled state/constraints. Report limits for inaccessible frames, closed shadow roots, canvas, and ambiguous custom controls.

Use browser-issued opaque target handles tied to session/frame/revision. Reject stale/ambiguous targets. Support navigation, activation, entry, selection, scrolling, waits, authorized uploads, and downloads. Use native input semantics where synthetic JS clicks are insufficient. Return observed changes and evidence, not just dispatch success.

Acceptance: fixtures for static pages/forms/SPAs/frames/DOM replacement work without screenshot loops; unsupported actions request takeover or return explicit limitations.

#### A4. Framework-neutral MCP — in progress

Opt-in private IPC and the official-SDK stdio/HTTP adapter expose current task
operations with published input schemas, connection ownership, capability
and selected-profile discovery, structured results, and existing user approval.
Two independently implemented clients passed the synthetic shared-profile and
cancellation workflow on MCP 2025-11-25 over both transports. Reconnect/resume
and broader client compatibility remain pending. Same-page
watch and scoped user handoff are implemented. See
[setup](#local-agent-access-mcp).

- Maintained MCP SDK; stdio client-launched adapters and an authenticated loopback-only Streamable HTTP endpoint.
- Restricted local IPC links adapters to the single running Qt process. No public debug port or grant bypass.
- Published schemas, capability discovery, structured errors, SDK-supported protocol compatibility, and interoperability tests.
- Initial tools: `task_create`, `task_status`, `task_control`, `task_events`; `session_open`, `session_close`, `page_navigate`; `page_inspect`, `page_act`; `search_query`; `artifact_store`, `artifact_list`; `app_list_tools`, `app_call_tool`.
- Core types: TaskGrant, SessionSnapshot, ActionDescriptor, ActionResult, Artifact, Connector.
- Results distinguish success, denied authority, required input, stale state, timeout, unsupported behavior, failure, and uncertain external outcome.
- Acceptance: two independently implemented MCP clients use the same tools without framework-specific browser code; both transports are tested.

#### A5. Optional search and classification — pending

Search uses an Exa adapter returning URLs/titles/content when available and retrieval provenance. Keep address-bar search unchanged. Credentials, disclosure permission, budgets, caching, timeouts, and rate-limit handling are required.

Extract candidates deterministically first; a Jev adapter answers finite classification questions with a pinned, benchmarked model version. Support replaceable cloud endpoints and a local-provider adapter. Classification annotates candidates/purpose; it never plans, invents targets, or authorizes execution. Cache by revision/context, batch where supported, and report uncertainty on low confidence/failure. Redact credentials/passwords/sensitive fields; authenticated content needs explicit disclosure permission. No-provider operation remains fully functional.

Acceptance: held-out comparisons against deterministic-only inspection measure coverage, incorrect mappings, latency, and cost. Enable classification only where it improves results.

#### A6. Application connections and recovery — in progress

The trusted connector gateway, exact tool/effect/destination scopes, worker limits,
immutable-draft binding, durable request identity, observed receipts and read-only
reconciliation passed mock document/email MCP workflows over both Steed transports.
Google Docs/Gmail mappings and user-owned desktop OAuth/Secret Service setup are
implemented; registries stay empty until accounts are configured. See
[current app operations](#browser-managed-application-operations) and
[Google setup](#google-app-connections). User-selected follow-ups with fresh access
and historical app-write protections are implemented; see
[task recovery](#continuing-previous-work). Real-account acceptance, additional
providers, automatic retention policies and agent-managed mode remain pending.
User-controlled clearing of provider result text/links is implemented; see
[result retention](#clearing-saved-app-results).

- Browser-mediated mode: MCP gateway enforces grants, logs activity, handles cancellation, and records results.
- Agent-managed mode: direct external connections; clearly disclose absent browser visibility/enforcement.
- User-approved connector setup; secrets in OS credential storage, never styling/exported logs.
- Trusted configuration defines risk; remote tool annotations alone do not. Sending/publishing/deleting/spending requires explicit authority.
- Persist request IDs/receipts, bound read retries, never blindly replay uncertain writes, reconcile external state where possible.
- Report partial results/blockers/uncertainty. Do not replay unfinished writes automatically after restart.
- Acceptance: generic gateway tested against mock document/email MCP servers before real accounts.

#### A7. Collaboration UI and declarative styling — in progress

Implemented: nonmodal same-page watch, trusted user takeover through supported
scoped actions, return with fresh references, and lifecycle cleanup. Shared-profile
and both MCP transport fixtures passed. Task Permissions includes recent
activity and retained download receipts with progress/cancellation.
Unrestricted pointer/custom controls and declarative styling remain pending.
Retained artifact review/deletion and app receipts/cancellation/reconciliation are
implemented.

- Task workspace shows agent identity/objective/authority/status/activity/artifacts/evidence.
- Watch attaches a view without taking control. Takeover obtains exclusive ownership and blocks agent mutations. Resume invalidates stale snapshots. Cancel stops queued/new commands; dispatched external effects may require reconciliation rather than rollback.
- Multiple agents have separate identities/tasks; each session has one mutation owner.
- Versioned `appearance.toml` controls supported browser chrome: colors, typography, spacing, component order/visibility/panel placement.
- Component registry and validated layouts, live preview, last-known-good rollback, recovery shortcut. No executable configuration or removal of all safety/recovery access. Website styling is separate later work.
- Acceptance: user watches/interrupts, continues in the same page, then resumes delegation without losing session state; malformed styling rolls back safely.

### End-to-end validation and rollout

#### Research → document → email — mock workflow passed; real services pending

An SDK client and independent JSON-RPC client each passed this workflow over both
Steed transports using synthetic page sources and mock document/email MCP providers.
Each client retained source-linked drafts, created one document, sent its reference
only to an approved synthetic recipient, and reconciled a lost acknowledgement
without resending. Google account setup is implemented with synthetic acceptance;
real-account checks and additional provider adapters remain pending.

The external agent obtains a scoped task grant, searches/inspects sources, generates its own document, stores it with references/provenance, creates a document through an approved connector, sends only to authorized recipients, and returns document references/receipts/unresolved limitations. Browser-side generation is not required.

Use fixtures/mock connectors first, then user-configured real services. Roll out locally: read-only inspection → controlled page writes → mock app writes → explicitly authorized real writes.

Required regression coverage:

- Browser essentials, profile persistence, restart/shutdown, permissions, multiple windows.
- Global-cookie migration, local logins, updates/deletions, no resurrection, incognito isolation.
- Stale targets, replaced DOM, frames, SPA transitions, unsupported controls/timeouts.
- Unauthorized profiles/destinations/files, forged handles, hostile instructions, connector denials.
- Takeover/resume, simultaneous commands, cancellation, expired grants, disconnect.
- Crash/interruption, failed providers, uncertain sends, duplicate request IDs.
- MCP transports/multiple clients/unsupported capabilities; styling rollback/recovery.

Definition of done: independently implemented clients complete the workflow, every controlled mutation passes policy, and failures provide usable evidence. Universal external task success is not promised.

### Later browser enhancements

#### Extensions — pending, non-blocking

Per-profile filesystem import/install, enable/disable/remove, errors, and action popups. Persist enabled choices and restore after startup. Start with Manifest V3 only. Do not promise arbitrary Chrome compatibility or store installation; Qt excludes Manifest V2 and custom extensions in off-the-record profiles.

#### Bookmarks — implemented daily-use milestone

Per-profile search, folder labels, direct opening in a new tab, name/address/folder
editing, atomic HTML import/export
and bounded New Tab previews are implemented. Imports preserve existing entries;
private bookmarks remain memory-only. See [usage](#welcome-new-tab-and-bookmarks).
Hierarchical empty-folder management, sync and agent bookmark APIs remain separate.

#### Built-in agent hosting — separate future proposal

Consider scheduling/autonomous planning/model-account management only after proving the framework. A new proposal must cover OS isolation, billing, credentials, resources, and recovery. The initial roadmap does not include an internal AI runtime or unrestricted automation.

## References

- [Qt pages](https://doc.qt.io/qt-6.11/qwebenginepage.html)
- [Qt extensions](https://doc.qt.io/qt-6.11/qwebengineextensionmanager.html)
- [Google browser/login restrictions](https://support.google.com/accounts/answer/7675428)
- [Brave filtering engine](https://github.com/brave/adblock-rust)
- [MCP transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
- [Exa search](https://exa.ai/docs/reference/search)
- [Jev models](https://docs.typesafe.ai/models)
