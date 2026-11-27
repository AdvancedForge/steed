# Development

Checks to run, the verification record and references. [Documentation index](README.md).

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
```

Tests that wait for engine work (renderers, helper processes, workers) give up
after `STEED_TEST_WAIT` seconds (default 30). Waits return as soon as their
condition holds, so passing runs are no slower; the limit only decides how long a
real failure takes to report. Raise it on a very busy machine. The full suite
passes with the default while every core is saturated (load average about 27 on
8 cores).

For headless Linux checks:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python tests/smoke_browser.py
QT_QPA_PLATFORM=offscreen python tests/smoke_google_login.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_active_content.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py --transport http
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py --documents --recipes
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_agent_mcp.py --transport http --documents --recipes
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_codex_provider.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_google_connector.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_google_documents.py
python tests/smoke_password_manager.py
```

Tests use temporary profiles and local content. Chromium still needs to initialize its process sandbox; a restricted container may require running the tests outside that container's sandbox.

Qt unittest classes use `tests/qt_test_support.py` to share one QApplication
throughout the process. Register profile fixture directories with
`keep_directory()` so they are removed only after final engine shutdown. Do not
destroy and recreate QApplication between test classes using WebEngine.

The Google compatibility smoke check also needs `openssl`. It uses a temporary loopback HTTPS server, maps the Google account host to that server for this test process, and trusts only its generated certificate key. It never contacts Google or uses an account.

Recipe TLS regressions also require `openssl` and use temporary certificates and a
local HTTPS server. Their socket fixture maps a validated public numeric address
to that server without external connections. Recipe account checks use an in-memory
synthetic keyring. `--recipes` adds source proposal, the actual default-Deny review
popup, trusted account setup, persistent access and selected-field reads to both
MCP clients. Version 2 also exercises exact payload previews, create/send receipts,
uncertain completion and read-only reconciliation without write replay. `--documents` additionally requires the installed optional bundle.

The active-content audit prints JSON with observed requests, STUN packet counts,
script execution results, and `tested_routes_contained`. Exit zero means its
positive controls and scriptless checks completed; it does not approve scripts.
The current forced-script probes report `tested_routes_contained: false`.

The optional MCP smoke requires `requirements/mcp.txt`. It runs an SDK client
and a separate JSON-RPC client against a temporary, synthetic signed-in profile.
The fixture alone clicks the approval popup and invokes trusted cancellation.
It checks native edits/submission/downloads/uploads, watch/user handoff/return, blocked agent
operations during takeover, stale handles, evidence without field values,
shared cookies/profile identity, concurrent ordinary user POST, ownership denial,
and task disposal. The trusted fixture alone uses the user controls to enter
synthetic text. It saves `/tmp/steed-watch-stdio-preview.png` or
`/tmp/steed-watch-http-preview.png` during takeover. It never accesses an existing
profile/account or proves that
a particular AI client/provider will plan a workflow successfully.
The HTTP mode additionally checks credential-bound transport identity, raw-client
document retrieval/completion, and DELETE cancellation of an owned task/page.
It starts the real HTTP adapter process and verifies shutdown and endpoint-file
removal when browser access is disabled. HTTP unit tests skip when the optional
SDK is absent.

The Google connector smoke uses synthetic desktop-client credentials, an in-memory
secret store and a local HTTP fixture. It exercises the real OAuth callback/PKCE
and API request code while routing its fixed Google endpoints only to that fixture.
It checks draft → Doc → email, lost TCP acknowledgements, empty read-only lookup,
stable receipts, restart without grants/replay and a fresh read-only archived Doc
check using the original operation property. It does
not contact Google or test HTTPS certificates, a real desktop keyring or consent UI.
It saves `/tmp/steed-google-connections.png`, `/tmp/steed-google-approval.png`,
`/tmp/steed-archived-read-approval.png`, `/tmp/steed-archived-receipts.png` and
`/tmp/steed-retention-receipts.png`. It also verifies user-cleared source/check
results while retaining their states, operation identities and no-replay behavior.
Neither optional SDK nor SecretStorage is needed for this synthetic smoke.

The Google documents smoke uses an in-memory credential store and the production
Google HTTP adapter routed to localhost. It checks folder → Doc → tab inspection
→ append, private text isolation between tabs, a lost append response, restart,
fresh read-only archived confirmation without draft copying, and atomic revision
rejection. It also shows the before/after popup, applies one user-reviewed replacement,
loses its TCP acknowledgement and confirms its operation range without replay.
It records one folder, Doc, append and replacement and sends no email.
It saves `/tmp/steed-google-document-receipts.png` and
`/tmp/steed-google-document-review.png`. It does not contact Google or
establish real account, consent, certificate or desktop-keyring acceptance.

## Your own Steed: a copy you or your agent change

Steed's code is open, and changing it is welcome: clone it (or fork it on
GitHub), point Claude Code, Codex or another agent at the folder (it reads
`AGENTS.md`), and run the result beside your everyday Steed:

```bash
git clone https://github.com/AdvancedForge/steed ~/steed-copy
cd ~/steed-copy && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
./steed --data-dir ~/steed-copy-data
```

`--data-dir` (or `STEED_DATA_DIR`) gives that Steed its own folder instead of
`~/.custom_browser`. Without it a second Steed would hand its links to the one
already running, or, with yours closed, run on your real profiles and
passwords. Its windows say which folder in the title (`Steed - Global
[steed-copy-data]`), and the `steed-mcp` launcher in that folder connects
agents to that Steed. To try it with your things, start from a copy of your
data: `cp -a ~/.custom_browser ~/steed-copy-data` (with a keyring, the copied
password vault opens as yours does).

## Verification record

### Startup exits and a live chat — November 16, 2026

The intermittent native exit (139, during the first persistent
`QWebEngineProfile` construction, seen in October in headless smoke runs) did
not occur in 85 starts on Qt 6.11.2: `tests/smoke_browser.py` 30 times in a row
while the full suite ran, 40 more four at a time, and
`tests/smoke_google_login.py` 15 times, all with `python -X faulthandler`. It
is not reproduced; no cause or fix is claimed. If it returns, the faulthandler
trace says where. It did return once on November 19: a child Steed started by
`test_browser`'s global-profile persistence test exited with signal 11 during a
full-suite run that the laptop's sleep paused for hours; that test, rerun five
times, passed each time.

The agents page was driven end to end in a real window, with the real agent
gateway and the synthetic Claude Code runtime: starting a chat, a streamed
reply, Steed closing the chat (recorded as interrupted, **Chat closed**),
**Continue this chat** resuming the same saved session in the chat's folder,
and a reply in the continued chat beneath the earlier messages.

A flaky test turned out to be a real race: when a Steed-run chat's connection
dropped, the agent gateway sometimes cancelled the task before the provider
recorded the interruption. The gateway now records it as interrupted.

### Daily agent reliability — October 13, 2026

