const $ = id => document.getElementById(id);
let bridge = null, state = null, context = null, draftVersion = 0, messagesKey = null, pendingSend = null;
const composer = $("composer");
const AGENTS = {
  codex: {name: "Codex", models: [["", "Codex default"], ["gpt-5.4", "GPT-5.4 (tested direct Steed tools)"]]},
  claude: {name: "Claude Code", models: [["", "Claude Code default"], ["opus", "Opus"], ["sonnet", "Sonnet"], ["haiku", "Haiku"]]},
};

function command(action, values = {}) {
  if (!bridge || !state) return;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, action, ...values}));
}

function send() {
  if (!state?.active || composer.disabled || pendingSend) return;
  pendingSend = {context, version: ++draftVersion};
  $("send").disabled = true;
  command("send", {value: composer.value, version: draftVersion});
}

function visible(id, show) { $(id).hidden = !show; }
function text(id, value) { $(id).textContent = value || ""; }

function renderMessages(messages, reset) {
  const key = JSON.stringify(messages);
  if (key === messagesKey) return;
  messagesKey = key;
  const log = $("messages");
  const position = log.scrollTop;
  const atEnd = reset || position + log.clientHeight >= log.scrollHeight - 40;
  const previous = new Map([...log.children].map(node => [node.dataset.id, node]));
  for (const message of messages) {
    let node = previous.get(message.id);
    if (!node) {
      node = document.createElement("article");
      node.dataset.id = message.id;
      const heading = document.createElement("div");
      heading.className = "message-heading";
      heading.append(document.createElement("span"), document.createElement("span"));
      heading.lastChild.className = "message-state";
      const body = document.createElement("div");
      body.className = "message-body";
      node.append(heading, body);
    }
    previous.delete(message.id);
    node.className = "message " + (message.role === "user" ? "user" : "agent");
    node.firstChild.firstChild.textContent = message.role === "user" ? "YOU" : "AGENT";
    node.firstChild.lastChild.textContent = message.role === "user" ? message.delivery || "" : message.state === "complete" ? "" : message.state;
    // Agent messages and service diagnostics are data, never HTML or commands.
    if (node.lastChild.textContent !== message.text) node.lastChild.textContent = message.text;
    log.append(node);
  }
  for (const node of previous.values()) node.remove();
  log.scrollTop = atEnd ? log.scrollHeight : position;
}

