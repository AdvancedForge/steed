# ROADMAP — Daily-Driver Browser → Agent Collaboration Layer

## Vision and decisions

The browser must be useful for ordinary browsing first. It then becomes a controlled workspace for independent agents to research, interact with websites, and use connected applications. The browser provides capabilities, context, permission enforcement, deterministic execution, and evidence. External agents provide planning, reasoning, and generation. Page classification is optional assistance, not an internal agent loop.

Keep Python, PyQt6, and Qt WebEngine initially; target the current Linux machine first. Keep engine and provider interfaces replaceable without a speculative rewrite. Prefer text and structured state over screenshots. A user can watch, pause, take over, resume, revoke, or cancel delegated work.

Implementation order:

1. Complete and verify the seven browser essentials, in the order below.
2. Implement reusable browser services, task sessions, grants, and deterministic page tools.
3. Expose tools through framework-neutral MCP adapters.
4. Add optional search and classification providers.
5. Add controlled application connections and collaboration UI.
6. Validate research → document → email end to end.
7. Add extensions and bookmarks as non-blocking enhancements.

Extensions/bookmarks move earlier only for a concrete workflow dependency. Built-in agent hosting is a separate future proposal, not part of the initial framework.

Status vocabulary: **pending**, **in progress**, **implemented — verification pending**, **verified**, **blocked**. Record verification and blockers here; an attempted action is not evidence of completion.

## Current handoff — September 30, 2026

The seven browser essentials below are implemented, with automated verification. The manual daily-use gate remains pending. Agent implementation has not started; extensions and bookmarks remain non-blocking later work. The user requested a stopping point here and will continue later.

October 1 update: the user reports Google account sign-in appears to work after the account-host compatibility fix. This verifies the reported account attempt; sign-in persistence after restart and third-party “Sign in with Google” remain unconfirmed. Local HTTPS redirects, POST bodies, and opt-out passed on Qt 6.11.2; the older system Qt 6.4.2 fixture stalled. Use the project virtual environment for the remaining login checks.

October 1 daily-use additions: Ctrl+R/F5 reload the active tab and Ctrl+L selects
the address bar. History suggestions match URLs and titles, retain up to 1,000
unique successful web visits per profile, survive ordinary-profile restarts,
and remain memory-only in incognito. Windows share history within a profile;
**File → Clear Browsing History...** clears its visits and suggestions.
KeePassXC Auto-Type is the initial documented password-manager workflow for the
current X11 desktop; window titles expose the active hostname for matching,
independently of page titles. No manager preference was given. A live vault
workflow and extension-based autofill remain unverified; credentials stay in
the external manager. See the README for setup.

Verification for these additions: four history storage tests and six focused
Qt browser checks passed, covering reload shortcuts from page/address focus,
completion navigation, shared-window/private-profile isolation, successful
visit recording, hostname matching, and profile rename persistence. Five
existing profile-switching/shutdown checks also passed. Ruff lint/format and
whitespace checks passed. Desktop Auto-Type remains a manual check.

Verification: 71 tests passed, including real Chromium request/cosmetic filtering, nested/srcdoc frame hiding and reload exceptions, filter compilation and immediate cached-worker outcomes, fullscreen enter/exit and repeated requests, completed file downloads, cookie scope and exact-path deletion, global update/logout, shared-window lifetime, a two-process persistent-cookie restart, persisted-cookie enumeration through the DevTools adapter, the startup-sharing flag in all-profiles mode, abandoned-download-dialog cancellation, shutdown confirmation when active downloads are genuinely at risk, and cookie-snapshot refresh scheduling, bounded recovery, and stale-response rejection. Repeated runs were used to confirm the suite is stable rather than merely green once. Ruff lint/format checks, whitespace checks, and the real-entry-point startup/render/shutdown smoke test also passed with Qt 6.11.2.

The final audit's four findings are fixed: fullscreen requests now follow their requested state; cookie inspection retries timeouts, bounds target errors, and ignores obsolete responses; cached filter compilation starts only after its completion handlers and service state are ready; and cosmetic filtering injects document-specific CSS into accessible same-origin frames rather than only the parent page. The manual acceptance gate below is unchanged.

Two further defects were found and fixed while stabilizing that coverage, both affecting B2 in ways the previous suite did not observe:

