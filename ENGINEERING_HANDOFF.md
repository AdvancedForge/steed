# Engineering handoff — October 15, 2026

## Start here

**HTML restyle H1 — October 15.** The owner's latest instruction is to begin the
HTML restyle, starting with the agent interface. This supersedes the old scope
below where it concerns maintained Steed UI. It does not authorize arbitrary
user/agent HTML to access data, or replace Qt/Chromium.

- `ui/agent_sidebar.py` is now a presentation controller and dock host; use
  `select_task`, `set_draft`, `send_message`, `start_chat` and presentation `state`
  in controller fixtures. There are no hidden legacy Widgets acting as a model.
- `ui/web/agents.html`, `agents.js` and `theme.css` are repository-owned assets.
  No frontend build or network server is required. The same surface survives
  sidebar/tab moves. Native permission/setup/task dialogs remain available.
- `ui/html_surface.py` owns an isolated off-the-record profile and only exposes
  ready/dispatch through QWebChannel. CSP nonces, denied navigation/network/file
  selection, command schemas, document generations and conversation contexts
  keep this bridge separate from website pages and editable start-page HTML.
  Never attach it to a normal browser profile or expose raw task/service objects.
- Draft versions prevent older render updates from replacing newer input. A
  matching send result unlocks the button; repeated clicks are not resubmitted.
  Private transitions synchronously cover old pixels before clearing the HTML.
  Interface reload restores controller-held drafts without replaying effects;
  closing a browser window explicitly destroys its UI page before its profile.
- Real HTML acceptance lives in `tests/test_html_agents.py`; controller/provider
  fixtures and `tests/smoke_agent_mcp.py` use the new controller methods. Light,
  dark, welcome and tab screenshots are `/tmp/steed-html-*.png`. H1 acceptance:
  **1,415 full regressions / 486.928 s**, then **27 final UI checks / 24.882 s**
  after storage-read recovery and connection-guidance fixes. Startup, SDK/raw
  stdio MCP, Ruff (249 files), dependencies and whitespace checks passed. Logs:
  `/tmp/steed-html-{acceptance,final-ui,startup,mcp}.log`. No real user accounts or
  profiles were used. Desktop/account checks remain distinct from these fixtures.
- Next: H2 shared settings/profile workspace; H3 history/bookmarks/downloads/file
  library; H4 task/panel presentation. Reuse the existing authorities rather than
  rewriting persistence or permissions. Toolbar/tab-strip HTML is a separate,
  later milestone. See the active H1 section in ROADMAP.md.

The October 13 checkpoint below remains the browser/agent capability baseline.

**Work resumed for daily agent blockers — October 13.** This section supersedes
old stop/handoff instructions below. The latest scope is grant reliability,
private live browsing, clear takeover, missing website actions, screenshot-guided
computer input and an explicit unsafe automatic-approval option. Those changes
are implemented and accepted; the current main checkpoint contains them. Do not restart the old
bookmark/download tasks or begin the deferred HTML bridge.

- New shared modules: `services/agent_errors.py` (maintained public diagnostics),
  `services/computer_input.py` (fixed native input/keys),
  `services/live_control.py` (pinned one-popup takeover) and
  `ui/live_control_review.py` (trusted review). Core private-live code lives in
  `services/browser_backend.py`; source routing/approval in `task_service.py`.
- Schema 21 replaces the one-live-grant index with separate pending/approved
  uniqueness. `approve_with_rule` ends prior approved grants in the same journal
  transaction. Preserve rollback and old permission fingerprints. Exact-repeat
  requests reuse receipts; approving replacement closes old task sessions.
- `computer` normalizes its read/visual/live/navigate/inspect/attach dependencies
  before review. Omitted session mode follows live authority; explicit restricted
  mode keeps old script-disabled retrieval. Private live pages use separate
  off-the-record profiles. Normal embeds load, but DOM frame access still obeys
  origin/same-origin limits; screenshot input is separately authorized.
- A capture ID binds one operation to the current visible page/revision/viewport
  for 60 seconds. Point coordinates refer to the returned image, mapped to CSS
  pixels. Native dispatch does not prove business success; never replay uncertain
  clicks/typing/keys. Input text/pixels are absent from journal bodies.
- YOLO is a trusted desktop, confirmation-required, per-run opt-in. Agents cannot
  enable it via MCP. It resolves exact Steed reviews, including writes/code/UI
  changes; auth/source setup and host/website permissions remain separate. Status
  bar/sidebar show it. Take Back returns a tab, but a fresh request can auto-share
  while YOLO is enabled. Old IDs never retake tabs. Recovery doesn't silently pick
  archived data. Keep mode changes/automatic decisions auditable and fail closed
  on journal failure.
- Generic MCP and native Codex instructions now describe the new workflow. There
  are 85 tools. Existing chats need restart/reconnect to pick up the catalogue.
  `tests/test_agent_daily_controls.py`, `test_agent_control_ui.py`, migration tests
  and updated stdio/HTTP synthetic journeys cover the contract. Keep Qt checks
  serial. User state/accounts remain untouched by automated checks.
- Final acceptance: **1,405 full regressions in 454.935 seconds**, 46 final UI
  checks in 17.583 seconds, SDK/raw stdio and authenticated HTTP, both installed
  Codex runtimes, startup/render/shutdown, light/dark previews, Ruff (247 files),
  dependencies, whitespace, 85 tool routes and all panel/recipe examples pass.
  This completes N5 broad acceptance too. All fixtures are synthetic/temporary.
  Logs: `/tmp/steed-agent-acceptance-oct13.log`,
  `/tmp/steed-agent-final-ui-oct13.log`,
  `/tmp/steed-agent-mcp-{stdio,http}-oct13.log`,
  `/tmp/steed-agent-native{,-direct}-oct13.log`,
  `/tmp/steed-agent-{startup,visual}-oct13.log`.
- A stdio retry reproduced the known offscreen Qt profile-startup segfault before
  task setup; preserve `/tmp/steed-agent-mcp-stdio-native-crash-oct13.log` as evidence.
  Later transport/native/startup pass. No production startup fix is claimed.
  The smoke fixture was extended for preceding private tasks and more renderer
  operations (120-second whole journey; original per-operation deadlines).

### Next work from this checkpoint

