const $ = id => document.getElementById(id);
let bridge = null, state = null, shown = [], selected = 0;

function command(action, values = {}) {
  if (!bridge || !state) return;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context: state.context, action, ...values}));
}

// Subsequence match scored for contiguous runs and word starts; null if absent.
function score(query, text) {
  if (!query) return 0;
  const haystack = text.toLowerCase();
  let position = -1, total = 0, run = 0;
  for (const char of query) {
    const next = haystack.indexOf(char, position + 1);
    if (next < 0) return null;
    run = next === position + 1 ? run + 1 : 0;
    const start = next === 0 || " ›·/.-_".includes(haystack[next - 1]);
    total += 1 + run * 2 + (start ? 3 : 0) - Math.min(next - position, 10) * 0.05;
    position = next;
  }
  return total;
}

function filter() {
  let query = $('query').value.trim().toLowerCase(), kind = null;
  const prefixes = {'>': 'command', '@': 'tab', '#': 'history', '*': 'bookmark'};
  if (prefixes[query[0]]) { kind = prefixes[query[0]]; query = query.slice(1).trim(); }
  const ranked = [];
  for (const row of state.rows) {
    // * lists saved tab groups with the bookmarks.
    if (kind && row.kind !== kind && !(kind === 'bookmark' && row.kind === 'group') && !(kind === 'tab' && row.kind === 'closed')) continue;
    // With nothing typed, show tabs rather than every command or visited page.
    if (!kind && !query && row.kind !== 'tab') continue;
    const best = Math.max(score(query, row.title) ?? -Infinity, (score(query, row.detail || '') ?? -Infinity) - 2);
    if (best === -Infinity) continue;
    ranked.push([best + ({tab: 0.5, group: 0.4, bookmark: 0.3}[row.kind] || 0), row]);
  }
  ranked.sort((a, b) => b[0] - a[0]);
  shown = ranked.slice(0, 60).map(entry => entry[1]);
  selected = 0;
  draw();
}

function draw() {
  const list = $('results'); list.replaceChildren();
  shown.forEach((row, index) => {
    const item = document.createElement('li');
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', index === selected ? 'true' : 'false');
    item.className = row.kind + (index === selected ? ' selected' : '') + (row.current ? ' current' : '');
    const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = {tab: 'Tab', closed: 'Closed tab', history: 'History', bookmark: 'Bookmark', group: 'Group'}[row.kind] || 'Command';
    const text = document.createElement('span'); text.className = 'text';
    const title = document.createElement('span'); title.className = 'title'; title.textContent = row.title;
    const detail = document.createElement('span'); detail.className = 'detail'; detail.textContent = row.detail || '';
    text.append(title, detail); item.append(badge, text);
    if (row.shortcut) { const key = document.createElement('kbd'); key.textContent = row.shortcut; item.append(key); }
    item.onmousemove = () => { if (selected !== index) { selected = index; draw(); } };
    item.onclick = () => command('choose', {id: row.id});
    list.append(item);
  });
  $('empty').hidden = shown.length > 0;
  list.querySelector('.selected')?.scrollIntoView({block: 'nearest'});
}

$('query').addEventListener('input', filter);
$('query').addEventListener('keydown', event => {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (shown.length) { selected = (selected + (event.key === 'ArrowDown' ? 1 : shown.length - 1)) % shown.length; draw(); }
  } else if (event.key === 'Enter') {
    event.preventDefault();
    if (shown[selected]) command('choose', {id: shown[selected].id});
  } else if (event.key === 'Escape') {
    event.preventDefault(); command('close');
  }
});

window.steedPalette = {focus() { $('query').focus(); $('query').select(); }};
window.steedUI = {render(next) {
  if (!next || !Array.isArray(next.rows)) return true;
  const fresh = !state || state.context !== next.context;
  state = next;
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  if (fresh) { $('query').value = next.mode ? next.mode + ' ' : ''; filter(); }
  $('query').focus();
  return true;
}};
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
