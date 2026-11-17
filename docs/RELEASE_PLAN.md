# Steed public release plan

Assessment: October 3, 2026, updated November 16, 2026. This is a proposed
release contract, not a claim that Steed is ready to ship. Where things stand is
in the [roadmap](../ROADMAP.md), what each round changed in the
[progress log](history/progress-log.md), and setup in the
[documentation](README.md). Recheck this list against each release tag.

Steed is an early preview: run from source, Linux only. This plan is
the backlog between that and a beta.

## Product vision and proposed release boundary

Steed should be a useful browser where people bring their preferred external
agents, talk alongside their browsing, delegate existing signed-in tabs, take
control back immediately, and let agents produce attachments or design service
connections. Steed owns browser state, user-controlled access and durable
receipts. The agent retains its own tools, reasoning and host permissions.

Recommended first public release: an **explicit Linux beta**, for selected and
tested distributions, with normal browsing, native/local external-agent access,
web-chat tabs/sidebar, live delegation, reviewed connectors and optional local
attachment generation. Agent features stay optional; ordinary browsing must work
without Codex, MCP, a keyring service or worker dependencies. Unsupported features
must be visibly labelled or unavailable, rather than presented as working.

A stable daily-browser release needs stronger compatibility, recovery,
distribution and maintenance evidence. Universal extension compatibility,
cross-device sync and every possible connector need not precede a beta. A
seamless cloud-agent connection does need to precede any promise that website
agents can directly control Steed after sign-in alone.

## What exists and what that evidence means

- Browser essentials: profiles/incognito, navigation, history suggestions,
  session/group restoration, downloads, ad blocking, find, zoom, website
  permissions, the built-in password vault and a terminal-free checkout launcher.
- Agent infrastructure: local authenticated MCP transports, scoped task grants
  and remembered choices, native Codex conversations, explicit live-tab sharing,
  Take Back, bounded DOM reads/native actions and separately approved viewport
  screenshots. Ordinary website scripts/network remain active on borrowed tabs.
- Durable work: drafts, selected input/output files, app reviews/receipts,
  uncertain-write handling, recovery with new authority, agent-designed recipes
  and approved local Python workers; an optional PDF/XLSX/image bundle.
- Website-chat UI: native agent chat can use a tab; normal HTTPS chats can move
  between tab and sidebar. Instructions/context have an explicit preview and
  copy/native-paste handoff. This does not connect their cloud models to MCP.
- Validation (November 16, 2026): **2,128 tests pass** on Qt 6.11.2, and
  startup/render/shutdown passes. Eight packaged app connections ship (Todoist,
  GitLab.com, Microsoft 365 read, Notion, Asana, Dropbox, Linear, Raindrop.io)
  plus three templates.

Automated synthetic checks do not prove live provider consent, broad website
compatibility, desktop graphics reliability, resistance to malicious pages or a
usable installer. Existing manual checks and occasional native Qt startup exits
remain release work. No completion percentage or release date is inferred.

## Required before a public beta

### 1. Release ownership, licensing and support scope