The final full suite passes **1,405 tests in 454.935 seconds**; 46 final sidebar,
permission/takeover/watch checks pass in 17.583 seconds. Both independent SDK/raw
MCP clients pass stdio and authenticated HTTP with private screenshot click/type/key,
reviewed live takeover/return, connector recipes/documents and retained account
panels. Both installed Codex runtime paths pass with synthetic local responses;
startup/render/shutdown and light/dark previews pass. Ruff (247 files), dependencies,
whitespace, all 85 client routes and panel/recipe examples pass. User data, real
accounts and external model calls were not used. Desktop/live-provider acceptance
remains manual. See ROADMAP for logs and the separately recorded offscreen Qt
profile-startup crash; one transport retry reproduced it before task setup, then
subsequent transport/native/startup checks passed. No production crash fix is claimed.


Codex sidebar integration (October 3, 2026): the deterministic provider suite
exercises real child processes, Qt IPC and synthetic Steed tasks without models
or accounts. Coverage includes automatic turns, ordered/deduplicated follow-ups,
early notifications, UTF-8 chunks, clearing during a stream, cancellation,
incognito, task/connection isolation and failed sign-in/configuration/protocols.
The separate `tests/smoke_codex_provider.py` requires an installed Codex CLI and
uses a temporary authentication/configuration directory, a loopback model
endpoint and a harmless inherited MCP marker. It verifies a native newer-model
code-runtime call into the bound Steed task, streaming follow-ups, native
capability preservation and adapter shutdown without external model calls.
The native smoke also checks the reported model, concurrent capabilities/context/
enabled-folder/connector discovery, live-tab denial without a user handoff, and a
synthetic workspace grant explicitly approved by the fixture's user, under a
native never-ask policy. It uses no real files/accounts and opens no page or
external application. Pass `gpt-5.4` to check the direct-tool path. Real
authenticated model replies and desktop browsing acceptance remain separate user
checks. Validation totals are
tracked in ROADMAP.md.

Document worker bundle (October 2, 2026): **679 tests passed in 314.002 seconds**
(`/tmp/steed-documents-full-final.log`). Sixty-one final worker checks passed in
12.902 seconds, ten store/migration checks in 1.070 seconds and eleven upload
checks in 20.552 seconds (`/tmp/steed-documents-focused-final.log`,
`/tmp/steed-documents-store-final.log`, `/tmp/steed-documents-uploads.log`). Coverage
includes real PDF/XLSX/PNG generation, native Pillow imports, missing/unsupported
bundles, exact runtime/environment approvals, changed helper snapshots, wheel
tampering, traversal/symlink/device archives, expansion/collision limits, inert
`.pth` files, atomic offline install, restart and schema-12/partial migration.

The maintained bundle was downloaded and verified, with no host dependency
installation (`/tmp/steed-documents-install-verified.log`). Actual XLSX creation
initially exposed CPython's `FIOCLEX` requirement; a traced synthetic failure led
to that single argument-filtered syscall rule, with other ioctl/network/process
operations still denied. Initial external MCP acceptance caught a missing runtime
argument length bound; it was corrected and regression-covered. Final SDK and
independent raw clients generated/read real documents and completed existing
browser/app/recovery workflows over both transports
(`/tmp/steed-documents-mcp-stdio-verified.log`,
`/tmp/steed-documents-mcp-http-verified.log`). Approval previews were inspected
(`/tmp/steed-document-worker-approval.png`, `/tmp/steed-worker-stdio-preview.png`).
Browser startup/render/shutdown passed (`/tmp/steed-documents-startup.log`).

The earlier 677-test full run hit `native_input_unavailable` in the existing stale
upload-reference check (`/tmp/steed-documents-full.log`); the focused upload group
and final full suite passed. Keep this intermittent native-input observation with
desktop acceptance rather than claiming a browser-engine fix. Ruff/format,
dependency health, exact lock consistency, whitespace and local doc links passed.
Native acceptance here used Linux x86-64/CPython 3.12, temporary profiles/files and
mock account effects. Other platforms and real-account/desktop acceptance remain
pending; Google registration was not changed.

Worker setup diagnostics (October 2, 2026): **657 tests passed in 285.324 seconds**
(`/tmp/steed-worker-setup-full.log`). Forty focused worker checks passed in 7.464
seconds (`/tmp/steed-worker-setup-focused.log`), including eleven new checks for
actual sandbox readiness, no granted authority/data use, caching across callers,
background execution, sanitized failure, trusted retry, denied probe injection,
shutdown/join, restart reset, actual-run revalidation and UI guidance.
The SDK and independent clients discovered support before grants, generated an
attachment and recovered its selected bytes over stdio/HTTP
(`/tmp/steed-worker-setup-mcp-stdio-final.log`,
`/tmp/steed-worker-setup-mcp-http.log`). The first stdio attempt exited natively
with the tracked Qt startup 139 before the workflow; identical retry passed.
Setup preview was inspected (`/tmp/steed-worker-setup.png`) and browser startup/
render/shutdown passed (`/tmp/steed-worker-setup-startup.log`). Ruff/format,
whitespace, dependency and local doc link checks passed. Synthetic accounts/state
only; shared Google registration remains deferred. No extra worker dependencies
or direct networking were introduced.

Selected-file recovery (October 2, 2026): **646 tests passed in 274.034 seconds**
(`/tmp/steed-file-recovery-full-final.log`). Thirty-five focused recovery checks
passed in 4.097 seconds; seventeen final file-recovery checks passed in 2.045
seconds (`/tmp/steed-file-recovery-focused.log`,
`/tmp/steed-file-recovery-focused-final.log`). Coverage includes selected binary
fidelity, provenance/fresh IDs, independent deletion/restart lifetime, workspace
permission, fresh approval despite saved rules, inert copied source, corrupt/
foreign/building/deleted input denial, limits and complete transaction rollback.
Both SDK and independent clients recovered generated ZIP attachments over both
MCP transports (`/tmp/steed-file-recovery-mcp-stdio-final.log`,
`/tmp/steed-file-recovery-mcp-http-final.log`). The initial MCP run caught a missing
recovery-context lookup in the Workspace preview; it was corrected and covered
for ordinary tasks before the final suite. The superseded full run was stopped.
The Files picker screenshot was inspected (`/tmp/steed-workspace-recovery.png`),
and real-entry-point startup/render/shutdown passed
(`/tmp/steed-file-recovery-startup.log`). Ruff, format, whitespace and local doc
links passed. All files/accounts were synthetic; no Google registration changes.

Local-worker milestone (October 2, 2026): **629 tests passed in 256.190 seconds**
(`/tmp/steed-worker-full.log`). Twenty-nine focused worker tests passed in 5.877
seconds (`/tmp/steed-worker-focused.log`), including real Bubblewrap/seccomp
host-file/environment/socket/process denial, hard resource limits, memory/CPU/wall
exhaustion, protocol bounds and private scratch cleanup. Manager coverage includes
default-Deny source review, separate exact approval, remembered-hash/task isolation,
no replay, declared app/destination checks, typed broker boundaries, operation budgets,
enqueue/cancel races, lifecycle/source deletion, schema-11 migration and journal
failure before execution/after completion. Setup failure never falls back to host code.

