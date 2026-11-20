# Agents in Steed

Steed works with any assistant: a model it runs itself (local or with your own
API key), Codex, Claude Code, any ACP agent, or any MCP client. Each gets the
same tools and asks you the same way. This page covers the agent sidebar, those
chats, and local MCP access. [Documentation index](README.md).

## Any model, nothing else to install

Open the agent sidebar, then **Settings and tools → Use any model…** (or **Use
any model** under Start a chat). Pick a service, then a model, and add it:

- **On this computer**: Ollama (`http://localhost:11434/v1`), LM Studio or a
  llama.cpp server. No key needed. **Find models** lists what you have pulled.
- **A hosted service** with your own API key: OpenAI, Anthropic, Google Gemini,
  OpenRouter, or any other OpenAI-compatible address (it must use https).

The key goes to the desktop keyring (install `requirements/apps.txt`), never to
`config.json`. Settings keep only where to find it: `keyring`, `none`, or
`env:NAME` for a key in an environment variable. A model you add becomes the
agent for new chats until you pick another.

Steed runs the conversation itself and gives the model its own MCP tools,
bound to the chat's task, so the model asks for permissions, gets approvals
and leaves receipts exactly like any other agent. It gets no files, terminal or
network of its own. **Browsing tools** (the default) offers the tools for
reading and acting on pages, permissions, watches, routines, settings, sign-in
and customization; **All of Steed's tools** adds files, apps, workers and
panels for larger models. Tick **It can look at screenshots** for models that
take images. A turn stops after 40 steps; older tool results are shortened as
a chat grows.

## Staying in charge

These hold for every agent and model, whatever its permissions, and even with
unsafe auto-approval on:

- **Pause All Agents** (Agents menu, `Ctrl+Alt+P`, or **Pause all agents** in
  the sidebar) holds every agent at once. While paused, agents can only read
  their conversation and check their task; anything else returns
  `agents_paused` and does nothing. Routines that come due wait and run after
  you resume. The menu button shows **⏸ Agents paused**, and each active task's
  journal records the pause.
- **Payment cards are yours alone.** Card fields (`autocomplete="cc-…"`, or
  named like a card number or security code) are treated like passwords:
  agents see them as `sensitive_field` with no actions, and cannot submit a
  form that has one. A site that charges a saved card with a single button is
  not caught by this, so keep submit permission for sites you trust.