- A stale enumeration of a destination profile arriving after the browser's own copy made `target_added` treat the old value as a local edit. That permanently marked the site locally controlled, which then suppressed the next global logout.
- A refresh requested while an inspection was still in flight was dropped. Qt reports a cookie *overwrite* while the previous run is pending, so overwriting a global cookie left the shared baseline permanently stale with nothing left to re-trigger a read. Queued refreshes are now recorded and re-run, and the global snapshot is re-inspected until two consecutive readings agree.

Refreshes are also debounced and the DevTools SDK module is cached on the frontend page, which removed the repeated module load that dominated each enumeration.

Implementation notes for the next session:

- Qt 6.11's `loadAllCookies` path does not report already-persisted cookies reliably in the tested runtime. `cookie_snapshot.py` uses a private, in-process DevTools frontend to obtain authoritative snapshots; no remote debugging listener is opened. Global-cookie state and the cookie viewer's load path both depend on this adapter, so a regression test now asserts that a known persisted cookie is enumerated and that the viewer lists it. `VERIFIED_AGAINST_QT` records the confirmed engine version; re-run those tests when Qt is upgraded. Snapshots reject concurrent-change races, coalesce writes that land mid-inspection, re-run rather than drop a refresh requested while one is in flight, and omit unsupported partitioned identities from viewing/sharing. One enumeration still costs seconds because the adapter is inherently a full protocol round trip; that is why the cookie tests observe the store's own signals where they are not specifically testing the adapter.
- Existing profiles deliberately do not accept previously unobserved global cookies automatically. Their pre-existing cookies stay local. Use **Cookies → Use Global Cookies for This Site** for an explicit baseline reset. New empty stores can inherit automatically; restarted stores reconcile confirmed inherited identities and explicit site opt-ins.
- Selected deletion expires the exact cookie identity rather than using Qt's broad name/URL deletion filter. Explicit deletion protects against reinheritance. A reset after Clear All enables only the chosen site, not all sites.
- Ad blocking starts with a small bootstrap list. Use **File → Ad Blocking → Download / Update EasyList and EasyPrivacy** for full lists. Network rules and supported CSS hiding are implemented; scriptlets and resource redirects are not enabled. Updates preserve the last working rules on failure. Cached full lists are compiled on a worker thread at startup rather than blocking the first window; the browser opens with bootstrap rules and upgrades when compilation finishes.
- `global_cookie_sharing_apply_on_startup` now gates all-profiles mode as well as on-switch mode. Previously the checkbox had no effect once sharing was set to all profiles, which could overwrite local logins on every launch.
- Profiles share engine instances across windows. Incognito data lives until the owning application registry closes, not merely until a profile switch. Profile archival is recoverable under `~/.custom_browser/deleted_profiles/`; active profiles cannot be renamed/archived.
- Download history persists only non-incognito metadata; unfinished entries become interrupted on restart. Downloads are never automatically reopened or restarted.

Manual acceptance checklist before A1:

- [ ] Launch on the normal desktop and verify graphics, navigation, search, media permissions, multiple windows, and clean shutdown.
- [ ] Reopen closed tabs repeatedly, switch profiles, and verify independent histories and restored groups/positions.
- [ ] Test real global and separate profile logins, restart, logout, explicit deletion, and confirmed global-baseline reset.
- [x] Google account sign-in: user reports it appears to work after the compatibility fix (October 1, 2026).
- [ ] Verify UA settings and signed-in session persistence after restart; test third-party Google sign-in. Record the exact error if either fails.
- [ ] Download a real file; check progress, pause/resume where supported, cancellation, opening/revealing files, and active-download shutdown confirmation.
- [ ] Update full filter lists, test ordinary sites, and verify exceptions after reload.
- [ ] Test filtered cookie deletion and tab-group operations during normal use.
- [ ] Try history suggestions, Ctrl+R/F5, Ctrl+L, and KeePassXC Auto-Type on the desktop.

Next step: complete this checklist and resolve any browser blockers, then start A1/A2. Do not mark the agent framework complete or bypass the agreed browser gate.

## 1. Browser essentials — prerequisite gate

### B1. Recently closed tabs — implemented — verification pending

