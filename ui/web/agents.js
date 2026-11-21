const $ = id => document.getElementById(id);
// How a reply's state reads (a cut-off turn keeps its partial reply).
const MESSAGE_STATES = {streaming: "reply arriving", interrupted: "cut off before finishing"};
// The chat list shows this many recent chats; the rest wait behind "More".
const RECENT_CHATS = 5;
let bridge = null, state = null, context = null, draftVersion = 0, messagesKey = null, pendingSend = null;
// null follows the default: details open until the first message or while something needs a look.
let activityOpen = null, showAllChats = false, chatsOpen = false;
const composer = $("composer");

function command(action, values = {}) {
  if (!bridge || !state) return;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, action, ...values}));
}

function send() {
  if (!state?.active || composer.disabled || pendingSend || !composer.value.trim()) return;
  pendingSend = {context, version: ++draftVersion};
  $("send").disabled = true;
  const values = {value: composer.value, version: draftVersion};
  if (attached.length) values.attach = attached.map(tab => tab.id);
  command("send", values);
  attached = []; renderAttached();
}

// @ in the message lists your open tabs; a picked tab goes with the message
// (its title, address and text), shown as a chip you can remove.
let attached = [], mentionIndex = 0;
function mentionQuery() {
  const before = composer.value.slice(0, composer.selectionStart);
  const match = before.match(/(^|\s)@([^\s@]*)$/);
  return match ? match[2].toLowerCase() : null;
}
// / at the start of the message lists your skills; picking one runs it on
// the page you are on.
function skillQuery() {
  const match = composer.value.match(/^\/([^\n]*)$/);
  return match ? match[1].trim().toLowerCase() : null;
}
function mentionChoices() {
  const skill = skillQuery();
  if (skill !== null && state) {
    return (state.skills || []).filter(s => s.name.toLowerCase().includes(skill))
      .slice(0, 8).map(s => ({id: s.id, title: s.name, host: s.steps ? "skill · with saved steps" : "skill", skill: true}));
  }
  const query = mentionQuery();
  if (query === null || !state) return [];
  return (state.mention_tabs || []).filter(tab => !attached.some(a => a.id === tab.id) &&
    (tab.title.toLowerCase().includes(query) || tab.host.toLowerCase().includes(query))).slice(0, 8);
}
function renderMentions() {
  const choices = mentionChoices();
  mentionIndex = Math.min(mentionIndex, Math.max(0, choices.length - 1));
  $("mentions").replaceChildren(...choices.map((tab, index) => {
    const item = document.createElement("li");
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", index === mentionIndex);
    const title = document.createElement("strong"), host = document.createElement("span");
    title.textContent = tab.title; host.textContent = tab.host;
    item.append(title, host);
    item.addEventListener("mousedown", event => { event.preventDefault(); pickMention(tab); });
    return item;
  }));
  visible("mentions", choices.length > 0);
}
function pickMention(tab) {
  if (tab.skill) {
    composer.value = "";
    visible("mentions", false);
    command("draft", {value: "", version: ++draftVersion});
    command("skill", {value: tab.id});
    return;
  }
  const start = composer.selectionStart, before = composer.value.slice(0, start);
  const cut = before.replace(/@[^\s@]*$/, "");
  composer.value = cut + composer.value.slice(start);
  composer.selectionStart = composer.selectionEnd = cut.length;
  if (attached.length < 5) attached.push(tab);
  renderAttached(); renderMentions();
  command("draft", {value: composer.value, version: ++draftVersion});
}
function renderAttached() {
  $("attached").replaceChildren(...attached.map(tab => {
    const chip = document.createElement("span");
    chip.className = "tab-chip";
    chip.textContent = "@" + tab.title;
    const remove = document.createElement("button");
    remove.type = "button"; remove.textContent = "×";
    remove.setAttribute("aria-label", "Remove " + tab.title);
    remove.addEventListener("click", () => { attached = attached.filter(a => a !== tab); renderAttached(); });
    chip.append(remove);
    return chip;
  }));
  $("attached").hidden = !attached.length;
}

function visible(id, show) { $(id).hidden = !show; }
function text(id, value) { $(id).textContent = value || ""; }

// Agents write Markdown. It is drawn with DOM nodes and textContent only:
// never parsed as HTML, links are shown, not followed.
function inline(parent, source) {
  const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\[[^\]\n]+\]\([^)\s]+\))/g;
  let last = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > last) parent.append(source.slice(last, match.index));
    const token = match[0];
    let node;
    if (match[1]) { node = document.createElement("code"); node.textContent = token.slice(1, -1); }
    else if (match[2]) { node = document.createElement("strong"); inline(node, token.slice(2, -2)); }
    else if (match[3]) { node = document.createElement("em"); inline(node, token.slice(1, -1)); }
    else {
      const split = token.indexOf("](");
      node = document.createElement("span");
      node.className = "link";
      node.textContent = token.slice(1, split);
      node.title = token.slice(split + 2, -1);
    }
    parent.append(node);
    last = match.index + token.length;
  }
  if (last < source.length) parent.append(source.slice(last));
}

