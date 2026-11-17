# Progress log

What each round of work changed, newest first, as written at the time. For
where things stand now, see the [roadmap](../../ROADMAP.md); for how things
work, the [documentation](../README.md). Links and file names are as they were
then: modules now live in `browser/`, and the documentation in `docs/`.

## Rounds since October 25, 2026

**Before going public — November 16, 2026.**
- Codex chats can continue after Steed closes too: kept threads are saved (not
  ephemeral) and resumed with `thread/resume` while Codex still has them. The
  setting is now **Keep agent chats so they can continue after Steed closes**.
- A Steed-run chat whose connection dropped is recorded as interrupted; the
  agent gateway used to race the provider and sometimes cancel it.
- The intermittent startup exit did not occur in 85 starts; recorded in the
  verification record, not claimed fixed.
- The agents page was checked end to end with a live (synthetic) chat.
- A security policy (`.github/SECURITY.md`); the release plan's outdated
  claims refreshed.

**A tidier repository — November 16, 2026.** The browser modules moved into
`browser/` and optional requirements into `requirements/`, leaving 13 files at
the root; DOCUMENTATION.md became topic files in `docs/`, and the roadmap's
dated notes moved here.


**Agents page round — November 16, 2026.** From using Steed's sidebar to talk
to Claude Code:
- Chats: the five most recent, newest first, named by their first message, with
  agent and time; the rest behind **N more**. Wide, they stand in a column on
  the left; narrow, they fold behind **Chats**.
- Closing Steed (or turning agent access off) records a chat as interrupted
  with the reason, never as a cancellation; the card says **Chat closed**.
- **Keep Claude Code chats so they can continue after Steed closes** (on by
  default): chats are saved by Claude Code in their own folder, and a closed
  one offers **Continue this chat**, resuming the same session with its earlier
  messages shown above. Codex chats are not kept yet.
- The session picker shows what you said last and a short id, so copies are
  told apart, and selects nothing for you.
- **Allow all commands for this chat** on Claude Code's command prompts.
- The page: full-height conversation, Markdown replies (built from text nodes,
  never HTML), a growing message box where Enter sends, a round Send button,
  a closed-chat bar instead of a dead composer, agent-neutral wording.

**Side branches folded in — November 16, 2026.** Main is the only branch. Work
that never reached it is now merged: the agent card says why a chat stopped
(error, ended or disconnected) and when the agent last reported; a turn cut
off mid-way is recorded as interrupted with its partial reply, not as a
cancellation; and Notion, Linear and Dropbox are packaged typed POST read
profiles in the connector picker.

**Real-world shakedown — November 15, 2026.** The last rounds checked against
the real services, in a scratch profile (deleted afterwards):
- uBlock Origin Lite from the Chrome Web Store installs, its background runs,
  and its six default rulesets load (18,951 rules). Ad scripts are swapped for
  its stand-ins and trackers blocked; on cnn.com and theverge.com 13–16
  requests per page were blocked or swapped. Matching was too slow with its
  real rules (4–13 ms a request); rules are now also filed by a whole word of
  their pattern and by the sites they apply on, down to about 0.2 ms (about
  1 ms on a first page load, while patterns compile), with every filed rule
  still found (5,046 checked).
- The real phishing and malware lists (18,241 and 1,772 domains) download; a
  listed domain shows the warning page and is never requested.
- The update check finds Qt WebEngine 6.140.0 on PyPI and new commits on the
  GitHub repository (tested on a clone set two commits back).
- Spell check downloads the real en-US dictionary; right-clicking "sentense"
  in a real page offers "sentence" first.
- A site app's desktop entry launches the app window (no tab strip or address
  bar) on the site.
- A Widevine-protected stream (Shaka Player's Sintel demo) plays decrypted with
  Chromium's Widevine module.
Still manual: printing to a printer, Read Aloud with a real voice, default
browser on the desktop, and the agent features with a real agent.

**A day in Steed — November 14, 2026.** For people already using Steed daily,
what still sent them elsewhere: Read Aloud (desktop voices), link hints for
keyboard browsing (Ctrl+;), site apps (a site in its own window and
applications-menu entry), your own keyboard shortcuts for every command,
Search What You've Read (opt-in local full-text index of pages read, cleared
with history, handed to the agent on request), and Routines (the agent on a
schedule, with signed-in pages read first, each run a new conversation).
Manual checks: a real voice, a site app from the desktop menu, a morning
routine with a real agent. Agents now suggest routines (reviewed in the
routine editor, like watches) and can search what you've read
(steed_reading_search) under a new Access & Permissions capability, "Search
what you've read" (Ask by default: the words are shown first). Next: a real
agent asked "find that article I read about X".

**Everyday basics and trust — November 14, 2026.** After an audit of what
would put people off Steed: printing (Ctrl+P and pages' Print buttons), New
Window (Ctrl+N), Quit (Ctrl+Q quits; Ctrl+Shift+W closes a window), full
screen (F11), page source (Ctrl+U), links' "new window" opening one, Search
for a selection, dark websites, a bookmarks bar, spell check with Chromium's
dictionaries, warnings for phishing and malware sites (public lists, nothing
sent) and for downloads that run programs, history and bookmarks up to
10,000, default-browser registration and local files from other apps,
`install.sh` (install, update, default), daily update checks for Steed and the
engine, protected streaming with an existing Widevine module (Chrome,
Chromium or Firefox), Steed's own icon, honest import and welcome wording, an
Agents menu with setup under Access and Setup, and a README for everyday use
first. Manual checks: printing to a real printer, default browser on GNOME
and KDE, Netflix in Steed, spell check underlining on a real site.

**Watches — November 14, 2026.** Right-click a page or selection → Watch This
with Agent: Steed checks it in the background (the profile's sign-ins, a
hidden muted page), compares the text of the chosen part, and when it changed
asks the person's agent CLI (claude -p / codex exec, no browser access) whether
it matches their words; desktop notification; steed:watches to manage.
A watch can also say what to do when it fires ("then…"): the agent gets
the task in the sidebar from the notification (or at once, if chosen), with
the usual site permissions. Tabs → Organize Tabs with Agent (proposed groups
and duplicates, applied only on request) and Compare Tabs with Agent (the
ticked tabs' text as a draft for the person's question).
Manual check: a real watch, organize and compare with Claude Code installed.
Agents now suggest watches from a chat (steed_watch_propose → a review
popup: adjust, pick the profile, Add watch; only the person lets a then-action
start without asking). A watched page that starts asking for a sign-in is
reported once ("Sign in again"), never as a change, and watching resumes
from the last text once readable.

**A calm start — November 14, 2026.** Welcome is a three-step tour (look,
your things, your agent; skippable); one-time tips the first time the address
bar, a website, a New Tab or the agent sidebar is reached; the New Tab ☰ menu
opens its sections on hover but never a whole page; Customize New Tab is three
picture tiles and the current choice, the rest under More options (kept open
after a choice there); the agent sidebar's welcome has one main action;
explanations fold to one line until pointed at; Settings → Everything lists
every Steed page. Menus regrouped with icons (ticks replace icons when on);
one filled main action per button row in dialogs; shorter intros on the
busiest management pages; the agent dock is titled Agents.
Task Permissions and App Connections explanations rewritten in plain words
with their limits kept (approvals, 10 minutes, 1,000 operations, isolation).

**One theme — November 14, 2026.** Steed's pages match the browser's colours
by default (photo, chosen browser colours, or the plain dark browser's own);
the old per-profile "pages follow the photo" (off for some) is retired and
migrated without touching other style choices. Liquid glass and Lumen are one
look: the page card floats over the frosted photo. Compact mode has no card.

**New Tab reload storm fixed — November 14, 2026.** Many open New Tabs
could reload over and over (a change arriving while tabs loaded asked them
to reload again after loading; closing any tab reloaded every Steed page).
Now: photos and their colours update open New Tabs in place (cross-fade,
nothing typed is lost); Steed's own reloads are coalesced, skip pages that
began loading after the change, wait for a loading tab, are capped per tab
and logged (reload_loops.log) if the cap trips; closing a tab reloads only a
page that lists closed tabs. Open Steed pages (Settings, management pages,
sidebar, dialogs) take colour and Motion changes live. Settings and the agent
panel show Steed's horseshoe instead of an "S". Tests: 46 checks of connector
planning documents removed (the documents stay); fixed waits in the password
assistant tests kept (they prove something does not happen).

**Lumen round 2 and fixes — November 13, 2026.** Lumen is editable on the
Style page (Light and page) and through reviewed agent style proposals
(`lumen`: light strength and colours, card radius/margin/shadow, typeface,
title width; font tokens for CSS); the agent sidebar and other docked panels
and each side of split view are cards; the light uses sites' theme-color;
dialogs arrive with motion. Tab icons: Steed's pages get their own glyphs and
icon-less sites a globe instead of a letter. New photo now: no false "Error
loading page", a message when the photo service is down (it was answering
HTTP 522), the last 12 photos kept to show instead, and a fetch timeout.
Addresses on the local network (192.168.x, 10.x, *.local…) open over http.
Test audit (1,982 tests): ~85–90% genuine behaviour checks; a mutation spot
check caught 6 of 12 planted bugs; the misses (address parsing, incognito
download history, cookie deletion for sites kept local, Balanced effects)
now have tests that catch them, and weak or misnamed tests were
strengthened. Left: ~46 tests of connector planning documents
(test_default_connector_profiles.py) could shrink to a schema check; fixed
sleeps in test_password_assistant.py could become event waits.

**Lumen, Steed's look — November 13, 2026.** The page floats as a card in a
field of light coloured by the current site (its icon) and the browser's
accent; bars without backgrounds; the light flows between tabs; lit capsule
tabs with a gliding highlight and growing new tabs; cross-fading tab switches;
animated toolbar hovers and an accent glow on the address field; frosted
menus that rise in; Mona Sans bundled (static cuts, via a private fontconfig
file) with expanded titles; Steed's pages restyled (sentence-case labels,
pointer light on cards, spring switches, scroll-driven title settling, one
entrance per page); a Motion and light setting (Full, Balanced, Minimal).
Manual check: hover, tab switching and menus on a real desktop, and a light
theme. Split view's current side carries a rim of the accent's light.

**Websites' speech recognition — November 13, 2026.** SpeechRecognition and
webkitSpeechRecognition are answered by Steed's dictation engine: the site's
microphone permission, standard event order, interim and continuous results,
pause detection, no-speech/not-allowed/aborted errors, Escape and navigation
end it; top frames of secure sites only. Checked with tests and a real model
on a voice-search page; manual check: a real site's voice button with a
microphone. Extension pages (tabs and toolbar popups) are answered too, the
prompt naming the extension; popups stay open while it asks.
Offscreen documents (how extensions listen from the background) are opened by
Steed itself, which also fixes a crash: Qt WebEngine 6.11's own
chrome.offscreen.createDocument ended the browser.

**Extension API crash audit — November 14, 2026.** Every function of the
APIs Qt WebEngine gives extensions natively (64 calls, one Steed process
each) was called from a background worker. Three failed: declarativeNetRequest
updateSessionRules ended the browser, updateDynamicRules never finished, and
i18n.getMessage in the background ended its worker (then Steed at exit).
Checking static rulesets against real traffic showed the engine applies no
declarativeNetRequest rules at all (content blockers did nothing). Steed now
keeps and applies packaged, dynamic and session rules itself
(extension_rules.py, indexed by domain: 500 requests against 52,000 rules in
well under 2 s), answers getMessage from the extension's locales, and
rebuilds extensions installed with older stand-ins at startup.
Manual check: uBlock Origin Lite from the Chrome Web Store on a real site.

**Dictation — November 13, 2026.** `Ctrl+Shift+Space` push-to-talk
anywhere in Steed (tap or hold, Escape cancels) with a listening pill showing
live words; the text goes into the focused page field (inputs, textareas,
rich editors, shadow roots, same-origin frames), Steed's own fields or the
agent message, else the clipboard. Engines: open models on this computer
(Moonshine, Whisper, Parakeet through sherpa-onnx in a separate worker,
downloaded on demand) or any OpenAI-compatible transcription service with the
key in the keyring. Websites' SpeechRecognition no longer crashes the tab.
Checked with a real model, real speech audio and the real microphone; manual
check: dictate with a microphone on the desktop. Websites' speech
recognition followed (above).

**Ask your agent from anywhere — November 13, 2026.** Right-click a
selection, link or page to draft a question to your agent with that context;
"@ question" in the address bar sends one about the page; desktop
notifications (D-Bus) when an agent replies or needs approval while you are
elsewhere, clicking through to the conversation.

**Continue Claude Code sessions, ACP agents — November 13, 2026.** The agent
sidebar can continue a saved Claude Code session (resumed as a fork in its
folder) and chat with any Agent Client Protocol agent set up in ACP agents
(stdio JSON-RPC, Steed's MCP server bound to the task, approvals in Steed, no
file system or terminal offered). Checked with synthetic runtimes; manual
check: continue a real session and try a real ACP agent such as Gemini CLI.

**Calmer management pages — November 13, 2026.** The pages rendered from
dialogs (cookies, downloads, bookmarks, history, files, connections, tasks,
layout, start pages, tab groups, permissions, agent setup, ad blocking,
services) get a real page header, card sections, quiet buttons with
destructive ones marked, switches for on/off buttons, tables that only break
long values, empty states, and a clearer sidebar; Close buttons that repeat
the header's ✕ are hidden on full pages. Welcome/classic pages get a round ☰
and pill header links.

**Extension shutdown crash fixed — November 12, 2026.** Closing Steed while a
just-installed extension's background was still registering crashed Qt
WebEngine (reproduced with the official Bitwarden package and a bare Qt
profile); Steed now watches new backgrounds until active and waits for them
on close.

**Passwords in web components — November 12, 2026.** Saving, filling,
one-time codes and generated passwords reach sign-in forms inside open shadow
roots (save/update prompts, TOTP and passkeys were already in place).

**Photo reader theme — November 12, 2026.** Reader view's default Photo theme:
photo-matched colours, a frosted reading card over a soft blur of the photo.

**Compact mode, side glass tabs — November 11, 2026.** Compact mode floats the
tabs and toolbar over the page (Ctrl+Shift+K); the glass picture now covers
any bar layout, so side tabs are a frosted sidebar lined up with the page.
Manual check: compact mode's reveal at the top edge on a real desktop.

**Live glass cards, fading colours — November 11, 2026.** Hover cards show
the current tab live and refresh other tabs' snapshots after loads; frosted
with Liquid Glass. Photo colour changes fade over 0.4 s.

**Your own photos — November 11, 2026.** New Tab photos can come from photos
you add, a folder (shuffled, each once per round), or favourites; save the
current photo, skip online ones for good, black & white style.

**Scroll bars, plain glass tab — November 11, 2026.** One themed scroll bar
for Steed's pages, menus and native panels; thin overlay scroll bars on
websites (`thin_scrollbars` to turn off); the glass tab has no curved feet.
Manual check: overlay scroll bars on a real desktop with GPU rendering.

**Smooth glass panels, tab outline — November 11, 2026.** Glass menu panels
draw a frosted snapshot of the browser behind them inside smooth rounded
corners (no pixel-cut masks, no see-through windows needed on X11); the
current tab's outline follows its feet into the strip's bottom line.

**One picture behind the window — November 11, 2026.** In glass mode the
New Tab picture fills the whole window: the bars show its top (evenly
blurred, toned for legibility) and the page the rest, aligned; the current
tab and toolbar share one tint; glass popups are cut to rounded shapes.

**Frosted bars, quieter menus — November 11, 2026.** Glass bars are frosted
over the New Tab picture; menu rows lose their boxes, the scrollbar is thin
and on the left, sub-menus cascade beside their panel, and only sub-menus
open on hover (Settings no longer pops up from a hover); YOLO mode shows as
warning pills. Manual check: frosted bars on a real desktop at other window
sizes.

**Pages follow the photo, menus beside — November 10, 2026.** Steed's own
pages take the New Tab photo's colours by default (Style can turn it off);
New Tab ☰ sections open beside the menu; glass menus are rounded and
see-through; the glass tab strip fades into the toolbar; tab titles stay
until tabs are very narrow.

**Every browser colour editable — November 10, 2026.** Style → Browser
colours: eleven chrome colours, each automatic (theme or photo) or fixed;
Keep these colours freezes the photo's; the tab strip's details (letter
icons, hovers, separators) now use the toolbar's colours instead of the page
palette; the glass tab joins the toolbar.

**Menus and tabs — November 10, 2026.** The active tab curves into the
toolbar (rounded style); the ☰ menus are compact and in the toolbar's
colours; Customize New Tab can sit inside the New Tab ☰ menu instead of its
own gear; the dark-mode photo tint is stronger and reaches the search box.

**Liquid glass — November 10, 2026.** A per-profile glass look (View →
Liquid Glass, the new tab panel, Style): frosted pill tabs on a softly lit
strip, a glossy toolbar and field, translucent hover cards, and glass search,
buttons and panel on the new tab with a refraction filter and sheen. Manual
check still to do: how the blur and refraction look with GPU rendering on a
real desktop (screenshots were taken offscreen, without the GPU).

**Colours from the photo — November 10, 2026.** With a photo or own-image
background the browser's tab strip, toolbar and the page's highlights take
the picture's main colour (on by default, *Match the background*). The
gear's settings are a panel on the new tab itself, with New photo now.

**A simpler new tab, quieter chrome — November 10, 2026.** The new tab page
is now just "Steed" and an empty search box over a photo that changes every
few hours (Lorem Picsum, credited, cached; gradient fallback), with colour and
own-image backgrounds, a classic layout, and the controls at the top right.
The menu and status bars are hidden by default, with messages shown briefly
over the page. Tab hover cards: one card (no second plain tooltip), Steed's
colours, transparent behind rounded corners, one width by default.