Note: a window always keeps one tab, so the final tab is neither closable nor restorable. Restoring into a collapsed group expands it deliberately so the restored tab stays visible.

- Maintain bounded, memory-only closed-tab history per profile.
- Restore URL, title, insertion position, and group through the menu and Ctrl+Shift+T.
- Do not restore another profile's closed tabs; empty history is harmless.
- Verify repeated closure/restoration in reverse order, profile switching, and invalid indices.

### B2. Global profiles — implemented — verification pending

Global cookies are a shared baseline, not unconditional replacements:

- Share one-way, global → ordinary profiles; never automatically publish ordinary cookies back.
- Track inherited-cookie provenance without persisting extra plaintext cookie values.
- Preserve pre-existing cookies as local during migration.
- Independent destination cookie changes/deletions make that site locally controlled; stop merging global cookies there to avoid mixing accounts.
- Apply global updates/deletions only to cookies still identified as inherited.
- Provide an explicit, confirmed “Use global cookies for this site” reset.
- Apply settings changes immediately and respect the startup-sharing setting.
- Replace fixed-delay, one-shot copies with idempotent event-driven reconciliation.
- Preserve host-only/domain distinctions. Never guess ambiguous origins or share unsupported cookie identities.
- Share one application-level engine-profile registry across windows. Never use competing engine instances for the same on-disk profile.
- Keep incognito isolated and provenance-free. Cookie sharing is not local-storage or complete authentication-session synchronization.
- Verify updates, deletion, local login preservation, migration, restarts, settings changes, multiple windows, and incognito isolation.

### B3. User-agent overrides and login — implemented — verification pending

- Apply overrides at profile creation/startup, including incognito; remove the outdated hardcoded Chrome default.
- Clearing an override restores the engine's actual default. Display the effective UA.
- Google account sign-in now has a default-enabled, account-host Firefox User-Agent workaround based on qutebrowser. A page-level interceptor preserves profile ad filtering. GET redirects are restarted with the destination's header; POSTs are not replayed. Local HTTPS acceptance is covered by `tests/smoke_google_login.py` on Qt 6.11. The user reports account sign-in appears to work (October 1); restart persistence and third-party sign-in remain unconfirmed.
- The system Qt 6.4.2 runtime stalled during the HTTPS fixture after account-host GET redirects. Use the pinned virtual environment for the live login gate.
- Verify newly created/existing profiles, settings changes, and restart.
- Manually test Google account login and third-party Google sign-in. UA spoofing is not a guarantee: Google may reject embedded browsers separately.
- If a required login is impossible on Qt, record the exact failure and revisit the engine before further dependent work. External-browser connector OAuth does not log websites into the Qt browser.

### B4. Download manager — implemented — verification pending

- Progress, destination, state, errors, cancel, supported pause/resume, history, open/reveal actions.
- Explicitly cancel abandoned save dialogs. Require a clear decision before closing with active downloads.
- Persist metadata, not live engine requests; unfinished historical requests become interrupted on restart.
- Verify real local downloads, interruption, cancellation, empty save dialogs, completed file existence, and shutdown.

### B5. Ad blocking — implemented — verification pending

- Qt request-interceptor network filtering through a filtering-engine adapter; use Python bindings to Brave's engine rather than inventing a filter parser.
- Filter-list management, last-known-good updates, per-site exceptions, diagnostics, and supported cosmetic filtering.
- No network or expensive list compilation in the request-interception hot path.
- Report unsupported filtering capabilities instead of claiming full uBlock compatibility.
- Verify advertisement/tracker fixtures, exception behavior, malformed updates, and regular-site usability.

### B6. Cookie deletion — implemented — verification pending

- Load cookies when opening the viewer; update from store events.
- Delete selected, site-level, or profile-wide cookies. Filtered rows must target the displayed cookie.
- Explicit deletion prevents immediate reinheritance; integrate with B2 provenance.
- Verify exact deletion, filtered selections, asynchronous updates, and no resurrection.

### B7. Tab groups — implemented — verification pending

- Create, rename, delete, move, collapse/expand, and persist groups.
- Stable tab/group identities, not mutable indices, define membership.
- Verify reordering, closure/restoration, profile switching, restart, and selected-tab behavior during collapse.

### Browser acceptance gate — pending

