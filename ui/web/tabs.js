const $ = id => document.getElementById(id);
const ACCENTS = {blue: "#3b6fe0", purple: "#8b5cf6", green: "#1b998b", orange: "#e07b1a", pink: "#d6457a", slate: "#5c6f84"};
let bridge = null, state = null, dragged = null, focusedId = null;

function command(action, values = {}) {
  if (!bridge || !state) return;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, action, ...values}));
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function favicon(tab) {
  const holder = element("span", "favicon");
  if (tab.loading) { holder.classList.add("loading"); return holder; }
  if (tab.icon && state.appearance.favicons) {
    const image = element("img");
    image.src = tab.icon; image.alt = "";
    holder.append(image);
  } else if (state.appearance.favicons) {
    holder.classList.add("letter");
    holder.textContent = (tab.site || tab.title || "•").trim().charAt(0).toUpperCase();
  } else {
    holder.hidden = true;
  }
  return holder;
}

function tabNode(tab, siteColor) {
  const node = element("div", "tab");
  node.setAttribute("role", "tab");
  node.setAttribute("aria-selected", tab.active ? "true" : "false");
  node.tabIndex = tab.id === focusedId || (!focusedId && tab.active) ? 0 : -1;
  node.dataset.id = tab.id;
  node.dataset.index = tab.index;
  const rest = {frozen: "Frozen to save resources; resumes when selected", unloaded: "Unloaded to save memory; reloads when selected"}[tab.lifecycle];
  // Steed's own hover card describes the tab; a title would add a second,
  // plain tooltip on top of it. Screen readers still get the description.
  node.setAttribute("aria-description", (tab.site ? `${tab.title}, ${tab.site}` : tab.title) + (rest ? `. ${rest}` : "") + (tab.keep_loaded ? ". Kept loaded" : ""));
  if (rest) node.classList.add("dormant", tab.lifecycle);
  if (tab.active) node.classList.add("active");
  if (tab.pinned) {
    node.classList.add("pinned");
    node.setAttribute("aria-label", `${tab.title || "Pinned tab"} (pinned)`);
    node.append(favicon(tab));
    if (tab.audible || tab.muted) node.classList.add("noisy");
    return node;
  }
  if (tab.group && state.appearance.grouping === "manual") {
    node.classList.add("grouped");
    node.style.setProperty("--group", tab.group_color);
  }
  if (siteColor) node.style.setProperty("--site", siteColor);
  node.draggable = state.appearance.grouping !== "site" || !vertical();
  node.append(favicon(tab));
  node.append(element("span", "title", tab.title || "New Tab"));
  if (tab.audible || tab.muted) {
    const audio = element("button", "audio", tab.muted ? "🔇" : "🔊");
    audio.type = "button"; audio.tabIndex = -1;
    audio.setAttribute("aria-label", tab.muted ? "Unmute tab" : "Mute tab");
    audio.dataset.action = "mute";
    node.append(audio);
  }
  const close = element("button", "close", "×");
  close.type = "button"; close.tabIndex = -1;
  close.setAttribute("aria-label", `Close ${tab.title || "tab"}`);
  close.dataset.action = "close";
  node.append(close);
  return node;
}

function groupChip(name, color, members, collapsed) {
  const chip = element("button", "group-chip", name);
  chip.type = "button";
  chip.style.setProperty("--group", color);
  chip.dataset.group = name;
  chip.setAttribute("aria-expanded", collapsed ? "false" : "true");
  chip.title = `${name} · ${members} tab${members === 1 ? "" : "s"}${collapsed ? " (collapsed)" : ""}`;
  if (collapsed) chip.append(element("span", "group-count", String(members)));
  return chip;
}

function vertical() { return ["left", "right"].includes(state.appearance.position); }

// The glass bottom line opens under the current tab, between its side outlines.
function placeGap() {
  const tab = document.querySelector(".tab.active"), root = document.documentElement.style;
  const box = tab ? tab.getBoundingClientRect() : null;
  root.setProperty("--gap-from", box ? `${Math.round(box.left + 1)}px` : "0px");
  root.setProperty("--gap-to", box ? `${Math.round(box.right - 1)}px` : "0px");
}
addEventListener("resize", placeGap);
document.addEventListener("scroll", placeGap, true);