The SDK and independent JSON-RPC client each built and ran a ZIP generator after
the actual trusted package popup, then read and verified its CSV attachment over
both stdio and HTTP (`/tmp/steed-worker-mcp-stdio.log`,
`/tmp/steed-worker-mcp-http.log`). Existing shared-profile browsing, watch/handoff,
Doc/email writes, archived read-only checks and retention workflows still passed.
Popup/results previews were visually inspected (`/tmp/steed-worker-approval.png`,
`/tmp/steed-worker-results.png`). Browser startup/render/shutdown, workspace picker/
export controls and synthetic Google HTTP reviewed replacement/recovery passed
(`/tmp/steed-worker-startup.log`, `/tmp/steed-worker-workspace-ui.log`,
`/tmp/steed-worker-google-review.log`). Ruff/format, dependency, whitespace and
local documentation-link checks passed. These used temporary state and synthetic
credentials/effects, not real account/keyring or desktop acceptance. At that
checkpoint, workers shared the host kernel and supported standard-library Python
only. The subsequent document bundle adds maintained dependencies; arbitrary new
providers remain separate work.

Standing connector access stage (October 2, 2026): **600 tests passed in 248.392
seconds** (`/tmp/steed-access-full.log`), including nine connector-switch
regressions (`/tmp/steed-access-focused.log`). Twenty-one existing account setup/
disconnect checks passed (`/tmp/steed-access-connections.log`). Coverage includes
new destinations under exact live grants, separate read/send/direct permissions,
revocation and saved-rule clearing, resource limits, no inherited file access,
immutable account identity, restart, new-tool partial state, trusted UI clicks,
and settings/approval journal failures. Accounts, credentials and effects were
synthetic; real Google consent and desktop keyring acceptance remain pending.

Generated-file/custom integration groundwork (October 2, 2026): **590 tests passed
in 255.599 seconds** (`/tmp/steed-workspace-full.log`), plus twenty-one final focused
workspace checks (`/tmp/steed-workspace-focused-final.log`). Coverage includes
arbitrary binary fidelity, chunk retries, bounded declarations/reads, integrity,
task isolation, live/revoked permission, exact export paths and size, symlink rejection,
user-selected snapshots, retained user export/deletion, schema-9 migration,
rollback and inert package declarations. Both independent MCP clients passed
binary upload/read/export and package staging over stdio and HTTP, alongside the
existing browser/connector/recovery workflows (`/tmp/steed-workspace-stdio.log`,
`/tmp/steed-workspace-http.log`). The first HTTP attempt caught a grant-schema
length limit; the schema was corrected. The first stdio attempt hit the tracked
native Qt startup exit 139; the identical retry passed.

The Workspace user-picker fixture passed (`/tmp/steed-workspace-ui.log`), and its
final plain-language layout was inspected (`/tmp/steed-task-workspace.png`).
Run it with the headless prefix and `.venv/bin/python tests/smoke_task_workspace.py`.
The Google HTTP review/recovery fixture and startup/render/shutdown passed
(`/tmp/steed-workspace-google.log`, `/tmp/steed-workspace-startup.log`). Ruff/format
and whitespace checks, dependencies and 74 local documentation links passed.
Files, account data and credentials were synthetic;
real-account/desktop acceptance remains pending. Bubblewrap 0.9.0 launched a
harmless isolated process with no home/network; no generated source was executed.
That groundwork did not execute source. The subsequent local-worker milestone
adds isolated execution with separate exact-package approval; see the current
record above.

Reviewed document-edit stage (October 2, 2026): **568 tests passed in 254.019
seconds** (`/tmp/steed-reviews-full.log`), plus fifteen final focused proposal regressions
(`/tmp/steed-reviews-focused.log`). Existing Google adapter/gateway checks and the
production-transport local HTTP workflow passed (`/tmp/steed-reviews-google.log`,
`/tmp/steed-reviews-gateway.log`, `/tmp/steed-reviews-http.log`). The review screenshot
was inspected: account/destination, current/proposed text, diff and default Deny
are visible. Tests cover user-only review, fresh/saved reviewed grants, separate
reusable direct grants, exact draft/revision binding, scrubbed previews, cancellation,
restart, storage failures, foreign tasks and uncertain replacement recovery.
All provider data and credentials were synthetic; real Google acceptance is pending.
Both independent MCP clients over stdio and HTTP, startup/render/shutdown,
Ruff/format and diff checks passed (`/tmp/steed-reviews-mcp-stdio.log`,
`/tmp/steed-reviews-mcp-http.log`, `/tmp/steed-reviews-startup.log`).

Google folder/document stages (October 2, 2026): folder milestone `e74dd12`
passed **531 tests in 246.031 seconds** (`/tmp/steed-folders-full.log`); the
following document milestone passed **555 tests in 252.809 seconds**
(`/tmp/steed-docs-full.log`). Fifty-two focused Google checks and 21 gateway
checks passed (`/tmp/steed-docs-focused.log`, `/tmp/steed-docs-gateway.log`).
Coverage includes exact parent/tab grants, account and ownership checks,
immutable drafts, UTF-16 offsets, nested tabs, bounded output, revision conflicts,
lost/malformed responses, unique-range checks, read-only archive confirmation,
and cancellation before mutation. The production-transport localhost workflow
passed with one folder/Doc/append and no repeated mutation
(`/tmp/steed-docs-http.log`). Its receipt screenshot was inspected.
The previous OAuth/Doc/email smoke, SDK and independent MCP clients over both
transports, startup/render/shutdown, Ruff/format, dependency checks and 33 local
documentation links also passed (`/tmp/steed-docs-google.log`,
`/tmp/steed-docs-mcp-stdio.log`, `/tmp/steed-docs-mcp-http.log`,
`/tmp/steed-docs-startup.log`). All account acceptance here is synthetic;
real Google consent/accounts, desktop keyring and hardware checks remain pending.

Provider result-retention stage (October 2, 2026): **518 tests passed in
255.304 seconds** (`/tmp/steed-retention-full.log`). Seventeen focused checks passed
in 1.486 seconds (`/tmp/steed-retention-focused.log`), covering owned/bulk clearing,
foreign IDs, idempotency, identity/state/draft preservation, historical-write guards,
shared-summary removal, queued/late writes, cleared source/probe confirmations,
restart, schema-7 migration, journal rollback, corrupt snapshots, agent API denial
and default-No UI confirmation. Clear markers suppress future text/link storage
while outcomes and independently confirmed evidence remain intact.

