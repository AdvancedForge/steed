# Steed roadmap

Build a useful daily browser first, then add a controlled collaboration layer for
external agents. Steed provides browser capabilities, context, permissions,
execution, and evidence; external agents provide planning and generation.

See [DOCUMENTATION.md](DOCUMENTATION.md) for setup and implementation details,
including the [planned architecture](DOCUMENTATION.md#planned-architecture).

## Current work — October 2, 2026

Safe follow-up milestone: agents can request a browser picker for an archived task
and selected drafts/evidence. No archive enumeration or client-selected source is
exposed. Imports preserve provenance with new IDs and require fresh grants, even
when saved approvals exist. Ancestral app-call guards block matching completed or
uncertain writes before dispatch, including recreated drafts and deleted originals.
Both independent MCP clients completed selection/approval/no-replay workflows over
stdio and HTTP. Historical provider probes still require a separate implementation;
Gmail stays send-only. See [task recovery](DOCUMENTATION.md#continuing-previous-work).

Permission/setup usability milestone: both website and agent prompts now offer
one-time and remembered choices with clear capability and profile scope.
Agent rules cover matching or narrower requests, preserve exact app accounts and
destinations, and can be forgotten with linked live access revoked. Website
rules are separate; regular approvals never enter private browsing, whose saved
choices are session-only. Google app registration is remembered in the desktop
keyring so subsequent account connections need only **Connect Google** and consent.
First-time Cloud registration still needs user-owned setup. Validation is recorded
in [DOCUMENTATION.md](DOCUMENTATION.md#verification-record).

Browser essentials include Ctrl+F find in page and per-tab zoom. The complete
479-test suite passed in 253.137 seconds (`/tmp/steed-recovery-final-full.log`), including
scoped uploads/downloads, watch/handoff, HTTP session/credential/lifecycle tests, native-input checks,
capability discovery, local agent transport/lifecycle checks, and shared-profile
focus/control-isolation, plus existing form, popup, and browser regressions.
Current startup/render/shutdown passed on Qt 6.11.2
(`/tmp/steed-recovery-startup.log`); the preceding local Google HTTPS check passed.
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
active-page authority still needs native engine support.
A3 includes content inspection, stale-target validation, observed link navigation,
scriptless control edits, native form submission, scrolling, bounded waits, and
scoped native attachment downloads and single-file multipart uploads.
Active document scripts are deferred following the bounded engine assessment;
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

- **Latest engineering checkpoint:** safe user-selected follow-ups, independent
  copied drafts and bounded evidence, schema-6 persistence, fresh approval and
  ancestral write protection. Agent tools are `steed_task_recover` and
  `steed_task_recovery`; **Task Permissions → Recovery** manages pending requests.
- **Next engineering follow-up:** read-only probing of an archived app receipt
  under a fresh exact grant, with durable ownership/cancellation and no mutation
  replay. This is not implemented by material recovery. Deliberate repeat-write
  overrides, additional providers, active website scripts and real-account
  acceptance remain separate work. Keep original inactive tasks and grants closed.

- **Latest usability checkpoint:** reusable approvals for both permission systems;
  **File → Website Permissions** and **Task Permissions → Saved Approvals** manage
  them. Removing an agent rule revokes linked tasks; disconnecting an app removes
  matching rules. Private website choices remain in memory. Google setup has a
  collapsible one-time import, reusable OS-keyring configuration and a forget
  control. No real account or hardware acceptance was performed.
- **Next user-dependent check:** restart Steed, review the new prompts, connect a
  user-owned Google client/account, and explicitly approve a small real Doc/email
  task. Decide about a shared production OAuth registration only if first-time
  user-owned setup remains too much. Additional providers and engine/script work
  remain separate follow-ups; password improvements remain deferred.

**A3 remains in progress: native keyboard activation/selection and native form
submission and scoped uploads/downloads are implemented alongside inspection, links,
edits, scrolling, and waits.**
**A4 is in progress: external stdio and HTTP clients can use those operations.**
**A7 is in progress: watch/handoff, retained drafts and app receipts are implemented.**
**A6 is in progress: mock-tested scoped gateway and uncertain-write recovery.**
Autonomous milestones: scoped downloads `229cfe4`, task activity/receipts `6ad9b1a`,
scoped uploads `027a860`, retained task artifacts `333965c`, scoped app operations
`21790d3`, Google connections `db9e209`, and the permission/setup usability checkpoint
described above.
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
  [assessment](DOCUMENTATION.md#bounded-engine-feasibility-assessment).
- **Local MCP implemented:** File → Enable Local Agent Access opts in for this
  run. A private authenticated Unix socket connects the optional official-SDK
  stdio adapter to the running Qt process. Thirty-five published tool schemas use
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
  See [HTTP setup](DOCUMENTATION.md#http-connection).
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
  See [handoff usage](DOCUMENTATION.md#watching-and-taking-control).
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
  See [download usage](DOCUMENTATION.md#scoped-task-downloads).
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
  See [upload usage](DOCUMENTATION.md#scoped-task-uploads).
- **Artifact milestone implemented:** explicit `artifact` permission saves immutable
  local notes/drafts, with bounded UTF-8/count/total storage, hashes and validated
  task-owned page-inspection source references. Content/title stay out of activity
  events. Task Permissions has an Artifacts tab for plain-text review after
  completion/restart and confirmed user deletion; agents cannot delete drafts.
  Both MCP workflows passed. The full 353-test suite passed, plus twelve final
  artifact checks after adding deletion. See
  [artifact usage](DOCUMENTATION.md#retained-task-artifacts).
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
  [app operations](DOCUMENTATION.md#browser-managed-application-operations).
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
  Desktop OAuth client and complete consent via File → App Connections, then run
  an explicitly approved small real Doc/email task. No real account was connected;
  tools remain absent until configured. Setup and limitations are documented in
  [Google app connections](DOCUMENTATION.md#google-app-connections).
  Publishing a shared Steed OAuth client/verification is a separate future decision.
  Archived-task recovery with fresh approval, app result retention, agent-managed
  mode and additional provider adapters remain follow-ups. Keep Gmail send-only
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
  [verification record](DOCUMENTATION.md#verification-record) for limits and smoke results.
  The expanded baseline and four native-policy comparisons also completed;
  the containment gate correctly exits 1 for TURN/TCP. See
  [native networking prototype](DOCUMENTATION.md#native-networking-prototype).
  The Chromium 153 native-policy diagnostic passed all containment, shared-profile,
  cross-process-frame, detachment, and disposal checks
  (`/tmp/steed-native-connection-policy.log`); five capability checks passed
  (`/tmp/steed-capabilities-focused.log`). See the selected
  [engine direction](DOCUMENTATION.md#active-script-engine-decision).
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
[verification record](DOCUMENTATION.md#verification-record). Passing automated
checks does not establish the unverified manual results below.

## Browser acceptance checklist

The user authorized proceeding with these manual follow-ups recorded. Resolve
any browser blocker found during continued use alongside A1 work.

- [x] Run the complete regression suite (274 checks), startup smoke,
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
See [audit details](DOCUMENTATION.md#active-content-networking-audit).

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
[engine decision](DOCUMENTATION.md#active-script-engine-decision).
Keep scripted forms denied until their body/handler authority is enforceable.
Native pointer gestures/custom controls remain pending. Exact upload-source/site
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
IPC. Thirty-five published input schemas expose current task operations, capability
and selected-profile discovery, structured results, and browser-assigned identity.
User approval is required; shared-profile collaboration, cancellation, isolation,
and evidence were tested with an SDK client and an independent JSON-RPC client
on MCP 2025-11-25 over both transports. HTTP sessions are credential-bound and
own separate browser connections; cancellation/termination/expiry remove owned
task access. See [setup](DOCUMENTATION.md#local-agent-access-mcp).

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
test. See [Google setup](DOCUMENTATION.md#google-app-connections).

User-selected material recovery with fresh approval and matching historical-write
protection is implemented. Remaining: real Google acceptance, additional providers,
folder selection/editing, archived read-only provider probes, broader result retention and
agent-managed mode. Gmail uncertain-send recovery deliberately stays manual with
send-only OAuth access. A shared production OAuth client/publishing model needs
separate user judgment. Acceptance of the gateway/Google adapter is synthetic;
real-account acceptance remains pending.

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
