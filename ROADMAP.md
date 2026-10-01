# Steed roadmap

Build a useful daily browser first, then add a controlled collaboration layer for
external agents. Steed provides browser capabilities, context, permissions,
execution, and evidence; external agents provide planning and generation.

See [DOCUMENTATION.md](DOCUMENTATION.md) for setup and implementation details,
including the [planned architecture](DOCUMENTATION.md#planned-architecture).

## Current work — October 1, 2026

Browser essentials and recent daily-use improvements are implemented with
automated checks. The next work is desktop acceptance and fixes found during
normal use. Agent implementation has not started.

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
| KeePassXC integration | Implemented; encrypted protocol, Chromium forms, and synthetic real-vault checks passed | Interactive pairing and filling/saving on real sites |
| Quick Switch startup and desktop launcher | Implemented; six focused regressions and launcher validation passed | Confirm application-menu launch and pinning during normal use |

Detailed results and test scope are recorded in the
[verification record](DOCUMENTATION.md#verification-record). Passing automated
checks does not complete the manual acceptance gate.

## Browser acceptance checklist

Complete this gate and resolve browser blockers before starting A1/A2.

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
- [ ] Pair KeePassXC, fill with Ctrl+Shift+L, and save a login on the desktop.

Use the pinned project virtual environment for the login checks. The older
system Qt 6.4.2 runtime stalled in the local Google HTTPS fixture. Interactive
KeePassXC pairing was not covered by the seeded synthetic-vault smoke check.

## Remaining browser improvements

These are pending enhancements. Move them earlier when needed for a concrete
daily-use workflow; they do not automatically block the agent milestones.

- **Password convenience:** automatic save prompts, updating existing entries,
  iframe/shadow-root forms, TOTP, and passkeys.
- **Extensions:** per-profile import, enable/disable/remove, startup restoration,
  and action popups. Investigate the official Bitwarden package crash on the
  tested Qt 6.11 runtime before presenting it as supported. Start with Manifest
  V3 and verify Qt's limits.
- **Bookmarks:** per-profile folders, search, editing, and HTML import/export.

## Agent collaboration milestones — not started

Keep Python, PyQt6, and Qt WebEngine initially, with replaceable engine/provider
interfaces. Prefer text and structured state; support watching, takeover,
resume, revocation, and cancellation. Each milestone below is pending.

### A1. Browser services and task storage

Extract reusable browser services, dispatch Qt operations through a command
broker, define a backend interface, and store tasks/events in application-owned
SQLite. Acceptance: a deterministic client operates a task session without AI.

### A2. Task sessions and permissions

Create task-owned pages independent of tabs, default to off-the-record profiles,
and enforce explicit task grants through all mutation paths. Acceptance:
cross-task, profile, destination, and grant violations fail before execution.

### A3. Page inspection and actions

Expose structured snapshots and revision-bound target handles with deterministic
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