SDK and independent JSON-RPC clients observed user-cleared results and summaries
and still rejected matching historical writes over both transports
(`/tmp/steed-retention-mcp-stdio.log`, `/tmp/steed-retention-mcp-http.log`). Provider
writes stayed at exactly two documents and two emails across clients. Shared-profile
browsing, watch/handoff, uploads/downloads, cancellation and cleanup also passed.
The synthetic Google HTTP/OAuth fixture cleared source/check results and retained
states and operation identity, with exactly one Doc/email and no extra mutations
(`/tmp/steed-retention-google.log`). The clearing controls and timestamp preview were
visually inspected using `/tmp/steed-retention-receipts.png`. Startup/render/shutdown
passed on Qt 6.11.2 (`/tmp/steed-retention-startup.log`). Ruff, formatting, whitespace
and dependency checks passed. No real accounts, desktop keyrings or hardware were used.

Archived app receipt stage (October 2, 2026): the full **501-test suite passed
in 245.296 seconds** (`/tmp/steed-archived-final-full.log`). After the final guard
against an absence report while the original worker is running, all twenty-two
focused regressions passed in 2.250 seconds
(`/tmp/steed-archived-focused-final.log`). Coverage includes
original provider/request identity, fresh read-only authority without draft access,
exact destinations, unshared/foreign receipts, unsupported lookup methods, stable
request IDs, cancellation before/during dispatch, account disconnect, timeouts,
operation limits, late-worker confirmation, atomic rollback and schema-6 migration.
Restart preserves probe mappings without network activity, old grants or write replay.

SDK and independent JSON-RPC clients each checked archived uncertain receipts
under fresh read permissions over both MCP transports
(`/tmp/steed-archived-mcp-stdio.log`, `/tmp/steed-archived-mcp-http.log`). Mock writes
remained exactly two documents and two emails across the clients. Historical-write
denial, selected draft reads, shared-profile user browsing/POST, watch/handoff,
uploads/downloads, cancellation and cleanup checks also passed.

The synthetic Google HTTP/OAuth fixture passed lost acknowledgements, an empty
lookup that stays uncertain, restart with no provider I/O, fresh read-only lookup
using the original Drive property, and manual-only Gmail handling
(`/tmp/steed-archived-google.log`). It recorded exactly one document and one email.
The read approval and Previous/READ receipt UI were visually inspected.
Startup/render/shutdown passed on Qt 6.11.2 (`/tmp/steed-archived-startup.log`).
Ruff, formatting, whitespace, dependency and 32 local documentation-link checks
passed. No real accounts, desktop keyrings or hardware were exercised.

Safe task follow-up stage (October 2, 2026): **479 tests passed in 253.137 seconds**
(`/tmp/steed-recovery-final-full.log`), including nineteen recovery regressions,
the automatic recovery/access popup sequence and external connection isolation.
Coverage includes selected-draft disclosure, fresh access despite saved approvals,
source provenance, atomic rollback, checksum failures, schema-5 migration,
selection/context/ancestry limits, restart, deleted originals, recreated bodies,
current ancestral receipt outcomes, valid new work after a denied duplicate and
storage failures that freeze authority. No old pages or grants are resumed.

SDK and independent JSON-RPC clients each completed source selection, fresh
approval, retained-draft reads and historical-write denial over both transports
(`/tmp/steed-recovery-mcp-stdio-final.log`, `/tmp/steed-recovery-mcp-http-final.log`).
The mock provider still recorded exactly two documents and two emails across the
two clients; follow-ups added no writes. Shared-profile/user POST, watch/handoff,
uploads/downloads, cancellation and cleanup checks passed in those workflows.
The recovery picker was visually inspected using synthetic drafts and receipts.
Startup/render/shutdown passed on Qt 6.11.2 (`/tmp/steed-recovery-startup.log`);
Ruff, formatting, whitespace, dependency and 31 local documentation-link checks
passed. Real accounts/keyrings and hardware were not used. Historical provider
probes through fresh tasks remain outside this checkpoint.

Permission/setup usability stage (October 2, 2026): the complete **458-test suite
passed in 247.049 seconds** (`/tmp/steed-permissions-acceptance.log`). New regressions
cover exact site/action/account/destination matching, profile identity and
rename/recreation, non-union of rules, atomic approval/provenance, revocation,
restart and disconnect, corrupt-state failure, website scope/private isolation,
save failures, page-bound profile selection, and reusable Google client setup.
Thirteen app connection checks and seventeen saved-approval checks passed after
the final UI changes (`/tmp/steed-permissions-ui-final.log`,
`/tmp/steed-approval-final.log`); setup and permission dialogs were visually
inspected using synthetic accounts.

The real Chromium notification fixture passed one-time approval followed by a
fresh prompt after navigation, saved approval reuse, forgetting, and exclusion
from private browsing (`/tmp/steed-site-native.log`). Run it separately:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" \
  .venv/bin/python tests/smoke_site_permissions.py
