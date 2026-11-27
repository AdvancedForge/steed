# Using Steed

Everyday browsing: the welcome tour, New Tab and bookmarks, tabs, profiles, passwords, cookies, downloads and the rest. [Documentation index](README.md).

## Welcome, New Tab and bookmarks

Fresh installations start in **Global**, with no automatically created Default
profile. Existing Default data remains an ordinary profile you can rename or
archive through Profile; it is not deleted. Older Default startup selections
become Global, while other explicitly selected profiles are preserved.

`steed:welcome` is a short tour in four steps, one idea each: how Steed looks
(a photo or colour background, marked **Chosen**, and a **Liquid glass**
switch; both apply as you choose), three quick questions, bringing
your bookmarks, history and passwords from another browser, and bringing an
assistant, or not: Steed works fully without one, and five cards (on this
computer, with your API key, a coding agent, a chatbot website, a cloud agent)
open each setup directly, noting any coding agent already installed. Every
step can be skipped; **Start browsing** (or *Skip the tour*)
completes it, and Settings → Everything → Welcome takes it again.

The questions are optional, and each answer sets a few defaults at once and
says which under it (all can be changed later):

| Question | Answer | Sets |
|---|---|---|
| How often do you use AI? | Every day | Agents menu first, New Tab errands, agent notifications |
| | Now and then | Agents menu in its place, New Tab errands, agent notifications |
| | Rarely or never | No New Tab errands, no agent notifications |
| More features, or lighter on memory? | Everything on | Memory: Performance, every effect, tab previews |
| | Balanced | Memory: Balanced, every effect, tab previews |
| | Light on memory | Memory: Memory saver, fewer effects, no tab previews |
| How should Steed tell you things? | On my desktop / Only inside Steed / Only when it needs me / Not at all | [Notifications](#notifications) |

The answers are kept in `config.json` as `welcome_answers`. After that,
Steed teaches as you go: the first time you reach the address bar, a website,
a New Tab or the agent sidebar, a small tip says one useful thing, once
("Got it", or it fades after a while; never twice, never over the tour).

Steed starts calm: in the New Tab's ☰ menu, pointing at a section (General,
Tabs…) opens its panel, while pages (Settings, Style, Customize) wait for a
click; Customize New Tab shows the background as three pictures (photo, colour,
your image) and what goes with the one chosen, with the rest under *More
options*; the agent sidebar shows one way to start (others under *Other ways
to start*); Settings and Steed's full pages show each explanation as one
quiet line that opens when you point at the setting, and Settings shows its
Save bar only when something changed. **Settings → Everything** lists every
Steed page, grouped (browsing, privacy and security, look, agents, more),
each a click away.
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
the **App Panel** beside the page (**View → Appearance → App Panel**). Each is one HTML page
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

**Bookmarks bar.** View → **Bookmarks Bar** (`Ctrl+Shift+B`) shows your
bookmarks under the toolbar in every window, in the order you keep them; a
folder is one button that opens its bookmarks, and what does not fit goes
under **»**. Click opens in the current tab, middle-click or `Ctrl`+click in a
new one. Off until you turn it on; incognito windows show none.
Press **Ctrl+Shift+O** to open the manager directly.
Type in its search field and press **Enter** to open the first matching bookmark;
the current folder filter applies. If nothing matches, the manager stays open.
To choose a different match, focus the results list, select it with the arrow
keys, and press **Enter** (keypad Enter also works) to open the selected bookmark.
Select a bookmark and click **Open**, or double-click it, to open a new tab in
the current profile. Use **Edit…** to change its details. If the profile has
changed while the manager was open, reopen the manager before opening a link.