1. Create a fresh branch/worktree from published main; read the active October 13
   roadmap and [daily workflow guide](DOCUMENTATION.md#daily-browser-access-and-permission-diagnostics).
   Native/generic clients need a fresh chat or reconnect to load all 85 tools.
   Do not rerun the old N5 acceptance agenda or restart completed small tasks.
2. Address real desktop/site failures using exact public error codes, active
   grant status, session mode/control owner and snapshot guidance. Avoid collecting
   input text, auth headers, cookies or raw image bytes in diagnostics. For normal
   private websites request live/read or computer; restricted mode is still useful
   for static retrieval. Do not revive the old active-script networking workaround.
3. Continue bounded agent connection/recovery polish. Already implemented: model
   reports, native working/thinking/queue indicators, mailbox delivery receipts,
   one-popup takeover and permission error guidance. Generic polling does not
   prove thinking or wake idle website chats. Recovery must keep fresh authority
   and duplicate prevention; never replay dispatched browser/app effects.
4. Keep the independently recorded offscreen initialization crash separate from
   live permission failures. Diagnose with faulthandler and serial fixtures; do
   not hide failures with retries or disable policy broadly as a workaround.
5. Agent-built HTML's reviewed read-only data bridge, Google shared registration
   and password improvements remain deferred. Broader desktop input, popup/device
   workflows and complete accessibility/frame observation need their own concrete
   scopes; this milestone only exposes browser viewport input. External agents
   keep their own tools. Preserve native/manual/API workflows alongside generic MCP.

The following October 4 material is historical context; the current section above
takes precedence.

**Owner-requested handoff checkpoint.** The owner asked for quick external-agent
work only if it fit a small milestone; otherwise finish the smallest daily-use
task and hand off. External-agent recovery needs a broader lifecycle change, so
this checkpoint delivers direct bookmark opening B2 and saves the already-built
account projections N5 with their exact validation status. Stop here rather than
starting the HTML bridge or another feature. This section takes precedence over
older milestones below. The owner subsequently requested additional small tasks;
bookmark manager shortcut B3, search keyboard opening B4 and selected-result
keyboard opening B5 are also delivered.
The final two owner-requested small tasks are Downloads search and Copy Path,
instead of further bookmark work. The proposed Down-from-search/Ctrl+F bookmark
changes were never implemented. No broader engineering was started.

### What is saved

- **Downloads search and Copy Path complete:** `ui/download_dialog.py` filters
  filename/profile/destination using literal case-insensitive matching. Refresh
  restores selection by download ID; filtering a row out clears it rather than
  targeting another download. Copy Path copies only the selected record's full
  destination, even for incomplete/missing files. No file-open or download action
  occurs. Enter in search never invokes an implicit Pause/Close action.
  Five `test_download_dialog.py` checks pass in 0.075 seconds; existing
  real local-download completion/cancellation and menu checks pass (two tests,
  1.457 seconds). Light/dark native previews, Ruff/format (241 files) and
  whitespace pass. Logs:
  `/tmp/steed-download-small-tasks.log`, `/tmp/steed-download-browser-checks.log`.
  Broader N5 validation and agent-recovery work remain below. Stop at this final
  daily-use checkpoint as requested.
- **B5 complete:** the focused bookmark results list accepts arrow-key selection
  and Return/keypad Enter to open the chosen link once. Widget-scoped shortcuts
  preserve search Enter and double-click behavior. Nineteen bookmark checks pass
  in 3.016 seconds, including second-result selection and both Enter keys;
  Ruff/format/whitespace pass. Log: `/tmp/steed-bookmark-results-enter.log`.
  Agent contracts and next engineering milestones remain unchanged.
- **B4 complete:** Enter in bookmark search opens the first matching link using
  the existing profile guard and folder filter. An empty result keeps the dialog
  open instead of invoking Close. The search tooltip documents the action.
  Eighteen bookmark checks pass in 2.718 seconds, including actual Qt Return
  events for successful and empty searches; Ruff/format/whitespace pass.
  Log: `/tmp/steed-bookmark-search-enter.log`. Broader acceptance remains below.
- **B3 complete:** Ctrl+Shift+O opens Manage Bookmarks for the current profile.
  The action is under Tabs beside Bookmark This Page. An actual synthetic Qt
  keyboard check from address-field focus and the menu route pass; 17 existing
  bookmark/menu checks pass in 2.732 seconds. Logs:
  `/tmp/steed-bookmark-shortcut-{check,focused}.log`. Ruff/format/whitespace pass.
  No agent/runtime authority or new shared tool changes; the pending N5 broad
  acceptance and next-agent recovery milestones below remain the next work.
- **B2 complete:** `ui/bookmarks_dialog.py` adds Open and double-click opening;
  `browser_window.py::_manage_bookmarks` supplies a new-tab callback with a
  current-profile/closing check. Existing stable profile identity guards prevent
  opening into a recreated profile. Edit remains separate. Search/folder filters
  identify the selected bookmark by its stable ID. Sixteen bookmark checks pass
  in 3.178 seconds, including real button and double-click signal routing and
  stale profile-switch refusal.
- **N5 implemented, broad acceptance pending (`1466ea0`):** panel v7 adds explicit bounded
  list/table mappings; `services/panel_mapping.py` owns validation/projection,
  `services/panel_accounts.py` retains only selected values and preserves typed
  empty shapes on failure/timeout. `ui/data_panels.py` reviews the exact mapping
  and shows omission counts. Keep manual Refresh, separate source permissions,
  immediate scrubbing and versions 1–6 unchanged. Example:
  `examples/data_panels/account-table.json`.
- **Final checks:** 109 panel regressions pass in 30.360 seconds, including the
  new late-response/timeout regression. SDK/raw stdio and authenticated HTTP
  account-table workflows already pass. Light/dark bookmark and panel previews,
  startup/render/shutdown, Ruff lint/format (240 files) and whitespace pass.
  The most recent full suite is **1,363 tests before N5 and these daily UI follow-ups**; do not report a
  full-suite pass for this checkpoint. Both installed Codex smoke paths also
  remain to be rerun for v7. All test data is synthetic/temporary; real desktop
  and live-provider acceptance remain manual.
- Logs: `/tmp/steed-handoff-all-panels.log`,
  `/tmp/steed-bookmark-open-{final,visual}.log`,
  `/tmp/steed-panel-mappings-{stdio,http-retry,visual}.log`,
  `/tmp/steed-handoff-startup.log`. `/tmp` is session-local evidence, not required
  source; committed tests are the reproducible checks.

### Incoming engineer: next bounded milestones

1. Branch from published main in your own worktree, e.g.
   `engineering/daily-agent-recovery`. Leave the user's live browser, real
   `~/.custom_browser/`, and connector contributor paths alone. Run Qt checks
   serially with `QT_QPA_PLATFORM=offscreen` and
   `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`. First finish broad acceptance:

   ```bash
   .venv/bin/python -X faulthandler -m unittest discover -s tests -v
   .venv/bin/python -X faulthandler tests/smoke_codex_provider.py
   PYTHONPATH=tests .venv/bin/python -X faulthandler -c 'from smoke_codex_provider import run; run("gpt-5.4")'
   ```

   Record actual counts/results; update N5 acceptance before new runtime work.
   Preserve a faulthandler stack if the known offscreen profile-create crash
   recurs; do not hide it with retries or call it a production fix.
2. **Agent connection clarity:** inspect `ui/agent_sidebar.py::_update_activity`,
   `services/agent_providers.py` and `services/task_messages.py`. Thinking,
   working, replying, native queue counts and fetched-message indicators already
   exist. Improve missing connection/stopped/stale distinctions rather than
   reimplementing them. Generic MCP reads prove delivery, not that a model is
   thinking. Website chatbots remain copy/export unless they actually connect.
   Add focused `test_agent_sidebar.py`/provider regressions for the resulting
   behavior, with native light/dark review and exact user-facing recovery steps.
3. **Interrupted-chat recovery:** reuse the existing explicit task recovery
   picker and fresh grants. Keep prior messages/reports available, clearly show
   an interrupted turn, and let the user choose the next conversation/task.
   Never automatically resend a dispatched message, replay a tool/write, infer
   model identity, or restore old grants. Test queued messages, partial replies,
   lost transport and duplicate prevention. Run both independent MCP transports
   and both installed native runtime fixtures after shared-agent changes.
4. Then address concrete session/profile/file-library friction found in those
   workflows, one tested commit at a time. Update ROADMAP and this handoff at
   each accepted milestone. The reviewed read-only HTML bridge is approved but
   explicitly deferred; password improvements and shared Google registration
   are also deferred. Ask the owner only for a genuine new engineering decision.

Work has resumed on main. Reviewed and locally integrated engineering branch
`c93748e` and connector PR #3 head `0082a19`; shared typed reads were corrected in
`ee17331`. Recipe v8 adds distinct continuation bodies, boolean stop/success flags
and resource POST pagination. 155 recipe checks and SDK/raw stdio/HTTP journeys
pass using synthetic fixtures. See the newest roadmap entry for final validation
and publication status. Do not restart earlier completed milestones.

Owner-approved linked-panel behavior: hide linked contents immediately on source
revocation; refresh after restoration. Panels may outlive their creating task
with a separate visible permission for the panel/display profile.
Selected library-file bindings are delivered below; independently saved reports
are unchanged. The owner selected manual Refresh first for account-backed panels; this is now
implemented as panel v5. Proposal/approval/view never fetch; account read access
and panel/profile permission are separate. Results are memory-only and clear on
revocation/restart. Acceptance: **1,339 full tests in 407.066 seconds** and 89 combined panel
regressions in 23.132 seconds. SDK/raw stdio/HTTP, both installed Codex runtimes,
startup/render/shutdown and light/dark native previews pass with synthetic fixtures.
Logs: `/tmp/steed-account-panels-{full,stdio-retry,http-retry,native,native-direct,startup}.log`.
The owner selected reusable action buttons that send reviewed instructions to
an external agent first; panel v6 implements that choice. **1,350 full regressions in 441.204 seconds**
and 11 final instruction checks pass. SDK/raw stdio/HTTP, both installed Codex
runtimes, startup and light/dark previews pass. Logs:
`/tmp/steed-panel-instructions-{full,panels,final-focused,stdio-retry,http,native,native-direct,startup}.log`. Direct connector effects remain separate. Preserve
explicit recipient/profile and payload review; do not infer writes or model
disclosure from a panel read permission.

Bookmark organization B1 is delivered: search/folder filters, stable-ID
address/name/folder editing, shared-window refresh and atomic UTF-8 Netscape HTML
merge/export (8 MiB, 2,000 bookmarks). Nested folders use readable labels; imports
keep existing entries and reject overflow atomically. New Tab shows 24 links with
Manage all. Private imports stay in memory and stale profile dialogs cannot save
into recreated profiles. **1,363 full regressions in 419.348 seconds** and 13 focused checks in 2.047 seconds
pass. Startup, light/dark native previews, Ruff (238 files) and whitespace pass.
Logs: `/tmp/steed-bookmarks-full.log`, `/tmp/steed-bookmark-workflows-final-focused.log`,
`/tmp/steed-bookmarks-{visual,startup}.log`.
Panel v7 now implements explicit bounded account-result list/table projections:
review exact JSON pointers, retain projected fields only, disclose row/cell limits
and clear failed or revoked results. Final targeted acceptance passes 109 panel
checks; full-suite/native-runtime acceptance is handed off above. Preserve v1–6
grammars and manual-only account reads. Direct bookmark opening is delivered.
The owner approved a later reviewed read-only HTML data bridge and explicitly
deferred its priority. Finish native account projection broad acceptance and
external-agent connection/recovery polish first. The bridge is backlog
work: define app-specific source permissions, update/removal lifecycle, revocation
and private-data/navigation boundaries before implementation. Do not infer HTML-app
authority from editable page installation or native panel grants.

Previous library checkpoint acceptance: **1,320 full regressions in 399.325 seconds** plus 13 final
linked-panel checks in 4.018 seconds. SDK/raw stdio and authenticated HTTP journeys,
both installed Codex runtime paths, startup/render/shutdown, light/dark native
previews, Ruff, dependency, whitespace and example/document targets pass.
The first final stdio invocation reproduced the known offscreen Qt profile-create
segfault before task setup; the captured stack is at
`/tmp/steed-linked-panels-stdio.log`. Retry/HTTP/native/startup pass. Diagnose this
headless initialization failure separately; no production workaround is proven.
Logs: `/tmp/steed-continued-engineering-full.log`, `/tmp/steed-panel-library-final.log`,
`/tmp/steed-linked-panels-{stdio-retry,http,native,native-direct,startup}.log`.

Current checkpoint adds Global startup, editable `steed:newtab`/`steed:welcome`,
and native sealed-workspace/image plus reusable linked-library panels.
Read [the page guide](DOCUMENTATION.md#welcome-new-tab-and-bookmarks) and the newest
roadmap entry. `services/start_pages.py` owns profile-local settings/bookmarks/HTML;
`services/news_feed.py` performs optional bounded cookie-free feed reads. URL-scheme
registration occurs before QApplication. BrowserPage intercepts only maintained
page actions; custom HTML has no native action bridge. Both Home and New Tab are
independent settings. Legacy Default storage is retained, no longer protected as
a built-in. Global now supports explicit reviewed task sharing and stable identity.

`start_page_format.py`, `services/start_page_proposals.py` and the native review
implement external-agent edits. Schema 19 added immutable proposals; schema 20
adds panel-source permissions. New source sharing uses existing connected-task messages, with exact payload preview and an
8 KB limit. Agent declarations have a 32 KB UTF-8 limit; local imports allow 256 KB.
Native panels now support versions 1/2/3/4/5/6/7; selected library files are implemented
with reusable panel/profile read permission. Account-backed reads now support manual Refresh and explicit list/table mappings;
continue richer cards and reviewed action controls without adding arbitrary
code callbacks or conflating private data with global appearance exports.
Website chatbots still use copy/export or enabled folders;
external MCP/Codex clients discover the three start-page tools automatically.

Previous workspace/image validation checkpoint: **1,220 combined tests pass in 416.669 seconds**; 136
browser/profile checks, 22 page/proposal/feed checks and 45 native source/form
checks pass separately. SDK/raw MCP journeys pass over stdio and authenticated
HTTP with recipes, reviewed pages, sealed file/image publication and explicit form
delivery. Both installed Codex paths pass using local model fixtures; startup/
render/shutdown and light/dark/minimal page plus native image/file previews pass.
Ruff lint/format and whitespace pass. Logs:
`/tmp/steed-internal-pages-panels-{full,stdio,http,native,native-direct,startup}.log`.
All automated browser data was synthetic and temporary; desktop checks against
the user's actual running session remain manual. One early stdio invocation
exited with a Qt segmentation fault before useful output; subsequent faulthandler
stdio/HTTP and both Codex runs passed. Keep faulthandler enabled for transport
acceptance and capture a stack if this intermittent failure recurs. The final two
transport runs did not reproduce it. A separate raw smoke-client naming collision
was corrected; this changed the fixture, not the MCP implementation.

### Incoming engineer: first steps and ownership

1. Read `AGENTS.md`, this checkpoint, current `ROADMAP.md`, and the page/panel/MCP
   guides in `DOCUMENTATION.md`. Fetch main and create your own branch/worktree,
   such as `engineering/daily-agent-recovery`. Leave the user's running checkout and
   real `~/.custom_browser/` state alone. Keep Qt tests/smokes serial.
2. Verify baseline lint/format and the focused page/panel tests. Use the repository
   virtual environment and temporary fixtures. Keep `-X faulthandler` enabled for
   transport/native acceptance; logs under `/tmp` are session-local evidence.
3. Connector PR #3 was re-reviewed at `0082a19`, integrated and confirmed merged
   after published main `3a4e1c5`.
   119 provider/profile workflow checks and four compatibility checks pass.
   [Published v8 contract and detailed continuation](https://github.com/AdvancedForge/steed/pull/3#issuecomment-5983954160)
   cover live acceptance, minor worksheet cleanup and v7/v8 provider adoption.
   Contributor-owned data is not automatically promoted by new runtime contracts.
4. Own shared runtime/UI/MCP/root-doc changes; leave `connectors/recipes/`,
   `connectors/fixtures/`, `connectors/SETUP.md` and the contributor's provider tests
   to that contributor. Publish a typed contract before asking them to adopt it.
   Do not change old source hashes, credentials or grants by rewriting recipes.

### Next engineering milestones

**Shared typed POST/query reads — delivered.** Recipe versions 7/8 support
bounded inputs, POST identity (including JSON null), fixed parsed GraphQL queries,
explicit errors/success flags and distinct continuation paths. Examples and
capabilities describe the exact contract. Follow-ups belong to provider adoption,
not a duplicate shared-runtime implementation. Preserve old fingerprints and
version grammars, identity rechecks and no automatic write replay.

**Selected data sources — N3 delivered; richer mappings remain.** Selected library files are delivered with a
separate user-approved panel/profile read permission that survives task end.
Permission must remain visible/revocable; disabling a folder or panel access hides
linked data immediately. Refresh checks exact file identity and bounds. Saved
task snapshots retain their established retention behavior. Account panels are
delivered in v5 through reviewed read tools and identity checks; never infer account/task access
from presentation approval. No private content in appearance exports or audit
metadata, executable callbacks, or agent approval endpoints.

**External agents/chatbots — preserve on every milestone.** Generic MCP is the
common capability surface; native Codex is one provider. User editing, native agent
source-review proposals, explicit connected-task source sharing, and website-chat
copy/export/import must remain usable. Reconnect native chats after tool-catalogue
changes. Do not claim that a website chatbot has MCP merely because its chat is
open in Steed. Test both independent protocol clients and both installed Codex
runtime paths after shared-tool changes.

**Hosted apps — parked, not abandoned.** Editable internal HTML is already a
concrete isolated page use case. A broader mini-app host needs a concrete task,
bundle/update lifecycle and typed data bridge; page installation itself never
grants browser/library/account access. Preserve this option alongside native UI.

Main includes the persistent file library and bounded discovery, open-shadow
observation, reviewed static API headers, explicit identity-label alternatives,
and the simplified agent sidebar. Branch from the current tip of `main`, not an
older chat-only checkpoint. Read `AGENTS.md`, the top of `ROADMAP.md`, and
[library instructions](DOCUMENTATION.md#persistent-agent-file-library) first.
Historical roadmap checkpoints describe earlier milestones; the current-work
section and this handoff take precedence for the next engineering scope.

User priority: improve daily browsing and delegated/collaborative agent work.
Public-release gates are parked reference backlog. Google shared registration
and password-manager improvements remain deferred. No rebuild is proposed.
External agents retain their own tools; Steed supplies communication, browser
capabilities, reusable files and explicit access controls.

## Latest Codex/sidebar follow-up

The latest user says the native chat now stays connected but its agent reports
rejected access discovery, and asks to see the actual running model. The new
checkpoint fixes native tool admission and concurrent MCP requests; do not undo
`default_tools_approval_mode = "approve"` on the dedicated Steed server or replace
the bounded FIFO with immediate sibling rejection. `auto` was reproduced rejecting
permission requests before browser dispatch; `approve` admits the transport to
Steed's unchanged authorization/review layer, not permission for arbitrary
external effects. Inherited servers/sandbox/OS settings remain native. MCP calls
that were dispatched must still never be automatically replayed.

The card uses runtime `thread/start` model/provider and `model/rerouted` metadata,
not the requested selector value. Foreign threads cannot change it; owned-turn
checks and early-event buffering apply. Generic clients/missing metadata stay
unreported. Model reports are transient for this browser run; an ended chat is
marked last-reported. Keep new-chat selection separate from current model identity.
Known failed commands/codes/stages are value-free diagnostics; do not put request
arguments, exceptions, file bodies or credentials in them. Grant-free discovery
does not include live tabs: those still need user sharing and live/read approval.
Restart/new-chat is required to pick up changed native settings/instructions.
See the current ROADMAP.md acceptance record for final tests/logs. The original
live account task was not executed; no applications were submitted by these checks.

The preceding follow-up addressed the original disconnect:

The user reports Codex answering that it will work, then disconnecting. The exact
live task is not reproduced. Shared runtime now supports the current question RPC,
standalone clock reads before/after turns, and foreign-thread notification exclusion.
Unknown optional host RPCs receive method-not-found; they gain no authority.
Owned-turn identities and malformed-data limits remain enforced. Failure text
includes a content-free stage/event and exception class. Do not inspect real
credentials/transcripts to diagnose this; use isolated fixtures and reported event
names. Any uncertain external effects remain subject to the existing receipt rules.

Transient native activity feeds the sidebar's Sending/Thinking/Working/Replying/
Waiting/Ready card and queued-message count. Generic MCP execution stays explicitly
unknown. Light/dark sections and speaker headers keep plain-text rendering;
advanced details remain collapsed. Installed Codex 0.159.2 passes offline native
follow-ups and both tool runtime paths. See current ROADMAP.md acceptance for logs.
Manual layout L1 is accepted at `5ece7e8`; portable layout/proposal L2 is accepted.
Native action panels/text sizing L3 and bounded accessible form hints are delivered.
Native task/text/list data panels N1, explicit forms N2 and comparison tables are
delivered; v3 workspace/image sources are also delivered. Continue only the
remaining selected library/connector bindings and shared runtime work above.
See the current ROADMAP acceptance record and the accepted native-first data-panel plan.

## Configurable layout review and continuation

**L1 accepted on October 4.** All findings below were corrected. Main now has
mandatory address anchoring, explicit widget visibility, shared commit broadcasts,
three-way concurrent-edit conflict handling, native width bounds, normalized preset
selection and a scrollable dialog. The complete suite passes 1,030 checks; final
focused UI checks and startup pass. The findings below are historical review
evidence, not outstanding work. Continue with L2/L3 rather than redoing L1.

Reviewed `engineering/configurable-layout` at
`988b68be70f2570764d9f79d01d632eebbcbdb54`, based on main `67c4ba2`, in
`/tmp/steed-layout-review`. **Historical result: changes requested at that head.**
The original review head remains unchanged; corrected work is now merged on main.
Toolbar/menu settings, sidebar side/width, density, named presets and reset are
implemented. The bounded model and independent preview copies are useful; layout
settings remain separate from browser, file and account authority.

### Findings to fix before merge

1. **Hidden controls remain in the focus chain.** `ui/address_bar.py:104` removes
   widgets from the layout without hiding them. A shown Passwords button reports
   `isVisible() == True` while absent from the layout; Tab focuses it and Space
   emits its action. Set explicit visibility on every application and check
   shown-widget visibility and focus traversal on hide/show/reset/cancel.
2. **A second window overwrites shared saved layouts.** `browser_window.py:617`
   opens from stale window preferences and saves the whole document. Window A
   saves a left-sidebar named layout; window B changes only density and saves.
   Stored placement returns to right and the named-layout list becomes empty.
   Read canonical shared settings when editing and broadcast committed Save/Reset
   to all owners. Keep preview/cancel local and preserve intervening commits.
3. **Defaults change the established address bar.** `ui/address_bar.py:106` puts
   every button before the address input. Preserve Back, Forward, Reload, Home,
   address input, zoom, Passwords on first launch/reset. Represent the mandatory
   address anchor in the model; test the actual established widget order.
4. **Accepted widths cannot be honored.** `layout_preferences.py:32` accepts
   200–800 px, but the native sidebar has a 300 px minimum. Requesting 260 px
   produces 300 px. Align storage/editor constraints with the real widget and
   test populated docks and sidebar → tab → sidebar transitions. Define whether
   manual dock resizing persists; programmatic previews must not overwrite it.
5. **Real browser acceptance fails.** `tests/test_browser_layout.py:38`, `:52`
   and `:191` compare capitalized displayed titles with lowercase IDs; `:182`
   queries grants for nonexistent task `missing`, which correctly raises
   `KeyError`. Compare visible labels/order and create a real synthetic task for
   permission assertions. Keep real browser coverage rather than skipping it.

Also fix `ui/layout_dialog.py:332`: storage normalizes names, but the selector
refresh uses raw input. Extra spaces can leave another preset selected; refresh
with the normalized name and test applying the newly saved preset.

### Review evidence

Offscreen Qt with temporary synthetic profiles ran **55 checks in 3.172 seconds:
51 pass, three fail, one error**. All 199 Python files pass Ruff lint/format.
The contributor's reported GLX constraint does not prevent these checks in the
maintainer environment. Logs: `/tmp/steed-layout-review-tests.log`,
`/tmp/steed-layout-review-probe.log`; reproduction script:
`/tmp/steed-layout-review-probe.py`. No real accounts or library files were used.

### L1 — Finish manual layout acceptance

Fix the findings on the contributor branch. Add shown-widget regressions for
visibility/focus restoration, default/reset address anchor, two-window preset
retention, fresh dialog state, local cancellation, committed reset propagation
and actual sidebar bounds. Exercise presentation changes without starting agents
or granting permissions. Keep every menu and the address field reachable.

Run serially from that checkout, using its own environment or this compatible one:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu PYTHONPATH=tests:. /home/coolguy/Scripts/Python/Browser/.venv/bin/python -m unittest test_layout_preferences test_browser_layout -v
/home/coolguy/Scripts/Python/Browser/.venv/bin/python -m ruff check .
/home/coolguy/Scripts/Python/Browser/.venv/bin/python -m ruff format --check .
```

Run the complete suite with maintained worker bundles; record missing dependency
limitations separately. Run startup/render/shutdown and inspect light/dark normal
and small-window previews. Submit the new SHA, commands/results and screenshots
for maintainer review. Rebase after this acceptance handoff is published; do not
merge or push to main from the contributor branch.

### L2 — Reviewed agent layout proposals (delivered)

`layout_format.py` strictly validates portable versions 1/2; `services/layout_proposals.py`
pins immutable source and task receipts in schema 17. `steed_layout_capabilities`,
`steed_layout_propose`, `steed_layout_status` are published; no agent approval
endpoint exists. `ui/layout_review.py` and the shared approval queue provide the
native diff/preview/Apply popup, with deferred requests in Task Permissions → Layouts.
General imports/exports appearance; View recovers the prior appearance while keeping
presets. Config writes roll memory back on failure. The complete L3 acceptance
verifies the earlier split integrated/migration checks together. Do not redo this milestone. Original scope:

After L1 acceptance, add versioned JSON proposals with stable component IDs,
mandatory address/reset affordances, valid placements and bounded dimensions.
Provide validation, readable preview/diff, explicit Apply, export/import and
last-working-layout recovery. Unknown/invalid versions cannot damage the current
layout. Keep drafts task-bound and treat generated content as untrusted data.
UI approval must not grant browser/profile/file/connector authority. Generated
Python, callbacks, JavaScript and shell commands cannot execute through this format.

### L3 — Native panels and appearance (delivered)

`layout_components.py` owns the 12-action catalogue and bounds. `ui/layout_panel.py`
binds native buttons to existing QActions; `ui/layout_panel_editor.py` edits up to
two panels/eight actions each. Portable/stored version 2 adds panels and text size;
version 1 migrates inertly. Only browser controls receive larger fonts, including
explicit descendant sizes after dark styles; website zoom and engine fonts stay
unchanged. Page actions disable when the native chat tab is active. The full suite
passes 1,063 checks, with synthetic light/dark previews inspected. Do not introduce
callbacks, HTML or arbitrary commands into this format. Original scope:

Extend the accepted schema with maintained native components/actions and bounded
appearance settings. Preserve keyboard accessibility, small-window scrolling,
incognito isolation, shared windows and sidebar/tab ownership. Unknown actions
cannot invoke arbitrary code or acquire capabilities. Deliver useful increments
with real-widget acceptance. Keep Qt; an executable extension runtime is a
separate architectural decision.

### Accessible form metadata (delivered)

`services/page_inspection.js` resolves visible help/errors within each document or
open root; `TargetDescriptor` adds four defaulted fields without new commands or
grants. The published `page.inspect` tool documents them. References share the node
budget; live context is limited to 65,536 characters/128 ancestor levels, reported
as unavailable rather than actionable when exceeded. Ancestor hidden/editable checks
also close the existing referenced-label omission gap. Synthetic stdio/HTTP SDK
and raw clients assert all four metadata fields. Labels/help/errors share a 16,000-
character budget with `target_text_truncated`; the worst-case Unicode fixture fits
the MCP response envelope. Final complete acceptance passes 1,073 tests; 79 final
focused checks pass. Startup and both MCP transports pass with synthetic data.
See ROADMAP for logs. Original scope:

Add bounded descriptions, author-declared required/invalid state and visible error
hints to target observations. Preserve current-value omission, hidden-content
exclusion, origin/read grants and shadow-root scoping. Live target fingerprints
must detect changed referenced help/error text while permitting unrelated DOM
updates. Test on local synthetic pages, including hidden/value traps and live
stale-target failures. This is rendered-DOM metadata, not a complete accessibility
tree. Richer agent-authored UI/data bindings remain a separate future scope.

## Native data panels — Decision and continuation

**User decision accepted:** build native data panels first; keep locally hosted
HTML/JavaScript mini-apps on the table. No new confirmation is needed to continue
native component work. The original alternatives below remain design context:

1. **Maintained native components (recommended).** Agents compose a documented
   catalogue of text/list/form widgets. Steed renders them and binds approved task,
   library or connector results through existing services. Global layout preferences
   contain placement/style only; content and bindings live separately with explicit
   profile/task ownership. The same profile can remain collaborative across users,
   windows and tasks. Start with task summaries/lists and visible data-source labels,
   then reviewed form effects. This extends the current Qt architecture.
2. **Locally hosted custom apps.** Agents build HTML/JavaScript mini-app bundles in
   their local worker environment. Steed adds a dedicated view/host and a typed bridge
   to its existing capability services. This offers substantially more UI freedom,
   but requires a new lifecycle, packaging/update and data-ownership contract. It
   should be a separate milestone rather than arbitrary callbacks in layout JSON.

This choice changes how much custom UI agents can implement without maintained
Steed widgets, and how that UI is distributed and kept working. External agents
retain independent tools in either option. Do not put personal notes, file bodies,
connector data or agent messages in the global appearance configuration or its
appearance export as a shortcut. Preserve task/profile grants and explicit user
handoff; UI review alone is not a data grant. Native-first scope is authorized.
Other parked engineering areas remain listed in ROADMAP; connector-owned paths
stay with their contributor.

### N1 — Native task/text/list panels (delivered)

`data_panel_format.py` publishes strict version-1 native declarations and bounds.
`services/data_panels.py` owns schema-18 proposals, profile identities, pinned
artifact bindings and atomic publication receipts. Agents have capabilities/
propose/status/publish only; approve/remove/view are trusted UI. `ui/data_panels.py`
provides read-only native widgets, the profile choice/preview popup and Data Panels
dock. Task Permissions includes deferred review/removal; the shared approval queue
opens requests outside private windows. The task/text/list transport workflows
run through independent SDK/raw clients on stdio and HTTP; native Codex discovers
the catalogue with both runtime paths. See ROADMAP for final counts/logs.

The complete suite passed 1,096 checks; the final UI selection follow-up passed
120 focused checks, including 24 panel regressions. Qt `QComboBox.findData` does
not reliably compare Python tuple payloads; preserve the chosen panel by comparing
`itemData` in Python. Keep the shown-widget selection/highlight regression. Review
and browser screenshots were inspected in both themes with synthetic data.

Saved drafts are explicitly retained after task end/restart. They are user UI
content, not renewed agent authority. Initial artifact digests are rechecked at
approval, each replacement is owned and pinned, and stale/current-profile content
cannot spill into private or recreated profiles. Never store bodies in appearance
preferences/exports or journal events. Publication is same-task, consecutive,
bounded and idempotent; no external effect is performed. Missing/tampered drafts
have no fallback. Task summary is metadata, not proof of real-world completion.

### N2 — Explicit native inputs (delivered)

Version 2 adds bounded text/choice fields and inert installation previews.
`ui/data_panel_form.py` provides field feedback and an exact payload/recipient
preview with explicit Send to agent. The owning task receives structured JSON
through the existing conversation/native chat route. Agent callbacks and direct
connector effects are absent; independent agent tools remain available.
`DataPanels.submit_form` is trusted UI-only and atomically stores the message plus
a value-free receipt through `TaskMessages._post(audit=...)`. Digests remain in
the append-only journal after Clear messages, so identical retries cannot notify
twice; changed ID reuse fails. No schema bump or input-value MCP reader is added.

`PanelContent.update_content` updates outputs in place to retain unsent fields,
previews and unaffected selection. Profile/panel switches, source removal/failure
and browser close scrub native input and close previews. Unsent values are
transient; sent values use existing conversation retention. Disconnected/ended
tasks and recreated profiles cannot receive submissions. Nested preview buttons
must set `autoDefault=False`: Qt otherwise made a disabled form button steal the
installation popup's Decline default. Keep the shown-dialog regression and both
independent protocol form journeys. See ROADMAP for final checks/logs.

Acceptance: 1,190 full regressions, 187 focused integration checks and 68 final UI
checks pass. Both actual MCP transports and both installed Codex runtime paths
pass; startup and synthetic light/dark visual acceptance pass. Main is the next
branch point. Forms and comparison tables need no further owner decision.

### N3 — Richer sources and components

Read-only owned-artifact comparison tables are delivered: eight distinct columns,
64 rows, text-only cells, version-2 declaration and pinned publication semantics.
Maintained static PNG/JPEG widgets and exact sealed owned workspace-file sources
are delivered in version 3. `data_panel_format.py` discovers source fields;
`services/data_panels.py` checks both authorities without double-consuming a mixed
operation and preserves old artifact-only retry fingerprints. Workspace reads
verify sealing/hash/MIME even with an image cache; `services/panel_images.py` uses
only the declared PNG/JPEG decoder and bounded dimensions/pixels. `ui/panel_image.py`
scrubs pixels on profile change. No database bump beyond 19 was needed. These are
retained task snapshots, including explicitly copied user inputs; deleting a
source hides it, while grant/task end freezes updates. New examples and stdio/HTTP
SDK/raw workflows cover mixed source publication. The combined 1,220-test suite
and both transports/runtimes pass; see the checkpoint at the top of this document.

Selected library files are delivered in version 4 with reusable panel/profile
permission and immediate revocation. Next add account-backed result bindings.
Recheck account identity and panel/source permission on refresh; do not infer read authority from panel review
or source names. Show freshness, unavailable/revoked sources and source labels.
Live private data and retained user drafts need distinct retention contracts.
Add reviewed service effects only by reusing Steed's existing permissions/payload
reviews/receipts; executable source never becomes a native widget callback.

### H1 — Optional locally hosted apps (retained backlog)

When native components prove insufficient for real user tasks, design a dedicated
local mini-app host, bundle/version/update lifecycle and typed capability bridge.
Reuse profile/task data ownership and receipts. Hosting approval must not grant
website/account/file access, and arbitrary UI code must not run inside Steed's Qt
controller. This is a complementary option, not a browser rebuild or restriction
on external agents' own runtime tools. Do not begin a broad host before a concrete
mini-app use case and reviewable lifecycle proposal exist.

## Branches, ownership and review

Work on a new branch such as `engineering/configurable-layout`, preferably in a
separate checkout/worktree. Do not modify the user's running checkout or profile:

```bash
git fetch origin
git worktree add -b engineering/configurable-layout ../steed-configurable-layout origin/main
```

Verify that the checkout contains `services/file_library.py` and this document.
Use its own environment or an explicitly chosen compatible environment; do not
assume the new worktree inherits `.venv`. Follow local sandbox permissions when
creating a sibling checkout. Never inspect real user profiles or credentials for
test fixtures.

The connector contributor owns `connectors/recipes/`, `connectors/fixtures/`,
`connectors/SETUP.md` and `tests/test_default_connector_profiles.py`. Leave those
paths to that contributor. The reviewed discovery branch has merged; reviewed
static request headers and reviewed identity-label alternatives are implemented
in the shared runtime as described below. Shared `services/`, `ui/`, MCP, root docs and shared-test changes need
coordination with the maintainer;
do not start overlapping runtime work in another contributor's checkout.

Commit each completed milestone on your branch, push it, and open/update a PR.
Describe resulting behavior, validation, limitations and exact head SHA. Include
a synthetic UI screenshot for visible changes. Submit for maintainer review;
do not merge, push directly to `main`, or self-approve. Rebase onto newer main
after approval/merge before the next milestone. The maintainer reviews the diff,
runs targeted Linux checks and records approval against the reviewed head.
If GitHub authentication identifies the PR author, formal self-approval is
unavailable: record the engineering assessment as a COMMENT instead.

## What is already implemented

- `services/file_library.py`: separate lazy SQLite library, persistent roots and
  enable switches, relative paths, binary chunks, revisions, atomic imports/
  commits, old versions, recursive Trash and recovery. Interrupted/cancelled
  writes never replay. Disabling cancels unfinished writes immediately.
- `ui/file_library.py`: **Agents → Agent File Library…** and sidebar entry, copy
  selected files/folders in a background reader, enable access, create subfolders,
  export, Trash, Versions and user-only confirmed recovery cleanup.
- `services/task_service.py`, `task_client.py`, `agent_tools.py`: twelve library
  commands exposed through both MCP transports and native agent chat. Total
  published tools at that library checkpoint: 73; current catalogue: 77 after
  layout/panel discovery and publication. Stable request IDs and expected revisions govern edits.
  The workspace bridge reuses library inputs and retains sealed outputs.
- `browser_window.py`, `ui/agent_sidebar.py`: shared-window ownership, private
  profile UI guards and scrollable controls in short native chat tabs.
- `services/file_library.py`, `ui/file_library.py`: bounded metadata discovery
  across enabled roots. `steed_library_search` returns folder id/name, relative
  path, kind, current revision and size in one call, matched as a literal path
  substring so `%`/`_` are never wildcards, ordered by folder name then path for
  deterministic cursor paging, with identical names in different roots kept
  distinguishable by folder id. Disabled roots, Trash and old versions are never
  returned and access is rechecked per call. The window filter uses the same
  literal match and reports when a row cap hid later entries. Metadata only: no
  content indexing, no decision model, no new approval prompts.

Preserve these decisions: imports are **copies**, not live host mounts. A root
enable switch explicitly grants **all authenticated connected agents** recursive
read/create/edit/remove access, including future contents, across tasks/restarts.
The managed library is shared across regular profiles. Entering incognito hides/
closes its UI; it does not revoke separately enabled global agent access. Browser,
website, execution and app-account permissions remain independent. Library CRUD
requires an active agent task but no repeated per-file/task grant. Workspace
bridges still require workspace permission. Agents cannot enable roots, choose
host paths, purge recovery copies or execute files through this interface.

Limits: 64 MiB/library file, 512 MiB including recovery/staging, 50 roots,
500 entries/import, 32 KiB chunks. UI Next/Previous controls page 200 entries;
agents page with `steed_library_search`. Worker workspace
limits remain 8 MiB/file, 32 MiB/task.
The database is private local storage, not an encrypted vault or forensic erase.
Normal websites never receive library access.

## Completed milestone: make reusable files easy to find

Delivered on `main` by reviewed merge `e20cfea`. The numbered scope below records
the original acceptance requirements; do not restart completed discovery work.

1. Add bounded filename/path discovery across enabled roots to the agent API and
   a simple search/filter in the library window. Start with metadata, not content
   indexing or a decision model. Return folder ID/name, relative path and current
   revision so the agent can read the right file using the existing interface.
2. Define deterministic pagination and explicit truncation. Same filenames in
   different roots must remain distinguishable. Treat query punctuation literally;
   never expose disabled-root names, contents or recovery data through agent
   search. Recheck access at each RPC/read, including after a root is disabled.
3. Improve navigation past the current 2,000-item UI ceiling using bounded paging
   or lazy expansion. Keep Qt/SQLite operations on the owning thread; background
   import readers must not use the Qt-owned database. Preserve selection and clear
   any UI search state when changing profiles or closing the dialog.
4. Add regressions for duplicate names, Unicode/literal `%`/`_`, empty and paged
   results, disabled roots, stale revisions, concurrent windows and close/profile
   changes. Extend actual MCP smoke journeys with new-task discovery/reuse.
5. Verify a real workflow with synthetic files: import once, locate a nested
   input from a new task, edit with a revision check, save a generated output,
   reopen/restart, then recover an older edit. Record desktop checks separately.

Do not add recurring approval prompts. Do not silently switch imports into live
host-folder access. Deliver this milestone for review before broadening scope.

## Observation checkpoint and remaining work

The first observation increment reads bounded open-shadow text/control names and
image descriptions. Root mutations and live label/host changes invalidate stale
references; roots have count/depth budgets, values stay omitted, and shadow
controls remain observation-only. Closed roots, slot ordering, canvas pixels and
unloaded/virtualized content still have explicit limits. Text-wait deadlines now
say when no inspection completed. Both actual MCP transports verify component
text/names and omitted private values, and startup passes. Final evidence is
recorded in ROADMAP.md.

Expand deterministic accessible names/roles and coverage signals in
`services/page_inspection.js`, `services/page_inspector.py` and
`services/browser_backend.py`, using the existing page/live fixtures. There is no
`services/browser_agent.py` module. Continue with ARIA descriptions, supported
widget semantics and dynamic/frame coverage after checking the actual code.
Keep snapshot/target freshness, read/inspect consent and profile boundaries.
Preserve ordinary JavaScript/network behavior for user-directed live delegation
and immediate **Take Back**. Do not create a separate restricted live browser.

Expose omissions rather than claiming every important page fact is captured.
Agents can use separately approved bounded viewport images when DOM evidence is
insufficient. Inspect actual Qt capabilities and feature-detect version-dependent
APIs before adopting them; retain older-engine compatibility. Use local HTML/
canvas/frame fixtures, not real signed-in pages. Update the acceptance journey
with a useful browser task combining a local page and a reusable library file.

## Shared connector engineering after corrected contracts

Connector PR [#2](https://github.com/AdvancedForge/steed/pull/2) head `ccf2c4a`
is integrated by `1383368`, with reviewed corrections at `65ced87`. All 80 Linux
profile checks and 127 shared runtime checks passed in an isolated combined
checkout. The new POSIX picker expectation now includes all five shipped services;
Graph descriptions distinguish driveItem IDs from sharing URLs, and Zotero source
review no longer claims a mismatched library must fail if it is accessible. The
setup matrix now reflects delivered static headers, identity alternatives and the
supplied-token Microsoft subset. Fixtures remain separate from live-account proof.
The contributor's C1–C5 pack is delivered; remaining work is live acceptance and
adopting new shared capabilities after their maintainer checkpoints. Re-review any
new source SHA; never silently migrate existing reviewed accounts to changed data.
The [latest exact-head review](https://github.com/AdvancedForge/steed/pull/2#pullrequestreview-5407033827)
records the corrections and detailed C6–C10 continuation: install/live-acceptance
preparation, adoption of typed POST/query reads after runtime delivery, separate
success/auth/paging contracts, complete agent workflows and reviewable readiness
evidence. These replace the completed C1–C5 assignment for new contributor work.

The published [v6 runtime coordination and next steps](https://github.com/AdvancedForge/steed/pull/2#issuecomment-5974337618)
records provider migration/fixture work after `11a28e9`. This runtime update does
not approve a changed PR head; re-review the contributor’s next exact SHA.

The static-header milestone is implemented: version 5 requires 1–4 canonical
`request_headers` with constrained non-secret values. Supported names are
`Notion-Version`, `X-GitHub-Api-Version`, `Zotero-API-Version` and `OCS-APIRequest`.
Versions 1–4 are unchanged. Version 5 retains v3 nested `$bind` requests and v4
plans. `steed_recipe_capabilities` exposes names/formats, limits and a synthetic
`headers_example`; `examples/connector_recipes/notes-headers.json` is the same
source. This example is not a live Notion implementation.

Source review and payload context show exact headers; identity, reads/paging,
writes/plan steps and reconciliation apply them. OAuth exchange/refresh never
receives them. Changed headers create a new source hash/provider; previous
accounts/grants/reviews cannot migrate to it. Old installed versions remain
usable until disabled. Names with different casing, arbitrary credentials,
reserved transport/auth headers, duplicate keys and injection are rejected.
All origin/DNS/TLS/redirect/body checks remain. Exact-wire, source-binding,
OAuth paging/refresh, uncertainty/no-replay and plan checks cover the contract.
At the v5 checkpoint, actual MCP plan workflows used the header example with
both SDK/raw clients; current journeys use v6 as described below. Versions 4/5
retain unit/integration coverage.

Reviewed **identity-label alternatives are implemented in recipe v6**. Identity
requires 1–3 distinct ordered pointers; only absent/null/empty labels fall back.
Malformed/whitespace/secret-bearing values fail; subject and resolved label retain
exact binding on every authorization/refresh/page. A reviewed subject-pointer
alternative can use its validated integer-to-string ID. Source review and metadata
show the order. Versions 1–5 retain strict grammar. V6 preserves nested writes/
plans and supports optional v5 headers. Capabilities expose `identity_example`;
`examples/connector_recipes/notes-identity.json` is synthetic. Actual MCP plan
journeys now use this example; v4/v5 retain regression coverage. No service-data
files were changed; live-provider acceptance is still separate.

**Next daily-workflow increment: remaining N3 sources.** Layout L1/L2/L3 and native
task/text/list/form/table/image plus sealed workspace bindings are delivered.
Library-file bindings are now delivered. Continue account-backed bindings; do not repeat completed layout,
form or workspace/image work. Keep generated code outside
the browser; optional hosted apps have their separate H1 lifecycle scope.

**Next shared connector increment:** provider adoption of delivered v7/v8 typed
reads, fixed GraphQL, identity POST and distinct continuation contracts. Shared
runtime is integrated on main; coordinate source/fixture promotion with the
connector contributor. Credential-derived headers, revision guards and transforms
still need their own reviewed types. Live-provider acceptance remains separate.

## Validation and checkpoint evidence

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

Previous v6/menu checkpoint:

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

Use `.venv/bin/python` and `unittest`. Headless Qt commands need
`QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu"`.
Run native Qt suites/smokes serially; concurrent graphics processes have caused
unrelated crashes. Socket, worker and namespace fixtures may require execution
outside the filesystem sandbox. Use temporary profiles and synthetic accounts.

Maintainer discovery review: **155 integration checks pass on Linux**, including
34 library checks, workspace/recovery, browser profiles, sidebar/chat and gateway.
The branch's GLX test limitation is resolved here. Corrected the ignored enabled
folder scope, overlong Unicode cursors and excessive row materialization; added
200-entry UI paging with full paths and a 2,103-file navigation regression.
Both actual MCP transport journeys pass. See the top of ROADMAP.md for the final
checkpoint; the earlier branch could not complete these browser/MCP checks in
its environment, so this evidence comes from maintainer integration.
The subsequent component-observation checkpoint passes the complete 926-test
suite in 381.348 seconds, validating both increments together.

Earlier checkpoint evidence: full suite **906 passed** before final polish; final **148
integration checks passed**, including **27 library regressions**, after all
changes. Both stdio and HTTP SDK/independent-client MCP journeys passed, including
document workers/recipe workflows and persistent files reused by another task.
Startup/render/shutdown passed on Qt 6.11.2. A settled synthetic library preview
was inspected. Ruff lint/format, whitespace and dependency checks passed.

Useful commands, adapted to the change:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
PYTHONPATH=tests .venv/bin/python -m unittest test_file_library test_task_workspace test_workspace_recovery test_agent_sidebar test_web_chat test_agent_gateway test_task_services test_task_profiles -v
.venv/bin/python tests/smoke_agent_mcp.py --transport stdio --documents --recipes
.venv/bin/python tests/smoke_agent_mcp.py --transport http --documents --recipes
.venv/bin/python tests/smoke_browser.py
```

The document-worker smoke needs the existing bundle; see DOCUMENTATION.md for
setup. Run the complete suite once for a substantial cross-cutting milestone.
Temporary checkpoint logs `/tmp/steed-library-{full,final-integration,stdio,http,startup}.log`
are session-local evidence, not files the incoming checkout can rely on. Record
your own commands/results. Update detailed usage in DOCUMENTATION.md and milestone
status in ROADMAP.md; keep README short.

## Decisions that still belong to the user

Stop for direction before live linked-host-folder semantics, cloud-agent transport
and identity/pairing, model-based extraction that sends page data or adds recurring
cost, automatic permanent retention/purge, or materially broader execution/account
authority. Public license/distribution/support policy and Google production
registration are also deferred owner decisions. Routine implementation choices,
tests and branch commits are authorized; finish reviewable work before requesting
any decision.