- **Page text aimed at agents is pointed out.** When a page contains text that
  seems to instruct an AI ("ignore your previous instructions…", "note to AI
  agents…"), `page.inspect` adds `suspected_agent_instructions` to its
  limitations and quotes it in its guidance as page content, not the user. The
  journal records it. It is a reminder, not a filter: the text is still there.
- **A routine that meets a sign-in page hands it to you.** Steed notifies you
  (click to open the page) and gives the agent a note in place of the login
  page; sign in and the next run reads it. Watches already did this.

## Replaying what an agent did

**Agents → Task Permissions… → Steps** lists every step of the selected task
in plain words: opened a page, typed into a field, clicked, sent a form, was
refused, signed in with your saved login. The journal never holds page text,
so the steps say what kind of thing happened, not what the page said.

Turn on **Keep a picture of each agent step** (Settings → Agents) and Steed
also keeps a picture of the page just after each action, navigation and newly
opened page; choose a step to see it. Pictures are taken only of pages on
screen (a tab you lent the agent, or one shown with `session.watch`); Steed
never renders a hidden page to photograph it, so background work has steps but
no pictures. They are saved as small JPEGs in `task_replays/<task>` under
Steed's settings (readable only by you), at most 300 per task, until you press
**Delete Pictures**. Agents cannot change this setting.

## Local agent access (MCP)

### Daily browser access and permission diagnostics

Restart Steed and start a new agent conversation after updating the tool catalogue.
This applies to native Codex and other MCP clients. Steed now exposes 85 tools.

Ordinary DNS names no longer need an extra flag just to request access. Omitting
`allow_private_network` with named origins includes that permission in the review;
the popup still explains that DNS can resolve to private addresses. Explicit
`false` remains enforced, and private literal IP addresses still need explicit
`true`. Origins remain exact, without paths, credentials or wildcards. Connector
HTTP/DNS protections are unchanged.

Grant errors report stable codes, maintained explanations and a field when known,
such as `invalid_scope` / `origins` or `invalid_app_scope` /
`connector_permissions`. They never echo arbitrary exceptions, submitted secrets
or storage paths. Exact repeated requests return the existing pending/approved
grant. A different scope can request replacement while the old grant remains
usable; approval atomically ends the old grant and closes its task sessions.
Reopen/adopt pages after replacement. A different pending request must finish
before another can be queued. The task database migrates to schema 21.

For an isolated, interactive website, a minimal screenshot-control request is:

```json
{
  "scope": {
    "profile": "private",
    "origins": ["https://example.com"],
    "actions": ["computer"]
  }
}
```

Send this to `steed_grant_request`, then poll `steed_grant_list`. `computer`
includes its required `read`, `visual`, `live`, `navigate`, `inspect` and `attach`
permissions in the same visible review. Open `steed_session_open` with
`profile="private"`, navigate and wait for readiness, then use
`steed_session_watch`. A live grant makes omitted session mode default to `live`;
explicit `mode="restricted"` retains script-disabled retrieval. Without `live`,
the default remains restricted. New private sessions use separate off-the-record
storage, with normal website JavaScript/background traffic in live mode. They do
not access the user's incognito tabs. In Watch, Take control pauses the agent and
allows direct interaction with a private live page; Return control resumes it.

To take over the user's current tab, call `steed_browser_request_control` with a
stable `request_id` and `selection="current_tab"` (or `profile_tabs`, up to ten
website tabs). One popup shows the task, profile, selected tabs and sites. Poll
`steed_browser_control_status`, then use `steed_browser_tabs` and
`steed_session_adopt` after approval. The selected profile/sites are pinned;
changed previews become `stale`. Take Back returns tabs immediately. Status becomes
`returned` after all selected tabs are returned, and repeating an old request ID
never retakes them. The sidebar's **Give agent tab control…** remains available.

When DOM actions are missing, inspection includes guidance distinguishing a
restricted page from custom/canvas controls. `steed_page_capture` returns an image
and `capture_id`; `steed_page_click` takes that ID plus `x`/`y` pixels measured
from the returned image's top-left corner. `steed_page_type` enters literal text
into the focused website control; `steed_page_key` supports a maintained key enum
including Enter, Tab, Escape, arrows, PageUp/Down and SelectAll. Each image ID is
usable once, expires after 60 seconds and is checked against navigation, scrolling
and viewport geometry. Capture again after each action. These controls operate
inside the website, including visible embedded content, and can submit data or
change accounts. They do not target desktop applications or browser chrome.
Native input uses the [Chromium Input protocol](https://chromedevtools.github.io/devtools-protocol/tot/Input/)
through Steed's existing private frontend; no debug listener or caller scripts are
exposed. A dispatched action is not proof of website success. Inspect its result;
timeouts may leave an uncertain outcome and must never trigger automatic replay.

### Unsafe automatic agent approval (YOLO mode)

Enable **Agents → YOLO mode: auto-approve agent requests (unsafe)** and explicitly
confirm the warning. This affects every connected agent and profile for the
current browser run. The status bar and sidebar mark the mode; it is off again
after restarting Steed. Agents cannot enable it through MCP. Disable it from the
same menu without another confirmation.

While enabled, Steed automatically resolves permission grants, tab takeover,
generated-worker execution, connector recipe installation, account payload
reviews and panel/layout/start-page proposals. Account writes and sends can occur
without payload review. This can be unsafe, especially with untrusted agents or
website instructions. Take Back ends the current handoff, but a new request can
be auto-approved while YOLO remains enabled; turn it off or cancel the task to
prevent that. Existing grants remain explicit and revocable in Task Permissions.

YOLO changes approval, not supported operations or validation. Account sign-in,
enabled library folders, exact destinations, ownership, sandbox/resource limits
and no-replay rules still apply. Website device permissions keep their separate
profile rules; external agents keep their own host permissions and tools. It never silently selects archived private task
data: an unspecified recovery source picker is declined, so choose historical
material with normal review before enabling the mode. Native file pickers/new
windows remain limited on task-owned pages; external agents retain their own tools
for work outside Steed. The read-only HTML data bridge stays deferred.

The optional `agent_mcp.py` adapter exposes the existing task operations over
stdio or loopback Streamable HTTP using the
[official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
It connects to the running Steed process; Qt pages and profiles stay in that
process. Local access is off when the browser starts.

1. Install the optional dependencies from the repository root:

   ```bash
   .venv/bin/python -m pip install -r requirements/mcp.txt
   ```

2. Start Steed and select **Agents → Enable Local Agent Access**. This enables
   local connections for the current browser run; it grants no site access.
3. Configure your MCP client to launch the adapter. For clients using an
   `mcpServers` configuration, substitute the absolute repository paths:

   ```json
   {
     "mcpServers": {
       "steed": {
         "command": "/absolute/path/Browser/.venv/bin/python",
         "args": ["/absolute/path/Browser/agent_mcp.py"]
       }
     }
   }
   ```

   The adapter normally reads `~/.custom_browser/agent-access.json`. A custom
   browser data directory can be selected with `--discovery /path/agent-access.json`.
   The browser must already be running and opted in. No terminal is needed for
   the GUI; the MCP client manages the adapter process.
4. Have the client call `steed_task_start` with an objective, then
   `steed_task_capabilities` and `steed_browser_context`. Context reports the
   active browser window's profile, or the last-used window when another app has
   focus. Use its `profile_selector` (for example `existing:work`) to request
   shared access through `steed_grant_request`; otherwise use `private`.
   A scope example is:

   ```json
   {
     "scope": {
       "origins": ["https://example.org"],
       "actions": ["navigate", "inspect", "read"],
       "profile": "existing:work",
       "allow_private_network": true,
       "duration_seconds": 600
     }
   }
   ```

   Hostname network permission is included in the review when its flag is omitted;
   explicit false remains enforced because Qt cannot pin DNS routes. The named
   profile must exist. Use private for a new isolated session.
5. Read the nonmodal approval popup and choose **Approve** or **Deny**. The client
   polls `steed_grant_list` for the decision, then opens a session with the exact
   approved profile. You can keep browsing on that same profile.
6. Revoke/cancel through **Agents → Task Permissions**, or disable local agent
   access to cancel connected tasks. Closing the adapter cancels its unfinished
   task. Use `steed_task_finish` after completing a workflow.
   The same connection can then start another task; it receives a new identity
   and must request fresh permissions. An active task cannot be replaced.

### External-agent conversation sidebar

**Agents → Agent Connection Setup…**, also available from the sidebar's
**Connect another agent** button (or **Settings and tools → Connection setup**
during a conversation), opens a nonmodal setup screen. It enables local
access only when you click **Enable Local Agent Access**, then offers **Copy
Connection Settings** and **Copy Starter Prompt**. The settings use the running
Python interpreter, repository and browser data directory, including paths with
spaces. Add the Steed server entry to your client's existing configuration;
clients without `mcpServers` JSON can use the displayed command and arguments.
Opening the screen or copying settings creates no task, launches no agent and
grants no page/app access. The screen never reads or copies gateway tokens. If
the optional MCP SDK is absent, it shows a one-time installation command; it
does not run the installer. The enable state follows the existing Agents-menu
switch and resets on browser restart. Seven setup regressions plus existing
agent/sidebar checks passed together (53 tests in 13.277 seconds); the final
setup screen was visually inspected and ordinary startup/render/shutdown passed.

Steed uses external agents first. Codex chats run in a dedicated external Codex
CLI process; Steed sends turns through its App Server interface. Other clients
can still use the task mailbox directly. Steed does not store model credentials.

#### Sidebar setup and everyday use

The Agents page has two parts: your chats and the open conversation. In a tab
(or a wide sidebar) the chats sit in a column on the left, like other chat
apps; in a narrow sidebar they fold behind **Chats** at the top. The list shows
your five most recent chats, newest first, each with what it was about (its
first message, or its saved title), the agent and when it was last used; the
rest are behind **N more**. A chat that was continued is listed once, as the
chat that carries it on. **+** starts a new chat.

With nothing open, the page offers **Start** with the agent to use (Codex,
Claude Code or an ACP agent you added), **Continue a Claude Code session** and
**Connect another agent**. **Settings and tools** (collapsed, under the list)
holds the agent and model for new chats, connection setup, the file library,
sidebar/tab presentation, the open chat's permissions, files and receipts,
detailed counts and **Clear chat**. Opening it creates no task or permission.
Pending approvals and unanswered agent questions stay visible above the chat.
Private browsing hides these controls and clears regular-profile text and
drafts. Hiding the sidebar keeps chats running; stopping one is explicit.

Your messages appear in a bubble on the right; the agent's replies are text
with its name above. Replies are drawn as Markdown: paragraphs, headings,
lists, tables, `code` and code blocks, bold and italic. They are built from
plain text nodes, never parsed as HTML, so markup in a reply shows as text and
links are shown, not followed. The message box grows as you type; **Enter**
sends and **Shift+Enter** starts a new line (**Ctrl+Enter** also sends). The
round Send button lights up once there is something to send.

The activity card is one line (state and connection) once a conversation has
messages; click it for the model and details. It stays open before the first
message and while the connection needs attention. It shows **Sending**,
**Thinking**, **Working**, **Replying**, **Needs your input** or **Ready** for
chats Steed runs, with a thin busy indicator and queued follow-up count. These
are transient runtime states; they do not expose reasoning text or establish
that an external action succeeded. Other MCP clients show **External agent
connected** because Steed cannot observe their execution.

When a chat is no longer live, the card says why: **Chat closed** (Steed closed
it: it quit, or agent access was turned off), **Stopped after an error**,
**Chat ended** or **Chat stopped** (you stopped it). Instead of a message box,
the chat then offers **Continue this chat** (when its conversation was saved,
see below), **Start a new chat** and **Hand off to another chat…**. Nothing
replays a queued message or resumes a turn. When the runtime has reported
anything, the card ends with **Last agent update HH:MM:SS**, a transport fact
only. Messages the agent never read from a stopped chat show as **Not read by
agent**.

Steed closing a chat, idle or mid-turn, is recorded as **interrupted**, never
as a cancellation you did not ask for; so is a turn cut off by a dropped
connection or protocol failure. `TaskService.interrupt_task` keeps a partial
reply, marked **cut off before finishing**. A chat that failed before any turn
was sent is still recorded as cancelled. Interrupted chats stay available to
**Continue previous work?**, which copies material into a new task with fresh
grants.

The card also shows **Model: …**, using the resolved model and
provider returned by the running App Server thread. A reported model reroute
updates the name and shows the previous model for that turn. **Model** in
Settings and tools selects it for future chats; changing it does not
change the selected conversation. When the runtime reports no valid model (and for other MCP clients), no model is
shown, rather than a guess from the selector.
After a chat ends, its name is marked **last reported** for this browser run;
these transient reports are not restored from old chat history after restart.

The maintained interface lives in `ui/web/agents.html`, `agents.js` and
`theme.css`. There is no frontend installation/build step or localhost server.
`ui/html_surface.py` loads those assets into its own off-the-record profile,
with network/navigation/file access denied and a small QWebChannel command API.
Python validates commands against the current document and selected conversation;
all browser/task authority remains in the existing services. User/agent-editable
start pages and remote websites do not receive this bridge.

If the interface fails to load or its renderer exits, **Reload interface** restores
the selected conversation and local draft received by the controller. It does not
replay sent messages, start another task, or repeat browser/app actions. Browser
windows retain independent unsent drafts. Private-profile changes immediately
cover the previous view and clear conversation text and drafts before showing the
private state. Light/dark, keyboard, bridge, draft, failure, privacy and sidebar/tab
checks use temporary profiles and synthetic messages in
`tests/test_html_agents.py`.

#### Codex chats from Steed

1. Install a current [Codex CLI](https://developers.openai.com/codex/cli) and sign
   in once with `codex login`. The integration has offline checks with Codex 0.159.2 and 0.160.0 and
   uses its App Server protocol and bundled code runtime. Older runtimes
   may be incompatible; failed setup leaves a cancelled, read-only task.
2. Install Steed's optional adapter dependencies:
   `.venv/bin/python -m pip install -r requirements/mcp.txt`.
3. Open **Agents → Agent Sidebar** (`Ctrl+Shift+A`) and choose **Start** with Codex
   (or the **+**).
   This explicitly enables local agent access for this browser run and starts a
   new task. No MCP client configuration or starter prompt is required.
   Before starting a chat, **Settings and tools** contains the model selector. It can
   use your Codex default or request GPT-5.4 for this chat
   without changing Codex's saved configuration. Direct Steed tools were verified
   with GPT-5.4 and code-runtime Steed calls with GPT-6.1 Sol in offline native
   fixtures; your account must still provide the selected model.
4. When the status says **Codex is ready**, type a message and press **Enter** or
   **Send**. Codex starts a turn automatically. Follow-ups queue in order while
   a reply is running, then continue the same live Codex thread and Steed task.

Codex uses its own account/configuration and model selection. Its account
authentication stays with Codex; Steed neither copies credentials nor handles
login/logout. Model requests use that account and its limits/billing. Up to two
Steed-started Codex chats can run together; all browser windows share their task
state while keeping unsent drafts local to each window. Opening the sidebar or
setup screen alone never launches Codex.

Steed preserves Codex's normal tools, configured MCP services, sandbox and
approval policy. It enables the bundled code runtime used by newer models and
adds one task-bound Steed MCP connection. Codex can research, code and use its
own integrations independently of Steed. Its normal capabilities are governed by
Codex's settings; Steed is a communication and browser-tool provider, not an OS
sandbox around the external agent. An installed native runtime is a trusted local
program with the same access it normally has outside this browser.

The dedicated task-bound Steed server uses Codex's `approve` tool-admission mode.
The former `auto` setting could reject tools before they reached Steed, including
resource discovery and permission requests. Only this new Steed server entry is
changed; inherited server policies and Codex's sandbox/OS approvals remain native.
Admission does not grant browser, connector, worker or write authority: Steed's
scope checks, enabled-folder decisions, approval popups and payload reviews still
apply. The values are documented in the official OpenAI
[Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

Each chat uses a dedicated CLI process and new ephemeral thread. A private,
single-use task capability binds its Steed connection; Steed verifies that task
before enabling Send. Malformed/oversized output, foreign task/turn identities,
startup failure and disconnect stop delivery without automatic replay. Runtime
stderr, account details and message bodies are not added to activity events.

Native Codex questions and command/file/network approval requests appear as
nonmodal popups while the selected chat is visible. **Answer agent (N)…** opens
pending requests when the sidebar was hidden. **Allow once**, **Allow for this
chat** and **Deny** answer native requests; questions accept an option or text.
These answers do not grant any Steed browser or connector authority. Codex's own
policy determines when it needs approval; Steed does not request an extra prompt
for actions already allowed by that policy. Clearing Steed chat does not erase
Codex's active thread, local files or external effects.

Messages still grant no Steed page, profile, connector or worker access. Codex
requests the same Steed permissions and payload reviews as any other MCP agent;
expand **Settings and tools** for **Permissions**, **Task files**, and **Receipts**. Replies
are streamed as plain text, coalesced and split at UTF-8 boundaries within existing
chat limits. No message bodies, account details or runtime stderr enter activity
events. **Clear Chat** removes queued prompts and suppresses the rest of an erased
reply; text already sent cannot be recalled from the external agent.

#### When an agent says discovery was rejected

There is no separate discovery switch for an active Steed connection. Agents can
call `steed_task_capabilities`, `steed_browser_context`, `steed_library_folders`
and `steed_app_list_tools` with `{}` before requesting a grant. Task identity is
bound to the connection; supplying `task_id` is invalid. Empty folder/connector
lists mean nothing is enabled/available. Enable the intended folder in **File
library** or connect the service in **App Connections**; discovery itself does
not connect accounts or expose disabled folders. Enabled library folders already
permit file access without repeated task grants.

`steed_browser_tabs` has a different requirement: use **Give agent tab control…** and
approve live/read access first. Rejection of that call does not disable library,
connector or capability discovery. Other browser/connector access uses
`steed_grant_request` with the exact scope, then `steed_grant_list` to check approval.
Failures report a known tool name and code where available; journal diagnostics
record only command, code and validation/execution stage, never argument values
or exception text. Restart Steed and start a new Codex chat to load changed native
connection settings/instructions. Existing conversations keep their original
runtime configuration.

Hiding the dock keeps the chat running. **Cancel Task**, disabling local agent
access, ending the task or closing the last browser window stops the dedicated
child and its adapter. This never controls another Codex instance or its daemon.
Restart/disconnect retains Steed history but does not reconnect, retry turns,
replay queued text or resume external writes. Start a new chat with fresh task
permissions; historical conversation is not automatically sent to it.

#### Claude Code chats from Steed

Install [Claude Code](https://docs.claude.com/en/docs/claude-code) and sign in by
running `claude` once. Choose **Claude Code** next to **Start** (or in **Settings
and tools → Agent for new chats**), optionally with Opus, Sonnet or Haiku, then
start a chat. Steed runs `claude -p` with the stream-json protocol and adds only
a bound Steed MCP server; your Claude Code settings, other MCP servers, tools and
permission rules stay in effect. Steed's own tools are pre-allowed in Claude Code
because Steed authorizes each capability itself; starting another task and the
conversation-reply tool are disabled. When Claude Code asks permission for one of
its own tools (for example Bash or a file edit), Steed shows the request: **Allow
once**, **Allow for this chat** (applies Claude Code's suggested session rule),
**Allow all commands for this chat** (for commands: Claude Code's own session
rule for every Bash command, until the chat ends) or **Deny**. Claude Code's
questions appear as answerable prompts. Replies stream into the sidebar; text
from subagents is not shown as the reply. A failed turn, an exit or an MCP
connection failure stops the chat without retrying.

**Keeping chats so they can continue.** With **Settings → Agents → Keep agent
chats so they can continue after Steed closes** on (the default), each new
Claude Code or Codex chat runs in its own folder,
`~/.custom_browser/agent_chats/<task>`, and the agent saves it in its own
history as usual: Claude Code as a session, Codex as a thread (started without
`ephemeral`). Steed records the session or thread id the agent reports. When the
chat is closed (Steed quit, or agent access was turned off), its card offers
**Continue this chat** while the agent still has it saved: Steed runs
`claude --resume <id>`, or asks Codex for `thread/resume`, in that folder, so the
same conversation carries on with its memory, as a new chat that shows the
earlier messages above a **Continued after the chat closed** line. Permissions
are asked for again; nothing is replayed. A chat continues once (afterwards it
is the newer chat that continues). Turned off, chats leave no history (Claude
Code runs with `--no-session-persistence`, Codex threads are ephemeral) and end
for good when Steed closes. ACP agents are not kept.

Checked with Claude Code 2.1.312 and a synthetic offline runtime; Codex resuming follows the App Server protocol of Codex CLI 0.166.0, checked with a synthetic runtime.

#### Asking your agent from anywhere

- **Right-click a page** → **Ask Agent About Selection** (with text selected),
  **Ask Agent About Link** (on a link) or **Ask Agent About This Page**. The
  sidebar opens on your current conversation (or any connected one, or starts
  a new chat) with a draft: the selection quoted, the page's title and address,
  and room for your question. Nothing is sent until you press Send.
- **Type `@` in the address bar**, then a question (`@ summarize this`), and
  press Enter: it goes straight to your agent with the page's title and address
  (not its contents; the agent asks for page access as usual).
- **Desktop notifications**: when an agent finishes a reply or needs your
  approval while Steed is not in front (or that conversation is not showing),
  a notification says so; clicking it brings the conversation up. Turn them off
  with `"agent_notifications": false` in `~/.custom_browser/config.json`.

Incognito windows have no agents, so none of these appear there.

#### Continuing a Claude Code session

**Continue a Claude Code session** (on the welcome) lists your saved Claude
Code sessions from `~/.claude/projects`: title and short id, what you said
last, folder and last use. Copies of a session share its title, so what you
said last tells them apart; nothing is selected for you, since the newest row
is often a session still open in a terminal. Chats Steed keeps are left out
(they continue from their own card). **Continue here** goes on with the chosen
one in the sidebar, with its history: Steed runs `claude --resume <id>
--fork-session` in the session's own folder, so it continues as a copy and a
session still open in a terminal is left as it is. The copy is saved in Claude
Code's history and, once closed, can itself be continued from its card. Claude
Code's own tools ask you in Steed as in any Claude Code chat, and browser
access still needs Steed's approval.

#### ACP agents

Agents that speak the [Agent Client Protocol](https://agentclientprotocol.com)
can chat in the sidebar too. **Settings and tools → Add an ACP agent…** keeps a
list of them: a name and the command that starts the agent in ACP mode (for
example `gemini --experimental-acp`). Each then appears under **Agent for new
chats**. Steed starts the command in a private folder, speaks ACP version 1
over its standard input and output, and gives it Steed's MCP server bound to
the chat's task, so browser access goes through Steed's approvals. Steed offers
the agent no file system or terminal of its own (those requests are refused);
the agent's permission requests appear as approval prompts (Allow once, Allow
for this chat, Deny). An agent that needs signing in must be signed in from a
terminal first. Only add commands you trust: they run as you.

#### Codex connection troubleshooting

Restart Steed after updating this checkout and start a **new** Codex chat. Ended
chats retain history and do not reconnect or automatically replay work.

The adapter supports the current `item/tool/requestUserInput` method as well as
the older question name. It answers the installed CLI's `currentTime/read` with
whole Unix seconds, including before/after a turn and for background threads.
This clock reply grants no Steed access. Notifications from other native Codex
threads are excluded from this chat; malformed or mismatched events within the
owned turn still fail. Unknown optional host RPCs receive a method-not-found
protocol error instead of causing Steed to terminate the connection. This does
not implement or approve the unsupported request.

These contracts were checked against the installed CLI's generated schemas and
[official App Server documentation](https://learn.chatgpt.com/docs/app-server).
Questions and native approvals remain separate from Steed grants. Genuine
connection/protocol failure now identifies only the handling stage/event and
exception class, without request bodies, commands, account details or stderr.
If it persists, report that event name and whether the task was using a tool or
requesting input; check receipts before repeating an uncertain external action.

The user's exact live-account disconnection has not been reproduced. Isolated
native checks and synthetic tool/question/clock/child-event regressions validate
these compatibility fixes; they do not establish that every Codex task works.

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

#### Personal agents and website coverage

Steed's stdio MCP adapter works with local clients that can launch a process.
Its authenticated HTTP adapter binds to loopback; a cloud agent cannot reach
that address directly. The browser must be running for the HTTP transport;
the stdio adapter may start first and connects on its first call (until then
calls return `browser_unavailable`, meaning nothing was sent).
Connecting a third-party client grants no page access: it starts its own task,
requests an exact profile/site scope and uses the published inspection/action
and conversation tools. **Resuming after a disconnect:** `task.start` returns a
`resume_token`. If the client's connection drops (restart, crash, network), the
task's access ends at once — its pages close, like an expired grant — but the
task, conversation and files are kept for five minutes. A new connection calls
`task.resume` with the token to continue the same task and receives a fresh,
single-use token; it then requests permissions again. Tokens work only in the
same browser run and alongside the normal agent-access credential. Tasks not
resumed in time are cancelled, as are all held tasks when agent access is
turned off; a connection Steed drops itself (a stuck operation) is cancelled
immediately. Steed-started Codex and Claude Code chats are not resumable this
way. The MCP adapter does this itself: it keeps the latest token, and after a
lost connection the agent's next call reconnects, resumes the task and then
runs, with `connection.task_resumed` (or, after a browser restart or more than
five minutes, `task_resumed: false`) added to its result. The call that failed
is never sent again, and calls already queued behind it fail instead of
running on the new connection. While a task waits, its sidebar shows
"Waiting to reconnect until HH:MM". Generic MCP cannot start a new turn in an idle client,
so an agent that is waiting for you calls `conversation.wait`: it blocks for up
to 25 seconds and returns as soon as you send a message (`waited: "message"`),
on timeout (`timed_out`, call again) or when the task ends (`task_ended`). The
agent's own replies do not end a wait, one wait per task runs at a time, and
calls the agent makes during a wait queue behind it. Steed-started Codex and
Claude Code chats get messages as new turns and do not have this tool.

Compatibility research (October 3, 2026):

| Agent | Verified integration route | Remaining work |
| --- | --- | --- |
| Codex | Dedicated native App Server and task-bound local MCP, including newer code runtime | Real-account desktop acceptance |
| Muse | Official [custom connector guidance](https://www.meta.com/help/artificial-intelligence/1687253048996149/) supports service API access | Confirm custom HTTP/MCP authentication and transport in an actual account; do not assume direct local-process support |
| ChatGPT dots | [Dots documentation](https://learn.chatgpt.com/docs/dots) supports enabled plugins and a separately connected local computer | Package and verify a Steed plugin/local bridge or authenticated remote connection; sidebar message delivery is separate |
| Instinct | [Official product page](https://instinct.com/) describes app/device access | No public custom MCP client setup was verified; cannot claim Steed compatibility yet |

The published page actions do **not** capture everything a human sees. Reads are
bounded rendered DOM snapshots with opaque references to supported links,
fields and forms. Bounded open-root text/control-name reads improve component
coverage; controls inside open shadow roots of the main document can be typed
into, selected, checked and activated like other controls (constraint
`shadow_control`), but form submission and file upload through them stay
unavailable. Closed roots and exact slot ordering remain
limited. Snapshot limitations report canvas content, form values/editable text, inaccessible frames,
out-of-scope frame origins and truncation. Images, PDFs, video/audio, visual
layout, custom widgets and unloaded/virtualized content can be missed. Restricted task pages disable website JavaScript and can miss modern app content;
live task sessions and explicitly shared user tabs run normal website scripts. Agents should inspect the
capability report and snapshot limitations rather than claim a complete view.

Existing-profile permission shares cookies/login state with ordinary browsing and
creates a separate restricted task page. **Watch / Take control / Return control**
preserves that task page.

#### Delegate normal live tabs

1. Open the agent sidebar and select a connected task.
2. Click **Give agent tab control…**. Choose the current tab or all currently open
   tabs on this named profile, across its windows (maximum ten).
3. Review the popup's profile, sites, selected tabs and actions, then approve.
   Existing task authority is replaced by a fresh reviewed scope; prior task
   pages close. Resolve any pending permission request first.
4. The agent calls `steed_browser_tabs` to discover only those shared tabs and
   `steed_session_adopt` to obtain/reuse their session IDs. The selected tabs are
   also adopted automatically on approval. It uses the normal inspection/action
   tools on the same page, with the existing login, document state, JavaScript,
   cookies and ordinary background network activity.
5. A bar above the page identifies the controlling task. **Take Back** removes
   that tab's authority immediately, including during pending input or loading.
   The agent cannot re-adopt it without a new explicit handoff. Address-bar
   navigation, Back, Forward and Reload also take the tab back first.

New tabs and tabs from other profiles are never included automatically. You can
browse other tabs while the agent works. Direct input into a delegated tab is
shielded; Take Back restores its prior input policy. Closing the tab/window,
changing profile, task cancellation, expiry, access disable and shutdown all
release borrowed pages before ordinary browser cleanup. Task completion does
not close or reload the user's tab. Incognito tabs cannot be delegated.

The `live` action is separate approval for this mode, with clear script/network
wording; remembered restricted-page approvals do not cover it. Ordinary website
scripts can send requests or change account data following interaction. Their
background requests are not filtered or counted against restricted-page network
budgets. Native button activation and form dispatch report `dispatched`, not
confirmed website success; observe the result and never retry uncertain effects.
DOM actions on password/file forms and custom widgets, plus borrowed-tab
upload/download automation, remain limited. Separately approved computer input
can operate visible password/custom controls; use restricted task transfers or explicitly shared files instead.
Normal user download/permission dialogs keep their ordinary browser behavior.

Live targets are revalidated individually against their node, markup, labels,
base URI and ancestor attributes, so unrelated DOM updates do not invalidate
all controls. Replaced or changed controls still require a fresh inspection.
Restricted pages retain their whole-document staleness checks. Control references remain opaque. Separate screenshot-guided computer permission
adds native click/type/key tools; arbitrary caller scripts and selectors remain
unavailable.

#### Website observation coverage

With `visual` and `read` permission, `steed_page_capture` returns the current
viewport as a native MCP image plus metadata. It needs a visible borrowed tab or
watched task view. Hidden tabs need `session.watch` first. The browser checks the
page's origin and lifetime again before releasing pixels, and Take Back cancels
pending captures. No image bytes are written to the task journal.

Visual approval includes visible field values and embedded content from other
sites, even frames omitted from DOM reads. It is separate from text-read approval;
restricted-page permissions do not silently expand. The sidebar's live delegation
popup explicitly includes visual permission. The bounded JPEG is at most
1280×960 pixels and 256,000 bytes; metadata reports clipping and viewport-only
coverage. The native frontend preserves the original viewport, scroll, scripts
and normal service-worker behavior. It opens no debugging listener. Image pixels are untrusted site content. They confer no input authority without
separate computer/live approval; each computer operation needs a fresh capture ID.

`page.inspect` provides bounded rendered DOM text and native controls, with flags
for truncation, inaccessible/out-of-scope frames, observed open shadow roots,
canvas, images/video/SVG and custom widgets. It distinguishes live website
execution from disabled scripts. Field values and editable text are omitted.
These flags describe coverage gaps, not a guarantee of completeness.

“Important” depends on the user's task. A layered observer should first extract
text, labels, links, headings and frames deterministically; then use accessibility
semantics and viewport images where DOM coverage is insufficient. A lightweight
model can route the next observation and rank relevant evidence within a budget.
It cannot recover unloaded content or guarantee that nothing was missed. The implemented layers include coverage signals, approved viewport images and
bounded form descriptions/required/invalid/error hints. Complete accessibility
trees and broader frame/widget inspection remain future work. Native frame inspection is available in Qt's
[QWebEngineFrame API](https://doc.qt.io/qt-6.11/qwebengineframe.html), but frame
lifetime and each frame's authority must be checked before exposing its content.

#### Other external clients

1. Complete the local MCP setup above and connect your external agent client.
2. Ask the agent to call `steed_task_start` and keep that connection open. Give
   the task a recognizable objective so you can choose it in Steed.
3. Open **Agents → Agent Sidebar**, or press `Ctrl+Shift+A`. Select the connected
   chat from the list (**Chats** in a narrow sidebar). The dock can move between the left and right sides.
4. Type a message and press **Enter** or **Send**. Only the submitted
   text is shared. Unsent drafts remain in that window and are not sent when
   changing tasks or hiding the sidebar. Messages have an 8 KiB UTF-8 limit.
5. Have the agent call `steed_conversation_read`, reply with
   `steed_conversation_reply`, and check for additional messages while working
   or waiting for your input. The sidebar shows pending messages and when the
   agent's read tool has fetched them; this does not prove the agent understood
   or acted on the text.
6. Expand **Settings and tools** for **Permissions**, **Task files**, and **Receipts**,
   which open the selected task's controls in a nonmodal window. The sidebar shows counts for pending
   grant, package, recipe and app reviews, files, drafts and app receipts.
   Website, task, package, recipe and payload approvals retain their existing
   separate controls. Conversation text never approves a request.
7. Hiding or closing the sidebar keeps the task running. **Stop task** ends
   it explicitly. Finish a final reply before calling `steed_task_finish`.
   Adapter disconnects still cancel unfinished tasks; restarting the browser
   retains history and marks active tasks and incomplete replies interrupted.

Suggested instruction for a connected external agent:

> Start a Steed task called “Browser collaboration”. Read its conversation
> mailbox, respond through conversation.reply, and check for follow-up messages
> while working. Request exact permissions for any page, file or app actions.

An ordinary MCP client may only run its agent after a prompt in that client's
own UI. **Send** queues a message for the task; it cannot wake such an idle
client. The agent must participate in the mailbox protocol, ideally by keeping
a `conversation.wait` call open while idle. Automatic turn delivery applies to
Steed-started Codex and Claude Code chats. Reconnect and
conversation replay into another task remain unavailable. Each new task needs
fresh permissions.

#### Chat tabs and website chats

##### Structured chatbot relay

Open **Agents → Website Chats → Chatbot relay…**, or **Use as agent…** in
website-chat sidebar controls. This lets a chatbot propose tool requests through
structured messages in **both directions**. It uses the existing task broker,
tool schemas, grant approvals and receipts. It does not give a website native MCP
or a native management bridge. Ordinary website chat and native external-agent
connections remain separate options.

1. Enter the task and choose **Copy/paste JSON manually** or **Enabled chat tab**.
   Tab mode requires explicit consent for a loaded HTTPS tab in this window's
   regular profile. The exact destination/profile/task remain visible. Incognito
   cannot start a persistent relay.
2. **Start relay task** prepares the first outbound JSON: your task, Steed's
   instructions, published tool schemas, exchange ID and queued user messages.
   Review the exact text. **Copy reviewed message** works with any chatbot UI;
   tab mode also offers **Paste reviewed message**. Steed focuses one unambiguous
   visible composer and uses native Paste. It never presses Send. If the composer
   is ambiguous, copy and paste into the intended field yourself.
3. Send in the chatbot. Paste its complete JSON response into Steed, or select
   only that response in the enabled tab and use **Read selected response**.
   Steed does not scrape the conversation or guess the latest assistant message.
4. **Review response** validates the complete batch and displays a canonical
   preview. **Run reviewed requests** dispatches that exact preview. Editing the
   response invalidates the review. Approve requested access through the normal
   Steed popup; a chatbot cannot approve itself. **Task permissions / details…**
   selects this task, including its pending approvals and recorded activity.
   If validation fails, **Prepare outbound message** includes structured
   `response_feedback` so the chatbot can correct its response; no requests from
   that rejected batch are dispatched.
5. Inspect **Results**, then **Prepare outbound message**. Review/copy/paste the
   new JSON back to the chatbot and repeat. Subsequent messages carry structured
   results/errors, the next exchange ID and new user messages. Tool schemas are
   omitted by default after the first exchange; the checkbox can include them
   again. Copy/paste sends the exact prepared snapshot, never an invisible refresh.
6. A `final` response becomes a reviewed answer in the task conversation; it does
   not prove external effects. **Stop relay** or closing the review cancels the task
   and its authority. No relay is resumed or replayed after restart.

Chatbots must return one JSON object (an exact `json` code fence is also accepted):

```json
{
  "version": 1,
  "exchange_id": "issued-by-Steed-for-this-exchange",
  "calls": [
    {
      "id": "inspect-1",
      "tool": "steed_page_inspect",
      "arguments": {"session_id": "previously-returned-session-id"}
    }
  ]
}
```

Use `{"version":1,"exchange_id":"current-ID","final":"Answer"}` for an answer
without tool calls. Each request needs a new short call ID, a published tool name
and schema-valid arguments. The trusted UI assigns task identity. Raw Python,
shell, JavaScript, approval and lifecycle commands are unavailable; generated code
uses existing reviewed worker/package tools. The parser rejects duplicate keys,
unknown fields/tools, nested schema mismatches, stale exchanges and reused IDs
before dispatch. Accepted IDs are consumed even when execution fails or later
calls are skipped. This prevents replay of the same IDs, not semantically duplicate
writes with invented new IDs: users and chatbots must still inspect outcomes.

Requests are limited to 64 KiB, four calls per batch and 32 exchanges per task.
Goals/final answers are limited to 8 KiB. The first transport is text/JSON; screenshot
and pixel-input tools, automatic image/file attachments and provider-specific
response extraction are not offered. Large results are explicitly omitted within
a 600 KiB result budget; outbound messages must fit 1 MiB. Any failure or omitted
result stops the remaining batch. Check outcomes before issuing further requests;
Steed never retries uncertain writes. A 35-second deadline ends an unresponsive
relay, without claiming that already-dispatched work had no effect.

Tab binding checks the exact view/page/profile/address (excluding the URL fragment)
at sharing and dispatch. It cannot detect an invisible provider conversation switch
that keeps the same URL; selected-response reading and request review remain required.
Before the first accepted response, a chat's initial conversation-address change
pauses exchange: **Enable this initial conversation** deliberately rebinds to a
loaded address on the same HTTPS origin. Subsequent navigation/reload, closing the
tab, profile changes, task/grant termination or reduced enabled-folder access
stops the relay and clears its unshared buffers. Clipboard text and data already
sent outside Steed cannot be recalled. Existing saved grant rules still apply
within their scope; reviewed requests never create an extra approval bypass.

Copy/paste acknowledges exported queued user messages; it does not establish that
the chatbot read them, is thinking or finished. Steed reports its own request
execution and result readiness. No real-account provider compatibility or
automatic Send/extract loop is claimed by synthetic page acceptance. Provider
adapters and image-capable exchange remain later milestones.

##### Current chat interfaces

The agent panel separates connection health, runtime activity and message delivery.
User messages show waiting/queued/awaiting acceptance/read states from actual
transport and runtime facts. A generic MCP fetch confirms a read, not execution
or completion. Pending permissions show **Approval requested** without asserting
that all other agent work has stopped. Disconnected and ended conversations keep
an editable local draft; Send stays disabled until a connected task is available.
Reloading the interface or reconnecting never resends messages automatically.

The **Last tool failure** card names the failed operation and a maintained cause
with a concrete next step. **Check Permissions** and **Check Receipts** open the
selected task's details; Dismiss hides that event locally until another failure.
Diagnostics contain no request values, raw exceptions or credentials. Uncertain
input outcomes direct you to inspect the website/receipts before another attempt.

For an interrupted or ended conversation, choose **Continue / hand off…**:

1. Write the new instruction; the original local draft is initially copied into
   the editor. **Use last unread message** is explicit and does not assume that
   earlier work had no effects.
2. Optionally check **Include the reviewed recent conversation** and edit that
   snapshot. History is unchecked by default. Long transcripts include a marked,
   UTF-8-safe recent excerpt; files, browser contents and tool results are never
   attached automatically. Review text for secrets.
3. Choose a fresh Codex chat, another connected external agent, or copy for an
   external agent/website chatbot. Inspect the exact message preview (8 KiB total).
4. **Send reviewed continuation** shares once. A fresh Codex chat receives a new
   Steed task and the message waits for its verified connection. **Copy reviewed
   message** only uses the clipboard; paste and send in your chosen client.

Other MCP clients must fetch queued messages through `conversation.read`. Steed
does not claim that sending a continuation automatically wakes an arbitrary client.

The old history/draft remains available. Old grants, live-tab handoffs and queued
messages are not restored or resent. This is a reviewed continuation, not resuming
the ephemeral native Codex thread. An active source task keeps running until you
explicitly stop it. Failed startup explains the problem and sends
nothing. Closing the review while connecting cancels its new, empty task. Profile
changes, private browsing, source selection changes and sidebar reload invalidate
the review. An uncertain send is never retried automatically; check the recipient's
conversation and Receipts before preparing another attempt. Interrupted work may
already have affected a website or external service.

Choose **Agents → Agent Chat Tab** or **Open as tab** in the agent sidebar to move
the same Steed conversation into a full browser tab. **Show sidebar** returns it
to the dock. Sent messages, selected task and unsent draft survive the move;
closing the tab hides the conversation and keeps its task running. Normal saved
tab restoration can reopen this local HTML interface. Its address is
`steed:agents`; it does not load a remote page or confer browser permission. Incognito still clears
and disables the Steed conversation.

For website subscriptions, choose **Agents → Website Chats → ChatGPT / Claude /
Gemini**. These open `chatgpt.com`, `claude.ai` and `gemini.google.com` in normal
tabs using the current profile's ordinary cookies and sign-in. Open another
HTTPS chat website normally to use it too. Choose **Current tab in chat sidebar**
to move that exact page beside your browsing. **Back to tab**, or selecting its
existing tab, returns it without reloading or copying its conversation. Changing
profiles closes the old profile's chat view. Its address, profile/loading status,
**Share instructions…** and **Back to tab** controls now use themed, offline HTML.
The website stays in its original profile's separate engine. Only its displayed
address/status enter the controls; its conversation and draft are not read.
Changing websites or reloading the controls invalidates queued control actions;
interface recovery never replays sharing or reloads the website. Private website chats use the
incognito profile; a provider's server-side retention still applies.

To pass instructions:

1. Open a website chat. While browsing the source page, choose **Agents → Website
   Chats → Share instructions with a chat…**, or **Share instructions…** in the
   website-chat sidebar.
2. Write the instructions in the preview dialog. Optionally include the current
   page's title, address and selected text. This starts unchecked; no full page,
   history, cookies, files, credentials or MCP tokens are automatically included.
   Context is labelled untrusted. URL user information, queries and fragments
   are removed; inspect selected text for anything you do not want to share.
3. Review the exact bounded prompt (at most 8 KiB UTF-8). **Copy instructions**
   puts it on your system clipboard for manual pasting. **Paste into chat**
   focuses one unambiguous visible textarea/contenteditable and uses native
   browser paste, so ordinary website input handlers run. If no unique composer
   is found, use Copy and choose the field yourself.
4. Review and send in the website. Steed does not press Enter, submit the form,
   scrape the reply or treat the message as permission approval. Closing the
   preview, navigating/reloading the destination or switching profiles blocks
   late paste. Profile changes clear the preview's unsent content. Clipboard
   copies remain subject to your desktop clipboard history.

Web chat and tool connection are separate. A prompt cannot make a website's
cloud model reach Steed's local socket/loopback MCP endpoint. Claude's remote
connectors originate in its cloud infrastructure and require a reachable server
([official network requirements](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).
OpenAI documents an outbound private-server tunnel, with its own workspace and
authentication controls
([Secure MCP Tunnel design](https://developers.openai.com/blog/connect-private-mcp-servers-to-openai-products));
Steed has not integrated or validated it. Google's documented remote MCP support
for its agent API
([agent API guide](https://ai.google.dev/gemini-api/docs/antigravity-agent))
does not establish arbitrary MCP support in an ordinary Gemini website chat.
Actual capabilities depend on the chosen product/account. Continue using native
Codex chats or a configured local external agent for current Steed tool access.
An authenticated cloud pairing/bridge is a separate pending milestone; no local
browser authority is exposed to website JavaScript.

#### Conversation tool protocol

`steed_conversation_read` accepts optional `after` (initially `0`) and `limit`
(1–16, default 16). It returns `messages`, `cursor`, and `has_more`. Save the
returned cursor and fetch again until `has_more` is false. Poll for follow-ups at
a modest interval, such as once a second, while the external agent is active.
The cursor represents message updates, not chronological display order: order
messages by `created_sequence` and replace the record with the same `message_id`
when its `sequence` changes. A streaming reply can therefore appear after a later
user message in a change page while retaining its original position in the UI.

A complete reply:

```json
{"message_id": "reply-1", "text": "I have checked the page."}
```

A streaming reply uses cumulative text and consecutive revisions:

```json
{"message_id": "reply-2", "text": "Checking", "state": "streaming", "revision": 0}
{"message_id": "reply-2", "text": "Checking the page…", "state": "streaming", "revision": 1}
{"message_id": "reply-2", "text": "Checking the page… Done.", "state": "complete", "revision": 2}
```

Default state is `complete`, default revision is `0`. Exact current-revision
retries return the same message without adding another activity event. Changed
repeats, skipped revisions, text replacements and edits after completion fail.
Start a new stable message ID for a longer reply. Limits are 256 message records,
1 MiB retained text and 2,048 post/update events per task; returned pages fit the
existing gateway response bound. Task identity is assigned by the browser, never
accepted from MCP arguments. Agents can neither post user messages nor clear a
conversation, approve requests or read another connection's task messages.

The trusted **Clear Chat** button removes message bodies from task rows in both
windows and subsequent tool reads, retaining content-free tombstones so old IDs
cannot be reused. It does not undo actions or remove copies already fetched by
an external client. It is not forensic secure erasure. Activity events retain
only message identity, role, revision and state. Conversation bodies live in the
private task database; schema 16 adds messages and read markers while preserving
older journals and receipts. Historical conversations are not automatically
shared by task recovery.

The incognito search profile disables the sidebar conversation, clears visible
text and unsent drafts, and requires selecting a task again after returning to
regular browsing. A task's existing approved profile stays pinned when you switch
regular browser profiles. Multiple windows can view the same task's sent messages;
unsent drafts stay local to each window. The sidebar loads only when opened, so
ordinary browser startup does not initialize agent task storage through this UI.

Automated sidebar validation (October 3, 2026): the full suite passed 813 tests
in 316.979 seconds, followed by 46 final agent/UI tests in 11.874 seconds and 12
conversation tests in 2.302 seconds. Independent SDK/raw clients exchanged actual
sidebar prompts and streamed replies over stdio and authenticated HTTP while
completing the existing shared browsing, recipe and document-worker workflows.
Startup/render/shutdown and the synthetic dock preview passed. The first full
run crashed during offscreen Vulkan initialization before any test ran; a
standalone retry passed. These fixtures do not demonstrate that a particular
idle agent client can wake or that real accounts have passed acceptance.

### HTTP connection

Enable local agent access in Steed, then start a separate adapter process:

```bash
.venv/bin/python agent_mcp.py --transport http --port 8765 --http-client codex --http-client other
```

It binds only `127.0.0.1`, with endpoint `http://127.0.0.1:8765/mcp`. Omit `--port`
to select an available port. Use a process supervisor to keep the adapter running
independently of a terminal. The GUI continues to use its existing launcher.

The adapter publishes `~/.custom_browser/agent-http.json` with mode 0600. Its
`url` is the endpoint, and `tokens` maps each configured client name to a generated
secret. Configure your HTTP MCP client with that URL and the header
`Authorization: Bearer <that client's token>`. Tokens are generated per adapter
run and are never printed or passed on its command line. Use distinct names and
credentials for different clients; up to four can be configured. Names contain
1–32 letters, digits, underscores or hyphens. Without `--http-client`, the name
is `default`. All methods require authentication; website Origin headers are
rejected. Client tokens use local pre-shared authentication; OAuth discovery is
not implemented, so clients must support custom authorization headers.

`--discovery` selects the browser's gateway metadata; `--http-discovery` selects
the separate HTTP endpoint file. An existing endpoint file is preserved and
startup fails instead of overwriting it. After a crash, confirm that its adapter
is stopped before removing a stale file. Normal shutdown removes the owned
file. Disabling agent access or closing Steed also shuts down this adapter;
restart it after enabling a fresh browser access run.

Each initialized MCP session owns a separate browser connection and task.
Its ID is bound to the credential that initialized it. Complete tasks with
`steed_task_finish`; send HTTP DELETE with `MCP-Session-Id` to end a session and
cancel any unfinished task. HTTP sessions survive network drops, so an ended or
expired HTTP session cancels its task instead of holding it for `task.resume`. Sessions expire after 30 minutes without an HTTP
request or open GET stream, which also cancels the task; `--idle-timeout`
changes this (10–86,400 seconds). The four-session limit bounds abandoned ones.
Closing a single HTTP connection does not cancel
its task. A cancellation notification during an operation closes its browser
connection, cancels its task, and prevents further operations on that connection.
There is no automatic reconnection, operation retry, event replay or restoration
of old grants. Effects of a cancelled/lost request can be uncertain; inspect
evidence instead of blindly retrying. These lifecycle rules follow the
[MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

HTTP uses the SDK's stateful session manager, JSON POST responses, optional GET
event streams and DELETE termination. Limits are four sessions, 64 KiB request
bodies, and one browser operation in flight per session. Browser connections
share the gateway's four-connection limit with stdio clients. Proxy headers are
ignored and access logging is disabled. Pages/profiles stay in the Qt process;
the HTTP adapter uses the same broker and popup approvals as stdio.

### Shared tools and permissions

Tool discovery publishes 73 input schemas. Names follow the internal command
table with `steed_` and underscores, for example `page.inspect` becomes
`steed_page_inspect`. `steed_task_start` creates the connection's task. Arguments
omit `task_id`; the browser assigns it. Opening pages, inspection, observed link
navigation, supported control edits, native form submission, scoped uploads and
downloads, scrolling, waits,
watching, retained drafts, configured app operations, evidence, and task lifecycle
are available. Approval, task enumeration,
raw cookies, JavaScript, arbitrary selectors, unshared file bytes, and raw Qt objects
are not exposed by this adapter. `steed_session_watch` needs explicit `attach`
permission and opens a browser-owned window for restricted task pages. On a live
session it brings the original tab/window forward. Taking/returning control and
live-tab selection are trusted user actions. See
[live delegation](#delegate-normal-live-tabs) and
[visual observation](#website-observation-coverage) for the separate normal-browser
mode and viewport image tool.

`steed_browser_context` needs an active task but no site grant. It returns
`window_id`, `profile`, `profile_selector`, `shared_profile_available`, and
`source`, without open URLs, tab contents, history or credentials. A focused
browser dialog resolves to its parent window. With no recorded focus, a sole
window is selected; multiple windows yield an unavailable context rather than a
guess. Closed windows are excluded. Incognito (`search`) cannot be selected for
shared task sessions, so its selector is null. Global now supports the explicit
`existing:global` selector with ordinary task review and stable identity. Discovery grants
no access, and switching profiles never retargets existing grants or sessions.
Restart Steed after updating the browser source to expose the new context tool.

### Watching and taking control

Restart Steed after updating its source. Enable local agent access and connect
the adapter as described above. To collaborate on an approved task page:

1. Have the agent request `attach` and `read` with its site/profile scope.
   Include `navigate`, `edit`, `scroll`, and `submit` only as needed; the usual
   approval popup applies.
2. Open **Agents → Task Permissions**, select the task and its session, then click
   **Watch Page**. The agent can also call `steed_session_watch` with its
   `session_id` to open this window.
3. Watching shows the same page and profile while the agent continues. The
   rendered page is read-only; opening the window never clones the session.
4. Click **Take Control** after the current page operation finishes. Choose an
   observed link or field and use **Open Link**, **Fill Field**, **Toggle**,
   **Choose Option**, **Select File** with an approved source, **Submit Form**, or
   the scroll buttons. The agent's page
   operations and content reads are blocked until you return control.
5. Click **Return to Agent** to continue delegation. The page and your edits
   remain intact. Old target references are invalidated; the agent must inspect
   again. `steed_session_inspect` reports `control_owner: "user"` or `"agent"`
   so clients can wait for the handoff.

This first takeover supports the same approved, scriptless actions as agent
restricted tasks. Direct clicking/typing inside the watched task page, website scripts,
password/multiple-file/folder forms, and custom controls remain unavailable. Site restrictions,
operation budgets, and grant deadlines still apply during user control;
ordinary browsing windows remain usable on the same profile.
Take Control pauses page operations; app operations belong to the task and use
their own cancellation controls in the Apps panel.

Closing an idle watch window, including Escape or closing its parent browser
window, returns control and leaves the task page alive while another browser
window remains. Closing during a pending user mutation cancels the task rather
than resuming the agent with an uncertain action. **Cancel Task**, revocation,
expiry, completion, disabling access, and last-window shutdown dispose owned
pages and close their watch windows. Cancellation cannot undo an effect already
sent to a website. Shared-profile cookies/storage follow the existing profile's
normal lifetime.

### Continuing previous work

Recovery starts a new follow-up task; it never reopens an old task, adopts its
connection, restores pages/grants or dispatches an old operation.

1. Have the agent call `steed_task_start`, then `steed_task_recover` before
   requesting a grant or doing work. It cannot enumerate archives or supply a
   source task ID. The browser opens a nonmodal **Continue previous work?** picker.
2. Select a finished, cancelled, failed or interrupted task. Check up to ten
   drafts under **Drafts** and up to ten sealed files under **Files** to share;
   neither list is selected by default. Files are bounded to 8 MiB each and
   32 MiB total. Drafts have plain-text previews; files show name/type/size/hash
   without rendering or running their contents. Sharing can disclose the selected
   material to the agent's model provider. The evidence checkbox shares prior permission scope, up to 50
   activity entries, the selected drafts' recorded source inspections and app
   receipt summaries. Receipt text is limited to 512 characters with an explicit
   truncation flag. Source profile/scope is historical information only.
3. Click **Share Selected Material**, or **Deny** to disclose nothing. Closing or
   Escape leaves the request pending; reopen it in **Agents → Task Permissions →
   Recovery → Choose Recovery Material…**. This tab also shows retained context.
4. The agent polls `steed_task_recovery`. After approval it receives only the
   selected context, new artifact IDs and `context.imported_files` mappings.
   Each file mapping has fresh `file_id`, `source_file_id`, name/type/size/hash;
   it contains no file bytes. Draft bodies still require a fresh
   `artifact` grant; use `steed_artifact_list`/`steed_artifact_read` after approval.
   File bytes separately require fresh `workspace` permission and
   `steed_workspace_list`/`steed_workspace_read`.
   Original draft IDs and source-event/target references belong to the old task
   and cannot be used as current observations.
5. Request the current sites, profile, actions and app destinations as usual.
   Recovered tasks always show a fresh access prompt, even when a saved rule
   would cover the request. Open fresh pages and inspect again before acting.

Copies have independent IDs and lifetime. Deleting an original draft/file does not
delete shared copies. Draft/file imports, source provenance, evidence and write protections
commit together in `tasks.sqlite` (current schema 14; file recovery itself added no schema migration). Copies are checked against their stored size/hash before sharing.
Only selected files enter the new workspace, marked `user_shared`; unselected,
deleted, building and foreign files are rejected. Imported source is data:
packages need fresh staging and exact code approval. Old package registrations,
worker receipts and remembered execution approvals are never copied. File provenance
is recorded in recovery mappings, value-free activity events and the Workspace panel.
Selection is bounded to ten drafts and ten files, the
context to 128 KiB and ancestry to 32 tasks. Oversized evidence can be omitted.
Restart interrupts follow-ups and ends pending pickers; another follow-up needs
another user selection and fresh approval.

Before a new app mutation, Steed compares its connector, tool, effect, exact
destination and body hash with writes recorded in the selected task's ancestry.
A matching completed or uncertain call raises `recovery_write_already_recorded`
before dispatch, even with a new request ID or a recreated identical draft.
Deleting the original draft or omitting evidence does not remove that protection.
Current receipt outcomes are checked; calls confirmed `not_applied` or cancelled
before dispatch can be attempted under a new write grant only after the original
worker has ended. Normal duplicate request-ID protection still applies within
the new task.

This is a bounded check for identical app requests, not general provider
idempotency: changed bodies, different connectors/destinations, independent tasks
and website form effects cannot be identified as the same intent. No historical
action is automatically replayed. Review uncertain outcomes in the original
**Apps** panel or provider UI. Shared archived receipts support the read-only
checks below; deliberate repeat-write overrides are not implemented. Gmail remains
send-only with manual verification. Historical text and provider results remain untrusted.

### Checking an archived app receipt

1. Start a new task and call `steed_task_recover` before requesting access. In the
   popup, choose the **original task** and include its evidence/receipt summaries.
   Draft selection is optional for a receipt check.
2. Poll `steed_task_recovery`. Pick an uncertain receipt from its shared
   `context.app_receipts`; callers cannot enumerate other archives or choose a
   foreign source task. Use `steed_app_list_tools` to check `can_reconcile` and
   `reconcile_effect` (`read`). Unsupported tools require manual verification.
3. Request `actions: ["app"]` with the source receipt's exact `connector`, `tool`
   and `destination`, and **`effect: "read"`**. Steed shows a fresh approval popup.
   Checking a previous write needs no draft, artifact or mutation permission.
4. Call `steed_app_reconcile_history` with `source_request_id` from the shared
   receipt and a new stable `request_id` for the check. Poll `steed_app_status`
   using the **new** request ID. Duplicate IDs return the same receipt. Cancel
   via `steed_app_cancel`; an uncertain check can be checked again with
   `steed_app_reconcile` after its worker ends.

The new task owns the check, its budget, live grant and receipt. The trusted
lookup callback receives the **original** provider operation ID, original request
ID and destination, with no draft body. Its original write callback is never
called. The old task stays finished/interrupted and its grants stay ended.

Confirmed outcomes update the new receipt and unresolved original receipt in
one transaction. A failed/empty lookup, timeout, cancellation or restart leaves
original uncertainty intact. A `not_applied` report while the original worker
is running stays uncertain until that worker ends. Late older workers cannot erase
independently confirmed evidence. Restart retains source mappings without replaying provider
requests. Google Drive's absent search result never proves `not_applied`.

**Agents → Task Permissions → Apps** shows shared receipts as **Previous** rows.
Select an uncertain row and use **Reconcile Uncertain Result** after fresh read
approval. The new check appears as a separate READ receipt. Archived receipts of
checks themselves are not original provider operations: choose the original task
when checking again. Gmail send has no read-only lookup; use **Sent** manually.

### Results and connection limits

Results include matching text JSON and `structuredContent`, wrapped in
`{"result": ...}`. Errors use `isError: true` and
`{"error": {"code": ..., "message": ...}}`. The SDK also rejects invalid tool
names/schemas. Browser errors omit exception strings and tracebacks. Page content
is untrusted, field values are omitted, references become stale after changes,
and existing read/edit/submit permissions still apply. Connection loss or a
cancelled request may follow a site effect; no operation is automatically retried.

The browser gateway uses a private Unix socket in a temporary directory (0700),
a 0600 discovery file, and an ephemeral token. It accepts at most four connections,
with one active task and one in-flight command per connection. Browser-side validation
rejects caller-selected task identities and unsupported tools even if the adapter
is bypassed. Requests are limited to 64 KiB and responses to 1 MiB. Authentication
times out after five seconds; an operation exceeding 30 seconds closes its
connection and cancels its task. Disabling access, journal failure, disconnect,
and last-window shutdown remove access and dispose owned pages before profiles.
The MCP adapter queues up to 32 active/waiting calls per connection, transmitting
one at a time. Parallel discovery therefore does not reject sibling requests.
Queue waiting is bounded to 35 seconds; overflow/queue-timeout errors explicitly
say that the affected call was not dispatched. Cancellation while waiting leaves
the active call alone. Cancellation/failure after dispatch closes the connection,
fails queued calls and never replays a potentially uncertain operation.
The dedicated Codex entry allows 75 seconds for a tool call, covering queue
admission plus its bounded dispatched response; the gateway's 30-second operation
timeout still applies. Other MCP clients choose their own transport deadlines.
Rejected tool/argument requests with an assigned task record a value-free denial
event; execution failures record a value-free `client.rejected` diagnostic.
Local processes running as your user can read the connection token and request
new tasks; they still cannot adopt another connection's task or approve grants.

Verified compatibility is MCP `2025-11-25` over stdio and Streamable HTTP, using
an SDK client and an independent JSON-RPC client on each transport. The full A4
milestone remains in progress: reconnect/resume semantics, additional protocol
revisions and broader client compatibility remain pending. Same-page watch and
scoped user handoff are implemented; broader collaboration UI is tracked in A7.