```

It uses temporary profiles and a loopback HTTP page, with real Chromium permission
requests and synthetic choices. It does not test OS notification delivery or
camera, microphone, location or screen hardware. Synthetic OAuth/API workflow
acceptance passed (`/tmp/steed-permissions-google-http.log`), as did independent
MCP clients over stdio and HTTP (`/tmp/steed-permissions-mcp-stdio.log`,
`/tmp/steed-permissions-mcp-http.log`) and startup/render/shutdown on Qt 6.11.2
(`/tmp/steed-permissions-startup.log`). Chromium's startup smoke required execution
outside the tool filesystem sandbox; this is separate from the tracked native
startup crash. Lint, formatting, whitespace, dependency and local-link checks
passed. No real Google account/keyring or hardware acceptance was performed.

Earlier verification records follow.

Google connection stage (October 2, 2026): the full 413-test suite passed in
245.201 seconds (`/tmp/steed-google-full.log`). Thirty-nine new checks cover account
identity/storage, setup/PKCE/state, tokens/scopes/rotation, destination validation,
plain-text request mapping, uncertain writes, cancellation/disconnect, trusted UI
and provider extensibility. Final account/provider/gateway/UI checks passed 58
tests in 2.626 seconds (`/tmp/steed-google-focused-identity-final.log`). The local HTTP
workflow passed (`/tmp/steed-google-http-identity-final.log`), applying exactly one
synthetic document/email despite lost acknowledgements, with read-only Doc recovery
and no restart replay. Independent SDK/JSON-RPC MCP workflows passed over stdio
and HTTP (`/tmp/steed-google-mcp-stdio.log`, `/tmp/steed-google-mcp-http.log`), and
ordinary startup/render/shutdown passed (`/tmp/steed-google-startup.log`). Account/scope
UI previews were checked. Ruff, formatting, dependencies and whitespace passed.
Final metadata-size and account-label checks passed the focused suite too.
Real Google OAuth, account writes and desktop keyring acceptance remain pending;
no real account was connected.

Connector gateway stage (October 2, 2026): the full 374-test suite passed in 225.006
seconds (`/tmp/steed-connectors-full.log`). Nineteen focused checks passed in 1.142
seconds (`/tmp/steed-connectors-focused-final.log`), covering trusted effect mapping,
exact destinations, artifact/receipt ownership, independent provider IDs, duplicate
and changed requests, queued cancellation, live timeout/capacity, expiry/budgets,
revocation, late actual outcomes, journal failures, restart recovery, draft deletion
before dispatch and retained plain-text user receipt/reconciliation controls.
Both real MCP transports completed research/draft → mock document → mock email,
with exactly one document and one email in the synthetic provider database
(`/tmp/steed-connectors-stdio.log`, `/tmp/steed-connectors-http.log`). An intentionally
lost email acknowledgement was reconciled read-only without resending; an outside
recipient was denied. Remote tool annotations falsely claimed read-only operations
while trusted browser mappings still required write/send authority. Startup passed
(`/tmp/steed-connectors-startup.log`), and receipt/approval previews were inspected.
The final receipt text layout was checked separately by the connector/UI group
(`/tmp/steed-connectors-ui-final.log`). No real accounts or emails were used.
The final dispatch-status adjustment (app-only tasks become running) also passed
all nineteen connector checks in 1.148 seconds
(`/tmp/steed-connectors-status-final.log`). Lint, formatting, whitespace, dependency
consistency and 26 local documentation file links passed.
The expanded final smoke checks passed with an SDK client and an independently
implemented JSON-RPC client each completing the full workflow over both transports
(`/tmp/steed-connectors-independent-stdio.log`,
`/tmp/steed-connectors-independent-http.log`). Each fixture's provider database had
exactly two documents and two emails, one of each per client, including independent
reconciliation of lost send acknowledgements and task-specific provider IDs.

Task artifact stage (October 2, 2026): the full 353-test suite passed in 245.973
seconds (`/tmp/steed-artifacts-full.log`). Twelve final focused artifact checks
passed in 0.725 seconds (`/tmp/steed-artifacts-focused-final.log`), including
content/metadata transactions, UTF-8/count/total quotas, source-event ownership,
grants and budgets, revocation, restart retention, schema migration, plain-text
completed-task review, confirmed user deletion and denial of agent deletion.
Failed evidence writes prevent disclosure or roll back deletion. Both MCP
workflows stored/listed/read sourced synthetic drafts
(`/tmp/steed-artifacts-stdio.log`, `/tmp/steed-artifacts-http-retry.log`). The first
HTTP run hit the previously tracked native Qt startup exit 139; identical retry
passed. The native issue remains unresolved. Startup/render/shutdown passed
(`/tmp/steed-artifacts-startup.log`); the synthetic artifact panel was visually
checked. No real account or app provider was used.

Scoped upload stage (October 2, 2026): eleven focused checks passed in 15.751 seconds
(`/tmp/steed-upload-focused-release.log`), covering immutable copied bytes, exact
source/site authority, symlinks, size limits, enabled-script denial, mid-copy
revocation, remaining staging budget, stale references, unsupported multiple files,
user file selection,
redirect replay and actual shared-profile cookies. Both MCP workflows passed
with separate selection/submission and a changed original source after copying
(`/tmp/steed-upload-stdio-final.log`, `/tmp/steed-upload-http-final.log`); the submitted body
contained the original copy. No file bytes appeared in task events.
The full 343-test suite passed in 233.164 seconds
(`/tmp/steed-upload-complete.log`); startup/render/shutdown passed
(`/tmp/steed-upload-startup.log`). The first full run found one permission-dialog
wording regression; the dialog now distinguishes grants with and without upload
authority. The final wrapped-label adjustment was checked separately by six
popup checks, which passed in 2.034 seconds
(`/tmp/steed-upload-approval-final.log`), including exact file/site disclosure and
wrapped-label sizing. Long scopes scroll while Approve/Deny stay visible;
the synthetic upload popup was visually checked at the top and bottom.
Ruff, formatting, whitespace, dependency consistency and local file links passed.
These are synthetic local fixtures, not acceptance of real authenticated upload
sites or scripted widgets.

Task activity and retained download receipts stage (October 2, 2026): three
real-engine UI checks passed in 1.855 seconds (`/tmp/steed-activity-focused.log`),
covering retained results, per-task selection, hidden form values, user stream
cancellation, and close-time polling cleanup. Nine storage tests passed in
0.635 seconds (`/tmp/steed-activity-store.log`), including bounded, filtered,
chronological event tails without changing external client cursors.
Both MCP workflows passed with visible receipt previews
(`/tmp/steed-activity-stdio.log`, `/tmp/steed-activity-http.log`), including the
existing handoff/download/concurrent-user checks. The synthetic Downloads panel
was visually inspected from `/tmp/steed-activity-http-preview.png`.
The full 331-test suite passed in 207.015 seconds
(`/tmp/steed-activity-full.log`); startup/render/shutdown passed
(`/tmp/steed-activity-startup.log`). Ruff, formatting and whitespace passed.

Scoped download stage (October 2, 2026): the full 324-test suite passed in
219.159 seconds (`/tmp/steed-download-full.log`). Sixteen initial download
regressions passed in 73.306 seconds (`/tmp/steed-download-focused.log`), covering
exact paths/size scopes, existing files, symlinks, parent substitution, native
private/shared profile downloads, credential sharing, ordinary download
independence, forbidden redirects, HTML responses, known/unknown-size limits,
stream cancellation, late destination creation, revocation, and publication
journal failure. The final 19 focused checks passed in 72.304 seconds
(`/tmp/steed-download-focused-final.log`), adding active-transfer expiry,
cross-task ownership and post-publication receipt failure. Ruff, formatting,
whitespace, and optional dependency checks passed.

Both MCP workflows downloaded and inspected a synthetic output file through
the real adapter (`/tmp/steed-download-stdio.log`,
`/tmp/steed-download-http.log`); shared-profile form/handoff, concurrent user
POST, cancellation and independent-client ownership checks remained passing.
Startup/render/shutdown passed (`/tmp/steed-download-startup.log`). These are
isolated fixtures, not real-account download acceptance. Completed files survive
normal task teardown; crashes can leave private staged directories until explicit
cleanup. No upload, pause/replay or executable-content authority was added.

Same-page watch and user handoff stage (October 2, 2026): the final 16 watch
regressions passed in 3.448 seconds (`/tmp/steed-watch-focused-final.log`).
They cover explicit attach authority, page/profile identity, read-only rendered
input, user edits/navigation, queued and in-flight agent denial, stale targets
and ownership keys, unchanged scopes, journal failure, revocation, busy close,
Escape, direct widget deletion, and reopening before deferred deletion.
A separate shared-window regression verifies parent-window close returns control
without destroying the task's shared profile. Earlier combined gateway/service
checks passed 47 tests in 8.248 seconds (`/tmp/steed-watch-focused.log`).

Both MCP `2025-11-25` workflows passed
(`/tmp/steed-watch-stdio-smoke.log`, `/tmp/steed-watch-http-smoke.log`).
The trusted fixture took control, entered synthetic text through the user panel,
verified the agent was denied, and returned the same page. The agent rejected
its old references, inspected again, edited/submitted the native form, and
finished while ordinary shared-profile user POST remained available. Entered
values were absent from the journal. HTTP ownership and independent-client
DELETE cancellation checks also passed. These isolated fixtures establish
supported handoff behavior, not real-account or unrestricted native input
acceptance. Website scripts remain unavailable.

The full 308-test suite passed in 146.577 seconds
(`/tmp/steed-watch-full.log`). Startup/render/shutdown passed
(`/tmp/steed-watch-startup.log`), as did Ruff, formatting, whitespace, and
`pip check`; 47 local documentation links passed. The synthetic handoff window
was visually inspected from `/tmp/steed-watch-http-preview.png`.
No native startup exit occurred in this stage; the intermittent
Qt startup exit recorded below remains unresolved.

Authenticated HTTP MCP stage (October 2, 2026): 13 focused HTTP/adapter tests
passed in 1.434 seconds (`/tmp/steed-http-focused.log`). Coverage includes all
method authentication, Host/Origin rejection, credential-bound session ownership,
DELETE/shutdown cleanup, idle expiry, GET-stream lifetime, explicit cancellation,
request/session limits, private endpoint publication, browser-access epoch
changes, and rejected overlapping operations.

Both real-adapter synthetic Qt workflows passed on MCP `2025-11-25`
(`/tmp/steed-http-stdio-smoke.log`, `/tmp/steed-http-mcp-smoke.log`). The HTTP
SDK client completed the shared-profile native form while ordinary user POST
continued. An independent urllib client rejected foreign-credential access,
completed approved document retrieval, reused its task connection with fresh
approval, and deleted a session owning an active page. Its task was cancelled
without disrupting the SDK client's workflow. Disabling browser access stopped
the adapter and removed its private endpoint file. Only the trusted fixture
approved requests; production HTTP has no approval bypass.

The full 290-test suite passed in 142.947 seconds
(`/tmp/steed-http-full.log`). Real browser startup/render/shutdown passed
(`/tmp/steed-http-startup.log`), as did Ruff, formatting, whitespace, 46 local
documentation links and `pip check`. One HTTP smoke run exited natively (139)
during Qt startup before the HTTP checks; the identical retry passed. This
intermittent startup issue remains unresolved. These are isolated fixture
results, not real-account HTTP, arbitrary client/OAuth, other protocol revision,
reconnect/resume or watch/takeover acceptance. The broader A4 milestone stays
in progress; core transport checks passed.

Active-profile discovery and live desktop MCP check (October 1, 2026): an
official-SDK client connected to the opted-in running browser. The user approved
read/navigation access to `https://en.wikipedia.org` on `existing:codex test`.
The client inspected Python's article, followed its observed Guido van Rossum
link, inspected that article, and finished the task successfully. This verifies
a real client and public-site workflow on the user-selected profile; it does
not test authenticated sites, website scripts or HTTP transport. The running
browser predated profile discovery, so the user supplied its profile name;
the new context tool is verified separately with temporary profiles.

