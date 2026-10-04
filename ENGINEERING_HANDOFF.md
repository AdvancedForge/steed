# Engineering handoff — October 4, 2026

## Start here

Work is paused at an accepted engineering milestone at the user's request for a
handoff. Branch from published `main`; do not restart earlier completed milestones.
Current checkpoint adds Global startup, editable `steed:newtab`/`steed:welcome`,
and native sealed-workspace text/JSON/image panels.
Read [the page guide](DOCUMENTATION.md#welcome-new-tab-and-bookmarks) and the newest
roadmap entry. `services/start_pages.py` owns profile-local settings/bookmarks/HTML;
`services/news_feed.py` performs optional bounded cookie-free feed reads. URL-scheme
registration occurs before QApplication. BrowserPage intercepts only maintained
page actions; custom HTML has no native action bridge. Both Home and New Tab are
independent settings. Legacy Default storage is retained, no longer protected as
a built-in. Global now supports explicit reviewed task sharing and stable identity.

`start_page_format.py`, `services/start_page_proposals.py` and the native review
implement external-agent edits; schema 19 adds immutable proposals. New source
sharing uses existing connected-task messages, with exact payload preview and an
8 KB limit. Agent declarations have a 32 KB UTF-8 limit; local imports allow 256 KB.
Native panels now support versions 1/2/3; the image/workspace milestone is
implemented. Continue selected library/connector sources without adding arbitrary
code callbacks or conflating private data with global appearance exports.
Website chatbots still use copy/export or enabled folders;
external MCP/Codex clients discover the three start-page tools automatically.

Validation checkpoint: **1,220 combined tests pass in 416.669 seconds**; 136
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
   such as `engineering/typed-post-reads`. Leave the user's running checkout and
   real `~/.custom_browser/` state alone. Keep Qt tests/smokes serial.
2. Verify baseline lint/format and the focused page/panel tests. Use the repository
   virtual environment and temporary fixtures. Keep `-X faulthandler` enabled for
   transport/native acceptance; logs under `/tmp` are session-local evidence.
3. Coordinate **PR #3** with the connector contributor. Exact reviewed head is
   `a7b7713ed3fe06b6c0587baeb6d01b7cd772c77c`, based on `629d8a4`. The independent
   isolated review passed 96 packaged-profile checks, 4 compatibility checks and
   repository lint/format. Read
   [the posted findings and C6–C10 continuation](https://github.com/AdvancedForge/steed/pull/3#issuecomment-5982781874).
   Acceptance steps, bounded GitLab quick actions, Linear auth-specific limits and
   self-contained POST source references need follow-up. It is not approved or
   merged. Re-review the contributor's exact updated head before integration;
   synthetic success must not become live-provider evidence.
4. Own shared runtime/UI/MCP/root-doc changes; leave `connectors/recipes/`,
   `connectors/fixtures/`, `connectors/SETUP.md` and the contributor's provider tests
   to that contributor. Publish a typed contract before asking them to adopt it.
   Do not change old source hashes, credentials or grants by rewriting recipes.

### Next engineering milestones

**Shared typed POST/query reads — next priority.** Design and implement a new
recipe version with bounded declarative request inputs, exact reviewed endpoints,
identity POST support, selected response fields and explicit application-error
contracts. Support fixed reviewed GraphQL reads without arbitrary queries or
mutations. Treat HTTP-200 GraphQL errors/partial data honestly; handle Dropbox
identity/list continuation and Notion cursor bodies. Preserve v1–v6 grammar,
credential/origin/header rules, source review, identity rechecks, cancellation and
no automatic write replay. Add parser/review/adapter/exact-wire/recovery tests plus
SDK/raw transport acceptance. Coordinate fixture promotion with PR #3's agent;
do not implement gated providers by disguising reads as creates.

**Selected data sources — remaining N3.** Existing v3 panels show immutable owned
task files, not live folders or accounts. Propose explicit library/connector
bindings and freshness/unavailable labels, with grant/identity rechecks on refresh.
Distinguish live data from deliberately saved drafts. Keep revoked sources hidden
unless an independently reviewed retention contract permits a snapshot; ask the
owner before adding automatic permanent retention. Keep field content and image
bytes out of appearance exports and audit metadata. Reuse existing services rather
than executable callbacks or new agent-side approval endpoints.

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

Next add selected library/connector-result bindings through existing grant checks. Recheck
permissions and identity on refresh; do not infer read authority from panel review
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
Continue only selected library/connector bindings; do not repeat completed layout,
form or workspace/image work. Keep generated code outside
the browser; optional hosted apps have their separate H1 lifecycle scope.

**Next shared connector increment:** typed read-only POST/query inputs, separate
from create/send and static headers. Define bounded source/request/response types,
visible source review, exact approved origin and credential handling, cancellation,
identity/page rechecks and safe replay behavior before provider adoption. Review
query semantics against official API contracts. Credential-derived headers,
revision guards and transforms need their own types. Contributor-owned data/fixture
paths remain separate. Coordinate the runtime increment before parallel work.

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