function markdown(body, source) {
  body.replaceChildren();
  const lines = source.split("\n");
  let paragraph = [], list = null;
  const flush = () => {
    if (paragraph.length) {
      const p = document.createElement("p");
      paragraph.forEach((line, index) => { if (index) p.append(document.createElement("br")); inline(p, line); });
      body.append(p);
      paragraph = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^\s*```/);
    if (fence) {
      flush(); list = null;
      const code = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
      const pre = document.createElement("pre");
      pre.textContent = code.join("\n");
      body.append(pre);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush(); list = null;
      const rows = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(lines[i]);
      i--;
      const cells = row => row.trim().slice(1, -1).split("|").map(cell => cell.trim());
      const table = document.createElement("table");
      rows.forEach((row, index) => {
        if (/^[\s|:-]+$/.test(row)) return;  // The header's divider line.
        const tr = document.createElement("tr");
        for (const cell of cells(row)) {
          const td = document.createElement(index === 0 && rows.length > 1 && /^[\s|:-]+$/.test(rows[1]) ? "th" : "td");
          inline(td, cell);
          tr.append(td);
        }
        table.append(tr);
      });
      const wrap = document.createElement("div");
      wrap.className = "table";
      wrap.append(table);
      body.append(wrap);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
    if (heading) {
      flush(); list = null;
      const h = document.createElement("h" + Math.min(6, heading[1].length + 2));
      inline(h, heading[2]);
      body.append(h);
    } else if (bullet) {
      flush();
      const kind = /\d/.test(bullet[1]) ? "OL" : "UL";
      if (!list || list.tagName !== kind) { list = document.createElement(kind); body.append(list); }
      const item = document.createElement("li");
      inline(item, bullet[2]);
      list.append(item);
    } else if (!line.trim()) {
      flush(); list = null;
    } else {
      list = null;
      paragraph.push(line);
    }
  }
  flush();
}

function messageNode(log, previous, message, agentName) {
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
  const user = message.role === "user";
  node.className = "message " + (user ? "user" : "agent") + (message.earlier ? " earlier" : "");
  node.firstChild.firstChild.textContent = user ? "You" : agentName;
  node.firstChild.lastChild.textContent = user ? message.delivery || "" : message.state === "complete" ? "" : (MESSAGE_STATES[message.state] || message.state);
  // Agent messages and service diagnostics are data, never HTML or commands.
  if (node.dataset.text !== message.text) {
    node.dataset.text = message.text;
    if (user) node.lastChild.textContent = message.text;
    else markdown(node.lastChild, message.text);
  }
  log.append(node);
}

function renderMessages(next, reset) {
  const earlier = (next.earlier || []).map(m => ({...m, earlier: true}));
  const key = JSON.stringify([earlier, next.messages, next.agent_name]);
  if (key === messagesKey) return;
  messagesKey = key;
  const log = $("messages");
  const position = log.scrollTop;
  const atEnd = reset || position + log.clientHeight >= log.scrollHeight - 40;
  const previous = new Map([...log.children].filter(n => n.dataset.id).map(node => [node.dataset.id, node]));
  const divider = log.querySelector(".continued") || Object.assign(document.createElement("div"), {className: "continued"});
  const name = next.agent_name || "Agent";
  for (const message of earlier) messageNode(log, previous, message, name);
  if (earlier.length) { divider.textContent = "Continued after the chat closed"; log.append(divider); } else divider.remove();
  for (const message of next.messages) messageNode(log, previous, message, name);
  for (const node of previous.values()) node.remove();
  log.scrollTop = atEnd ? log.scrollHeight : position;
}

function when(stamp) {
  const time = Date.parse(stamp);
  if (Number.isNaN(time)) return "";
  const minutes = Math.max(0, (Date.now() - time) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${Math.floor(minutes)} min ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} h ago`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)} d ago`;
  return new Date(time).toLocaleDateString(undefined, {month: "short", day: "numeric"});
}

const STATUS_WORDS = {cancelled: "stopped", interrupted: "closed", failed: "error", completed: "ended"};

function renderChats(next) {
  const chats = next.tasks || [];
  const selected = chats.findIndex(chat => chat.id === next.task_id);
  // The open chat stays listed even when it is older than the recent few.
  const shown = showAllChats ? chats : chats.filter((chat, index) => index < RECENT_CHATS || index === selected);
  const list = $("tasks");
  list.replaceChildren(...shown.map(chat => {
    const item = document.createElement("li");
    item.className = "chat-row" + (chat.id === next.task_id ? " current" : "") + (chat.live ? " live" : "");
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(chat.id === next.task_id));
    item.tabIndex = 0;
    item.dataset.id = chat.id;
    const title = document.createElement("span");
    title.className = "chat-title";
    title.textContent = chat.title || chat.label;
    const meta = document.createElement("span");
    meta.className = "chat-meta";
    meta.textContent = [chat.agent, when(chat.when), STATUS_WORDS[chat.status] || chat.status].filter(Boolean).join(", ");
    item.append(title, meta);
    return item;
  }));
  const hidden = chats.length - shown.length;
  visible("more-chats", chats.length > RECENT_CHATS);
  text("more-chats", showAllChats ? "Show fewer" : `${hidden} more`);
}

// Assistants grouped by where they run, then a way to add another.
function agentOptions(agents) {
  const groups = new Map();
  for (const entry of agents) {
    if (!groups.has(entry.where)) groups.set(entry.where, []);
    groups.get(entry.where).push(entry);
  }
  const nodes = [];
  for (const [where, list] of groups) {
    const group = document.createElement("optgroup");
    group.label = where;
    group.append(...list.map(entry => new Option(entry.name, entry.id)));
    nodes.push(group);
  }
  nodes.push(new Option("Add an assistant…", "__add"));
  return nodes;
}

function renderAgents(next) {
  const agents = next.agents || [];
  const known = JSON.stringify(agents.map(a => [a.id, a.name, a.where]));
  for (const id of ["agent", "welcome-agent"]) {
    if ($(id).dataset.known !== known) {
      $(id).replaceChildren(...agentOptions(agents));
      $(id).dataset.known = known;
    }
  }
  const current = agents.find(a => a.id === next.new_agent) || agents[0];
  $("agent").dataset.current = $("welcome-agent").dataset.current = current ? current.id : "";
  if (!current) return;
  $("agent").value = current.id;
  $("welcome-agent").value = current.id;
  if ($("model").dataset.agent !== current.id) {
    $("model").replaceChildren(...current.models.map(([value, label]) => new Option(label, value)));
    $("model").dataset.agent = current.id;
  }
  $("model").value = next.new_model || "";
  visible("model-row", current.models.length > 1);
  visible("continue-session", agents.some(a => a.id === "claude"));
}

window.steedUI = {render(next) {
  const switched = context !== next.context;
  state = next;
  if (switched) { context = next.context; draftVersion = next.draft_version; messagesKey = null; activityOpen = null; }
  if (switched || !next.active || (next.send_result.context === context && next.send_result.version >= pendingSend?.version)) pendingSend = null;
  document.documentElement.dataset.theme = next.dark ? "dark" : "light";
  visible("unsafe", next.yolo);
  visible("paused", next.paused);
  visible("bridge-on", next.bridge);
  visible("hide-sidebar", !!next.docked);
  visible("pause-agents", next.available && !next.paused);
  visible("welcome", !next.usable);
  visible("welcome-start-row", next.available && next.has_provider);
  text("welcome-copy", next.private ? "Agent chats are off in private browsing. Switch to a regular profile to chat." : next.available ? "Ask for anything. It keeps working while you browse." : "Agent chats are unavailable. Open Connection setup from the Agents menu to check your connection.");
  text("welcome-note", next.yolo ? "Agent requests are approved automatically. Turn it off in Agents → Access and Setup to review access." : "Only what you send is shared. You choose what it may do.");
  visible("new-chat", next.available && next.has_provider);
  visible("task-picker", next.available && next.tasks.length > 0);
  visible("chats-toggle", next.available && next.tasks.length > 0);
  renderChats(next);
  renderAgents(next);
  visible("activity", next.usable);
  text("activity-title", next.activity.title);
  text("activity-hint", next.activity.hint);
  text("running-model", next.activity.model);
  // A closed chat already says so in its title.
  text("connection", next.ended ? "" : next.connection.label); $("connection").dataset.tone = next.connection.tone;
  visible("connection", !next.ended && !!next.connection.label);
  text("delivery", next.delivery);
  renderActivityDetails();
  visible("progress", next.activity.busy);
  $("activity-dot").classList.toggle("working", next.activity.busy);
  $("activity-dot").dataset.connection = next.connection.state;
  visible("error", !!next.error); text("error", next.error);
  visible("tool-failure", !!next.failure);
  text("failure-title", next.failure ? "Last tool failure: " + next.failure.title : "");
  text("failure-operation", next.failure ? `${next.failure.command} (${next.failure.code})` : "");
  text("failure-guidance", next.failure?.guidance);
  $("failure-details").dataset.action = next.failure?.action || "receipts";
  $("failure-details").textContent = next.failure?.action === "permissions" ? "Check Permissions" : "Check Receipts";
  visible("attention", next.usable && (next.pending > 0 || next.requests > 0));
  visible("pending", next.usable && next.pending > 0); text("pending", `Review ${next.pending} approval${next.pending === 1 ? "" : "s"}`);
  visible("agent-requests", next.usable && next.requests > 0); text("agent-requests", `Answer the agent (${next.requests})`);
  visible("conversation", next.usable);
  // A closed chat offers what can happen next instead of a dead composer.
  visible("compose", next.usable && !next.ended);
  visible("ended", next.usable && next.ended);
  visible("continue-chat", next.can_continue);
  $("ended-new").classList.toggle("primary", !next.can_continue);
  renderMessages(next, switched);
  visible("empty-chat", next.messages.length === 0 && !(next.earlier || []).length);
  if (switched || next.draft_version >= draftVersion) {
    if (composer.value !== next.draft) composer.value = next.draft;
    draftVersion = next.draft_version;
  }
  composer.disabled = !next.can_draft;
  composer.placeholder = next.active ? `Message ${next.agent_name || "your agent"}…` : "Write a draft while the agent is away…";
  fitComposer();
  renderSend();
  visible("cancel", next.can_cancel); visible("delegate", next.can_delegate);
  visible("options", next.available);
  if (!next.available) $("options").open = false;
  visible("model-control", next.has_provider);
  $("library").disabled = !next.can_library;
  for (const id of ["permissions", "files", "receipts", "clear"]) $(id).disabled = !next.usable;
  text("status", next.status); text("summary", next.summary);
  return true;
}};

function renderSend() {
  $("send").disabled = !state?.active || !!pendingSend;
  $("send").classList.toggle("ready", !!composer.value.trim() && !$("send").disabled);
}

function fitComposer() {
  composer.style.height = "auto";
  composer.style.height = Math.min(composer.scrollHeight, 220) + "px";
}

function renderActivityDetails() {
  const details = [next => next.activity.model, next => next.activity.hint, next => next.delivery].some(f => f(state));
  // A closed chat's bar below already says what can happen next.
  const open = details && (activityOpen ?? (!state.ended && (state.messages.length === 0 || state.connection.tone === "warning")));
  visible("activity-details", open);
  $("activity-toggle").setAttribute("aria-expanded", String(open));
  $("activity-toggle").disabled = !details;
}

function setChatsOpen(open) {
  chatsOpen = open;
  $("workspace").classList.toggle("chats-open", open);
  $("chats-toggle").setAttribute("aria-expanded", String(open));
}

function choose(row) {
  if (!row) return;
  command("select", {value: row.dataset.id});
  setChatsOpen(false);
}

$("activity-toggle").addEventListener("click", () => {
  if (!state) return;
  activityOpen = $("activity-toggle").getAttribute("aria-expanded") !== "true";
  renderActivityDetails();
});
composer.addEventListener("input", () => { fitComposer(); renderSend(); renderMentions(); command("draft", {value: composer.value, version: ++draftVersion}); });
composer.addEventListener("keydown", event => {
  const choices = $("mentions").hidden ? [] : mentionChoices();
  if (choices.length && ["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    mentionIndex = (mentionIndex + (event.key === "ArrowDown" ? 1 : choices.length - 1)) % choices.length;
    renderMentions(); return;
  }
  if (choices.length && (event.key === "Enter" || event.key === "Tab")) { event.preventDefault(); pickMention(choices[mentionIndex]); return; }
  if (choices.length && event.key === "Escape") { event.preventDefault(); visible("mentions", false); return; }
  // Enter sends; Shift+Enter adds a line (Ctrl+Enter also sends).
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
});
composer.addEventListener("blur", () => visible("mentions", false));
$("tasks").addEventListener("click", event => choose(event.target.closest(".chat-row")));
$("tasks").addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(event.target.closest(".chat-row")); }
});
$("more-chats").addEventListener("click", () => { showAllChats = !showAllChats; if (state) renderChats(state); });
$("chats-toggle").addEventListener("click", () => setChatsOpen(!chatsOpen));
$("model").addEventListener("change", () => command("model", {value: $("model").value}));
for (const id of ["agent", "welcome-agent"]) $(id).addEventListener("change", () => {
  if ($(id).value === "__add") {
    $(id).value = $(id).dataset.current || "";
    command("add_assistant");
  } else command("agent", {value: $(id).value});
});
document.addEventListener("click", event => {
  const button = event.target.closest("button[data-action]");
  if (!button || button.disabled) return;
  if (button.dataset.action === "send") send();
  else { if (button.dataset.action === "new_chat") setChatsOpen(false); command(button.dataset.action); }
});
// The relative times in the chat list stay current.
setInterval(() => { if (state) renderChats(state); }, 60000);
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