Nineteen focused gateway/UI tests passed in 4.589 seconds
(`/tmp/steed-context-focused.log`), including six new regressions for exact
profile names, grant stability during switches, focus/dialog selection,
multiple/closed windows, incognito/global exclusion, and bound-task lifetime.
The two-client MCP smoke passed with discovery supplying the shared-profile
grant/session selector (`/tmp/steed-context-mcp-smoke.log`). The full 280-test
suite passed in 135.526 seconds (`/tmp/steed-context-full.log`). Real-entry-point
startup/render/shutdown passed (`/tmp/steed-context-startup.log`), as did Ruff,
formatting, whitespace and 45 local documentation links. These checks used
isolated configuration and synthetic credentials.

A4 stdio stage and bounded engine assessment (October 1, 2026): no compatible
packaged embedded engine or maintainable small backport was established from the
installed engine probes, Qt release information, public request API source, and
Chromium policy design. Custom compilation/full patch auditing were not attempted.
Scripted tasks are deferred; the existing Qt browser and profile model remain.

Two external clients passed the synthetic delegated workflow on MCP 2025-11-25
(`/tmp/steed-agent-mcp-smoke.log`): the official-SDK client exercised capability
discovery, denial before approval, the nonmodal approval popup, observed link
navigation, native text/checkbox edits, stale-reference denial, form submission,
value-free evidence, cancellation and denied access afterward. A separate raw
JSON-RPC client initialized, discovered tools, created its own task, rejected
foreign-session/approval attempts, and finished. The task shared the user's
actual profile and fixture cookie; ordinary user POST remained available and
task pages were disposed. The fixture alone approved/cancelled; production code
provides no remote approval tool. This proves these local client/fixture paths,
not real-account, desktop, AI-provider, HTTP-transport or complete A4 acceptance.

Eleven focused gateway/UI regressions passed in 2.561 seconds
(`/tmp/steed-agent-focused.log`); last-owner shutdown coverage was then added.
Three adapter failure checks passed for private/symlinked discovery, malformed
metadata, and closing uncertain/cancelled requests without retry. The final
274-test suite passed on Qt 6.11.2 in 162.856 seconds
(`/tmp/steed-mcp-final.log`), including 16 new transport/UI/adapter checks and
sequential tasks requiring fresh identity/approval. The final two-client smoke
also passed reuse without carrying grants into a new task. Real-entry-point
startup/render/shutdown passed (`/tmp/steed-mcp-startup.log`). Ruff, formatting,
whitespace, 45 local documentation links, and optional MCP dependencies via
`pip check` passed. Historical native Qt startup exits remain
unresolved; this stage does not claim to fix them.

A3 engine decision and capability discovery (October 1, 2026): the native
connection-policy gate passed on separately installed Chromium 153.0.8010.36
(`/tmp/steed-native-connection-policy.log`). Unapproved HTTP/WebSocket, worker,
STUN UDP, and TURN/TCP probes were contained in the restricted document and its
cross-process iframe. Positive controls retained ordinary networking and
approved-origin GET/POST; shared cookies/storage/live updates, control-session
detachment, owned-frame disposal, and the surviving user tab passed. This is a
synthetic external-engine experiment, not embedded Qt acceptance or real-account
testing. Native policy inside the actual shared embedded engine is the selected
direction; obtaining compatible engine support and trusted policy delivery remain
next. Steed keeps website scripts unavailable.

Five capability regressions passed (`/tmp/steed-capabilities-focused.log`),
covering version eligibility without script authority, unverified versions,
discovery without grants/page creation, task identity/lifetime, and missing
alternate-backend reports. The complete 258-test suite passed on Qt 6.11.2 in
135.128 seconds (`/tmp/steed-engine-full.log`). Ruff, formatting, whitespace,
and local documentation links passed. This run does not resolve the historical
native Qt startup exits or establish interactive desktop acceptance.

