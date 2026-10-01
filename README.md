# Custom Browser for Framework 12

A Python desktop browser built with PyQt6 and Qt WebEngine, with profile management, tab persistence, and cookie controls. Originally built for a Framework 12 laptop, it uses Qt's normal display scaling and graphics backend selection.

## Features

### Core Features

- **Profile Management**: Multiple isolated browser profiles with separate cookies and data
- **Tab Persistence**: Tabs are automatically saved when closed and restored when reopening a profile
- **Search Profile (Incognito)**: Special profile that doesn't save cookies or browsing data
- **Cookie Management**: Per-profile cookie storage with easy viewing and deletion
- **Dark Mode**: Dark theme for the browser interface
- **Recently Closed Tabs**: Per-profile, memory-only restore history with position/group restoration
- **Tab Groups**: Stable groups with creation, rename, deletion, collapse/expand, and grouped movement
- **Downloads and Permissions**: Progress/history manager, supported pause/resume, cancellation, save prompts, and site permissions
- **Ad Blocking**: Native network rules and supported CSS hiding, list updates, and site exceptions
- **User-Agent Overrides**: Applied at startup and to new profiles; clearing restores the engine default
- **History Suggestions**: Address completion by URL or page title, saved separately for each ordinary profile
- **Refresh Shortcuts**: Ctrl+R and F5 reload the current tab; Ctrl+L selects the address bar
- **KeePassXC Passwords**: In-browser login selection, fill shortcut, and saving through KeePassXC's encrypted local protocol

### Profile System

- Ordinary profiles have separate cookies, tabs, and web storage; application preferences are shared
- Profiles are stored in `~/.custom_browser/profiles/{profile_name}/`
- The `global` profile is stored in `~/.custom_browser/global_profile/`; its cookies form a one-way shared baseline while separate profile logins remain local
- Windows share application-owned engine profiles; a second browser process cannot open the same storage directory
- Search profile acts as incognito mode (no persistence)
- Easy profile switching without losing current session

### Tab Management

- Multiple tabs with tab bar interface
- Tabs are saved before closing
- Tabs are restored when reopening a profile
- Tab state includes URL, title, and stable tab/group identities
- `Ctrl+Shift+T` restores recently closed tabs for the current profile (up to 50, not saved across restart)

### Cookie Control

- View all cookies for current profile
- Delete selected cookies, selected sites, or all cookies through the viewer/menu
- Filter cookies by domain or name
- Search profile has no cookie persistence

Existing profiles are migrated conservatively: pre-existing cookies remain local rather than being overwritten by the global profile. **Cookies → Use Global Cookies for This Site** explicitly resets the current site's baseline, after confirmation. Local changes/deletions prevent automatic reinheritance. Cookie sharing does not copy local storage or guarantee a complete signed-in session.

Qt 6.11 persisted-cookie enumeration uses a private in-process inspection adapter; no remote debugging port is opened. Partitioned cookies are omitted from viewing/sharing because Qt's cookie type cannot preserve their identity; bulk clear still operates on the engine store. Because that adapter depends on undocumented Chromium internals, a Qt upgrade can break it — `cookie_snapshot.VERIFIED_AGAINST_QT` records the version it was last confirmed against, and the regression suite fails if enumeration stops working.

### Downloads and Ad Blocking

Open **File → Downloads** (`Ctrl+J`) for progress, cancellation, supported pause/resume, file opening, and history. Incognito downloads are excluded from disk history. Interrupted downloads are not automatically restarted.

Open **File → Ad Blocking** to download/update EasyList and EasyPrivacy, toggle filtering, or add host-name exceptions. Only a small bootstrap rule set ships with the browser; download the full lists for broader coverage. Rule compilation runs off the GUI thread, and a failed update keeps the last working rules. Network blocking applies immediately, while cosmetic (CSS) hiding is re-applied on page load — reload pages after changing exceptions. Same-origin frames, including nested frames and `srcdoc`, receive their own CSS rules; each pass covers up to 32 documents and eight frame levels. Cross-origin frames cannot be inspected. Scriptlets and resource redirects are not enabled, and unsupported filters are not presented as full uBlock compatibility.

The full browser-first and agent-framework plan, current status, and manual acceptance checklist are in [ROADMAP.md](ROADMAP.md). Agent implementation has not started.

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
python main.py
```

You can also run `.venv/bin/python main.py` directly. Running `python3 main.py` without activating the environment may use a different PyQt installation.

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

## Framework 12 Optimizations

High-DPI scaling uses Qt's pass-through rounding policy. Hardware acceleration follows Qt's platform defaults. Saved tabs are restored eagerly.

## Project Structure

```
Browser/
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

## Technical Details

- **GUI Framework**: PyQt6
- **Browser Engine**: PyQtWebEngine (Chromium-based)
- **Storage**: JSON files for configuration, SQLite for cookies (via Qt)
- **Profiles**: Isolated storage directories per profile

JSON preferences and tab files are replaced atomically. Settings are saved when you click Save in the preferences dialog.

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

## Existing limitations

- Real Google login and normal-desktop daily use still require manual verification. Google can reject embedded browsers independently of the user-agent override.
- Global-cookie inheritance intentionally preserves unknown/local ownership and excludes partitioned identities; it is not full browser-session synchronization. With `global_cookie_sharing_apply_on_startup` disabled, sharing stays inactive until you switch profile or opt a site back in.
- Ad blocking requires downloading full lists for broad coverage and does not enable scriptlets or resource redirects.
- Active profiles must be switched away from in every window before rename/archive. Archived profile data is recoverable under `~/.custom_browser/deleted_profiles/`.
- Tab sessions are saved per profile; the most recently saved window supplies that profile's restored session.
- A window always keeps at least one tab, so the final tab cannot be closed or restored.
- Site permissions are per request and never remembered.

## Future Enhancements

Planned features (not yet implemented):
- Bookmarks management
- Extension system
- External-agent task sessions, scoped permissions, deterministic page tools, MCP connections, and declarative interface styling (see roadmap)

## License

This is a custom browser implementation. Use at your own discretion.
