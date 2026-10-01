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
- [KeePassXC setup](#third-party-password-managers)
- [Profiles and stored data](#profiles-and-stored-data)
- [Tabs and groups](#tabs-and-groups)
- [Cookies and global sharing](#cookies-and-global-sharing)
- [Downloads and ad blocking](#downloads-and-ad-blocking)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Engine and storage internals](#engine-and-storage-internals)
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
- Ad Blocking...
- Settings...
- Exit

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
values.

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
├── codec_test.html         # Local media/codec fixture
├── ui/
│   ├── address_bar.py     # Address bar widget
│   ├── tab_bar.py         # Tab bar widget with group context menu
│   ├── cookie_dialog.py   # Cookie viewer
│   ├── download_dialog.py # Download list with pause/resume/cancel/open
│   ├── password_dialog.py # KeePassXC connection, login picker, fill/save actions
│   ├── adblock_dialog.py  # Filtering toggle, exceptions, list updates
│   ├── group_dialog.py    # Tab group management
│   └── settings_dialog.py # Preferences dialog
├── tests/
│   ├── test_browser.py    # Regression suite (needs Qt)
│   ├── test_permissions.py # Permission API compatibility checks
│   ├── test_features.py   # Pure policy/filtering checks
│   ├── test_history.py    # History persistence and private-session checks
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
python tests/smoke_password_manager.py
```

Tests use temporary profiles and local content. Chromium still needs to initialize its process sandbox; a restricted container may require running the tests outside that container's sandbox.

The Google compatibility smoke check also needs `openssl`. It uses a temporary loopback HTTPS server, maps the Google account host to that server for this test process, and trusts only its generated certificate key. It never contacts Google or uses an account.

The optional password-manager smoke check needs `keepassxc` and `keepassxc-cli`.
It runs offscreen with isolated configuration and a synthetic temporary vault,
seeds a fixture pairing, and checks encrypted saving/retrieval and vault locking.
It does not exercise the interactive pairing dialog or access an existing vault.
The check passed with KeePassXC 2.7.6; normal desktop pairing and real-site
filling still need user verification.

## Verification record

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

The final audit's four findings are fixed: fullscreen requests now follow their requested state; cookie inspection retries timeouts, bounds target errors, and ignores obsolete responses; cached filter compilation starts only after its completion handlers and service state are ready; and cosmetic filtering injects document-specific CSS into accessible same-origin frames rather than only the parent page. The manual acceptance gate remains pending in [ROADMAP.md](ROADMAP.md#browser-acceptance-checklist).

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

The following is a design reference for future work. Agent features, generic
extension support, and bookmarks are not implemented. Implementation order and
status live in [ROADMAP.md](ROADMAP.md).

### Agent framework

#### A1. Capability services and task storage — pending

- Incrementally extract profiles, sessions, navigation, downloads, permission handling, and persistence from the UI coordinator.
- Keep engine operations on the Qt thread; dispatch through a command broker.
- Introduce a backend interface for session creation, navigation, inspection, actions, and view attachment; implement Qt only initially.
- Application-owned SQLite stores task records and an append-only event journal; retain existing profile/config compatibility.
- Acceptance: a deterministic test client operates a task session with no AI or external framework.

#### A2. Sessions and grants — pending

- Task-owned pages, grant, artifacts, history, and lifecycle exist independently of visible tabs.
- Default to a separate off-the-record task profile. Explicitly authorized persistent profiles share their existing cookies/storage; disclose that boundary.
- Task-scoped grants cover sites, profiles, app tools, file destinations, data disclosure, external effects, duration, and budgets. Standing permissions supply defaults, never self-expanding authority.
- A proposed task is not authorized until approved by the user. Ask again only when new authority is needed.
- All UI/test/MCP mutations use the same policy checks.
- Deny arbitrary shell, unrestricted filesystem, raw cookie export, and public unrestricted JavaScript evaluation.
- Private-network destinations require explicit permission. Page text, classification, and connector descriptions are untrusted, not authority.
- This is a browser capability boundary, not an OS sandbox for external agents; tools accessed outside it cannot be controlled.
- Acceptance: cross-task/profile/destination/grant attempts fail before controlled execution.

#### A3. Text-first inspection and deterministic actions — pending

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
