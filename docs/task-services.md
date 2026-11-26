# Agent task services

How agent tasks, permissions, approvals, files, workers and app connections work inside Steed. [Documentation index](README.md).

## Internal task services

Ordinary tabs and task pages use `services/navigation_service.py` for
navigation and `services/permission_service.py` for permission wiring and
lifetime checks. Browser prompts stay in the UI; task pages deny permissions.
The application-owned `DownloadManager` receives a destination-picker callback
from the registry instead of opening dialogs itself. A manager with no provider
declines downloads. Task downloads have their own explicit destination grants,
staging, cancellation, and receipts; they never invoke the ordinary file picker.
Single-file task uploads require exact source-file and upload-site approval.

The task service is available to trusted in-process Python controllers through
`window.profile_manager.registry.tasks`. It is initialized only on demand and
shared across windows using the same application registry. This is a developer
API; the optional [local MCP adapter](agents.md#local-agent-access-mcp) exposes a bounded
subset through private IPC. **Agents → Task Permissions**
provides user approval, denial, revocation, and cancellation. A client can propose
a scope but cannot approve it through the command broker.

### Remembering agent approvals

Task popups show the requested sites, actions, profile, app account/tool/effect
and destination, files, duration and budgets. Choose **Allow once** for this task,
**Allow every time** for all profiles (including private tasks), or **Allow every
time on this profile** for one existing named profile. Profile-specific choices
are unavailable for private task profiles. A saved approval can be used by any
connected agent; enabling local agent access is still required after startup.

A future request is approved automatically only when one saved rule covers its
whole scope: sites, actions, files, upload sites and exact app account/tool/effect/
destination must be subsets; duration and budgets cannot increase, and private
network access cannot be added. Separate rules are never combined. Each matching
task gets a fresh bounded grant and its own duration/budgets; rules are not an
aggregate lifetime spending limit. Expanded requests still show a popup.
If an automatic approval fails, for example because the profile cannot be
reserved, the request stays pending for review and the journal records
`permission.auto_approval_failed`; other tasks keep running.

Manage rules in **Agents → Task Permissions → Saved Approvals**. **Forget and Revoke
Access** removes the rule and revokes all active tasks approved through it.
Revoking one task leaves its saved rule intact. Disconnecting an app account
removes matching rules and revokes matching grants; reconnecting creates a new
account identity requiring fresh approval. Profile rules follow renames, but not
deletion/recreation under the same name. Saved lists retain their original labels.

Rules and their grant provenance are journaled atomically in `tasks.sqlite`
(schema 8). Restart preserves rules but interrupts tasks and never restores old
grants or retries operations. Approval management remains trusted browser UI;
agent tools cannot create, broaden or remove rules. Website choices are separate.

### Internal command API

`service.broker.submit(name, **arguments)` returns a `concurrent.futures.Future`.
Worker threads can submit commands; handlers and engine operations execute on
the Qt application thread. Never block that thread on `future.result()` before
completion. Use callbacks or wait from a worker while Qt's event loop runs.
Deferred handlers are flattened into that same Future; inspection completes
after the engine reply rather than returning a nested Future. Broker shutdown
rejects running deferred replies and cancels undispatched commands.
Cancellation discards queued work; stopping an already-dispatched navigation is
a separate command.

The application controller creates a task, then calls `service.client(task_id)`
on the Qt thread to issue a task-bound handle. `client.submit(name, **arguments)`
supplies the task ID automatically and rejects impersonation and management
commands. Give clients these handles; reserve the raw `service.broker` for trusted
application management. Neither interface is a sandbox for arbitrary Python
running inside the application. The local gateway authenticates adapter connections
and binds each to one task. Each HTTP MCP session has its own such connection;
its transport identity is also bound to the credential that initialized it.

| Command | Arguments / result |
| --- | --- |
| `task.create` | `objective`; returns a task record |
| `task.status`, `task.list` | `task_id` for status; records for list |
| `task.capabilities` | `task_id`; backend/engine versions, supported actions, and website-script availability |
| `task.events` | `task_id`, optional `after` sequence and `limit`; ordered events |
| `task.finish` | `task_id`, optional terminal `status`; closes owned sessions |
| `task.recover` | `task_id`; asks the user to select a finished/interrupted source and drafts before grants or work; returns recovery status |
| `task.recovery` | `task_id`; status and only the historical context explicitly shared by the user; does not restore authority |
| `artifact.store` | `task_id`, `title`, `content`, optional `format` and `source_events`; explicit `artifact` permission, returns immutable draft metadata |
| `artifact.list`, `artifact.read` | `task_id`, plus `artifact_id` for read; artifact permission, returns owned metadata or text |
| `worker.run` | `task_id`, `extension_id`, stable `request_id`; worker permission, requests trusted exact-package approval before isolated execution |
| `worker.capabilities` | `task_id`; fixed background sandbox self-check, cached availability/limits; no grant or task-data access |
| `worker.status`, `worker.list` | `task_id`, plus `request_id` for status; current worker permission, returns owned execution receipts |
| `worker.cancel` | `task_id`, `request_id`; stops an owned worker without spending a cleanup operation |
| `app.list_tools` | `task_id`; configured tool descriptions/effects, no grant or credentials |
| `app.call_tool` | `task_id`, `connector`, `tool`, `request_id`, `destination`, optional `artifact_id` and typed `inputs`; exact app permission, starts a call and returns its durable receipt |
| `app.status`, `app.cancel`, `app.reconcile` | `task_id`, `request_id`; owned receipt lookup, cooperative cancellation or read-only reconciliation |
| `grant.request` | `task_id`, `scope`; records a pending request, without authority |
| `grant.list` | `task_id`; task grant records, including decisions |
| `session.open` | `task_id`, optional `profile="private"`; requires approval, returns a session snapshot |
| `session.navigate` | `task_id`, `session_id`, `url`; dispatches GET navigation |
| `session.inspect` | `task_id`, `session_id`; URL/title/load metadata and `control_owner` (`agent` or `user`) |
| `page.inspect` | `task_id`, `session_id`; explicit `read` grant, returns a structured page snapshot |
| `page.target` | `task_id`, `session_id`, `snapshot_id`, `target_id`; validates an observed target and returns its description |
| `page.navigate` | `task_id`, `session_id`, `snapshot_id`, `target_id`; follows a validated link and reports the load outcome |
| `page.act` | `task_id`, `session_id`, `snapshot_id`, `target_id`, `operation`, optional `text`/`index`; requires `read` + `edit`, returns a control-change receipt |
| `page.submit` | `task_id`, `session_id`, `snapshot_id`, `target_id`; requires `read` + `navigate` + `submit`, returns an observed native-form outcome |
| `page.scroll` | `task_id`, `session_id`, `snapshot_id`, optional `frame_id`, `dx`, `dy`; requires `read` + `scroll`, returns observed offsets |
| `page.wait` | `task_id`, `session_id`, optional `condition`, `text`, `timeout_ms`; requires `read`, returns an observed match, timeout, or load failure |
| `page.download` | `task_id`, `session_id`, `snapshot_id`, `target_id`, `destination`; requires `read` + `navigate` + `download`, starts an observed attachment link and returns its download ID |
| `page.upload` | `task_id`, `session_id`, `snapshot_id`, `target_id`, `source`; requires `read` + `upload`, binds an approved source copy to an observed single-file input and returns metadata |
| `download.status` | `task_id`, `session_id`, `download_id`; download grant, returns progress/completion receipt without file bytes |
| `download.cancel` | `task_id`, `session_id`, `download_id`; cancels owned transfer and discards staged bytes without spending a cleanup operation |
| `session.stop`, `session.close` | `task_id`, `session_id`; stops or destroys the session |
| `session.attach` | `task_id`, `session_id`; internal read-only Qt view of the existing page |
| `session.watch` | `task_id`, `session_id`; opens the same-page watch window; returns JSON watch/control state |

An active task can call `client.submit("task.capabilities")` before requesting a
grant. This read-only query opens no page, consumes no operation budget, and
issues no authority. It reports `supported_actions`, `website_scripts`,
`script_unavailable_reason`, and engine versions. Queries retain the client's
task identity and reject inactive tasks. Alternate backends must provide their
own report; a missing implementation returns an error rather than claiming Qt
capabilities.

The Qt report currently returns `website_scripts: false`. Chromium below 152
reports `engine_upgrade_required`; a verified newer version reports
`native_policy_not_integrated`. An unrecognized version reports
`engine_version_unverified`. `native_policy_candidate` describes version
eligibility only: upgrading the engine never enables scripts or adds a grant.

For example, submit `task.create` with an objective, obtain its `task_id`, issue a
bound client, and use that client to submit `grant.request` with this scope:

```json
{
  "origins": ["https://example.org"],
  "actions": ["navigate", "inspect", "attach"],
  "profile": "private",
  "allow_private_network": true,
  "duration_seconds": 600,
  "max_operations": 100,
  "max_requests": 1000,
  "max_sessions": 1
}
```

A nonmodal **Allow task access?** popup opens automatically in a browser window.
Review the task, profile, sites, actions, and duration, then click **Approve** or
**Deny**. Requests queue one at a time. Closing/Escape leaves a request pending;
review it later in **Agents → Task Permissions**, which also provides revocation.
Requests made before a window opens wait for one. A client only submits
`grant.request`; approval is never available through its command handle.
Then submit `session.open` through the client. Use the returned `session_id` for
navigation and inspection; omit `task_id` when using the bound handle.
Navigation dispatch is not load completion: use `page.wait` with `condition="ready"`
under a read grant, or wait for metadata with `loading=False` and check
`load_success`. `tests/test_task_services.py` demonstrates
the complete workflow with a real local HTTP fixture, without AI or an account.

By default, each session owns a separate off-the-record profile. Its page survives closing
an attached view, and a new view attaches to the same page. Task completion or
the last browser window closing disposes pages before their profiles. File transfers
require the scoped download/upload operations described below. Site permissions,
document scripts, new workers, WebSockets, external URL
schemes, and the built-in PDF viewer are unavailable in current task pages.
Ordinary browser tabs retain their existing settings. Task browsing currently
supports document retrieval with GET/HEAD and explicitly authorized native form
submission. Even GET/HEAD can cause website effects; a grant does not establish
that a site is read-only. POST is allowed only through a bound, one-use form ticket.

Scopes match exact HTTP/HTTPS origins, including scheme and port; subdomains and
redirect destinations need their own entries. Credentials, wildcards, and origin
paths are rejected. `about:blank` is allowed without adding an origin.
Qt 6.11 can follow a subresource or frame redirect before its request
interceptor's block takes effect, and DevTools Fetch can neither hold nor
rewrite those requests. Restricted pages therefore also get a Content Security
Policy listing only the scope origins, added from an application-world script
as each document's head is created; Blink checks it before following every
redirect hop. Restricted pages run no page JavaScript that could remove it.
Scopes containing an IPv6 literal cannot be expressed in CSP and rely on the
interceptor alone. Live delegated tabs are unchanged. Exact file
destinations and upload sources/sites have separate permissions. Supported actions are
`navigate`, metadata `inspect`, content `read`, view `attach`, control `edit`,
`scroll`, form `submit`, `download`, `upload`, retained-text `artifact` and scoped
`app`. The default action list remains
`navigate`, `inspect`, and `attach`; content, edits, scrolling, and submission need
explicit approval.
An edit grant does not authorize submission, POST, active scripts, or file actions.

### Read-only page inspection

Add `"read"` to a proposed grant's `actions` before requesting approval. The
approval screen discloses that readable text and control labels will be shared
with the task client, including on signed-in pages. Existing metadata-only grants
cannot call `page.inspect`, `page.target`, `page.navigate`, `page.act`, `page.submit`, `page.scroll`,
or `page.wait`. No cloud provider receives this data
through a built-in classification/search provider. Local MCP clients receive
approved reads; their external model/provider use is outside browser enforcement.

Agents get snapshots in a compact form by default (`detail: "compact"`): each
frame's text and outline lines (`# Heading`, `table "Prices" [Item | Cost] 4
rows`), and its targets as one text line each, `[target_id] role "label" ->
link {operations} (states and flags) !constraints form: METHOD action --
help error: message`, with empty fields left out and same-site links
shortened. On busy real pages that list was 3–9 times smaller than the full
form, whose seventeen named fields per target are still there with
`detail: "full"`. Steed's own callers (the watch window, tests) always get the
full form; the compact one is made where results leave for agents
(`services/task_client.py`, `services/page_compact.py`).
Browser-managed provider disclosure remains future work.

After successful loading, call `page.inspect` through the task-bound client. It
returns a `PageSnapshot` with a unique snapshot ID, session/task IDs, URL/title,
navigation revision, source (`rendered_dom`), frame snapshots, and limitations.
Each readable frame contains bounded text and target descriptions: opaque target
ID, frame ID, kind/role, label, enabled/read-only state, link URL when supported,
candidate operations, and constraints. Candidate operations describe controls;
they do not authorize or execute actions. Supported native submit buttons report
`submit`, a query/fragment-free `form_action`, and `form_method`; custom controls,
unsupported forms, specialized input types, and multiple selection report constraints.
Option labels and values remain omitted; current selection uses an index.

Each frame also has an `outline`: visible page structure in document order, so
an agent can orient without reading all text. Entries are headings (with level),
landmarks (banner, navigation, main, complementary, contentinfo, search, and
named regions/forms), images with alt text or labels, tables (caption, up to 12
header columns and a row count), lists (ordered flag and item count), open
dialogs, alerts and status/live regions, `<details>` disclosures (summary and
open state) and tab lists. Labels come from author-provided names or bounded
visible text; field values and editable text are never included. At most 150
entries and 12,000 characters are returned per snapshot (`outline_truncated`),
and images without any text alternative add `unlabelled_images_present`.
Targets report ARIA states as `states`: `expanded`/`collapsed`, `pressed`,
`selected`, `current`, `checked`/`unchecked`/`mixed` (from `aria-checked`),
`has_popup` and `busy` (the control or a container is `aria-busy`). A target's
`role` is its explicit ARIA role, or else the role its native element exposes:
`link`, `button`, `textbox`, `searchbox`, `combobox` (single selects and inputs
with a suggestion list), `listbox` (multiple selects), `spinbutton`, `slider`,
`checkbox` or `radio`. Custom sliders and spin buttons report their
`aria-valuetext` or `aria-valuenow` as `value_text`; native control values stay
omitted. Custom textboxes, listboxes and grid cells are described but have no
DOM action (`custom_control_unsupported`); tree items and comboboxes join the
pointer-activated roles below (activate a combobox to open it, inspect again,
then activate its option).

In live sessions, a focusable custom slider or spin button reports `adjust`
with the `keyboard_adjustment` constraint. `page.act` with `operation: "adjust"`
and `steps` (−100 to 100, not 0; positive increases) focuses it and presses
ArrowRight/ArrowLeft (sliders) or ArrowUp/ArrowDown (spin buttons) that many
times through the same native keyboard path as checkboxes, checking focus
between batches. The receipt reports the widget's resulting `value_text` and
`applied` or `no_change`; the website decides the step size and limits. It needs
edit permission. During the adjustment the control's own markup may change, so
only its identity and focus are rechecked.

Frames from another origin cannot be read by the main document's script. When
such a frame's origin is in the task's scope, Steed runs the same read inside
that frame and returns its text, outline and scroll position. In restricted
sessions the frame is read-only (`cross_origin_frame_read_only`, no targets).
In live sessions it reports `cross_origin_frame` and its controls, each with the
`cross_origin_frame` constraint, and they work like main-page controls: text
entry, checkboxes, selects, buttons, custom widgets, sliders, hover and frame
scrolling (`page.scroll` with the frame's `frame_id`). Checks and actions run
inside that frame. Form submission and uploads stay main-document only
(`frame_form_unsupported`). Pointer actions (custom widgets, hover) are measured
inside the frame and mapped through its frame element, which must itself be the
topmost element at that point on the page; they are available only for frames
directly inside the page (`nested_frame_pointer_unavailable` otherwise).
Keyboard-based actions work at any depth. Frames outside the scope, sandboxed
frames and frames that do not answer within five seconds stay unread
(`frame_inaccessible_or_not_ready`).

Each frame also reports `scroll`: its scroll position, scrollable width/height
and viewport size in CSS pixels. When content continues below the viewport the
snapshot adds `more_content_below`. To read infinite feeds and lazily loaded or
virtualized lists, scroll the frame (`page.scroll`), call `page.wait` with
`condition: "settled"` — it reads the page every 250 ms and reports `matched`
once text length, controls and scrollable height stay unchanged for 750 ms (or
`timed_out`) — and inspect again. Restricted pages run no website scripts, so
script-driven lazy loading needs a live session.

Many web apps scroll an inner panel — a message list, sidebar or dialog body —
rather than the page. Give `page.scroll` a `target_id` (any control inside that
panel) instead of a `frame_id`: Steed scrolls the nearest scrollable container
around the target and reports its `scroll_position` and `container_size`
(scroll width and height, visible width and height). When only the page itself
scrolls, the result is `scroll_container_unavailable`; scroll the frame instead.

In live sessions, `page.act` with `operation: "hover"` moves the pointer over any
visible target without clicking, to open menus and tooltips that appear on
mouse-over; inspect again to see what appeared. Hover uses the same
visible/in-view/unobscured/stable checks as pointer activation and needs edit
permission. Restricted sessions return `live_session_required`.

`operation: "drag"` with `to_target_id` presses on the target, moves to the
other target in ten steps and releases there — for reordering lists, moving
cards between board columns or dragging handles. Both must be visible,
unobscured main-page targets from the same snapshot; each point is measured
twice about 60 ms apart and must not move. Pointer-event drag libraries and
native HTML5 drag-and-drop between page elements both work. Dropping files from
the computer is not supported. Drag needs submit permission, since moving items
can change data; inspect again to confirm the result.

For effects that arrive after an action — search results, validation messages,
a list that loads — call `page.wait` with `condition: "changed"`. It compares
reads every 150 ms with the agent's last `page.inspect` of the same page (or,
without one, with its own first read) and reports `matched` once the visible
text, any control's role, label, enabled state, states or value text, or the
outline differs, or once the page navigates. A change that landed before the
wait started therefore still matches.

In live sessions (shared tabs or live private pages), custom widgets in the main
document with an ARIA role of button, link, tab, menuitem (including checkbox and
radio items), switch, checkbox, radio, option or treeitem report `activate` with the
`pointer_activation` constraint. Activation needs `submit` permission, like other
live button activation, and is a single left click at the element's centre.
Before clicking, the page checks twice, about 60 ms apart, that the element is
enabled, visible, not inert, inside the viewport (after scrolling it into view)
and the topmost element at that point, including through open shadow roots; a
covered, off-screen or moving target fails with `target_obscured`,
`target_outside_viewport` or `target_moved` and nothing is clicked. Agents never
supply coordinates. Restricted pages and frames keep these
widgets observation-only.

Targets also expose `description`, `required`, `invalid`, and `error_message`.
Descriptions prefer visible `aria-describedby` references, then `aria-description`,
then a title differing from the label. Native form controls' `required` attribute
and `aria-required` contribute required markers. `invalid` reports author-declared
`aria-invalid`; it does not run browser validation or inspect current field values.
When invalid is declared, visible `aria-errormessage` references supply the error
hint. See [W3C guidance on invalid fields and associated messages](https://www.w3.org/WAI/WCAG22/Techniques/aria/ARIA21).
An error also associated through `aria-describedby` stays in the description;
agents should read both fields rather than treating absence of one as success.

Labels, descriptions and errors are bounded to 256 characters each and 16,000
characters combined across all targets. `target_text_truncated` signals omitted
target text; controls retain opaque IDs and constraints. This also keeps large
Unicode observations within the MCP response budget. References use at most eight
IDs per attribute and resolve within the same document or open shadow root. Hidden/editable ancestors and current field values remain
excluded, including when referenced by a visible field. This intentionally omits
hidden accessible descriptions and is not the browser's complete accessibility
name/description algorithm. `accessibility_tree_not_inspected` and
`hidden_accessible_references_omitted` make these coverage limits explicit.

Live references include associated label/help/error content and ancestor attributes
in their freshness fingerprint. A changed hint, error or visibility container
requires a new inspection; unrelated page updates remain permitted. Fingerprints
are bounded to 65,536 characters and 128 ancestor levels. A live target whose
context exceeds the bound has no candidate operations and reports
`live_target_context_limit`; it cannot be acted on through that stale reference.
Reference reading shares the overall node budget, with exceptionally deep ancestry
reported as `accessible_reference_depth_limit`. These fields add observation only;
they grant no new action, file, history or profile permission.

Pass a snapshot ID and its target ID to `page.target` to validate that reference.
Only the most recent inspection for a session is retained. Navigation, frame URL
or document changes, observed DOM mutations, removed/replaced nodes, and a newer inspection
invalidate old references. The navigation revision alone is not a DOM revision;
the snapshot ID and browser-side document/node checks enforce freshness.
Unknown targets and cross-task/session references fail. Every completed edit or
scroll attempt also invalidates references, including changes to value/checked
properties that do not produce DOM mutations. File actions remain pending.

Inspection uses fixed browser code in Qt's
[isolated application world](https://doc.qt.io/qt-6.11/qwebenginescript.html#ScriptWorldId-enum).
Clients cannot supply scripts or selectors. Restricted sessions keep document JavaScript disabled;
the reader changes no DOM content. It omits input/select/textarea values, editable
text, and hidden/script/style content. These omissions do not classify arbitrary
visible page text as safe: site text and labels remain untrusted data.

Results contain at most 32,000 text characters, 200 targets, and 16 frames, with a
12,000-node traversal budget and a five-second engine timeout. Labels and link
URLs are bounded too; links over 2,048 characters cannot be navigated rather than
executing a truncated destination. Same-origin frames and readable `srcdoc` frames are
inspected. Cross-origin/sandboxed or unready frames report
`frame_inaccessible_or_not_ready`. Open shadow roots contribute bounded text and
control names, including image alt descriptions. Up to 32 roots at eight levels
are inspected within the existing node/text/target budgets; exceeding them reports
`shadow_root_limit`. Closed roots and canvas pixels remain uninspected. Slot/light
DOM ordering is approximate and reported when slots are observed. Shadow controls
are observation-only and report `shadow_control_actions_unavailable`; inspect a
fresh snapshot after changes. Mutations inside inspected roots invalidate
restricted snapshots, and live targets recheck their own names and host ancestry.
Truncation and unsupported content are reported, not silently presented as a
complete page.

Visible rich-text editor boundaries now appear as `kind="editable"` with labels,
help/error metadata and `values_omitted`. Their text and nested controls stay out
of observations, including in open shadow roots. On a normal live page, an explicit
main-frame document-root `contenteditable` editor supports `page.act(operation="enter")` with
read/edit permission. Chromium replaces its selected contents as literal text;
multiline text, trusted input events and native undo are supported. The receipt
contains only changed/text-match facts. Existing formatting may be retained by the
browser. Live website input handlers may change data; Enter/Send is not pressed.
Readonly/disabled controls, redirected focus and changed targets are rejected.
Restricted, child-frame and shadow-root editors remain observation-only. Missing or
partly unsupported controls produce screenshot/computer-input guidance without
claiming the entire page was understood.
Static `contenteditable="false"` wrappers retain normal text/control discovery.
Their noneditable islands inside a real editor remain omitted with that editor,
including when another control references them as help text.

The read permission and live grant are checked at dispatch and before returning
content. Revocation, expiry, navigation, stop, renderer termination, supersession,
and session shutdown reject interrupted reads; late callbacks cannot revive them.
An inspection event records counts, identifiers, revision, and limitations before
content is returned. Page text, labels, form values, and full snapshots are not
written to the task journal. A failed evidence write rejects the result and
disables task commands.

### Target-based link navigation

After `page.inspect`, follow an observed link using its opaque identifiers:

```python
client.submit(
    "page.navigate",
    session_id=session_id,
    snapshot_id=snapshot.snapshot_id,
    target_id=link.target_id,
)
```

Both `read` and `navigate` must be approved. The browser revalidates document/node
identity and enabled state, checks the exact destination origin, and records the
request before issuing GET navigation in the task's main page. It does not click
site handlers, submit forms, or execute caller scripts. Links inside readable
frames can be followed, but their destination opens in the main task page.

The future returns `NavigationResult` with session ID, actual URL, revision, and
`status`: `loaded`, `same_document`, `load_failed`, or `timed_out`. Fragment jumps
report `same_document` after observing the destination without reloading the
document. A blocked redirect is a failed
load. Timeout stops the load and is not proof of absent website effects. Stop,
superseding navigation, renderer termination, revocation, expiry, or close reject
interrupted operations. New navigation invalidates old target references.
Request and outcome evidence must be saved before dispatch and return respectively;
failed journal writes disable task commands. Labels and returned page text remain
outside the journal. Loaded means document load completion, not application-level
success; scripts and full SPA/form workflows remain unsupported.

### Controlled edits and scrolling

Request `"read"` and `"edit"` to change supported controls; request `"read"` and
`"scroll"` to scroll. The approval popup and management dialog disclose these
actions. Existing grants keep their original authority. Example control entry:

```python
client.submit(
    "page.act",
    session_id=session_id,
    snapshot_id=snapshot.snapshot_id,
    target_id=field.target_id,
    operation="enter",
    text="Example text",
)
```

Use a fresh `page.inspect` after each completed attempt. The browser checks node
identity, document/frame state, visibility, enabled/read-only state, and live
authority before dispatch and receipt. Supported operations:

| Operation | Supported controls and input | Semantics |
| --- | --- | --- |
| `enter` | Text, search, email, URL, telephone inputs and textareas; `text` only, up to 4,096 UTF-16 units | Focus/select the resolved control, then Chromium's editing command; trusted input events, undo, and field length limits are verified on Qt 6.11.2 |
| `activate` | Checkbox/radio inputs; no additional input | Focus the resolved control and send native Space down/up; browser-generated keyboard/click/input/change events are trusted |
| `select` | Single-selection native selects; `index` from 0 to 999 | Native Home/ArrowDown keys skip unavailable choices; observe the actual index; absent/disabled/hidden options and multiple selection fail |

The text editor uses the deprecated `document.execCommand` API through fixed
browser code, verified on the pinned engine. See Chromium's
[editing implementation](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/editing/commands/editor_command.cc)
and the [HTML activation specification](https://html.spec.whatwg.org/multipage/interaction.html#activation-behavior).
No arbitrary commands, scripts, selectors, or protocol operations are exposed.
Checkbox/radio and selection receipts use `input_semantics="chromium_keyboard"`.
Fixed [`Input.dispatchKeyEvent`](https://chromedevtools.github.io/devtools-protocol/1-3/Input/#method-dispatchKeyEvent)
commands run through an in-process Qt frontend with separate off-the-record
storage. Shared-profile pages reuse their worker-bypass adapter; private pages
initialize it on first native control use. No public debug listener is opened.
Task-only focus emulation starts before focusing the resolved control and ends
after observation. It does not activate a desktop window or focus another tab.
An unattached page with a zero-sized viewport gets a fixed 1280×800 virtual
viewport, retained for the task page; a page with an existing viewport keeps it.
This changes the task's layout without creating a visible window. Readable child
frames use the focused document. Pointer gestures and custom widgets remain pending.

Selection uses at most 32 keys per batch and rechecks the target, focus, document,
and live grant between batches. It can generate events for intermediate choices;
an unchanged selection sends no selection keys. The complete native action is
bounded to ten seconds, including adapter setup; individual input batches have
five-second deadlines. Protocol acknowledgement alone is insufficient: the browser
observes checked/selected state before returning. A mismatch returns `unsupported`;
adapter loss, stale state, focus drift, timeout, and cancellation reject the action.
There is no synthetic-click/selection fallback or automatic retry.

These semantics do not establish compatibility with JS handlers, custom widgets,
or SPA workflows. Password/file fields, specialized inputs, generic buttons,
and contenteditable controls remain unavailable to restricted-page `page.act`.
Normal live-page rich-text entry is described above. Scriptless edits
change the task page's DOM; they do not submit the form or update another tab's
controls, even when those tabs share the profile.

`PageActionResult` reports action/session/reference IDs, operation, revision,
observed `changed` state, input semantics, and `status`: `applied`, `no_change`,
`constrained`, or `unsupported`. It can include checked state, selected index,
text-match confirmation, or scroll offsets; it never includes field text or
option values. Length limits or engine sanitization can return `constrained`;
unsupported editing can return `unsupported`, rather than claiming success.

To scroll a snapshot's main frame, submit `page.scroll` with `snapshot_id` and
`dy=400`; optional `frame_id` selects an inspected readable frame. `dx`/`dy` are
integer CSS-pixel offsets bounded to ±10,000. Fixed CSSOM scrolling returns the
observed position and whether it changed; an edge can return `no_change`.
Network requests caused by scrolling still use the original scope and budget.

Only one controlled action runs per session. Stop, superseding navigation,
renderer termination, close, revocation, expiry, or timeout reject interrupted
receipts. A request gets a journaled action ID before dispatch, and the outcome
must be saved before returning it. Entered text and option values never enter
these events. Already dispatched work may have happened despite an interrupted
receipt; inspect state before retrying. No rollback is promised.

### Explicit form submission

Request `"read"`, `"navigate"`, and `"submit"` before using `page.submit`.
The ordinary Approve/Deny popup explains that submission can change account data
and send current fields, including hidden site tokens. `edit` alone never grants
submission; `submit` alone never grants reading or editing. Existing grants retain
their original actions. The form destination must belong to the approved origins.

Inspect the loaded page, choose a target whose operations include `submit`, then:

```python
client.submit(
    "page.submit",
    session_id=session_id,
    snapshot_id=snapshot.snapshot_id,
    target_id=submit_button.target_id,
)
```

Steed resolves the browser-issued target, rechecks its document and live form,
then uses native [`requestSubmit`](https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#dom-form-requestsubmit).
Required-field validation and the chosen button's action, method, target,
encoding, and validation overrides apply. Native serialization includes successful
controls, hidden tokens, and the chosen submit button; field values are not returned
by inspection. Controls named `action`, `method`, or `checkValidity` cannot shadow
the browser's native form checks. Every completed attempt invalidates references.

Current support is main-document, same-window HTTP/HTTPS GET or POST with
`application/x-www-form-urlencoded` encoding and at most 200 native form controls.
Approved single-file uploads additionally support POST `multipart/form-data`.
Forms containing passwords, multiple/folder file inputs, child-frame forms,
plain-text forms, dialog methods, image submitters, and new-window/named targets
are unavailable. Multipart forms require the explicit upload grant described below.
Document scripts remain disabled, so script-handled forms and SPAs need later work.
Submission checks this setting before preparation and again before arming its
ticket. An enabled setting returns `scripted_submission_unavailable` without
dispatch: JavaScript submit handlers could change sensitive controls after DOM
preflight, and a route/method ticket does not bind the serialized body.
This does not add password-manager integration to tasks.

One per-page ticket binds the destination and method to a dispatch. The interceptor
checks main-frame resource type, grant lifetime, origin, and request budget; it never
exports the request body. General POST requests are denied. Main-frame redirects
after an authorized POST submission are not followed: Qt can apply an interceptor
block only after a 307/308 redirect has already resent the body, and the
navigation gate, which runs before the redirect, cannot tell 303 from 307. The
result reports `redirect_not_followed`; navigate to the destination separately.
GET form submissions keep ordinary redirect origin and budget checks. Qt retains its
form navigation classification on those redirects; the separate navigation gate
rejects unsolicited second form submissions.
User tabs on the same profile retain their ordinary submission behavior.

`FormSubmissionResult` includes action/session/target IDs, revision, destination
origin, `request_authorized`, and status: `validation_failed`, `loaded`, `load_failed`,
`redirect_not_followed`, or `timed_out`. Authorization means the network gate allowed a request, not that the
server received it. `site_effects` remains `unknown`: a loaded document does not
confirm an account change, and a failed/interrupted receipt does not prove nothing
happened. Stop, expiry, revocation, renderer failure, and journal failure interrupt
pending work. There is no automatic retry or rollback.

Requests commit their action ID before dispatch; receipts commit before disclosure.
Journal URL evidence now stores origins only and omits page titles, since result
paths, queries, fragments, and titles can reflect submitted values. Submission
receipts also expose origins only. Authorized live inspection and normal navigation/
wait receipts still return actual URLs and page content; sites may echo submitted
values there. Form values, hidden tokens, and request bodies are never copied into
action events. Existing journal records are not rewritten.

### Bounded state waits

`page.wait` needs `read`, spends one operation, and supports two conditions:

- `ready` (default): wait for current loading to finish; returns `matched` or
  `load_failed`. It reports the actual URL/revision, not application-level success.
- `text`: provide a literal, case-sensitive `text` of 1–256 characters; poll
  normalized readable frame text after successful loading. Field values and
  inaccessible content remain omitted. Returned limitations make incomplete
  observation explicit; a timeout does not prove text is absent from the whole page.

`timeout_ms` defaults to 5,000 and is bounded to 1–10,000. Both conditions return
`timed_out` on deadline. If the deadline expires before any text read completes,
`inspection_not_completed` distinguishes missing evidence from an observed miss.
`PageWaitResult` contains session, condition, status,
URL/revision, and limitations; the query and page text are excluded from receipts
and journal events. Text waits refresh inspection and invalidate old references.
They allow DOM changes while waiting; navigation, stop, a control action, renderer
termination, or session shutdown interrupts them. Revocation/expiry checks also
apply before returning results. Only one wait runs per session, and a wait cannot
start during a controlled action. Waiting does not spend network budget by itself.

### Scoped task downloads

Request `download` alongside `read` and `navigate`, with exact absolute filenames
in `download_destinations`. For example:

```json
{
  "origins": ["https://example.org"],
  "allow_private_network": true,
  "actions": ["navigate", "inspect", "read", "download"],
  "download_destinations": ["/home/yourname/Downloads/report.pdf"],
  "max_download_bytes": 26214400
}
```

The approval popup lists destinations and the per-file size limit. Parent
directories must already exist and cannot contain symlinks. Filenames must be
normalized absolute paths; there is no folder-wide, overwrite, or execution
authority. Up to 20 filenames may be approved. The default size limit is 25 MiB;
the maximum is 100 MiB.

Inspect a page and pass the attachment link's current `snapshot_id` and
`target_id`, plus an approved `destination`, to `steed_page_download`. It starts
navigation on that same task page/profile and returns a `download_id`.
Poll `steed_download_status` with its session/download IDs; terminal states are
`completed`, `cancelled`, or `failed`. Status polling spends operation budget,
so use a bounded polling interval, such as 500 ms. Completed receipts include
destination, observed received/total bytes, state, and a bounded error code.
The file's contents and URL query are excluded from journal evidence.

Steed accepts only the expected native attachment request. HTML/save-page
responses are unsupported; download URL redirects fail closed when they change
the request's URL. Unapproved origins are blocked before their request reaches
the server. Pending transfers have a 30-second deadline and block other page
actions/takeover. The endpoint and cookie tests cover private and concurrently
shared profiles; ordinary tabs keep their existing download picker/history.

The engine writes to a private temporary directory anchored by a Linux directory
descriptor. Before publishing, Steed rechecks authority, destination identity,
byte counts, and journal availability. Atomic hard-link publication fails if a
file or symlink already occupies the approved name. Completed files have mode
0600. Progress checks cancel oversized/unknown-length transfers; temporary disk
usage may exceed the limit briefly before an engine notification is processed.
The final file is never published over the size limit.

`steed_download_cancel`, stop/navigation, revocation, expiry, disconnect, and
session/task shutdown cancel pending transfers and remove staged bytes. Files
already published remain; cancellation cannot undo an HTTP request already sent.
No retry, pause/resume, file execution, or file-content API is supplied. There
are at most 100 download records per session. An evidence failure after physical
publication disables task access; reconcile the destination before retrying.

In **Agents → Task Permissions**, select a task, then open **Activity** for its
latest 200 journal events or **Downloads** for progress, approved destination,
byte counts and results. Select a pending transfer and click **Cancel Selected
Download** to stop that file while retaining the task. Completed/failed receipts
remain available after its pages close. The display is task-scoped and excludes
page/form contents. It refreshes while the dialog is open; closing stops polling.

Qt requires choosing the download path before accepting its request; see
[QWebEngineDownloadRequest](https://doc.qt.io/qt-6.11/qwebenginedownloadrequest.html).
Steed uses ordinary page navigation to retain page request enforcement, rather
than exposing an unrestricted engine download API.

### Retained task artifacts

Include `artifact` in a task's requested actions to save/read generated notes or
drafts locally. This permission is separate from page reading, uploads and app
connections; it grants no website access. The popup explains that drafts remain
after the task ends. Old grants gain no artifact authority.

Use `steed_artifact_store` with `title`, `content`, optional `format` (`text` or
`markdown`, default `markdown`) and optional `source_events`. Each source must be
the sequence number of this task's successful `page.inspected` journal event;
foreign, invented, duplicate and other event types are rejected. Up to 20 sources
are allowed. These are inspection references, not verification of generated claims.
Source URLs can be written into the draft by its author; Steed does not fetch them.

Storage returns an immutable ID, size, SHA-256 fingerprint and source references,
without echoing the body. `steed_artifact_list` returns owned metadata and
`steed_artifact_read` retrieves one owned draft. Calls spend the task's operation
budget and require current approved authority; completion, revocation, expiry and
restart end client access. A new task cannot adopt another task's drafts.

Limits are 16 KiB UTF-8 per draft, 50 drafts/512 KiB per task and a 200-character
printable title. The MCP transport's serialized-request size limit also applies.
Drafts are immutable through the API; save a new draft to revise one. Body storage
and its metadata receipt commit together. Titles/body stay out of journal events.
Content is retained as local plaintext in owner-only `tasks.sqlite`, not encrypted
or automatically sent to any provider, app or website.

In **Agents → Task Permissions → Artifacts**, choose a draft to review it as plain
text. Markdown/HTML do not run scripts, load images or follow links. Completed and
interrupted tasks retain drafts for user review after restart. **Delete Selected
Draft** asks for confirmation and removes the stored draft with a content-free
receipt; this trusted user action is unavailable to agents. It does not promise
forensic erasure from SQLite journals, free pages or external copies.

### Persistent agent file library

Use **Agents → Agent File Library…**, or **Settings and tools → File library** in the
agent sidebar, to keep reusable files outside individual tasks:

1. Choose **New library folder…** and name it, for example `Research`.
2. Use **Add files…** or **Add folder…**. Imports copy regular files, subfolders
   and empty directories into Steed; originals remain unchanged. Select a
   subfolder first to add files inside it. Symlinks and special files are rejected.
   Reading selected files happens in the background, and each import commits as
   one operation. Closing the window cancels pending publication.
3. Turn on **Full file access for all connected agents**. This gives every
   authenticated connected agent read/create/edit/remove access throughout that
   folder, including future contents and subfolders, across tasks and restarts.
   No repeated per-file or task-grant popup is needed for library operations.
   Folders start disabled. Enable only folders whose contents you intend agents
   and their model/process providers to receive.
   To keep something you downloaded, select it in **Downloads** and choose
   **Add to Library…**, then the library folder; a taken name becomes
   `name (2).ext`. The button is not offered in incognito windows.
4. Ask your agent: “Use my Research library folder, read the project notes and
   save your revised report back into it.” The agent discovers enabled folders
   through MCP; it does not need a local host path or another upload. Agents can
   also search inside text files (`library.search` with `content: true`, files up
   to 4 MiB, returning a short snippet) and list the newest additions first
   (`order: "recent"`), so “use the PDF I just added” or “find my notes about the
   budget” work without browsing every folder. Each result reports `modified`;
   files saved before this version report 0 until they next change.
5. Disable the same switch to stop further access immediately. Unfinished writes
   are cancelled and cannot resume simply because you re-enable the folder.
   Copies an agent has already read cannot be retracted.

This is an explicitly shared library across regular browser profiles, separate
from browsing cookies/history and per-task workspaces. Websites never receive
library access. The library UI is unavailable in incognito and closes/clears on
profile changes; independent agent access previously enabled on a library folder
remains in effect until you disable it. No library permission authorizes code
execution, browser actions or app-account access. External agents keep their own
host tools/permissions; the library does not limit those tools.

**Remove** moves files or complete subfolders to **Show Trash**. Choose an entry
there and **Restore** to recover it. **Versions…** restores an older file edit
as a new current revision. Agents cannot silently overwrite a newer edit: writes
require the listed revision, and directory revisions change with descendants.
**Export file…** copies a current file to a new chosen location. **Clear recovery
copies…** permanently clears the selected library folder's Trash/old versions
after confirmation; current files remain. These controls do not promise forensic
SQLite erasure or remove copies already exported/fetched.

Storage is local in `~/.custom_browser/agent_library.sqlite`, with private file
permissions and a separate schema from tasks. Current limits are 50 library root
folders, 64 MiB per file, 512 MiB total including versions and pending writes,
500 import entries at once, and bounded metadata/history counts. The UI can
filter the selected folder by name or path with the **Filter** box (a literal
substring, so `%` and `_` are ordinary characters). **Next page** and **Previous
page** navigate 200 entries at a time, including entries beyond the former 2,000
row limit. Paths remain visible when a filter or page omits their parent folder.
Agents can page with `steed_library_search` to find a file across every enabled
folder without listing them one at a time. Files are stored as data,
never rendered/executed automatically. Reads and uploads use 32 KiB chunks.
These are resource limits, not repeated approval requirements.

| MCP tool | Use |
| --- | --- |
| `steed_library_folders` | Discover enabled root IDs/names; disabled roots are hidden. |
| `steed_library_list` | List direct children with `folder_id`, optional `path`, `after`, `limit`; recurse into directories. |
| `steed_library_search` | Find files across enabled folders by name/path, optionally restricted to `folder_id`. Returns folder id/name, path and current revision; literal substring match, page with the returned cursor. `has_more`/`truncated` indicate that more matches can be retrieved on the next page. |
| `steed_library_read` | Read base64 bytes with `folder_id`, `path`, current `revision`, optional `offset`/`limit`; changed revisions fail. |
| `steed_library_mkdir` | Create a relative subfolder and missing parents. |
| `steed_library_write_begin` | Bind `request_id`, path, byte count, SHA-256 and expected revision; `0` creates a new name. |
| `steed_library_write_chunk` | Append canonical base64 at the next offset to an owned `write_id`; identical retries are harmless. |
| `steed_library_write_commit` | Verify length/hash and publish atomically; committed repeats return the original receipt without replay. |
| `steed_library_remove` | Move a file/subdirectory to recoverable Trash with its expected revision. |
| `steed_library_restore` | Restore a known removed file/subdirectory inside an enabled root. |
| `steed_library_import_workspace` | Copy a current library file into a sealed task input; requires workspace permission. |
| `steed_library_save_workspace` | Save a sealed task output into an enabled library root; requires workspace permission and expected revision. |

The workspace bridge removes repeated uploading for local generators. Import a
library file, use its returned `file_id` in the existing worker protocol, then
save the sealed output back to the library. Normal workspace/code/app permissions
still apply; library access alone does not execute a package. Task workspace
limits remain 8 MiB/file and 32 MiB/task. Pending library writes are task-owned;
cancelled tasks cannot continue them. Restart discards unfinished writes without
replaying edits. Files and enable switches remain available to later tasks.

Linked/live host directories, filesystem mounts, automatic indexing/content
search, drag-and-drop and larger configurable quotas remain possible follow-ups.
The first milestone is a persistent managed library with explicit folder access.

### Generated files and custom integration packages

Agents can supply arbitrary file formats through a task-owned workspace: PDFs,
spreadsheets, images, archives and source code all use the same byte interface.
Steed stores and exports those bytes. Agents can supply bytes they already generated,
or build a [local Python worker](#running-an-agent-built-local-worker) to create a
missing generator or integration using supported app tools.

For users, open **Agents → Task Permissions → Workspace**, then select the task:

1. **Share File With Task…** selects one regular local file. Its snapshot becomes
   readable by the connected agent while that task has approved `workspace`
   permission. Sharing may disclose its contents to the agent's model provider.
   Host folders, other task files and browser credentials are not included.
2. Select a generated or shared file to see its name, source, type, state, size
   and SHA-256. Contents are not rendered or executed in this panel.
3. **Export Selected File…** saves a sealed file to a new filename, including
   after completion or restart. Existing filenames and symlink paths are rejected.
4. **Delete Selected File** defaults to No and clears the retained bytes after
   confirmation. Packages depending on the file become unavailable. Exported
   copies and the content-free journal remain; forensic SQLite erasure is not promised.

For agents, request `workspace` in `steed_grant_request.scope.actions`. It is
independent of page reading, text artifacts, app connections and file transfers.
Each call consumes an operation. Use these tools in order:

| Tool | Arguments and behavior |
| --- | --- |
| `steed_workspace_begin` | `name`, `media_type`, `byte_count`, lowercase `sha256`; matching declarations resume the same ID. |
| `steed_workspace_write` | `file_id`, next byte `offset`, canonical `data_base64`; identical written chunks can be repeated safely. |
| `steed_workspace_seal` | `file_id`; verifies declared length/hash and makes bytes immutable. |
| `steed_workspace_list` | Owned metadata, including `generated`/`user_shared` provenance. |
| `steed_workspace_read` | `file_id`, optional `offset`/`limit`; bounded base64 plus `eof`. |
| `steed_workspace_export` | `file_id`, exact `destination`; also requires approved `download` permission for that filename and size. |
| `steed_workspace_stage_extension` | `manifest_id`; pins owned sealed source and reports requested access, granting and running nothing. |

Limits: **8 MiB/file, 50 names and 32 MiB reserved/task**, with 32 KiB decoded
chunks. Uploading the maximum file takes at least 258 operations, so request an
appropriate budget. Names are normalized relative paths, never host filesystem
paths. Deleted names remain reserved; a different revision needs a new name.
Data and metadata commit with content-free events in schema-10 `tasks.sqlite`.
Local bytes are plaintext in this owner-only database. Restart retains bytes but
ends all old task authority; retained-file export/deletion is a trusted user action.
Recovery can also copy explicitly selected sealed workspace files into a fresh
task; see [continuing previous work](agents.md#continuing-previous-work).

Export publication checks live permission again, avoids following parent symlinks
and never overwrites. A journal failure before publication prevents export; failure
to record completion can leave the published file, ends task service authority,
and must not be treated as proof of non-export. Sharing and exporting bounded
regular files currently use the Qt thread, so slow local storage can briefly pause UI.
Sending/uploading exported files still requires the corresponding independent
site/app permission. Gmail attachments are not implemented by this workspace.

To stage a custom integration, seal its source files and a UTF-8 JSON manifest
(maximum 16 KiB). For example:

```json
{
  "version": 1,
  "name": "custom-helper",
  "entrypoint": "main.py",
  "files": ["main.py", "README.md"],
  "capabilities": []
}
```

All listed source names must identify distinct sealed files in the same task;
the entrypoint must appear in that list. Up to 20 source files and 20 exact
requested capability records are allowed. Each capability declares `connector`,
`tool`, `effect` and `destination`, following existing app-scope syntax. These
are untrusted requests; the trusted adapter registry determines actual effects.
Duplicate manifest keys/capabilities, traversal, missing/unsealed sources and
changed hashes are rejected. Staging is repeatable by manifest ID and pins source
IDs/hashes. The Workspace panel shows package identity and requested access.
**Staging grants no execution or app authority.** Running a package requires the
separate task and exact-package approvals below. Packages cannot register trusted
tools or obtain credentials. Agents cannot import host paths, delete retained files
or approve their own code.

Meta's [Muse architecture](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)
describes an isolated agent runtime, separate credential services and privileged
brokers for user-data access. The useful pattern for Steed is to keep executable
agent code outside the browser while retaining typed, permission-checked operations
and user-selected data sharing. Steed has not implemented Muse's VM or taint tracking.

### Running an agent-built local worker

The user selected a local worker. Before uploading source, an active agent can
call `steed_worker_capabilities` without a grant. It runs one fixed, bounded
background setup check per browser run, checking the actual isolated Python,
read-only package mount, syscall restrictions and hard limits. It returns
`available`, a sanitized `reason`, requirements and resource limits. Its
`environments` list reports `stdlib` and the optional `documents` environment,
library versions, environment hashes and actual sandbox readiness. It opens
no page, reads no task/user files and grants no execution or account access.
Concurrent callers share the cached check; agents cannot force repeats or supply
probe code. Actual package runs independently recheck setup and permissions.

Users can click **Agents → Task Permissions → Workers → Check Worker Setup** to
retry after installing dependencies or correcting machine policy. The UI stays
responsive, reports readiness or setup guidance, and never exposes exception
details. Restart resets the support snapshot; shutdown ends a pending check.

It requires Linux, a root-owned `/usr/bin/bwrap`
supporting `--disable-userns`/`--assert-userns-disabled` (tested with 0.9.0), enabled
unprivileged user namespaces, `libseccomp.so.2`, and root-owned system Python under
`/usr`. Debian/Ubuntu packages are `bubblewrap` and `libseccomp2`. Browser launch and
dependency checks still use Steed's virtual environment; generated code uses isolated
system Python with its standard library and, when explicitly selected, the separate
maintained document bundle. Missing isolation support fails the run;
there is no host-execution fallback. A separate-kernel VM remains a possible future
deployment option.

1. Request `actions: ["workspace", "worker"]` with a sufficient operation budget.
   Add `artifact` or exact `app` capabilities only when needed. Ordinary task
   approval permits execution requests; it does not approve arbitrary packages.
2. Seal source and its manifest, then call `steed_workspace_stage_extension`.
3. Call `steed_worker_run` with `extension_id` and a stable `request_id`.
   Set `runtime: "documents"` for the installed optional bundle; the default is
   `"stdlib"`. No agent argument accepts dependency paths, URLs or installations.
4. The user sees **Run custom code?**, the pinned source/hash, declared app access
   and limits, plus the environment name and library versions. **Deny** is the
   default. **Run Package** approves that source in that environment. The optional
   checkbox remembers both hashes for this task and current grant; changed code,
   runtime, Python or bundle bytes need fresh approval. Decisions end on restart.
5. Poll `steed_worker_status`, then read/export the generated Workspace files.
   Users can review receipts, reopen pending reviews and cancel runs under
   **Agents → Task Permissions → Workers**. Closing the popup leaves review pending.

`steed_worker_list` returns owned receipts; `steed_worker_cancel` stops an owned run.
States are `awaiting_approval`, `running`, `cancelling`, `succeeded`, `failed`,
`cancelled` and `interrupted`. Repeating a run ID returns its receipt without
executing again, including failures and cancellation; changed package/runtime inputs are
rejected. Restart interrupts unresolved records without replay or restored grants.
Receipts describe script completion; external app outcomes have separate Apps
receipts and can remain uncertain after cancellation. Use a new reviewed package
and run ID for an intentional retry after inspecting existing effects.

Entry points must be `.py`. Source can import the mounted `steed_worker` helper:

```python
import io
import zipfile
from steed_worker import store_file

output = io.BytesIO()
with zipfile.ZipFile(output, "w") as archive:
    archive.writestr("report.csv", "name,value\nSteed,1\n")
store_file("report.zip", output.getvalue(), "application/zip")
```

`read_file(file_id)` reads an owned, explicitly shared/generated task file.
`call(command, **arguments)` supports typed `workspace.begin/write/seal/list/read`,
`artifact.store/list/read` and `app.list_tools/call_tool/status`. Every call still
needs live task authority and spends its existing budget. App calls additionally
need the exact manifest connector/tool/effect/destination; actual effects come from
the trusted registry. Only this run's app request IDs are available through status.
Generated code cannot approve grants/reviews, export host files, launch other
workers, register providers, use arbitrary HTTP, or access browser pages directly.
Credentials stay with Steed's trusted connector adapter.

Each run has **20 seconds wall time, 10 CPU seconds, 512 MiB address space,
32 MiB scratch, 8 MiB per scratch file, 64 file descriptors and 512 broker calls**.
There are at most two concurrent local workers, one per task, and fifty receipts
per task. Workspace file/byte quotas also apply. Package and helper mounts are read
only; home/profile/state/keyring/session-bus directories and parent environment
are absent. Network/PID/user namespaces are isolated. Before importing generated
code, an irreversible seccomp allowlist blocks sockets, subprocess/thread creation
and namespace changes, and hard resource limits are installed.

The [Bubblewrap maintainers](https://github.com/containers/bubblewrap/blob/main/README.md)
explain that callers define the isolation policy; Steed supplies those restrictions
and tests actual blocked operations. Kernel isolation shares the host kernel and
is not a VM. No package dependencies, shell/build tools or direct network are
available. Custom integrations can compose existing approved adapters; adding
new external services still needs a trusted adapter/credential policy.

Cancellation, task completion, revocation, expiry, source deletion and shutdown
stop the worker. Queued broker operations recheck authority immediately before
dispatch. Scratch and materialized source are removed after termination; abrupt
host crashes can leave private temporary directories. Retained outputs stay in
Workspace until user deletion. User code's stdout is reserved for the bounded
broker protocol; stderr is bounded and discarded. Receipts/events omit code,
file contents, stderr and credential text.

### Worker dependency decision

The user selected the optional maintained bundle. **`documents-v1` is implemented**:
[ReportLab 5.0.1](https://pypi.org/project/reportlab/5.0.1/) for PDFs,
[openpyxl 3.1.5](https://pypi.org/project/openpyxl/3.1.5/) for XLSX, and
[Pillow 12.3.0](https://pypi.org/project/pillow/12.3.0/) for images. Its pinned
dependencies are charset-normalizer 3.5.2, et-xmlfile 2.0.0 and defusedxml 0.7.1.
`worker_bundle.lock.json` records exact wheel URLs, sizes and hashes;
`requirements/worker.txt` provides the corresponding hash lock. Browser dependencies
and basic browsing remain unchanged; do not install that lock into Steed's venv.

Run once from the repository root:

```bash
.venv/bin/python -m browser.install_worker_bundle
```

The command downloads only the locked wheels using pip's isolated configuration,
`--require-hashes`, `--only-binary=:all:` and `--no-deps`. It atomically publishes
them in the git-ignored `.worker-bundles/documents-v1/` directory, then tests real
PDF/XLSX/PNG generation inside Bubblewrap. Repeating it verifies the existing set.
There are no host package imports, setup hooks or source builds. For offline setup,
add `--wheel-directory /path/to/exact/wheels`. `--destination` is for packaging/tests;
Steed itself always discovers its repository's `.worker-bundles` directory.

Restart Steed or click **Task Permissions → Workers → Check Worker Setup**.
An agent calls `steed_worker_capabilities`, checks the `documents` entry's
`available` flag, then uses `runtime: "documents"` with `steed_worker_run`.
For example, a reviewed package can save a PDF with:

```python
from io import BytesIO
from reportlab.pdfgen import canvas
from steed_worker import store_file

output = BytesIO()
pdf = canvas.Canvas(output)
pdf.drawString(72, 720, "My report")
pdf.save()
store_file("report.pdf", output.getvalue(), "application/pdf")
```

Each run verifies wheel hashes off the Qt thread and extracts verified snapshots
into a separate read-only `/dependencies` mount. Python uses `-I -S` and an explicit
dependency path; `.pth` startup hooks stay inert. The same syscall/resource policy
applies to native Pillow code. The only added `ioctl` request is `FIOCLEX`, which
CPython needs to mark temporary-file descriptors close-on-exec; other requests
remain denied. Dependencies get no direct network, home, credentials or subprocesses.

The environment hash includes system Python/architecture, trusted worker code,
the entire maintained lock and selected wheel hashes. Receipts and remembered
decisions bind that hash to the source package and live task grant. An environment
change before approval is rejected; tampered wheels fail before generated code or
broker calls. Missing or failing document support leaves stdlib workers available.
Schema 13 preserves legacy receipt history, labels it stdlib with an unknown old
environment, and discards legacy package approvals; it never restores execution.

The maintained wheel selection covers CPython 3.10–3.14 on Linux glibc 2.28+
x86-64/ARM64. Actual native-library acceptance was on CPython 3.12/x86-64 here;
other selections need their own machine checks. Unsupported platforms fail closed.
The existing 20-second/512-MiB/32-MiB-scratch budget still applies, so this is for
small document generators, not large spreadsheets or unlimited image processing.

Updates must be maintained changes to the versioned lock, with reviewed hashes,
real sandbox acceptance and fresh approvals. To uninstall, stop workers/Steed and
remove only `.worker-bundles/documents-v1/` (make its directory writable first);
user Workspace files/receipts are independent and remain intact. Agents cannot
install dependencies. Arbitrary per-task dependencies/build tools and reusable
integration installation remain separate work. Google registration stays deferred.

For implementation background, pip's
[secure installation guidance](https://pip.pypa.io/en/stable/topics/secure-installs/)
documents hashed binary-only dependencies. Hashes identify selected bytes, not
trustworthiness; isolation continues to apply.

### Agent-designed connector recipes

Reviewed recipes are the first implemented stage of agent-designed providers. An
agent describes a bounded REST integration in JSON; Steed interprets it through
its trusted connector broker. Agents never receive account credentials or install
Python in the browser. Recipes support native OAuth/PKCE or bearer-token authentication,
fixed collection paths, resource reads and bounded cursor pagination. Version 2 adds
reviewed create/send POST tools and optional exact-operation read probes. Provider-specific
edits and multi-step workflows remain later milestones. The goal
is the same account and permission experience as Steed's built-in Google provider.
Shared Google registration remains deferred independently.

To add a service:

1. Ask an agent to design a recipe from the service's official API documentation.
   It calls `steed_recipe_capabilities` for the supported format and limits, requests
   `workspace` and `connector` permission and uploads/seals the JSON. Call
   `steed_recipe_validate` first to get grammar diagnostics and compiled domains,
   account selectors, tools and destinations. It makes no network requests, opens
   no popup and installs nothing. Correct invalid source in a new sealed file,
   then call `steed_recipe_propose` with `file_id` and a stable `request_id`.
   Successful preflight is not approval or verification of provider semantics.
   Alternatively, choose **Agents → App Connections → Add Connector Recipe…** and
   select a local JSON file.
2. Review **Add this service connection?**. It shows the actual HTTPS domain,
   identity endpoint, tool paths, selected returned fields and exact source hash.
   OAuth recipes also show both authorization/token endpoints and every scope.
   Check the actual service, declared effects and payload bindings against its API documentation;
   a method or declared effect is not proof of what that service does. Click **Add Connection Recipe** or **Deny**.
   Closing leaves an agent proposal pending. Reopen it in
   **Task Permissions → Connector Recipes → Review Connection Recipe…**.
3. Select the installed service in **App Connections**, choose its tools, and click
   **Connect**. For bearer recipes, enter that service's API token in the password
   field. For OAuth recipes, import a native client JSON once under **Set Up App
   Access…**, then sign in through your system browser. Future connections reuse
   client setup from the keyring. Steed verifies the account ID/label at the reviewed
   identity endpoint. Credentials are never workspace inputs.
4. Enable the account's **Read**, **Write** or **Send** switches if agents should
   request those tools without a permission popup for every app-only task.
   Recipe creates/sends still require an exact payload approval for every action. Each task still requests exact tool/resource
   destinations and gets normal budgets, cancellation and retained receipts.
   With switches off, approve each task separately. Recipe installation itself
   grants neither account access nor task permission.
5. Finish the recipe-design task and start a fresh task to use the connected
   account: Steed's task grants are fixed, so installation does not silently widen
   its existing grant. Discover the account/tool via `steed_app_list_tools`, then
   use `steed_app_call_tool` with an exact resource ID, or literal `account` for a
   fixed collection path, as `destination` and a stable
   `request_id`. Poll `steed_app_status`; the normal receipt keeps selected JSON
   fields and replay returns the same receipt without repeating the HTTP request.
   For creates/sends, request both `app` and `artifact` permission, store the draft
   with `steed_artifact_store`, and pass its owned `artifact_id`. Review **Review App
   Action**: account, exact URL, destination and **Request Payload**. Approve once
   with **Create This Item** or **Send This Message**, or choose **Deny**. The
   **Draft** tab displays the original text. Only the trusted browser UI can approve.

The [example recipe](../examples/connector_recipes/notes.json) uses a synthetic
`.example` domain; it is a format reference, not a working external account.
The [OAuth collection example](../examples/connector_recipes/notes-oauth.json) is
also synthetic. Version 1 declares one exact public HTTPS API origin, authentication,
an identity path with JSON pointers for subject and label, and 1–10 named GET tools.
A tool path is fixed, with literal destination `account`, or includes one complete
`{destination}` segment; resource IDs are single,
bounded ASCII segments, never URLs or queries. Each response selects 1–10 labeled
JSON pointers (objects and array indexes only). Source cannot contain credentials,
custom headers, executable expressions, arbitrary requests or API request bodies.
OAuth token forms are built exclusively by trusted Steed code for its exact reviewed
token endpoint, never invoked as agent tools.
Recipes are at most 16 KiB; API responses at most 128 KiB and retained tool results
at most 16 KiB. Each task retains at most 20 proposals with three pending at once.
A catalog retains at most 50 versions and is limited to 2 MiB.

Native OAuth recipes use this authentication object (all endpoints/scopes appear
in the installation popup):

```json
{
  "type": "oauth_pkce",
  "authorization_endpoint": "https://auth.fixture.example/authorize",
  "token_endpoint": "https://auth.fixture.example/token",
  "scopes": ["profile", "notes.read"]
}
```

Register a native/desktop client with the actual provider and supply a user-owned
JSON file containing `{"client_id":"YOUR_NATIVE_CLIENT_ID"}` and optionally
`client_secret`. Client files cannot change recipe endpoints. No registration or
credentials are provided by the synthetic example. The provider must support PKCE
S256, authorization-code exchange and an ephemeral loopback
`http://127.0.0.1:<port>/callback`. Steed opens the external system browser and
validates callback Host/path/state; it does not reuse a browser cookie session for
API authorization. It requests all reviewed scopes, with no Google-specific
`prompt` or `access_type` parameters. Provider-specific parameters, discovery,
device flows and hosted-only callback services are unsupported; bearer setup
remains available. See [native-app OAuth](https://www.rfc-editor.org/rfc/rfc8252.html).

The trusted worker verifies granted scopes and token lifetime, binds credentials
to exact recipe/source/account and saves access/refresh tokens plus client setup
only in Secret Service. Refresh is serialized per connected account, verifies
identity and persists rotated credentials before publishing data. The first use
after restart refreshes again when refresh access exists. A service that grants
only an access token works until expiration, then requires reconnection. Turning
access off, disconnecting or removing the source denies late publication and
credential restoration. Failed keyring saves publish no data. Provider failures
and revoked consent require reconnection rather than automatic operation retries.
The protocol follows the [OAuth authorization/refresh flow](https://www.rfc-editor.org/rfc/rfc6749.html).

Changing the provider in setup clears the selected client file to prevent
accidental reuse across services. Recipe provider names in account UI include the
actual API domain, and tools include the connected account label. Results redact
literal current access/refresh/client-secret values before JSON escaping, including
object keys; this is not a general information-flow/taint guarantee against a
service transforming or disclosing its own credentials.

For a fixed collection path, an optional pagination declaration supports at most
five pages in one approved tool operation:

```json
"pagination": {"cursor": "/next", "parameter": "cursor", "max_pages": 3}
```

The [paginated example](../examples/connector_recipes/notes-paginated.json) uses this
format. The first request uses the exact fixed path with no query. Each response's
cursor pointer must exist; `null` or an empty string ends pagination. Otherwise it
must be an ASCII cursor of at most 512 bytes, encoded as the one reviewed query
parameter. Cursor URLs are never followed as destinations. Selected response
fields must be arrays; Steed concatenates each selected array and adds
`_pagination: {pages, more_available}` to the retained result. That output label is
reserved. At the reviewed page limit, `more_available` truthfully reports remaining
data without fetching it or exposing a cursor. This is bounded enumeration, not
an unlimited full-account export or arbitrary page selection.

Identity, catalog, account and cancellation are checked between pages. Repeated or
credential-reflecting cursors, wrong response shapes and oversized combined output
fail without publishing partial data. The transport accepts only the configured
path/parameter and canonical encoded value; extra/duplicate parameters, foreign
paths and redirects fail before credential dispatch. Each API response remains
bounded to 128 KiB and the entire result to 16 KiB. Task receipts count a single
operation with at most the reviewed number of page requests and identity checks.

The HTTPS transport rejects private, reserved, multicast, mapped and mixed DNS
answers, connects to a validated numeric public address without resolving again,
and verifies TLS/hostname against the reviewed domain. It ignores proxy settings,
follows no redirects and makes no automatic retries. Identity is verified again
before each tool read, write preview, write dispatch and reconciliation probe.
Changes to identity, catalog or account state deny entry/results. After write dispatch,
an interrupted or malformed response remains uncertain because the service may have committed.
Requests and account setup run off the Qt thread; agents cannot supply authorization
headers or invoke the trusted installation/account methods.

Each recipe hashes its exact source bytes into a new immutable provider identity.
Even a formatting-only change is a new version with a fresh account and switches
initially off. **Remove Selected Recipe** persists catalog denial first, revokes
matching tasks/saved permissions and disables accounts before deleting local
credentials and forgetting saved OAuth client setup. A failed cleanup cannot restore an old account upon reinstallation.
Existing workspace files, drafts and receipts remain independently manageable.
Catalog and account metadata live beside app settings in
`~/.custom_browser/connector_recipes.json` and `app_accounts.json`; secrets live
only in the keyring. An invalid recipe catalog disables recipe providers while
leaving the built-in Google provider available.

Task storage schema 14 adds content-free recipe proposal receipts. Restart
interrupts pending installations and never repeats them. Deleting a proposed
source or ending its task cancels the review. Catalog publication is idempotent
but separate from SQLite journaling: if journaling fails after installation, the
catalog can retain the reviewed recipe, with no account, credential or task access
created. A new trusted connection step is still required.

Recipe milestone acceptance: **707 regressions passed in 297.745 seconds**;
28 recipe checks passed in 4.567 seconds. SDK/raw clients both completed recipe
review, synthetic connection and selected-field reads over stdio and authenticated
HTTP MCP alongside actual sandbox PDF/XLSX/PNG generation. Startup/render/shutdown
and Ruff/format passed. Logs: `/tmp/steed-recipes-full.log`,
`/tmp/steed-recipes-focused.log`, `/tmp/steed-recipes-mcp-{stdio,http}.log`, and
`/tmp/steed-recipes-startup.log`. Review/settings previews were inspected. Real
service accounts and native desktop interaction remain separate acceptance checks.

OAuth/collection milestone acceptance: **723 regressions passed in 295.840
seconds**; 45 final recipe checks passed in 6.286 seconds, including subsequent
malformed-catalog and immediate UI-refresh fixes. Both external clients completed
bearer and OAuth collection workflows over both MCP transports alongside real
sandbox document generation. Ruff/format passed and review/setup previews were
inspected. Logs: `/tmp/steed-recipes-oauth-full.log`,
`/tmp/steed-recipes-oauth-focused.log`, and
`/tmp/steed-recipes-oauth-mcp-{stdio,http}.log`. Real-provider consent, refresh
compatibility and native desktop interaction remain separate acceptance checks.

Preflight/pagination milestone acceptance: **733 regressions passed in 283.392
seconds**; 55 final recipe checks passed in 6.169 seconds, including a subsequent
long-metadata discovery/registration fix. Both independent clients completed
preflight and bearer/OAuth/paginated reads through both MCP transports alongside
real sandbox document generation. Pagination previews, Ruff/format, doc links,
53-tool discovery and all three examples passed. Startup crashed while concurrent
offscreen processes initialized Vulkan; a standalone retry passed. The cause is
not proven. Logs: `/tmp/steed-recipes-pagination-{full,focused,mcp-stdio,mcp-http}.log`,
`/tmp/steed-recipes-pagination-startup.log` and
`/tmp/steed-recipes-pagination-startup-alone.log`. Native desktop and real-account
acceptance remain separate.

### Agent-designed write boundary

Latest static-header/menu acceptance (October 3, 2026): **938 checks pass in
375.207 seconds**. Both actual SDK and independent clients complete v5 reviewed
plans over stdio and authenticated HTTP MCP, alongside document workers and
persistent library reuse. Exact TLS wire, changed-source/account refusal,
OAuth refresh/paging, source/payload preview and lost-reply/no-replay checks pass.
Startup/render/shutdown passes on Qt 6.11.2. Source/menu previews, Ruff/format,
dependency, whitespace, seven recipe examples and local doc links pass.
Logs: `/tmp/steed-menus-headers-final-full.log`,
`/tmp/steed-headers-mcp-{stdio,http}.log`, `/tmp/steed-headers-startup.log`.
Live provider/native desktop acceptance remains separate.

Packaged service discovery: open **Agents → App Connections → Add a Service…**.
Steed loads bounded JSON recipes from `connectors/recipes/`; choosing a service
opens the same default-Deny source review as importing a recipe. This installs
only the reviewed source. Connect its account, enable its action switches, and
grant the task separately. Each create/send still needs its exact payload review.
Missing or malformed catalog entries are skipped; unavailable or changed selections
require reopening the picker. Discovery reads at most 50 matching regular files,
rejects symlinks and special files, and performs no network requests.

The [starter connector handoff](../docs/history/connectors-handoff-2026-10.md) assigns service data and
fixtures to another contributor. The reviewed starter branch is now integrated:
the picker offers **Todoist, GitLab.com, Asana, Raindrop.io and Microsoft 365**.
Microsoft 365 uses a supplied delegated token; native OAuth remains deferred.
GitHub (one repository), GitLab (one project) and Zotero (one library) are imported
templates whose placeholders must be replaced before source review. See
[connector setup](../connectors/SETUP.md) for exact subsets and installation steps.
Every service still needs account connection and enabled actions; no account is
connected automatically. Synthetic acceptance is complete; live-account acceptance
is pending for all these services. Compatibility proofs remain reference data.
Six catalog/picker regressions passed, including changed-source rejection, safe
file discovery, and installation granting neither account nor task access.

### Reviewed API-version headers

**Recipe version 5** adds a required root `request_headers` object containing
1–4 supported non-secret headers. Versions 1–4 keep their existing grammar.
Version 5 retains nested JSON and sequential plans; use explicit `$bind` objects
for draft/destination bindings rather than version-2 flat string bindings.

| Canonical header name | Accepted value |
| --- | --- |
| `Notion-Version` | A valid date in `YYYY-MM-DD` form |
| `X-GitHub-Api-Version` | A valid date in `YYYY-MM-DD` form |
| `Zotero-API-Version` | A string containing an integer from `1` to `99` |
| `OCS-APIRequest` | The string `true` |

For example, add `"version": 5` and
`"request_headers": {"Notion-Version": "2025-09-03"}` to an otherwise supported
recipe. The [versioned sequential example](../examples/connector_recipes/notes-headers.json)
is synthetic; its notes paths are not a Notion connector.
`steed_recipe_capabilities` publishes the accepted names/formats, supported
versions, count limit and `headers_example`. Recipe preflight/list metadata
returns these exact non-secret values.

Source review shows the headers before installation. They apply consistently to
API identity, each read/page, every reviewed POST/plan step and reconciliation.
Payload review includes them alongside the exact URL and source hash. A changed
value creates a different immutable source/provider, so the previous account,
capabilities and payload approval cannot authorize it. Old installed versions
remain usable until disabled; replacing a version does not silently change it.
OAuth authorization and token exchange/refresh never receive these API headers.

Only the four listed names and their constrained values are supported. Other
names, noncanonical casing, duplicate keys, newline/control injection, arbitrary
tokens, authentication/cookie/host/transport overrides and operation-ID headers
are rejected. Credential-derived headers require a separate future authentication
contract; credentials remain in the keyring. Exact-origin/DNS/TLS/redirect/body
checks and per-write previews remain in force. Header syntax acceptance does not
verify a provider's supported version or make a whole service compatible:
identity labels, pagination, POST reads and payload/response shapes still need
separate contracts and acceptance.

### Reviewed identity-label alternatives

**Recipe version 6** adds a required identity field `label_alternatives`: an
ordered list of 1–3 distinct JSON pointers, also distinct from the primary
`label`. The account `subject` remains one exact, validated pointer. For example:

```json
"identity": {
  "path": "/me",
  "subject": "/id",
  "label": "/name",
  "label_alternatives": ["/email", "/id"]
}
```

Only missing, null or empty-string labels advance to the next reviewed pointer,
including a missing/null parent. Invalid types, malformed containers, control
characters, whitespace-only or oversized labels and known credential echoes
fail instead of being hidden by a fallback. An explicitly selected subject
pointer may use the validated numeric-to-string account ID. No label field is
inferred, and no email or ID is used unless the source explicitly lists it.

Source review and preflight/catalog metadata show the alternatives in order.
Account connect, cached authorization, OAuth refresh and paged reads retain exact
subject and resolved-label checks; a changed resolved identity requires reconnect.
Access/refresh tokens and other known exchange secrets must not appear in either
identity value. Alternatives change the source hash/provider, so old accounts,
permissions and reviews cannot authorize the changed source.

Version 6 retains nested requests and sequential plans. Its `request_headers`
field is optional; if present it has the same strict v5 allowlist and limits.
Versions 1–5 keep their own strict grammar and do not accept label alternatives.
`steed_recipe_capabilities` exposes versions 1–8, the alternative limit/policy and
`identity_example`; [notes-identity.json](../examples/connector_recipes/notes-identity.json)
is the corresponding synthetic plan, not a live provider integration.

This supports explicitly reviewed optional display-name contracts; for example,
Notion documents that only user `object` and `id` are always present
([official user reference](https://developers.notion.com/reference/user)). It does
not establish provider consent, request semantics or support for POST queries.
Those need separate contracts and real-account acceptance.

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

**Version 7 typed reads, query inputs and fixed GraphQL documents.** Version 7
keeps every v1-v6 grammar and adds bounded declarative read inputs. A v7 read tool
declares `"effect": "read"` explicitly with `method: "POST"`. Ordinary GET reads
keep the existing grammar:

```json
{
  "name": "search_notes",
  "description": "Search this account's notes with reviewed inputs",
  "method": "POST",
  "effect": "read",
  "path": "/notes/search",
  "destination": "account",
  "inputs": {
    "query": {"type": "string", "max_length": 200},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    "archived": {"type": "boolean", "default": false}
  },
  "request": {
    "text": {"$bind": "input.query"},
    "limit": {"$bind": "input.limit"},
    "include_archived": {"$bind": "input.archived"},
    "cursor": null
  },
  "response": [{"label": "notes", "pointer": "/items"}],
  "pagination": {
    "cursor": "/cursor",
    "parameter": "cursor",
    "max_pages": 3,
    "location": "body"
  },
  "errors": {"pointer": "/error", "code": "/reason", "message": "/summary"}
}
```

- **Inputs are typed and bounded.** At most 8 per tool, lowercase names, and
  `string` (with `max_length` up to 512), `integer` (with ordered `minimum`/
  `maximum`) or `boolean`. An input without `default` is required. Unknown names,
  wrong types and out-of-range values fail before any receipt or HTTP request.
- **The body is fixed by the recipe.** Only `input.<name>` and `destination`
  bindings are accepted in a typed read `request`; draft, credential and
  operation-ID bindings are rejected. The effect stays `read`, so there is no
  payload preview, no operation header, no reconciliation and no automatic retry.
  Literal bodies, empty objects and JSON null are supported. Review the actual
  service endpoint semantics; declaring a read cannot change an external API.
- **POST cursors travel in the body.** `pagination.location` is `body` for typed
  POST reads; ordinary GET reads retain query-string pagination. The named field
  must already exist in the reviewed request. The first body retains its reviewed
  value, and later bodies use the returned cursor. Identity is rechecked
  before every page and a repeated or credential-reflecting cursor fails closed.
- **Application errors are declared, not hidden.** `errors` names the pointer
  that reports a failure plus its code and message. An HTTP 200 body that
  reports an error there fails the read and surfaces the reported code/message
  instead of returning empty reviewed fields. Absent, null and empty error fields
  are ordinary successful responses.

**Fixed GraphQL reads.** A typed read tool may replace `request` with `query`:

```json
"query": {
  "operation": "NoteById",
  "document": "query NoteById($id: ID!) { note(id: $id) { id title body } }",
  "variables": {"id": {"$bind": "destination"}},
  "data_pointer": "/data",
  "errors_pointer": "/errors"
}
```

The document is a reviewed literal in the recipe, at most 4 KiB, and must be a
single named `query` matching `operation`. The pinned
[GraphQL-core parser](https://pypi.org/project/graphql-core/3.2.8/) validates syntax;
the AST is limited to 1,024 tokens, depth 32 and eight declared variables.
Mutations, subscriptions, fragments, schema
definitions, directives and multiple operations are rejected, and agents have no
argument that carries query text. Variables must match the declared names and bind
only the reviewed destination; an empty object works for a fixed viewer query.
A non-empty errors array fails the read and reports up to five bounded
code/message pairs; partial data is never returned as if it had succeeded, and a
missing or null `data` fails rather than yielding empty fields.

**Reviewed POST identity.** `identity` may declare `"method": "POST"` with a
literal bounded `request` body for services that require one (for example a
fixed GraphQL `me` document or a JSON-null identity request). The body accepts no
bindings at all. The same exact
body is used for connect, every authorization recheck, every pagination recheck
and OAuth refresh, and it is pinned to the same reviewed origin and headers.

**Agent surface.** `app.list_tools` publishes each read tool's `inputs` schema,
and `steed_app_call_tool` accepts an optional `inputs` object containing only
those names. Inputs are values, not authority: they cannot change the reviewed
endpoint, effect or destination, they are not part of any permission, and the
existing app permission still governs the call. The stable `request_id` is
fingerprinted over the resolved inputs, so a repeated request ID with different
inputs is rejected instead of silently reusing an earlier receipt. Calls without
typed values retain their earlier fingerprint so old receipts stay retryable.

Source review shows the identity method and body, each typed input with its type,
bounds and default, the exact request bindings, the full GraphQL document and
variables, the application-error pointers and the cursor location.
`steed_recipe_capabilities` reports version 8, the typed-read and GraphQL
policies, the new limits and `query_example`;
[notes-query.json](../examples/connector_recipes/notes-query.json) is the same
synthetic source, not a live provider integration. Credential, origin, header,
cancellation, identity-recheck and no-write-replay rules are unchanged. Live
provider acceptance, credential-derived headers, revision guards, transforms and
gated providers remain separate.

**Version 8 continuation and success contracts.** Versions 1–7 retain their
source grammar. A read or identity can declare an exact boolean success rule:

```json
"success": {"pointer": "/ok", "value": true, "required": true}
```

A missing required flag, non-boolean flag or wrong value fails before selected
fields are returned. Optional flags may be absent but must match when present.
Read pagination can declare `"has_more": "/has_more"`. This must be a boolean:
false ends pagination even when the service retains a final cursor; true requires
a nonempty next cursor. Existing page/cursor bounds still apply and truncation
reports `more_available: true`.

A typed POST read can use a distinct reviewed continuation endpoint:

```json
"pagination": {
  "cursor": "/cursor", "parameter": "cursor", "max_pages": 3,
  "location": "body", "has_more": "/has_more",
  "continuation": {
    "path": "/notes/search/continue",
    "request": {"cursor": {"$bind": "cursor"}}
  }
}
```

Only the returned cursor can bind into this continuation body; the path is fixed
on the reviewed origin. The first request retains its own reviewed body, which
need not contain a cursor field when a continuation is declared. Version 8 also
supports body pagination on an exact resource POST path. GraphQL pagination
remains unsupported. Identity, account access, source identity and cancellation
are rechecked before every page. Read POST and write POST routes, including
resource-template overlaps, cannot share a transport path.

The native review displays success/stop rules and the continuation path/body.
`steed_recipe_capabilities` publishes `continuation_example`;
[notes-continuation.json](../examples/connector_recipes/notes-continuation.json) is a
synthetic executable example. String inputs may be empty for service root paths;
reviewed maximum lengths and control-character rejection still apply. These
contracts enable provider implementation; they do not establish live-provider
acceptance or turn external write endpoints into reads.

**Version 3 nested JSON templates:** GET tools retain the existing format. POST
`request` can contain nested objects, arrays and JSON literals. Bind draft data
with an explicit single-key object such as `{"$bind": "artifact.content"}`;
supported bindings are `artifact.title`, `artifact.content`, `destination` and
`operation_id`. Plain strings are literals, even when they look like binding
names. Binding substitutes the string without interpreting JSON, HTML, code,
expressions or URLs inside it. Objects containing `$bind` must contain exactly
that supported binding; it is reserved template syntax.

Every write must bind draft content; every send must also bind its one approved
email recipient. Limit templates to 10 top-level fields, 20 fields/items per
object/array, 100 total nodes, depth 8 and a final preview/body of 16 KiB. Numbers
must be finite; integers must fit the exact JSON number range ±(2^53−1). Literal
strings cannot contain NUL. Steed freezes the parsed template and exposes a fresh
copy in recipe metadata. The transport permits nested bodies only for reviewed
nested POST rules; v2 bodies remain flat. User approval covers the actual
serialized body, URL, source hash, account, draft hash and operation ID.

The [nested example](../examples/connector_recipes/notes-nested.json) is synthetic.
Nested payloads are the first part of declarative request plans. Version 4 adds
bounded result-dependent POST sequences; version 5 adds the reviewed static
headers above; version 6 adds explicit identity-label alternatives. Arbitrary
query inputs, root-array POSTs, revision guards and response conditions remain future work; provider sign-in and API semantics
still require separate acceptance.

Nested-template acceptance: **774 regressions passed in 299.980 seconds**;
71 final recipe checks passed in 14.370 seconds, including subsequent popup/fixture
refinements. Both SDK/raw clients completed nested creates, lost-reply exact-operation
reconciliation and no-replay checks over stdio and authenticated HTTP MCP alongside
actual PDF/XLSX/PNG workers. Source and payload previews were visually inspected.
Ruff/format and dependency checks passed. Logs:
`/tmp/steed-nested-recipes-{full,final-focused,mcp-stdio,mcp-http}.log`.
These are synthetic account/API checks; live provider/native desktop acceptance
remains separate.

**Implemented: reviewed declarative creates/sends.** Version 1 read recipes remain
compatible. Version 2 may mix the existing GET format with POST tools. The
[create/send example](../examples/connector_recipes/notes-writes.json) is synthetic;
it is not a live service integration. `steed_recipe_capabilities` publishes all
supported versions, bindings, limits and examples. Installation review does not
connect an account, enable switches or authorize a task.

A POST tool declares the ordinary name/description/path/response fields plus:

```json
{
  "name": "create_note",
  "description": "Create a reviewed note",
  "method": "POST",
  "effect": "write",
  "path": "/notes",
  "destination": "account",
  "request": {
    "title": "artifact.title",
    "body": "artifact.content",
    "operation_id": "operation_id"
  },
  "idempotency_header": "Idempotency-Key",
  "response": [{"label": "id", "pointer": "/id"}],
  "reconciliation": {
    "path": "/operations/{operation_id}",
    "operation_id": "/operation_id",
    "status": "/status",
    "success": "succeeded"
  }
}
```

- `effect` is `write` (create/add) or `send`. Writes use destination `account` with
  a fixed path, or `resource_id` with one complete `{destination}` segment. Sends
  require `email` and one `email:recipient@example.com` destination; their path is
  fixed and their payload must bind `destination`. Email payload bindings omit the
  `email:` prefix. Recipient lists, display names and header injection are rejected.
- `request` has 1–10 flat fields with bounded ASCII names. Values bind only
  `artifact.title`, `artifact.content`, `destination` or `operation_id`.
  At least one field must use draft content. No literals, nested templates, raw
  JSON interpolation, expressions, cookies, arbitrary headers or credential bindings
  are supported. Trusted JSON serialization preserves quotes/newlines as data.
  The entire serialized payload, including title, operation ID and JSON escaping,
  is limited to 16 KiB; a maximum-size draft may exceed this and be rejected.
- Optional `idempotency_header` accepts `Idempotency-Key`, `X-Idempotency-Key` or
  `X-Request-ID`, populated only with the receipt's generated operation ID. This
  correlates requests; it does not prove that a service deduplicates them.
- Optional `reconciliation` uses one reviewed GET path with a complete
  `{operation_id}` segment. The POST must carry that ID in a bound field or supported
  header. A probe succeeds only when the returned ID exactly matches the receipt
  and the selected status equals the reviewed success value. It returns the same
  selected response fields. Missing results, mismatches, errors and pending statuses
  remain uncertain; absence never proves an action was not applied. The task needs
  a separate exact `read` permission for that tool to call `steed_app_reconcile`.

Every create/send requires the default-Deny payload popup even when account
switches or saved task permissions approve app access. Review binds source hash,
account/subject, exact destination, operation ID, draft hash, title and serialized
payload. Steed rechecks those inputs and live authority before the single POST.
Pending preview content is scrubbed after approval/denial/cancellation and on restart;
receipts remain durable. Agents and local workers cannot approve their own writes.

The trusted transport permits POST only on reviewed API path templates with exact
JSON/authentication headers and the configured operation header. The OAuth form
exchange remains separate and pinned to its reviewed token endpoint. TLS, public
numeric-address pinning, response limits, no proxies and no redirects apply to both.
Only JSON replies with HTTP 200/201 are accepted; the selected fields represent the
service's acknowledgement, not proof of eventual message delivery. Other statuses,
missing/oversized fields, disconnects and cancellation after HTTP entry remain
uncertain. A same-ID replay returns its receipt and never makes a second POST.
No automatic write retry occurs, consistent with [HTTP's non-idempotent retry
boundary](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2).

The user chose reviewed agent recipes instead of requiring a maintained Steed adapter
for each service's write support. Actual API behavior still depends on the service;
verify its semantics during source review. New source versions get fresh accounts
and switches. Real-account effects require their own explicit user tasks. Shared
Google registration remains deferred. Edits/replacements need a revision/conditional
write contract; delete/spend and executable connector adapters are outside this
implemented format.

Create/send acceptance: **755 regressions passed in 297.778 seconds**; 63 final
recipe checks passed in 12.762 seconds, including a subsequent shared-POST-path
operation-header fix. Both SDK/raw MCP transports passed, standalone startup/render/
shutdown passed, and Ruff/format, whitespace, four example parses and local doc links
passed. Source/create/send popup previews were inspected. Logs:
`/tmp/steed-recipe-writes-full.log`, `/tmp/steed-recipe-writes-final-recipes.log`,
`/tmp/steed-recipe-writes-mcp-{stdio,http}.log`, and
`/tmp/steed-recipe-writes-startup.log`. Real-provider and native desktop acceptance
remain separate. Focused tests
cover owned drafts, recipient scope, forged/missing approvals, exact preview bytes,
OAuth refresh/form separation, overflow, source/account identity changes, revocation
before/after dispatch, restart/non-replay and exact-operation probes. Both SDK/raw
clients have completed reviewed creates/sends and timeout/reconciliation through
stdio and authenticated HTTP, alongside reads and real sandboxed document workers.
No real accounts or desktop secrets were used.

### Sequential connector request plans

**Implemented: recipe version 4 sequential POST plans.** Version 1–3 recipes remain
compatible. A v4 POST tool uses the v3 nested request format for its first step;
optional `plan` contains 1–4 following POST step objects, giving 2–5 total steps.
Each following step has `name`, `description`, `method`, `path`, `request`,
`response` and optionally `idempotency_header`. It inherits the parent's declared
effect and destination. Reads, mixed effects, arbitrary code, loops, branches,
reconciliation and revision edits are outside this first subset.

The [sequential note example](../examples/connector_recipes/notes-plan.json) creates a
note, then appends a comment using the first response's selected ID. Bind previous
selected fields with `{"$bind": "step.create_note.id"}`. Step and referenced field
names must be lowercase identifiers; forward/unknown references are rejected.
Outputs bound into JSON must be strings of at most 4096 UTF-8 bytes without NUL.
At most one full path segment can bind either `{destination}` or an earlier field
such as `{step.create_note.id}`; that field must pass the existing resource-ID
validator, never supply a URL, query, slash or traversal. Normal nested payload
limits and draft-content/recipient requirements apply to **every** mutation.

Calling the existing `app.call_tool` creates a parent receipt and a journaled child
receipt for each step. Each child has its own operation ID, payload hash and
default-Deny popup showing the actual URL/account/draft plus **Step N/M**. Previous
results are frozen into its payload before review. Approval is only for that child;
the next step requires another materialized preview. Source/account/live grants,
owned artifact and durable review hash are checked again before dispatch. Sequencing
and SQLite stay on the Qt owner thread; workers receive only the selected step.

Poll the parent with `app.status`. Its `reference` identifies the current child
while active; its JSON `text.steps` lists child request/operation IDs, states and
payload hashes. A completed child can be inspected through `app.status` under the
same exact task grant. Successful plans report `succeeded`; if later work is denied
or cannot run after a successful mutation, the parent reports `failed` with its
successful/failed child history. An uncertain child makes the parent `uncertain`.
Completed effects are not rolled back. No later step runs after uncertainty,
denial, cancellation, revocation, timeout or failed preparation, and repeated parent
request IDs return their existing receipt instead of starting again.

Task storage schema 15 retains parent/child links without storing request bodies in
the event journal. Restart interrupts active plans, scrubs pending previews and
preserves partial/uncertain receipts without resuming. Plan children reserve slots
within the existing 50-receipt task limit. Clearing a parent's results also clears
its child results and stops pending work; clearing a child stops its active plan.
Confirm uncertain effects manually; this first plan format has no automatic
retry or reconciliation.
Native sign-in and real-provider semantics remain separate acceptance checks.

Plan milestone acceptance: **791 final regressions passed in 284.704 seconds**;
88 final recipe checks passed in 19.453 seconds. Both independent SDK/raw clients
completed reviewed create→append sequences and uncertain-first-step stops over
stdio and authenticated HTTP MCP alongside real PDF/XLSX/PNG sandbox workers.
Four unchanged starter compatibility checks passed. Legacy migration, receipt
reservation, hostile/unknown bindings, payload hash changes, timeout/late reply,
revocation, result clearing and restart/partial-completion paths are covered.
Standalone startup/render/shutdown, Ruff/format, whitespace, 52 local link targets
and all six recipe examples passed. Source and Step N/M previews were visually
inspected. Logs:
`/tmp/steed-plans-{final-full,final-recipes,mcp-stdio,mcp-http,startup}.log`.
No live provider accounts or native desktop acceptance were tested.

### Complex provider workflow decision

The next architectural boundary is how agent-designed connectors handle provider
logic beyond a single reviewed JSON request. Steed's Google implementation uses
provider-specific destination checks, multipart creation, revision-aware edits
and marker-based reconciliation. Other workflows may need dependent mutations. Recipes from
version 4 represent nested payloads and bounded result-dependent POST sequences
with partial completion receipts. Read steps, provider-specific transformations,
revision-guarded edits and read-only per-step reconciliation remain future work.

The chosen design keeps source declarative: trusted Steed code compiles templates
and ordered steps. A parent owns a durable child receipt for each mutation and
each fully materialized payload needs review. Later writes stop after uncertain
completion. Credentials stay in the broker; generated workers can prepare drafts
but cannot execute arbitrary connector code there. Future read-only probes should
reconcile exact step IDs without automatically continuing a stopped plan.

The alternative is a broker-backed sandboxed adapter package: generated code computes
provider logic in the local worker, while typed broker calls own credentials,
authority, payload approvals and per-step receipts. That offers wider protocol and
transform support but adds an executable adapter trust boundary and ongoing sandbox
compatibility work. Existing attachment workers have no connector credential or
network access. No generated Python would run inside the browser under either design.
The user chose declarative request plans first on October 3, 2026. Implementation
continues in that direction. See the [connector implementation handoff](../docs/history/connectors-handoff-2026-10.md)
for independently owned provider recipes and verified current-runtime subsets.
The framework work owns shared catalog discovery and plan execution; service
contributors do not edit the shared broker or UI.

### Shared Google registration decision

**Deferred by the user.** Shared registration remains a goal; local engineering
continues using the existing developer-owned setup. Revisit this at distribution
time rather than requiring a registration decision for each engineering milestone.

To give new installations the same **Connect → sign in → toggle access** flow,
Steed needs a maintained shared Google Desktop OAuth client. The current remembered
developer-owned setup already gives subsequent connections that flow. These are
two distribution choices:

| Choice | User experience | Ownership |
| --- | --- | --- |
| Personal/developer setup | Import a client once, then sign in and reuse switches | Each user maintains their own Cloud project |
| Shared Steed registration (recommended for broader distribution) | Bundled public desktop client configuration; sign in and choose switches | Steed's publisher maintains the project, branding, support and verification |

The implementation proposal is to bundle only the desktop app registration,
retain developer import as an advanced option, and reuse the existing external
browser PKCE/loopback flow, desktop keyring and account switches. User access/refresh
tokens remain local; this needs no Steed cloud credential server and no broader
Google scopes. An installed app's client configuration is not a private user token;
Google's [native-app OAuth guidance](https://developers.google.com/identity/protocols/oauth2/native-app)
describes the installed-app model.

Before publication, the owner must provide a Cloud project, support contact and
public homepage/privacy identity, then complete the applicable production process.
Google's [brand verification guidance](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)
describes domain ownership and branding requirements. `gmail.send` is classified
as sensitive in the [Gmail scope list](https://developers.google.com/workspace/gmail/api/auth/scopes)
and public production access requires the applicable
[sensitive-scope review](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).
Personal-use exceptions exist; they do not establish public distribution readiness.

No shared registration, public policy or external project change has been made.
The decision is whether to keep Steed personal for now or accept maintaining that
publisher identity. Once chosen, client selection, onboarding and synthetic tests
can be implemented before any actual registration/publication.

### Browser-managed application operations

The gateway and recovery rules are implemented and tested against synthetic
document/email MCP services and Google API fixtures. Google account setup and
trusted Docs/Gmail mappings are bundled; [connect an account](#google-app-connections)
to enable them. `steed_app_list_tools` returns an empty list until accounts are configured.
It requires an active task, but no grant or page. External agents cannot register
connectors, choose endpoints or change credential configuration.

Trusted browser code can supply a `ConnectorRegistry` to `TaskService`, registering
immutable `ConnectorTool` definitions with a fixed effect (`read`, `write`, `send`,
`delete`, `spend`), description, artifact requirement, worker `execute` callback and
optional read-only `reconcile` and destination-validation callbacks. Remote tool annotations do not set risk;
the mock server deliberately claims writes are read-only to test this distinction.
Adapters receive a typed destination and owned draft, not arbitrary client JSON,
URLs, credential paths or recipient arrays. Concrete adapters must validate and
map provider-specific destinations and fields; trusted Python adapters are not
an OS sandbox. They must enforce their own bounded network timeouts.

Discover configured tools first, then request explicit permission, for example:

```python
scope = {
    "actions": ["artifact", "app"],
    "connector_permissions": [
        {
            "connector": "documents",
            "tool": "create",
            "effect": "write",
            "destination": "approved-workspace-id",
        },
        {
            "connector": "email",
            "tool": "send",
            "effect": "send",
            "destination": "approved-recipient@example.org",
        },
    ],
}
```

Each of up to 20 entries binds an exact connector, tool, trusted effect and
destination. Wildcards, unknown tools, risk downgrades and missing required draft
permission are rejected before approval. The popup displays each effect and
destination, disclosing external changes, retained results and cancellation limits.
Browser-page/profile permissions and app accounts are separate; app authority does
not enable website scripts or turn browser cookies into provider credentials.

Store an immutable draft, then call `steed_app_call_tool` with the matching tool,
destination, `artifact_id` when required and a stable `request_id` of 1–64 letters,
digits, underscores or hyphens. The browser assigns a separate globally unique
`operation_id` for provider deduplication/lookup; identical local IDs in different
tasks cannot collide there. Reusing a request ID with matching inputs returns its
receipt, including uncertain results, without redispatch or extra operation cost.
Changing its inputs is rejected. Deleting the draft before dispatch prevents sending
the queued copy; deleting it after dispatch cannot retract external effects.

Poll `steed_app_status`. States include `requested`, `dispatched`, `succeeded`,
`not_applied`, `failed`, `cancelled`, `uncertain`, and `reconciling`. Calls/status
queries spend operation budget; owned cleanup does not. A confirmed result comes
from the trusted adapter's observed response, not simply from dispatch. Provider
exception text is omitted from receipts/events. Outputs are bounded to 16 KiB
UTF-8 plus a reference of at most 2,048 characters. Text/reference stay out of
activity events; they are retained as local plaintext in private `tasks.sqlite`.

There are at most 50 receipts per task, one running worker per task and four
globally. A 30-second timeout requests cooperative cancellation and reports an
uncertain write or failed read. A worker still running retains its capacity slot;
another write or reconciliation cannot race it. `steed_app_cancel`, task endings,
revocation, expiry and shutdown stop queued operations and request cancellation
of dispatched ones. They do not promise rollback. A late observed response may
update the user's receipt after task cancellation, without restoring client access.

After an uncertain operation's worker ends, `steed_app_reconcile` invokes only the
trusted receipt-lookup callback with the original provider operation ID and
destination, without draft bytes or a second write. No automatic write retry occurs.
Initial requests and dispatch gates commit before adapter entry; failed evidence
writes disable access. Restart marks unresolved dispatched writes/reconciliation
uncertain and queued requests cancelled; it neither replays calls nor restores
grants. Recovery stays observable even if a provider acknowledgement was lost.

In **Agents → Task Permissions → Apps**, review owned destinations, states, request
IDs, references and result text, cancel a running operation, or reconcile an
uncertain result while its original task/grant remains active. Finished/interrupted
receipts remain readable by the user. Shared archived receipts can also be checked
under a fresh read-only grant; see [archived receipt checks](agents.md#checking-an-archived-app-receipt).
User-controlled [result clearing](#clearing-saved-app-results) is implemented;
automatic retention policies and additional real providers remain follow-ups.
A client cannot adopt another task's ledger.

### Clearing saved app results

Open **Agents → Task Permissions → Apps** and select a receipt. **Clear Saved Result**
removes its saved provider text and link after confirmation. A **Previous** row
clears the original receipt and its shared recovery summaries. **Clear This Task’s
Results** clears the selected task's own receipts (up to fifty); Previous rows
belong to their original tasks. The confirmation defaults to **No**.

Outcomes, accounts, destinations, request/operation IDs, fingerprints, draft IDs
and the append-only journal remain available for recovery and duplicate-write
protection. The preview shows when the result was cleared; owned MCP receipts
include `result_cleared_at`. Clearing and scrubbing shared summaries commit in one
transaction. Repeating a clear is a no-op. Only trusted browser UI can clear
results; agents have no clearing tool or management authority.

Clearing can also apply to queued/running operations or uncertain receipts.
Operations continue under their existing permissions and their outcome can still
be recorded. A persistent marker prevents later workers or reconciliation from
restoring text/links on the cleared receipt, including after restart. For an
uncertain receipt, a new separately approved read-only check owns its own result;
clear that receipt too if you want to remove its saved text. Completed receipts
with cleared links retain their outcome; use the provider UI to locate their data.
Matching historical writes remain blocked.

Provider-side files/emails, drafts and independent check receipts retain their
own lifetimes. Previously exported/copied data and backups are outside this local
removal; this is not a secure disk erase. Automatic expiry and broader retention
policies remain future work.

### Google app connections

**Agents → App Connections** now provides persistent switches for each connected
account: read/check results, create/add content, direct replacements, sending,
and any supported delete/spend tools. Sign-in alone grants no task authority.
Turn on only the actions you want to allow without another app-task prompt.
Reviewed replacements still show their before/after proposal; direct replacements
have a separate switch. Gmail remains send-only, with no inbox access.

These choices apply to all connected agents across browser profiles, but only
automatically approve **app-only tasks** using private task profiles, optionally
with text-draft permission. Each request keeps exact tools/effects/destinations,
at most ten minutes/1,000 operations/1,000 requests and one session. Browser sites,
shared cookies, binary workspace files and code execution require their own
approval. Follow-ups containing recovered material continue to require fresh task
approval. Larger or combined requests still show the ordinary permission popup.

Turning a switch off clears matching remembered approvals and stops matching
active tasks, including manually approved ones. Existing external changes remain.
Disconnecting clears all switches for that immutable account identity. Reconnecting
creates a new identity with switches off. Choices persist in schema-11 task storage;
restart ends old task authority. A newly added tool does not inherit a previous
switch: the UI shows partial access until you explicitly enable the group again.
The browser's trusted registry determines actual effects; remote tool annotations
or generated package declarations cannot change these choices.

The design borrows the separation of sign-in and persistent read/write policy
from [Muse's connector architecture](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse).
For example, Dropbox's [official Muse setup](https://blog.dropbox.com/topics/company/put-dropbox-project-context-to-work-meta-muse)
describes selecting the connector and signing in to authorize access. Steed's
Google flow is similarly reusable after its one-time app registration, but a
production shared OAuth client is still needed to remove Cloud setup for new users.

Google is the first real provider adapter. The same account store, provider factory,
approval gateway and receipt format can support Microsoft 365 and other trusted
MCP/API adapters. Those additional adapters are not bundled yet; a synthetic second
provider verifies that account setup and task policy do not depend on Google.

To configure Google:

1. Install the optional dependency in Steed's virtual environment:
   `.venv/bin/python -m pip install -r requirements/apps.txt`.
   Your Linux desktop needs a working Secret Service keyring, such as GNOME
   Keyring or a Secret Service provider you already use. Steed has no plaintext
   credential fallback. This is separate from the website password vault.
2. In your Google Cloud project, enable **Google Drive API** and **Google Docs API**
   for documents and/or
   **Gmail API** for sending. Configure the OAuth consent screen; for an external
   testing app, add your Google account as a test user. Follow Google's
   [consent setup](https://developers.google.com/workspace/guides/configure-oauth-consent).
3. Create an OAuth client with application type **Desktop app** and download its
   client JSON. Use a project/client you control; Steed does not bundle a shared
   production client. The import ignores endpoints supplied by the JSON and uses
   fixed Google HTTPS endpoints. Google's [desktop OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app)
   describes client creation and the system-browser/loopback flow.
4. Open **Agents → App Connections**, select **Google Docs and folders** and/or **Send
   Gmail**, expand **Set Up App Access…**, and choose the JSON once. **Open Google
   Setup** links to the Cloud client page. Click **Connect Google**. Steed saves
   the app configuration in your desktop keyring for subsequent connections;
   after this first setup, just select capabilities and click **Connect Google**.
   An existing connected installation can reuse its saved client configuration
   without the original file. Unlock the desktop keyring
   if prompted. Finish consent in your system browser, select the intended Google
   account, then return to Steed. Use a system browser supported by Google's OAuth
   flow; if Steed is your default and authorization is refused, temporarily select
   Firefox/Chrome as your default browser for setup. Steed does not extract cookies
   from your tabs or perform this consent inside its embedded WebEngine.
5. The connected account and enabled tools appear in App Connections. Enable
   local agent access as usual. Agents discover the account-specific connector
   through `steed_app_list_tools`, request exact app permissions, retain a draft
   with `steed_artifact_store`, and send its ID to `steed_app_call_tool`. Connecting
   an account alone never approves a task.
6. Enable the account access switches you want to reuse. Matching bounded app-only
   tasks proceed without a popup. Otherwise, approve the task popup only for the
   account, tool and destination you intend.
   Review results in **Agents → Task Permissions → Apps** and drafts in **Artifacts**.
   **Disconnect Account** disables the connection, denies pending
   requests and revokes active grants using it before attempting keyring deletion.
   If the keyring is locked, unlock it and retry removal; the connection stays disabled.

**Forget Saved App Setup** removes the reusable registration from the keyring;
existing accounts stay connected, but adding another account requires importing
the client JSON again. Disconnecting an account retains this reusable setup.
Importing a client saves configuration even if OAuth consent is later cancelled;
that configuration grants no account or task access. The setup controls collapse
after a successful connection. A brand-new installation still requires the
user-owned Cloud registration above: Steed has no shared production OAuth client.

The requested OAuth scopes are `openid` and `email` for account identity, plus
`drive.file` for documents and/or `gmail.send` for sending. Google defines
[Drive file access](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
as app-created/selected file access and [Gmail send](https://developers.google.com/workspace/gmail/api/auth/scopes)
as send-only access. Steed does not request inbox-reading or whole-Drive access.
Google external apps in Testing issue refresh tokens that expire after seven days
for these non-basic scopes; reconnect when needed. See Google's
[token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).

| Tool | Exact destination | Behavior |
| --- | --- | --- |
| `google_<connection-id>.create_document` | `drive:root` or `drive:folder:<id>` | Create a new Google Doc in My Drive or one exact Steed-created folder; draft title is its name and content is imported as plain text. |
| `google_<connection-id>.create_folder` | `drive:root` or `drive:folder:<id>` | Create one folder; the immutable draft title is its name. The required draft body is unused and is never uploaded. Result text contains the new exact destination. |
| `google_<connection-id>.inspect_folder` | `drive:folder:<id>` | Read that exact Steed-created folder's identity and name, without a draft or a listing of its contents. |
| `google_<connection-id>.inspect_document` | `doc:<id>` | Read one exact Steed-created Doc's title and tab identities, without returning body text or requiring a draft. |
| `google_<connection-id>.read_document` | `doc:<id>:tab:<tab-id>` | Read bounded plain text from one exact tab; no draft or write access is required. |
| `google_<connection-id>.append_document` | `doc:<id>:tab:<tab-id>` | Append the immutable draft body to one exact tab; its title is unused. Existing text is retained. Adds an operation named range visible to collaborators. |
| `google_<connection-id>.replace_document` | `doc:<id>:tab:<tab-id>` | Prepare a complete plain-text body replacement and show a before/after popup. Every change needs trusted user review, including under saved task grants. |
| `google_<connection-id>.replace_document_direct` | `doc:<id>:tab:<tab-id>` | Replace the complete plain-text body without another content popup. Requires its own exact named permission; reusable approvals are supported. |
| `google_<connection-id>.send_email` | `email:recipient@example.com` | Send one plain-text email; draft title becomes its subject and content becomes its body. |

Use the actual connector ID returned by discovery, not the placeholder above.
Mailbox destinations accept a single ASCII address with a lowercase domain;
display names, recipient lists and header injection are rejected before approval.
Local-part case, dots and plus tags remain exact. Arbitrary existing Drive files,
shared drives, editing arbitrary existing documents, rich-text writes, attachments and
mailbox reads are not implemented. Markdown drafts remain literal plain text. Drive
[upload conversion](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
creates the document and imports its text in one multipart request; no second
content write or broad Docs permission is needed.

To organize task output, approve `create_folder` on `drive:root`, retain a draft
whose title is the desired folder name, and call the tool with that draft ID.
Copy the `drive:folder:<id>` destination from its receipt. Request a separate
`create_document` or `create_folder` grant for that exact destination; a root
grant never includes its folders. Before a folder write, Steed checks that the
parent is untrashed, owned by the connected Google identity, writable, outside
shared drives, and carries Steed's account-bound creation metadata. Shortcuts and
unmarked folders are rejected before the mutation. Inspection reveals only the
approved folder's name and identity; it does not enumerate Drive. Reconnecting
the same Google identity and OAuth app retains folder eligibility but requires a new connector
grant. Editing/removing the private metadata can make a folder unavailable.
Google’s [folder API](https://developers.google.com/workspace/drive/api/guides/folder)
creates folders as metadata-only files; Steed performs one creation request.

For collaborative document updates:

1. Take `doc:<id>` from a successful `create_document` receipt. Request
   `inspect_document` with `read` access to that exact destination. Its result
   lists tab titles and exact `doc:<id>:tab:<tab-id>` destinations, including
   nested tabs. It returns no body text.
2. Request `read_document` with `read` access to the chosen tab to see its current
   plain text. Output is capped at 8 KiB of UTF-8 text and 16 KiB of serialized
   result; `truncated` reports shortening and `incomplete` reports omitted tables,
   non-text elements or inline suggested edits. Provider JSON responses have a
   separate 128 KiB limit. These are saved app results and can be cleared in
   **Task Permissions → Apps**.
3. To add content, request `append_document` with `write` access to that exact
   tab plus `artifact` permission. Store the intended body as an immutable draft
   and call the tool with its ID. Inspection does not grant `read_document` or
   `append_document` access, and a grant for one tab does not include another tab.
4. Steed verifies the file's Google account, ownership, untrashed Doc type and
   private creation metadata before reading its tabs. Appending also requires
   writeability, a valid current revision and a supported final paragraph. Docs
   created before the account marker was introduced remain eligible if their
   original private operation marker and ownership still validate.
5. The append inserts a newline followed by the literal draft body immediately
   before the chosen tab's final newline. Existing styles may be inherited from
   the adjacent paragraph. Text and a `steed_append_<operation-id>` named range
   are submitted in one atomic batch with `requiredRevisionId`. A concurrent
   revision change rejects the entire batch as `not_applied`; Steed does not
   reread and resend automatically. Characters Google strips during insertion
   are rejected before any API request so UTF-16 range offsets stay correct.
6. A lost/malformed response leaves an uncertain receipt. **Reconcile Uncertain
   Result**, or a fresh read-only [archived receipt check](agents.md#checking-an-archived-app-receipt),
   looks for the unique operation range in the original tab. It never calls the
   append operation. Missing, ambiguous or removed ranges remain uncertain;
   absence never proves the edit did not happen. A confirmed range establishes
   that the atomic append was applied, not that collaborators left its text unchanged.

Google documents the [atomic batch and revision precondition](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate)
and [tab-aware document structure](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents).
Named ranges are visible to every collaborator with access and can be changed
or removed. They contain an opaque operation identifier, never local task IDs,
credentials or draft metadata. This is recovery evidence, not server-side
idempotency. Reading/checking a document remains possible after it becomes
read-only; appending does not. Documents with more than 32 tabs, malformed tab
identity or unsupported final structures fail closed. Large tab-name results
report `tabs_truncated` if the result budget omits entries.

These tools use the existing `drive.file` scope; enabling Docs API does not add
whole-Drive or broad document access. Opening a file in a normal browser tab does
not authorize its connector use. Existing user-created documents/folders need
explicit selection through a future access flow. Google's
[desktop Picker flow](https://developers.google.com/workspace/drive/picker/guides/desktop-mobile-picker)
requires Drive-only consent, separate from Steed's current identity/Gmail consent;
it is not enabled by these tools. General replacement/editing policy also remains
handled through the reviewed/direct tools described below.

### Reviewed and direct document edits

Use `replace_document` for the default review workflow. It requires an immutable
draft and exact tab write permission, then reads the complete current plain-text
body and revision in a worker. Its receipt stays `requested` with reason
`awaiting_review`; no mutation has been sent. A popup shows the account,
destination, a diff and separate current/proposed text views. **Deny** is the
default. **Apply This Change** authorizes that captured draft and revision only.
Closing/Escape defers the popup; reopen it through **Task Permissions → Apps →
Review Proposed Change**. Cancel the app operation to discard the proposal.
The management window and automatic approval queue share the same user-only
decision method; no agent command can approve a content review.

To delegate edits without reviewing every body, request the separately named
`replace_document_direct` permission. The permission popup explicitly describes
direct replacement and possible formatting changes. **Allow every time** and
**Allow every time on this profile** can save that exact scope through the usual
approval controls. A saved `replace_document` permission still requires each
content review; it never covers `replace_document_direct`, another account or
another tab. Direct permission does not reveal account credentials.

Replacement supports a complete plain-text tab body of at most 16 KiB, with
validated contiguous UTF-16 indices. Tables, inline non-text objects and suggested
content are rejected rather than silently omitted. Body formatting may change;
the tab title and other tabs remain outside the edit. Deletion, insertion and a
`steed_replace_<operation-id>` range run in one revision-bound atomic batch. The
final mandatory newline is preserved. A changed revision/text between preview
and approval returns `not_applied`, requiring a fresh proposal; there is no silent
rebase or retry. Lost replies can be checked read-only using the operation range,
as with appends. Replacing body content can remove older operation ranges, so a
missing historical range cannot establish whether an older update happened.

Pending proposal previews live in a separate bounded schema-9 store, not agent
receipts or activity events. Decision, cancellation, grant termination and restart
scrub their before/after/provider-context text. The immutable agent draft remains
independently retained. Restart never resumes a pending review or approved write.
Clearing saved receipt results still follows the existing retention policy and
does not cancel an operation or pending review.

Connections use immutable provider/subject identities and a new opaque connector
ID on every reconnect, including the same account. Account names and capabilities
live in private `~/.custom_browser/app_accounts.json`; OAuth client credentials,
refresh/access tokens and scopes live in Linux Secret Service. No token/client
path is sent to agents or written to task events. On first use after startup and
on refresh, Steed checks Google's authenticated userinfo subject and email, saves rotated
credentials before writes and fails closed on mismatch or unavailable credentials.
Short-lived access tokens are cached in memory; locking the keyring does not erase
an already cached token. Disconnect disables its use in Steed. Disablement also
prevents refresh workers from restoring removed credentials. Disconnect does not
revoke Google's server-side OAuth consent or undo external effects; manage that
consent separately in your Google Account's third-party connections.

Setup, keyring prompts and API calls run in workers. OAuth uses a random loopback
port, state verification and PKCE; setup waits up to three minutes for consent.
Requests have ten-second socket timeouts, bounded JSON responses and no redirects
or automatic retries. Cooperative adapters check task authority/cancellation
before each request; a dispatched request can still finish after cancellation.
Provider API calls use the connector operation budget, not browser-page request
accounting. A fresh root creation/send needs token refresh, identity verification
and one mutation; a cached-token root creation/send needs one mutation. Folder
writes also verify parent metadata; document appends also fetch file metadata and
the current document revision. OAuth setup is user-owned,
outside task grants.

Google Docs and folders attach the browser's operation ID as a private Drive app property.
Read-only recovery searches that property in the original account/parent destination;
one unique result confirms success. Empty, multiple or incomplete results remain
uncertain: indexing delay, file moves/deletion and provider failure cannot prove
non-creation. Workspace documents cannot use Drive's pre-generated IDs; the
adapter therefore never retries an uncertain create. Gmail send-only scope cannot
verify a lost send acknowledgement, so its receipt remains uncertain and has no
automatic reconciliation. Check Gmail **Sent** before sending another email. The
stable `Message-ID` helps manual identification and is not an idempotency guarantee.
A fresh task can check a user-shared archived Google Doc receipt with exact
read-only permission using [archived receipt checks](agents.md#checking-an-archived-app-receipt).
The lookup uses the original account/parent/operation ID; it does not need draft
bytes or write permission. Gmail remains send-only and requires manual checking.
[Task recovery](agents.md#continuing-previous-work) keeps matching historical writes blocked.

Real Google consent and writes have not been exercised with your account. Synthetic
OAuth/API tests validate the request shapes, identity checks and uncertainty rules;
real-account acceptance needs your configured client and explicit task approval.

### Scoped task uploads

Request explicit source files and upload origins alongside ordinary task sites:

```python
scope = {
    "profile": "private",
    "origins": ["https://example.org"],
    "allow_private_network": True,
    "actions": ["navigate", "inspect", "read", "upload", "submit"],
    "upload_sources": ["/home/yourname/Documents/report.pdf"],
    "upload_origins": ["https://example.org"],
    "max_upload_bytes": 5 * 1024 * 1024,
}
```

The approval popup lists the exact local files, upload sites and staging budget.
Sources must be normalized absolute filenames; symlink components and nonregular
files are rejected. Upload origins must be a subset of the task's allowed origins.
Both the page and form destination need upload authority, even if the destination
already has navigation permission. Existing grants gain no file access.

Inspect the page, choose an input whose operations include `upload`, and call
`steed_page_upload` with its current `session_id`, `snapshot_id`, `target_id` and
approved `source`. Steed copies the source in a worker thread into private storage,
rejecting size violations or changes during copying. It binds that immutable copy
through a fixed native engine operation, without exposing selectors, script or
file bytes to the client. The receipt includes the filename, byte count and SHA-256
fingerprint. Later changes to the original file do not change the selected copy.

Selection does not submit the form. Inspect again for fresh references, then use
`steed_page_submit` with `read` + `navigate` + `submit` permission. Native validation,
hidden fields and the chosen submitter apply; the one-use POST ticket rejects body
replay through a redirect. A loaded response still does not establish account-level
success. File bytes and form values are excluded from journal events.

Support is limited to one ordinary main-document file control per POST multipart
form. Website scripts, multiple files, folder inputs, password controls, frames,
shadow-root file inputs and custom upload widgets remain unavailable. The private
frontend adapter is verified on Qt 6.11.2 and needs checking on engine upgrades.

The default staging budget is 5 MiB, capped at 25 MiB across retained copies in a
session, with at most 20 copies. Copying and selection have a 30-second deadline.
Pending selection checks live authority and is cancelled by stop, revocation or
teardown. Successful copies remain until their session closes; cleanup removes
them. A crash can leave private staging directories; automatic orphan recovery is
not implemented. There is no raw file-reading API or automatic submission retry.

During **Take Control**, select the file input, choose an approved **Source** and
click **Select File**. Return control to let the agent inspect fresh references and
continue; source and destination permissions stay the same.

### Existing signed-in task profiles

An internal client can request `"profile": "existing:work"` to use the existing
**work** profile. Use the identical selector in `session.open`; omitting it selects
`private` and fails a named-profile grant. The profile must already exist under
Steed's profiles directory. Arbitrary paths, aliases outside that directory,
the global-cookie source, and the incognito search profile are rejected.

Approval permits a task and the user to use the **same live Qt profile at the
same time**. Multiple approved tasks can share it too; every task retains its own
grant, pages, sites, budgets, and cancellation. Current tabs, pop-outs, downloads,
and password operations remain available. Rename/archive/delete are blocked
while a task holds the profile, including when it has no open sessions. Approval
failure rolls back the task's profile reference.

Cookies, cache, and site storage are shared, rather than copied. Requests can
send existing credentials; site changes persist and can change the shared
signed-in session. The popup discloses this. Cookie inspection, configured cookie
inheritance, and ordinary ad filtering remain active.

Private sessions use a profile request guard. Shared-profile task pages use a
page request guard after ordinary profile filtering. Task pages disable document
scripts and new workers. Before navigating a shared-profile page, a hidden,
in-process DevTools adapter enables that target's service-worker bypass. Existing
workers therefore cannot serve or fetch on behalf of its document requests;
ordinary user pages retain their service workers. Initialization failure, timeout,
frontend reload, or renderer termination disables task navigation. The adapter
uses fixed browser code and exposes no debug listener or client protocol access.
Its private frontend SDK names are verified against Qt 6.11.2 and require
rechecking on engine upgrades, as does the cookie snapshot adapter. See
[the protocol's worker-bypass command](https://chromedevtools.github.io/devtools-protocol/1-3/Network/#method-setBypassServiceWorker).

Task budgets apply to task-page requests. Unrelated user/worker traffic belongs
to ordinary browsing and does not spend those budgets. This is shared account
state, not isolation from the user's simultaneous changes. Revocation closes only
that task's pages; other tasks and user browsing continue without reselecting the
profile. Ending a grant cannot undo requests, account effects, or storage changes.

Qt shares persistent data among pages using a profile and applies profile
interception before page interception; see the
[Qt profile documentation](https://doc.qt.io/qt-6.11/qwebengineprofile.html) and
[page interception documentation](https://doc.qt.io/qt-6.11/qwebenginepage.html#setUrlRequestInterceptor).

### Grant enforcement and persistence

`allow_private_network=false` permits public literal IP addresses only. Private literal addresses require explicit `true`. DNS hostnames infer `true`
when the flag is omitted; this is disclosed in the approval dialog. Explicit
`false` remains enforced. The [Qt request-info API](https://doc.qt.io/qt-6.11/qwebengineurlrequestinfo.html)
provides URLs but no resolved peer address; Steed therefore treats a DNS name's
network route as unverified. Allowing this flag still restricts requests to the
listed origins; it does not add other sites or ports. There is no DNS pinning,
OS/network sandbox, or control over tools used outside this application.

Approval installs an immutable live grant only after its journal transaction
commits. Command checks happen at dispatch, and a profile or task-page
[Qt request interceptor](https://doc.qt.io/qt-6.11/qwebengineurlrequestinterceptor.html)
also checks page requests and request budgets. It uses live policy without
calling engine objects or SQLite from its callback. Denials record reason codes;
denied URL queries are excluded from those events. Broader access requires a new reviewed request. An approved replacement ends
prior authority and closes prior task pages; repeating an unchanged request is
idempotent. Stop, close, and terminal task transitions remain
available for cleanup without spending operation budget.

Revocation cancels the task and disposes its pages. Expiry and exhausted budgets
end only that grant: its pages close, running workers and queued app calls are
cancelled, and the task stays active so the agent (or a bound conversation) can
request fresh access. Pending reviews remain and recheck authority on approval.
Expiry uses a monotonic deadline, checked on requests/commands and by a Qt timer.
Queued mutations then fail their authority checks. Cancellation cannot undo a
request already sent to a website. Controller approval/denial/revocation methods
are Qt-thread methods deliberately absent from the client command surface.

`tasks.sqlite` has owner-only permissions. It persists objectives, task states,
URLs/titles, grant scopes/decisions, and event metadata; private engine
cookies/storage stay in memory. Schema 4 adds retained artifacts and app receipts to the task database
without replacing its journal.
Task state transitions and their events are transactional. SQL triggers prevent
updating/deleting journal entries. Active tasks become `interrupted` when the
service shuts down or starts after an interruption; pending and approved grants
end too. Pages, commands, and approved authority are never
restored/replayed. An asynchronous journal write failure disables further task
commands and emits `service.failed`; cleanup still disposes engine resources.

Active document execution and script-handled forms remain future
work. Watching and scoped user handoff use the existing task page.
Existing-profile grants support the same restricted
retrieval, metadata, content reads, control edits, and native scriptless forms as
private task pages, with the corresponding explicit permissions.

### Active-content networking audit

`tests/smoke_task_active_content.py` deliberately enables JavaScript in synthetic
task pages to identify missing boundaries. The application and grant API still
keep website scripts unavailable. This experiment uses temporary profiles,
loopback HTTP/WebSocket destinations, a UDP STUN receiver, and a TURN/TCP
connection listener, without real
accounts or external services. It requires working local TCP/UDP networking.

The Qt 6.11.2 / Chromium 140.0.7339.225 baseline results are:

| Probe | Private task with forced scripts | Existing-profile task with forced scripts |
| --- | --- | --- |
| Approved-origin GET | Reached fixture | Reached fixture |
| Unapproved-origin fetch | Blocked | Blocked |
| General POST | Blocked | Blocked |
| Blob dedicated-worker fetch to unapproved origin | Blocked | Blocked |
| Blob shared-worker fetch to unapproved origin | Blocked | **Reached fixture** |
| Shared worker constructed through a fresh `about:blank` iframe | Blocked | **Reached fixture** |
| Unapproved WebSocket handshake | Blocked | Blocked |
| WebRTC STUN packets to another port | **Reached UDP receiver** | **Reached UDP receiver** |
| WebRTC TURN/TCP connection to another port | **Reached TCP listener** | **Reached TCP listener** |

Both scriptless task controls produced no tested active-content requests, UDP
packets, or TURN/TCP connections. Ordinary user pages reached all positive-control destinations, including
while a task shared their profile. These measurements cover the specific fixtures,
not every transport, redirect, realm, or Chromium version.

A separate fixture-controlled CSP probe used `worker-src 'none'`, restricted
`connect-src`, and `webrtc 'block'`. It blocked the shared-worker request, but the
engine reported the `webrtc` directive as unrecognized and still sent STUN packets.
The fixture's meta policy is a capability probe, not shipped enforcement for
arbitrary documents. HTTP interceptors or a CSP worker policy alone therefore do
not establish the required active-script boundary. Main-world JavaScript wrappers
also need proof against fresh iframe realms and cannot serve as the sole boundary.

Before introducing a `scripts` grant, establish native enforcement for workers,
child contexts and non-HTTP transports without restricting concurrent user tabs.
Keep script-enabled forms denied until serialized-body/handler semantics have a
separate enforceable design. A new script authority must explicitly describe
website code's access to the selected profile's storage and account state.

### Native networking prototype

`tests/smoke_task_native_policy.py` reuses the audit with experimental per-target
Chromium controls in the isolated task frontend. It is not imported by the
application and does not introduce a website-script grant. Its results on the
bundled engine are:

| Candidate | Fresh-iframe/shared workers | STUN UDP | TURN/TCP connection |
| --- | --- | --- | --- |
| `Fetch.continueResponse` adding worker CSP, 100% packet loss, zero latency | Existing-profile worker reached fixture | Reached receiver | Reached listener |
| `Fetch.fulfillRequest` with worker CSP, 100% packet loss, zero latency | Blocked | Reached receiver | Reached listener |
| `Fetch.fulfillRequest` with worker CSP, 100% packet loss, 1 ms latency | Blocked | Blocked | **Reached listener** |
| Fulfillment with worker CSP and `Connection-Allowlist`, no packet loss | Blocked | Reached receiver | Reached listener |

Ordinary user positive controls, including concurrent browsing on the task's
profile, remained available. The prototype coordinates interception through the
frontend SDK because registering another Fetch dispatcher raced its default
response continuation. Private pages are initialized before attaching the
frontend so a protocol target exists. Fulfillment retrieves/replaces fixture
bodies; production acceptance still needs byte/encoding preservation, streaming,
existing response policy/cookies, redirects, cross-process frames, and adapter
failure/revocation tests. Its size check runs after native buffering and is not
a response-memory boundary.

The [Chromium protocol](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Network.pdl)
describes WebRTC packet-loss emulation, but the measured TCP connection proves
that dropping payloads is insufficient for Steed's exact-origin authority.
The newer [Connection Allowlist design](https://chromium.googlesource.com/chromium/src/+/main/docs/connection_allowlist_design.md)
includes native WebRTC restrictions. The prototype tries
`Connection-Allowlist: (response-origin);webrtc=block`; it did not contain RTC
on the bundled engine. A compatible engine/native hook or stronger isolation
must pass the networking gate before active-script support can proceed.

Run the native prototype and its comparisons from the activated virtual environment:

```bash
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py --delivery continue --latency-ms 0
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py --connection-allowlist --packet-loss 0 --latency-ms 0
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python tests/smoke_task_native_policy.py --require-containment
```

Normal runs exit zero when audit controls complete, even when containment fails.
`--require-containment` exits nonzero when a tested route escapes; it currently
fails because TURN/TCP reaches the unapproved listener. A future passing result
covers these fixtures only and does not establish full active-script acceptance.

### Active-script engine decision

Use native per-document connection enforcement in the same embedded engine and
actual profile as the user's tabs. The selected direction combines
`Connection-Allowlist` with `webrtc=block`, a worker-blocking CSP, and the existing
live-grant request checks. This preserves collaborative account/storage access
without changing ordinary tabs' networking.

Chrome documents stable Connection-Allowlist support starting in
[Chromium 152](https://developer.chrome.com/blog/fedcm-connection-allowlist).
The installed Steed engine is Qt 6.11.2 / Chromium 140.0.7339.225; its failed RTC
probes remain the embedded-backend baseline. The latest published
[PyQt6-WebEngine-Qt6 package](https://pypi.org/project/PyQt6-WebEngine-Qt6/) checked
October 1, 2026 is 6.11.2, so reinstalling the current dependencies does not
provide the required Chromium generation. A compatible Qt engine/native hook or
backport is required before production integration.

| Approach | Decision |
| --- | --- |
| Native policy inside the shared embedded engine | Selected; needs a compatible engine and trusted policy-delivery integration |
| A separate external Chromium process for tasks | Does not share the actual live `QWebEngineProfile`; rejected for current collaboration requirements |
| Disable RTC/networking across the shared profile | Would restrict the user's ordinary tabs; rejected |
| Packet loss or JavaScript constructor wrappers | Insufficient connection boundary; the bundled-engine probes expose TCP/realm gaps |

`tests/smoke_native_connection_policy.py` proves engine feasibility using the
locally installed Chromium 153.0.8010.36. It starts a separate headless test
browser through anonymous DevTools pipes, with a temporary synthetic profile
and loopback HTTP, UDP STUN, and TCP TURN listeners. It neither accesses Steed's
profile nor installs an application backend or public debugging endpoint.

```bash
.venv/bin/python tests/smoke_native_connection_policy.py
.venv/bin/python tests/smoke_native_connection_policy.py --chromium /usr/bin/chromium
```

The restricted document's fixture server supplies a native connection allowlist
covering two exact loopback origins, `webrtc=block`, and `worker-src 'none'` before
website code runs. Unapproved fetch/WebSocket requests, shared/dedicated workers
(including a fresh initial iframe realm), STUN UDP, and TURN/TCP connections were
blocked. An allowed cross-process iframe ran the same probes. Approved-origin
GET and synthetic POST reached the server; cross-origin response access still
follows ordinary CORS rules. Connection policy does not provide method/body
authority: production still needs the existing request filter, grant checks,
and a separate script-handled form design.

Ordinary pages sharing the synthetic profile reached all positive-control
destinations before and during restricted browsing. Both tabs shared cookies,
local storage, and a live storage update. Native policy survived detachment of
the control session; task closure disposed its owned frame targets while the
ordinary tab retained access to storage. The diagnostic exits nonzero if a
positive control is missing or the containment checks fail. A passing
`tested_connection_policy_contained` covers these fixtures, not all transports
or Steed's missing adapter.

Next, obtain a compatible embedded engine and implement trusted policy delivery
for every task document/frame before its code executes. Preserve response bytes,
encoding, streaming, cookies, and existing CSP/connection-policy restrictions;
policy merging must not broaden a server restriction. Cover redirects,
initial/child realms, service-worker bypass, unknown or malformed policy, adapter
failure, revocation, and exact grants in the real shared-profile backend.
Fail closed when delivery or verification fails. Keep website scripts and
script-handled forms unavailable until those acceptance checks pass.

### Bounded engine feasibility assessment

Decision on October 1, 2026: defer agent website-script support and continue
with the current supported operations. Keep the Qt browser and its shared-profile
model; no dependency replacement, engine fork, or profile migration was performed.

The installed engine and failed embedded RTC probes establish the current gap.
Qt's [separate-release announcement](https://www.qt.io/blog/qt-webengine-will-release-separate)
states that the first Qt WebEngine 6.140 release accompanying Qt 6.12 still uses
Chromium 140. A newer Chromium baseline is planned later, with its exact feature
support still requiring verification. No compatible packaged Qt/PyQt build was
identified in this assessment.

The inspected [Qt request API source](https://raw.githubusercontent.com/qt/qtwebengine/dev/src/core/api/qwebengineurlrequestinfo.h)
provides request blocking/header changes, without the required document-response
policy hook. Chromium's [native policy design](https://chromium.googlesource.com/chromium/src/+/main/docs/connection_allowlist_design.md)
requires context identifiers across browser, renderer, and network-service code,
frame/worker propagation, registration before resource loading, and restoration
after network-service failure. I infer that porting the complete feature and
exposing a trusted Qt policy hook is substantial native-engine work, rather than
a small Python adapter change. A bounded, maintainable backport was not established;
no custom engine compilation or complete patch/dependency audit was attempted.

Revisit this decision when a maintained build offers the required enforcement or
a reproducible, supportable integration is demonstrated. Verify the actual feature,
policy delivery, and shared-profile fixtures before granting scripts. A whole-browser
engine migration would require its own compatibility and maintenance proposal.