All seven essentials must have automated regression coverage and a manual daily-use checklist before agent implementation. Check navigation/address/search, startup/shutdown, multiple windows, permissions, profile persistence, and required Google login. Unverified live-account behavior remains explicitly pending rather than being represented as successful.

## 2. Agent framework

### A1. Capability services and task storage — pending

- Incrementally extract profiles, sessions, navigation, downloads, permission handling, and persistence from the UI coordinator.
- Keep engine operations on the Qt thread; dispatch through a command broker.
- Introduce a backend interface for session creation, navigation, inspection, actions, and view attachment; implement Qt only initially.
- Application-owned SQLite stores task records and an append-only event journal; retain existing profile/config compatibility.
- Acceptance: a deterministic test client operates a task session with no AI or external framework.

### A2. Sessions and grants — pending

- Task-owned pages, grant, artifacts, history, and lifecycle exist independently of visible tabs.
- Default to a separate off-the-record task profile. Explicitly authorized persistent profiles share their existing cookies/storage; disclose that boundary.
- Task-scoped grants cover sites, profiles, app tools, file destinations, data disclosure, external effects, duration, and budgets. Standing permissions supply defaults, never self-expanding authority.
- A proposed task is not authorized until approved by the user. Ask again only when new authority is needed.
- All UI/test/MCP mutations use the same policy checks.
- Deny arbitrary shell, unrestricted filesystem, raw cookie export, and public unrestricted JavaScript evaluation.
- Private-network destinations require explicit permission. Page text, classification, and connector descriptions are untrusted, not authority.
- This is a browser capability boundary, not an OS sandbox for external agents; tools accessed outside it cannot be controlled.
- Acceptance: cross-task/profile/destination/grant attempts fail before controlled execution.

### A3. Text-first inspection and deterministic actions — pending

Prefer authorized app/API connectors, then public document retrieval, then live JS-enabled pages. Visual inspection is optional or user-requested. A session owns QWebEnginePage independently of a view; attach the same page on watch/takeover. Invisible pages may still need JS, layout, and Chromium resources.

Snapshots include URL/title, frame identity, revision, readable content/provenance, and candidate actions with labels/roles/operations/enabled state/constraints. Report limits for inaccessible frames, closed shadow roots, canvas, and ambiguous custom controls.

Use browser-issued opaque target handles tied to session/frame/revision. Reject stale/ambiguous targets. Support navigation, activation, entry, selection, scrolling, waits, authorized uploads, and downloads. Use native input semantics where synthetic JS clicks are insufficient. Return observed changes and evidence, not just dispatch success.

Acceptance: fixtures for static pages/forms/SPAs/frames/DOM replacement work without screenshot loops; unsupported actions request takeover or return explicit limitations.

### A4. Framework-neutral MCP — pending

- Maintained MCP SDK; stdio client-launched adapters and an authenticated loopback-only Streamable HTTP endpoint.
- Restricted local IPC links adapters to the single running Qt process. No public debug port or grant bypass.
- Published schemas, capability discovery, structured errors, SDK-supported protocol compatibility, and interoperability tests.
- Initial tools: `task_create`, `task_status`, `task_control`, `task_events`; `session_open`, `session_close`, `page_navigate`; `page_inspect`, `page_act`; `search_query`; `artifact_store`, `artifact_list`; `app_list_tools`, `app_call_tool`.
- Core types: TaskGrant, SessionSnapshot, ActionDescriptor, ActionResult, Artifact, Connector.
- Results distinguish success, denied authority, required input, stale state, timeout, unsupported behavior, failure, and uncertain external outcome.
- Acceptance: two independently implemented MCP clients use the same tools without framework-specific browser code; both transports are tested.

### A5. Optional search and classification — pending

Search uses an Exa adapter returning URLs/titles/content when available and retrieval provenance. Keep address-bar search unchanged. Credentials, disclosure permission, budgets, caching, timeouts, and rate-limit handling are required.

Extract candidates deterministically first; a Jev adapter answers finite classification questions with a pinned, benchmarked model version. Support replaceable cloud endpoints and a local-provider adapter. Classification annotates candidates/purpose; it never plans, invents targets, or authorizes execution. Cache by revision/context, batch where supported, and report uncertainty on low confidence/failure. Redact credentials/passwords/sensitive fields; authenticated content needs explicit disclosure permission. No-provider operation remains fully functional.