**Clearing options — November 8, 2026.** Clear Browsing Data with time
ranges (history and downloads now keep times), per-site clearing with its
size, forget-on-close clearing all site storage, and clearing on exit. Site
storage is cleared through Chromium's Storage.clearDataForOrigin via a hidden
DevTools page. Found while testing: a malformed origin (https://www.127.0.0.1)
made Chromium clear another site's storage; Steed now never sends hosts
Chromium could misread. Also fixed: recently closed tabs listed oldest first
in tab search.

**Extensions, browsing, privacy, saving, crashes — November 8, 2026.**
Extensions: errors kept and shown, webNavigation events, tabs/windows opened
or closed by backgrounds through Steed, options pages. Browsing: split view,
sites that always open in a profile, recently closed tabs in tab search
(vertical tabs already existed). Privacy: tracking parameters removed on load,
HTTPS first with fallback, forget a site when its tabs close, opt-in
fingerprinting resistance. Saving: pages as MHTML, complete HTML or PDF;
highlights and notes in reader view; the reading list as an EPUB. Crashed
tabs show a panel with Reload, and a crash log keeps time, host and reason.
Open: a desktop check of split view and of fingerprinting resistance on real
sites.

**Open problems fixed — November 8, 2026.** The crash at exit: PyQt deletes
leftover parentless objects (closed windows, singletons) while walking its
object table at QApplication deletion, and their Python code grew the table
under the walk (freed memory, seen as 0xdd... with PYTHONMALLOC=debug; it
predates this work and could also hit a normal quit). Steed now destroys its
leftovers first (qt_cleanup.py, from main.py and the tests); the reproduction
went from crashing in most runs to none. Also: runtime.onMessage in injected
extension scripts; extension tabs matched by performance.timeOrigin; passkeys
sync sealed like passwords; passkey suggestions (conditional requests) through
a bar. Not possible: passkeys from a phone by QR code.

**Passkeys, Password Checkup, extension script worlds — November 8, 2026.**
Passkeys kept in the encrypted vault (P-256, attestation "none"), created and
used through a page script standing in for navigator.credentials, asked for
each time, verified in tests by checking the signature with the public key the
page received; security keys stay available. Password Checkup finds reused
and weak passwords locally and breached ones through Have I Been Pwned's
padded range search (5 hash characters sent). Scripts injected for extensions
now have chrome.runtime (id, getURL, getManifest, sendMessage with sender.tab)
and chrome.storage. (The open items found here were fixed the same day; see above.)

**Extension menus, scripting, badges, shortcuts — November 8, 2026.** A hidden
bridge page per extension connects its background to Steed: context menu items
in the page menu (kept across restarts), scripting.executeScript (functions,
async ones, files), insertCSS/removeCSS and registerContentScripts run by Steed
in the extension's own script world, badges on pinned buttons, action.onClicked
for extensions without popups, manifest command shortcuts and Alt+Shift+1–9
for pinned buttons. Found while testing: Chromium stops Steed when an
extension's message port outlives its profile, or an extension goes while its
page shows; Steed now drops bridge pages first and lets ports close before
profiles go. Open: injected scripts lack chrome.* APIs; tab matching is by
address and title.

**Extension stand-ins, updates, site access, pins — November 8, 2026.**
Steed now builds what it installs from a kept copy: a fixed id (the store key
from the .crx header, else a generated key), so installing again keeps
chrome.storage (measured: Qt keeps an extension's storage by id across
uninstall/install, but will not install over an installed one); stand-ins for
missing APIs, including a working tabs list and messaging over content-script
ports, sender.tab/frameId, and storage.sync kept locally; and site limits
narrowing host access and content scripts. Daily store update checks (more
permissions wait for a yes), Site Access…, pinned toolbar buttons, popup links
as tabs. Measured: Dark Reader works; uBlock Origin Lite starts but Qt does not
apply declarativeNetRequest rules. Open: a desktop check with several store
extensions; real context menus and scripting.executeScript through Steed.

**Extension store, toolbar, details, sync — November 8, 2026.** Browse the
Chrome Web Store in a tab and add with an "Add to Steed" bar, or paste a store
link; extensions download from Google's update address, then Steed shows name,
icon, version, plain-words permissions and the Chrome APIs Qt WebEngine lacks
before asking. A puzzle toolbar button lists extensions and opens popups (a
frameless window: as a Qt popup it closed itself when the menu closed). Store
extensions sync by id; each computer installs/removes/switches to match.
Measured on Qt 6.11.2: extensions get alarms, declarativeNetRequest, i18n,
management, offscreen, runtime, storage, tabs and webRequest only; the real
uBlock Origin Lite installs but stops at chrome.permissions. Open: a desktop
check with real store extensions; small stand-ins for missing APIs
(permissions, scripting) where Steed can provide them; extension updates.

**Extensions — November 8, 2026.** Chrome extensions (Manifest V3) work:
add from a folder, .zip or .crx, switch on/off, remove, open toolbar popups
(General → Extensions); switched-on ones come back on after a restart. The
crash when enabling one was not Qt's alone: setting a profile's storage folder
after creating it (as Steed, and the earlier C++ reproducer, did) leaves
Chromium's extension code with freed preferences, as Pacitum browser found.
Profiles are now made with QWebEngineProfileBuilder (its constructor called
through Qt, since PyQt 6.11 omits it); no Qt patch was needed. Open (then): a
store link installer and a toolbar button, both since added.

**Reading list, sync, extensions — November 7, 2026.** A per-profile reading
list (View → Save to Reading List) keeps the article text so pages can be read
offline in reader view. Sync between computers through a folder you choose
(General → Sync…): bookmarks, saved groups, the reading list, search keywords
and, with a sync passphrase, passwords sealed in the folder; per-item
newest-wins merging with dated deletions, tested with simulated computers.
Open: a desktop check across two real computers through Syncthing or similar.

**Import, local translation, screenshot editing — November 7, 2026.** Import
bookmarks, history and Firefox open tabs (as a saved group) from Chrome-family
browsers and Firefox, reading copies of their files. `setup_translation.py`
installs LibreTranslate locally for translation that never leaves the computer
(its steps are tested; a full install is still to run). Screenshots can be
cropped, highlighted and partly hidden before saving or copying. Extensions:
Qt WebEngine 6.11's extension manager loads an unpacked extension disabled, and
both its load-finished signal and enabling it crash under PyQt (also on
6.12/6.140); extension support waits on that.

**Screenshots, translation, tab quick actions — November 7, 2026.**
Full-page and visible-area screenshots (saved or copied; full page through
Chromium's own capture); page translation through a LibreTranslate-compatible
service you choose (text only, no cookies or address, reversible) or Google
Translate's proxy; Copy Clean Link, Duplicate with History and Send Tab to
Profile in the tab menu.
Found while testing: a search-suggestion request that failed (offline, or the
engine blocked) crashed Steed, since Qt returns nothing to read from a failed
reply; that and the same pattern in the page-app fetch, reader images and the
news feed are fixed, with a regression test.

**Media, address bar, previews, small text — November 7, 2026.** Media
controls (toolbar play/pause/mute, a Mini Player that floats the playing tab,
since Qt WebEngine disables picture-in-picture, and MPRIS for media keys and
the desktop sound menu); search keywords (built-in and your own) and inline
completion of visited sites; tab hover previews from snapshots taken when
leaving a tab; and automatic zoom for pages with tiny text, per site and
switchable. Open: a desktop check of media keys and the Mini Player on real
video sites.

**Session history — November 7, 2026.** Ten-minute snapshots of a profile's
windows (48 kept, only when changed, plus one at quit) and closed windows (20
kept); Tabs → Session History… previews, opens (lazily, with tab groups and
colours) and deletes them; Tabs → Reopen Closed Window. Not in incognito.

**Saved groups in search; on-demand cookie inspection — November 7, 2026.**
Save All Tabs as Group keeps the window's pages without closing them; saved
groups appear in the palette and reopen from it. A `steed:memory` option,
"Cookie inspection: only when needed" (Memory saver), skips inspection for the
global profile alone and incognito, and starts it with any profile that
receives shared cookies; sharing tests pass with it. New memory settings follow
the stored mode instead of making it Custom.

**Tab group colours and saved groups — November 6, 2026.** Groups take a
chosen colour (nine, or automatic) in both tab strips. Save and Close Group
keeps a group's pages per profile; Tabs → Saved Groups reopens them lazily as a
group or deletes them. Not in incognito.

**Downloads and search — November 5, 2026.** Failed, cancelled or cut-off
downloads can be retried to the same file without asking; an address-bar button
shows overall progress and "Done"; Show Folder selects the file. The palette
also searches bookmarks (`*`) and history (`#`, Ctrl+Shift+Y) and opens them in
a new tab.

**Downloads from local pages — November 7, 2026.** The "steed: page cannot
start a second download" finding was the local-page protection
(`LocalContentCanAccessRemoteUrls` off): the engine refuses downloads that any
`steed:` or `file://` page starts. Link clicks on local pages are now reopened
as typed navigations (download links work) and Save Link/Image/Media As there is
started by Steed; local pages still cannot fetch from the web (now tested).

**Video codecs installed — November 4, 2026.** The Qt WebEngine 6.11.2 codec
build finished (Release, three compile jobs: Chromium's inner ninja needs
`NINJAFLAGS` at configure, or it runs one job per core and runs out of memory)
and is installed in `.venv`. H.264 and AAC play; all 1,799 tests and the smoke
test pass on it; speed and memory match the PyPI engine. Open: a desktop check
of Discord/Tenor GIFs and other MP4 sites. The build is optional; `clean` frees
about 13 GB afterwards, keeping the 0.5 GB engine.

**Unloading keeps sign-ins — November 4, 2026.** Discord still signed out
sometimes when a background tab unloaded: a frozen page could take longer than
the 1.5 seconds Steed waited for it to save. Background unloading now waits up
to 5 seconds plus 0.3 seconds for storage, sends Chrome's `freeze` and a proper
`pagehide` too, and never unloads a page that did not answer (it stays frozen;
three tries per session).

**Priority: daily workflow engineering.** Public-release work remains reference
backlog; it is not the active agenda.

**Engine upgrade routine — November 3, 2026.** `upgrade_engine.py` checks
PyPI, tries the newest PyQt6/Qt WebEngine in `.venv-upgrade` (Chromium
version, feature check, smoke test, tests, optional timing comparison), and
applies or rolls back only trialled versions. PyPI now has PyQt6 6.12.0 and
Qt WebEngine 6.140.0 (a new version numbering). Its trial passed (1,788 tests,
smoke test) with the same Chromium 140 web features and security fixes up to
152 (151 now), but used 10–17% more memory on screen. Not applied: it gains no
web features, and the codec build is prepared for 6.11.2.

**User scripts and styles — November 3, 2026.** `steed:userscripts` (General →
User Scripts & Styles…) keeps per-profile CSS and JavaScript for listed sites:
styles from Steed's script world at document start, scripts in the page world
at start/ready/loaded with errors contained, on/off per entry, none in
incognito. Agents propose user scripts and panel apps with
`customization.capabilities/propose/status`; a native review shows the exact
code (every-site scripts called out), the user picks a profile, and Add only
creates new entries.

**Reader view — November 3, 2026.** F9 (View → Reader View) shows the page's
article as text in a sealed Steed page with size and light/sepia/dark controls;
the extraction runs in Steed's script world and returns text only. Images are
fetched by Steed without cookies (size and format checked from their bytes) and
shown inline; the reader page still has no network access.

**Site settings — November 3, 2026.** The address bar's site icon opens a menu
for the current site: JavaScript, autoplay with sound and pop-ups (allow or
block, applied before each page loads), per-site zoom (remembered when you
zoom, shared by the site's tabs) and clearing them. Stored per profile; incognito
in memory only.

**Faster restarts and closing — November 3, 2026.** Restored tabs load when
first selected (a `steed:memory` setting; Performance mode loads all), each
tab's scroll position is saved with the session and restored, and an unloaded
tab's position is kept for the session too. Closing hides the window before
page cleanup (90 ms instead of about 1 s on screen), and agent/app services are
imported on first use (startup imports 130 → 98 ms). On screen, an 11-tab
restart reached its first page 8% sooner with 12% less memory. The memory help
no longer says unloading loses typed text: Chromium keeps it and the scroll
position. Timing check: restart-with-tabs and close-until-hidden figures,
`--load-all-tabs`.

**Engine boundary finished, more timings — November 3, 2026.** Page
settings, private and stored profiles, the cookie policy and engine versions now
go through `webengine.py` too; the 14 files still importing Qt WebEngine are
page/view subclasses, scheme handlers, interceptors and permission requests.
`tests/timing_check.py` adds scrolling frame times, waking a frozen tab and
reloading an unloaded one, and warns on a busy computer. The quiet on-screen run
is recorded in DOCUMENTATION.md (Timing check).

**Engine & features page and codec warning — November 3, 2026.**
`steed:features` (View → Engine & Features…) shows the engine and Chromium
versions, the codec build state, and 44 web features tested in a real https
page, each missing one explained (newer engine needed, absent from Qt
WebEngine, or the codec build). A non-modal startup warning appears when pip
has replaced the codec build, with a per-version "don't remind me".

**Video codecs, timing check and engine boundary — November 3, 2026.**
`build_codecs.py` builds Qt WebEngine 6.11.2 with H.264/AAC and swaps it into
`.venv` (backup, version and exported-function checks, `restore`, pip-undo
detection); `PyQt6-WebEngine-Qt6` is pinned to the built version.
`tests/timing_check.py` reports startup, first page, new tab, tab switch,
typing, memory (1 and 10 tabs) and shutdown, with `--save`/`--compare`.
`webengine.py` is now the engine boundary: tab lifecycle uses Steed's own
states there, as do script running, page actions, find, steed:// failures and
view lookup; 9 more files no longer import Qt WebEngine, and
`tests/test_engine_boundary.py` keeps the remaining 14 from growing. Open: the
build itself (waits on the system build packages), a codec-build timing
comparison, and moving settings, permissions and profile creation behind the
boundary.

**Site permission prompts no longer hang — November 3, 2026.** Asking about a
website permission (clipboard, camera, notifications, screen sharing) froze
Steed: the HTML question opened inside the engine's own callback, which waits
until it returns, so the dialog could never load. Questions are now asked just
after the callback, with a copy of the request. A live regression clicks a
clipboard copy button and answers both ways.

**Password follow-ups — November 3, 2026.** Security-key sign-ins (WebAuthn
via Qt's webAuthUxRequested: account choice, PIN entry/setting, touch prompt,
failure reasons with retry); platform and synced passkeys remain unavailable
in Qt WebEngine on Linux. Keep This Tab Loaded is saved with sessions and
closed tabs. A one-time prompt removes old KeePassXC pairing files. Open: a
desktop check with a real security key.

**Panel apps — November 3, 2026.** `steed:apps` creates, edits and imports
per-profile HTML/JavaScript apps shown in a new App Panel dock. Each runs at its
own `steed://app-<id>/` origin (separate storage), uses the same data API as the
New Tab page under the Local pages access settings (Ask answers are per app),
opens links in tabs, and cannot be loaded by websites. Open: agent-proposed
panel apps through the reviewed start-page proposal flow.

**Built-in password manager — November 1, 2026.** KeePassXC is replaced by a
per-profile encrypted vault (SecretBox; key in the Secret Service keyring, or an
Argon2id master password without one). Steed offers to save or update logins
after a user-initiated submit (navigation or the form disappearing), fills the
top page's most recent login on load, and fills embedded frames (by their own
site) and one-time codes on request; the key menu fills, copies, shows codes and
generates passwords. `steed:passwords` manages logins, the never-save list, CSV
import (KeePassXC, Chrome, Firefox, Bitwarden) and export. A security review's
eight findings are fixed. Open: a desktop check on real sign-ins (multi-step
logins, sites that block autofill); passkeys.

**Access levels — October 31, 2026.** `steed:access` sets per-capability
Never/Ask/Always policies with Nothing/Ask/Everything presets for agents (seven
capabilities mapped onto grant actions; Never refuses with `access_blocked`,
all-Always auto-approves) and for local pages. Custom New Tab/Welcome pages can
now build live dashboards through same-origin `steed://newtab/api/{fetch,tabs,
history,bookmarks}` (cookieless fetch, per-session prompts for Ask). Open: the
same access settings for side-panel HTML apps once those exist.

**Load-tolerant tests — October 31, 2026.** Intermittent full-suite failures all
came from short waits (2–15 s) on a heavily loaded machine. Test waits now share
`STEED_TEST_WAIT` (30 s, returns early when the condition holds); worker waits
cover the worker's own 20 s limit; the CPU-budget test's wall limit no longer
races the CPU limit; the narrow-layout test waits for the rendered state; and
native file selection retries its focused-control lookup across protocol round
trips. Verified: the full suite passes with all cores saturated (load ~27).

**Navigation and sign-in fixes — October 31, 2026.** Back/forward buttons were
only refreshed on tab switches, so they stayed disabled after following links;
they now follow every navigation, and Alt+Left/Right and mouse side buttons work.
Sign-ins: session cookies persist while tabs are restored; the default user agent
drops the QtWebEngine token so it matches the engine's client hints, and Settings
warns about overrides claiming another Chrome version (the reporter's override
claimed Chrome 137 on a Chromium 140 engine). Incognito was one in-memory profile
for the whole run; it is now discarded once no window uses it. Local storage was
verified to persist across restarts and profile switches. Follow-up: Discord still signed out
after profile switches and tab sleep. Steed deleted or discarded pages without
running unload handlers, and Discord restores its token to local storage only on
beforeunload; pages now run beforeunload/pagehide/unload (1.5 s limit) before tab
close, window close, profile switch and unloading. A setting (Let sites save
before closing, on by default) can turn this off. Closing a window's last tab now
closes the window instead of being refused. Open: confirm on real Discord.

**Memory modes and cookie inspection — October 30, 2026.** `steed:memory`
offers Memory saver, Balanced and Performance presets over seven individual
settings: cookie inspector sleep and wake-up spacing, freezing and unloading
background tabs (respecting Qt's recommended state and keeping audible, agent,
DevTools, loading and `steed:` tabs), native interface, palette keep/preload and
returning freed memory, plus live usage and Free memory now. Cookie inspection:
the window's cookie manager no longer forces an immediate inspection on every
change (the inspector already coalesces them), and saver mode keeps inspectors
asleep between 20-second wake-ups (0 awake after activity vs 2). Measured session:
~350 / ~520 / ~575 MB. Follow-up: frozen/unloaded tabs are dimmed in both tab strips,
**Keep This Tab Loaded** exempts a tab, and an automatic switch to Memory saver
runs below 10% available system memory (back above 20%). Open: a desktop check of
tab freezing/unloading on real sites (media, chat, forms).

**Security and memory review — October 30, 2026.** An independent review of the
recent agent work found no high-severity issues; all four findings are fixed:
mapped frame clicks refuse transformed/zoomed frames and size mismatches, a
cross-origin frame is routed only when exactly one engine frame has its URL,
keys to frame controls require the frame to hold page focus, and incognito
downloads cannot be added to the library. Memory: each HTML interface surface is
its own ~12 MB renderer; the palette now frees its renderer after a minute
hidden, and a new **Lighter native interface** setting swaps the tab strip,
right-click menus, palette and dialogs for Qt widgets (two windows: 5 → 2
renderers, ~390 → ~350 MB). Lighter DevTools entry points for cookie inspection
were tried and rejected: only the full frontend keeps its connection. Open: the
cookie inspectors still cost ~60 MB per profile while awake during browsing. A tab/window churn test showed no
leak (stable page/profile/view counts, flat after the first round); freed heap kept by
glibc is now returned five seconds after closes (~320 → ~230 MB).

**Agent page coverage completed — October 30, 2026.** Live sessions act inside
approved cross-origin frames (checks and actions run in the frame; pointer
actions are mapped through the frame element and limited to frames directly in
the page; submission and uploads stay main-document only). `page.act` gains
`hover` and `drag` (pointer libraries and native HTML5 drag-and-drop), and
`page.scroll` scrolls the container around a target. This closes the native
pointer-gesture and frame items in A3. Deliberately not done: reading the
engine's accessibility tree (inspection runs in an isolated script world whose
nodes have no DevTools node ids to join on; the DOM-derived roles and names
cover the common cases), file drops, and script-handled or child-frame form
submission in restricted mode (blocked on engine support, see A3). Still the
user's decision: A5 search/classification providers, cloud-agent pairing and
live host-folder mounting.

**Harder controls and library discovery — October 30, 2026.** Focusable custom
sliders and spin buttons accept `adjust` (arrow keys through the native keyboard
path, reporting the resulting value); custom comboboxes join pointer activation.
`library.search` can match text inside text files with snippets and order by
most recent change (library schema v2 adds modification times, migrated in
place). Downloads offer **Add to Library…**. Open: actionable cross-origin
frames (their controls live in a separate frame context, and pointer clicks
would need frame-to-page coordinates), and reading the engine's own
accessibility tree (needs mapping its nodes back to inspected targets).

**Website understanding and agent reconnects — October 30, 2026.** Targets
report the role their native element exposes (searchbox, combobox, listbox,
spinbutton, slider, …), `aria-checked`, popup and busy states, and custom slider
positions; tree items join pointer activation. `page.wait` gains `changed`,
compared with the agent's last inspection so an effect that landed before the
wait still matches. The MCP adapter now reconnects on the next call after a
lost connection and resumes the task with its stored token, never replaying the
failed call or calls queued behind it; a stdio client can start before Steed.
The sidebar shows a disconnected task's reconnect deadline. Open: reading the
engine's own accessibility tree (still `accessibility_tree_not_inspected`),
actions for custom sliders/comboboxes, actionable cross-origin frames, and a
desktop check with a real Claude Code/Codex client across a Steed restart.

**Memory and startup cost — October 29, 2026.** Measured one window (PSS over
the process tree): 316 MB with 6 renderers at startup. The window itself uses only
two web views; four renderers were the hidden cookie-inspection page and DevTools
frontend for the global and current profiles. These now sleep after 30 s without
cookie activity and wake on the next write or viewer refresh: idle drops to
198 MB with 2 renderers (−37%); a write wakes only its profile (267 MB, 4).
Menus already show content in about 40–50 ms and the palette is instant after
its first open, so preloading was not added; sharing one engine profile across
Steed's HTML surfaces would save only small main-process overhead and is
deferred. Open: desktop check that cookie sharing still feels immediate after
an idle period.

**Review fixes — October 28, 2026.** A full-suite failure exposed a real race:
under load Qt applied the interceptor block only after a 307 redirect had resent
an uploaded POST body (reproduced 3/20 runs). Restricted task pages now refuse
main-frame redirects after an authorized POST in the navigation gate, which runs
before the redirect is followed; results report `redirect_not_followed` (0/30
under the same load). 303 post-redirect-get is therefore no longer followed in
restricted mode; live delegation is unchanged. Also fixed: typed POST
reads paginating a `{destination}` resource could never connect; the Settings
search default disagreed with `config.py`; panel approval committed partway through
its transaction; a recipe test waited on a nonexistent state. Grant expiry and
exhausted budgets now end only the grant, not the task, so long agent chats can
request fresh access; user revocation still cancels the task. The subresource race
(an in-scope image redirect reached an out-of-scope origin in 5/30 loaded runs)
is fixed with a scope-only CSP on restricted pages, enforced by Blink before each
redirect hop: 30/30 under the same load. DevTools Fetch was tried first and does
not hold or rewrite requests on Qt 6.11.2.

**Tabs and agents, round 2 — October 29, 2026.** Pinned tabs; per-window
sessions (restart reopens every window, a second window of an open profile
starts fresh) with Move Tab to New Window/Window, drag between strips and
tear-off; side tabs resize by dragging. Agents can click ARIA custom widgets on
live pages after a twice-checked visible, unobscured, stable hit test; snapshots
report scroll extent and `more_content_below`, and `page.wait` gains `settled`
for lazily loaded feeds; external MCP agents can block in `conversation.wait`
until the user's next message instead of polling. Moving a tab reopens its
address (since October 29: the live page moves, except Steed pages, agent-shared tabs and tabs with DevTools).

**Startup crash fix — October 29, 2026.** An intermittent segmentation fault
(seen in tests on main) was Chromium adding `LIBVA_MESSAGING_LEVEL` to the
process environment from a worker thread while another engine thread read the
environment for fontconfig. Steed now defines that variable and Qt's
`force_s3tc_enable` before creating QApplication, so engine threads never grow
the environment. Before: crashes in roughly 1 of 7–37 runs of one window test;
after: 0 of 60.

**Page outline for agents — October 29, 2026.** `page.inspect` snapshots now
include a bounded per-frame outline (headings, landmarks, tables, lists, dialogs,
alerts/status, disclosures, tab lists, labelled images) and ARIA states on
targets, under the existing read permission. Still open: actions on custom
widgets (tabs, menus, switches) and open-shadow controls, virtualized lists,
cross-origin frames and the browser's native accessibility tree.

**Developer Tools — October 29, 2026.** F12/Ctrl+Shift+I, Ctrl+Shift+J and
Ctrl+Shift+C open Chromium DevTools per tab, docked right, bottom or in a separate
window, plus Inspect in the page menu. The address bar now keeps the user's edit
and its undo/redo history when the page changes URL; in-page redo (Ctrl+Shift+Z,
Ctrl+Y) was verified working.

**Customizable tab strip — October 29, 2026.** The Qt tab bar is replaced by
an HTML strip configured in Layout Settings → Tabs: top/bottom/left/right
placement, five styles, size, accent, close-button and new-tab-button placement,
shrink/scroll/wrap overflow, manual or per-site grouping, site icons and a tab
count. Portable layouts gain version 3 with a strict `tabs` section; stored
layouts stay version 2 and older documents get defaults. Tabs drag to reorder,
middle-click closes, the strip is keyboard navigable, and the right-click menu
adds Duplicate, Mute, Copy Link Address, group actions, Close Other Tabs, Close
Tabs to the Right and Reopen Closed Tab; group chips have their own menu. Not
yet: pinned tabs, dragging tabs between windows, and resizing side tabs by drag.

**Claude Code chats — October 29, 2026.** Claude Code is a second native chat
agent beside Codex, chosen per chat in the sidebar. Steed drives `claude -p`
stream-json with a bound MCP server, streams replies, maps native permission
prompts and questions to Steed's request popup, and verifies the MCP connection
before the first message. Offline fixture tests plus one live Haiku check passed.
HTTP agent sessions now idle out after 30 minutes (configurable) instead of 60 s,
and a second launch opens a window or links in the running browser.

**Review findings, second pass — October 28, 2026.** Fixed: a failed automatic
approval (saved rule, account access or YOLO) disabled every task instead of
leaving the request pending; Codex chat messages sent between the gateway
connection and the Codex handshake were dropped; a profile named
`global_profile` disappeared from the profile list; closed pop-out windows
stayed allocated until their opener closed; recipe docs listed versions 1–7.
Verified as intended and kept: global saved approvals apply to every profile;
Clear All Cookies blocks inherited global cookies until re-enabled per site; a
corrupt `retired_profiles.json` stops startup; live-tab delegation validates
before retiring the old grant. Open: HTTP MCP sessions expire after 60 s idle
(cancelling their task) and a second launch shows a warning instead of opening
a window in the running browser.

**Website editor coverage — implemented and accepted.** Inspection now
reports rich-editor boundaries/labels without returning their contents or nested
controls. Explicit main-frame document-root editors on live pages support bounded literal
entry through Chromium editing, with multiline text, trusted input and undo. Read/
edit grants, individual live fingerprints, focus checks and existing action
invalidation remain required. Restricted/child-frame/shadow editors stay observation-only;
partial and zero-action coverage carries explicit fallback guidance. **91 focused
checks / 46.378 s** passed (`/tmp/steed-live-editors-final-focus.log`). Broader shadow
actions and frame/accessibility coverage remain.
The final follow-up restores discovery inside static `contenteditable="false"`
wrappers while keeping nested private editor islands and references omitted.
Unsupported-control, readonly/hidden and redirected-focus failures now explain the
actual problem instead of suggesting that another grant will add missing support.
Full acceptance: **1,543 regressions / 411.981 s**, plus 93 focused final editor/
inspection/action/live checks. User Undo is tested after Take Back, activating the
window and focusing the editor; one engine event turn is not a user-focus contract.
Real entry-point startup/local render/shutdown and independent SDK/raw stdio plus
authenticated HTTP smoke passed. Ruff lint/format (270
Python files), dependencies and whitespace passed. Logs:
`/tmp/steed-live-editors-{accepted,final,startup,stdio,http}.log`. The initial full
run's Undo-test failure is retained in `/tmp/steed-live-editors-full.log`; final
acceptance is the clean serial run. Signed-in desktop/provider checks remain manual.

**Chatbot-to-agent relay — both options implemented.**
The owner chose manual pasted JSON and an explicitly enabled chat tab, with
structured messages in both directions. The maintained HTML review prepares the
goal/tool catalogue, then returns structured results/errors with fresh exchange
IDs. Tab mode reads only selected response text and pastes the exact reviewed
outbound message through native editing; the user presses Send. Whole-batch schema
validation, task-bound guarded dispatch, consumed IDs and metadata-only relay
events preserve existing approval/receipt controls. Errors stop later calls; no
uncertain operation is retried. Task/profile/source revocation hides unshared
buffers; first-conversation same-origin rebinding requires an explicit click.
Subsequent navigation ends the task. Input is bounded to 64 KiB/four calls, output
to 1 MiB, 32 exchanges per task. Text transport excludes screenshot/pixel tools
and automatic attachments. See **Structured chatbot relay** in DOCUMENTATION.md.
Remaining: signed-in provider/manual desktop acceptance, provider-specific
response identification/automatic exchange only with separately reviewed scope,
and image-capable transport. These are not claimed by the selected-response path.
Full regression acceptance: **1,579 tests / 547.384 s**. A follow-up preserves
specific permission error codes/fields, adds structured validation feedback and
validates escaped Unicode; **75 final relay/chat/gateway checks / 20.612 s** passed.
Real entry-point startup/local render/shutdown and independent SDK/raw MCP journeys
over stdio and authenticated HTTP passed. Ruff lint/format (276 Python files),
dependencies and whitespace passed. Synthetic HTML setup/exchange previews were
inspected; real-account provider acceptance remains manual. Logs:
`/tmp/steed-relay-{full,final,startup,stdio,http,preview-final}.log`. Both directions
are exact reviewed JSON; exported messages do not prove chatbot activity.
The final destination/status text polish passed **11 HTML relay checks / 7.029 s**
(`/tmp/steed-relay-final-ui.log`).

**Reviewed conversation recovery and handoff — implemented and accepted.**
**Continue / hand off** previews one new instruction, with optional editable recent
history (unchecked by default). It can copy for website/personal agents, send to
another connected external task, or start a fresh Codex chat and deliver once after
its verified connection. Original history/drafts remain available; old permissions,
queued prompts and uncertain actions are never automatically restored/replayed.
Close during startup cancels only the newly created empty task; private/profile,
source selection and sidebar reload invalidate the review. Failed startup remains
explanatory; uncertain delivery disables another send. Text is bounded, UTF-8-safe
and fully previewed. Codex threads remain ephemeral; persistent native-thread
resumption and cloud pairing remain separate architecture decisions.
Validation so far: **71 agent/runtime/HTML checks / 27.267 s**, plus **9 focused
recovery checks / 21.067 s**, including storage-failure cleanup. Logs:
`/tmp/steed-agent-recovery-{final-focus,storage}.log`.

Full acceptance: **1,534 regressions / 332.054 s**. Real entry-point startup/local
render/shutdown passed with Qt 6.11.2. Independent SDK/raw stdio and authenticated
HTTP acceptance passed. **13 final health/recovery checks / 21.361 s** passed after
status-color and explanatory-text polish. Log:
`/tmp/steed-agent-reliability-final-controls.log`. Ruff
lint/format (269 Python files), dependencies and whitespace passed. Full/smoke logs:
`/tmp/steed-agent-reliability-{full,startup,stdio,http}.log`. Synthetic health and
review previews were inspected at `/tmp/steed-agent-{health,recovery}-preview.png`.
Two standalone preview attempts encountered the known offscreen Qt profile-init
crash; the successful retry is `/tmp/steed-agent-recovery-preview-final.log`, with
the original logs preserved. This milestone does not claim to resolve that Qt
issue. Signed-in desktop/live-provider acceptance remains manual. Next: website
action coverage and concrete file-library friction; persistent runtime resume,
cloud pairing and the previously deferred architecture work remain separate.

**Agent connection health and delivery — implemented.** The sidebar now separates
transport health, runtime activity and message-read facts; generic MCP reads never
imply thinking, work or completion. Managed queues distinguish pending acceptance
from follow-ups. Pending approvals are prominent, offline/ended conversations keep
editable local drafts, and reconnect/interface reload never replays sends. Real
gateway validation/execution failures notify the UI and expose task-scoped,
sanitized diagnostics with permission/receipt recovery links and local dismissal.
The existing 88 sidebar/runtime/gateway/HTTP checks passed. Dedicated live-HTML and
gateway checks are in `test_agent_health.py`; logs are
`/tmp/steed-agent-health-{baseline,live,final}.log`. Reviewed continuations are
implemented in the subsequent checkpoint above.

**HTML website-chat controls and compact navigation — implemented and accepted.**
Website-chat sidebars now use the maintained HTML
presenter for address/profile status and handoff controls. The original website
engine, session and unsent draft survive sidebar/tab moves; controls receive no
conversation content. Navigation/replacement/reload rotates control identities;
profile/window cleanup clears state and disposes the offline control engine.
Loading percentage and failure state are explicit, including a previously failed
page moved into the dock. Management pages use a compact page dropdown below
780 CSS pixels; editor tabs support arrows/Home/End with disabled sections skipped
and focus retained. Empty panels hide unused selectors. Root CSS exposes the new
compact navigation/sidebar selectors. Embedded Escape/close dismisses reusable
sidebars, and identical cookie snapshots retain row identities/selection while
changed data clears selection. Background panel updates preserve unsent form text
and focus.

Acceptance: **1,521 full regressions / 373.148 s**, plus **46 final HTML checks /
26.002 s** covering reusable native close/HTML controls and focus preservation.
Startup/local rendering/shutdown and SDK/raw external-client journeys over stdio
and authenticated HTTP passed, including approval, takeover/return, conversations,
linked panels and reviewed page/theme proposals. Ruff lint/format (265 Python
files), dependencies and whitespace passed. Logs:
`/tmp/steed-html-continuation-{accepted,startup,mcp-stdio,mcp-http,final-controls}.log`.
Inspected synthetic previews: `/tmp/steed-html-{chat,narrow}-preview.png` and
`/tmp/steed-html-panels-preview.png`. The initial full run exposed a redundant
cookie-row rebuild and an unsynchronized viewport-resize test; the accepted run
includes stable cookie rows and a wait for actual Chromium viewport changes.
Normal signed-in desktop acceptance remains manual. Next: use these interfaces in
daily workflows and address concrete findings; the broader authored-page data
bridge, Qt replacement, Google production registration and password improvements
remain deferred.

**HTML data-panel tab and sidebar — implemented.** `steed:panels` and the compact
sidebar share the existing panel controller, root theme, image previews, forms,
instruction reviews and manual linked-source refresh. The sidebar can open the
full page and reopen after dismissal. Source/profile changes conceal stale pixels
synchronously and scrub disabled linked contents; private pages do not query saved
panels. Tab and dock disposal release presentation pages before engine profiles.
Validation: **106 targeted regressions / 35.699 s**, plus dedicated live-HTML
checks for literal contents, revocation, profile isolation, reopening, form preview
and explicit delivery. Logs: `/tmp/steed-panels-milestone.log` and
`/tmp/steed-panels-html-final.log`. Website-chat controls and responsive/keyboard
polish are covered by the continuation above; the broader data bridge remains deferred.

**HTML management pages and browser dialogs — owner-requested continuation
(implemented and accepted).** The owner expanded the restyle to all
browser management popups. Native menu interactions are retained, with commands
opening reusable HTML tabs. Fifteen new `steed:` routes cover cookies, downloads,
bookmarks, history, files, connections, tasks, passwords, layout, start-page
customization, groups, website permissions, agent setup, ad blocking and the
service catalog. The rail groups browsing/customization/agents and labels shared
versus profile-local management. Tables page beyond 500 rows; hidden pages suspend
presentation polling and refresh on return. Existing private/profile rules,
backend operations and exact permission reviews remain authoritative.

Transient browser messages, text/choice inputs, website/media requests, agent
questions, recipe/layout/package/page/panel reviews and sharing previews now have
HTML presentation too. Live task views retain their separate engine under HTML
controls. Image previews are bounded raster data; sensitive review changes cover
stale pixels before publishing replacement state. Website-chat handoff hides its
presenter before native paste and scrubs the draft on close/profile change. Layout
preview cancellation, password-source rebinding/return, failed-backend reload and
internal-page delegation guards are covered by regressions. Native OS pickers,
startup failure handling and emergency style recovery remain available.

Acceptance: **1,508 full regressions / 355.449 s**, plus **16 real-management
checks / 14.517 s** covering actual cookie arrival/deletion, all fifteen routes,
HTML permission decisions, secret masking, pagination, image clearing, keyboard
opening, private/profile cleanup, password return, background refresh and backend
retry. The web-chat fixes passed 80 targeted checks. Ruff/formatting (263 Python
files), dependencies and whitespace passed. Inspected management/approval previews
are `/tmp/steed-management-*.png`. Logs:
`/tmp/steed-management-{release-checks,live-checks,final-focus,preview}.log`.
Real startup/local render/shutdown and both SDK/raw stdio + authenticated HTTP
MCP smoke journeys passed. These include approval, live takeover/return, normal
browsing, conversations, linked panels and exact reviewed page/theme changes.
Smoke logs: `/tmp/steed-management-{startup,mcp-stdio,mcp-http}.log`.
The subsequent HTML panel and compact-sidebar checkpoints above complete that
presentation work. The broader
agent-authored data bridge and replacing Qt remain deferred; this milestone keeps
existing Qt controllers behind maintained HTML.

**New Tab HTML menus, palettes and placement — owner-requested continuation
(implemented and accepted).** The desktop top menu and toolbar
menu retain native behavior, as clarified by the owner. The maintained New Tab
menu opens HTML category dropdowns and settings editors beside their trigger;
long hover opens editors/submenus without running ordinary commands. Each
category has **Open in Settings**, opening its matching `steed:settings` section.
Popups follow root styling, account for zoom and screen bounds, and release their
pages before profiles. Native security reviews and OS pickers are retained.

`steed:style` adds fourteen editable palette colors and a CSS reference also
published to external agents. Strict, reviewed `page=style`/`mode=theme` proposals
reuse existing start-page receipts; the owner chooses the profile and optional
all-profile synchronization. Existing-data grants do not approve styling, and
capabilities disclose no current CSS. Old stored styles migrate without losing
preferences. A native **Ctrl+Alt+Shift+R** reset remains usable with bad colors.

The toolbar menu starts at the left edge and joins Layout Settings' reorderable
controls; menu/address remain mandatory. Older stored/portable layouts retain
other controls' relative order. Downloads' Save As picker starts in the system
Downloads folder (fallback `~/Downloads`). Detailed usage is in DOCUMENTATION.md.

Acceptance: **1,487 full regressions / 633.642 s**. Subsequent renderer/editor
fixes passed **141 HTML/workspace checks / 85.929 s** and **112 final checks /
51.502 s**, including all nine adapted settings controllers, actual Layout panel
editing, multiline source editing, render-failure/reload, protected toolbar rows,
Escape/hover dismissal, stale controls, zoom and profile lifetime checks. Earlier
234 focused checks also passed. Ruff/formatting (260 Python files), dependency
and whitespace checks passed. Final screenshots for New Tab, dropdown, editors
and light/dark palette controls were inspected at `/tmp/steed-menu-*.png`.
The real startup/render/shutdown and SDK/raw stdio + authenticated HTTP MCP
journeys passed, including reviewed agent theme proposals and exact retries.
Logs: `/tmp/steed-menu-{acceptance,final-render,verified,preview,startup,mcp-stdio,mcp-http}.log`. The known
intermittent headless Qt/Vulkan initialization failure remains tracked separately;
actual desktop checks remain separate from offscreen acceptance.
Next: continue dedicated HTML collection screens and daily-use polish in bounded
milestones. Preserve the owner's native desktop-menu behavior. Agent-authored
read-only data bridges and replacing Qt remain deferred architecture work.

**HTML pages and appearance — owner-requested continuation (implemented and
accepted).** `steed:settings`, `steed:agents` and `steed:style` expose
maintained HTML workspaces as tabs, alongside New Tab/Welcome. A shared root
stylesheet and profile identity-scoped overrides support themes, accent/CSS,
menu/status visibility, a New Tab menu and configurable estimated loading progress.
Apply-all replaces appearance overrides atomically, including defaults for future
profiles; private edits stay in memory. Images now cover the New Tab background.
Hidden menus retain keyboard shortcuts and the mandatory address-bar menu button;
native Reset Page Style recovers from unusable CSS. Maintained workspaces cannot
be delegated as website tabs, including during a pending navigation. Custom page
HTML retains its existing isolation and reviewed editing workflow.

Acceptance: **1,466 full regressions / 532.260 s**, plus **162 focused checks /
106.133 s** covering real HTML, profile scope, keyboard access, loading progress,
source isolation, conflicts and storage failures. Startup/render/shutdown and
SDK/raw stdio + authenticated HTTP MCP journeys passed. Ruff (256 Python files),
formatting, dependency and whitespace checks passed. Light/dark Style, cover
background and loading-bar previews were inspected at `/tmp/steed-pages-*.png`.
Logs: `/tmp/steed-pages-{acceptance-final,final-focused,startup,mcp-stdio,mcp-http}.log`.
All fixtures used temporary profiles and synthetic/local data; actual desktop
checks remain separate, and the known headless Qt initialization issue remains
tracked in H2 below.
Next remains H3 history/bookmarks/downloads/file-library HTML screens; Qt replacement
and the deferred authored-app data bridge are not part of this milestone.

**HTML interface H2 — settings and profiles (implemented and accepted).**
General Settings and the profile create/switch/pop-out/rename/archive menu routes
now open one reusable nonmodal HTML workspace. Sections follow General, Profile,
Tabs, View, Cookies, Agents. Existing preferences have explicit Save/Cancel;
appearance previews only inside settings until Save, which updates open windows.
Per-key conflict checks merge independent edits and stop conflicting saves.
Reload preserves the preference draft and discards pending profile reviews.

Profile actions remain immediate and separate from preference saving. Rename and
recoverable archive use one-use visible reviews pinned to the selected directory;
built-in, active, agent-used and startup-profile restrictions have explanations.
Usage notifications update other workspaces without changing task authority.

Real HTML acceptance exposed delayed Chromium storage writes recreating retired
profile directories. `services/profile_storage.py` preserves existing legacy
stores in place, records retired names and gives fresh profile identities unique
engine storage/cache subdirectories. Late-write remnants remain recoverable and
are not offered as active profiles. Explicit name reuse preserves remnants and
uses a fresh engine path; stale cookie/snapshot callbacks cannot join it. Keep
the per-profile `.steed_profile.json` with complete backups/restores.

Acceptance: **1,441 full regressions in 469.217 seconds**, then **36 final
HTML/storage checks in 27.226 seconds** including the small-desktop sizing guard.
Startup/render/shutdown, two-process cookie write/read, SDK/raw stdio and
authenticated HTTP MCP journeys, Ruff (252 files), dependency and whitespace
checks passed. Settings/profile light, dark, narrow and review previews were
inspected in `/tmp/steed-h2-*.png`. Logs:
`/tmp/steed-h2-{acceptance,final-ui,startup,persistence-write,persistence-read,mcp-stdio,mcp-http}.log`.
All account/profile fixtures were synthetic/temporary; real desktop checks remain
separate. One standalone persistence attempt reproduced the pre-existing offscreen
Qt profile-constructor crash before test setup; its log is
`/tmp/steed-h2-persistence-startup-crash.log`. The fixture now sets the application's
shared-OpenGL-context attribute; subsequent persistence and transports passed.
This does not establish a production fix for the known initialization instability.

**HTML interface H1 — agent workspace (implemented and accepted).**
The owner approved beginning an HTML restyle after the architecture review.
The conversation sidebar and `steed:agents` now share one maintained local HTML
surface, with light/dark tokens, separate sender cards, a fixed composer, clearer
runtime model/activity indicators and collapsed conversation settings. The same
page moves between sidebar and tab, preserving drafts, selection and UI state.
Connection setup, exact access reviews and task details still use their existing
trusted dialogs. Generic MCP and website-chat workflows remain supported.

`ui/html_surface.py` owns an off-the-record engine profile, an allowlisted command
bridge, nonce-based content policy, network/navigation denial and a native reload
fallback. Python retains service authority. Conversation-context and document-
generation checks reject stale commands; draft revisions protect newer typing,
and Send waits for a result before another submission. Private-profile changes
cover stale pixels immediately and clear messages, choices and unsent drafts.
Reload restores controller-held drafts without replaying messages or effects.
This is Steed's maintained UI API; the broader agent-built HTML data bridge stays
deferred and user-authored start pages gain no native access.

H1 acceptance covers actual HTML/bridge events, Ctrl+Enter, streaming literal
text, failed/duplicate sends, private transitions, model/activity/YOLO notices,
renderer fallback, local drafts and sidebar/tab continuity. Light/dark sidebar
and full-tab previews are in `/tmp/steed-html-*.png`. Acceptance: **1,415 full
regressions in 486.928 seconds**, followed by **27 final UI checks in 24.882
seconds** covering the final storage-read recovery and connection-guidance fixes.
Startup/render/shutdown, SDK/raw stdio MCP journeys, Ruff (249 Python files),
dependency checks and whitespace checks passed. Logs:
`/tmp/steed-html-{acceptance,final-ui,startup,mcp}.log`. These checks used temporary
profiles/local fixtures; real desktop/account acceptance remains separate.

HTML milestones, keeping the engine and Python services:

1. H2: settings/profile workspace is implemented and accepted above.
2. H3: history/bookmarks/downloads/library screens, preserving source access,
   recoverable deletion, private browsing and native file selection.
3. H4: task/data-panel presentation, retaining reviewed source lifetimes and
   existing approval boundaries. Evaluate browser toolbar/tab-strip migration
   separately after the internal screens are proven.

Qt/Chromium replacement is not part of this restyle. Engine upgrades remain a
separate capability/compatibility decision rather than a styling prerequisite.

**Daily agent reliability — October 13 (implemented and accepted).**
The owner reported generic grant failures, private-browser/takeover friction and
missing website actions, and requested screenshot input plus unsafe automatic
approval. Reproduced ordinary DNS-origin validation failures and repeated-grant
rejection; the gateway previously flattened those exceptions into
`operation_failed`. Named-origin requests now infer the reviewed network flag;
explicit false and exact-origin bounds remain enforced. Maintained error codes,
field hints and corrective messages exclude submitted secrets/exception strings.
Unchanged requests are idempotent; scope changes request replacement while old
access remains usable. Schema 21 separates pending/approved uniqueness and ends
old grants atomically on approved replacement, closing prior task sessions.

Private live sessions now run normal website scripts and embedded/background
traffic in separate off-the-record storage. Existing scoped document retrieval
stays available with explicit restricted mode. Live grants default to live mode;
computer permission includes its required visible browser dependencies. One
agent-requested takeover popup pins the selected profile/tabs/sites. Take Back
returns control; old request IDs report returned and never retake tabs. Private
Watch supports direct user interaction while agent operations are paused.

Native `page.click`, `page.type` and `page.key` use a visible viewport capture:
image-pixel coordinates, one-use capture IDs, 60-second expiry, geometry/lifetime
checks and value-free receipts. They cover canvas/custom/embedded controls when
DOM references are absent; inspection explains missing actions. No arbitrary
caller script, desktop input, or automatic effect replay is added. Five additive
MCP tools bring the catalogue to 85; reconnect agent conversations after restart.

**YOLO mode** is an explicit, warning-confirmed Agents menu option, disabled by
default and after browser restart. Status bar/sidebar mark it. It automatically
approves Steed grants/takeover, worker execution, recipes, account payload reviews
and panel/layout/page proposals for all connected agents/profiles this run.
Validation, ownership, supported APIs, source setup, resource limits and no-replay
remain. Recovery source selection is not guessed; website/host permissions stay
separate. Take Back ends the current handoff, but a fresh request can be approved
while the mode remains on. Disable it or cancel the task to stop new handoffs.

Acceptance: **1,405 full regressions pass in 454.935 seconds** and 46 final
sidebar/approval/takeover/watch checks pass in 17.583 seconds. Both SDK and
independent raw clients pass stdio and authenticated HTTP journeys with private
click/type/key, one-popup takeover/return/no replay, reviewed recipes, document
bundles and retained account tables. Both installed native Codex runtimes pass
using synthetic local model fixtures; startup/render/shutdown and light/dark
private permission, takeover and unsafe-mode previews pass. Ruff lint/format
(247 files), dependency checks, whitespace, 85 client tool routes and all panel/
recipe examples pass. No real accounts, user profiles or external model calls
were used; real desktop/live-provider acceptance remains manual.

The expanded transport fixture now allows its private tasks to precede the main
workflow and has a 120-second whole-journey bound, retaining individual-operation
bounds. Earlier full attempts caught optional profile-config compatibility,
private-live/visual test expectations and native-error wording; these corrections
are verified. One stdio retry reproduced the known offscreen Qt profile-startup
segfault before task setup. Its stack is preserved at
`/tmp/steed-agent-mcp-stdio-native-crash-oct13.log`; the subsequent full transport,
HTTP, native and startup checks pass. Do not claim a production startup fix.
Logs: `/tmp/steed-agent-acceptance-oct13.log`,
`/tmp/steed-agent-final-ui-oct13.log`,
`/tmp/steed-agent-mcp-{stdio,http}-oct13.log`,
`/tmp/steed-agent-native{,-direct}-oct13.log`,
`/tmp/steed-agent-{startup,visual}-oct13.log`. This also finishes the previously
handed-off N5 broad acceptance. Restart Steed and start/reconnect agent chats to
load the new catalogue. The HTML data bridge remains deferred.

**Review and connector runtime checkpoint (integrated on main).** Reviewed
engineering branch `c93748e` and connector PR #3 head `0082a19` in isolated
worktrees, then merged both. The PR review and detailed C6–C10 continuation are
[posted on GitHub](https://github.com/AdvancedForge/steed/pull/3#issuecomment-5983460074).
The PR is confirmed merged at `cfc9425`; the published v8 contract and extended
[contributor milestones](https://github.com/AdvancedForge/steed/pull/3#issuecomment-5983954160)
are posted. 119 packaged-profile/workflow checks, four compatibility checks and contributor
lint/format pass. Real-account acceptance remains unperformed.

**Typed connector reads v7 and continuation v8 (implemented).** Typed bounded
POST inputs, literal/null identity bodies and fixed GraphQL reads now use the
shared runtime and MCP receipt pipeline. Review fixes preserve legacy receipt
fingerprints, enforce POST/body-cursor grammar and use the pinned GraphQL AST
parser for syntax, operation/variable matching and bounds. Commit `ee17331` records
those fixes. Version 8 adds reviewed distinct continuation endpoints, boolean
stop flags, boolean success contracts and resource POST pagination. Ambiguous
read/write POST routes are rejected. Credentials, source/origin/header bounds,
identity rechecks and write approvals remain intact; no automatic write replay.

Acceptance: **155 recipe regressions pass in 24.036 seconds**, including actual
loopback TLS framing and native review. Both SDK/raw stdio and authenticated HTTP
MCP journeys pass typed continuation reads and fixed GraphQL calls with stable
receipts. Logs: `/tmp/steed-continued-post-{recipes,stdio,http}.log`.
Examples: [v7](../../examples/connector_recipes/notes-query.json),
[v8](../../examples/connector_recipes/notes-continuation.json). Provider promotion and
live acceptance remain the connector contributor's follow-up work.

**Manual account-linked sources N3 (implemented and accepted).** Native
panel version 5 binds a text widget to an exact configured account/read tool,
destination and resolved bounded inputs. Proposal/review/view never fetch.
Explicit Refresh uses trusted adapters and their identity, source, credential and
cancellation checks. Account read access and reusable panel/profile permission
are independently required. Task completion preserves panel authority; results
stay in memory and need an explicit new fetch after restart/restoration. Revocation,
disconnect, metadata mismatch, failed reads and shutdown hide contents. Late
responses cannot restore revoked data. Two physical workers and a 16-request queue
bound concurrency; timed-out workers retain their slot until they return.
No new agent refresh/approval/data-reading endpoint, background poll or model call.

**89 combined panel regressions pass in 23.132 seconds**, including 16 account
cases covering disconnect, stale review and shutdown after task completion. Transport acceptance caught fixture assumptions that incorrectly retained a
second panel across shared-account revocation and assumed concurrent HTTP clients
finished in a fixed order. Assertions now follow the last explicit user refresh
and require clearing of the other panel; runtime behavior was correct.
The full suite passes **1,339 tests in 407.066 seconds**. SDK/raw stdio and
authenticated HTTP journeys pass manual account refresh after task completion,
immediate shared-account scrubbing and explicit restoration. Both installed Codex
runtime paths and startup/render/shutdown pass using local fixtures. Light/dark
native review/dock previews, Ruff (234 files), dependencies, whitespace, ten
recipe/six panel-content examples and local documentation targets pass. Logs:
`/tmp/steed-account-panels-{full,stdio-retry,http-retry,native,native-direct,startup}.log`.
No live provider/model acceptance was performed; the earlier headless profile-create
crash remains a separately recorded issue. Example:
[account-report.json](../../examples/data_panels/account-report.json). Rich field mappings
and reusable action controls remain separate work.

**Reusable agent instruction buttons N4 (implemented and accepted).**
Panel v6 adds fixed, bounded native instruction buttons. The creating task may end;
the user selects a currently connected active recipient, reviews the literal
message and explicitly sends. Display-profile identity and panel approval are
rechecked. No automatic linked-content attachment, provider launch, connector
effect or new agent approval/send endpoint. Atomic value-free receipts pin the
recipient and payload, prevent retry/double-click duplicates, and survive message
clearing. Native Codex uses existing delivery; generic MCP clients read their
mailbox; website chatbots retain copy/export/import. Version 1–5 grammars remain
intact. A library parser variable shadowing correction preserves the original
source string without changing stored sources or hashes. Example:
[daily-instructions.json](../../examples/data_panels/daily-instructions.json).
Acceptance: **1,350 full regressions in 441.204 seconds**, 100 combined panel
checks in 28.192 seconds and 11 final instruction checks in 2.536 seconds. Both
SDK/raw MCP stdio and authenticated HTTP clients receive reviewed instructions in
new conversations after panel creators end, without linked-content disclosure.
Both installed Codex runtimes, startup/render/shutdown, light/dark native message
previews, Ruff (236 files), whitespace and ten recipe/seven panel-content examples
pass. An early smoke fixture used Qt `findData` for tuple IDs and selected no panel;
explicit item comparison corrected the fixture. Logs:
`/tmp/steed-panel-instructions-{full,panels,final-focused,stdio-retry,http,native,native-direct,startup}.log`.
All account/model/browser fixtures are synthetic; real desktop/provider acceptance
and the previously captured offscreen profile-create crash remain separate.

**Bookmark organization B1 (implemented and accepted).** Search and folder
filters, stable-ID name/address/folder editing, shared-window refresh and bounded
New Tab previews with Manage all improve ordinary browsing. UTF-8 Netscape HTML
imports/exports are bounded to 8 MiB and 2,000 bookmarks. Imports merge atomically,
keep existing names/folders, report duplicates/invalid links and reject overflow
without partial writes. Nested imported folders become readable path labels;
empty folder trees are not retained. Exports escape text and replace atomically.
Private bookmarks stay memory-only; stale profile dialogs cannot write into a
recreated profile. Built-in Manage all opens native UI; custom HTML has no new
bridge. Acceptance: **1,363 full regressions in 419.348 seconds** and 13 focused bookmark
checks in 2.047 seconds pass. Startup/render/shutdown and light/dark manager/editor
previews pass. Ruff (238 files), formatting and whitespace pass. Existing external
agent/provider contracts are unchanged. Logs: `/tmp/steed-bookmarks-full.log`,
`/tmp/steed-bookmark-workflows-final-focused.log`, `/tmp/steed-bookmarks-{visual,startup}.log`.
No new agent reading/mutation authority is added.

**Explicit account list/table mappings N5 (implemented and accepted).**
Panel v7 projects exact JSON-array/scalar fields from manual account reads into
native lists and up to eight table columns. Proposal/review never fetch. Review
shows the exact mapping; only projected fields remain cached. A 64-row/512-character
cell bound has visible total-row and shortened-cell counts. Empty arrays work;
missing/non-scalar/ambiguous/malformed results clear the widget without raw-JSON
fallback. Immediate revocation scrubs native cells and results. Versions 1–6 keep
their grammars, and v5/6 text remains unchanged. Final targeted acceptance passes
**109 panel regressions in 30.360 seconds**, including timeout/late-response
scrubbing and native empty-table shape. SDK/raw stdio and authenticated HTTP
journeys pass with synthetic fixtures; light/dark native previews and startup
pass. Logs: `/tmp/steed-handoff-all-panels.log`,
`/tmp/steed-panel-mappings-{stdio,http-retry,visual}.log`,
`/tmp/steed-handoff-startup.log`. The October 13 checkpoint above completes full-suite, independent transport and
both installed Codex-runtime acceptance for v7. Example:
[account-table.json](../../examples/data_panels/account-table.json).

**Direct bookmark opening B2 (implemented and accepted).** Manage Bookmarks
opens the filtered selected bookmark in a new tab through **Open** or a
double-click. Editing remains a separate button. Stable profile identity and
current browser profile are checked before opening; a stale manager cannot
navigate after a switch/recreation. No profile is implicitly switched. All
**16 bookmark regressions pass in 3.178 seconds**, including button/double-click
integration and stale-profile refusal. Light/dark previews, startup/render/shutdown,
Ruff lint/format (240 files) and whitespace pass. Logs:
`/tmp/steed-bookmark-open-{final,visual}.log`, `/tmp/steed-handoff-startup.log`.
External-agent connection/recovery polish was assessed as a broader milestone
and is deliberately handed off rather than partially changed.

**Bookmark manager shortcut B3 (implemented and accepted).** Ctrl+Shift+O opens
the current profile's bookmark manager. Manage Bookmarks now lives in Tabs beside
Bookmark This Page, matching the documented category. A synthetic Qt keyboard
check verifies shortcut delivery from the address field and the menu route;
**17 existing bookmark/menu regressions pass in 2.732 seconds**. Ruff lint/format
and whitespace pass. Logs: `/tmp/steed-bookmark-shortcut-{check,focused}.log`.
This is the owner's requested additional small task; the handoff priorities below
are unchanged. No full-suite or new native-agent acceptance is claimed.

**Bookmark search keyboard opening B4 (implemented and accepted).** Enter in
the search field opens the first matching bookmark in a new tab, respecting the
current folder filter and existing profile guards. No match leaves the manager
open; Close is not an implicit Enter action. The search tooltip explains the
shortcut. **18 bookmark regressions pass in 2.718 seconds**, including actual Qt
Return events for matching and empty results. Ruff lint/format and whitespace
pass. Log: `/tmp/steed-bookmark-search-enter.log`. Handoff priorities remain
unchanged; no full-suite or native-agent acceptance is claimed for this increment.

**Bookmark result keyboard selection B5 (implemented and accepted).** Focus
the results list, select a match with the arrow keys, then press Return or keypad
Enter to open that selected link. Shortcuts are scoped to the list, preserving
the search field's first-match behavior and existing double-click handling.
**19 bookmark regressions pass in 3.016 seconds**, including both Enter keys,
selection of the second matching result and exactly one new-tab request per key.
Ruff lint/format and whitespace pass. Log: `/tmp/steed-bookmark-results-enter.log`.
The incoming engineer's broader acceptance and agent-recovery priorities remain
unchanged.

**Downloads search and path copying (implemented and accepted).** The owner's
last two small tasks are outside bookmarks: case-insensitive literal filtering
by filename/profile/destination, and Copy Path for the selected record's full
destination. Filtering and progress updates keep controls bound to stable
download IDs; a hidden or removed selection cannot redirect Cancel or clipboard
actions. Copy Path does not open the file or require it to exist. Enter in search
cannot implicitly invoke Pause or close the dialog. Five focused checks pass in
0.075 seconds; two existing real local-download/menu checks pass in 1.457 seconds.
Light/dark native previews, Ruff lint/format (241 files) and whitespace pass. Logs:
`/tmp/steed-download-small-tasks.log`, `/tmp/steed-download-browser-checks.log`.
The handoff priorities below are unchanged; no broader engineering is started.

**Next daily-use chunks, in order:**

1. Check the corrected workflows on real desktop sites and accounts, using the
   documented private-live, takeover and screenshot controls; capture remaining
   failures as concrete, value-free diagnostics. Synthetic acceptance is complete.
2. Improve external-agent connection health, queued/read/working indicators and
   interrupted-task recovery. Keep generic MCP and website-chat workflows clear;
   automatic delivery must reflect what the connected client actually supports.
3. Address browser reliability and file-library friction found during those
   workflows: session restoration, profile switching, imports and startup/shutdown.
   Investigate the recorded offscreen Qt startup crash separately; distinguish
   automated checks from real desktop acceptance.

**Reviewed HTML data bridge — owner approved, deferred priority.** The owner chose
a reviewed, read-only bridge for future agent-built HTML/JavaScript dashboards,
then explicitly kept it below daily-use engineering. This is a backlog direction,
not the next implementation milestone. Before building it, define app-specific
source permissions, installation/update/removal lifecycle, revocation and private
data/navigation boundaries. Editable-page installation and existing native-panel
grants must not implicitly authorize an HTML app. Writes, sends and broader
capabilities need separate decisions. Existing custom pages remain isolated.

Cloud pairing, credential-derived headers and live-account acceptance remain
separate. Existing native forms stay bound to their owning task; instructions
are reusable actions. Password-manager improvements and shared Google
registration remain deferred as previously requested.

**Reusable linked library sources N3 (implemented and accepted).**
Native panel v4 binds text/list/table widgets to exact enabled library files.
A separate explicit native checkbox grants read access to that panel/display
profile after task end; Data Panels exposes Allow/Disable linked reads and Refresh.
Folder or panel revocation synchronously scrubs linked content in docks and open
reviews; restoring access permits refresh. Missing/deleted/corrupt/oversized or
malformed files fail closed, with per-widget unavailable labels. Saved reports
retain their existing behavior. Local reads are bounded to 16 KiB and verify
revision/digest; no new agent data-reading/approval endpoint or host mount exists.
Task schema 20 migrates a separate source-permission table without changing old
receipts. Linked paths cannot be retargeted through saved-draft publication.

The full suite passes **1,320 tests in 399.325 seconds**. Thirteen final focused
regressions pass in 4.018 seconds, including added persistence-failure and native
list/table scrubbing and stale confirmation checks: authority separation, active/restarted
panels, immediate scrubbing, profile rename/recreation, source failures, agent
refusal, audit/export containment and saved reports. Updated stdio and authenticated HTTP SDK/raw journeys pass linked-panel review,
explicit user source grants, retained panels and disable/restore checks.
Both installed Codex runtime paths pass against local model fixtures, and
startup/render/shutdown passes. Ruff lint/format, dependency checks, whitespace,
ten recipe/five panel-content examples and local document targets pass.
Light/dark native linked-panel dock and review previews were inspected. Logs:
`/tmp/steed-continued-engineering-full.log`, `/tmp/steed-panel-library-final.log`,
`/tmp/steed-linked-panels-{stdio-retry,http,native,native-direct,startup}.log`. The first stdio attempt reproduced the
known intermittent offscreen Qt crash at `QWebEngineProfile` construction
(`profile_manager.py:324`) before task/panel setup; its faulthandler stack is saved
in `/tmp/steed-linked-panels-stdio.log`. The retry and HTTP journey pass; this
headless startup defect remains unresolved. Example:
[library-notes.json](../../examples/data_panels/library-notes.json).

**Account-panel direction (owner approved and implemented above):** manual Refresh first. No recurring
API requests on view or automatic refresh; future scheduling remains a separate
choice.
The owner has approved reusable panel/profile permission and immediate hiding on
source revocation. Account-backed reads must reuse trusted tools and account identity
checks; live-provider acceptance is separate. Public-release work and broader
HTML mini-app hosting remain separate.

**Native workspace/image sources N3 (implemented and accepted).**
Version 3 binds text/list/table widgets to exact sealed owned files and introduces
static PNG/JPEG previews. Versions 1/2 retain their grammar. Proposals and updates
check the source's task, status, size, MIME and digest, pin its reviewed source
type, and require the corresponding artifact/workspace authority. Mixed source
operations consume one budget unit; existing artifact receipt hashes stay stable.
Images enforce 8 MiB/four-megapixel/dimension bounds and an eight-preview cache;
only matching PNG/JPEG decoders run. Profile switches scrub pixels/source records.
Saved task snapshots survive task end/revocation with no update authority; deleted
or changed files fail closed even with a cached preview. No live library/account
read or executable callback is introduced.

Acceptance: **1,220 combined regressions pass in 416.669 seconds**, plus 45 focused
image/file/artifact/form checks. Independent SDK/raw workflows pass sealed file
creation, image review, mixed-source publication, form delivery, page edits and
stable retries over stdio and authenticated HTTP. Both installed Codex runtime
paths discover the updated catalogue against local model fixtures. Startup/render/
shutdown and light/dark native file/image previews pass; Ruff lint/format and
whitespace pass. A synthetic raw-client argument collision was corrected before
the successful transport runs. No real accounts or external models were used.
Logs: `/tmp/steed-internal-pages-panels-{full,stdio,http,native,native-direct,startup}.log`.
Typed connector query/POST reads are delivered above. Next: selected
library/connector sources with explicit freshness/revocation. Broader
local HTML hosting/bridges need a concrete app and lifecycle proposal.

**Global and editable internal pages (implemented and accepted).**
Fresh installs start in Global and no longer create a built-in Default profile;
existing Default data remains an ordinary, mutable profile. Home and New Tab have
separate address preferences, initially `steed:newtab`. `steed:welcome` provides
onboarding and links to agents/folders. Both pages use maintained local HTML with
reload/session restore and one-colon address display. Per-profile bookmarks,
section ordering, copied local images, recent tabs/visits and opt-in RSS/Atom news
are available. Custom HTML/CSS/inline JavaScript can replace either page through
the native editor or import; restoring built-in sections is explicit.

External agents have three page tools for format discovery, immutable proposals
and receipts. A native source review chooses the display profile and checks its
identity/current-state digest before Apply. No automatic data grant or page-source
disclosure occurs. Users explicitly share source through existing connected-task
conversations, copy it for website chats or export it to enabled folders. Pending
reviews appear in Task Permissions and the sidebar count. Schema 19 retains old
tasks/drafts and interrupts unfinished reviews on restart. Global now supports
reviewed `existing:global` task access, so the default profile remains useful for
external agents; incognito remains excluded from shared tasks.

Acceptance: 136 browser/profile/integration checks and 22 final page/edit/feed
checks pass. Independent SDK/raw workflows pass proposal, native review and stable
retries over stdio and authenticated HTTP. Both installed Codex runtime paths
discover the page tools using local model fixtures. Light/dark Welcome, New Tab,
customization and minimal-page previews were inspected. A feed refresh preserves
the search draft; a startup autofocus scroll issue was found during visual review
and corrected. The full suite's three old Default/Global assumptions were updated;
the final combined 1,220-test suite now passes. Startup/render/shutdown passes.
Logs: `/tmp/steed-start-pages-*`.
Native image/workspace bindings are also accepted above. Next engineering scopes
are selected library/connector sources and provider adoption of the delivered
typed connector reads. The editable start
page is a concrete local HTML use case; broader hosted apps/capability bridges
remain separate work.

**Native inputs N2 and comparison tables (implemented and accepted).**
Version 2 adds bounded text/choice forms and read-only tables to reviewed panels;
version 1 retains its original grammar. Forms preview exact values and the owning
task/profile before an explicit Send to agent. They use existing conversations,
not executable callbacks or automatic connector writes. Queued messages and
value-free retry receipts commit together; retries never re-notify, even after
message clearing or task end. Disconnected/ended tasks disable forms. Artifact
updates preserve form values, previews and unaffected selections in place; profile/
panel switches and window close scrub input. Inputs are transient until sent and
never enter appearance exports. The sidebar now counts panel/layout reviews in
its pending approvals. Tables share owned-artifact digests, authority, publication
revisions and retained-draft semantics.

Acceptance: **1,190 regressions pass in 453.283 seconds**; 187 focused integration
checks pass. The final readable-popup follow-up passes **68 UI checks** in 22.718
seconds. Independent SDK/raw MCP workflows pass preview/send, correct-task delivery
and table publication over stdio and authenticated HTTP. Both installed Codex
runtime paths and startup/render/shutdown pass; Ruff lint/format, dependencies,
examples, local documentation targets and whitespace pass. Light/dark browser,
inert-installation and readable-send previews were inspected with synthetic data.
No real accounts or external models were used. Logs:
`/tmp/steed-native-inputs-{full,focused,final-ui}.log`,
`/tmp/steed-native-inputs-{stdio,http}.log`,
`/tmp/steed-native-inputs-native{,-direct}.log`,
`/tmp/steed-native-inputs-startup.log`.
Next: image/workspace sources,
then selected library/connector-result bindings with explicit freshness and
revocation behavior. Local HTML/JavaScript hosting remains a complementary backlog.

**Connector starter branch integrated.** PR #2 head `ccf2c4a` is merged locally by
`1383368`; correction `65ced87` updates the POSIX picker expectation and obsolete
setup gates, distinguishes Graph item IDs from sharing URLs and discloses that a
Zotero library ID is not checked against the connected identity. Five packaged
subsets now appear in Add a Service: Todoist, GitLab.com, Asana, Raindrop.io and
supplied-token Microsoft 365. GitHub repository, GitLab project and Zotero library
templates remain explicit imports. **80 Linux profile checks and 127 shared
runtime checks pass** against the combined branch. All live-account acceptance
remains pending; installation connects no account and grants no task access.
The [exact-head review and C6–C10 continuation instructions](https://github.com/AdvancedForge/steed/pull/2#pullrequestreview-5407033827)
are posted. Branch from published main for the next contributor milestone.
Logs: `/tmp/steed-connectors-ccf2-profiles-final.log`,
`/tmp/steed-connectors-ccf2-runtime.log`. Shared typed query/POST reads remain the
next runtime connector scope; gated provider contracts remain reference data.

**Native data panels N1 (implemented and accepted).**
The user chose native components first, retaining locally hosted HTML/JavaScript
apps as a later option. The first catalogue offers task status, read-only text and
lists bound to owned immutable artifacts. A native preview lets the user choose
one normal display profile and enable same-task draft updates. Templates/bindings
live in the task journal database (schema 18), never global appearance settings
or exports. Stable profile identities preserve renames and block name recreation;
private/other profiles show no panel content. Shared windows receive updates,
unchanged selections survive refresh, and changed-profile content is erased.

Four MCP tools discover, propose, poll and publish pinned artifact bindings with
exact revisions and stable update receipts. Review grants no data access; draft
publication needs active artifact authority. Pending requests cancel/interrupt,
while enabled saved drafts survive task end/restart with no update authority.
Deleted or tampered sources produce an unavailable panel. Atomic journal failures
roll back receipts/bindings and stop commands. Explicit widget/proposal/publication
bounds apply. The Data Panels dock shares space with the agent sidebar through
native dock tabs and preserves chat-tab ownership.

Acceptance: **1,096 regressions pass in 428.872 seconds**. The final selection
follow-up passes **120 browser/layout/permission/chat checks**, including all
24 native-panel regressions and a shown-widget check that preserves the selected
panel and highlighted text across refresh. A Qt tuple-lookup issue found during
visual acceptance was fixed. Independent SDK/raw workflows pass over stdio and
authenticated HTTP, including user review, draft updates, duplicate retries and
retained drafts. Installed Codex direct-tool/code-runtime discovery and local
startup pass. Ruff lint/format, dependency, documentation-target and whitespace
checks pass. Light/dark browser and 640×520 review previews were inspected with
synthetic profiles; no real accounts or external model calls were used. Logs:
`/tmp/steed-data-panels-full.log`,
`/tmp/steed-data-panels-focused-final3.log`,
`/tmp/steed-data-panels-{stdio,http}-final.log`,
`/tmp/steed-data-panels-native{,-direct}.log`,
`/tmp/steed-data-panels-startup.log`.
Native inputs and comparison tables are delivered above; richer sources/components
follow with existing message/permission/receipt controls. HTML/JavaScript hosting stays deferred rather
than excluded. See [usage](../architecture.md#native-data-panels--implemented-first-milestone)
and [the continuation plan](engineering-handoff-2026-10.md#native-data-panels--decision-and-continuation).

**Accessible form hints (implemented and accepted).**
Rendered-DOM targets now expose bounded descriptions, author-declared required/
invalid state and visible error messages. Referenced content remains scoped to its
document/open shadow root; hidden/editable ancestors and current values are omitted.
The reader reports that it is not a complete accessibility tree. Live fingerprints
include referenced help/error content and visibility ancestors, retain unrelated
page-update tolerance, and expose oversized context as unavailable. No action,
profile, file or website permission expands.

Acceptance: **1,073 regressions pass in 410.071 seconds**; 79 final observation/
live-target/gateway checks pass. A large Unicode fixture confirms that aggregate
label/help/error text stays within 16,000 characters and the serialized snapshot
fits the MCP response limit with explicit truncation flags. SDK and independent
raw clients receive metadata over stdio and authenticated HTTP. Synthetic startup/
render/shutdown, Ruff lint/format, dependency and whitespace checks pass. No real
profiles, accounts or external models were used. Logs:
`/tmp/steed-form-observation-full-final.log`,
`/tmp/steed-form-observation-budget.log`,
`/tmp/steed-form-observation-{stdio,http,startup}.log`.
The native-first architecture is now accepted and implemented above; local
HTML/JavaScript hosting remains on the roadmap.

**Native quick-action panels and text sizing L3 (implemented and accepted).**
Portable layout version 2 adds up to two native action panels, eight maintained
buttons per panel, top/bottom placement, neutral/accent emphasis and normal/large
browser text. Version-1 documents migrate with no new controls. Layout Settings
has a panel editor; agent proposals use the existing inert preview and Apply
popup. Creation never triggers an action or grants data access. Panels reflect
existing action availability; page actions disable in the native agent chat tab.
Keyboard focus reveals offscreen actions; text sizing preserves website zoom and
font families, and survives dark-mode styling. Shared-window commits, local
cancellation, private browsing and previous-layout recovery remain covered.

Acceptance: **1,063 regressions pass in 387.754 seconds**. The final font/UI
follow-up passes 94 checks; startup/render/shutdown and installed Codex direct-tool
and code-runtime fixtures pass. Ruff lint/format, dependency and whitespace checks
pass. Final light/dark browser and review previews were inspected using synthetic
profiles. No real accounts or external models were used. Logs:
`/tmp/steed-native-panels-full.log`, `/tmp/steed-native-panels-font-final.log`,
`/tmp/steed-native-panels-startup.log`,
`/tmp/steed-native-panels-native{,-direct}.log`.
Bounded form hints and changing-reference checks are delivered above.

**Portable layouts and reviewed agent proposals L2 (implemented and accepted).**
Strict portable JSON has stable mandatory controls, bounded dimensions and no
callbacks/commands. Import opens a plain-text diff and inert native preview;
Export writes appearance only to a new filename. Previous-layout recovery restores
appearance across windows while keeping current presets. Configuration write
failures restore in-memory values as well as preserving the atomic disk file.

Three MCP tools discover the grammar, propose exact immutable source and poll a
task-bound receipt. Submission creates no data permission; the native popup's
Apply/Decline is user-only. Closed requests remain in Task Permissions → Layouts.
Stale previews refresh before approval; limits bound pending source; cancellation
and restart never replay requests. Schema 17 retains existing task evidence.

Validation: 135 focused checks and 18 final proposal checks pass. The integrated
run exercised **1,048 checks**: 1,043 passed; five legacy version-16 expectations
were corrected for schema 17 and all five pass separately. An initial Qt startup
crash did not reproduce in 26 startup/access checks or the integrated rerun.
Installed Codex 0.159.2 passes direct-tool/code-runtime discovery and follow-ups;
startup/render/shutdown, dependency, Ruff and whitespace checks pass. Light/dark
review previews were inspected. All fixtures are synthetic; no real accounts or
external model calls. Logs: `/tmp/steed-layout-proposals-focused-final.log`,
`/tmp/steed-layout-proposals-acceptance.log`,
`/tmp/steed-layout-proposals-full-final.log`,
`/tmp/steed-layout-proposals-migrations.log`,
`/tmp/steed-layout-proposals-native{,-direct}.log`.
The complete L3 suite above also verifies the corrected schema-17 expectations.

**Configurable browser layout L1 (fixed, accepted and merged).** The contributor's
manual layout milestone now preserves the original address-field position and
explicitly hides disabled controls. The address field is a mandatory reorderable
anchor. Save/Reset broadcasts through the shared registry; editors open from
canonical settings, preserve unrelated concurrent edits and refuse same-setting
conflicts. Cancel restores the latest committed choices. Named-layout selection
uses the normalized name. Widths match the native 300–800 px bounds, and the
scrollable dialog keeps Save/Cancel reachable in small windows.

Acceptance: **1,030 regressions pass in 394.677 seconds**; the final widget/parent
and small-window follow-up passes **61 checks**. Startup/local render/shutdown,
Ruff lint/format and whitespace pass. Light/dark 640×520 previews were inspected.
No real accounts or profiles were used. Logs: `/tmp/steed-layout-fixes-full.log`,
`/tmp/steed-layout-fixes-focused-final.log`, `/tmp/steed-layout-fixes-startup.log`.
Original findings and subsequent scopes are retained in
[the engineering handoff](engineering-handoff-2026-10.md#configurable-layout-review-and-continuation).
Portable layouts/proposals and native action panels are delivered as L2/L3 above.

**Codex access discovery and reported model (implemented and validated).**
The user reports an agent saying access-discovery requests were rejected. Two
reproducible faults were found: the native Steed MCP entry used `auto` admission,
which could reject calls before Steed saw them, and the adapter immediately
rejected siblings of an in-flight request. The dedicated bound entry now uses
`approve`, while Steed still owns enabled-folder access, scope approvals and
payload reviews. Other native servers and OS/sandbox policies remain unchanged.
Parallel calls queue with a 32-call bound/35-second waiting timeout; cancelled
waiting calls do not close the active call, and dispatched uncertainty is never
replayed. Discovery instructions explicitly name the zero-argument tools, exclude
caller-supplied task IDs, explain empty lists and distinguish protected live-tab
listing from grant-free capability/context/folder/connector discovery. Known
tool names/codes and value-free rejection stages make future failures traceable.

The visible activity card now reports the thread's actual Codex model/provider,
updates on owned-turn reroutes (including early events), and labels missing
metadata as unreported. The selector explicitly applies to new chats. Reports
are scoped across conversations/windows, retained as last-reported until browser
restart, and hidden in private browsing. The installed runtime's expanded offline
workflow discovers resources concurrently, confirms live-tab denial, requests
one exact workspace grant approved by its synthetic user, and keeps follow-ups
connected under a native never-ask policy. No real resumes, knowledge-base data,
account credentials or external applications were used. The original live task
still needs a new-chat retry after restart; there is no discovery enable switch.

Final main-checkpoint acceptance: **969 regressions pass in 376.410 seconds**.
Installed Codex 0.159.2 passes both direct-tool and code-runtime offline workflows
with strict checks for reported model, concurrent discovery, protected-tab denial,
the exact requested/approved synthetic grant and connected follow-ups. The direct
fixture decoder now handles native timing headers before its JSON output; this
was a test-harness correction, not a production tool failure. Startup/local
render/shutdown, Ruff lint/format and whitespace checks pass. No external model
calls or real-account task execution were performed.
Logs: `/tmp/steed-discovery-model-full-final.log`,
`/tmp/steed-discovery-model-native{,-direct}-final.log`,
`/tmp/steed-discovery-model-startup-final.log`.

**Codex tool-transition compatibility and readable activity (implemented and validated).**
The user reported a reply promising work followed by a generic connection failure.
Current question requests and standalone clock reads are supported; unrelated
Codex thread notifications are excluded from the bound conversation rather than
ending it. Unsupported optional host RPCs get method-not-found, never approval.
Owned-turn mismatch/malformed-data checks remain. Failures show content-free
handling stage/event and exception class for further diagnosis. The exact live
account failure is unconfirmed; do not claim a universal compatibility fix.

The sidebar now has a light/dark activity card, distinct conversation/composer
headings, colored speaker headers, emphasized Send/New Chat buttons and visible
Sending/Thinking/Working/Replying/Waiting/Ready states with queued-message count.
A thin busy indicator remains active until native work finishes. Other MCP clients
retain an explicit unknown-execution-state label. Detailed connection/permission
status moves into More options; pending approvals/questions stay visible.
All 54 focused provider/sidebar/web-chat checks pass in 23.713 seconds. Installed
Codex 0.159.2 passes offline streamed follow-ups/task-bound tool calls through both
direct-tool and code-runtime model fixtures; no real account or external model
requests were made. Source/working light/dark previews were inspected.
Logs: `/tmp/steed-codex-activity-focused-final.log`,
`/tmp/steed-codex-activity-native{,-direct}.log`.

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

Connector contributor coordination: [v6 runtime coordination and next steps](https://github.com/AdvancedForge/steed/pull/2#issuecomment-5974337618)
was posted after publishing `11a28e9`. Provider data ownership and the separate
next-head review remain unchanged.

**Simpler agent sidebar and requested menu order (implemented).** Menus now read
General, Profile, Tabs, View, Cookies, Agents. First open offers a short welcome,
New Codex chat and Connect another agent. Selecting a conversation reveals chat,
Send/Cancel and tab sharing; More options starts collapsed and holds model/setup,
file library, presentation and task administration. Pending approvals/questions
remain visible outside the collapsed controls. Drafts, lifecycle, privacy and
explicit browser authority remain intact. All 27 focused menu/sidebar/web-chat
checks pass in 12.606 seconds. Synthetic welcome/chat/options/menu previews were
inspected; final integrated acceptance is recorded in the connector checkpoint below.
Log: `/tmp/steed-sidebar-simplified-final.log`.

**Reviewed identity-label alternatives (implemented and validated).**
Recipe v6 declares 1–3 ordered alternatives for a missing/null/empty display label.
It never guesses a field, masks malformed/secret-bearing data, or relaxes exact
account-subject binding. Review/preflight/capabilities expose the pointers; v6
retains nested writes/plans and optionally the v5 static headers. Old versions
remain strict. Cached and refreshed OAuth identity checks reject known credential
echoes before storing credentials or dispatching tools. A synthetic v6 example
and 107 focused recipe regressions pass in 15.864 seconds.
Log: `/tmp/steed-identity-focused-final.log`. Provider contracts and live accounts
remain separate; no contributor-owned recipe or fixture was edited.

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

**Reviewed API-version headers (implemented and validated).**
Recipe version 5 binds 1–4 supported non-secret headers to immutable source.
The first set is Notion-Version, X-GitHub-Api-Version, Zotero-API-Version and
OCS-APIRequest with constrained date/version/flag values. Source and payload
reviews show them, and identity, all read pages, writes/plan steps and probes
send exactly the reviewed values. OAuth exchange/refresh excludes API headers.
Versions 1–4 remain strict and supported. Hash/provider changes prevent old
accounts/grants/reviews from authorizing changed source; installed old versions
are unchanged until disabled. Credential-bearing and transport/auth overrides,
noncanonical names, duplicate keys and injection are rejected. New capabilities
metadata and a synthetic v5 plan example guide agent-authored recipes. Provider
contracts and real-account acceptance remain separate; headers alone do not
unblock every Notion/Zotero/Nextcloud operation. No contributor-owned data changed.
Identity-label alternatives are delivered in v6 above. Next connector scope is
separately typed query/POST-read contracts, followed by provider acceptance.

Validation: the final complete suite passes **938 tests in 375.207 seconds**.
Both actual MCP SDK/independent-client journeys pass over stdio and authenticated
HTTP with document workers, reusable library files and v5 reviewed plans; each
client verifies header metadata, payload context and synthetic dispatch. Exact
TLS wire tests cover GET/POST and token-exchange separation. Startup/local
render/shutdown passes on Qt 6.11.2. Source review and the final six-menu layout
were visually inspected with synthetic fixtures. Ruff lint/format, dependency,
whitespace, seven recipe examples and local document-link checks pass.
Live provider accounts and native desktop acceptance remain separate.
Logs: `/tmp/steed-menus-headers-final-full.log`,
`/tmp/steed-recipe-headers-corrected.log`,
`/tmp/steed-headers-mcp-{stdio,http}.log`, `/tmp/steed-headers-startup.log`.

**Menu organization (implemented).** General is first and contains everyday
settings, passwords, downloads and website permissions. Profile remains its own
top-level menu with creation, switching, pop-out, private browsing and Quick Switch.
Tabs gathers tab commands and a Tab Groups submenu with current-tab assignment,
removal and collapse/expand actions. Agents gathers every existing agent control,
including native/web chat, file library, connections and local MCP setup. View
contains address focus, find and zoom; Cookies includes browsing-history clearing.
File/Edit and the separate Tab Groups category are removed. Keyboard
shortcuts, profile Quick Switch and history/profile isolation remain intact.
Documentation uses the new menu paths. The initial layout passed 114 browser/
menu/agent-access/setup/sidebar/web-chat checks in 68.864 seconds. The final
layout retains the top-level Profile menu at the user's request; its regression
passes in the final 938-test suite above. Synthetic offscreen previews were
inspected. Ruff lint/format and whitespace checks pass.
Logs: `/tmp/steed-menu-regressions.log`, `/tmp/steed-menu-final-preview.log`.

**Observation increment (implemented and validated).** Page reads now
include open-shadow text and root-local control labels, nested components and
image alt descriptions. A 32-root/eight-level budget joins existing text/node/
target limits. Root mutation invalidates restricted snapshots; live references
recheck root-local names and host ancestry. Closed roots, slot ordering, hidden/
editable values, canvas pixels and shadow-control actions retain explicit limits.
Short text-wait deadlines distinguish an unfinished inspection from an observed
miss. New synthetic Chromium checks cover privacy, root/depth limits and stale
references. The complete suite passes all 926 checks in 381.348 seconds.
Actual SDK and independent clients pass component text/name/value-omission checks
over both stdio and HTTP MCP. Startup/local rendering/shutdown passes on Qt 6.11.2.
Ruff lint/format, JavaScript syntax, whitespace, dependency and local doc-link
checks pass. The paged library dialog was visually inspected with synthetic data.
Real provider/desktop daily-use acceptance remains distinct from these fixtures.
Logs: `/tmp/steed-engineering-final-full.log`,
`/tmp/steed-observation-{stdio,http}.log`,
`/tmp/steed-engineering-final-startup.log`.

**Library discovery milestone (engineering branch reviewed and integrated).**
Reusable files are now findable instead of only reachable by walking every
folder. `steed_library_search` returns file metadata across every enabled root
in one call — folder id, folder name, relative path, kind, current revision and
size — which is exactly what `steed_library_read` and `steed_library_write_begin`
already need, so an agent can locate a nested input and reuse it from a new task
without listing folders one at a time. Matching is a literal substring of the
path, so `%` and `_` are never wildcards. Results are ordered by folder name then
path and paged with an opaque cursor, and identical file names in different roots
stay distinguishable by folder id. Disabled roots, Trash and old versions are
never returned, and access is rechecked on every call, so disabling a folder
hides it immediately. Each call fetches one page plus a sentinel, reporting
`has_more`/`truncated` when another page can be retrieved. A **Filter** box
applies the same literal match to the selected folder; 200-row Next/Previous
pages replace the old ceiling, and filter/navigation clear on profile change or
close. Discovery is metadata only: no content indexing, no decision model, no
recurring approval prompts, and imports remain copies.

Reviewed branch head `f6c28b4` and retained its search/tool/filter/smoke work.
Maintainer corrections: honor an enabled `folder_id` restriction (the branch
discarded it), keep Unicode cursors within the published limit, fetch only the
requested page, and replace 20,000-widget refreshes with actual UI paging and
unambiguous paths. All 155 final library/workspace/profile/chat/gateway/service
checks pass in 82.229 seconds, including a 2,103-file navigation regression.
Both stdio and HTTP MCP journeys pass with actual SDK and independent clients.
The contributor's GLX test limitation is resolved
in this Linux environment. The following observation checkpoint's complete
926-test run also validates the merged discovery implementation.

**Earlier completed milestone:** a persistent agent
file library: user-added files/subfolders survive tasks and restart; a clear
folder enable switch gives connected agents full file access without repeated
uploads or per-file/task approvals. Old versions and Trash support recovery,
revision checks protect concurrent edits, and task/workspace bridges allow
generators to reuse inputs and keep outputs. Library access is independent of
browser/profile and execution permission. Imports are copies into a managed,
globally shared regular-profile library; linked host directories are not mounted.

Validation: the complete suite passed 906 checks before final recovery/export/
lifecycle polish. After those changes, all 148 library/workspace/profile/chat/
gateway/service checks passed in 80.971 seconds, including 27 library checks and
the short chat-tab scroll regression. Actual SDK and independent-client journeys
passed over both stdio and HTTP: enabled-folder discovery, binary CRUD, repeated
commit receipts and reuse from a new task without per-file/task grants. Existing
document-worker and recipe workflows passed in those journeys. Startup/local
render/shutdown passed on Qt 6.11.2. The settled library dialog was visually
inspected using synthetic files. Ruff lint/format, whitespace and dependency
checks pass. Real desktop/agent daily-use acceptance is still separate.
Logs: `/tmp/steed-library-full.log`, `/tmp/steed-library-final-integration.log`,
`/tmp/steed-library-stdio.log`, `/tmp/steed-library-http.log`,
`/tmp/steed-library-startup.log`; temporary logs are not portable release evidence.

Public-release audit and proposed gates now live in
[RELEASE_PLAN.md](../RELEASE_PLAN.md). This separates a supported Linux beta from a
stable daily-browser release and the broader agent-collaboration vision. Missing
license/distribution/engine-update policy, CI, desktop acceptance and security/
privacy review are concrete release work; feature coverage alone is insufficient.
License, package/support target and cloud-agent release scope remain owner choices.

Connector [PR #2](https://github.com/AdvancedForge/steed/pull/2) was re-reviewed at
`d94f093`: all 75 Linux profile/catalog/interpreter/compatibility/HTTP checks pass,
including 40 profile tests and the actual selector path, without skips. Prior
corrections are addressed; the shipped starter subsets receive a positive
engineering assessment. [Review](https://github.com/AdvancedForge/steed/pull/2#pullrequestreview-5402708365)
and [five continuation milestones](https://github.com/AdvancedForge/steed/pull/2#issuecomment-5973415389)
cover workflow completeness, additional supported templates, executable provider
contracts, broader families and lifecycle testing. Candidate contracts need
careful transport-size/onboarding distinctions; that review head was unmerged, and no
live account acceptance is claimed.

Chat presentation milestone: native agent conversation can move between sidebar
and a saved/restorable tab; normal HTTPS website chats can move the same page
between tab/sidebar. Instructions use exact preview, opt-in selected context,
copy/native paste and user-controlled send. This does not connect cloud models to
the local MCP endpoint. Full regressions passed: 883 checks in 373.049 seconds;
after final profile/delegation fixes, all 115 browser/live/sidebar/web-chat
integration checks passed in 90.419 seconds (including 12 new chat regressions).
Startup/render/shutdown passed with Qt 6.11.2. Settled native-tab and prompt
previews were visually checked using synthetic data; real provider login/composer
acceptance remains pending. Ruff lint/format, whitespace and local doc links pass.
Logs: `/tmp/steed-chat-final-full.log`,
`/tmp/steed-chat-final-integration.log`, `/tmp/steed-chat-startup-final.log`.

**Resume checkpoint:** persistent library and reviewed discovery/paging are
complete; engineering branch `f6c28b4` is integrated by merge `e20cfea`.
The first open-shadow observation increment and external component acceptance
are complete. Reviewed non-secret API-version headers and explicit identity-label
alternatives are implemented. Manual/portable layouts, native action panels and
task/text/list/form/table data panels are complete. The next daily-workflow scope
adds image/workspace sources and richer data bindings; the next shared connector scope
is typed query/POST reads. Deeper accessible semantics and supported shadow-control
input remain separate follow-ups. Further agents work on their own branches and
submit milestones for maintainer review.
See [the October engineering hand-off](engineering-handoff-2026-10.md) for ownership, ordered scopes,
acceptance checks and the review procedure. Release gates remain reference work
in RELEASE_PLAN.md. Continue connector work under the posted PR instructions.
Cloud-agent transport/pairing needs a separate product decision. Google shared
registration and password improvements remain deferred. No rewrite is proposed.

Latest browser milestones: [live-tab delegation](#live-tab-delegation-and-coverage-signals--october-3-2026)
(`d7544a0`) preserves normal website behavior and adds immediate Take Back;
[approved viewport evidence](#approved-viewport-evidence--october-3-2026) adds bounded
native MCP images. DOM coverage signals help agents decide when a visual read is
needed. Accessibility and broader frame/widget coverage come next. All 873
regressions and native image delivery over both MCP transports passed.

Document bundle milestone: the optional maintained `documents-v1` worker creates
PDFs, XLSX spreadsheets and images. Its pinned wheels live outside browser Python;
each run verifies/copies them into a read-only sandbox mount. Package approvals
also bind the selected environment. Install once with
`.venv/bin/python -m browser.install_worker_bundle`, then refresh Workers setup discovery.

Local-worker milestone: approved agent-built Python packages generate attachments
and compose existing app adapters through the task broker. The automatic package
popup shows source, hash, app capabilities and limits, defaults to Deny, and can
remember the exact package for the current task/grant. Bubblewrap namespaces,
read-only mounts, hard limits and a seccomp allowlist apply before source runs.
No direct networking, credential mounts, subprocesses or runtime dependency installation.
Workers has retained receipts, review and cancellation; stable IDs never replay.
See [worker usage](../task-services.md#running-an-agent-built-local-worker).

File-recovery milestone: follow-ups can explicitly share selected sealed binary
files and source alongside drafts/evidence. Files receive independent IDs,
size/hash validation and source provenance; imports and write guards commit
together. Fresh workspace permission is still needed to read bytes. Packages
are copied as data and need new staging and code approval. Google shared
registration remains important and is deferred at the user's request.

Result-retention milestone: **Task Permissions → Apps** can clear a saved provider
result or the selected task's own results. Clearing removes text/links and shared
receipt summaries atomically, persists across restart, and suppresses late-worker
restoration. Outcomes and operation/request identity remain for recovery and
historical-write protection. Only trusted UI can clear; agents cannot erase their
receipts. Independently approved checks and drafts keep their own lifetimes.
See [retention controls](../task-services.md#clearing-saved-app-results).

Archived receipt milestone: a fresh follow-up can check a user-shared uncertain
app receipt with exact **read-only** account/tool/destination permission. Checks
have their own durable receipt and cancellation/budget lifecycle, but use the
original provider operation identity. Confirmed evidence updates the original
receipt atomically without restoring old grants or resending writes. Google Docs
support this; Gmail stays send-only. SDK and independent clients passed both MCP
transports; the restarted synthetic Google HTTP flow recorded exactly one Doc and
one email. See [archived receipt checks](../agents.md#checking-an-archived-app-receipt).

Safe follow-up milestone: agents can request a browser picker for an archived task
and selected drafts/evidence. No archive enumeration or client-selected source is
exposed. Imports preserve provenance with new IDs and require fresh grants, even
when saved approvals exist. Ancestral app-call guards block matching completed or
uncertain writes before dispatch, including recreated drafts and deleted originals.
Both independent MCP clients completed selection/approval/no-replay workflows over
stdio and HTTP. Archived read-only provider probes are now implemented;
Gmail stays send-only. See [task recovery](../agents.md#continuing-previous-work).

Permission/setup usability milestone: both website and agent prompts now offer
one-time and remembered choices with clear capability and profile scope.
Agent rules cover matching or narrower requests, preserve exact app accounts and
destinations, and can be forgotten with linked live access revoked. Website
rules are separate; regular approvals never enter private browsing, whose saved
choices are session-only. Google app registration is remembered in the desktop
keyring so subsequent account connections need only **Connect Google** and consent.
First-time Cloud registration still needs user-owned setup. Validation is recorded
in [DOCUMENTATION.md](../development.md#verification-record).

Browser essentials include Ctrl+F find in page and per-tab zoom. The complete
518-test suite passed in 255.304 seconds (`/tmp/steed-retention-full.log`),
including seventeen result-retention regressions. Coverage includes
scoped uploads/downloads, watch/handoff, HTTP session/credential/lifecycle tests, native-input checks,
capability discovery, local agent transport/lifecycle checks, and shared-profile
focus/control-isolation, plus existing form, popup, and browser regressions.
Current startup/render/shutdown passed on Qt 6.11.2
(`/tmp/steed-retention-startup.log`); the synthetic Google HTTP/OAuth check passed
(`/tmp/steed-retention-google.log`).
Actual Chromium notification requests passed once/saved/forget/private behavior
(`/tmp/steed-site-native.log`); synthetic Google API and both MCP transports passed.
The artifact HTTP fixture hit the tracked native startup exit 139; identical retry
passed. Twelve final artifact checks cover user-controlled draft deletion too.
The previous form-stage Google fixture exited natively during `QWebEngineProfile`
creation before passing on retry. An earlier startup fixture also exited natively.
Track that intermittent exit separately from desktop acceptance. Ruff, formatting,
and whitespace checks passed. Launcher validation passed
during the previous browser checks.

The user reports few complaints during daily use and authorized proceeding after
the checks available to the assistant. A1 is implemented, and A2 supports private
and concurrently shared existing-profile grants, with automatic approval popups.
Exact download destinations, upload source files and upload origins are implemented;
restricted active-page network containment still needs native engine support.
Explicitly delegated live tabs now use normal browser execution instead.
A3 includes content inspection, stale-target validation, observed link navigation,
scriptless control edits, native form submission, scrolling, bounded waits, and
scoped native attachment downloads and single-file multipart uploads.
Active document scripts on restricted task pages are deferred following the bounded engine assessment;
scripted/custom forms and complete SPA acceptance remain pending. A4 includes
opt-in local IPC and working stdio/HTTP MCP adapters for current task operations.
A7 is in progress: users can watch the same task page, take control through
supported actions, and return it with edits preserved and old references invalidated.
A6 is in progress: a trusted connector gateway binds exact app tools/effects and
destinations to immutable drafts, with durable receipts and read-only reconciliation.
Mock document/email MCP workflows passed; the default browser has no real provider
configured. Artifacts and app receipts have retained user review panels.
Real-account login persistence, third-party Google sign-in, hardware permission
flows, and ordinary site usability remain follow-up checks; no automated fixture
establishes those results. Password improvements remain deferred at the user's request.

### Resume checkpoint

- **Latest live/browser checkpoint:** `d7544a0`, normal page/script preservation,
  explicit current-tab/profile selection, controller banner, immediate Take Back
  and per-control live revalidation. 149 focused regressions passed. The current
  viewport-evidence milestone adds distinct visual consent and bounded MCP image
  delivery; finish its verification below before proceeding to accessibility.
- **Earlier Google checkpoint:** exact Google folder destinations (`e74dd12`),
  document/tab inspection, bounded tab reads and immutable draft appends with
  current-revision preconditions. Atomic operation ranges support read-only
  confirmation after a lost response/restart without repeating the append.
  Folder/account/ownership checks, schema-8 result clearing and historical-write
  protection remain supported. Root/file/tab grants never include other destinations.
  Acceptance: **555 tests passed in 252.809 seconds**, focused Google/gateway
  checks, both Google HTTP workflows, both MCP clients over both transports,
  startup/render/shutdown, Ruff/format, dependencies and local documentation links
  passed. Fixtures used temporary state and synthetic accounts; real Google and
  desktop acceptance remains pending.
- **Document-edit decision accepted and implemented** (`c734b65`): user-reviewed proposals
  by default, with a separately named reusable direct-edit permission. Schema-9
  previews, automatic before/after popups, default Deny, exact revision/draft
  binding, private-preview scrubbing and read-only edit recovery are implemented.
  Saved reviewed grants never bypass content review or cover direct edits.
  Plain-text replacement stays within one exact Steed-created document tab.
  Acceptance: **568 regressions passed**, focused proposal and existing Google
  checks passed, and the local HTTP fixture applied exactly one reviewed replacement
  with lost-response recovery. The actual before/after popup was inspected.
- **Generated files/custom integration groundwork implemented** (`9d0f505`): schema-10
  task-owned binary files, chunked transfer, hash/size sealing, exact approved
  exports, explicitly user-selected input snapshots and retained user export/deletion.
  Seven new MCP tools work with both independent clients over stdio and HTTP.
  Custom source packages pin a manifest and owned sealed files; requested access
  is visible but grants nothing. Staging never runs code or registers tools;
  execution is now separately approved in the local-worker milestone.
  See [workspace usage and limits](../task-services.md#generated-files-and-custom-integration-packages).
  Acceptance: **590 tests passed in 255.599 seconds**, twenty-one final workspace
  checks, both MCP transports/clients, user sharing/export UI, Google HTTP review/
  recovery and startup/render/shutdown passed. Ruff/format and diff checks passed.
  The first stdio run hit the tracked Qt startup exit 139; retry passed. All inputs
  and account acceptance were synthetic. Real desktop/account acceptance is pending.
- **Local worker implemented** (`2a7a8e5`): schema-12 owned execution
  receipts, separate exact-package approval, task/grant-bound remembered hashes,
  standard-library helper, two-worker capacity and bounded typed RPC are implemented.
  Actual Bubblewrap/seccomp checks reject host-data/environment access, sockets,
  subprocesses, namespace changes and raised hard limits. Resource/protocol limits,
  cancellation races, app capability/destination checks, source integrity/deletion,
  restart/migration and journal failures have regression coverage. SDK and raw MCP
  clients generated ZIP attachments after actual package popups over both transports.
  Acceptance: **629 regressions passed in 256.190 seconds**, 29 focused worker
  checks passed in 5.877 seconds, both independent MCP clients passed stdio/HTTP,
  and startup/render/shutdown, workspace picker/export UI and synthetic Google
  document review/recovery passed. Approval/results screenshots were inspected.
  Ruff/format, dependencies, diff and local documentation links passed. All state,
  files, credentials and app effects were synthetic; real-account acceptance is pending.
  Website scripts remain unavailable; this does not change ordinary page execution.
  Additional dependencies, reusable installed integrations, arbitrary provider HTTP
  remain future work. Selected workspace-file recovery is now implemented below.
  No daily-browser rebuild was needed.
  Existing user-created file/folder selection also needs a separate Drive-only
  Picker consent/credential flow with verified account binding; no broader OAuth
  scopes or credential migration have been introduced. Broader provider access,
  automatic retention policies and agent-managed mode remain separate milestones.
  Real-account acceptance needs user-owned setup and explicit approval; website
  scripts remain deferred until compatible native engine support exists.

- **Connector simplicity milestone implemented** (`0e5a46c`): per-account read/write/direct/
  send switches remember supported tools and automatically approve bounded app-only
  requests. Sign-in alone grants nothing; file/browser/execution permissions remain
  separate. Disabling access stops matching tasks and clears matching saved rules.
  Schema-11 settings survive restart without reviving tasks; new tools require
  renewed choice. **600 regressions passed in 248.392 seconds**, nine focused
  switch checks and 21 existing account checks passed. Synthetic accounts only.
  Removing one-time Google Cloud setup still requires a shared production OAuth
  registration/publishing decision; no real registration or scopes were changed.

- **Selected file recovery implemented** (`b21c917`): user-only Files
  picker shares up to ten sealed files/32 MiB with no default selections.
  Copies preserve bytes/hash and gain fresh IDs with explicit source mappings;
  bytes stay outside recovery JSON and events. Draft/file copies, evidence and
  historical-write guards are atomic. Foreign/building/deleted/corrupt inputs,
  oversized selections/context and journal failures leave no partial imports.
  Restart keeps copied data and ends authority. Source deletion does not delete
  copies; packages/worker approvals never transfer. Workspace preview shows
  provenance. No schema migration or broader provider authority is needed.
  Acceptance: **646 regressions passed in 274.034 seconds**, 35 focused recovery
  checks and seventeen final file-recovery checks passed. Both independent MCP
  clients recovered generated ZIP files over stdio/HTTP after the real user picker
  and fresh workspace grants. The corrected Workspace preview passed for ordinary
  tasks without recovery too. Browser startup, screenshot inspection, Ruff/format,
  whitespace and local documentation links passed. Synthetic state/files only.

- **Earlier engineering checkpoint — worker setup diagnostics:** agents discover
  actual local-worker readiness and limits before uploading packages through
  `worker.capabilities`. One cached fixed background check per browser run uses
  no task data/accounts, creates no grant and consumes no task budget. Trusted
  Workers UI can retry after setup changes; agents cannot force new checks or
  inject source. Concurrent callers share the result; unavailable environments
  get sanitized setup guidance. Actual runs still recheck isolation/authority.
  Shutdown stops pending checks, and restart resets the support snapshot.
  Acceptance: **657 regressions passed in 285.324 seconds**, forty focused worker
  checks passed in 7.464 seconds, both independent MCP clients discovered setup,
  generated/recovered ZIP files and completed existing app/browser workflows over
  stdio/HTTP. The first stdio launch hit the tracked native Qt exit 139 before
  acceptance; the identical retry passed. Setup UI preview and startup/render/
  shutdown passed. Ruff/format, whitespace, dependencies and local doc links passed.
  All account data, profiles and file effects were synthetic.

- **Latest engineering checkpoint — document worker bundle:** the user chose
  the optional maintained bundle. `documents-v1` adds pinned ReportLab/openpyxl/
  Pillow plus exact transitive dependencies, without changing browser dependencies.
  The installer downloads hashed binary wheels and checks real sandbox generation;
  runs verify/copy them into a read-only mount off the Qt thread. Schema 13 binds
  receipts and remembered decisions to both source and environment hashes.
  Changed runtime/version needs new approval, tampered wheels fail before execution,
  and unavailable bundle support leaves stdlib usable. Agent discovery and trusted
  approval/receipt UI expose the chosen environment. See
  [bundle setup](../task-services.md#worker-dependency-decision).
  Google registration stays deferred; no arbitrary task installations/build tools
  or direct worker networking are enabled.
  Acceptance: **679 regressions passed in 314.002 seconds**, 61 focused worker
  checks passed in 12.902 seconds, ten final store/migration checks and eleven
  upload checks passed. SDK and independent raw clients generated actual PDF,
  XLSX and PNG files through approved source over both stdio and authenticated
  HTTP MCP. Native document self-check and browser startup/render/shutdown passed;
  approval previews were inspected. Ruff/format, dependency health, wheel-lock
  consistency, whitespace and local doc links passed. The earlier full run hit
  `native_input_unavailable` in one existing upload check; focused and final full
  checks passed, so native desktop acceptance remains tracked independently.
  All profiles, files and account effects were synthetic.

- **Latest engineering checkpoint — reviewed connector recipes:** the user chose
  recipes as the first agent-designed provider path, targeting built-in Google
  quality. Version 1 accepts immutable reviewed JSON for exact public HTTPS domains,
  verified account identities, bearer-token/keyring setup and selected read-only
  fields. Schema 14 adds task-owned proposal receipts; a nonmodal default-Deny
  popup installs exact source only. Installation grants no credentials, account
  access or task authority. Custom accounts reuse app switches, exact destinations,
  operation budgets, retained receipts and revocation. Removal persists denial
  before cleanup; failed removal/reinstallation cannot resurrect account access.
  Public-address pinning, TLS verification and redirect/private-network rejection
  constrain the transport. See [workflow and limits](../task-services.md#agent-designed-connector-recipes).
  Acceptance: **707 regressions passed in 297.745 seconds**; all 28 focused recipe
  checks passed in 4.567 seconds. SDK and independent raw clients proposed,
  reviewed, connected and read a synthetic recipe over stdio and authenticated
  HTTP alongside actual sandbox PDF/XLSX/PNG generation. Startup/render/shutdown,
  Ruff/format, dependency health, whitespace and local documentation links passed.
  Review and connection previews were inspected. Logs: `/tmp/steed-recipes-full.log`,
  `/tmp/steed-recipes-focused.log`, `/tmp/steed-recipes-mcp-{stdio,http}.log`, and
  `/tmp/steed-recipes-startup.log`. All data and credentials were synthetic.
- **Latest recipe checkpoint — native OAuth and collection reads:**
  reviewed sources now declare exact authorization/token endpoints and scopes.
  User-owned native client setup is imported once and reused through the keyring;
  sign-in uses external-browser PKCE/state/loopback callbacks. Refresh rotation is
  persisted only after identity verification; failed/revoked setup cannot publish
  data or resurrect credentials. Fixed collection paths use explicit destination
  `account`; source/tool limits and the existing read switch still apply. Provider
  UI identifies the actual domain/account and clears client files on provider
  changes. Current secret literals are redacted before JSON escaping. Example:
  `examples/connector_recipes/notes-oauth.json`. Acceptance: **723 regressions passed
  in 295.840 seconds**; 45 final recipe checks passed in 6.286 seconds, including
  subsequent malformed-catalog and immediate UI-refresh fixes. Both SDK/raw clients
  completed bearer and native OAuth collection workflows over stdio and authenticated
  HTTP, alongside real sandbox document generation. Review/setup previews were
  inspected and Ruff/format passed. Logs: `/tmp/steed-recipes-oauth-full.log`,
  `/tmp/steed-recipes-oauth-focused.log`, and
  `/tmp/steed-recipes-oauth-mcp-{stdio,http}.log`. All accounts were synthetic.
- **Latest recipe checkpoint — preflight and bounded pagination:** agent-visible
  `recipe.validate` checks sealed owned source offline and returns grammar diagnostics
  plus compiled domains/account selectors/tools, with no popup, installation or API
  request. A successful result explicitly leaves provider semantics unverified.
  Fixed collection tools may follow one reviewed cursor parameter for 1–5 pages;
  results merge selected arrays and report pages/more-available without exposing
  cursors. Identity, revocation, source/account state and output limits are checked
  between pages. Canonical query encoding prevents arbitrary URL/query dispatch.
  Example: `examples/connector_recipes/notes-paginated.json`. Acceptance: **733
  regressions passed in 283.392 seconds**; 55 final recipe checks passed in 6.169
  seconds, including a subsequent long-metadata discovery/registration fix. Both
  external clients preflighted and read bearer/OAuth/paginated recipes over both
  MCP transports alongside sandbox document generation. Startup crashed during
  concurrent offscreen Vulkan initialization; a standalone retry passed. Its cause
  is not proven, so native desktop startup remains a separate check. Ruff/format,
  whitespace, local doc links, 53-tool discovery and all three synthetic examples
  passed; pagination previews were inspected. Logs:
  `/tmp/steed-recipes-pagination-{full,focused,mcp-stdio,mcp-http}.log` and
  `/tmp/steed-recipes-pagination-startup-alone.log`. All accounts/data were synthetic.
- **Latest recipe checkpoint — reviewed creates/sends:** version 2 preserves v1
  reads and adds single POST create/send tools with bounded flat draft bindings,
  one exact email recipient for sends, optional receipt operation headers and exact-ID
  read-only probes. Installation lists actual effects/paths/bindings; every mutation
  needs a default-Deny **Review App Action** popup with its exact payload/account/URL.
  Saved permissions and account switches never skip this payload review. Source,
  identity, account and live authority are rechecked; uncertain results and same-ID
  replays never resend. Preview content is scrubbed on decisions/restart. OAuth form
  exchange remains separate from pinned API POSTs. Acceptance: **755 regressions
  passed in 297.778 seconds**; 63 final recipe checks passed in 12.762 seconds,
  including a subsequent shared-path/header fix. Both SDK/raw MCP transports passed
  synthetic create/send and post-commit uncertainty/reconciliation alongside real
  document workers. Standalone startup/render/shutdown, Ruff/format, whitespace,
  four example parses and local doc links passed; source/create/send previews were
  inspected. Logs: `/tmp/steed-recipe-writes-{full,final-recipes,mcp-stdio,mcp-http,startup}.log`.
  Real-provider/native desktop acceptance remains separate. See the
  [implemented format](../task-services.md#agent-designed-write-boundary).
- **Approved next architecture — declarative request plans:** the user chose bounded
  declarative plans with trusted per-step receipts before sandboxed adapter packages. This affects result-dependent multi-call writes, provider-specific
  transforms and revision-guarded edits needed for broader Google-level capability.
  Recommended direction: declarative plans first, preserving one reviewed payload
  per mutation, exact step authority and no continuation after uncertain completion.
  The first sequential POST subset is implemented; read steps, transforms,
  revision guards and per-step reconciliation remain ahead. No generated code
  runs in the browser.
  See the [concrete design comparison](../task-services.md#complex-provider-workflow-decision).
  Google registration remains deferred independently.

- **Nested request templates implemented:** recipe v3 supports immutable bounded
  objects/arrays/literals with explicit draft, destination and operation-ID string
  bindings. Exact serialized payload review and single-dispatch receipts remain
  mandatory; v1/v2 stay compatible. Depth, node, item and byte limits apply before
  HTTP entry. All 774 regressions passed in 299.980 seconds; 71 final recipe checks
  passed in 14.370 seconds after popup/fixture refinements. Stdio MCP completed
  nested creates and lost-reply reconciliation from both independent clients;
  authenticated HTTP completed the same checks. Source/payload previews were
  inspected. Ordered result-dependent POST execution follows below; provider-specific headers,
  sign-in and API semantics still require separate acceptance.

- **Parallel connector handoff — published implementation scope:**
  [priority list and ownership](connectors-handoff-2026-10.md) identifies Todoist,
  owner-pinned GitHub and GitLab.com starter subsets that execute through unchanged
  v2 recipes. Four synthetic wire-shape compatibility checks passed; Microsoft reads
  are a conditional bearer-token subset, not verified integrated OAuth. Notion,
  Slack and broader integrations have explicit framework gaps. Another agent owns
  new provider data/fixtures/setup and its isolated test file; this agent owns the
  shared catalog/picker and declarative plan runtime. Default discovery requires
  one generic catalog addition and must preserve explicit recipe/account review.

- **Packaged service picker implemented:** App Connections → Add a Service reads
  JSON profiles from `connectors/recipes/` without provider-specific Python.
  The existing default-Deny source review, account connection, switches and task
  grants remain separate. Changed selections fail closed; malformed, oversized,
  symlink and special-file entries are skipped. Six focused regressions passed;
  the synthetic picker was visually inspected. Starter service data remains the
  other contributor's isolated work; compatibility proofs are not shipped defaults.

- **Sequential request plans implemented:** recipe v4 adds 2–5 ordered POST steps,
  prior selected-string bindings and one bounded result-derived path segment.
  Each mutation has a journaled child receipt, independent operation ID, durable
  payload hash and default-Deny Step N/M review. Failure, denial, revocation,
  cancellation, timeout or uncertainty stops later writes; partial completion is
  retained and restart never resumes. Schema 15 owns links on the Qt thread;
  future child slots are reserved within receipt quotas. Clearing parent results
  clears children and stops pending work. Read steps, transforms, headers, revision
  edits and per-step reconciliation are not in this first subset. Both SDK/raw
  clients completed success and uncertain-stop sequences over stdio/authenticated
  HTTP alongside document workers. All 791 final regressions passed in 284.704
  seconds; 88 final recipe checks passed in 19.453 seconds. Legacy migrations
  preserve earlier receipts and events while adding schema 15. Four starter
  compatibility checks, standalone startup/render/shutdown, Ruff/format,
  whitespace, 52 local link targets and six recipe examples passed; source/step
  previews were visually inspected.
  Logs: `/tmp/steed-plans-{final-full,final-recipes,mcp-stdio,mcp-http,startup}.log`.
  See the [implemented format](../task-services.md#sequential-connector-request-plans).

- **External-agent conversations implemented:** Agents → Agent Sidebar
  (`Ctrl+Shift+A`) opens a lazy, movable dock for the selected external MCP task.
  Explicit user messages, cumulative streamed replies, read indicators, pending
  review/file/draft/receipt counts and nonmodal task controls are available.
  Messages grant no browser/app authority; profile grants remain pinned. Hide
  preserves work; cancel/disconnect/restart retain interrupted history. Incognito
  disables conversation and clears unsent drafts. Schema 16 retains private,
  bounded bodies separately from content-free activity events; exact replies are
  idempotent and cleared IDs remain tombstoned. Agents cannot impersonate users,
  clear messages or read another connection's conversation.
  Validation: 813 regressions passed in 316.979 seconds; 46 final agent/UI checks
  passed in 11.874 seconds and 12 final conversation checks in 2.302 seconds.
  Independent SDK/raw clients exchanged actual sidebar prompts/replies over both
  stdio and authenticated HTTP alongside reviewed recipes and document workers.
  Ordinary user/shared-profile browsing and cleanup passed. Startup/render/shutdown,
  Ruff/format, whitespace and relative file links passed; the synthetic dock was
  visually inspected. The first full run hit an offscreen Vulkan initialization
  crash before any test; the standalone retry passed. Desktop acceptance and idle
  external-client wake-up remain separate checks.
  Logs: `/tmp/steed-sidebar-{full,final-focused,messages,mcp-stdio,mcp-http,startup}.log`.
  See [conversation setup and protocol](../agents.md#external-agent-conversation-sidebar).

- **External-client setup simplified:** Agents → Agent Connection Setup… and the
  sidebar's Connection Setup… button open one nonmodal screen with explicit local
  opt-in, copyable interpreter/adapter/discovery settings and a mailbox starter
  prompt. No gateway token is read/copied, no client is launched/configured and
  opening/copying creates no task or grant. Missing optional dependencies are
  explained without installing them. Seven onboarding checks plus the existing
  final agent/sidebar checks passed: 53 tests in 13.277 seconds. The remaining
  integration decision is the external client to support for starting/waking an
  idle agent from Steed; the generic MCP mailbox remains available now. The final
  setup/sidebar previews were inspected; Ruff/format, whitespace and standalone
  browser startup/render/shutdown passed. No real external agent or account was
  launched by these synthetic checks.
  Logs: `/tmp/steed-agent-setup-{focused,preview,startup}.log`.

- **Latest usability checkpoint:** reusable approvals for both permission systems;
  **General → Website Permissions** and **Task Permissions → Saved Approvals** manage
  them. Removing an agent rule revokes linked tasks; disconnecting an app removes
  matching rules. Private website choices remain in memory. Google setup has a
  collapsible one-time import, reusable OS-keyring configuration and a forget
  control. No real account or hardware acceptance was performed.
- **Next user-dependent check:** restart Steed, review the new prompts, connect a
  user-owned Google client/account, and explicitly approve a small real Doc/email
  task. Decide about a shared production OAuth registration only if first-time
  user-owned setup remains too much. Additional providers and engine/script work
  remain separate follow-ups; password improvements remain deferred.

- **Shared Google registration — deferred by the user:** important for future
  sign-in-only onboarding, but not blocking local engineering milestones. Keep
  the existing one-time personal setup while proceeding with file recovery and
  local integration groundwork. Revisit publisher ownership/registration later.
  The distribution choices are: keep developer-owned Google setup for this personal
  browser, or distribute a shared Steed desktop OAuth registration for new users.
  Shared registration is recommended for sign-in-and-toggle onboarding. It needs
  a project owner, public branding/privacy/support identity and production scope
  verification; the application cannot invent these or publish on the user's behalf.
  The [concrete distribution proposal](../task-services.md#shared-google-registration-decision)
  preserves local credentials and existing scopes. No Cloud project or public
  registration has been created or changed.

**A3 remains in progress: native keyboard activation/selection and native form
submission and scoped uploads/downloads are implemented alongside inspection, links,
edits, scrolling, and waits.**
**A4 is in progress: external stdio and HTTP clients can use those operations.**
**A7 is in progress: watch/handoff, retained drafts and app receipts are implemented.**
**A6 is in progress: mock-tested scoped gateway and uncertain-write recovery.**
Autonomous milestones: scoped downloads `229cfe4`, task activity/receipts `6ad9b1a`,
scoped uploads `027a860`, retained task artifacts `333965c`, scoped app operations
`21790d3`, Google connections `db9e209`, permission/setup usability `7122e9a`,
safe material recovery `a34fee5`, archived receipt checks `7338b12`, and the
result-retention checkpoint `675e631`. Google folder destinations follow these
checkpoints at `e74dd12`; exact-tab document updates follow that milestone.
See the current resume checkpoint.
Continued from watch/control commit `045b706`,
following HTTP MCP commit `8266c37`
and basic MCP commit `d663fc5`
and native-networking commit
`1cd031f`; preceding safeguards/audit
commit `47ba170` and native-input commit `c23c6ce` remain earlier checkpoints.

- **Completed:** automatic approval popups; concurrent user/task profiles with
  separate grants; bounded inspection and stale-reference checks; observed link
  navigation; text entry with Chromium editor events/undo; checkbox/radio
  activation and selection by index through trusted Chromium keyboard events;
  bounded frame scrolling and ready/text waits;
  explicit main-document GET/POST form submission with native validation and
  submitter overrides, a one-use network ticket, and observed outcomes.
- **Authority:** edits need explicit `read` + `edit`; scrolling needs `read` +
  `scroll`; waits need `read`; submission needs `read` + `navigate` + `submit`.
  Upload selection needs `read` + `upload`, an exact approved source and upload
  origin; sending additionally needs submission authority. Old grants retain
  their scope. General POST, document scripts, password/multiple-file/folder
  forms, child-frame forms, and custom controls stay unavailable.
- **Boundary audit completed:** a loopback-only experiment verifies scriptless
  task controls and concurrent user networking. Forced website scripts exposed
  an existing-profile blob shared-worker origin bypass and WebRTC STUN traffic
  outside the HTTP filter in both profile modes. A fixture CSP blocked workers
  but its `webrtc` directive was unrecognized; STUN still reached the receiver.
  Native submission now rejects enabled document scripts before preparation and
  immediately before ticket authorization. No website-script grant was added.
- **Native prototype completed:** saved response-policy and packet-loss
  experiments, fresh-iframe worker and TURN/TCP controls, and an executable
  containment gate. Native response fulfillment with worker CSP blocked the
  tested shared workers; 1 ms/100% packet loss stopped observed STUN packets.
  TURN/TCP still established an unapproved connection in every candidate.
  A `Connection-Allowlist` header did not contain RTC on bundled Chromium
  140.0.7339.225. Ordinary concurrent user networking remains available.
  Prototype code lives only in tests; scripts remain unsupported in the app.
- **Engine decision completed:** select native per-document connection policy
  inside the same embedded engine/profile. A separate Chromium 153 diagnostic
  blocks unapproved fetch/WebSocket traffic, fresh-realm/shared workers, STUN UDP,
  and TURN/TCP while ordinary tabs retain networking and actual shared cookies/
  live storage. Cross-process frames, control-session detachment, and task disposal
  passed. This is engine feasibility, not an installed Steed backend. External
  task processes cannot share the actual `QWebEngineProfile`; profile-wide network
  restrictions would affect user tabs. Neither alternative meets this design.
- **Capability discovery implemented:** task-bound `task.capabilities` reports
  engine versions and implemented actions without a grant or page creation.
  Engine upgrades alone never confer script authority; Qt currently reports
  `engine_upgrade_required`, and newer candidates still require native integration.
- **Engine work deferred:** no compatible packaged build or maintainable small
  backport was established. Qt's upcoming separate WebEngine 6.140 release still
  uses Chromium 140; complete connection policy spans browser, renderer, network
  service, context lifetime and recovery. No engine fork/build or migration was
  started. Revisit native support when a maintained build or reproducible bounded
  integration is available; website scripts remain unavailable. See the
  [assessment](../task-services.md#bounded-engine-feasibility-assessment).
- **Local MCP implemented:** Agents → Enable Local Agent Access opts in for this
  run. A private authenticated Unix socket connects the optional official-SDK
  stdio adapter to the running Qt process. Thirty-six published tool schemas use
  connection-bound active-task identity and the existing broker/grant checks. Clients
  cannot approve or adopt other tasks. Disconnect, timeout, disabling access,
  journal failure and browser shutdown remove access and dispose owned pages.
  Two independent clients completed a synthetic shared-profile workflow with
  nonmodal approval, observed edits/form submission, user POST, cancellation,
  and cross-task denial. This is stdio acceptance, not the full A4 milestone.
  A completed/cancelled task can be followed by a new task on the same connection;
  it gets a fresh identity and requires new approval.
- **Live desktop MCP check passed:** an SDK client used the running browser's
  `codex test` profile with user-approved Wikipedia read/navigation access,
  inspected Python's article, followed its observed Guido van Rossum link,
  inspected that article and finished. Public-site browsing is verified;
  authenticated-site workflows remain unchecked.
- **Profile discovery added:** `steed_browser_context` reports the active or
  last-used window and exact profile selector before permission is requested.
  No page data is exposed; incognito/global are unavailable for shared sessions.
  Existing grants never follow later profile switches. The running browser needs
  a normal restart to expose this tool.
  Nineteen focused gateway/UI checks and the two-client MCP smoke passed, with
  discovery supplying the shared-profile selector. The full suite passed
  280 tests in 135.526 seconds (`/tmp/steed-context-full.log`); startup, lint,
  formatting, whitespace and local documentation links also passed.
- **HTTP MCP implemented:** the optional adapter binds only `127.0.0.1` and
  publishes generated per-client bearer credentials in a private endpoint file.
  Each credential-bound MCP session owns a separate browser connection/task;
  the same tools, Qt broker and approval popups apply. Website origins and
  foreign-credential session access are rejected. DELETE, explicit operation
  cancellation, idle expiry and shutdown close owned connections. HTTP socket
  disconnect alone is not task cancellation. Disabling browser access shuts
  down the adapter, removes its endpoint file, and invalidates its credentials.
  An SDK client passed the shared-profile form workflow over both transports;
  independent raw clients passed discovery, task reuse and ownership checks.
  The HTTP raw client also completed approved document reading and DELETE
  cancelled its next active task/page without disrupting the SDK client.
  Thirteen focused adapter checks and the full 290-test suite passed in
  142.947 seconds (`/tmp/steed-http-full.log`). Both transport smoke checks,
  startup/render/shutdown, lint/format/whitespace, 46 local links and `pip check`
  passed. One HTTP smoke run hit the known native Qt startup exit (139) before
  HTTP checks and passed on identical retry; that issue remains unresolved.
  See [HTTP setup](../agents.md#http-connection).
- **Watch and user handoff implemented:** explicit `attach` permission opens a
  nonmodal view of the existing page via Task Permissions or `steed_session_watch`.
  Watching leaves the agent running; Take Control waits for idle, blocks agent
  page operations/reads, and routes supported user actions through the same scoped
  broker. Return preserves page/profile state and invalidates old references.
  Closing an idle view returns control; closing during a pending user mutation
  cancels the task. Revocation, expiry, cancellation, and shutdown close views/pages.
  Direct rendered-page input remains blocked; website scripts, custom controls,
  and password/multiple-file/folder forms remain unavailable. Sixteen watch regressions passed,
  including queued/in-flight access, stale handles/keys, journal failure, Escape,
  direct deletion, and reopening before deferred deletion. A shared-window check
  verifies closing the watch's parent returns control while retaining the task's
  shared profile. Both MCP transport fixtures passed user edit/return and
  continued agent form completion alongside ordinary user POST.
  The full suite passed 308 tests in 146.577 seconds; startup/render/shutdown,
  Ruff/formatting/whitespace, 47 local documentation links, and `pip check` passed.
  The synthetic handoff window was visually inspected. No native startup exit was
  observed in this stage; the intermittent exit recorded above remains unresolved.
  See [handoff usage](../agents.md#watching-and-taking-control).
- **Scoped downloads implemented:** exact absolute destination files and a
  per-file byte limit appear in the approval popup. `page.download` follows an
  observed attachment link on the same task page/profile; `download.status` and
  `download.cancel` expose receipts and cleanup. Private descriptor-anchored
  staging and atomic, non-overwriting publication preserve existing files and
  deny path/symlink substitution. Known/unknown-size checks, cancellation,
  revocation, journal failure, and private/shared-profile origin boundaries are
  covered. Both MCP workflows completed a synthetic file alongside ordinary
  user POST; independent ordinary downloads still use their picker/history.
  The full 324-test run passed in 219.159 seconds; startup and both transports
  passed (`/tmp/steed-download-full.log`, `/tmp/steed-download-startup.log`,
  `/tmp/steed-download-stdio.log`, `/tmp/steed-download-http.log`).
  Additional streaming expiry, ownership, and post-publication evidence checks
  are recorded in the focused download verification. Downloads are bounded to
  30 seconds/100 MiB and expected attachments; HTML saving, redirected
  request URLs, replay/resume, and file-content access remain unsupported.
  Crash recovery of orphaned staging directories remains a follow-up.
  See [download usage](../task-services.md#scoped-task-downloads).
- **Autonomous continuation authorized:** implement and commit routine milestones
  until a major architecture/product choice requires user judgment.
- **Task activity implemented:** Task Permissions now has Permissions, Activity,
  and Downloads tabs. Activity shows a bounded chronological event tail; downloads
  show progress and retained receipts with user cancellation of a single transfer.
  Failed starts are terminal receipts rather than permanently pending rows.
  Synthetic UI/storage and both MCP workflows passed, including task isolation,
  hidden values, cancellation, and closing the dialog's polling timer.
  The full 331-test suite passed in 207.015 seconds
  (`/tmp/steed-activity-full.log`); startup/render/shutdown, both MCP transports,
  Ruff/formatting/whitespace also passed. The synthetic Downloads panel was
  visually inspected. Core artifact/connector activity remains future work.
- **Scoped uploads implemented:** grants list exact source files and upload sites,
  with a 5 MiB default/25 MiB maximum staging budget per session. `page.upload`
  creates an immutable private copy off the Qt thread and binds an observed
  single-file field; fresh inspection and a separate `page.submit` send it.
  Main-document POST multipart forms work without enabling website scripts.
  Source changes, symlinks, size limits, forbidden destinations, revocation,
  stale references, POST redirect replay and shared-profile cookies are covered.
  Take Control includes approved-source selection through **Select File**.
  Eleven focused checks passed in 15.751 seconds; both MCP transport workflows and
  startup passed (`/tmp/steed-upload-focused-release.log`,
  `/tmp/steed-upload-stdio-final.log`, `/tmp/steed-upload-http-final.log`,
  `/tmp/steed-upload-startup.log`). Full verification is recorded above.
  Selected copies remain until session teardown; crash orphan recovery remains
  pending. File bytes are never exported through tools or journal events.
  See [upload usage](../task-services.md#scoped-task-uploads).
- **Artifact milestone implemented:** explicit `artifact` permission saves immutable
  local notes/drafts, with bounded UTF-8/count/total storage, hashes and validated
  task-owned page-inspection source references. Content/title stay out of activity
  events. Task Permissions has an Artifacts tab for plain-text review after
  completion/restart and confirmed user deletion; agents cannot delete drafts.
  Both MCP workflows passed. The full 353-test suite passed, plus twelve final
  artifact checks after adding deletion. See
  [artifact usage](../task-services.md#retained-task-artifacts).
- **Engine direction agreed:** the remaining A3 scripted-site/custom-control
  and unrestricted takeover work needs native policy inside the shared embedded
  engine. Engine work remains deferred as previously agreed; routine file work
  has not changed that boundary. The user accepted prioritizing artifacts and
  connectors on the current backend. Continue A4/A7 artifact work and the A6
  connector gateway with mock services before real accounts; optional A5 providers
  remain pending. No rebuild has been started.
  Reconnect/resume, broader client compatibility, crash staging recovery and
  remaining A7 activity/artifact/styling work also stay pending.
- **Connector gateway implemented:** trusted definitions, exact tool/effect/destination
  scopes, immutable-draft binding, globally unique provider operation IDs and
  persistent receipts. Duplicate local request IDs return their old receipt;
  changed inputs and unauthorized destinations fail before adapter entry.
  Cancellation/timeout after dispatch leave writes uncertain; a trusted read-only
  lookup can confirm them without resending. Late observed outcomes remain visible
  to the user after cancellation; restart never restores grants or replays work.
  Task Permissions has an Apps panel for readable receipts, cancellation and
  active-task reconciliation. Nineteen focused checks, the full 374-test suite,
  both MCP document/email workflows and startup passed. Scope/receipt previews
  were visually checked. An SDK client and independent JSON-RPC client each
  completed the full workflow over both transports; exactly one document/email
  per client was stored, with distinct provider IDs and no uncertain-send replay
  (`/tmp/steed-connectors-independent-stdio.log`,
  `/tmp/steed-connectors-independent-http.log`). Final connector/UI/status checks,
  lint, formatting, whitespace, dependencies and local links passed. See
  [app operations](../task-services.md#browser-managed-application-operations).
- **Google-first connection milestone (implemented here):** user selected Google
  while requiring other providers to remain possible. Added provider-neutral
  immutable account identities/factories, App Connections UI, desktop OAuth with
  PKCE/state and Linux Secret Service storage. Google creates a plain-text Doc in
  `drive:root` or sends one draft to an exact `email:` mailbox using only selected
  Drive-file/Gmail-send scopes plus account identity. Reconnect gets a new connector
  ID; disconnect disables accounts, denies pending requests and revokes matching
  grants before credential removal. A cooperative authority gate checks each API
  request. Docs use a private operation property for read-only recovery; absence
  stays uncertain. Gmail cannot reconcile with send-only scope and is never retried.
  Thirty-nine new tests and the full 413-test suite passed; 58 final focused checks
  passed too. A local HTTP fixture completed OAuth → draft → Doc → email with lost
  acknowledgements and exactly one document/email, then restarted without replay.
  Both independent MCP clients still pass over both transports, and startup passed.
  A synthetic Microsoft provider uses the same setup/grant/receipt system without
  Google-specific changes to task policy; a real Microsoft/MCP adapter remains future work.
- **Next account acceptance checkpoint:** supply a user-controlled Google Cloud
  Desktop OAuth client and complete consent via Agents → App Connections, then run
  an explicitly approved small real Doc/email task. No real account was connected;
  tools remain absent until configured. Setup and limitations are documented in
  [Google app connections](../task-services.md#google-app-connections).
  Publishing a shared Steed OAuth client/verification is a separate future decision.
  Archived-task recovery with fresh approval and user-owned result clearing are
  implemented. Automatic retention policies, agent-managed mode and additional
  provider adapters remain follow-ups. Keep Gmail send-only
  unless the user deliberately chooses broader mailbox access.
- **Future script acceptance:** obtain a compatible embedded engine/native hook and integrate
  trusted connection/worker policy delivery before document code executes.
  Chrome's native policy starts at 152; installed Qt embeds 140 and the latest
  checked PyQt wheel remains 6.11.2. Updating current dependencies is insufficient.
  Validate arbitrary documents, response bytes/streaming/cookies/existing policies,
  exact scopes, redirects, initial and cross-process frames, adapter failure and
  revocation in the real shared-profile backend. The passing external diagnostic
  is a reference gate; the bundled-engine gate remains failing. Keep script-enabled
  forms denied until handler/serialized-body authority is enforceable.
  Native pointer gestures/custom controls remain pending. Bounded file grants
  are implemented; do not mark A3 complete before its full acceptance.
- **Resume points:** `services/browser_backend.py`, `services/page_inspector.py`,
  `services/page_inspection.js`, `services/page_actions.py`, `services/task_service.py`,
  `services/form_submission.py`, `services/task_input.py`, `services/task_network.py`,
  `services/task_downloads.py`, `tests/test_task_downloads.py`,
  `services/task_uploads.py`, `tests/test_task_uploads.py`,
  `services/engine_capabilities.py`, `services/task_client.py`,
  `agent_mcp.py`, `services/agent_gateway.py`, `services/agent_tools.py`,
  `ui/task_watch.py`, `ui/task_dialog.py`, `tests/test_task_watch.py`,
  `tests/test_agent_gateway.py`, `tests/test_agent_access_ui.py`, `tests/smoke_agent_mcp.py`,
  `tests/test_page_actions.py`, `tests/test_page_forms.py`,
  `services/app_accounts.py`, `services/app_connections.py`, `services/app_oauth.py`,
  `services/google_connector.py`, `ui/app_connections.py`,
  `tests/test_app_accounts.py`, `tests/test_app_connections.py`,
  `tests/test_google_connector.py`, `tests/smoke_google_connector.py`,
  `tests/smoke_task_active_content.py`, `tests/smoke_task_native_policy.py`,
  `tests/smoke_native_connection_policy.py`, and `tests/chromium_pipe.py`.
  Preserve concurrent
  user browsing, exact site scopes, stale-reference rejection, and request/receipt persistence.
- **Validation saved:** complete 274-test suite passed on Qt 6.11.2 in 162.856
  seconds (`/tmp/steed-mcp-final.log`); the preceding loopback audit and 20 form checks
  passed (`/tmp/steed-active-audit.log`, `/tmp/steed-active-forms.log`). Audit success
  means its controls ran, not that script networking is contained. New regressions
  verify enabled scripts before/during preflight send no form request. Native keys
  generate trusted events, support readable child frames, skip unavailable choices,
  and stop on focus drift, deadlines, and adapter loss. Native forms
  preserve validation, hidden fields, submitter overrides, and user POST access;
  redirect replay, late mutation/expiry, and failed evidence writes are covered.
  Text editing generated trusted input events and supported undo. Current startup
  smoke and Google HTTPS passed (`/tmp/steed-native-startup.log`,
  `/tmp/steed-native-google.log`). Prior profile-creation/startup exits remain
  unresolved; their form-stage diagnostic is `/tmp/steed-form-google-smoke.log`.
  The approval popup was visually checked in the form stage; lint/format passed. See the
  [verification record](../development.md#verification-record) for limits and smoke results.
  The expanded baseline and four native-policy comparisons also completed;
  the containment gate correctly exits 1 for TURN/TCP. See
  [native networking prototype](../task-services.md#native-networking-prototype).
  The Chromium 153 native-policy diagnostic passed all containment, shared-profile,
  cross-process-frame, detachment, and disposal checks
  (`/tmp/steed-native-connection-policy.log`); five capability checks passed
  (`/tmp/steed-capabilities-focused.log`). See the selected
  [engine direction](../task-services.md#active-script-engine-decision).
- **External validation saved:** two independent stdio clients passed the
  shared-profile form/cancellation fixture on MCP 2025-11-25
  (`/tmp/steed-agent-mcp-smoke.log`). Eleven focused transport/UI checks passed
  (`/tmp/steed-agent-focused.log`); last-owner teardown coverage was added to the
  full suite. Optional SDK dependencies passed `pip check`.
  The final 16 gateway/UI/adapter regressions include private discovery, malformed
  requests, ownership, denial evidence, last-owner shutdown, and sequential tasks
  requiring fresh approval. Real-entry-point startup/render/shutdown passed
  (`/tmp/steed-mcp-startup.log`). Lint/format/whitespace and 45 local links passed.
- **Still pending:** full A3 form/SPA/file acceptance, later A2 authority, broader A4
  interoperability/recovery, A5, remaining A6 real-provider/recovery work, and A7 styling.
  Password improvements stay deferred.
  Real-account collaboration and interactive desktop checks remain follow-ups.

| Area | Progress | Remaining work |
| --- | --- | --- |
| B1: Recently closed tabs | Implemented; automated checks passed | Repeated restore and profile isolation during daily use |
| B2: Global profiles and cookies | Implemented; automated checks passed | Real separate logins, restarts, logout, and explicit baseline resets |
| B3: User-agent settings and Google login | Implemented; local HTTPS checks passed; user reports account login works | Login persistence after restart and third-party Google sign-in |
| B4: Downloads | Implemented; local download checks passed | Real downloads, pause/resume where supported, and shutdown decisions |
| B5: Ad blocking | Implemented; filtering checks passed | Full-list updates, ordinary-site compatibility, and exceptions |
| B6: Cookie deletion | Implemented; exact deletion checks passed | Filtered deletion and no reinheritance during normal use |
| B7: Tab groups | Implemented; automated checks passed | Reordering, restoration, collapse, and persistence during daily use |
| History and refresh | Implemented; storage and browser checks passed | Desktop suggestions and Ctrl+R/F5/Ctrl+L use |
| Find in page and zoom | Implemented; five focused browser checks passed | Try Ctrl+F, match navigation, and zoom on ordinary sites |
| KeePassXC integration | Implemented; encrypted protocol, Chromium forms, and synthetic real-vault checks passed | Interactive pairing and filling/saving on real sites |
| Quick Switch startup and desktop launcher | Implemented; six focused regressions and launcher validation passed | Confirm application-menu launch and pinning during normal use |

Detailed results and test scope are recorded in the
[verification record](../development.md#verification-record). Passing automated
checks does not establish the unverified manual results below.

## October 3, 2026 — native Codex communication milestone

Implemented a shared external-provider interface and **New Codex Chat** in the
sidebar. Send starts native turns; streamed answers and queued follow-ups stay
on one task/thread. Dedicated CLI processes use Codex's own sign-in, normal
settings/tools/MCP services and permission policy. Newer bundled code runtime
support is included, following the user's choice; native approvals/questions
are relayed through readable nonmodal popups. Steed permissions remain separate.

Single-use expiring bindings attach the native MCP connection to exactly one
browser task. Cancellation/access disable/last-window shutdown stop the owned
process group, revoke unused bindings and retain read-only history. Clearing
chat suppresses erased replies and removes queued prompts without replay.
Profile privacy, multiwindow ownership, UTF-8 limits and storage failures have
regression coverage. No model credentials or runtime stderr are stored by Steed.

Validation: native Codex 0.160.0 with synthetic loopback Responses, GPT-6.1 Sol
code-runtime Steed status round trip and streaming follow-ups passed. Direct-tool
GPT-5.4 native calls, both MCP transports with document/recipe workflows and
ordinary browser startup/render/shutdown also passed. The full suite passed
**846 tests in 326.979 seconds** (`/tmp/steed-codex-final-full.log`); final
focused popup/sidebar checks cover subsequent UI refinements. Ruff lint/format
and whitespace checks passed.
Real account/model availability and desktop acceptance remain manual checks.

**Direction reaffirmed:** external agents keep their normal independent
capabilities; Steed provides communication and browser tools. The user's request
for taking over current profiles/tabs is distinct from existing-profile task
pages. The next milestone below implements user-directed live delegation;
restricted task pages retain their existing execution/network policy. Visual
and accessibility coverage remain the next observation work.

**External-agent integration research:** Muse supports custom service connectors;
dots uses enabled plugins/local computer connections. Neither cloud agent can
reach Steed's loopback endpoint without a local bridge or authenticated remote
transport. Instinct's public custom MCP setup was not verified. Detailed sources
and the compatibility matrix are in DOCUMENTATION.md. A cloud bridge requires
choosing its hosting/authentication/tunnel model before publishing an endpoint.
Google production registration remains deferred; connector implementation handoff
paths remain reserved for the other contributor.


### Live tab delegation and coverage signals — October 3, 2026

**Implemented:** sidebar handoff of the current tab or up to ten currently open
same-profile tabs, including other windows. Trusted tab selection plus fresh
`live`/read/action approval is required. `browser.tabs` lists only shared tabs;
`session.adopt` uses their original pages and preserves state, login, scripts,
normal service workers and background requests. No tab reload, copy, reparenting
or global profile policy change occurs. New/unshared/incognito tabs stay outside
this task's access.

A visible controller bar offers immediate **Take Back**. User address navigation,
Back/Forward/Reload, tab disposal, profile switch, task end/expiry and shutdown
return authority without destroying the borrowed page. Pending reads/native
input are cancelled and tab offers are removed, preventing re-adoption without
a fresh user handoff. Native activation executes ordinary website handlers;
form/button receipts distinguish dispatch from unknown site effects. Transfers
remain on the existing restricted path. No debug listener is opened.

Live controls tolerate unrelated mutations while revalidating their referenced
node, labels, base URI and ancestors. Restricted-page staleness remains unchanged.
DOM snapshots flag actual shadow roots, canvas, visual content and custom widgets
and distinguish enabled live scripts from restricted execution. Completeness is
never claimed; a task-aware model may later select further bounded observations.

**Validation:** 149 focused live/task/browser/MCP regressions passed in
115.303 seconds (`/tmp/steed-live-regressions.log`), including
identity/state retention, normal background POST, native handlers, pending input
revocation, loading, scope isolation, multiwindow/profile lifecycle and permission
replacement. Ruff lint/format and whitespace checks pass.

**Next:** approved viewport visual evidence (implemented in the following milestone), accessibility semantics and broader
frame/widget coverage; use coverage-driven deterministic routing first. Real
signed-in desktop acceptance remains a manual check. Cloud personal-agent access
still requires a local bridge or a separately chosen hosting/authentication model.
Google production registration and password improvements remain deferred.


### Approved viewport evidence — October 3, 2026

**Implemented:** `visual` is a separately described permission for native viewport
screenshots, including visible field values and embedded content omitted from
DOM reads. `page.capture` returns an MCP image content block plus bounded metadata
through the same stdio/HTTP adapter. Base64 pixels are not duplicated in text or
structured results and are not retained in the task journal. Other tool response
shapes remain unchanged.

Captures require an owned, visible view, approved top-level origin and read/visual
authority. The private bundled frontend captures without changing viewport,
scroll, normal scripts or service workers. Revocation, Take Back, navigation and
view/lifetime checks prevent late evidence release. JPEGs are limited to 1280×960
pixels and 256,000 bytes; clipping and viewport-only limitations remain explicit.
Native frontend failure reports an unavailable observation and grants no fallback
script/coordinate access.

**Validation:** all 873 regressions passed in 357.385 seconds. Native pixel/canvas
and viewport checks, clipping/size bounds, distinct consent, pending-capture
revocation and multiwindow runtime tab identity passed. SDK and independent raw
clients received native JPEG image blocks over stdio and authenticated HTTP.
Installed Codex direct and code-runtime transport checks passed with synthetic
loopback responses. Startup/render/shutdown passed with Qt 6.11.2; the settled
approval popup was visually checked with synthetic data. Ruff lint/format and
whitespace checks passed. Real signed-in desktop acceptance remains outstanding.
Logs: `/tmp/steed-live-visual-final-full.log`,
`/tmp/steed-live-visual-stdio.log`, `/tmp/steed-live-visual-http.log`,
`/tmp/steed-visual-startup-final.log`.

**Next observation work:** accessibility semantics, independently scoped frame
reads and native/custom-widget coverage. Route observations from deterministic
coverage signals first; external agents can already choose viewport evidence.
A lightweight ranking/router can be added when representative browsing tasks show
where it improves coverage or latency. No automatic claim of “all important
information,” implicit history access or remote cloud endpoint is introduced.
