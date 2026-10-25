let state = null, bridge = null, sequence = 0, context = null, seen = new Set();
const nodes = new Map(), $ = id => document.getElementById(id);
function command(action, id, value, extra = {}) {
  if (!bridge || !state) return;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, id, value, ...extra}));
}
function children(container, definitions) {
  const wanted = [];
  for (const node of definitions.filter(Boolean)) {
    seen.add(node.id);
    let entry = nodes.get(node.id);
    if (!entry || entry.kind !== node.kind) { entry = build(node); nodes.set(node.id, entry); }
    update(entry, node); wanted.push(entry.element);
  }
  for (const element of [...container.children]) if (!wanted.includes(element)) element.remove();
  wanted.forEach((element, index) => { if (container.children[index] !== element) container.insertBefore(element, container.children[index] || null); });
}
function build(node) {
  const entry = {kind: node.kind}, el = tag => document.createElement(tag);
  let element;
  if (node.kind === 'label') element = el('p');
  else if (node.kind === 'image') element = el('img');
  else if (node.kind === 'group') element = el('section');
  else if (node.kind === 'button') {
    element = el('button'); element.onclick = () => command('click', node.id);
  } else if (node.kind === 'input' || node.kind === 'text' || node.kind === 'number') {
    element = el(node.kind === 'text' ? 'textarea' : 'input');
    if (node.kind !== 'text') element.type = node.kind === 'number' ? 'number' : node.secret ? 'password' : 'text';
    element.oninput = () => command(node.kind === 'number' ? 'number' : 'edit', node.id, node.kind === 'number' ? Number(element.value) : element.value);
    if (node.kind === 'input') element.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); command('enter', node.id); } };
  } else if (node.kind === 'combo') {
    element = el(node.editable ? 'input' : 'select');
    element[node.editable ? 'oninput' : 'onchange'] = () => command(node.editable ? 'edit' : 'choose', node.id, node.editable ? element.value : Number(element.value));
    if (node.editable) { entry.list = el('datalist'); entry.list.id = 'options-' + node.id; element.setAttribute('list', entry.list.id); }
  } else if (node.kind === 'tabs') {
    element = el('section'); entry.nav = el('nav'); entry.body = el('div'); element.append(entry.nav, entry.body);
  } else if (node.kind === 'table') element = el('div');
  else element = el('div');
  element.dataset.control = node.id; entry.element = element;
  return entry;
}
function update(entry, node) {
  const element = entry.element;
  element.dataset.footer = !!node.footer;
  element.className = 'control ' + (node.kind === 'group' ? 'form-group' : node.kind === 'label' ? 'form-label' : node.kind === 'table' ? 'table-wrap' : node.kind);
  element.disabled = !node.enabled;
  if (node.kind === 'image') {
    if (element.src !== node.value) element.src = node.value;
    element.alt = node.text;
  } else if (node.kind === 'label' || node.kind === 'button') {
    element.textContent = (node.checkable ? (node.checked ? '✓ ' : '○ ') : '') + node.text;
    if (node.checkable) element.setAttribute('aria-pressed', node.checked);
  } else if (['input','text','number'].includes(node.kind)) {
    if (document.activeElement !== element && element.value !== String(node.value)) element.value = node.value;
    element.readOnly = !!node.readonly; element.placeholder = node.secret && node.has_value ? 'Stored value unchanged; type to replace' : node.placeholder || '';
    if (node.maximum !== undefined) { if (node.kind === 'number') { element.min = node.minimum; element.max = node.maximum; element.step = node.step; } else element.maxLength = node.maximum; }
  } else if (node.kind === 'combo') {
    const key = JSON.stringify(node.options);
    if (entry.options !== key) {
      const target = entry.list || element; target.replaceChildren();
      node.options.forEach((text, index) => { const option = document.createElement('option'); option.value = node.editable ? text : index; option.textContent = text; target.append(option); });
      entry.options = key;
    }
    if (document.activeElement !== element) element.value = node.editable ? node.value : node.selected;
    if (entry.list && entry.list.parentElement !== document.body) document.body.append(entry.list);
  } else if (node.kind === 'group') {
    if (!entry.body) { entry.heading = document.createElement('h2'); entry.body = document.createElement('div'); element.append(entry.heading, entry.body); }
    entry.heading.textContent = node.title || ''; entry.heading.hidden = !node.title;
    children(entry.body, node.children || []);
  }
  else if (node.kind === 'tabs') {
    const key = JSON.stringify([node.labels, node.selected, node.tab_enabled]);
    if (entry.tabs !== key) {
      entry.nav.replaceChildren();
      node.labels.forEach((name, index) => { const button = document.createElement('button'); button.textContent = name; button.disabled = !node.tab_enabled[index]; button.setAttribute('aria-selected', index === node.selected); button.onclick = () => command('choose', node.id, index); entry.nav.append(button); });
      entry.tabs = key;
    }
    children(entry.body, node.children || []);
  } else if (node.kind === 'table') {
    const key = JSON.stringify(node);
    if (entry.rows === key) return;
    const focusedRow = document.activeElement?.closest('[data-row]')?.dataset.row;
    const restoreFocus = element.contains(document.activeElement);
    entry.rows = key; element.replaceChildren();
    const table = document.createElement('table'), head = document.createElement('thead'), body = document.createElement('tbody'), header = document.createElement('tr');
    node.headers.forEach(text => { const th = document.createElement('th'); th.textContent = text; header.append(th); });
    head.append(header); table.append(head, body);
    node.rows.forEach(row => {
      const tr = document.createElement('tr'); tr.dataset.row = row.id; tr.tabIndex = row.selected || (!node.rows.some(item => item.selected) && row === node.rows[0]) ? 0 : -1; tr.setAttribute('aria-selected', row.selected);
      const send = (action, value, additive = false) => command(action, node.id, value, {item: row.id, additive});
      tr.onclick = event => send('select', null, event.ctrlKey || event.metaKey); tr.ondblclick = () => send('activate');
      tr.onkeydown = event => {
        if (event.key === 'Enter') { event.preventDefault(); send('activate'); }
        else if (event.key === ' ') { event.preventDefault(); send('select'); }
        else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); const next = event.key === 'ArrowDown' ? tr.nextElementSibling : tr.previousElementSibling;
          if (next) { next.tabIndex = 0; next.focus(); command('select', node.id, null, {item: next.dataset.row}); }
        }
      };
      row.cells.forEach((text, index) => {
        const td = document.createElement('td');
        if (index === 0) {
          if (row.checkable) { const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = row.checked; checkbox.onclick = event => { event.stopPropagation(); send('check', checkbox.checked); }; td.append(checkbox); }
          if (row.expandable) { const button = document.createElement('button'); button.textContent = row.expanded ? '−' : '+'; button.className = 'row-button'; button.onclick = event => { event.stopPropagation(); send('expand'); }; td.append(button); }
          if (node.reorder) for (const [label, direction] of [['↑', -1], ['↓', 1]]) { const button = document.createElement('button'); button.className = 'row-button'; button.textContent = label; button.onclick = event => { event.stopPropagation(); send('move', direction); }; td.append(button); }
        }
        td.append(document.createTextNode(text)); tr.append(td);
      });
      body.append(tr);
    });
    const paging = document.createElement('div'); paging.className = 'table-paging';
    const count = document.createElement('span'); count.className = 'row-count'; count.textContent = node.count ? 'Showing ' + (node.offset + 1) + '–' + (node.offset + node.rows.length) + ' of ' + node.count : 'No results';
    paging.append(count);
    if (node.count > 500) for (const [label, direction] of [['Previous', -1], ['Next', 1]]) {
      const button = document.createElement('button'); button.textContent = label;
      button.disabled = direction < 0 ? !node.offset : node.offset + node.rows.length >= node.count;
      button.onclick = () => command('page', node.id, direction); paging.append(button);
    }
    element.append(table, paging);
    if (restoreFocus) [...body.children].find(row => row.dataset.row === focusedRow)?.focus();
  }
}
window.steedUI = {render(next) {
  if (context !== next.context) { context = next.context; sequence = 0; nodes.clear(); $('controls').replaceChildren(); }
  state = next; document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  document.body.classList.toggle('workspace-page', !!next.workspace);
  document.body.dataset.page = next.workspace || 'dialog';
  $('workspace-nav').hidden = !next.workspace;
  $('scope').hidden = !next.workspace;
  $('scope').textContent = next.shared ? 'Browser-wide · viewing ' + (next.profile === 'search' ? 'Incognito' : next.profile) : next.profile === 'search' ? 'Incognito · temporary session' : 'Profile · ' + next.profile;
  if (next.workspace && $('workspace-nav').dataset.page !== next.workspace) {
    const nav = $('workspace-nav'); nav.replaceChildren(); nav.dataset.page = next.workspace;
    for (const [heading, entries] of [
      ['Browse', [['history','History'],['bookmarks','Bookmarks'],['downloads','Downloads'],['cookies','Cookies'],['passwords','Passwords'],['library','Files']]],
      ['Customize', [['settings','Settings'],['style','Style'],['layout','Layout'],['newtab-settings','Start pages'],['tab-groups','Tab groups'],['adblock','Ad blocking'],['permissions','Website permissions']]],
      ['Agents', [['agents','Conversations'],['tasks','Tasks & permissions'],['connections','Connections'],['services','Add a service'],['agent-setup','Agent setup']]]
    ]) {
      const title = document.createElement('h2'); title.textContent = heading; nav.append(title);
      for (const [page, name] of entries) { const button = document.createElement('button'); button.textContent = name; button.setAttribute('aria-current', page === next.workspace ? 'page' : 'false'); button.onclick = () => command('workspace', null, page); nav.append(button); }
    }
  }
  $('title').textContent = next.title; $('error').textContent = next.error; $('error').hidden = !next.error;
  seen = new Set(); children($('controls'), next.children);
  for (const [id, entry] of nodes) if (!seen.has(id)) { entry.list?.remove(); nodes.delete(id); }
  return true;
}};
$('close').onclick = () => command('close');
document.addEventListener('keydown', event => { if (event.key === 'Escape') command('close'); });
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