Acceptance: held-out comparisons against deterministic-only inspection measure coverage, incorrect mappings, latency, and cost. Enable classification only where it improves results.

### A6. Application connections and recovery — pending

- Browser-mediated mode: MCP gateway enforces grants, logs activity, handles cancellation, and records results.
- Agent-managed mode: direct external connections; clearly disclose absent browser visibility/enforcement.
- User-approved connector setup; secrets in OS credential storage, never styling/exported logs.
- Trusted configuration defines risk; remote tool annotations alone do not. Sending/publishing/deleting/spending requires explicit authority.
- Persist request IDs/receipts, bound read retries, never blindly replay uncertain writes, reconcile external state where possible.
- Report partial results/blockers/uncertainty. Do not replay unfinished writes automatically after restart.
- Acceptance: generic gateway tested against mock document/email MCP servers before real accounts.

### A7. Collaboration UI and declarative styling — pending

- Task workspace shows agent identity/objective/authority/status/activity/artifacts/evidence.
- Watch attaches a view without taking control. Takeover obtains exclusive ownership and blocks agent mutations. Resume invalidates stale snapshots. Cancel stops queued/new commands; dispatched external effects may require reconciliation rather than rollback.
- Multiple agents have separate identities/tasks; each session has one mutation owner.
- Versioned `appearance.toml` controls supported browser chrome: colors, typography, spacing, component order/visibility/panel placement.
- Component registry and validated layouts, live preview, last-known-good rollback, recovery shortcut. No executable configuration or removal of all safety/recovery access. Website styling is separate later work.
- Acceptance: user watches/interrupts, continues in the same page, then resumes delegation without losing session state; malformed styling rolls back safely.

## 3. End-to-end validation and rollout

### Research → document → email — pending

The external agent obtains a scoped task grant, searches/inspects sources, generates its own document, stores it with references/provenance, creates a document through an approved connector, sends only to authorized recipients, and returns document references/receipts/unresolved limitations. Browser-side generation is not required.

Use fixtures/mock connectors first, then user-configured real services. Roll out locally: read-only inspection → controlled page writes → mock app writes → explicitly authorized real writes.

Required regression coverage:

- Browser essentials, profile persistence, restart/shutdown, permissions, multiple windows.
- Global-cookie migration, local logins, updates/deletions, no resurrection, incognito isolation.
- Stale targets, replaced DOM, frames, SPA transitions, unsupported controls/timeouts.
- Unauthorized profiles/destinations/files, forged handles, hostile instructions, connector denials.
- Takeover/resume, simultaneous commands, cancellation, expired grants, disconnect.
- Crash/interruption, failed providers, uncertain sends, duplicate request IDs.
- MCP transports/multiple clients/unsupported capabilities; styling rollback/recovery.

Definition of done: independently implemented clients complete the workflow, every controlled mutation passes policy, and failures provide usable evidence. Universal external task success is not promised.

## 4. Later browser enhancements

### Extensions — pending, non-blocking

Per-profile filesystem import/install, enable/disable/remove, errors, and action popups. Persist enabled choices and restore after startup. Start with Manifest V3 only. Do not promise arbitrary Chrome compatibility or store installation; Qt excludes Manifest V2 and custom extensions in off-the-record profiles.

### Bookmarks — pending, non-blocking

Per-profile folders/search/editing and HTML import/export. Keep bookmarks separate from task artifacts.

### Built-in agent hosting — separate future proposal

Consider scheduling/autonomous planning/model-account management only after proving the framework. A new proposal must cover OS isolation, billing, credentials, resources, and recovery. The initial roadmap does not include an internal AI runtime or unrestricted automation.

## References

- [Qt pages](https://doc.qt.io/qt-6.11/qwebenginepage.html)
- [Qt extensions](https://doc.qt.io/qt-6.11/qwebengineextensionmanager.html)
- [Google browser/login restrictions](https://support.google.com/accounts/answer/7675428)
- [Brave filtering engine](https://github.com/brave/adblock-rust)
- [MCP transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
- [Exa search](https://exa.ai/docs/reference/search)
- [Jev models](https://docs.typesafe.ai/models)
