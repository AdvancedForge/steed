# Steed roadmap

Steed is a daily browser first, with a controlled way for your own agents to
work in it: Steed provides browser capabilities, context, permissions,
execution and evidence; agents provide planning and generation.

How things work is in the [documentation](docs/README.md) (including the
[planned architecture](docs/architecture.md#planned-architecture)). What each
round changed, newest first, is in the [progress log](docs/history/progress-log.md).
The road to a public release is the [release plan](docs/RELEASE_PLAN.md).

## Where things stand

- **Everyday browser: usable daily.** Tabs, groups and split view, profiles and
  incognito, passwords and passkeys, bookmarks and history (with import), reader
  view, translation, dictation, spell check, ad blocking and safe browsing,
  Chrome (Manifest V3) extensions, site apps, printing. Linux only, run from
  source.
- **Customization: broad.** Lumen (Steed's look) and every colour, font and
  motion setting, `steed:style` themes and CSS, user scripts, panel apps, an
  editable New Tab and Welcome, a rearrangeable toolbar and menus, custom
  shortcuts.
- **Agents: working, still growing.** A sidebar for Codex, Claude Code and ACP
  agents (Claude Code and Codex chats can continue after Steed closes), local MCP access
  for other agents, page reading and actions under per-task permissions,
  watches, routines, app connections and agent-built workers, each reviewed
  before it runs. The milestones below track what is left.
- **Release: early preview.** Not packaged or security-reviewed yet; see the
  release plan.

## Browser acceptance checklist

Manual checks on a real desktop, still open unless ticked. Automated checks
cover much of the same ground; these confirm it by hand.

- [x] Run the complete regression suite (2,139 tests on November 19, 2026),
  startup smoke, local Google-login smoke, launcher validation, and lint/format
  checks.

- [ ] Launch from the applications menu; check graphics, navigation, search,
  media permissions, multiple windows, and clean shutdown.
- [ ] Verify Quick Switch and its keyboard shortcuts before the first switch.
- [ ] Reopen closed tabs repeatedly, switch profiles, and check independent
  histories and restored groups/positions.
- [ ] Test real global and separate profile logins, restart, logout, explicit
  cookie deletion, and confirmed global-baseline reset.
- [x] Google account sign-in: confirmed working after the compatibility fix
  (October 1, 2026).
- [ ] Verify user-agent settings and signed-in session persistence after restart;
  test third-party Google sign-in. Record the exact error if either fails.
- [ ] Download a real file; check progress, supported pause/resume, cancellation,
  opening/revealing files, and active-download shutdown confirmation.
- [ ] Update full filter lists, browse ordinary sites, and check exceptions after
  reload.
- [ ] Check filtered cookie deletion and tab-group operations during normal use.
- [ ] Try history suggestions, Ctrl+R/F5, and Ctrl+L.
- [ ] Try Ctrl+F, next/previous matches, Ctrl++/Ctrl+-, and Ctrl+0 on real sites.
- [ ] Save a login on a real site, see it filled next time, and import a KeePassXC CSV export.

Use the pinned project virtual environment for the login checks. The older
system Qt 6.4.2 runtime stalled in the local Google HTTPS fixture. Interactive
KeePassXC pairing was not covered by the seeded synthetic-vault smoke check.

## Remaining browser improvements

These are pending enhancements. Move them earlier when needed for a concrete
daily-use workflow; they do not automatically block the agent milestones.

- **Passwords:** save prompts, updates to saved logins, forms in frames and web
  components, TOTP codes, passkeys and Password Checkup are in place. Next:
  checking them against more real sign-in pages.
- **Extensions:** per-profile import, enable/disable/remove, startup restoration
  and action popups are in place. The Bitwarden crash (November 12, 2026) was
  Qt WebEngine 6.11 crashing when a profile closes while a just-installed
  extension's background worker is still registering; Steed now waits for it.
  Bitwarden 2026.10.2 installs and runs. Manual check: sign in to Bitwarden
  and fill a login on the desktop.
- **Bookmarks B1 — implemented above:** per-profile search/folder labels, stable-ID
  editing and atomic HTML import/export. Hierarchical empty-folder management and
  sync remain optional follow-ups.

## Agent collaboration milestones

Keep Python, PyQt6, and Qt WebEngine initially, with replaceable engine/provider
interfaces. Prefer text and structured state; support watching, takeover,
resume, revocation, and cancellation. A1 is implemented; A2 supports private and
existing-profile grants for the current backend. A3, A4, and A7 are in progress;
A5–A6 remain pending. Scripted tasks are deferred; supported operations are now
available to external stdio and HTTP clients.

### A1. Browser services and task storage — implemented

Implemented the service foundation under `services/`: private SQLite task
records and an append-only event journal, a named-command broker dispatching on
the Qt thread, a replaceable backend interface, and independent off-the-record
Qt pages. A deterministic local client opens/navigates/inspects a session,
attaches a view to the same page, and finishes the task. Services initialize on
demand and are shared until the last browser owner closes. Interrupted tasks
are recorded without replaying pages or commands.

Verification: four transactional storage checks and six real-Qt service checks
passed as part of the full 132-test suite. Coverage includes view-independent
navigation, same-page view attachment, task/cookie isolation, Qt-thread dispatch,
queued cancellation, append-only events, restart/shutdown recovery, and journal
failure handling. Qt test classes share one QApplication for the process to
avoid reinitializing WebEngine after application destruction.

Ordinary tabs and task pages now share navigation and modern/legacy permission
handling. Download lifetime is application-owned, with an injected destination
picker; screen-sharing and download dialogs remain UI components. Regressions
cover invalidated permissions, shutdown during prompts, task permission denial,
destination-provider failures, and exactly-once share selection/cancellation.
The current API is internal only. A2 adds task grants; external-agent access
remains pending.

Target: extract reusable browser services, dispatch Qt operations through a command
broker, define a backend interface, and store tasks/events in application-owned
SQLite. Acceptance: a deterministic client operates a task session without AI.

### A2. Task sessions and permissions — in progress

Implemented for the current task backend:

- Pending scopes gain authority only through user approval in the automatic
  popup or **Agents → Task Permissions**.
  Clients have task-bound handles and cannot approve themselves or impersonate
  another task.
- Exact origins, supported actions, session counts, monotonic expiry, operation
  budgets, and request budgets are checked at dispatch and in the engine.
  Redirects, frames, and resources stay inside the approved network scope.
- SQLite grant decisions and journal events commit atomically. Restart ends
  authority without replay. Revocation, cancellation, expiry, and exhausted
  budgets close pages and reject queued mutations.
- Permission requests automatically open a simple Approve/Deny popup; additional
  requests queue and the management dialog remains available for review/revocation.
- Existing-profile grants share live signed-in cookies/storage alongside user
  tabs and other tasks. Task pages have separate scopes and budgets. Profile
  rename/archive/delete remain blocked while tasks hold it; user switching,
  pop-outs, downloads, password operations, and cookie inspection continue.
- Private/DNS network access needs explicit permission. Document scripts, new
  workers and WebSockets are unavailable. Exact download destinations, upload
  sources/sites and byte limits are implemented. GET/HEAD remain ordinary
  retrieval methods; POST requires a one-use, explicitly approved native-form action.
  Shared-profile task pages bypass existing service workers through a private
  per-target adapter; user workers retain ordinary behavior. Setup failure
  denies task navigation. Storage changes persist and are disclosed.

Verification: policy/storage/service regressions and sixteen shared-profile
checks, plus four automatic-popup checks. Coverage includes task worker bypass
without disabling user workers, concurrent profiles, selective revocation,
setup/journal failures, and a browser menu failure check. Full browser regressions and startup smoke passed; a synthetic
approval dialog was rendered and visually inspected. Manual desktop use remains a
desktop follow-up.

Remaining A2 work: active document execution needs its own
scope enforcement. Unsupported authority remains rejected. External tools and
transports belong to later milestones.

Acceptance target: cross-task, profile, destination, and grant violations fail
before execution. Private and existing-profile document workflows are verified;
bounded file workflows are verified; active-document execution remains unsupported.

### A3. Page inspection and actions — in progress

Implemented inspection, navigation, scriptless control actions, and native forms:

- `page.inspect` requires an explicit `read` grant; existing metadata grants do
  not gain content access. It returns bounded text, frame provenance, and control
  descriptions with labels, roles, enabled/read-only states, and constraints.
- `page.target` checks browser-issued references against their session and most
  recent snapshot. Navigation, DOM/frame changes, replacement nodes, and a new
  inspection invalidate older references. Clients cannot supply scripts or selectors.
- Fixed inspection code runs in an isolated application context while document
  scripts stay disabled. Input values and editable text are omitted; page text
  remains untrusted. The journal stores evidence counts and identifiers, not the
  returned text or labels.
- Reads are asynchronous and bounded. Permissions are checked again before
  disclosure; timeouts, revocation, shutdown, and journal failures reject pending
  results. Same-origin frames are inspected; inaccessible frames, shadow DOM,
  canvas content, and truncation have explicit limitations.

- `page.navigate` follows a validated link target with explicit read/navigation
  authority. It returns an observed load outcome; stale references, blocked
  redirects, timeout, cancellation, and evidence failures cannot become success.

- `page.act` requires `read` + `edit`. It supports text entry, checkbox/radio
  activation, and native-select choice by index. Text uses Chromium's editor
  with trusted input events, undo, and length limits; activation and selection use
  trusted native keyboard/control events. Generic buttons, sensitive/file fields,
  specialized inputs, contenteditable, and custom controls remain unsupported.
- `page.scroll` requires `read` + `scroll` and a current frame snapshot; it
  returns observed offsets. `page.wait` uses read authority and bounded ready/text
  conditions. Values and inaccessible content remain omitted from text waits.
- Edit/scroll attempts invalidate references even without a DOM mutation.
  Requests and receipts have action IDs; entered text, option values, and wait
  queries stay outside the journal. Live permission checks, timeout, cancellation,
  shutdown, and failed evidence writes cannot become successful receipts.
- `page.submit` requires `read` + `navigate` + `submit`. Native validation and
  submitter overrides apply to main-document, same-window URL-encoded GET/POST
  forms. A per-page one-use ticket binds destination/method; arbitrary POST and
  POST redirect replay are denied. Approved single-file POST multipart forms
  are implemented; password, multiple/folder-file, child-frame, popup,
  and script-handled forms remain unsupported. User tabs keep ordinary POST access.
- Form receipts report validation/load/timeout outcomes and leave site effects
  unknown. No automatic retry is performed. Action bodies/field values stay out
  of the journal; URL evidence stores origins and omits potentially reflected titles.
- Native keyboard input runs through a fixed, private per-target adapter with
  separate off-the-record frontend storage. It establishes task focus and a
  1280×800 viewport when the page has none, without opening a window. Key batches
  are bounded and recheck node/document/focus/grant; receipts observe actual state.
  Failed adapters, interruption, and focus drift fail closed. User pages keep
  their controls/focus, workers, POST access, and profile ownership.

Verification: all 274 regressions passed. Five capability checks cover version
eligibility without script authority, unknown versions, discovery without grants/
pages, task identity/lifetime, and alternate-backend limitations. Six native-input checks and a
shared-profile control/focus check cover trusted events, viewless viewport setup,
child frames, unavailable option skipping, unchanged choices, bounded batches,
focus drift, deadlines/interruption, adapter loss, and frontend/profile teardown.
Twenty form/ticket checks plus
shared-profile and popup checks cover native GET/POST serialization, required
fields/validation overrides, submitter destinations/methods, named controls,
one-use method/destination binding, redirect boundaries, stale and last-moment
mutations, callback expiry, stop/revocation, deadlines, and request/receipt failures.
Existing control/wait checks cover editor events/undo, constrained entry,
clearing, activation, selection, unsupported controls, stale references, readable
child frames, scrolling, waits and deadlines, superseding loads, cancellation,
callback-time expiry, revocation, and request/receipt failures. Existing inspection,
navigation, profile, popup, and daily-browser regressions also passed.

Active-content audit: synthetic forced-script pages expose a shared-worker scope
bypass on existing profiles and WebRTC STUN outside the HTTP filter in both modes.
Scriptless controls pass; concurrent user networking remains available. Fixture
CSP stops shared workers but not STUN. Submission now checks that document scripts
are disabled before preparation and ticket authorization; two regressions pass.
See [audit details](docs/task-services.md#active-content-networking-audit).

Native prototype: response fulfillment with worker CSP prevents the tested
shared-worker/fresh-iframe origin bypass. Packet loss with 1 ms latency stops
STUN packets, but TURN/TCP still connects outside scope. Connection-Allowlist
did not contain RTC on the bundled Chromium 140 engine. The required-containment
gate exits 1 for that known gap; prototype changes are test-only.

Engine decision: native document connection policy on Chromium 153 passed the
loopback gate, including TURN/TCP, cross-process frames, control detachment, task
disposal, and ordinary tabs with shared cookies/live storage. Select this approach
inside the actual shared embedded engine; the external diagnostic is test-only.
Task clients can discover implemented actions through `task.capabilities`.

Deferred: obtain compatible embedded native-policy support, then integrate trusted
delivery and verify response semantics and all frame/failure/revocation paths.
Bundled Chromium 140 is insufficient; newer version eligibility alone does not
enable website scripts. The existing shared-profile model and user networking
must remain available. See the
[engine decision](docs/task-services.md#active-script-engine-decision).
Keep scripted forms denied until their body/handler authority is enforceable.
Native pointer gestures (click, hover, drag) and custom controls are implemented for
live sessions, including approved cross-origin frames. Exact upload-source/site
and download-destination grants accompany implemented file operations. Full form/SPA/file
acceptance remains pending.

Target: expose structured snapshots and revision-bound target handles with deterministic
navigation, form, upload, and download actions. Acceptance: static pages, forms,
SPAs, frames, and DOM replacement work with observed results; unsupported cases
request takeover or return clear limitations.

### A4. Framework-neutral MCP

**In progress; both core transports implemented.** Optional `agent_mcp.py` uses
the official Python SDK over stdio and authenticated loopback Streamable HTTP
and connects to an opted-in running browser through private authenticated local
IPC. Thirty-six published input schemas expose current task operations, capability
and selected-profile discovery, structured results, and browser-assigned identity.
User approval is required; shared-profile collaboration, cancellation, isolation,
and evidence were tested with an SDK client and an independent JSON-RPC client
on MCP 2025-11-25 over both transports. HTTP sessions are credential-bound and
own separate browser connections; cancellation/termination/expiry remove owned
task access. See [setup](docs/agents.md#local-agent-access-mcp).

Remaining: other protocol/client checks, reconnect/resume,
and future artifact/search/connector tools.
Acceptance: two independent MCP clients work through both transports without
framework-specific browser code. Core transport checks passed; full A4 is not
complete.

### A5. Optional search and classification

Add replaceable Exa search and Jev classification adapters with disclosure
permissions, budgets, provenance, caching, and deterministic fallbacks.
Acceptance: held-out benchmarks justify each enabled classification capability;
operation without providers remains functional.

### A6. Application connections and recovery

**In progress.** The grant-enforcing gateway, typed adapter boundary, exact
tool/effect/destination approvals, immutable drafts, unique provider operation IDs,
durable receipts, worker limits and read-only reconciliation are implemented.
Mock document/email MCP services pass over both transports, including deliberately
incorrect remote risk annotations, lost acknowledgements, no blind replay and
task ownership. Queued work can stop; dispatched effects remain uncertain until
observed. Restart preserves receipts and ends authority without replay.

Google-first provider/account setup, OS-keyring credentials, exact destinations
and least-privilege Docs/Gmail tools are implemented with synthetic acceptance.
The provider-neutral factory/account system also passes a synthetic second-provider
test. See [Google setup](docs/task-services.md#google-app-connections).

User-selected material recovery with fresh approval and matching historical-write
protection is implemented. Archived receipts now support exact read-only checks
under fresh authority, with independently owned receipts and atomic confirmation.
Google HTTP acceptance after restart recorded one Doc/email with no repeat mutation;
both independent MCP clients passed over both transports. Exact Steed-created
folder destinations, nested creation, read-only inspection and creation probes
are implemented. Adapter/gateway regressions and a real local HTTP fixture cover
foreign/trashed/shared-drive/read-only parent rejection, exact grants, lost
acknowledgements and no repeated creation. Remaining: real Google
acceptance, additional providers, existing-file selection/general document editing, automatic retention
policies and agent-managed mode. User-owned clearing of saved result text/links is
implemented without deleting receipt identity or the append-only journal. Gmail uncertain-send recovery deliberately stays manual with
send-only OAuth access. A shared production OAuth client/publishing model needs
separate user judgment. Acceptance of the gateway/Google adapter is synthetic;
real-account acceptance remains pending.

Exact-tab document inspection, bounded reads and plain-text appends are now
implemented for Steed-created Google Docs, using existing `drive.file` access.
Writes require immutable drafts, exact tab authority, live account checks and
`requiredRevisionId`. Text plus an operation named range commit atomically.
Lost replies remain uncertain; probes read the original range without resending.
The local HTTP fixture confirms exactly one folder/Doc/append, restart recovery
under fresh read-only authority, no copied draft, no other-tab text disclosure and
no automatic retry after revision rejection. Operation ranges are visible to
collaborators; a retained marker confirms application, not unchanged text.
Reviewed/plain-text direct replacement is implemented, with review by default
and a separate reusable direct-edit grant. Before/after
previews are private to trusted UI, default Deny, and scrubbed after decisions or
restart. Agents cannot approve them. New revision/text requires a fresh proposal.
The HTTP fixture exercises actual review and lost-reply confirmation without a
second replacement. Rich/unsupported bodies are rejected; formatting can change.
Broader structural edits remain future work;
existing-file selection needs separate account-bound consent. No rebuild, broad
OAuth scope, real account effect or password work was started.

### A7. Collaboration interface and styling

**In progress.** Same-page watch and scoped user handoff are implemented.
The nonmodal window offers observed links, supported fields, form submission,
and scrolling with the task's existing permissions. Agent operations pause during
user control; returning control preserves edits and requires fresh references.
Closing/reopening, revocation, concurrent shared-window lifetime, and both MCP
transport handoffs passed synthetic fixture checks. Full direct page input,
custom controls and styling remain pending. Task Permissions includes retained
plain-text task drafts with source evidence and confirmed user deletion, and
recent activity and download progress/retained receipts with cancellation.

Show tasks, authority, activity, artifacts, and evidence. Support watching,
exclusive takeover, resume, and cancellation. Add validated declarative chrome
styling with rollback and recovery. Acceptance: takeover preserves the same page,
stale snapshots are rejected, and malformed styling recovers safely.

### A8. Generated files and agent-built integrations

**Local worker implemented; broader integrations pending.** Agents can supply arbitrary byte formats and source
packages through owned immutable workspace files. User-selected input snapshots
are separate from host filesystem access; exact download grants cover agent
exports. The Workspace panel exposes provenance, hash, state, user export/deletion
and staged package capability requests. Staging grants nothing; execution uses
separate task/package/environment approval and an isolated local worker with stdlib
or the optional maintained PDF/XLSX/image bundle.
Limits are 8 MiB/file and 50 names/32 MiB/task. Sending/uploading exported files
continues to require its own permission. User-selected workspace-file recovery
is implemented; async local storage remains future work.

Implemented: local execution, resource budgets, broker-only data/app access,
credential separation, durable receipts, revocation/cleanup and adversarial checks.
Remaining: arbitrary dependencies/build tools, reusable installation/versioning/removal,
trusted effect mapping and credential/HTTP policy for new providers, and broader
recovery. Acceptance: an agent creates a useful missing generator/integration,
the user sees its actual requested capabilities, and isolated execution cannot
read unshared files, obtain host credentials, bypass app permissions or outlive revocation.

## End-to-end validation and rollout — pending

Validate research → document → email with fixtures and mock connectors first,
then user-configured real services. Roll out in order: read-only inspection,
controlled page writes, mock application writes, explicitly authorized real
writes.

Completion requires independent clients to finish the workflow, policy checks on
every controlled mutation, usable receipts/evidence, and regression coverage for
stale targets, hostile inputs, authorization failures, takeover, cancellation,
provider failures, restarts, and uncertain or duplicate writes. The detailed
[validation design](docs/architecture.md#end-to-end-validation-and-rollout) records
the required cases.

## Current engineering queue

Prioritize daily workflow and reuse the existing Qt architecture. The latest
request distinguishes configurable browser UI from saved agent workflows.

1. **Configurable UI:** manual settings, mandatory navigation, shared-window
   commits and reviewed portable agent layouts are implemented. Export/import
   and previous-layout recovery work. Maintained native action panels and bounded
   text/appearance are delivered, alongside reviewed native task/text/list data
   panels with profile-bound artifact updates, explicit native forms and tables.
   Sealed files/images and reusable library bindings are delivered. Next:
   manual account-backed reads and reusable agent instructions are delivered. Next:
   richer cards and interactive components; explicit list/table mappings are
   delivered above. Local HTML/JavaScript apps need the pending bridge decision.
   Keep personal panel data out of global appearance exports.
   Details: [layout usage](docs/architecture.md#configurable-browser-layout--implemented-first-milestone)
   and [agent proposals](docs/architecture.md#portable-layouts-and-agent-proposals--implemented).
2. **Connector interoperability:** typed read-only POST/query requests, bounded
   query inputs, fixed reviewed GraphQL documents, explicit application-error
   contracts, body/query cursors and distinct continuations are integrated as
   recipe v7/v8 on main. Credential-derived headers, revision guards
   and transforms need separate reviewed types. The
   C1–C5 connector data and fixture pack is integrated. Future provider
   adoption follows reviewed shared-runtime checkpoints and separate live-account
   acceptance. Production Google registration stays deferred.
3. **Better website understanding:** bounded accessible form hints are delivered.
   Remaining scope includes complete accessibility semantics, dynamic content/frame
   coverage and native actions for supported open-shadow controls.
   Retain explicit omissions for closed roots/canvas/unsupported frames. Any
   model-based extraction sending page content or adding cost needs user direction.
4. **Agent and file workflow polish:** generic external-client reconnect/wake-up,
   clearer connection health and task recovery, then useful managed-library content
   discovery and import ergonomics. Cloud-agent pairing and live host-folder
   mounting are separate user decisions; today's library imports remain copies.
5. **Browser and release backlog:** bookmarks/session and desktop compatibility
   acceptance, packaging/engine updates and release ownership/security review.
   Password-manager changes and shared Google registration remain deferred.
   [RELEASE_PLAN.md](docs/RELEASE_PLAN.md) tracks public-release gates separately.

Each increment needs a focused commit, meaningful regressions and updated usage/
acceptance notes. Layout work changes presentation, never browser/app/file authority.
There is no current requirement for a browser rebuild.

## Beyond the initial plan

Agent customization remains a product goal, including reviewed connector recipes,
generated files and configurable browser controls. The engineering queue above
starts with manual layout settings, then a supported declarative UI format and
agent-designed native panels. Installation/update needs versioning, review,
disable and rollback. Do not run generated browser Python or treat installation
as authority. Saved workflows are optional actions, not a substitute for UI settings.

**Agent conversation sidebar — external-agent implementation:** external agents
came first (decided October 3, 2026). A show/hide dock beside the current page
selects an existing MCP task, sends explicit text, shows streamed replies and
read markers, and opens that task's permissions/files/app receipts. Closing the
dock preserves the task; cancellation remains explicit. Ordinary browsing and
shared-profile collaboration retain their existing authority boundaries.

Generic external clients must actively read/reply through the task mailbox;
provider-specific automatic delivery is now implemented for Codex. Automatic
reconnect, task reassignment and wake-up for other clients remain pending.
Reviewed connector/file customization remains in scope, while declarative browser
styling/features need an installation/versioning/rollback contract before execution.

Built-in agent hosting, scheduling, autonomous planning, and model-account
management need a separate proposal after the collaboration layer is proven.
That proposal must address OS isolation, billing, credentials, resources, and
recovery.