- Steed is licensed GPLv3 (LICENSE), which fits PyQt's GPL option
  ([Riverbank licensing FAQ](https://en.riverbankcomputing.com/commercial/license-faq)).
  Still to do: third-party notices and a dependency/license inventory, including
  Qt WebEngine/Chromium and the optional document wheels.
- Name supported Linux distributions, architectures, desktop/display systems and
  Python/Qt builds. Windows contributor tests do not imply Windows browser support.
- Define who maintains releases, handles vulnerabilities and can revoke a bad
  build. Record what “beta” promises and which features are experimental.

Acceptance: explicit license/support policy, required notices shipped, and a
named maintainer for engine/security updates. License selection is an owner
decision; collecting the inventory and testing targets are engineering work.

### 2. Installable, verifiable releases and engine updates

- Pick and build a Linux distribution route. The current `steed` script and
  `browser/install_launcher.py` point at a checkout and its `.venv`; they are not a
  standalone release package. Provide install/uninstall, desktop icon/branding,
  OS library checks and clean missing-dependency errors.
- Bundle or clearly provision optional MCP/keyring/worker dependencies. Verify
  Codex and Bubblewrap discovery without relying on the developer's PATH.
- Produce tagged, repeatable builds, dependency manifests, checksums and a
  release artifact verification process. Keep credentials out of every artifact.
- Establish a timely Qt/Chromium security-update policy, dependency review and
  an update channel with a tested upgrade/rollback procedure. A managed package
  channel can supply updates; a bespoke updater is not automatically required.

Acceptance: install, launch, upgrade and uninstall on clean supported machines;
profile data survives upgrades; verification and urgent engine-update ownership
are documented. Test the packaged sandbox, not just checkout Python.

### 3. Automated release gates and desktop reliability

- Add CI for lint/format, Linux unit/regression tests, startup smoke, migration
  checks and reproducible packaging. No CI workflow exists yet. Pin a
  known-good release environment and test dependency updates.
- Investigate the intermittent native startup exits with crash diagnostics and
  repeated serial runs; determine whether they are confined to offscreen/GPU
  test environments. Retrying alone is not an acceptance criterion.
- Execute the existing manual browser checklist on actual supported desktops:
  Wayland/X11 as advertised, scaling, dark/light themes, shortcuts before first
  use, navigation, groups, multiple windows, shutdown and launch without terminal.
- Test render-process failure, disk-full/read-only storage, abrupt termination,
  interrupted downloads, abandoned worker staging and orphan-process cleanup.

Acceptance: a release candidate passes CI and recorded desktop/packaged smoke
checks with no unexplained data-loss, shutdown or recurring startup failure.

### 4. Browser compatibility and basic trust indicators

- Record real login/restart/logout/session-clearing checks, including Google
  and third-party Google sign-in. User-reported Google success is useful but is
  not a cross-service guarantee; a user-agent workaround needs ongoing acceptance.
- Verify real downloads, file uploads, permission prompts, media/codecs,
  camera/microphone/screen sharing and popup flows. Publish actual DRM/passkey/
  extension limitations; do not promise parity with Chrome from Qt availability.
- Make origin, connection errors and permission ownership understandable; verify
  default rejection of certificate errors, mixed content and unsafe local-file
  access. Provide useful network/certificate/renderer error and recovery UI.
- Test adblock list updates/errors, cosmetic rules, exceptions and performance
  on representative sites. Confirm find/zoom/history and password fill in real use.
- OS-opened HTTP/HTTPS URLs and local files reach the running instance, and
  Settings → General registers Steed as the default browser (done; needs a
  check on GNOME, KDE and a minimal window manager).

Acceptance: a published compatibility matrix and complete ordinary-user journeys,
with unsupported behavior honestly surfaced. Avoid claiming every site works.

### 5. Privacy, local data and user-controlled deletion

- Audit permissions for all profile/config/history/cookie/task directories and
  databases. The base Config directory creation currently uses normal process
  defaults; verify private access end to end, including backups and temporary files.
- Explain per-profile separation and the intentional global-cookie baseline;
  confirm incognito never imports persistent consent or leaves durable session data.
- Provide understandable controls to clear cache/site storage as well as cookies,
  histories and retained agent materials. Audit what survives each operation.
- Publish a privacy/data-flow statement: local state versus third-party model
  traffic, selected context, screenshot values/embedded content, account tokens,
  clipboard history and retained receipts. Decide explicitly whether diagnostics
  are opt-in; do not ship secret-bearing logs or automatic crash uploads.
- Test migration/backup/restore and recovery without replaying writes. Clarify
  that clearing local results cannot retract data already fetched by a provider.

Acceptance: privacy documentation matches observed behavior, deletion/isolation
tests pass, and users can find and control every retained-data category.

### 6. Agent access, security review and public onboarding

- Conduct a focused review of hostile page content, prompt injection, misleading
  controls, origin changes, redirects, frames, delegation, saved grants,
  transport authentication and worker/recipe boundaries. Website text is data;
  it must never approve a task or silently expand permission.
- Verify Take Back/cancel/disconnect/expiry on real sites and pending actions.
  Clearly distinguish stopping agent access from stopping ordinary website
  background requests; already dispatched site effects may still finish.
- Keep native external-agent host powers under that agent's own permission model.
  Steed worker isolation is a separate execution path, not a restriction on what
  an independent Codex/Claude agent may do with its other tools.
- Simplify first-run setup and missing-dependency diagnostics. Record end-to-end
  native Codex and another independent local MCP client on supported desktops;
  show connected, waiting, working, disconnected and interrupted states clearly.
- Validate web-chat sign-in, composer handoff and sidebar sizing on real providers.
  Copy fallback is required when a website changes. Do not claim automatic reply
  capture or cloud MCP control from an ordinary web-chat tab.
- Add SECURITY.md, a private vulnerability reporting route and response process.

Acceptance: external review or a recorded structured security assessment, no
known access-boundary defects, successful user onboarding, and accurate UI claims.
Experimental capabilities can be excluded from beta rather than overstated.

### 7. Connectors and generated attachments: qualify what is shipped

- Resolve PR #2 review follow-ups, integrate reviewed starter data and record live
  connection/read/create/revoke/expiry/error checks using test accounts. Synthetic
  wire tests remain necessary but cannot establish real provider acceptance.
- Preserve exact write previews and uncertain outcomes without automatic replay;
  describe GitLab quick actions and each provider's service-side interpretation.
- Keep Google production registration deferred as requested. Before advertising
  public one-click Google connection, complete shared app ownership, consent,
  scopes, provider review as applicable, sign-in/refresh/revoke and support.
  A beta can label Google as user-configured/experimental instead.
- Test attachment generation/export with a clean worker install, missing
  Bubblewrap/namespace restrictions, unsupported wheel/platform, cancellation,
  quota errors and representative PDFs/XLSX/images. Validate outputs and limits;
  never fall back to unsandboxed execution when capability checks fail.
- Provide one consistent user flow for selecting inputs, approving a generated
  package, reviewing results, exporting and removing retained materials.

Acceptance: every advertised connector/worker bundle has a defined supported
subset and real acceptance evidence, or is visibly excluded/experimental.

### 8. Documentation, performance and release operations

- Replace remaining “Custom Browser” branding in product surfaces, add an About
  screen/version/build identity, concise onboarding and accessible permission UI.
- Measure startup, idle CPU/memory, large histories/tab collections, page/DOM
  inspection, screenshots, filter updates and generated-file workloads. Set
  release budgets from actual supported hardware rather than inventing numbers.
- Publish installation, troubleshooting, privacy/security, known limitations,
  change log and recovery instructions. Keep README short and developer detail
  in the documentation. Provide issue templates and reproducible diagnostics.
- Run a small external beta; triage failures, designate blockers, freeze a
  release candidate, test upgrades from it and publish a tagged release with
  support/contact channels. Review feedback before widening the supported matrix.

Acceptance: users can install and diagnose a version without the developer, and
maintainers can reproduce, fix and distribute an urgent problem.

## Additional work before a stable daily-browser release

1. **Durability:** repeatable crash/session recovery, upgrade migrations and
   backups; long-running real-user testing across the supported graphics matrix.
2. **Everyday completeness:** bookmarks and import/export, external URL handling,
   default-browser flow and usable browser-data migration. Decide which print/PDF,
   per-site zoom, broader accessibility and other daily workflows are supported.
3. **Passwords:** finish real-site acceptance of the built-in vault and reliable failure feedback.
   Advanced save/update, frames, TOTP/passkeys remain deferred; either implement
   required stable workflows later or publish a clear supported subset. Generic
   extensions/Bitwarden should remain unadvertised until their engine crash and
   lifecycle compatibility are resolved.
4. **Agent reliability:** reconnect/resume without duplicate actions, better
   multiwindow feedback, task/provider errors, recovery and useful bounded page
   observations on modern SPAs. Test collaboration on realistic signed-in tasks.
5. **Account UX:** common connector sign-in and revocation should be simple enough
   for intended users; provider policies, registration and support ownership must
   be settled for the integrations advertised as default.
6. **Maintenance evidence:** dependable security releases, supported version
   lifecycle, compatibility regressions and tested rollback/upgrade practices.

Sync, a browser-engine fork, Windows/macOS ports and universal extensions are
separate projects, not prerequisites for an explicitly Linux stable release.

## Work toward the full agent-collaboration vision

- **Cloud personal agents:** paired outbound bridge or provider tunnel, remote
  authentication/client identity, per-task binding, revocation, reconnect and
  consent UX. Prototype with one provider and verify others through the same MCP
  contract. Steed's local HTTP transport currently rejects website-origin calls.
  A hosted relay introduces operating costs and a new data path; do not choose
  one or expose loopback authority publicly without an explicit product decision.
- **Better website understanding:** accessibility semantics, independently scoped
  frame reads, open shadow trees, custom/virtualized widgets, pointer gestures,
  native file dialogs and richer action evidence. Combine deterministic coverage
  signals with DOM and viewport reads. Add a lightweight routing/ranking model
  only after representative tasks demonstrate value. No observer can guarantee
  all important information, especially hidden state, closed roots, offscreen
  content or third-party frames. Expose omissions and uncertainty to the agent.
- **Connectors equal to maintained integrations:** reviewed static headers,
  auth schemes, useful identity shapes, paging, application-level errors, OAuth
  lifecycle, typed inputs, payloads, service semantics and reconciliation. The
  21-family assessment in PR #2 identifies concrete gates; packaging a recipe
  alone cannot provide missing interpreter capability. Runtime work and connector
  data work retain separate ownership to avoid contributor conflicts.
- **Agent-designed customizations:** versioned install/enable/disable/remove,
  rollback, UI extension contracts, packaging/dependency policy, reusable worker
  environments and validated capabilities. Current recipes/local Python outputs
  are groundwork, not arbitrary hot-patching of Steed or unrestricted plugins.
- **Better conversations:** additional external runtime adapters, proactive
  delivery where supported, task continuity, links/files/rich results and
  collaborative UI. Generic MCP mailboxes currently require client participation;
  they do not independently wake an idle personal agent.

These can follow beta in measured milestones. Bring them forward if the release
promise requires them. A rebuild is not currently justified by these gaps; keep
Qt/browser ownership, task policy and agent/provider boundaries explicit while
extracting UI responsibilities from the growing BrowserWindow coordinator.

## Proposed sequence and decisions to settle

1. Finish chat acceptance and publish this release inventory; complete the
   connector review without overlapping the contributor's paths.
2. Add CI/security reporting, investigate startup reliability, audit private
   storage and complete browser/agent desktop acceptance.
3. Choose licensing and one initial supported packaging target; build and test
   clean install, upgrade, engine updates and rollback.
4. Qualify advertised connectors/workers, finish onboarding/branding and run a
   small beta. Expand only after the measured release gates pass.
5. Pursue cloud-agent pairing, observation breadth and customization as separate
   milestones toward stable release and the larger vision.

Owner decisions needed: **license/source distribution**, **first supported
platform/package channel**, and **whether cloud website-agent control is required
for the first public release**. Recommended product scope is a Linux beta with
local/native agent control and ordinary website chat, followed by a paired
outbound cloud bridge. Keep shared Google registration and password improvements
deferred until explicitly resumed. No major infrastructure migration is proposed.