**Import HTML…** merges a UTF-8 browser bookmark export (up to 8 MiB and 10,000
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

Agents can also propose **changes to Steed's settings** with
`steed_settings_capabilities`, `steed_settings_propose` and
`steed_settings_status`: preferences, keyboard shortcuts, search keywords,
your own ad-blocking rules and sites, and per-site settings (zoom, JavaScript,
autoplay, pop-ups), in one document with a one-line summary. A popup lists
every change as **now → after**, asks which profile per-site settings belong
to, and changes nothing until you press **Apply**. Settings that affect
security, privacy or agents (safe browsing, HTTPS-first, fingerprinting,
tracking removal, what Steed remembers, cookie sharing, the user agent, agent
settings) cannot be proposed; capabilities lists what can, with the current
preferences and every menu command's shortcut so a proposal can be precise.

Ask an agent for a page or an automation ("make me a dashboard of my price
watches", "every Friday, check these three shops and tell me what changed")
and it can propose one with `steed_creation_capabilities`,
`steed_custom_page_propose`, `steed_automation_propose` and
`steed_creation_status`. The popup shows a page's whole HTML and JavaScript,
or an automation's trigger and every step in order, and saves nothing until
you press **Add**; it goes to the profile of the window you review it in.
Proposals are checked exactly like what you make yourself (reserved names,
web addresses only, known steps), at most three wait per task, and they grant
no access: what a page's scripts may do still follows Access → Local pages.

Agents can **suggest a watch** when you ask for one in a chat ("tell me when
tickets go on sale") with `steed_watch_capabilities`, `steed_watch_propose`
(`url`, optional `title`, `instruction`, `every` of `15m`/`hourly`/`6h`/
`daily`, and `then`) and `steed_watch_status`. Only `http(s)` pages are
accepted. A review popup shows the page and lets you change what to tell you
about, how often and the Then… action, pick the profile whose sign-ins it uses,
and choose **Add watch**, **Decline** or **Later**. Only you can let a Then…
action start without asking (automatic mode adds watches that ask first).
Nothing is watched before you add it, and a proposal reads nothing from the
browser. Pending suggestions are in **Task Permissions → Pages & Scripts**.

Steed registers a host-based local URL scheme before application startup, following
the [Qt URL-scheme documentation](https://doc.qt.io/qt-6/qwebengineurlscheme.html).
Canonical engine URLs use `steed://newtab/` and `steed://welcome/` for reload and
session restore; the user-facing spelling remains `steed:newtab`/`steed:welcome`.
There is no general-purpose HTML app capability bridge in this milestone.

## Usage

### Keyboard Shortcuts

- `Ctrl+T`: New tab
- `Ctrl+N`: New window (of the current profile); `Ctrl+Shift+N`: incognito window
- `Ctrl+P`: Print the page (a page's own Print button opens the same dialog)
- `F11`: Full screen
- `Ctrl+U`: The page's source, in a new tab
- `Ctrl+;`: Follow a link with the keyboard (letters appear on everything you
  can click; type one); `Ctrl+Shift+;` opens the chosen link in a new tab
- `Ctrl+Shift+U`: Read the selection or the article aloud (again: stop)
- `Ctrl+Shift+F`: Search what you've read

Every shortcut can be changed: **General → Keyboard Shortcuts…** lists every
command with its shortcut; **Change…** (press the new keys), **No shortcut**,
**Steed's own** and **Reset all**. A shortcut given to one command is taken
from the command that had it (the page says which). Changes apply to every
window and are kept in `config.json` (`shortcuts`).
- `Ctrl+W`: Close current tab; closing a window's last tab closes the window (and
  quits Steed if it was the last one, keeping its tabs for next start)
- `Alt+Left` / `Alt+Right`, or the mouse's back/forward side buttons: Back / Forward
- `Ctrl+Shift+T`: Restore the most recently closed tab for this profile
- `Ctrl+Shift+N`: Open search profile in a separate window (incognito)
- `Ctrl+Alt+N`: Make a new profile and open it in a separate window
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
- `Ctrl+Shift+Space`: Dictate — tap to start and again to finish, or hold while
  talking; `Escape` cancels ([Dictation](#dictation))
- `Ctrl+,`: Settings
- `Ctrl+Shift+W`: Close the window
- `Ctrl+Q`: Quit Steed (every window comes back at the next start)

### Menu Options

The menu bar has six categories, in this order:

- **General:** Passwords, Fill Login, Downloads, Ad Blocking, Website Permissions,
  Settings and Exit.
- **Profile:** Create, switch, pop out, rename or delete profiles, open private
  browsing and use Quick Switch shortcuts. **Open in New Window** opens any
  profile in a window of its own while this one keeps its profile, and **New
  Profile in New Window…** (`Ctrl+Alt+N`) makes a profile and opens it there.
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
  (View → Keyboard Navigation → Search History…). Ctrl+Tab / Ctrl+Shift+Tab and
  Ctrl+PgDn / Ctrl+PgUp move to the next or previous tab, Ctrl+1–8 open that tab
  and Ctrl+9 the last one; tabs hidden in a collapsed group are skipped.
- **View:** Go to Address Bar, Find in Page, Find Next, Find Previous, Zoom In,
  Zoom Out and Reset Zoom. While you edit the address bar, the page changing its
  URL no longer replaces your text or its undo/redo history (Ctrl+Z, Ctrl+Shift+Z
  or Ctrl+Y); Escape restores the page's address.
  **View → Advanced → Developer Tools** opens Chromium's DevTools for the current tab, like
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

Use **General → More Tools → Website Permissions** to forget a choice and make future requests
ask again. Forgetting does not promise to terminate an existing capture; close
the tab using your camera, microphone or screen to stop it. Website approvals
never authorize agents: task pages continue to deny engine permissions.

Regular choices live in owner-only `~/.custom_browser/site_permissions.json`.
On supported Qt versions Steed uses `AskEveryTime` so native persisted choices
cannot bypass these controls; older native approvals will prompt again. Read or
write failures deny access rather than granting an unsaved remembered permission.

### User scripts and styles (`steed:userscripts`)

**General → More Tools → User Scripts & Styles…** or `steed:userscripts` keeps your own CSS
and JavaScript for chosen sites in this profile (`<profile>/user_scripts.json`,
up to 50 entries). Each has a name, its sites (one per line or comma separated,
such as `example.org`; a site includes its subdomains, `*.example.org` means
the same, and `*` means every site), CSS, JavaScript, when the script runs (as
the page starts, when it is ready, or after it has loaded) and an on/off
switch. They also run in frames from the listed sites.

**Commands.** Set a script to run **When I choose it** and it becomes a
command of your own instead: it never runs by itself, and appears in **View →
Your Commands** and the command palette, where choosing it runs it on the
current page (only if that page is one of its sites). Like any menu command it
can take a keyboard shortcut. Use one for something you do often: copy a page
as Markdown, tidy a cluttered site, fill a form the same way every time.

Styles are added from Steed's own script world as soon as the document exists,
so pages do not flash unstyled. Scripts run in the page's world, like
userscripts, so they can use the site's own code; an error is reported in the
page's console (`Steed user script <name>`) without stopping the page. Scripts
have the page's permissions and none of Steed's data. Changes apply to pages
loaded or reloaded afterwards. Incognito windows run none.

### The new tab page

By default a new tab shows just **Steed** and an empty search box over a photo
that changes every six hours: free Unsplash photos from Lorem Picsum, with the
photographer credited at the bottom left. When Picsum does not answer (it was
down for hours in November 2026), Steed asks Wikimedia Commons for one of its
featured landscape photos instead, credited with the photographer and its
licence, and goes there first for the next half hour. If neither answers,
*New photo now* shows one of your last 12 photos (kept in
`~/.custom_browser/newtab/recent/`) and says so. A new photo (and the colours
taken from it) changes open New Tabs in place, cross-fading; they are never
reloaded for it, so what you typed or opened stays. Steed reloads its own
pages only when their layout or settings change, once per burst of changes,
never a tab still loading (it waits) or one that began loading after the
change, and at most four times in ten seconds per tab; if that limit is ever
reached it stops and writes what asked to `~/.custom_browser/reload_loops.log`.
Closing a tab reloads nothing unless a page lists recently closed tabs. Steed keeps the current photo in
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
- **Errands under the search box** (shown): three one-click starts for your
  assistant. **Watch a price** and **Fill a form from my notes** open the
  sidebar with a draft that says what to fill in; **Summarise my tabs** opens
  the Compare dialog with your web tabs ticked (up to six; untick any you'd
  rather keep) and sends their text with a request to summarise each. Not in
  incognito.

- **Settings button**: the gear as its own button, or *In the ☰ menu*, where
  Customize New Tab is the first row of the menu and opens the same panel in
  place (the gear stays when the New Tab ☰ menu is turned off in Style).

**More options…** (also General → Customize New Tab…) opens the full dialog
with the classic sections, a feed, a custom colour and your own HTML.

### Your own steed: pages

**View → Your Pages…** (the `steed:pages` tab) makes pages of your own, opened like Steed's as
`steed:<name>` in any tab: a dashboard of your watches and routines, a
launcher for your skills, a board of links, a small tool. Start from a
template (a dashboard, a skills launcher, a board of what changed on pages you watch, your bookmarks as tiles, or a blank page), write the HTML and
JavaScript yourself, or describe it and press **Write it for me** to have your
assistant draft it. Type its address to open it, or set it as your New Tab in
Settings (New tab page: `steed:dashboard`). Names are lowercase letters,
digits and dashes; Steed's own page names stay Steed's.

A page's scripts reach Steed with `fetch("/api/…")`, each capability allowed in
**Access & Permissions → Local pages** (never by default, ask, or always). While a
capability is still at its default, a page of yours that asks for it shows a
bar above it: **Allow** gives that one page that capability (kept with the
page, never in a pack), **Not now** leaves it for this session. Your Pages
lists what each page may do; **Take Back Access** clears it. Once you set a
capability in Access yourself, that setting is the answer:

| Endpoint | What it gives or does | Capability |
| --- | --- | --- |
| `fetch?url=…` | a web address, without cookies | Load data from the web |
| `tabs`, `history`, `bookmarks` | titles and addresses | See tabs, history, bookmarks |
| `watches`, `routines`, `skills` | what you watch (and what changed), your routines, your skills | See your watches, routines and skills |
| `run-skill?id=…`, `run-routine?id=…`, `open?url=…`, `notify?title=…&text=…` | run one of yours, open a web page, notify you | Run your skills and routines, open tabs and notify you |

Refusals come back as JSON with `"status": 403`. Pages keep their own
`localStorage`. They run under a strict content policy: their own inline
scripts and styles, images from the web, no other scripts. Kept in the
profile's start-page data; not in incognito.

### Packs: sharing pages, skills and automations

**View → Packs…** (the `steed:packs` tab) puts any of your pages, skills and automations in one
`.steedpack` file, to keep or give someone. A pack holds exactly what each
item is (a page's HTML, a skill's ask and saved steps, an automation's trigger
and steps, with their web addresses and text) and nothing else: no logins,
history, settings or run records. Items whose saved steps type text are marked
so you can check that text before sharing. An automation started by one of
your watches goes in as run-by-hand, since watches are yours.

**Add a pack…** reads a pack with the same checks as anything you make
(items that fail are listed and left out), shows every item in full, and adds
only what you leave ticked. Automations arrive paused; resume them in Agents →
Automations. A page or skill whose name you already use comes in renamed
(`board-2`, `Price check (2)`), an identical skill is skipped, and automation
steps that run a renamed skill follow it.

### Workspaces

**Tabs → Workspaces** keeps what you are working on (Work, Trip, Taxes) apart
inside one profile: switching puts this window's web tabs away under the
current workspace and opens the other one's, each loading when you first look
at it. A profile still holds your sign-ins, cookies and history; workspaces
only sort tabs. **Switch Workspace…** (`Ctrl+Alt+W`) lets you type to find
one among many, or type a new name to make it; the window title shows the
workspace when it is not Main, and once you have more than one, its name sits
before the address field (click it for the Workspaces menu). Deleting a
workspace moves its open tabs to Main. Kept in the profile's
`workspaces.json`; not in incognito.

**Keep This Tab in Every Workspace** leaves a tab (your mail, your music) open
whichever workspace you switch to. **Agents in This Workspace** sets what
agents may do in it: **As My Settings Say**, **Ask Me About Everything**
(every request from a chat started there waits for you, whatever your access
settings, saved approvals or auto-approval say) or **Not Allowed** (no chats
start there and no tabs are handed to an agent).

### Help me write

Right-click a text field → **Help Me Write…**: Steed shows what goes to your
assistant (the field's text, or the part you selected, and the page's title),
you pick a goal (Polish, Shorter, Longer, Friendlier, More formal, Fix
spelling) or say what you want, and you see the result, which you can edit,
before **Insert** puts it in the field. Nothing is submitted, and password and
card fields are never offered. It uses the assistant you chose (a model you
added, else Claude Code or Codex).

### Your week in topics

**Tabs → More Tab Tools → Group My Recent History into Topics…** sends the titles and sites of
the past week's pages (never page text; no banking, health or sign-in sites;
nothing from incognito) to your assistant, which groups them into topics such
as "Planning a trip to Lisbon". Reopen a topic's pages, or ask your agent to
help you pick up where you left off. Nothing is kept; run it whenever you like.

**Find a Page I Saw…** (in the History menu, and **Describe it instead…** on
the History page) finds a page from a description, such as "that heat pump
article last week". Your assistant gets the titles, sites and dates of the
past month's pages under the same rules (no page text, no banking, health or
sign-in sites, nothing from incognito) and picks up to five matches; open the
one you meant. Nothing is kept. With **Remember what I read** on, the
description's words (without fillers such as "that article last week") are
also looked up in the text of pages you read, on this computer; those
matches show at once and nothing more is sent.

**Find with** in the same window chooses how: **Your assistant**, as above, or
**Your embeddings service**, which searches by meaning without an assistant.
Give the address of any service that answers OpenAI's `/v1/embeddings`
request (Ollama at `http://localhost:11434/v1`, LM Studio, llama.cpp's server
or LocalAI on this computer, or a hosted one over https) and a model, such as
`nomic-embed-text` or `bge-small`; a hosted service's key goes in the desktop
keyring. Steed sends your description and each page's title and site (with
the start of its text when Remember what I read is on, under the same
site rules) and ranks the pages itself. Each page is embedded once; the
vectors stay in `embeddings.sqlite` in Steed's data folder. In a test on 487
real pages, a small model (bge-small) put the page someone described first
93% of the time, against 80% for keyword search.

### Split view and sites in a profile

**View → Split View with Next Tab**, or **Show Beside Current Tab** in a tab's
right-click menu, puts a tab beside the current one;
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
  Link removes) are dropped from its address. **Tracking rules** picks how:
  Steed's short list (the default and the most cautious: the same few
  parameters on every site), **ClearURLs** (the ClearURLs project's rules,
  bundled with Steed under the LGPL-3.0: hundreds of site rules such as
  Amazon's `/ref=…` and YouTube's `si=`, and tracking redirects like
  `google.com/url?q=…` go straight to where they lead; sites a rule would break
  are its exceptions), or **your own rules file** in the same format. **Also
  remove these parameters** adds names of your own to any of them. The same
  rules clean Copy Clean Link and Send to Your Phone. In a test of 24 shared
  links from big sites, ClearURLs removed 55 of 70 trackers and Steed's list
  10, and neither removed a parameter the page needed.
- **Cookie banners** (Reject): Steed answers sites' "accept cookies?"
  banners for you. **Reject** declines everything optional, **Accept** agrees,
  **Leave them for me to answer** does nothing. **On your agents' own pages**
  can differ (the same as yours by default), so an agent's pages can clear
  banners while you answer your own; agents working in your tabs follow your
  choice. **Leave these sites alone** takes site names (and their
  subdomains), and **Your own rules file** adds rules in autoconsent's JSON
  format (a list, or `{"autoconsent": [...]}`) to the built-in ones. It uses
  DuckDuckGo's [autoconsent](https://github.com/duckduckgo/autoconsent)
  (bundled under the MPL-2.0), which knows about 1,600 consent tools and sites
  and recognises unknown banners by their buttons. It runs in a script world
  of its own in each web page's top frame, which the page can't see; banners
  shown only inside an embedded frame are handled when a rule for the page
  covers them. The status bar says when a banner was declined, accepted or
  hidden. In a test on 14 news and shopping sites, it declined the banner on
  6 of the 7 that showed one.
- **HTTPS first** (on): http:// pages load over HTTPS. When a site has no
  HTTPS, Steed loads it unencrypted after all (the status bar says so) and
  remembers the site until it quits. Local addresses, IP addresses and
  addresses with a port are left alone.
- **Secure DNS** (off, Settings → General): looks up website addresses over
  DNS over HTTPS, with Cloudflare, Google, Quad9, Mullvad or any https://
  server (Other…). **On, with your computer's DNS if it fails** falls back when
  the provider can't be reached; **On only** never does, so sites stop loading
  instead. It applies at once to every profile, incognito included. (Qt
  WebEngine has the setting, but PyQt6 doesn't expose it, so
  `browser/webengine.py` calls Qt's own function.)
- **Warn about dangerous sites** (on, Settings → General): pages on public
  lists of phishing sites (OpenPhish, PhishTank and others) and sites that have
  recently served malware (abuse.ch URLhaus), from the
  [malware-filter](https://gitlab.com/malware-filter) project, are stopped with
  a warning page: **Go back**, or **Visit this site anyway** (until Steed quits).
  Downloads from them are refused. The lists are downloaded whole about twice a
  day into `~/.custom_browser/safe_browsing/`, so checking a page sends nothing
  anywhere. This is not Google Safe Browsing: new phishing sites can take hours
  to be listed.
- **Resist fingerprinting** (off): pages see common hardware values (4 cores,
  8 GB, no plugins), a generic graphics card name, and canvas images with tiny
  changes (stable for a site until Steed quits), so they cannot recognize this
  computer as easily. Some sites may misbehave; it applies to pages loaded
  afterwards.

The site menu's **Forget this site when its tabs close** deletes the site's
cookies (it and its subdomains) when its last tab in the profile closes or
moves to another site, along with everything else it stores.

### Notifications

Agents replying or needing you, watches, routines, automations and your own
pages can notify you. Settings → General → **Notifications** chooses where:
**On my desktop** (the default), **Only inside Steed** (a note in the
top-right corner of the window you used last; it waits until you've had the
window in front for ten seconds, and clicking it opens what it is about), or
**None**. **How often**: **Every time**, **At most one every 15 minutes** or
**At most one an hour** (what comes in between is gathered into one: "3
updates from Steed"), or **Only when something needs me**. Something that
needs you (an approval, a page an agent handed you, a watch or routine that
needs you to sign in, agents past their cost limit) always comes at once.

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

### Read aloud

**View → Read Aloud** (`Ctrl+Shift+U`) reads the selection, or else the page's
article (as reader view finds it), sentence by sentence. A bar at the bottom
shows the sentence being read, with **Pause**, **Slower**, **Faster** (kept
for next time), **Voice** and **Stop**.

**View → Media → Read Aloud Voice…** (also **Voice** on the bar, and Settings → Everything) chooses who
reads:

- **The desktop's voices** (the default): speech-dispatcher, with any of its
  voices; add more with your system's packages (espeak-ng, RHVoice, a Piper
  module). Nothing leaves the computer.
- **Your speech service**: anything that answers OpenAI's `/v1/audio/speech`
  request, such as Kokoro-FastAPI (`http://localhost:8880/v1`), LocalAI or
  openedai-speech on this computer, or a hosted service over https. Give the
  model and, if you like, a voice (blank lets the service choose); a hosted
  service's key goes in the desktop keyring. The text is sent a sentence or so
  at a time, and the next one is asked for while one plays. Slower and Faster
  become the service's speed. Private windows always use the desktop's voices.

**Listen** plays a sample with what you chose.

### Site apps

**Tabs → Site Apps → Install This Site as an App…** puts the site in your applications
menu, with its icon, and opens it in its own window: no tab strip or address
bar, just the site. Links to other sites, and windows or tabs it opens, go to
a normal Steed window; the app window is not part of saved sessions. Starting
it from the menu runs `steed --app=<address>` (into the running Steed if there
is one). **Tabs → Site Apps → Site Apps…** lists them to open or remove (removing takes
the menu entry away). Kept in `site_apps.json`; menu entries are
`steed-app-*.desktop` in `~/.local/share/applications/`.

### Search what you've read

With **Remember what I read** on (Settings → General; off by default), Steed
keeps the text of web pages you finish loading in a full-text index on this
computer (`read_pages.sqlite` beside the profile's history, the newest 5,000
pages). Never kept: incognito pages, pages with a password field (sign-ins),
and sites you list under **Never remember these sites**. **History → Search
What You've Read…** (`Ctrl+Shift+F`) finds pages by any words in them, with
where the words are and when you read it; **Open**, or **Ask Agent About
These**, which drafts a message to your agent with the pages' text (you send
it). Clearing browsing history (all of it or a time range) clears this too.

Your agent can search it too (`steed_reading_search` and
`steed_reading_status`), as Agents → Access & Permissions → **Search what
you've read** allows: **Ask** (the default) shows you the words it wants to
search for, with **Allow**, **Deny** and **Always allow searches**; **Always**
answers at once; **Never** refuses. An answer has the matching pages' titles,
addresses, when you read them, a snippet and up to 3,000 characters of each;
it is kept in memory only, not in the task's records. Nothing is searchable
unless Remember what I read is on, and never in incognito.

### Saving pages

**General → Print…** (Ctrl+P) opens the system print dialog (printers, page
range, and Print to File for a PDF); a page's own Print button opens the same
dialog. Right-click a selection → **Search … for "…"** searches it with your
search engine in a tab beside the page; **Open Link in New Window** opens a real
window.

**View → Save Page As…** (Ctrl+S) saves the page as one file (`.mhtml`, with
its images and styles, opened again by Steed or Chrome), as HTML with a folder
of its files, or as PDF. Screenshots are under View → Take Screenshot.

### When a tab crashes

When a page's process ends unexpectedly (a crash, or the system stopping it,
for example when memory runs out), its tab shows **This tab crashed** with
**Reload** and **Crash Log…**. **General → Help → Crash Log…** lists the last 200
crashes: when, which site (only the host; "incognito" for private windows) and
why, with no page content; **Clear** empties it.

Steed's own errors don't close Steed: an error in one part (a menu, a bar, a
page tool) is printed and kept in `~/.custom_browser/errors.log` (the newest
256 KB, readable only by you, tracebacks without page content), and the rest
keeps working. Without this, PyQt ends the whole program on such an error.
While an agent has a page, the guard that keeps your clicks off it fails
closed: if its check goes wrong, input to that page is blocked, not let
through. The test suite reports such errors too, and fails the run at the end
if there were any.

### Lumen: Steed's look

Steed's look is built on light. The page floats as a rounded card; around
it, behind the tabs and toolbar, is a soft field of light in the browser's
colours. The bars have no backgrounds or borders of their own: the buttons
and a capsule address field rest on the light.

- **The light follows the site.** It takes the colour the site declares
  (its `theme-color`), or else its icon's (on Steed's own pages, your accent
  turned a little), and flows from one colour to the next as you switch
  tabs. With a New Tab photo, everything starts from the photo's colours.
