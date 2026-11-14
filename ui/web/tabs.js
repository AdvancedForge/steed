const $ = id => document.getElementById(id);
const ACCENTS = {blue: "#3b6fe0", purple: "#8b5cf6", green: "#1b998b", orange: "#e07b1a", pink: "#d6457a", slate: "#5c6f84"};
let bridge = null, state = null, dragged = null, focusedId = null;
// Lumen: tabs seen in the last render (new ones grow in) and the light that
// slides behind the current tab.
let knownTabs = null, litFrom = null;
const lit = Object.assign(document.createElement("div"), {id: "lit"});
lit.setAttribute("aria-hidden", "true");

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

// 24×24 stroke icons in the toolbar's style (chrome_style.ICONS).
const GLYPHS = {
  newtab: '<path d="M12 3.5l1.9 5.2 5.6 1.8-5.6 1.8L12 17.5l-1.9-5.2-5.6-1.8 5.6-1.8z"/><path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  steed: '<path d="M6.5 20V11a5.5 5.5 0 0 1 11 0v9"/><path d="M4.5 20h4M15.5 20h4"/><path d="M9.5 9.5h.01M14.5 9.5h.01M8.8 14h.01M15.2 14h.01"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
  history: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  bookmarks: '<path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6z"/>',
  downloads: '<path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 19h14"/>',
  passwords: '<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l3 3M14 9l2 2"/>',
  cookies: '<path d="M20 12.5A8 8 0 1 1 11.5 4a3 3 0 0 0 4 4 3 3 0 0 0 4.5 4.5z"/><path d="M9 10h.01M8 15h.01M13 14h.01"/>',
  style: '<path d="M4 20c4 0 5-2 5-4a3 3 0 0 1 3-3l7-8-2-2-8 7a3 3 0 0 1-3 3c-2 0-2 3-2 7z"/>',
  agents: '<path d="M5 6h14v9H9l-4 3.5z"/><path d="M9.5 10.5h.01M14.5 10.5h.01"/>',
  library: '<path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>',
  permissions: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/>',
  apps: '<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>',
  globe: '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4c2.5 2.5 3.5 5.2 3.5 8s-1 5.5-3.5 8c-2.5-2.5-3.5-5.2-3.5-8s1-5.5 3.5-8z"/>',
};
GLYPHS.welcome = GLYPHS.steed;
GLYPHS.files = GLYPHS.library;
GLYPHS.access = GLYPHS.permissions;
GLYPHS.panels = GLYPHS.apps;
GLYPHS.watches = '<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>';

function glyph(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  // Fixed markup from GLYPHS above, never page text.
  svg.innerHTML = GLYPHS[name] || GLYPHS.globe;
  return svg;
}

function favicon(tab) {
  const holder = element("span", "favicon");
  if (tab.loading) { holder.classList.add("loading"); return holder; }
  if (tab.icon && state.appearance.favicons) {
    const image = element("img");
    image.src = tab.icon; image.alt = "";
    holder.append(image);
  } else if (state.appearance.favicons) {
    // No icon: Steed's pages get their own, other sites a quiet globe.
    const steed = !!tab.page;
    holder.classList.add("glyph", steed ? "steed" : "site");
    holder.append(glyph(steed ? (GLYPHS[tab.page] ? tab.page : "steed") : "globe"));
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
  applyLight(next.chrome?.light);
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
  const lumen = document.documentElement.classList.contains("lumen");
  if (lumen && knownTabs) {
    for (const node of nodes.flatMap(n => n.classList.contains("tab") ? [n] : [...n.querySelectorAll(".tab")])) {
      if (!knownTabs.has(node.dataset.id)) node.classList.add("entering");
    }
  }
  knownTabs = new Set(next.tabs.map(tab => tab.id));
  list.replaceChildren(...nodes);
  if (lumen) list.append(lit);
  list.setAttribute("aria-orientation", vertical() ? "vertical" : "horizontal");
  $("new-end").hidden = look.new_tab_button !== "end";
  $("count").hidden = !look.tab_count;
  $("count").textContent = `${next.tabs.length} tab${next.tabs.length === 1 ? "" : "s"}`;
  if (vertical()) list.scrollTop = scroll; else list.scrollLeft = scroll;
  placeGap();
  const active = list.querySelector(".tab.active");
  if (active && !dragged) active.scrollIntoView({block: "nearest", inline: "nearest"});
  if (focusedId) list.querySelector(`[data-id="${focusedId}"]`)?.focus();
  if (lumen) placeLit(active);
  reportHeight();
}

// The window's light field, the same one lumen.py paints around the page:
// glows are [x, y, radius] as shares of the window and #AARRGGBB colours.
function applyLight(light) {
  const root = document.documentElement;
  const valid = light && /^#[0-9a-fA-F]{6}$/.test(light.base) && Array.isArray(light.glows)
    && light.glows.length === 3 && Array.isArray(light.origin) && Array.isArray(light.size);
  root.classList.toggle("lumen", !!valid);
  root.classList.toggle("motion", !!valid && !!light.motion);
  if (!valid) return;
  const [ox, oy] = light.origin.map(Number), [w, h] = light.size.map(Number);
  const reach = Math.max(w, h);
  root.style.setProperty("--light-base", light.base);
  light.glows.forEach(([x, y, r, color], i) => {
    const n = i + 1;
    const css = /^#[0-9a-fA-F]{8}$/.test(color) ? `#${color.slice(3)}${color.slice(1, 3)}` : "transparent";
    root.style.setProperty(`--x${n}`, `${x * w - ox}px`);
    root.style.setProperty(`--y${n}`, `${y * h - oy}px`);
    root.style.setProperty(`--r${n}`, `${Math.max(1, r * reach)}px`);
    root.style.setProperty(`--light-${n}`, css);
  });
}

// The lit capsule behind the current tab glides from the last one.
function placeLit(active) {
  if (!active || active.classList.contains("pinned") && !active.offsetWidth) { lit.hidden = true; return; }
  lit.hidden = false;
  const box = {left: `${active.offsetLeft}px`, top: `${active.offsetTop}px`,
    width: `${active.offsetWidth}px`, height: `${active.offsetHeight}px`};
  Object.assign(lit.style, box);
  const moved = litFrom && litFrom.id !== active.dataset.id;
  if (moved && document.documentElement.classList.contains("motion")
      && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    lit.animate([litFrom.box, box], {duration: 320, easing: "cubic-bezier(.22, 1, .36, 1)"});
  }
  litFrom = {id: active.dataset.id, box};
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
