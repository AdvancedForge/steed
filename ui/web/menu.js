let bridge = null, state = null, context = null, sequence = 0, key = null, hover = null;
function command(action, id, button) {
  if (!bridge || !state) return;
  const rect = button?.getBoundingClientRect();
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, id, x: rect ? Math.min(innerWidth, rect.right) : 0, y: rect ? rect.top : 0}));
}
window.steedUI = {render(next) {
  if (context !== next.context) { context = next.context; sequence = 0; key = null; clearTimeout(hover); }
  state = next; document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  document.documentElement.classList.toggle('glass', !!next.glass);
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
  for (const row of next.rows) {
    if (row.separator) { items.append(document.createElement('hr')); continue; }
    const button = document.createElement('button'); button.disabled = !row.enabled; button.dataset.action = row.id; button.setAttribute('role', 'menuitem');
    for (const [cls, text] of [['mark', row.checked ? '✓' : ''], ['label', row.text], ['shortcut', row.shortcut], ['arrow', row.submenu ? '›' : '']]) { const span = document.createElement('span'); span.className = cls; span.textContent = text; button.append(span); }
    button.onclick = () => { clearTimeout(hover); command('activate', row.id, button); };
    if (row.submenu || row.editor) button.onpointerenter = () => { clearTimeout(hover); hover = setTimeout(() => command('hover', row.id, button), 700); };
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