function render(next) {
  const strip = $("strip"), list = $("tabs");
  const scroll = vertical() ? list.scrollTop : list.scrollLeft;
  const look = next.appearance;
  strip.className = ["strip", `pos-${look.position}`, `style-${look.style}`, `size-${look.size}`,
    `close-${look.close_button}`, `overflow-${look.overflow}`, `density-${next.density}`,
    vertical() ? "vertical" : "horizontal"].join(" ");
  document.documentElement.style.setProperty("--row", `${next.row_height}px`);
  const toolbar = /^#[0-9a-fA-F]{6}$/.test(next.chrome?.toolbar || "") ? next.chrome.toolbar : "";
  if (toolbar) document.documentElement.style.setProperty("--toolbar", toolbar);
  else document.documentElement.style.removeProperty("--toolbar");
  document.documentElement.classList.toggle("native-dark", !!next.chrome?.dark);
  // The toolbar's own colours for text, borders, hovers and the accent.
  const names = {ink: "--ink", muted: "--muted", border: "--border", hover: "--soft", strip: "--strip-bg"};
  for (const [key, name] of Object.entries(names)) {
    const value = next.chrome?.palette?.[key];
    if (typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value)) document.documentElement.style.setProperty(name, value);
    else document.documentElement.style.removeProperty(name);
  }
  const glow = /^#[0-9a-fA-F]{6}$/.test(next.chrome?.glow || "") ? next.chrome.glow : "";
  document.documentElement.classList.toggle("glass", !!next.chrome?.glass);
  document.documentElement.classList.toggle("strip-chosen", (next.chrome?.chosen || []).includes("strip"));
  // Frosted glass over the New Tab picture (a JPEG Steed made).
  const frost = /^data:image\/jpeg;base64,[A-Za-z0-9+\/=]+$/.test(next.chrome?.frost || "") ? next.chrome.frost : "";
  document.documentElement.classList.toggle("frost", !!frost);
  if (frost) document.documentElement.style.setProperty("--frost", `url("${frost}")`);
  else document.documentElement.style.removeProperty("--frost");
  if (glow) document.documentElement.style.setProperty("--glow", glow);
  else document.documentElement.style.removeProperty("--glow");
  // A tab accent chosen in Tab appearance wins; otherwise the toolbar's.
  const toolbarAccent = next.chrome?.palette?.accent;
  const accent = ACCENTS[look.accent] || (/^#[0-9a-fA-F]{6}$/.test(toolbarAccent || "") ? toolbarAccent : "");
  if (accent) document.documentElement.style.setProperty("--tab-accent", accent);
  else document.documentElement.style.removeProperty("--tab-accent");

  const nodes = [];
  const grouping = look.grouping;
  const pinned = next.tabs.filter(tab => tab.pinned);
  const unpinned = next.tabs.filter(tab => !tab.pinned);
  if (pinned.length) {
    const row = element("div", "pinned-row");
    row.append(...pinned.map(tab => tabNode(tab, null)));
    nodes.push(row);
  }
  if (grouping === "site" && vertical()) {
    const order = [], bySite = new Map();
    for (const tab of unpinned) {
      const site = tab.site || "Other";
      if (!bySite.has(site)) { bySite.set(site, []); order.push(site); }
      bySite.get(site).push(tab);
    }
    for (const site of order) {
      const members = bySite.get(site);
      const color = next.site_colors[site] || "var(--muted)";
      if (order.length > 1) nodes.push(groupChip(site, color, members.length, false));
      for (const tab of members) nodes.push(tabNode(tab, color));
    }
  } else {
    let previous = null;
    for (const tab of unpinned) {
      const group = grouping === "manual" ? tab.group : null;
      if (group && group !== previous) {
        const members = next.tabs.filter(t => t.group === group).length;
        nodes.push(groupChip(group, tab.group_color, members, next.collapsed.includes(group)));
      }
      previous = group;
      if (grouping === "manual" && !tab.visible) continue;
      const site = grouping === "site" ? next.site_colors[tab.site] : null;
      nodes.push(tabNode(tab, site));
    }
  }
  const inline = look.new_tab_button === "after_tabs";
  if (inline) {
    const button = element("button", "new-tab", "+");
    button.type = "button"; button.id = "new-inline";
    button.setAttribute("aria-label", "New tab"); button.title = "New tab (Ctrl+T)";
    nodes.push(button);
  }
  list.replaceChildren(...nodes);
  list.setAttribute("aria-orientation", vertical() ? "vertical" : "horizontal");
  $("new-end").hidden = look.new_tab_button !== "end";
  $("count").hidden = !look.tab_count;
  $("count").textContent = `${next.tabs.length} tab${next.tabs.length === 1 ? "" : "s"}`;
  if (vertical()) list.scrollTop = scroll; else list.scrollLeft = scroll;
  placeGap();
  const active = list.querySelector(".tab.active");
  if (active && !dragged) active.scrollIntoView({block: "nearest", inline: "nearest"});
  if (focusedId) list.querySelector(`[data-id="${focusedId}"]`)?.focus();
  reportHeight();
}