- **Cards.** The page is a card; in split view each side is one (the side
  you are on has a thin rim of the accent's light), and docked panels such as
  the agent sidebar are cards beside it.
- **Menus and buttons.** The browser menus keep related items together, with
  icons for the ones people look for (a tick takes the icon's place on items
  that are on). In dialogs, the main action is filled in the accent; Cancel,
  Later and Decline stay quiet and removals are marked in red.
- **Tab icons.** Steed's own pages have their own icons (New Tab, Welcome,
  Settings, History, Downloads, Passwords…); sites without an icon show a
  quiet globe.
- **Motion that answers you.** The current tab sits in a lit capsule that
  glides to the tab you pick; new tabs grow into place; switching tabs
  cross-fades the pages; toolbar buttons fade a capsule in under the
  pointer; the address field glows in the accent colour while you type;
  menus are frosted, rounded panels that rise into place.
- **Steed's typeface.** Mona Sans (SIL Open Font License, bundled in
  `ui/assets/fonts/`) for everything, with its wide cuts for titles and the
  New Tab's name. Steed adds the folder to its own font setup only; nothing is
  installed on your system.
- **Steed's pages.** Settings, the management pages, Welcome and the New Tab
  share the type, lit cards that catch a soft light under the pointer, round
  buttons and springy switches, titles that settle as you scroll, and one
  entrance as a page opens.

