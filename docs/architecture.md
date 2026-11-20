# Architecture

How the code is laid out, engine and storage internals, current limitations and the planned architecture. [Documentation index](README.md).

## Project Structure

```
steed/                      (the repository)
├── README.md               Overview and quick start
├── ROADMAP.md              Where things stand and what is next
├── AGENTS.md               Conventions for people and coding agents working here
├── steed                   Launcher: runs main.py with the project's .venv
├── install.sh              Install or update; registers the menu entry
├── main.py                 Entry point
├── agent_mcp.py            MCP adapter that agents start to reach the running browser
├── requirements.txt        The engine (PyQt6, Qt WebEngine)
├── requirements-dev.txt    Plus the lint and format tool
├── requirements/           Optional extras: mcp, apps, dictation, worker wheels
├── browser/                The browser itself
│   ├── browser_window.py   Main window: tabs, profiles, menus, permissions
│   ├── profile_manager.py  Profiles and the shared engine registry
│   ├── tab_manager.py      Tab save/restore, closed tabs, tab groups
│   ├── webengine.py        Engine settings; the one place engine details live
│   ├── config.py           Settings (~/.custom_browser/config.json)
│   ├── password_*.py       Encrypted vault, filling, checkup; passkeys.py
│   ├── cookie_*.py         Cookie viewing, sharing between profiles, snapshots
│   ├── extension*.py       Chrome extensions: install, bridge, rules, shim
│   ├── ad_blocker.py       Request filtering; download_manager.py, privacy.py, …
│   └── build_codecs.py, upgrade_engine.py, install_launcher.py,
│       setup_translation.py, install_worker_bundle.py
│                           Helper commands: .venv/bin/python -m browser.<name>
├── ui/                     Windows, dialogs and widgets; ui/web/ holds the HTML,
│                           CSS and scripts of Steed's own pages (steed:…)
├── services/               Agent tasks and their permissions, the agent gateway
│                           and providers (Codex, Claude Code, ACP), app
│                           connections, workers, routines, watches, dictation
├── connectors/             Packaged app connection recipes and their fixtures
├── examples/               Example connector recipes and data panels
├── tests/                  unittest suites (test_*.py), smoke scripts, fixtures
└── docs/                   This documentation, screenshots, release plan, history
```

`browser/`, `ui/` and `services/` are packages imported from the repository
root (`from browser import config`, `from ui.address_bar import …`). Each
module starts with a docstring saying what it is for.

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

`browser/webengine.py` is the only module that talks to the web engine (Qt
WebEngine today). Everything else asks for things in Steed's terms, so another
engine means another version of that one module. It provides:

- **Pages and views:** `Page`, `View` and `Profile`. Steed's pages subclass
  `Page` and decide through Steed's hooks, with kinds in words:
  `navigation_allowed(url, kind, main_frame)` (`"link"`, `"typed"`, `"form"`,
  `"back_forward"`, `"reload"`, `"redirect"`, `"other"`), `new_window(kind)`
  (`"tab"`, `"background_tab"`, `"window"`, `"dialog"`), `console_message(level,
  …)`, `choose_files(several, …)` and `script_alert/confirm/prompt`. `View`
  has `new_window` too. `is_view`, `is_page`, `view_for(page)`, `view_around`.
- **Network requests:** `Request` (its `url`, `page`, `initiator`, `method`,
  `resource` in the names browser extensions use, such as `"main_frame"`,
  `"script"` or `"service_worker"`, `navigation`, `main_frame`, and `block`,
  `redirect`, `set_header`), `RequestFilter` (override `filter(request)`) and
  `set_request_filter(profile_or_page, filter)`. Ad blocking, extensions'
  network rules, agents' network limits, HTTPS-first and Steed's own pages'
  isolation are all request filters.
- **Steed's own pages:** `register_scheme`, `SchemeHandler` (override
  `handle(request)`), `SchemeRequest` (`url`, `method`, `initiator`, `alive`,
  `reply(mime, data)`, `fail(reason)`), `install_scheme`.
- **Scripts:** `run_script(page_or_frame, source, callback, world=...)` and
  `run_in(target, source, [world], [callback])` in `PAGE_WORLD` (the page's own
  JavaScript) or `STEED_WORLD` (same document, separate globals the page cannot
  see); `page_script` and `add_profile_script` for scripts every page runs at
  `"start"`, `"ready"` or `"loaded"`.
