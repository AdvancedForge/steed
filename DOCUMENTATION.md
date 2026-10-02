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
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Find in page and zoom](#find-in-page-and-zoom)
- [KeePassXC setup](#third-party-password-managers)
- [Profiles and stored data](#profiles-and-stored-data)
- [Tabs and groups](#tabs-and-groups)
- [Cookies and global sharing](#cookies-and-global-sharing)
- [Downloads and ad blocking](#downloads-and-ad-blocking)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Engine and storage internals](#engine-and-storage-internals)
- [Internal task services](#internal-task-services)
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

### Google sign-in

Google sign-in compatibility is enabled by default in **File → Settings**. It sends a Firefox User-Agent to `https://accounts.google.com/`, following [qutebrowser's account-host workaround](https://github.com/qutebrowser/qutebrowser/issues/5182). Other sites use your configured/default User-Agent. Main-page GET redirects into or out of the account host are restarted to avoid Qt losing the per-site header; POST requests are never replayed.

Restart with `.venv/bin/python main.py`, then open `https://accounts.google.com/` and sign in normally. The compatibility setting takes precedence over the global User-Agent override for this host. Reload sign-in tabs after changing the setting.

Local HTTPS tests verify redirects, form bodies, and the opt-out on the pinned Qt 6.11 runtime. The user reported account sign-in working on October 1, 2026; persistence after restart and third-party sign-in remain unconfirmed. [Google can restrict embedded-browser sign-ins](https://support.google.com/accounts/answer/7675428), and this workaround cannot guarantee acceptance for every account. External-browser OAuth does not create a signed-in web session in this browser.

The same HTTPS fixture stalled on the older system Qt 6.4.2 installation. Use the project's virtual environment for Google sign-in; older-Qt startup compatibility does not establish login compatibility.

## Usage

### Keyboard Shortcuts

- `Ctrl+T`: New tab
- `Ctrl+W`: Close current tab (the window always keeps one tab)
- `Ctrl+Shift+T`: Restore the most recently closed tab for this profile
- `Ctrl+Shift+N`: Open search profile in a separate window (incognito)
- `Ctrl+Shift+0`: Switch the current window to search profile
- `Ctrl+Shift+1` … `Ctrl+Shift+9`: Switch to the Nth profile
- `Ctrl+J`: Downloads
- `Ctrl+R` or `F5`: Reload the current tab
- `Ctrl+L`: Focus and select the address bar
- `Ctrl+F`: Open find in page and select the current query
- `Enter`, `F3`, or `Ctrl+G`: Next match (`Enter` while the find input is focused)
- `Shift+Enter`, `Shift+F3`, or `Ctrl+Shift+G`: Previous match
- `Escape`: Close an open find bar
- `Ctrl++` or `Ctrl+=`: Zoom in
- `Ctrl+-`: Zoom out
- `Ctrl+0`: Reset zoom to 100%
- `Ctrl+Shift+L`: Fill a matching KeePassXC login (choose an entry if several match)
- `Ctrl+,`: Settings
- `Ctrl+Q`: Close the window

### Menu Options

**File Menu:**

- New Tab
- Restore Closed Tab
- Close Tab
- Reload
- Go to Address Bar
- Clear Browsing History...
- Passwords...
- Fill Login
- Downloads...
- Task Permissions...: Review, approve, deny, revoke, or cancel task requests
- Ad Blocking...
- Settings...
- Exit

**Edit Menu:** Find in Page, Find Next, Find Previous.

**View Menu:** Zoom In, Zoom Out, Reset Zoom.

**Profile Menu:**

- New Profile...
- Switch Profile...
- Pop out Profile...: Open a profile in a second window
- Rename Profile...
- Delete Profile...: Archives a profile (recoverable)
- Pop Out Search Profile (Incognito)
- Switch to Search Profile (Incognito)
- Quick Switch: Per-profile shortcuts

**Tab Groups Menu:**

- Manage Tab Groups...

Individual tabs also expose **Set Group…**, **Set to existing**, **Clear Group**, and
**Collapse / Expand Group** from their context menu.

**Cookies Menu:**

- View Cookies...: Open cookie manager dialog
- Clear All Cookies: Delete all cookies for current profile
- Use Global Cookies for This Site...: Reset the current site's baseline

Dark mode is available in **File → Settings**.

Site permissions (camera, microphone, location, notifications, screen sharing) are
asked per request and default to deny. Nothing is remembered between requests.

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

Use **File → Clear Browsing History...** to clear the current profile's saved
visits and suggestions. Subsequent visits start a new history.

### Third-Party Password Managers

The **Passwords** button beside the address bar connects directly to KeePassXC
on Linux. Login selection, filling, and saving happen inside this browser;
KeePassXC owns the encrypted vault and handles unlocking and access approval.
No browser extension or Auto-Type configuration is required.

1. Install [KeePassXC](https://keepassxc.org/download/), create or open a vault,
   and enable **Tools → Settings → Browser Integration → Enable browser integration**.
2. Install this project's updated requirements (`.venv/bin/python -m pip install
   -r requirements.txt`) and restart the browser.
3. Click **Passwords → Connect KeePassXC**, approve the connection in KeePassXC,
   and give it a name such as `Steed default`. Pairing is remembered per ordinary
   profile; incognito pairing lasts only for this application's session.
4. Open an HTTPS login page. Press **Ctrl+Shift+L** to fill the sole matching
   entry, or choose an entry when several match. You can also use
   **Passwords → Fill Login**. KeePassXC may ask you to approve site access once.
5. To store a login, type it into the form and choose
   **Passwords → Save This Login...**. Review the website, username, and masked
   password, then Save. This creates a new entry in the open KeePassXC vault.

For separate username/password screens, such as Google, use the fill shortcut
on each step. Filling sends input/change events and never submits the form.
Automatic save prompts, updating existing entries, iframe/shadow-root forms,
TOTP, and passkeys are not implemented. Cross-site form actions and non-HTTPS
pages are excluded (loopback HTTP is allowed for local fixtures).

The connection follows the published
[KeePassXC browser protocol](https://github.com/keepassxreboot/keepassxc-browser/blob/develop/keepassxc-protocol.md).
It uses PyNaCl authenticated encryption, verifies the local socket peer belongs
to this user, and checks response nonces. Passwords are used transiently for
the requested fill/save and are not written to browser JSON or logs.
Only private pairing metadata is stored in per-profile `password_manager.json`
with owner-only permissions. Navigation or document replacement invalidates a
pending fill. The picker retains only entry names/usernames/identifiers and
fetches credentials again when you fill, so locking the vault invalidates the
listed login's access. Closing the password dialog cancels pending requests.

[KeePassXC Auto-Type](https://keepassxc.org/docs/KeePassXC_UserGuide#_auto_type)
remains an optional alternative. Window titles include the active hostname,
so a window association such as `Custom Browser - * - accounts.google.com`
can match the site. General extension support remains pending. Loading the
unmodified official Bitwarden 2026.9.2 package crashed the tested Qt WebEngine
6.11 runtime with both install and unpacked-load APIs, so it is not exposed as
a supported password-manager option.

## Profiles and stored data

- Ordinary profiles have separate cookies, tabs, and web storage; application preferences are shared
- Profiles are stored in `~/.custom_browser/profiles/{profile_name}/`
- The `global` profile is stored in `~/.custom_browser/global_profile/`; its cookies form a one-way shared baseline while separate profile logins remain local
- Windows share application-owned engine profiles; a second browser process cannot open the same storage directory
- Search profile acts as incognito mode (no persistence)
- Easy profile switching without losing current session

### Creating and Switching Profiles

1. Go to **Profile → New Profile**
2. Enter a profile name
3. The browser will switch to the new profile
4. Use **Profile → Switch Profile** to change profiles

### Search Profile (Incognito)

The search profile is a special profile that:

- Doesn't save cookies
- Doesn't save browsing history
- Doesn't save tabs
- Keeps browsing data in memory for the application registry's lifetime; switching away or closing one of several windows does not reset that shared in-memory profile

Access it via **Profile → Pop Out Search Profile (Incognito)** or `Ctrl+Shift+N`.

**Profile → Quick Switch** lists existing profiles immediately at startup.
The numbered shortcuts work before the menu is opened. Ordinary profiles are
listed alphabetically, including `default` and `global`.

## Tabs and groups

- Multiple tabs with tab bar interface
- Tabs are saved before closing
- Tabs are restored when reopening a profile
- Tab state includes URL, title, and stable tab/group identities
- `Ctrl+Shift+T` restores recently closed tabs for the current profile (up to 50, not saved across restart)

## Cookies and global sharing

- View all cookies for current profile
- Delete selected cookies, selected sites, or all cookies through the viewer/menu
- Filter cookies by domain or name
- Search profile has no cookie persistence

Existing profiles are migrated conservatively: pre-existing cookies remain local rather than being overwritten by the global profile. **Cookies → Use Global Cookies for This Site** explicitly resets the current site's baseline, after confirmation. Local changes/deletions prevent automatic reinheritance. Cookie sharing does not copy local storage or guarantee a complete signed-in session.

## Downloads and ad blocking

Open **File → Downloads** (`Ctrl+J`) for progress, cancellation, supported pause/resume, file opening, and history. Incognito downloads are excluded from disk history. Interrupted downloads are not automatically restarted.

Open **File → Ad Blocking** to download/update EasyList and EasyPrivacy, toggle filtering, or add host-name exceptions. Only a small bootstrap rule set ships with the browser; download the full lists for broader coverage. Rule compilation runs off the GUI thread, and a failed update keeps the last working rules. Network blocking applies immediately, while cosmetic (CSS) hiding is re-applied on page load — reload pages after changing exceptions. Same-origin frames, including nested frames and `srcdoc`, receive their own CSS rules; each pass covers up to 32 documents and eight frame levels. Cross-origin frames cannot be inspected. Scriptlets and resource redirects are not enabled, and unsupported filters are not presented as full uBlock compatibility.

## Configuration

Configuration is stored in `~/.custom_browser/config.json`. You can edit this file directly or use the browser's settings interface.

### Configuration Options

- `default_profile`: Default profile to use on startup
- `home_page`: URL to load for home button
- `search_engine`: URL prefix such as `https://www.duckduckgo.com/?q=` (search text is appended)
- `dark_mode`: Enable dark mode by default
- `restore_tabs_on_startup`: Restore tabs when opening a profile
- `window_width`: Default window width
- `window_height`: Default window height
- `user_agent_override`: Replacement user agent; empty restores the engine default
- `google_login_compatibility`: Account-host User-Agent workaround (enabled by default)
- `global_cookie_sharing_mode`: `on_switch` or `all_profiles`
- `global_cookie_sharing_apply_on_startup`: Whether global cookies are reconciled automatically
- `adblock_enabled`: Enable network blocking and cosmetic filtering
- `adblock_exceptions`: Host names excluded from filtering

The same directory also holds browser-managed state, which is not configuration:
`downloads.json`, `filter_lists.json`, `global_cookie_deletions.json`, the
`profiles/` and `global_profile/` storage directories, `deleted_profiles/` for
archived profiles, and per-profile `tabs.json`, `groups.json`, `history.json`,
`password_manager.json`, and
`cookie_inheritance.json`. The inheritance journal stores hashes, never cookie
values. Internal task services create `tasks.sqlite` on first use to store task
metadata and their event journal.

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
├── password_manager.py     # Encrypted local KeePassXC connection and pairing
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
│   ├── task_store.py       # SQLite tasks, append-only events, restart recovery
│   ├── command_broker.py   # Named commands dispatched on the Qt thread
│   ├── browser_backend.py  # Task pages, profile sharing, and observed navigation
│   ├── navigation_service.py # Shared navigation, find, and zoom operations
│   ├── permission_service.py # Modern/legacy permission responses and task denial
│   ├── task_policy.py      # Exact scopes, expiry, and thread-safe request budgets
│   ├── task_network.py     # Private per-target service-worker bypass adapter
│   ├── task_input.py       # Fixed native keyboard events and task focus emulation
│   ├── task_client.py      # Task-bound client command facade
│   ├── page_inspector.py   # Asynchronous snapshots, validation, and fixed actions
│   ├── page_actions.py     # Bounded action inputs and value-free receipts
│   ├── form_submission.py  # Per-page submission tickets and journal URL privacy
│   ├── page_inspection.js  # Fixed isolated-world DOM reader and mutation checks
│   └── task_service.py     # Application-owned task/session coordination
├── codec_test.html         # Local media/codec fixture
├── ui/
│   ├── address_bar.py     # Address bar widget
│   ├── find_bar.py        # Debounced page search and match navigation
│   ├── tab_bar.py         # Tab bar widget with group context menu
│   ├── cookie_dialog.py   # Cookie viewer
│   ├── download_dialog.py # Download list with pause/resume/cancel/open
│   ├── media_dialog.py    # One-shot screen/window share picker
│   ├── task_dialog.py     # Trusted task-grant review and cancellation
│   ├── task_approval.py   # Automatic queued approval popups
│   ├── password_dialog.py # KeePassXC connection, login picker, fill/save actions
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
│   ├── smoke_google_login.py # Local HTTPS login-compatibility check (manual)
│   ├── smoke_password_manager.py # Real KeePassXC synthetic-vault check (optional)
│   └── profile_restart.py # Two-process cookie persistence check (manual)
├── pyproject.toml         # Ruff lint/format configuration
├── requirements-dev.txt
└── requirements.txt
```

The smoke and profile-restart files are runnable scripts rather than unittest modules, so
`unittest discover` does not collect them; run them directly as shown below.

## Engine and storage internals

- **GUI Framework**: PyQt6
- **Browser Engine**: PyQtWebEngine (Chromium-based)
- **Storage**: JSON files for configuration, SQLite for cookies (via Qt)
- **Profiles**: Isolated storage directories per profile

JSON preferences and tab files are replaced atomically. Settings are saved when you click Save in the preferences dialog.

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
declines downloads. Task file actions remain disabled until destination grants
and deterministic file operations are implemented.

The task service is available to trusted in-process Python controllers through
`window.profile_manager.registry.tasks`. It is initialized only on demand and
shared across windows using the same application registry. This is a developer
API; no agent endpoint or MCP tools are exposed yet. **File → Task Permissions**
provides user approval, denial, revocation, and cancellation. A client can propose
a scope but cannot approve it through the command broker.

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
running inside the application. External transport authentication remains A4 work.

| Command | Arguments / result |
| --- | --- |
| `task.create` | `objective`; returns a task record |
| `task.status`, `task.list` | `task_id` for status; records for list |
| `task.events` | `task_id`, optional `after` sequence and `limit`; ordered events |
| `task.finish` | `task_id`, optional terminal `status`; closes owned sessions |
| `grant.request` | `task_id`, `scope`; records a pending request, without authority |
| `grant.list` | `task_id`; task grant records, including decisions |
| `session.open` | `task_id`, optional `profile="private"`; requires approval, returns a session snapshot |
| `session.navigate` | `task_id`, `session_id`, `url`; dispatches GET navigation |
| `session.inspect` | `task_id`, `session_id`; URL/title/load metadata only |
| `page.inspect` | `task_id`, `session_id`; explicit `read` grant, returns a structured page snapshot |
| `page.target` | `task_id`, `session_id`, `snapshot_id`, `target_id`; validates an observed target and returns its description |
| `page.navigate` | `task_id`, `session_id`, `snapshot_id`, `target_id`; follows a validated link and reports the load outcome |
| `page.act` | `task_id`, `session_id`, `snapshot_id`, `target_id`, `operation`, optional `text`/`index`; requires `read` + `edit`, returns a control-change receipt |
| `page.submit` | `task_id`, `session_id`, `snapshot_id`, `target_id`; requires `read` + `navigate` + `submit`, returns an observed native-form outcome |
| `page.scroll` | `task_id`, `session_id`, `snapshot_id`, optional `frame_id`, `dx`, `dy`; requires `read` + `scroll`, returns observed offsets |
| `page.wait` | `task_id`, `session_id`, optional `condition`, `text`, `timeout_ms`; requires `read`, returns an observed match, timeout, or load failure |
| `session.stop`, `session.close` | `task_id`, `session_id`; stops or destroys the session |
| `session.attach` | `task_id`, `session_id`; returns a Qt view of the existing page |

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
review it later in **File → Task Permissions**, which also provides revocation.
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
the last browser window closing disposes pages before their profiles. Downloads,
file selection, site permissions, document scripts, new workers, WebSockets, external URL
schemes, and the built-in PDF viewer are unavailable in current task pages.
Ordinary browser tabs retain their existing settings. Task browsing currently
supports document retrieval with GET/HEAD and explicitly authorized native form
submission. Even GET/HEAD can cause website effects; a grant does not establish
that a site is read-only. POST is allowed only through a bound, one-use form ticket.

Scopes match exact HTTP/HTTPS origins, including scheme and port; subdomains and
redirect destinations need their own entries. Credentials, wildcards, and origin
paths are rejected. `about:blank` is allowed without adding an origin. File
destinations cannot currently be granted. Only
`navigate`, metadata `inspect`, content `read`, view `attach`, control `edit`,
`scroll`, and form `submit` are supported actions. The default action list remains
`navigate`, `inspect`, and `attach`; content, edits, scrolling, and submission need
explicit approval.
An edit grant does not authorize submission, POST, active scripts, or file actions.

### Read-only page inspection

Add `"read"` to a proposed grant's `actions` before requesting approval. The
approval screen discloses that readable text and control labels will be shared
with the task client, including on signed-in pages. Existing metadata-only grants
cannot call `page.inspect`, `page.target`, `page.navigate`, `page.act`, `page.submit`, `page.scroll`,
or `page.wait`. No cloud provider receives this data
through the current implementation; external-agent transport and provider
disclosure permissions remain future milestones.

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
Clients cannot supply scripts or selectors. Document JavaScript stays disabled;
the reader changes no DOM content. It omits input/select/textarea values, editable
text, and hidden/script/style content. These omissions do not classify arbitrary
visible page text as safe: site text and labels remain untrusted data.

Results contain at most 32,000 text characters, 200 targets, and 16 frames, with a
12,000-node traversal budget and a five-second engine timeout. Labels and link
URLs are bounded too; links over 2,048 characters cannot be navigated rather than
executing a truncated destination. Same-origin frames and readable `srcdoc` frames are
inspected. Cross-origin/sandboxed or unready frames report
`frame_inaccessible_or_not_ready`; shadow DOM and canvas content are not inspected.
Truncation and unsupported content are reported, not silently presented as a
complete page.

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
and contenteditable controls remain unavailable to `page.act`. Scriptless edits
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
Forms containing password/file controls, child-frame forms, multipart/plain-text
forms, dialog methods, image submitters, and new-window/named targets are unavailable.
Document scripts remain disabled, so script-handled forms and SPAs need later work.
Submission checks this setting before preparation and again before arming its
ticket. An enabled setting returns `scripted_submission_unavailable` without
dispatch: JavaScript submit handlers could change sensitive controls after DOM
preflight, and a route/method ticket does not bind the serialized body.
This does not add password-manager integration to tasks.

One per-page ticket binds the destination and method to a dispatch. The interceptor
checks main-frame resource type, grant lifetime, origin, and request budget; it never
exports the request body. General POST requests and a second POST through a
307/308 redirect are denied. GET redirects after submission retain ordinary origin
and budget checks. Qt retains its form navigation classification on those redirects;
the separate navigation gate rejects unsolicited second form submissions.
User tabs on the same profile retain their ordinary submission behavior.

`FormSubmissionResult` includes action/session/target IDs, revision, destination
origin, `request_authorized`, and status: `validation_failed`, `loaded`, `load_failed`,
or `timed_out`. Authorization means the network gate allowed a request, not that the
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
`timed_out` on deadline. `PageWaitResult` contains session, condition, status,
URL/revision, and limitations; the query and page text are excluded from receipts
and journal events. Text waits refresh inspection and invalidate old references.
They allow DOM changes while waiting; navigation, stop, a control action, renderer
termination, or session shutdown interrupts them. Revocation/expiry checks also
apply before returning results. Only one wait runs per session, and a wait cannot
start during a controlled action. Waiting does not spend network budget by itself.

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

`allow_private_network=false` permits public literal IP addresses only. Private
addresses and all DNS hostnames require `true`, explicitly disclosed in the
approval dialog. The [Qt request-info API](https://doc.qt.io/qt-6.11/qwebengineurlrequestinfo.html)
provides URLs but no resolved peer address; Steed therefore treats a DNS name's
network route as unverified. Allowing this flag still restricts requests to the
listed origins; it does not add other sites or ports. There is no DNS pinning,
OS/network sandbox, or control over tools used outside this application.

Approval installs an immutable live grant only after its journal transaction
commits. Command checks happen at dispatch, and a profile or task-page
[Qt request interceptor](https://doc.qt.io/qt-6.11/qwebengineurlrequestinterceptor.html)
also checks page requests and request budgets. It uses live policy without
calling engine objects or SQLite from its callback. Denials record reason codes;
denied URL queries are excluded from those events. Broader access requires a new
task/request and user approval. Stop, close, and terminal task transitions remain
available for cleanup without spending operation budget.

Revocation, expiry, and exhausted budgets cancel the task and dispose its pages.
Expiry uses a monotonic deadline, checked on requests/commands and by a Qt timer.
Queued mutations then fail their authority checks. Cancellation cannot undo a
request already sent to a website. Controller approval/denial/revocation methods
are Qt-thread methods deliberately absent from the client command surface.

`tasks.sqlite` has owner-only permissions. It persists objectives, task states,
URLs/titles, grant scopes/decisions, and event metadata; private engine
cookies/storage stay in memory. Schema 2 upgrades the original task database
without replacing its journal.
Task state transitions and their events are transactional. SQL triggers prevent
updating/deleting journal entries. Active tasks become `interrupted` when the
service shuts down or starts after an interruption; pending and approved grants
end too. Pages, commands, and approved authority are never
restored/replayed. An asynchronous journal write failure disables further task
commands and emits `service.failed`; cleanup still disposes engine resources.

Active document execution, script-handled forms, file actions, and watch/takeover
controls remain future work. Existing-profile grants support the same restricted
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

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
```

For headless Linux checks:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python tests/smoke_browser.py
QT_QPA_PLATFORM=offscreen python tests/smoke_google_login.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_active_content.py
python tests/smoke_password_manager.py
```

Tests use temporary profiles and local content. Chromium still needs to initialize its process sandbox; a restricted container may require running the tests outside that container's sandbox.

Qt unittest classes use `tests/qt_test_support.py` to share one QApplication
throughout the process. Register profile fixture directories with
`keep_directory()` so they are removed only after final engine shutdown. Do not
destroy and recreate QApplication between test classes using WebEngine.

The Google compatibility smoke check also needs `openssl`. It uses a temporary loopback HTTPS server, maps the Google account host to that server for this test process, and trusts only its generated certificate key. It never contacts Google or uses an account.

The active-content audit prints JSON with observed requests, STUN packet counts,
script execution results, and `tested_routes_contained`. Exit zero means its
positive controls and scriptless checks completed; it does not approve scripts.
The current forced-script probes report `tested_routes_contained: false`.

The optional password-manager smoke check needs `keepassxc` and `keepassxc-cli`.
It runs offscreen with isolated configuration and a synthetic temporary vault,
seeds a fixture pairing, and checks encrypted saving/retrieval and vault locking.
It does not exercise the interactive pairing dialog or access an existing vault.
The check passed with KeePassXC 2.7.6; normal desktop pairing and real-site
filling still need user verification.

## Verification record

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
- Site permissions are per request and never remembered.

## Planned architecture

The following is the target design. The [internal task foundation](#internal-task-services)
is implemented; full agent features, generic extension support, and bookmarks remain
future work. Implementation order and status live in [ROADMAP.md](ROADMAP.md).

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
gestures and active-page
permissions, file grants, and full SPA/form workflows remain pending.
Website JavaScript remains ungranted: the next stage must cover background
network transports/workers and submit-handler mutations with tested enforcement
before enabling scripts. No JavaScript wrappers alone establish that boundary.
The following describes the complete milestone target.

Prefer authorized app/API connectors, then public document retrieval, then live JS-enabled pages. Visual inspection is optional or user-requested. A session owns QWebEnginePage independently of a view; attach the same page on watch/takeover. Invisible pages may still need JS, layout, and Chromium resources.

Snapshots include URL/title, frame identity, revision, readable content/provenance, and candidate actions with labels/roles/operations/enabled state/constraints. Report limits for inaccessible frames, closed shadow roots, canvas, and ambiguous custom controls.

Use browser-issued opaque target handles tied to session/frame/revision. Reject stale/ambiguous targets. Support navigation, activation, entry, selection, scrolling, waits, authorized uploads, and downloads. Use native input semantics where synthetic JS clicks are insufficient. Return observed changes and evidence, not just dispatch success.

Acceptance: fixtures for static pages/forms/SPAs/frames/DOM replacement work without screenshot loops; unsupported actions request takeover or return explicit limitations.

#### A4. Framework-neutral MCP — pending

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

#### A6. Application connections and recovery — pending

- Browser-mediated mode: MCP gateway enforces grants, logs activity, handles cancellation, and records results.
- Agent-managed mode: direct external connections; clearly disclose absent browser visibility/enforcement.
- User-approved connector setup; secrets in OS credential storage, never styling/exported logs.
- Trusted configuration defines risk; remote tool annotations alone do not. Sending/publishing/deleting/spending requires explicit authority.
- Persist request IDs/receipts, bound read retries, never blindly replay uncertain writes, reconcile external state where possible.
- Report partial results/blockers/uncertainty. Do not replay unfinished writes automatically after restart.
- Acceptance: generic gateway tested against mock document/email MCP servers before real accounts.

#### A7. Collaboration UI and declarative styling — pending

- Task workspace shows agent identity/objective/authority/status/activity/artifacts/evidence.
- Watch attaches a view without taking control. Takeover obtains exclusive ownership and blocks agent mutations. Resume invalidates stale snapshots. Cancel stops queued/new commands; dispatched external effects may require reconciliation rather than rollback.
- Multiple agents have separate identities/tasks; each session has one mutation owner.
- Versioned `appearance.toml` controls supported browser chrome: colors, typography, spacing, component order/visibility/panel placement.
- Component registry and validated layouts, live preview, last-known-good rollback, recovery shortcut. No executable configuration or removal of all safety/recovery access. Website styling is separate later work.
- Acceptance: user watches/interrupts, continues in the same page, then resumes delegation without losing session state; malformed styling rolls back safely.

### End-to-end validation and rollout

#### Research → document → email — pending

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

#### Bookmarks — pending, non-blocking

Per-profile folders/search/editing and HTML import/export. Keep bookmarks separate from task artifacts.

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