A3 native networking prototype stage (October 1, 2026): the expanded baseline
audit passed (`/tmp/steed-native-policy-baseline.log`). Response continuation and
fulfillment comparisons, plus the connection-allowlist header probe, completed
on Qt 6.11.2 / Chromium 140.0.7339.225 (`/tmp/steed-native-policy-continue.log`,
`/tmp/steed-native-policy-fulfill.log`, `/tmp/steed-native-policy-allowlist.log`,
`/tmp/steed-native-policy-zero-latency.log`).
Fulfillment blocked shared workers, including a fresh iframe constructor, and
1 ms/100% packet loss stopped the observed STUN packets. Holding fulfillment
and packet loss fixed while removing latency restored the observed STUN traffic.
Every candidate still
allowed an unapproved TURN/TCP connection; the required-containment gate
therefore exited 1 as intended (`/tmp/steed-native-policy-gate.log`). Scriptless
controls and ordinary concurrent user networking passed. These are test-only
prototypes; runtime behavior and permissions were not expanded. The prior
253-regression full-suite result remains the production baseline; no production
code changed in this stage. Lint, formatting and whitespace checks passed.

A3 active-content boundary stage (October 1, 2026): the loopback audit completed
on Qt 6.11.2 (`/tmp/steed-active-audit.log`), identifying the shared-worker and
WebRTC gaps described above. CSP prevented the shared-worker request in its
controlled fixture but did not stop STUN. Website scripts remain unavailable;
no broader grant or profile-wide user restriction was added. All 20 form/ticket
checks passed (`/tmp/steed-active-forms.log`), including two new regressions for
script settings enabled before preparation or during its asynchronous callback.
Neither path armed a ticket or sent a form request. Ordinary browsing and earlier
native actions retain their existing behavior. The complete 253-test suite passed
in 143.111 seconds (`/tmp/steed-active-full.log`); Ruff, formatting and whitespace
checks passed. This stage does not establish interactive desktop or real-account
acceptance. Historical native Qt startup exits remain unresolved.

A3 native keyboard stage (October 1, 2026): all 251 regressions passed on Qt 6.11.2
in 141.614 seconds (`/tmp/steed-native-full.log`). Six new input checks and a new
shared-profile check verify trusted keyboard/click/input/change events, viewless
task rendering with a virtual viewport, readable child-frame focus, disabled
option/optgroup skipping, unchanged selections, focus checks between bounded
batches, timeout/stop with late responses, and adapter loss. Ordinary user values,
checkbox state, and focus stayed independent on the shared profile; revocation
destroyed the private frontend before its profile and left the user page alive.
Existing form, worker-bypass, permission, journal, and daily-browser tests passed.
Website JavaScript permission and physical pointer/custom-control behavior remain
unimplemented. No external-agent connection or cloud provider was introduced.
Startup/render/shutdown and local Google HTTPS redirects/POSTs/opt-out passed
(`/tmp/steed-native-startup.log`, `/tmp/steed-native-google.log`). Ruff, formatting,
whitespace, and thirteen local documentation links passed. The prior native Qt
startup/profile-creation exits below remain unresolved; these passing runs do not
establish a root cause or fix.

A3 form-submission stage (October 1, 2026): all 244 regressions passed on Qt 6.11.2
in 115.758 seconds (`/tmp/steed-form-full.log`). Eighteen new form/ticket checks
and new shared-profile/popup checks cover native GET/POST serialization, hidden
fields, submitter overrides, required-field validation, named-control shadowing,
exact one-use authority, redirect checks and POST replay denial, stale/last-moment
DOM changes, late expiry, stop/revocation, missing-load deadlines, and request/
receipt journal failures. Signed-in task POST and concurrent user POST retained
their cookies and ordinary user behavior. Form/result secrets stayed out of the
journal; native validation failures sent no request. The prior direct-POST
denial behavior also passed after separating general requests from form tickets.
These local fixtures do not establish real-account changes, script-driven forms,
SPA behavior, or interactive desktop compatibility. Historical native exits below
remain unresolved.

Startup/render/shutdown passed (`/tmp/steed-form-startup-smoke.log`). The form
approval popup was rendered and visually checked at 480×420 with readable scope,
site list, notices, and Approve/Deny buttons (`/tmp/steed-form-approval.png`).
Ruff, formatting, whitespace, and thirteen local documentation links passed.
The first local Google HTTPS smoke process exited natively (139) during the first
persistent `QWebEngineProfile` construction at `profile_manager.py:142`, before
task/form operations. The fault trace is `/tmp/steed-form-google-smoke.log`.
The identical diagnostic retry passed HTTPS redirects, POSTs, and compatibility
opt-out (`/tmp/steed-form-google-retry.log`). This adds another occurrence to the
intermittent headless startup issue; no root cause or fix is claimed.

A3 control/wait stage (October 1, 2026): the complete 224-test suite passed on
Qt 6.11.2. Nineteen new action/wait regressions plus explicit-permission and
shared-profile checks verify trusted text input and undo, field limits, clearing,
checkbox/radio state, selection by index, rejected sensitive/unsupported controls,
freshness after edits, readable child frames, observed scrolling, ready/text
waits, bounded timeout, superseding loads, interruption/revocation, delayed
callback expiry, and request/receipt journal failures. User controls and document
scripts stayed independent while sharing the task's signed-in profile. No entered
text or wait query was written to the journal. Existing browser checks passed.

The implementation uses Chromium editing commands and documented HTML/DOM
control semantics. It does not establish physical pointer/native-select gestures,
submission, script-enabled forms, SPA behavior, or real-account compatibility.
Startup/render/shutdown passed on a diagnostic rerun after one headless fixture
process exited natively (139); the cause remains unknown. Local Google HTTPS
compatibility, Ruff, formatting, whitespace, and local documentation-link checks
passed. This does not replace manual desktop acceptance.

An exploratory temporary DevTools focus-emulation probe also terminated a fixture
process natively; that path is absent from the implementation. No cause or fix
is claimed for either native exit. The shipped action paths passed the suite above.


Latest collaborative-profile/navigation update (October 1, 2026): the complete
198-test suite passed, followed by 44 focused checks after adding five further
regressions and fixing same-document anchor navigation. The suite now contains
203 tests. The focused run covered all current inspection, target-navigation,
shared-profile, and approval-popup tests. Startup/render/shutdown, local Google
HTTPS compatibility, Ruff, formatting, and whitespace checks passed. A synthetic
popup was rendered and visually inspected separately from desktop acceptance.

New coverage includes queued nonmodal approvals, explicit decisions, pending
requests without windows, plain-text objectives, failed approval writes, two tasks
and a user sharing one signed-in profile, selective revocation, retained user
POST/download/worker behavior, worker-protection loss, target scope and permission
checks, stale/unsupported/long links, anchor jumps, blocked redirects, observed
load results, timeout, stop/supersession, and request/receipt journal failures.
Real-account collaboration and interactive desktop usage remain user follow-ups.

Earlier verification records:

