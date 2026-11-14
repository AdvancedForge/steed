let bridge = null, state = null, context = null, sequence = 0, key = null, hover = null;
function command(action, id, button) {
  if (!bridge || !state) return;
  const rect = button?.getBoundingClientRect();
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, id, x: rect ? Math.min(innerWidth, rect.right) : 0, y: rect ? rect.top : 0}));
}
// Line icons (24px grid, drawn in the row's ink) for the items people look for.
const ICONS = {
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  undo: 'M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3',
  close: 'M6 6l12 12M18 6 6 18',
  back: 'M15 18l-6-6 6-6',
  forward: 'M9 18l6-6-6-6',
  reload: 'M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5',
  home: 'M4 11l8-7 8 7M6 9.5V20h12V9.5',
  layers: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5',
  clock: 'M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  star: 'M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z',
  bookmarks: 'M7 3h10v18l-5-4-5 4z',
  spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z',
  columns: 'M4 4h7v16H4zM13 4h7v16h-7z',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z',
  brush: 'M14 4l6 6-8.5 8.5a3 3 0 0 1-4.2 0l-1.8-1.8a3 3 0 0 1 0-4.2zM4 20l3-3',
  layout: 'M4 4h16v16H4zM4 9h16M10 9v11',
  key: 'M14.5 9.5a4.5 4.5 0 1 1-1.3-3.2M13 11l8 8M18 16l2-2M16 14l2-2',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  mic: 'M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM6 11a6 6 0 0 0 12 0M12 17v4',
  puzzle: 'M9 4h4v3a1.5 1.5 0 0 0 3 0V4h4v6h-3a1.5 1.5 0 0 0 0 3h3v7h-6v-3a1.5 1.5 0 0 0-3 0v3H4v-6h3a1.5 1.5 0 0 0 0-3H4V4z',
  import: 'M12 3v12M7 10l5 5 5-5M4 15v5h16v-5',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  lock: 'M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 0 1 7 0V11',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  exit: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM16 16l4.5 4.5',
  command: 'M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z',
  'zoom-in': 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM16 16l4.5 4.5M8 11h6M11 8v6',
  'zoom-out': 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM16 16l4.5 4.5M8 11h6',
  book: 'M4 5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2zM4 19a2 2 0 0 0 2 2h13',
  translate: 'M4 5h9M8.5 3v2M6 5c.5 3 3 6 6 7M11 5c-.5 3-3 6-6.5 7M13 20l4-9 4 9M14.5 17h5',
  camera: 'M4 8h3l2-3h6l2 3h3v11H4zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z',
  save: 'M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6',
  code: 'M9 7l-5 5 5 5M15 7l5 5-5 5',
  chat: 'M5 5h14v10H10l-5 4z',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 21a7.5 7.5 0 0 1 15 0',
  users: 'M9 12a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM3 20a6 6 0 0 1 12 0M16 5.2a3.5 3.5 0 0 1 0 6.6M17.5 14.5A6 6 0 0 1 21 20',
  cookie: 'M20.5 12.5A8.5 8.5 0 1 1 11.5 3.5a3 3 0 0 0 4 4 3 3 0 0 0 5 5zM9 10h.01M14 15h.01M9 15h.01',
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('icon');
  if (ICONS[name]) { const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', ICONS[name]); svg.append(path); }
  return svg;
}
window.steedUI = {render(next) {
  if (context !== next.context) { context = next.context; sequence = 0; key = null; clearTimeout(hover); }
  state = next; document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  document.documentElement.classList.toggle('glass', !!next.glass);
  document.documentElement.classList.toggle('motion', !!next.motion);
  // The browser's toolbar colours (checked: six-digit hex only).
  const css = {menu: '--surface', field: '--field', ink: '--ink', muted: '--muted', border: '--border', hover: '--soft', accent: '--accent'};
  for (const [key, name] of Object.entries(css)) {
    const value = next.colors?.[key];
    if (typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value)) document.documentElement.style.setProperty(name, value);
    else document.documentElement.style.removeProperty(name);
  }
  document.getElementById('title').textContent = next.title;
  // Right-click menus show only their items: no title bar or settings footer.
  document.body.classList.toggle('context-menu', !!next.context_menu && !next.back);
  document.querySelector('footer').hidden = !!next.context_menu;
  document.getElementById('back').hidden = !next.back;
  const signature = JSON.stringify(next.rows); if (key === signature) return true; key = signature;
  const items = document.getElementById('items'); items.replaceChildren();
  items.classList.toggle('marks', next.rows.some(row => row.checkable || row.checked));
  items.classList.toggle('icons', next.rows.some(row => row.icon));
  for (const row of next.rows) {
    if (row.separator) { items.append(document.createElement('hr')); continue; }
    const button = document.createElement('button'); button.disabled = !row.enabled; button.dataset.action = row.id; button.setAttribute('role', 'menuitem');
    button.append(icon(row.checked ? 'check' : row.icon)); button.classList.toggle('checked', !!row.checked); for (const [cls, text] of [['mark', row.checked ? '✓' : ''], ['label', row.text], ['shortcut', row.shortcut], ['arrow', row.submenu ? '›' : '']]) { const span = document.createElement('span'); span.className = cls; span.textContent = text; button.append(span); }
    button.onclick = () => { clearTimeout(hover); command('activate', row.id, button); };
    // Only sub-menus open on hover; anything else waits for a click.
    if (row.submenu) button.onpointerenter = () => { clearTimeout(hover); hover = setTimeout(() => command("hover", row.id, button), 300); };
    button.onpointerleave = () => clearTimeout(hover);
    items.append(button);
  }
  return true;
}};
document.getElementById('back').onclick = () => command('back');
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { event.preventDefault(); command('close'); }
  if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault(); const buttons = [...document.querySelectorAll('nav button:not(:disabled)')], index = buttons.indexOf(document.activeElement);
    buttons[(index + (event.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length]?.focus();
  }
});
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });

document.getElementById("open-settings").onclick = () => command("settings");