window.steedUI = {render(next) {
  const switched = context !== next.context;
  state = next;
  if (switched) { context = next.context; draftVersion = next.draft_version; messagesKey = null; }
  if (switched || !next.active || (next.send_result.context === context && next.send_result.version >= pendingSend?.version)) pendingSend = null;
  document.documentElement.dataset.theme = next.dark ? "dark" : "light";
  visible("unsafe", next.yolo);
  visible("welcome", !next.usable);
  visible("welcome-start", next.available && next.has_provider);
  visible("setup", next.available);
  $("setup").textContent = next.has_provider ? "Connect another agent" : "Connect an agent";
  text("welcome-copy", next.private ? "Agent conversations are unavailable in private browsing. Switch to a regular profile to continue." : next.available ? "Start a conversation, delegate a task, and keep browsing while your agent works." : "Agent conversations are unavailable. Open connection setup from the Agents menu to check your connection.");
  text("welcome-note", next.yolo ? "YOLO mode automatically approves Steed requests. Turn it off in the Agents menu to review access." : "Only messages you send are shared. You choose when an agent can access your browser.");
  visible("new-chat", next.available && next.has_provider);
  visible("task-picker", next.available && next.tasks.length > 0);
  const taskOptions = JSON.stringify(next.tasks);
  if ($("tasks").dataset.options !== taskOptions) {
    $("tasks").replaceChildren(new Option("Choose a conversation", ""), ...next.tasks.map(t => new Option(t.label, t.id)));
    $("tasks").dataset.options = taskOptions;
  }
  $("tasks").value = next.task_id || "";
  visible("activity", next.usable);
  text("activity-title", next.activity.title);
  text("activity-hint", next.activity.hint);
  text("running-model", next.activity.model);
  text("connection", next.connection.label); $("connection").dataset.tone = next.connection.tone;
  text("delivery", next.delivery);
  visible("progress", next.activity.busy);
  $("activity-dot").classList.toggle("working", next.activity.busy);
  $("activity-dot").dataset.connection = next.connection.state;
  visible("error", !!next.error); text("error", next.error);
  visible("tool-failure", !!next.failure);
  text("failure-title", next.failure ? 'Last tool failure · ' + next.failure.title : '');
  text("failure-operation", next.failure ? next.failure.command + ' · ' + next.failure.code : '');
  text("failure-guidance", next.failure?.guidance);
  $("failure-details").dataset.action = next.failure?.action || 'receipts';
  $("failure-details").textContent = next.failure?.action === 'permissions' ? 'Check Permissions' : 'Check Receipts';
  visible("attention", next.usable && (next.pending > 0 || next.requests > 0));
  visible("pending", next.usable && next.pending > 0); text("pending", `Review ${next.pending} approval(s)`);
  visible("agent-requests", next.usable && next.requests > 0); text("agent-requests", `Answer agent (${next.requests})`);
  visible("conversation", next.usable); visible("compose", next.usable);
  renderMessages(next.messages, switched);
  visible("empty-chat", next.messages.length === 0);
  if (switched || next.draft_version >= draftVersion) {
    if (composer.value !== next.draft) composer.value = next.draft;
    draftVersion = next.draft_version;
  }
  composer.disabled = !next.can_draft;
  composer.placeholder = next.active ? 'Ask your agent…' : 'Write a local draft while the agent is unavailable…';
  $("send").disabled = !next.active || !!pendingSend;
  visible("cancel", next.can_cancel); visible("delegate", next.can_delegate);
  visible("options", next.available);
  if (!next.available) $("options").open = false;
  visible("model-control", next.has_provider);
  // Every agent a new chat can use (Codex, Claude Code and ACP agents).
  const agents = Object.fromEntries((next.agents || []).map(a => [a.id, a]));
  for (const [id, entry] of Object.entries(AGENTS)) if (!agents[id] && !next.agents) agents[id] = {id, ...entry};
  const agent = agents[next.new_agent] ? next.new_agent : "codex";
  const known = JSON.stringify(Object.keys(agents));
  if ($("agent").dataset.known !== known) {
    $("agent").replaceChildren(...Object.values(agents).map(entry => {
      const option = document.createElement("option");
      option.value = entry.id; option.textContent = entry.name;
      return option;
    }));
    $("agent").dataset.known = known;
    $("model").dataset.agent = "";
  }
  $("agent").value = agent;
  text("welcome-agent", agents[agent].name);
  if ($("model").dataset.agent !== agent) {
    $("model").replaceChildren(...agents[agent].models.map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value; option.textContent = label;
      return option;
    }));
    $("model").dataset.agent = agent;
  }
  $("model").value = next.new_model || "";
  $("library").disabled = !next.can_library;
  for (const id of ["permissions", "files", "receipts", "clear"]) $(id).disabled = !next.usable;
  text("status", next.status); text("summary", next.summary);
  return true;
}};

composer.addEventListener("input", () => command("draft", {value: composer.value, version: ++draftVersion}));
composer.addEventListener("keydown", event => {
  if (event.key === "Enter" && event.ctrlKey && !event.isComposing) { event.preventDefault(); send(); }
});
$("tasks").addEventListener("change", () => command("select", {value: $("tasks").value}));
$("model").addEventListener("change", () => command("model", {value: $("model").value}));
$("agent").addEventListener("change", () => command("agent", {value: $("agent").value}));
document.addEventListener("click", event => {
  const button = event.target.closest("button[data-action]");
  if (!button || button.disabled) return;
  if (button.dataset.action === "send") send();
  else command(button.dataset.action);
});
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
