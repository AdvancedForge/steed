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

Qt 6.11 persisted-cookie enumeration uses a private in-process inspection adapter; no remote debugging port is opened. Partitioned cookies are omitted from viewing/sharing because Qt's cookie type cannot preserve their identity; bulk clear still operates on the engine store.

### Downloads and Ad Blocking

Open **File → Downloads** (`Ctrl+J`) for progress, cancellation, supported pause/resume, file opening, and history. Incognito downloads are excluded from disk history. Interrupted downloads are not automatically restarted.

Open **File → Ad Blocking** to download/update EasyList and EasyPrivacy, toggle filtering, or add host-name exceptions. Initial bootstrap rules are limited; download the full lists for broader coverage. Reload pages after changing exceptions. Scriptlets and resource redirects are not enabled; unsupported filters are not presented as full uBlock compatibility.

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

## Usage

### Keyboard Shortcuts

- `Ctrl+T`: New tab
- `Ctrl+W`: Close current tab
- `Ctrl+Shift+N`: Open search profile in a separate window (incognito)
- `Ctrl+Shift+0`: Switch the current window to search profile
- `Ctrl+,`: Settings
- `Ctrl+Q`: Close the window

### Menu Options

**File Menu:**

- New Tab
- Close Tab
- Exit

**Profile Menu:**

- Switch Profile: Change to a different profile
- New Profile: Create a new profile
- Search Profile (Incognito): Open incognito mode

**Cookies Menu:**

- View Cookies: Open cookie manager dialog
- Clear All Cookies: Delete all cookies for current profile

Dark mode is available in **File → Settings**.

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
- `global_cookie_sharing_mode`: `on_switch` or `all_profiles`

## Framework 12 Optimizations

High-DPI scaling uses Qt's pass-through rounding policy. Hardware acceleration follows Qt's platform defaults. Saved tabs are restored eagerly.

## Project Structure

```
Browser/
├── main.py                 # Entry point
├── browser_window.py       # Main window UI
├── profile_manager.py      # Profile management
├── tab_manager.py          # Tab save/restore
├── cookie_manager.py       # Cookie handling
├── config.py               # Configuration management
├── utils.py                # Utility functions
├── webengine.py            # Shared engine settings
├── ui/
│   ├── address_bar.py     # Address bar widget
│   ├── tab_bar.py         # Tab bar widget
│   ├── cookie_dialog.py   # Cookie viewer
│   └── settings_dialog.py # Preferences dialog
├── tests/                 # Regression tests and startup smoke check
├── pyproject.toml         # Ruff lint/format configuration
├── requirements-dev.txt
└── requirements.txt
```

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
```

Tests use temporary profiles and local content. Chromium still needs to initialize its process sandbox; a restricted container may require running the tests outside that container's sandbox.

## Existing limitations

- Real Google login and normal-desktop daily use still require manual verification. Google can reject embedded browsers independently of the user-agent override.
- Global-cookie inheritance intentionally preserves unknown/local ownership and excludes partitioned identities; it is not full browser-session synchronization.
- Ad blocking requires downloading full lists for broad coverage and does not enable scriptlets or resource redirects.
- Active profiles must be switched away from in every window before rename/archive. Archived profile data is recoverable under `~/.custom_browser/deleted_profiles/`.
- Tab sessions are saved per profile; the most recently saved window supplies that profile's restored session.

## Future Enhancements

Planned features (not yet implemented):
- Bookmarks management
- Extension system
- External-agent task sessions, scoped permissions, deterministic page tools, MCP connections, and declarative interface styling (see roadmap)

## License

This is a custom browser implementation. Use at your own discretion.
