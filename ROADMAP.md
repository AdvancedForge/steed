# Steed roadmap

Build a useful daily browser first, then add a controlled collaboration layer for
external agents. Steed provides browser capabilities, context, permissions,
execution, and evidence; external agents provide planning and generation.

See [DOCUMENTATION.md](DOCUMENTATION.md) for setup and implementation details,
including the [planned architecture](DOCUMENTATION.md#planned-architecture).

## Current work — October 1, 2026

Browser essentials include Ctrl+F find in page and per-tab zoom. The complete
251-test suite passed, including six new native-input checks and a shared-profile
focus/control-isolation check, plus existing form, popup, and browser regressions.
Current startup/render/shutdown and local Google HTTPS smoke checks passed on Qt 6.11.2.
The previous form-stage Google fixture exited natively during `QWebEngineProfile`
creation before passing on retry. An earlier startup fixture also exited natively.
Track that intermittent exit separately from desktop acceptance. Ruff, formatting,
and whitespace checks passed. Launcher validation passed
during the previous browser checks.

The user reports few complaints during daily use and authorized proceeding after
the checks available to the assistant. A1 is implemented, and A2 supports private
and concurrently shared existing-profile grants, with automatic approval popups.
File and active-page authority will be implemented alongside their A3 operations.
A3 includes content inspection, stale-target validation, observed link navigation,
scriptless control edits, native form submission, scrolling, and bounded waits.
Active document scripts, file actions, and complete form/SPA acceptance remain next.
Real-account login persistence, third-party Google sign-in, hardware permission
flows, and ordinary site usability remain follow-up checks; no automated fixture
establishes those results. Password improvements remain deferred at the user's request.

### Resume checkpoint

**A3 remains in progress: native keyboard activation/selection and native form
submission are implemented alongside inspection, links, edits, scrolling, and waits.**
Resumed from form-submission commit `9c51d97`; earlier control commit `4e90899`.

- **Completed:** automatic approval popups; concurrent user/task profiles with
  separate grants; bounded inspection and stale-reference checks; observed link
  navigation; text entry with Chromium editor events/undo; checkbox/radio
  activation and selection by index through trusted Chromium keyboard events;
  bounded frame scrolling and ready/text waits;
  explicit main-document GET/POST form submission with native validation and
  submitter overrides, a one-use network ticket, and observed outcomes.
- **Authority:** edits need explicit `read` + `edit`; scrolling needs `read` +
  `scroll`; waits need `read`; submission needs `read` + `navigate` + `submit`.
  Old grants retain their scope. General POST, document scripts, sensitive/file
  forms, child-frame/multipart forms, and custom controls stay unavailable.
- **Next task:** define and test active-script networking and submission boundaries
  before enabling scripts/SPA behavior. Include worker/blob contexts, WebSocket/
  WebRTC transports, and submit-handler mutations; preserve user-page networking.
  Native pointer gestures/custom controls remain pending. Add destination grants
  alongside uploads/downloads; do not mark A3 complete before its full acceptance.
- **Resume points:** `services/browser_backend.py`, `services/page_inspector.py`,
  `services/page_inspection.js`, `services/page_actions.py`, `services/task_service.py`,
  `services/form_submission.py`, `services/task_input.py`, `services/task_network.py`,
  `tests/test_page_actions.py`, and `tests/test_page_forms.py`. Preserve concurrent
  user browsing, exact site scopes, stale-reference rejection, and request/receipt persistence.
- **Validation saved:** complete 251-test suite passed on Qt 6.11.2. Native keys
  generate trusted events, support readable child frames, skip unavailable choices,
  and stop on focus drift, deadlines, and adapter loss. Native forms
  preserve validation, hidden fields, submitter overrides, and user POST access;
  redirect replay, late mutation/expiry, and failed evidence writes are covered.
  Text editing generated trusted input events and supported undo. Current startup
  smoke and Google HTTPS passed (`/tmp/steed-native-startup.log`,
  `/tmp/steed-native-google.log`). Prior profile-creation/startup exits remain
  unresolved; their form-stage diagnostic is `/tmp/steed-form-google-smoke.log`.
  The approval popup was visually checked in the form stage; lint/format passed. See the
  [verification record](DOCUMENTATION.md#verification-record) for limits and smoke results.
- **Still pending:** full A3 form/SPA/file acceptance, later A2 authority, A4
  external-agent/MCP access, and A5–A7. Password improvements stay deferred.
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
[verification record](DOCUMENTATION.md#verification-record). Passing automated
checks does not establish the unverified manual results below.

## Browser acceptance checklist

The user authorized proceeding with these manual follow-ups recorded. Resolve
any browser blocker found during continued use alongside A1 work.

- [x] Run the complete regression suite (251 checks), startup smoke,
  local Google-login smoke, launcher validation, and lint/format checks.

- [ ] Launch from the applications menu; check graphics, navigation, search,
  media permissions, multiple windows, and clean shutdown.
- [ ] Verify Quick Switch and its keyboard shortcuts before the first switch.
- [ ] Reopen closed tabs repeatedly, switch profiles, and check independent
  histories and restored groups/positions.
- [ ] Test real global and separate profile logins, restart, logout, explicit
  cookie deletion, and confirmed global-baseline reset.
- [x] Google account sign-in: user reported it working after the compatibility
  fix (October 1, 2026).
- [ ] Verify user-agent settings and signed-in session persistence after restart;
  test third-party Google sign-in. Record the exact error if either fails.
- [ ] Download a real file; check progress, supported pause/resume, cancellation,
  opening/revealing files, and active-download shutdown confirmation.
- [ ] Update full filter lists, browse ordinary sites, and check exceptions after
  reload.
- [ ] Check filtered cookie deletion and tab-group operations during normal use.
- [ ] Try history suggestions, Ctrl+R/F5, and Ctrl+L.
- [ ] Try Ctrl+F, next/previous matches, Ctrl++/Ctrl+-, and Ctrl+0 on real sites.
- [ ] Pair KeePassXC, fill with Ctrl+Shift+L, and save a login on the desktop.

Use the pinned project virtual environment for the login checks. The older
system Qt 6.4.2 runtime stalled in the local Google HTTPS fixture. Interactive
KeePassXC pairing was not covered by the seeded synthetic-vault smoke check.

## Remaining browser improvements

These are pending enhancements. Move them earlier when needed for a concrete
daily-use workflow; they do not automatically block the agent milestones.

- **Password convenience — deferred:** automatic save prompts, updating existing
  entries, iframe/shadow-root forms, TOTP, and passkeys. Revisit when the user
  wants to improve the password workflow.
- **Extensions:** per-profile import, enable/disable/remove, startup restoration,
  and action popups. Investigate the official Bitwarden package crash on the
  tested Qt 6.11 runtime before presenting it as supported. Start with Manifest
  V3 and verify Qt's limits.
- **Bookmarks:** per-profile folders, search, editing, and HTML import/export.

## Agent collaboration milestones

Keep Python, PyQt6, and Qt WebEngine initially, with replaceable engine/provider
interfaces. Prefer text and structured state; support watching, takeover,
resume, revocation, and cancellation. A1 is implemented; A2 supports private and
existing-profile grants for the current backend. A3 is in progress; A4–A7 remain
pending.

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
  popup or **File → Task Permissions**.
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
  workers, WebSockets, and file actions are unavailable. GET/HEAD remain ordinary
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

Remaining A2 work: destination-specific file grants must accompany A3's file
operations; active document execution needs its own
scope enforcement. Unsupported authority remains rejected. External tools and
transports belong to later milestones.

Acceptance target: cross-task, profile, destination, and grant violations fail
before execution. Private and existing-profile document workflows are verified;
successful file workflows and active-document execution remain unsupported.

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
  POST redirect replay are denied. Password/file, child-frame, multipart, popup,
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

Verification: all 251 regressions passed. Six native-input checks and a
shared-profile control/focus check cover trusted events, viewless viewport setup,
child frames, unavailable option skipping, unchanged choices, bounded batches,
focus drift, deadlines/interruption, adapter loss, and frontend/profile teardown.
Eighteen form/ticket checks plus
shared-profile and popup checks cover native GET/POST serialization, required
fields/validation overrides, submitter destinations/methods, named controls,
one-use method/destination binding, redirect boundaries, stale and last-moment
mutations, callback expiry, stop/revocation, deadlines, and request/receipt failures.
Existing control/wait checks cover editor events/undo, constrained entry,
clearing, activation, selection, unsupported controls, stale references, readable
child frames, scrolling, waits and deadlines, superseding loads, cancellation,
callback-time expiry, revocation, and request/receipt failures. Existing inspection,
navigation, profile, popup, and daily-browser regressions also passed.

Next: enforce active-document networking/submission boundaries before enabling
scripts and SPA behavior. Native pointer gestures/custom controls remain pending. Add
upload/download destination grants alongside file operations. Full form/SPA/file
acceptance remains pending.

Target: expose structured snapshots and revision-bound target handles with deterministic
navigation, form, upload, and download actions. Acceptance: static pages, forms,
SPAs, frames, and DOM replacement work with observed results; unsupported cases
request takeover or return clear limitations.

### A4. Framework-neutral MCP

Provide stdio adapters and authenticated loopback Streamable HTTP, restricted
local IPC, published schemas, and structured results. Acceptance: two independent
MCP clients work through both transports without framework-specific browser code.

### A5. Optional search and classification

Add replaceable Exa search and Jev classification adapters with disclosure
permissions, budgets, provenance, caching, and deterministic fallbacks.
Acceptance: held-out benchmarks justify each enabled classification capability;
operation without providers remains functional.

### A6. Application connections and recovery

Add a grant-enforcing connector gateway, approved credential storage, receipts,
cancellation, and reconciliation of uncertain writes. Acceptance: mock document
and email services pass before connecting real accounts.

### A7. Collaboration interface and styling

Show tasks, authority, activity, artifacts, and evidence. Support watching,
exclusive takeover, resume, and cancellation. Add validated declarative chrome
styling with rollback and recovery. Acceptance: takeover preserves the same page,
stale snapshots are rejected, and malformed styling recovers safely.

## End-to-end validation and rollout — pending

Validate research → document → email with fixtures and mock connectors first,
then user-configured real services. Roll out in order: read-only inspection,
controlled page writes, mock application writes, explicitly authorized real
writes.

Completion requires independent clients to finish the workflow, policy checks on
every controlled mutation, usable receipts/evidence, and regression coverage for
stale targets, hostile inputs, authorization failures, takeover, cancellation,
provider failures, restarts, and uncertain or duplicate writes. The detailed
[validation design](DOCUMENTATION.md#end-to-end-validation-and-rollout) records
the required cases.

## Beyond the initial plan

Built-in agent hosting, scheduling, autonomous planning, and model-account
management need a separate proposal after the collaboration layer is proven.
That proposal must address OS isolation, billing, credentials, resources, and
recovery.