Before the shared-profile/popup/navigation update, all 188 browser/A1/A2/A3
regressions passed (October 1, 2026) on Qt
6.11.2, including four new task-storage checks and six real-Qt task-service
checks. The deterministic client exercised navigation without a view, attachment
to the same page, completion, task/cookie isolation, worker dispatch, queued
cancellation, restart recovery, shared-window lifetime, and write-failure
handling. The combined suite required one shared QApplication to avoid a native
WebEngine crash when a later test class recreated the application.
The latest checks also cover find match counts, direction and case, navigation,
profile-query isolation, stale callbacks, fullscreen restoration, zoom shortcuts
and bounds, injected download destinations, invalidated permissions, task
permission denial, and exactly-once screen-share cleanup. Ordinary tabs and
task pages use the shared navigation and permission services.
Startup/render/shutdown and local Google-login HTTPS smoke checks, desktop-entry
validation, Ruff, formatting, and whitespace checks passed. The user authorized
proceeding with remaining manual follow-ups recorded in the roadmap. Password
improvements are deferred.

Initial A2 verification added nine policy checks, four storage checks, twelve real-Qt service checks,
fourteen existing-profile checks, and a browser menu failure check to the previous
132-check suite. Coverage includes pending/denied
authority, client identity, origin/profile/action/file rejection, native redirect
and resource interception, POST blocking, cancelled task downloads, UI approval
and revocation, expiry without further commands, concurrent request budgets,
queued-work rejection, database migration, restart recovery, and SQLite approval
failure handling. The new approval dialog was rendered with a synthetic request
and visually inspected; this is separate from manual desktop acceptance.
Existing-profile checks use temporary storage and synthetic credentials. They
now verify cookie/local-storage continuity, concurrent user/task access,
switching/pop-outs, profile path restrictions, task downloads without destination
prompts, ordinary user downloads and POST requests, redirect/resource boundaries,
worker bypass for task pages with ordinary workers retained, selective revocation,
expiry, ad filtering, shared-window shutdown order, and journal/setup failures. These checks establish fixture behavior, not
real-account compatibility. Task document scripts and file operations remain
unavailable.

A3's read-only stage adds fourteen inspection/broker checks, one explicit-content
policy check, and one signed-in read/approval check to the 172-test baseline.
Real Qt fixtures verify extraction without a view, field-value/editable-text
omission, control descriptions, readable same-origin frames, inaccessible frame
limits, output bounds, unchanged DOM, stale/replaced/forged targets, cross-task
denial, timeout, supersession, revocation, callback-time expiry, deferred worker
replies, shutdown, and journal failure without content disclosure. The full
188-test suite, startup smoke, Google compatibility smoke, lint/format, and
whitespace checks passed. These results apply to the read-only stage; target-based navigation is added below.
Control activation and full SPA workflows remain unimplemented.
The content-access disclosure was rendered with a synthetic existing-profile
request and visually inspected. All fourteen focused inspection checks passed
again after tightening string and label traversal bounds.

During A1 verification, one full-suite attempt exited with a native WebEngine segmentation fault
during its first test. The same test passed with crash diagnostics enabled, and
the subsequent full 132-test suite and startup smoke passed. No cause was
established; this is recorded as an intermittent native failure, not a confirmed
fix.

### Earlier implementation checks

These are recorded checks from earlier implementation work, not a fresh run of
the entire suite. Live desktop checks still pending are tracked in the
[roadmap acceptance checklist](../ROADMAP.md#browser-acceptance-checklist).

History and refresh additions: four history storage tests and six focused
Qt browser checks passed, covering reload shortcuts from page/address focus,
completion navigation, shared-window/private-profile isolation, successful
visit recording, hostname matching, and profile rename persistence. Five
existing profile-switching/shutdown checks also passed. Ruff lint/format and
whitespace checks passed. Desktop Auto-Type remains a manual check.

Password integration verification: seven encrypted local-protocol checks and nine
real Chromium form/UI checks passed, including replay/tamper rejection,
credential-free pairing persistence, denial/cancellation, separate login steps,
save requests, keyboard filling, navigation invalidation, vault locking after
listing, and form-action changes during input handling. Four existing profile
and shutdown checks also passed. An isolated real KeePassXC 2.7.6 vault passed
encrypted save/retrieve and vault-lock checks with a seeded fixture association.
The user still needs to verify interactive pairing and normal site filling on
the desktop. The official Bitwarden 2026.9.2 extension crashed this Qt WebEngine
runtime during both install and unpacked load; generic extension support has
not been exposed.

Verification: 71 tests passed, including real Chromium request/cosmetic filtering, nested/srcdoc frame hiding and reload exceptions, filter compilation and immediate cached-worker outcomes, fullscreen enter/exit and repeated requests, completed file downloads, cookie scope and exact-path deletion, global update/logout, shared-window lifetime, a two-process persistent-cookie restart, persisted-cookie enumeration through the DevTools adapter, the startup-sharing flag in all-profiles mode, abandoned-download-dialog cancellation, shutdown confirmation when active downloads are genuinely at risk, and cookie-snapshot refresh scheduling, bounded recovery, and stale-response rejection. Repeated runs were used to confirm the suite is stable rather than merely green once. Ruff lint/format checks, whitespace checks, and the real-entry-point startup/render/shutdown smoke test also passed with Qt 6.11.2.

The final audit's four findings are fixed: fullscreen requests now follow their requested state; cookie inspection retries timeouts, bounds target errors, and ignores obsolete responses; cached filter compilation starts only after its completion handlers and service state are ready; and cosmetic filtering injects document-specific CSS into accessible same-origin frames rather than only the parent page. Manual follow-ups are recorded in [ROADMAP.md](../ROADMAP.md#browser-acceptance-checklist).

Two further defects were found and fixed while stabilizing that coverage, both affecting B2 in ways the previous suite did not observe:

- A stale enumeration of a destination profile arriving after the browser's own copy made `target_added` treat the old value as a local edit. That permanently marked the site locally controlled, which then suppressed the next global logout.
- A refresh requested while an inspection was still in flight was dropped. Qt reports a cookie *overwrite* while the previous run is pending, so overwriting a global cookie left the shared baseline permanently stale with nothing left to re-trigger a read. Queued refreshes are now recorded and re-run, and the global snapshot is re-inspected until two consecutive readings agree.

Refreshes are also debounced and the DevTools SDK module is cached on the frontend page, which removed the repeated module load that dominated each enumeration.

Startup and launcher fixes (October 1, 2026): six focused desktop regression
checks passed, covering the initial Quick Switch menu and shortcut, profile
switching, shared-window lifetime, reload, and shutdown. The executable was
checked from another working directory with a path containing spaces; desktop
entries passed `desktop-file-validate`, and Ruff and whitespace checks passed.
The Steed application-menu entry was installed on the development machine.

## References

- [Qt pages](https://doc.qt.io/qt-6.11/qwebenginepage.html)
- [Qt extensions](https://doc.qt.io/qt-6.11/qwebengineextensionmanager.html)
- [Google browser/login restrictions](https://support.google.com/accounts/answer/7675428)
- [Brave filtering engine](https://github.com/brave/adblock-rust)
- [MCP transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
- [Exa search](https://exa.ai/docs/reference/search)
- [Jev models](https://docs.typesafe.ai/models)