function reportHeight() {
  if (!state || vertical() || state.appearance.overflow !== "wrap") return;
  // Measure the rows themselves; the strip fills whatever height it is given.
  const strip = getComputedStyle($("strip"));
  const rows = [...$("tabs").children].reduce((end, child) => Math.max(end, child.offsetTop + child.offsetHeight), 0);
  const height = Math.ceil(rows + parseFloat(strip.paddingTop) + parseFloat(strip.paddingBottom));
  if (Math.abs(height - state.content_height) > 1) command("size", {height});
}

function tabOf(event) { return event.target.closest(".tab"); }

// Hovering a tab for a moment asks Steed for its preview.
let hoverTimer = null, hovered = null;
document.addEventListener("mouseover", event => {
  const tab = event.target.closest ? event.target.closest(".tab") : null;
  if (tab === hovered) return;
  hovered = tab; clearTimeout(hoverTimer);
  if (!tab) { command("unhover"); return; }
  hoverTimer = setTimeout(() => {
    const box = tab.getBoundingClientRect();
    command("hover", {id: tab.dataset.id, x: Math.round(box.left), y: Math.round(box.bottom)});
  }, 500);
});
document.addEventListener("mouseleave", () => { hovered = null; clearTimeout(hoverTimer); command("unhover"); });
document.addEventListener("mousedown", () => { hovered = null; clearTimeout(hoverTimer); command("unhover"); });

function menuAt(node, event) {
  const box = node.getBoundingClientRect();
  const x = event && event.clientX !== undefined && event.type !== "keydown" ? event.clientX : box.left;
  const y = event && event.clientY !== undefined && event.type !== "keydown" ? event.clientY : box.bottom;
  return {x: Math.round(x), y: Math.round(y)};
}

document.addEventListener("click", event => {
  if (event.target.closest(".new-tab")) { command("new"); return; }
  const chip = event.target.closest(".group-chip");
  if (chip) {
    if (state.appearance.grouping === "manual") command("toggle_group", {group: chip.dataset.group});
    return;
  }
  const tab = tabOf(event);
  if (!tab) return;
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "close") command("close", {id: tab.dataset.id});
  else if (action === "mute") command("mute", {id: tab.dataset.id});
  else command("select", {id: tab.dataset.id});
});

document.addEventListener("auxclick", event => {
  const tab = tabOf(event);
  if (event.button === 1 && tab) { event.preventDefault(); command("close", {id: tab.dataset.id}); }
});
document.addEventListener("mousedown", event => { if (event.button === 1) event.preventDefault(); });

document.addEventListener("dblclick", event => {
  if (!event.target.closest(".tab, .group-chip, button")) command("new");
});

document.addEventListener("contextmenu", event => {
  event.preventDefault();
  const tab = tabOf(event);
  const chip = event.target.closest(".group-chip");
  if (tab) command("context", {id: tab.dataset.id, ...menuAt(tab, event)});
  else if (chip && state.appearance.grouping === "manual") command("group_context", {group: chip.dataset.group, ...menuAt(chip, event)});
});

$("tabs").addEventListener("wheel", event => {
  if (vertical() || state.appearance.overflow === "wrap") return;
  const list = $("tabs");
  if (list.scrollWidth <= list.clientWidth) return;
  event.preventDefault();
  list.scrollLeft += Math.abs(event.deltaY) > Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
  placeGap();
}, {passive: false});

