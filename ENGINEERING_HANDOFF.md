# Engineering handoff — October 3, 2026

## Start here

The persistent agent file-library milestone is complete at `7e7596d`.
The reviewed `f6c28b4` engineering branch adds bounded library discovery
(`steed_library_search` plus the library window filter), so branch from the
current tip of `main`, not from the older chat-only checkpoint `9d1e37f`. Read
`AGENTS.md`, the top of `ROADMAP.md`, and
[library instructions](DOCUMENTATION.md#persistent-agent-file-library) first.
Historical roadmap checkpoints describe earlier milestones; the current-work
section and this handoff take precedence for the next engineering scope.

User priority: improve daily browsing and delegated/collaborative agent work.
Public-release gates are parked reference backlog. Google shared registration
and password-manager improvements remain deferred. No rebuild is proposed.
External agents retain their own tools; Steed supplies communication, browser
capabilities, reusable files and explicit access controls.

## Branches, ownership and review

Work on a new branch such as `engineering/request-headers`, preferably in a
separate checkout/worktree. Do not modify the user's running checkout or profile:

```bash
git fetch origin
git worktree add -b engineering/request-headers ../steed-request-headers origin/main
```

Verify that the checkout contains `services/file_library.py` and this document.
Use its own environment or an explicitly chosen compatible environment; do not
assume the new worktree inherits `.venv`. Follow local sandbox permissions when
creating a sibling checkout. Never inspect real user profiles or credentials for
test fixtures.

The connector contributor owns `connectors/recipes/`, `connectors/fixtures/`,
`connectors/SETUP.md` and `tests/test_default_connector_profiles.py`. Leave those
paths to that contributor. The reviewed discovery branch has merged; the next
shared runtime scope is reviewed non-secret static headers described below.
Shared `services/`, `ui/`,
MCP, root docs and shared-test changes need coordination with the maintainer;
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
- `ui/file_library.py`: **File → Agent File Library…** and sidebar entry, copy
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

Once the contracts are corrected, the smallest useful shared framework increment
is reviewed **non-secret static headers**, such as API-version headers. Plan it
with the maintainer and keep connector data owned by the contributor. Bind headers
to reviewed source/hash, display them during source review, apply them consistently
to identity/read/write requests and invalidate old approvals on source changes.
Preserve old recipes and transport origin/DNS/redirect/payload checks. Reject
attempts to override authentication, host, cookies or transport/framing headers;
bound names/values/counts and reject newline injection and duplicate names.
Credential-derived headers such as Figma's token need a separate authenticated
contract, not token literals in a recipe. Headers alone do not imply that a whole
service is supported; nullable identities, paging and response envelopes remain
separate requirements. Runtime tests must prove the exact wire header set.

## Validation and checkpoint evidence

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