**Style → Light and page** changes the look itself: the light's strength
and whether it follows sites, the page's corners, the space around it and
its shadow, the typeface (Mona Sans or your desktop's) and how wide titles
are. Your agent can change all of it too, with your review: a style
proposal's `lumen` part (strength, fixed light colours, card, type — see
the style guide agents are given) for what Steed draws itself, and its
`css` for everything drawn in HTML (tabs, menus, Steed's pages), using the
documented selectors and tokens such as `--steed-font`, `--stride` and
`#lit`.

**Settings → View → Motion and light** sets how much of this runs:
*Full* (everything above), *Balanced* (the same look; colours fade but
nothing cross-fades, so no snapshot is kept), or *Minimal* (flat colours,
no motion, nothing extra in memory). Your desktop's reduce-motion setting
is always followed. With liquid glass the page
card floats over the frosted photo instead of the light, so bars, page edges
and the New Tab are one picture. Compact mode and fullscreen video are edge
to edge.

**Light or dark.** At the top of Style, **Theme** (Follow my system, Light or
Dark) applies at once to the browser and Steed's pages. Your own colours and
CSS, and the browser colours one by one, are folded away below it as
advanced.

**One theme by default.** Everything takes its colours from one place: the
New Tab photo (with *Match the background*), or else the browser colours set
in Style, or else Steed's own. The bars, the light, menus, the New Tab and
Steed's pages (Settings, History, Downloads…) all follow it — *Style → Steed's
pages match the browser's colours* is on for everyone (it replaced the older
"pages take their colours from the photo"). Colours you choose by hand for
pages or the browser still win, and the matching can be turned off.

Whatever the photo, what is worked out from it stays readable. Light pages
take a pale wash of the photo's colour; dark pages and the dark toolbar take
its hue at a fixed depth, so a bright or pale photo never makes bright cards
under light text or a murky bar. The accent is a clear version of the photo's
hue, moved lighter or darker only as far as needed. In the tests this means
4.5:1 for text, links and help text (WCAG AA) and 3:1 for icons. Text on accent buttons is white or near-black,
whichever reads better (`--on-accent` for your own CSS). An accent you chose
yourself is kept as it is, with readable text on it (`ui/contrast.py`,
`page_colors` and `tint_colors` in `ui/chrome_style.py`).

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