document.addEventListener("keydown", event => {
  const tab = tabOf(event);
  if (!tab) return;
  const tabs = [...document.querySelectorAll(".tab")];
  const position = tabs.indexOf(tab);
  const back = vertical() ? "ArrowUp" : "ArrowLeft", forward = vertical() ? "ArrowDown" : "ArrowRight";
  let target = null;
  if (event.key === back) target = tabs[Math.max(0, position - 1)];
  else if (event.key === forward) target = tabs[Math.min(tabs.length - 1, position + 1)];
  else if (event.key === "Home") target = tabs[0];
  else if (event.key === "End") target = tabs[tabs.length - 1];
  else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); command("select", {id: tab.dataset.id}); return; }
  else if (event.key === "Delete") { command("close", {id: tab.dataset.id}); return; }
  else if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
    event.preventDefault(); command("context", {id: tab.dataset.id, ...menuAt(tab, event)}); return;
  }
  if (target) {
    event.preventDefault();
    focusedId = target.dataset.id;
    tabs.forEach(t => { t.tabIndex = t === target ? 0 : -1; });
    target.focus();
  }
});
document.addEventListener("focusout", event => { if (!event.relatedTarget?.closest?.(".tab")) focusedId = null; });

const TAB_TYPE = "application/x-steed-tab";
const token = () => [...crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2, "0")).join("");
const foreign = event => !dragged && event.dataTransfer.types.includes(TAB_TYPE);

document.addEventListener("dragstart", event => {
  const tab = tabOf(event);
  if (!tab) return;
  dragged = tab.dataset.id;
  // Another window's strip accepts the drop only with this one-time token.
  const ticket = token();
  command("drag_start", {id: dragged, token: ticket});
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData(TAB_TYPE, ticket);
  event.dataTransfer.setData("text/plain", "");
  tab.classList.add("dragging");
});
document.addEventListener("dragover", event => {
  if (foreign(event) && !tabOf(event)) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; return; }
  const tab = tabOf(event);
  if (!(dragged || foreign(event)) || !tab) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  document.querySelectorAll(".drop-before, .drop-after").forEach(n => n.classList.remove("drop-before", "drop-after"));
  const box = tab.getBoundingClientRect();
  const after = vertical() ? event.clientY > box.top + box.height / 2 : event.clientX > box.left + box.width / 2;
  tab.classList.add(after ? "drop-after" : "drop-before");
});
document.addEventListener("drop", event => {
  const tab = tabOf(event);
  if (foreign(event)) {
    event.preventDefault();
    const ticket = event.dataTransfer.getData(TAB_TYPE);
    let to = state.tabs.length;
    if (tab) to = Number(tab.dataset.index) + (tab.classList.contains("drop-after") ? 1 : 0);
    document.querySelectorAll(".drop-before, .drop-after").forEach(n => n.classList.remove("drop-before", "drop-after"));
    if (/^[0-9a-f]{32}$/.test(ticket)) command("receive", {token: ticket, to});
    return;
  }
  if (!dragged || !tab) return;
  event.preventDefault();
  const from = state.tabs.findIndex(t => t.id === dragged);
  const target = Number(tab.dataset.index);
  const after = tab.classList.contains("drop-after");
  let to = after ? target + 1 : target;
  if (to > from) to -= 1;
  if (from >= 0 && to !== from) command("move", {id: dragged, to});
});
document.addEventListener("dragend", event => {
  // Dropped outside every tab strip: tear the tab off into a new window.
  if (dragged && event.dataTransfer.dropEffect === "none")
    command("detach", {id: dragged});
  dragged = null;
  document.querySelectorAll(".dragging, .drop-before, .drop-after").forEach(n => n.classList.remove("dragging", "drop-before", "drop-after"));
});

new ResizeObserver(reportHeight).observe($("tabs"));

window.steedUI = {render(next) {
  // The surface can publish before the strip's first state arrives.
  if (!next || !next.appearance || !Array.isArray(next.tabs)) return true;
  state = next;
  document.documentElement.dataset.theme = next.dark ? "dark" : "light";
  render(next);
  return true;
}};
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
