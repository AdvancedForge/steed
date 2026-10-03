# Engineering handoff — October 3, 2026

## Start here

The persistent agent file-library milestone is complete in the commit containing
this document. Branch from that checkpoint on `main`, not the older chat-only
checkpoint `9d1e37f`. Read `AGENTS.md`, the top of `ROADMAP.md`, and
[library instructions](DOCUMENTATION.md#persistent-agent-file-library) first.
Historical roadmap checkpoints describe earlier milestones; the current-work
section and this handoff take precedence for the next engineering scope.

User priority: improve daily browsing and delegated/collaborative agent work.
Public-release gates are parked reference backlog. Google shared registration
and password-manager improvements remain deferred. No rebuild is proposed.
External agents retain their own tools; Steed supplies communication, browser
capabilities, reusable files and explicit access controls.

## Branches, ownership and review

Work on a new branch such as `engineering/library-discovery`, preferably in a
separate checkout/worktree. Do not modify the user's running checkout or profile:

```bash
git fetch origin
git worktree add -b engineering/library-discovery ../steed-library-discovery origin/main
```

Verify that the checkout contains `services/file_library.py` and this document.
Use its own environment or an explicitly chosen compatible environment; do not
assume the new worktree inherits `.venv`. Follow local sandbox permissions when
creating a sibling checkout. Never inspect real user profiles or credentials for
test fixtures.

The connector contributor owns `connectors/recipes/`, `connectors/fixtures/`,
`connectors/SETUP.md` and `tests/test_default_connector_profiles.py`. Leave those
paths to that contributor. Your first scope is the library backend/UI, its tool
registration/service integration and library tests. Shared `services/`, `ui/`,
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
- `services/task_service.py`, `task_client.py`, `agent_tools.py`: eleven library
  commands exposed through both MCP transports and native agent chat. Total
  published tools: 69. Stable request IDs and expected revisions govern edits.
  The workspace bridge reuses library inputs and retains sealed outputs.
- `browser_window.py`, `ui/agent_sidebar.py`: shared-window ownership, private
  profile UI guards and scrollable controls in short native chat tabs.

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
500 entries/import, 32 KiB chunks. UI currently displays up to 2,000 entries;
agents paginate children. Worker workspace limits remain 8 MiB/file, 32 MiB/task.
The database is private local storage, not an encrypted vault or forensic erase.
Normal websites never receive library access.

## Next milestone: make reusable files easy to find

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

## Following milestone: improve website observation

After the first PR is accepted, expand deterministic accessible names/roles and
coverage signals in `services/browser_agent.py` and its existing page fixtures.
Start with an audit of the actual current snapshot and target logic, then cover
labelled controls, open shadow roots, dynamic lists and frame/widget limitations.
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
reviewed head `c1821fe`. All 72 Linux profile/catalog/interpreter/HTTP checks pass;
Ruff/format pass. The earlier review fixes were delivered. The latest
[review](https://github.com/AdvancedForge/steed/pull/2#pullrequestreview-5402272122)
requests Notion identity/version corrections, GitHub issue/PR read disclosure and
precise GitLab quick-action wording. The contributor has
[detailed next steps](https://github.com/AdvancedForge/steed/pull/2#issuecomment-5972430276).
Re-review the corrected head before merge; fixtures are not live account proof.

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

Checkpoint evidence: full suite **906 passed** before final polish; final **148
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