### Compact mode

**View → Appearance → Compact Mode** (Ctrl+Shift+K, or Style → Browser bars) gives the page
the whole window: the tab strip (when it is on top) and the toolbar float over
the page instead, shown while the pointer is at the top edge or on them, or
while you type an address (Ctrl+L), and hidden shortly after. The page does
not move when they appear. Side tabs stay beside the page. With Liquid Glass
and a New Tab photo the floating bars are frosted over the page itself.

### Scroll bars

Steed's pages, menus and panels share one scroll bar: thin, rounded, in the
page's text colour, inset evenly from both sides, without arrows. Websites
get thin overlay scroll bars that take no space and show while you scroll or
point at them (sites that style their own keep theirs). To have websites use
classic scroll bars, set `"thin_scrollbars": false` in
`~/.custom_browser/config.json` and restart Steed.

### Liquid glass

**View → Appearance → Liquid Glass** (also the new tab's gear panel under *Look*, or the
Style page's Browser bars section) switches the profile to a glass look: tabs
become frosted pills lit by the accent colour (the current one, along the top,
grows out of the toolbar with curved feet), the toolbar and address field
get a glossy, lit-from-above finish, hover cards turn translucent, and the new
tab's search box, buttons and panel become blurred glass. With a New Tab
photo or image (and *Match the background* on), the picture becomes the
background of the whole window: the menu bar, tab strip and toolbar show its
top as frosted glass and the New Tab page shows the rest, lined up (redone
when the window is resized; kept in `~/.custom_browser/frost/`). The current
tab and the toolbar share one glass tint over it (side tabs, set in Style →
Tabs & toolbar, become a frosted sidebar the same way); its outline runs straight
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
skipped; the 10,000-bookmark limit applies), **history** (the latest 10,000 web
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

**Just installed.** A newly installed or switched-on extension's background
can take a while to start (the official Bitwarden extension's took about a
minute in testing), and Qt WebEngine 6.11 crashes if its profile closes before
then. Steed checks each such background until it is active; closing the last
window in the meantime shows "Finishing setting up …" and closes when it is
ready (at most two minutes; closing again quits at once).

**Password managers.** Bitwarden's extension (2026.10.2) installs and runs;
filling a login with it has not been checked on a real desktop yet. 1Password's
extension has not been tried (it also needs the 1Password app or account to
pair with). Manage Extensions says the same. Without either extension you can
import their exports into Steed's Passwords, or unlock your Bitwarden vault
with General → **Bitwarden…** so Steed fills its logins (see Passwords).

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
- **offscreen**: Qt's own offscreen documents end the whole browser (a crash
  in Qt WebEngine 6.11), so Steed opens the document itself as a hidden page
  of the extension: `offscreen.createDocument`, `closeDocument`,
  `hasDocument` and `runtime.getContexts` (for `OFFSCREEN_DOCUMENT`) work as in
  Chrome, one document per extension, from the background or the extension's
  pages. It closes when the extension is switched off or removed.
- **declarativeNetRequest** (content blockers' network rules): Qt WebEngine
  6.11 stores rules but never applies them, `updateDynamicRules` never
  finishes and `updateSessionRules` ends the whole browser. Steed keeps all
  three kinds itself (`extension_rules.py`) and applies them in the profile's
  request filter, ahead of ad blocking: the extension's packaged rulesets (read
  when it is switched on, in the background, filed by domain so tens of
  thousands of rules stay fast — by domain, by a whole word of their pattern and by the sites they apply on; uBlock Origin Lite's 18,951 default rules take about 0.2 ms a request; `updateEnabledRulesets`, `getEnabledRulesets`,
  `updateStaticRules` and `getDisabledRuleIds` work), dynamic rules and session
  rules. Conditions: `urlFilter`
  (`||`, `|`, `^`, `*`) or `regexFilter`; request and initiator domains and
  their exclusions; resource types (all but `main_frame` by default); methods;
  first or third party. Actions: block, allow, allowAllRequests (everything on
  a page whose address matches), upgradeScheme, redirect (url, extensionPath,
  regexSubstitution, transform with query parameters) and setting request
  headers. Response headers and appended or removed request headers are
  skipped (a request filter cannot change them). Dynamic rules are kept in
  `steed_rules.json` in the profile's extension folder until the extension
  removes them or is uninstalled (ruleset choices in `steed_rulesets.json`);
  session rules last until Steed quits or the extension is switched off.
- **i18n.getMessage in the background**: Qt's ends the background worker;
  Steed answers it from the extension's own `_locales` (the default locale,
  then the language, then the exact locale), with placeholders and `$1`–`$9`.
- **Older installs**: when Steed's stand-ins change, extensions installed with
  an older Steed are built again at startup, keeping their id and data.
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
Lite starts and its popup works. Qt WebEngine does not apply
`declarativeNetRequest` rules, so Steed applies them itself (above); blocking
with uBlock Origin Lite on a real site still needs a desktop check. A test checks the engine's API list, so it is updated when Qt changes.

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
(General → More Tools → Sync…): each computer downloads and installs the ones another
added, removes the ones another removed and follows switching on and off,
without asking again. A download that fails is tried again at the next sync or
start. Extensions added from a folder or file stay on their computer.

Earlier Steed versions could not enable extensions: Qt WebEngine 6.10/6.11
crashes when a profile's storage folder is set after the profile exists (the
profile's preferences are replaced under Chromium's extension code). Steed now
creates profiles with `QWebEngineProfileBuilder`, folders set first; PyQt 6.11
does not expose the builder's constructor, so `browser/webengine.py` calls Qt's own.

### Sync between computers

**General → More Tools → Sync…** keeps your computers in step through a folder you choose
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

**A translation service on this computer (optional).** `browser/setup_translation.py`
installs LibreTranslate (open-source, offline translation) in its own Python
environment under `~/src/steed-libretranslate`, with CPU-only packages, and
points Steed's setting at it:

