# Configuration

Settings, `config.json` keys and where Steed keeps its data. [Documentation index](README.md).

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
**Dark websites** (Settings → View) shows websites dark even when they have no
dark look of their own (Chromium's automatic dark mode); open tabs change when
you save.
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
