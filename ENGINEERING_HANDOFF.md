# Engineering handoff — October 3, 2026

## Start here

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
Manual configurable UI remains the next substantive layout scope.

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
  published tools: 70. Stable request IDs and expected revisions govern edits.
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

Connector PR [#2](https://github.com/AdvancedForge/steed/pull/2) is unmerged at
reviewed head `d94f093`. All 75 Linux profile/catalog/interpreter/HTTP checks pass;
Ruff/format pass. The prior corrections are delivered and the starter subsets
receive a positive [assessment](https://github.com/AdvancedForge/steed/pull/2#pullrequestreview-5402708365).
The contributor has [five substantial milestones](https://github.com/AdvancedForge/steed/pull/2#issuecomment-5973415389).
The connector PR was not merged during the engineering integration. Re-review any
new head before merge; fixtures are not live account proof.

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

**Next daily-workflow increment: configurable browser UI.** Read the current
engineering queue and documentation customization plan. Start with persisted
manual toolbar visibility/order, sidebar side/width, density and named layouts.
Keep today's defaults and make Settings/Reset always reachable. Use stable
component IDs and validate configuration; test restart, invalid settings and
shared-window behavior before adding versioned agent proposals with preview,
apply/export/import and last-working-layout recovery. Native panels come later.
Never let layout changes grant profile/library/account capabilities or execute
agent-generated Python. This work extends Qt; no rebuild is needed.

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