- **Frames, cookies, developer tools:** `frames(page)`, `main_frame`,
  `cookie_store(profile)`, `devtools_of(page)`, `attach_devtools(page, tools)`.
- **Permissions and downloads:** `on_permission(page, modern, legacy)`,
  `keep_permission`, `answer_legacy_permission`, `MODERN_PERMISSIONS`,
  `on_download(profile, handler)`, `save_page(page, path, "single" |
  "complete" | "mhtml")`, `Channel` (the bridge to Steed's own pages) and
  `WebAuthRequest` (passkey prompts).
- **Tab lifecycle and crashes:** `tab_state(page)` (`"active"`, `"frozen"`,
  `"unloaded"`), `set_tab_state`, `wake`, `lightest_state`, `deeper`, and
  `crash_reason(status)`.
- **Page actions, options, profiles, versions:** `trigger(page, "back")` and
  the other `PAGE_ACTIONS`, `menu_action`, `find_text`; `set_options(settings,
  javascript=False, …)` with Steed names for each engine setting;
  `private_profile`, `stored_profile`, `set_keep_session_cookies`; `versions()`.

Pages' everyday methods and signals (`url`, `title`, `setUrl`, `load`, `back`,
`history`, zoom, `loadFinished`, `urlChanged`, `titleChanged` and the like) are
the common ground of web views and part of the interface as they are; another
engine's `Page` provides them under the same names.

`tests/test_engine_boundary.py` enforces it: no other file imports Qt
WebEngine or Qt WebChannel, calls the engine's own methods for what the module
wraps, or overrides the engine's hooks; and every engine navigation, window,
console, request and crash kind has a Steed name.

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

The following is the target design. The [internal task foundation](task-services.md#internal-task-services)
is implemented; full agent features, generic extension support, and bookmarks remain
future work. Implementation order and status live in [ROADMAP.md](../ROADMAP.md).

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

The [Focus example](../examples/layouts/focus.json) demonstrates portable version 1:
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

The [Daily tools example](../examples/layouts/daily-tools.json) uses portable version 2
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
   [the dashboard example](../examples/data_panels/task-dashboard.json), replacing
   `OWNED_ARTIFACT_ID` with the returned ID. Submit `steed_panel_propose` with the
   exact UTF-8 `source` and a stable `request_id`; poll `steed_panel_status` until
   the user chooses a profile and enables the panel.
3. Version 3 also accepts `file_id` for text/list/table and requires it for images.
   Create/seal bytes through the workspace tools under existing workspace authority;
   user-shared files already copied into that task's workspace are eligible too.
   See [the image/file example](../examples/data_panels/generated-chart.json). A file
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
[the linked-notes example](../examples/data_panels/library-notes.json):

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
[account-report.json](../examples/data_panels/account-report.json), replacing the
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
[account-table.json](../examples/data_panels/account-table.json). A list uses:

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
[daily-instructions.json](../examples/data_panels/daily-instructions.json):

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
[form example](../examples/data_panels/research-form.json).

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
[comparison data](../examples/data_panels/comparison-table.json).
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
See the [engineering queue](../ROADMAP.md#current-engineering-queue) for sequencing.

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
[setup](agents.md#local-agent-access-mcp).

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
[current app operations](task-services.md#browser-managed-application-operations) and
[Google setup](task-services.md#google-app-connections). User-selected follow-ups with fresh access
and historical app-write protections are implemented; see
[task recovery](agents.md#continuing-previous-work). Real-account acceptance, additional
providers, automatic retention policies and agent-managed mode remain pending.
User-controlled clearing of provider result text/links is implemented; see
[result retention](task-services.md#clearing-saved-app-results).

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
private bookmarks remain memory-only. See [usage](using-steed.md#welcome-new-tab-and-bookmarks).
Hierarchical empty-folder management, sync and agent bookmark APIs remain separate.

#### Built-in agent hosting — separate future proposal

Consider scheduling/autonomous planning/model-account management only after proving the framework. A new proposal must cover OS isolation, billing, credentials, resources, and recovery. The initial roadmap does not include an internal AI runtime or unrestricted automation.