```bash
.venv/bin/python -m browser.setup_translation install --languages en,de,fr
.venv/bin/python -m browser.setup_translation start     # http://127.0.0.1:5000, this computer only
.venv/bin/python -m browser.setup_translation status
.venv/bin/python -m browser.setup_translation stop
.venv/bin/python -m browser.setup_translation uninstall # removes it and clears the setting
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
**Send to Your Phone…** (also Tabs → More Tab Tools) shows the page's address as a
QR code: point your phone's camera at it and the page opens there. Nothing is
sent anywhere, the address leaves out tracking parameters, and very long
addresses (over about 2,300 characters) can't fit in a code. Steed draws the
code itself (`browser/qr.py`); the tests check every code size with the zbar
scanner.

### Media controls and Mini Player

While a tab plays sound, a **⏸ title** button appears in the address bar (a
`media` toolbar component; **▶** once paused). Its menu offers **Pause/Play**,
**Mute Tab**, **Go to Tab** and **Open Mini Player**; **View → Media → Play or Pause
Media** and **View → Media → Mini Player** do the same. The title comes from the site's
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
the text size (14–30 px) and **Photo / Light / Sepia / Dark** the colours;
both are remembered. **Photo** (the default) takes its colours from the New
Tab photo and sets the text on a frosted card over a soft blur of it, with a
glass toolbar (light or dark with the theme; plain colours without a photo). **F9**, **Esc** or **Exit reader** returns to the page, as does
following a link in the tab.

**Finding a page's article** (Settings → General) picks the extractor:
**Defuddle** (the default; the extractor of Obsidian's web clipper),
**Readability** (Firefox's reader mode) or **Steed's own** small heuristic.
Defuddle and Readability come with Steed (`browser/data`, MIT and Apache-2.0)
and run in Steed's script world; their article becomes the same plain text
blocks, and when one finds no article Steed's own has its turn. On 18 live
pages Steed's own picked the wrong part of 5 (a photo caption on the BBC,
Wikipedia's reference list, a two-line epigraph on an old-style essay site)
where Defuddle found the article. The same choice serves Read Aloud and the
reading list. **When an agent reads a page** sends it the article (as Markdown
with headings and lists, link addresses left out so the text fits) or
everything on the page; pages with no article always send everything. This
covers `@` questions, tabs attached in the chat, skills that use the page,
`browser.read_tab` and Compare Tabs.
A PDF counts too: Chromium's PDF viewer gives page scripts no text, so Steed
fetches the file from inside the tab (with its sign-in, usually from the
cache) and reads its text with Qt's PDF module, the first 400 pages and up to
40 MB.

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
Each site keeps its zoom (in the profile's `site_settings.json`, so it lasts
across restarts; incognito forgets it); a page with no zoom of its own opens at
100%.

### Spell check

Settings → General → **Spell check**: choose a language and Steed downloads
its dictionary once from Chromium's dictionary repository (under 2 MB, into
`~/.custom_browser/dictionaries/`); misspelled words in text fields are then
underlined in every profile, and right-clicking one offers corrections at the
top of the menu. Off by default (no download until you choose). What you type
is checked on this computer only.

### Address Suggestions

Type part of a visited URL or page title in the address bar, choose a suggestion
with the arrow keys, and press Enter. Clicking a suggestion also opens it.
Successful HTTP/HTTPS visits are stored newest first, with up to 10,000 unique
URLs per profile. Ordinary profile history survives restarts and is shared by
windows using that profile. Incognito suggestions stay in memory and do not
include ordinary profile history. Local files and URLs containing embedded
usernames/passwords are excluded.

**Search engine** (Settings → General): DuckDuckGo (the default), Google,
Bing, Ecosia, Brave Search or Startpage from the list, or **Other…** to type
any search address; Steed adds your search text to its end.

**Search suggestions** (Settings → General, on by default): under "Search … for"
the bar shows up to four suggestions from your search engine (DuckDuckGo,
Google, Bing or Ecosia; other engines get none). Steed sends what you type to
that engine 150 ms after you pause, without cookies or profile identity, and
never sends text that looks like a web address. Incognito windows never send
anything. Answers are cached for repeated text in the session.

Use **History → Clear Browsing History…** to clear the current profile's saved
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
- **Web components:** sign-in forms built inside web components (open shadow
  roots) are saved and filled like any other.
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
so a window association such as `Steed - * - accounts.google.com`
can match the site. The official Bitwarden extension (2026.10.2, checked
November 12, 2026) installs from the Chrome Web Store and runs; see
Extensions for the one engine limit it hit (closing while its background was
still starting), which Steed now works around.

### Your agent signing in for you

When a page your agent is working on asks for a sign-in (for example a watched
page that signed out), the agent can ask Steed to sign in with your saved
login for that site (`steed_signin_request`, then `steed_signin_status`).
Steed fills and submits each step itself (username, password, and the
one-time code when the login has one) and tells the agent only the outcome:
**signed_in**, **still_on_sign_in** (a wrong password, a captcha or a check you
must do), **code_needed_from_person**, **no_saved_login**, **no_sign_in_form**
or **could_not_submit**. The agent never receives the password, the code or
the username, and the task's records keep only the site, the step and the
outcome.

Agents → Access & Permissions → **Sign in with your saved passwords** decides:
**Ask** (the default) shows the site and, when you have several logins there,
lets you pick the account, with **Sign in**, **Deny** and **Always allow**;
**Always** signs in at once with the login you used most recently there;
**Never** refuses. One approval covers that site for the task for three
minutes on the same page, so a sign-in in several steps is asked about once.
Steed only fills a login on a form that looks like signing in (a few fields,
one password, nothing about deleting, changing or paying), on its own site,
never into a form that would send it to another site or window, and only in a
live session (sign-in pages run their own scripts) whose task may submit forms
on that site; if a sign-in fails, it empties what it filled. Not in
incognito, and not while your passwords are locked. A cloud agent's sign-in
always asks.

**Bitwarden.** General → **Bitwarden…** unlocks your Bitwarden vault for this
run (Steed uses the Bitwarden command line, `bw`; sign in to it once in a
terminal with `bw login`). Its logins for a site then work like Steed's own
saved logins, for agents' sign-ins and for routines, filled by Steed so no
agent sees them. Your master password goes to `bw` in its environment, never on
a command line; the session lives only in Steed's memory until you lock it or
Steed closes, and Steed saves nothing from Bitwarden. Only logins saved for
exactly that site are used.

**Routines that sign themselves in.** Tick **If a page asks me to sign in,
sign in with my saved login** in a routine and, when a page it reads asks for
a password, Steed signs in with your login for that site (Steed's passwords
or an unlocked Bitwarden) under the same rules, then reads the page again.
Without a saved login, or when the sign-in does not go through, you get the
notification to sign in yourself, as before.

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
other computers along with passwords (General → More Tools → Sync…, with the sync
passphrase), sealed in the sync folder. Not possible here: using a passkey from
a phone by QR code (that needs Bluetooth and Google's relay).

### Addresses and cards

steed:passwords → **Addresses and cards** keeps shipping addresses and payment
cards in the same encrypted vault as your logins. A card's security code is
never saved, and the page itself only ever sees a card's last four digits.
When a page shows address or card fields (card fields may sit in a payment
provider's frame), a bar asks **Fill your address?** or **Fill your card?**;
nothing is filled until you click **Fill**, only empty fields are filled, and
hidden fields are skipped. **Not now** stops the offer on that site until
Steed closes. The key menu in the address bar lists the same entries. After
you submit a checkout form by hand, Steed offers to save the card and then the
address (or to update a saved one). Two settings on the same page turn the
offers off. Agents never fill card fields. Addresses and cards sync with your
other computers along with passwords (sealed with the sync passphrase).

### Password Checkup

steed:passwords → **Check** marks logins whose password is **reused** or
**weak** (short, common, one repeated character, a run of digits or letters),
on this computer. **Check for breaches** also asks Have I Been Pwned's range
search: only the first 5 characters of each password's SHA-1 hash are sent
(padded, so the answer's size gives nothing away), and the matching happens
here. Logins found in breaches show how often; change those passwords. Results
last until the page closes.

### Routines

Routines are automations now (see Automations above): each one you had became
an automation the first time this version started (the old list is kept as
`routines.migrated.json`), and a routine you or an agent adds is kept as the
automation it is: its schedule, a step that reads its pages (with its saved
steps and signing in if allowed), what Steed noticed if asked, then a chat
with its ask, or a note when it changed. **Agents → Automations → Morning
brief…** starts the brief. What follows describes what a routine does.


**Agents → Routines…**: things your agent does on a schedule, such as a
morning brief. A routine has a name, what to ask, when (every day at a time on
the days you choose, or every 1–12 hours), and up to five pages to read first.
At its time Steed reads those pages in the background with the profile's
sign-ins, starts a new conversation in the agent sidebar with your request and
the pages' text, and shows a notification; the agent's answer raises the usual
"replied" notification. Anything more the agent wants to do in the browser
still asks for your permission. **Run now** runs one at once. Steed must be
running; a run missed while it was closed happens once at the next start, if
within a day. Kept in `routines.json`; never in incognito.

Agents can suggest a routine when you ask for something regular ("every
weekday morning, tell me what's on my calendar"): `steed_routine_capabilities`,
`steed_routine_propose` (name, ask, `daily` at a time on days 0–6 or `hours`
every 1–12 hours, up to five pages) and `steed_routine_status`. A review popup
shows it in the routine editor, where you can change anything, then **Add
routine**, **Decline** or **Later**; nothing runs before you add it. Pending
suggestions are in Task Permissions → Pages & Scripts.

**Saved steps: routines that need no model.** A routine can carry up to 20
steps that Steed does itself, in the routine's profile, before reading: open a
page, click something by its label, type into a field, choose an option, wait
for some text, read the page. After an agent has done a task once in a chat,
it can propose the routine with `steps_from_this_chat`: Steed saves exactly the
page actions that worked there (it notes them in memory while the chat lasts,
never in the journal), and you see every step in the review before adding it.
Each run replays them without asking a model; if a step no longer works (the
site changed), the run goes to your agent as usual, saying which step stopped
and why, so it can finish the job and propose new steps. Saved steps never type
into password or card fields and never press a button beside a filled one.

**Morning brief…** in Routines makes a brief that is ready to go: weekdays
at 08:00, asking for what needs your attention today, then anything else
worth knowing, ending with a line of what was read. Add the pages it should
read (your calendar, your inbox); with **Add what Steed noticed** on, Steed
also adds the watched pages that changed since the last run.

**Each run → Just tell me when it changes** needs no assistant at all: Steed
reads the pages (after any saved steps) and notifies you only when what it read
differs from the last run.

### Automations

**Agents → Automations → Manage Automations…** (the `steed:automations` tab) makes your own workflows. A
routine asks your agent on a schedule; an automation is the general form,
and many need no AI at all.

**What starts it:** you (Agents → Automations, the command palette, or a button
on one of your pages), a schedule (every day at a time, or every few hours), a
watched page changing (with what changed), Steed starting, or:

- **a download finishing**, optionally only files named like `.pdf` (its name,
  where it was saved and where it came from become `{last}`);
- **visiting a site** such as `shop.example` or its subdomains (the page's title
  and address; at most once every ten minutes, never in incognito);
- **a keyboard shortcut** with Ctrl, Alt or Meta, such as `Ctrl+Alt+K`;
- **a page's right-click menu**: *Run Automation on This Page* lists these, and
  the page's title, address and text become `{last}`.

**Its steps, in order**, each able to use what the step before found by
writing `{last}`:

| Step | What it does |
| --- | --- |
| Read a page | the page's text, read in the background with your sign-ins |
| Read pages, signing in | a routine's reading: saved steps, then pages, signing in with your saved login if a page asks |
| Add what Steed noticed | the watched pages that changed since the last run |
| Fetch a web address | the response, without cookies |
| Ask your assistant | a one-off answer from the assistant you chose, with no tools |
| Start a chat | a new agent chat with the text, which may act with your approvals |
| Run a skill | one of your skills, with the text |
| Notify me | a desktop notification |
| Open a page | a new tab |
| Go on only if it contains | stops unless the text so far contains something |
| Go on only if it changed | stops unless the text differs from the last run's |
| Go on only if my assistant says yes | asks your assistant a yes-or-no question about the text; stops on anything but yes |
| Save it to my file library | the text as a file in the library's *Automations* folder (`{date}` and `{time}` in the name; never overwrites; the folder is not shared with agents unless you share it) |
| Wait | 1 to 60 minutes before the next step |

For example: every morning, read the bank's rates page, go on only if it
changed, ask your assistant for the change in one line, notify me. **Examples…**
starts from a ready one: a morning brief, a price check, summarizing the page
you right-click into your files, a weekday reading digest, a download
notification, or a reminder when you open a site. It opens in the editor to
change (an address, the days) before you save. A daily schedule runs every
day, on weekdays, on weekends, or on the days it was given. **Write it
for me** turns a description into an automation for you to check step by step
before saving. Runs happen one at a time; Automations shows when each last ran
and how it ended. **History…** shows its last 20 runs, newest first:
what started it, each step with a short part of the text it had and gave, and
the step that stopped it, so you can see why it did (or didn't) act. History
is kept on this computer only (`automation_runs.json`), goes when you remove
the automation, never goes into a pack, and **Clear History** empties it. Pause All Agents stops steps that need an assistant. Your own
pages can list automations and run them (`/api/automations`,
`/api/run-automation?id=…`). Kept in `automations.json`; never in incognito.

### Watches

Ask Steed to keep an eye on a page for you. Right-click a page — or select the
part you care about first (a price, a status, a list of replies) — and choose
**Watch This with Agent…**. Say what matters in your own words ("tell me when
it's under $50", "when grades are posted"; empty means any change) and how
often to look (15 minutes to daily).

Steed reads the page in the background on that schedule, in the same profile,
so your sign-ins apply, and compares the text (only the selected part, if you
chose one — clocks and ads elsewhere on the page don't count). When it
changed and you said what matters, it asks your helper assistant (see below) with just the before and after
text, run once from its command line with no browser access, whether this is
what you asked about. If it is, or if you asked for any change (or no agent
is installed), a desktop notification says what happened; clicking it opens
the page. **Watches** (Settings → Everything, or `steed:watches`) lists them
with their last news; pause, look now or remove them there. A page that
cannot be read five times in a row is paused. When a watched page starts
asking you to sign in (it sends Steed to a sign-in address, or shows a
password field it did not have before), Steed does not count that as a change:
one notification says to sign in again (clicking it opens the page), the
Watches page shows "Sign in again", and watching picks up from the last text
seen once the page is readable again. Incognito pages are never
watched. Watches live in `~/.custom_browser/watches.json` (with the last text
seen, to compare with).

**Then…** (optional) says what your agent should do when the watch fires:
"add it to my cart", "email me a summary", "book the earliest slot". By
default the notification says what happened and what will be done, and
clicking it starts the agent; tick **Start without asking me first** to have it
start at once (the notification then says it is on it). Either way the agent
gets a new message in the sidebar — which watch fired, what changed, and what
you asked for — and still asks your permission before it reads or acts on a
site, as in any conversation. The Watches page shows each watch's action.

### Organize and compare tabs with your agent

**Tabs → Organize Tabs with Agent…** sends your agent the window's web pages
(only their titles and addresses, numbered) and shows the groups it proposes,
plus any duplicates. Nothing changes until you choose **Apply**: the tabs join
named tab groups (pinned tabs stay put), and duplicates are closed if
**Close duplicates** stays ticked. It uses your helper assistant, like watches.

**Agents → Helper Assistant** is the one assistant Steed sends its small,
one-question jobs to: Write it for me, Help Me Write, history topics,
organizing tabs, watches, and automations' ask and decide steps. It gets only
the question, with no browser access. When more than one is available (a model
you added, Codex, Claude Code), Steed asks which to use the first time one of
these needs it, or when you save a watch or an automation that will, and
remembers the answer; nothing is sent before you choose.

**Tabs → Compare Tabs with Agent…** lists the window's web pages with the
current tab (and the split-view tab) ticked; tick two to six. Their visible
text (up to about 24,000 characters in all) goes into a draft message in the
agent sidebar, ready for your question ("which one fits a small kitchen?").
Only the tabs you tick are shared, and nothing is sent until you send it.

### Dictation

Speak instead of typing, anywhere in Steed. Press `Ctrl+Shift+Space` (or
**General → More Tools → Dictation → Dictate**) and talk; press it again to finish, or hold
it while you talk and let go. A pill at the bottom of the window shows the
microphone level and, on this computer, the words as they are recognized;
`Escape` cancels. The text goes where the cursor was:

- a field on a page (text boxes, search boxes, rich editors such as mail and
  documents, fields inside web components and same-site frames) — inserted at
  the cursor with a space where needed, through the page's own editing so undo
  and the site's input handling work;
- Steed's own fields: the address bar, find bar, and the agent sidebar's
  message box;
- anywhere else (or a password field, which is never filled): it is copied and
  Steed says to paste it with `Ctrl+V`.

Say "new line" or "new paragraph" for breaks (single-line fields get a space).

**Dictation Settings** (same menu) chooses how speech becomes text:

- **On this computer** (default) — private, offline. Choose a model; it is
  downloaded once from the [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
  releases into `~/.custom_browser/dictation-models/`:

  | Model | Size | Good for |
  | --- | --- | --- |
  | Moonshine base (default) | 111 MB | English, fast and accurate |
  | Moonshine tiny | 30 MB | English on slower computers |
  | Whisper base | 208 MB | ~100 languages (set a language code or leave blank) |
  | Parakeet 0.6B v3 | 487 MB | Best accuracy, 25 European languages |

  This needs the speech engine package once:
  `.venv/bin/python -m pip install -r requirements/dictation.txt`.
  The model runs in a separate process (`browser/dictation_worker.py`) so the browser
  never stutters; on an i3-1315U, Moonshine base writes down a 4-second
  sentence in about 0.2 s.
- **An online service** — anything that accepts OpenAI's
  `/v1/audio/transcriptions` request: OpenAI (`gpt-4o-mini-transcribe`,
  `whisper-1`), Groq (`https://api.groq.com/openai/v1/audio/transcriptions`,
  `whisper-large-v3-turbo`), or your own server (Speaches, LocalAI,
  whisper.cpp's server; plain `http://` is allowed only for `localhost`). The
  API key is kept in the desktop keyring (Secret Service), never in Steed's
  files. Private (search-profile) windows always dictate on this computer.
- **Off**.

**Test microphone** records four seconds and shows what was heard. Audio is
only held in memory while you speak and while it is transcribed; nothing is
saved. Steed uses the system's default microphone (choose it in the desktop's
sound settings).

