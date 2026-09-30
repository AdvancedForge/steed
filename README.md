# Custom Browser for Framework 12

A custom Python browser built with PyQt6 and PyQtWebEngine, optimized for Framework 12 laptops with profile management, tab persistence, and cookie control.

## Features

### Core Features
- **Profile Management**: Multiple isolated browser profiles with separate cookies and data
- **Tab Persistence**: Tabs are automatically saved when closed and restored when reopening a profile
- **Search Profile (Incognito)**: Special profile that doesn't save cookies or browsing data
- **Cookie Management**: Per-profile cookie storage with easy viewing and deletion
- **Dark Mode**: System-wide dark theme support
- **Framework 12 Optimizations**: Optimized for high-DPI displays, touchscreen support, and battery efficiency

### Profile System
- Each profile has isolated storage for cookies, tabs, and preferences
- Profiles are stored in `~/.custom_browser/profiles/{profile_name}/`
- Search profile acts as incognito mode (no persistence)
- Easy profile switching without losing current session

### Tab Management
- Multiple tabs with tab bar interface
- Tabs are saved before closing
- Tabs are restored when reopening a profile
- Tab state includes URL, title, and scroll position

### Cookie Control
- View all cookies for current profile
- Delete individual cookies or all cookies
- Filter cookies by domain or name
- Search profile has no cookie persistence

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the browser:
```bash
python main.py
```

## Usage

### Keyboard Shortcuts
- `Ctrl+T`: New tab
- `Ctrl+W`: Close current tab
- `Ctrl+Shift+N`: Open search profile (incognito)
- `Ctrl+Q`: Quit browser

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

**View Menu:**
- Dark Mode: Toggle dark theme

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
- Clears all data when closed

Access it via **Profile → Search Profile (Incognito)** or `Ctrl+Shift+N`.

## Configuration

Configuration is stored in `~/.custom_browser/config.json`. You can edit this file directly or use the browser's settings interface.

### Configuration Options
- `default_profile`: Default profile to use on startup
- `home_page`: URL to load for home button
- `search_engine`: Search engine URL (with `{query}` placeholder)
- `dark_mode`: Enable dark mode by default
- `restore_tabs_on_startup`: Restore tabs when opening a profile
- `window_width`: Default window width
- `window_height`: Default window height

## Framework 12 Optimizations

The browser is optimized for Framework 12 laptops:
- **High DPI Support**: Proper scaling for 1920x1200 display
- **Touchscreen**: Larger touch targets (minimum 44x44px)
- **Battery Efficiency**: Optimized rendering and lazy tab loading
- **Performance**: Hardware acceleration enabled
- **Memory**: Efficient memory management for systems with 12GB+ RAM

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
├── ui/
│   ├── address_bar.py     # Address bar widget
│   ├── tab_bar.py         # Tab bar widget
│   └── cookie_dialog.py   # Cookie viewer/editor
└── requirements.txt
```

## Technical Details

- **GUI Framework**: PyQt6
- **Browser Engine**: PyQtWebEngine (Chromium-based)
- **Storage**: JSON files for configuration, SQLite for cookies (via Qt)
- **Profiles**: Isolated storage directories per profile

## Future Enhancements

Planned features (not yet implemented):
- Bookmarks management
- Download manager
- Ad blocking
- Extension system

## License

This is a custom browser implementation. Use at your own discretion.