**Websites' voice buttons.** Sites that use the browser's speech recognition
(`SpeechRecognition` / `webkitSpeechRecognition` — voice search, voice notes,
language apps) work in Steed and use the same engine as your dictation. Qt
WebEngine has none of its own (starting it used to end the tab), so Steed's
script answers instead:

- The first time, the site asks for the **microphone** like any other site
  request; the answer can be remembered in Website Permissions.
- The pill shows "‹site› is listening…" with the words as they come; `Escape`
  stops it. Leaving or closing the page ends it.
- Sites get the standard events and results: interim results when they ask,
  one phrase ending after a pause, or a final result per phrase in continuous
  mode until they stop. The page's language goes to an online service.
- Extensions' pages work too: in a tab, in their toolbar popup, or in an
  offscreen document (how extensions listen from the background); the
  microphone question names the extension.
- Only the top page of an `https://` site (or `localhost`) is answered; frames
  inside a page and plain `http://` sites get the standard
  "service-not-allowed" error, as do sites when dictation is off or not set up
  (Steed says what to set up). While you dictate with `Ctrl+Shift+Space`,
  sites get "audio-capture".

Extension pages (popups, options and background pages) don't get this yet.

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

### Incognito

Incognito is a special profile that:

- Doesn't save cookies
- Doesn't save browsing history
- Doesn't save tabs
- Keeps cookies, site storage and cache in memory only while an incognito window
  is open. Once no window uses it — you switch the last one away or close it —
  Steed discards that private profile, and the next incognito window starts
  empty, as when closing the last incognito window in other browsers. Downloads
  still running in incognito delay this until they finish. Sites can still
  recognize you by your network address.

Open it with **Profile → New Incognito Window** (`Ctrl+Shift+N`), or turn this
window incognito with **Profile → Switch to Incognito** (`Ctrl+Shift+0`). In
incognito the same item reads **Leave Incognito** and takes the window back to
the profile it was using.

**Profile → Quick Switch** lists existing profiles immediately at startup.
The numbered shortcuts work before the menu is opened. Ordinary profiles are
listed alphabetically, including `default` and `global`.

## Tabs and groups

**Tabs on the side.** View → Appearance → **Tabs on the Side** puts tabs in a
list on the left (Style → Tabs also offers the right, and the width can be
dragged). Groups show as a heading over their tabs; click it to collapse the
group to its first tab with a count, and again to open it.

**Mouse gestures** (off; Settings → General). Hold the right button over a
page and draw: ← Back, → Forward, ↓ New tab, ↓→ Close tab, ↑↓ Reload. A plain
right-click still opens the page menu, when you let go of the button (Steed
holds the press back until it knows you aren't drawing). They are off by
default because some sites use right dragging themselves.

**Tab previews.** Hovering a background tab for half a second shows a small
snapshot of it (taken when you last left it; about 15 KB in memory, never
saved), with its title and site, in both the HTML and native tab strips. Tabs
not shown since Steed started show only their title and site. **Tab previews
on hover** in Settings turns them off.

**Session history.** Every 10 minutes Steed records this profile's open windows
and tabs (only when they changed; also once when Steed quits), keeping the last
48, and records each window you close while another of the profile stays open,
keeping the last 20. **Tabs → More Tab Tools → Session History…** lists them with a preview of
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
**Delete** removes it. **Tabs → More Tab Tools → Save All Tabs as Group…** saves the window's web
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

Existing profiles are migrated conservatively: pre-existing cookies remain local rather than being overwritten by the global profile. **History → Use Global Cookies for This Site…** explicitly resets the current site's baseline, after confirmation. Local changes/deletions prevent automatic reinheritance. Cookie sharing does not copy local storage or guarantee a complete signed-in session.

## Downloads and ad blocking

Open **General → Downloads** (`Ctrl+J`) for progress, cancellation, supported pause/resume, file opening, and history. Incognito downloads are excluded from disk history.

Files that run programs when opened (`.AppImage`, `.sh`, `.deb`, `.rpm`, `.run`,
`.desktop`, `.jar`, `.exe` and others) are saved only after you choose **Save**
in a warning; downloads from sites on the dangerous-site lists are refused.

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

Downloads go straight to your system's Downloads directory (`~/Downloads` if
the desktop does not publish one), using the suggested filename without any
supplied directory components; a taken name gets a number (`manual (2).pdf`),
so nothing is overwritten. Turn on Settings → General → **Ask where to save
each download** to choose the place and name for each one instead.

Search filenames, profiles or destination paths in the Downloads search field;
matching ignores case and treats punctuation literally. Clear the search to show
all records. Progress updates retain the selected download; filtering it out clears
selection. Select a row and use **Copy Path** to copy its full destination path,
including when the file is still downloading or is no longer available.

Open **General → More Tools → Ad Blocking** to download/update EasyList and EasyPrivacy, toggle filtering, add host-name exceptions, or write **your own rules**, one per line in the same syntax as the lists: `||ads.example.com^` blocks a site's requests, `example.com##.cookie-banner` hides an element there, and `@@||example.com^` lets a site through (up to 1,000 rules, kept in `adblock_rules` in `config.json`). Your rules are compiled with the lists and survive list updates. Only a small bootstrap rule set ships with the browser; download the full lists for broader coverage. Rule compilation runs off the GUI thread, and a failed update keeps the last working rules. Network blocking applies immediately, while cosmetic (CSS) hiding is re-applied on page load — reload pages after changing exceptions. Same-origin frames, including nested frames and `srcdoc`, receive their own CSS rules; each pass covers up to 32 documents and eight frame levels. Cross-origin frames cannot be inspected. Scriptlets and resource redirects are not enabled, and unsupported filters are not presented as full uBlock compatibility.
