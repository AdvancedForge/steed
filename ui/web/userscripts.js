const $ = id => document.getElementById(id);
let state = null, bridge = null, sequence = 0, context = null, busy = false, editing = null;
function command(action, extra = {}) {
  if (!bridge || !state || busy) return;
  busy = true;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, ...extra}));
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(label, action) { const b = element('button', '', label); b.onclick = action; return b; }
function edit(entry) {
  editing = entry ? entry.id : null;
  $('editor').hidden = false;
  $('editor-title').textContent = entry ? 'Edit' : 'New';
  $('name').value = entry ? entry.name : 'My tweak';
  $('sites').value = entry ? entry.sites.join('\n') : 'example.org';
  $('css').value = entry ? entry.css : state.starter.css;
  $('js').value = entry ? entry.js : state.starter.js;
  $('when').value = entry ? entry.when : 'ready';
  $('name').focus();
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  for (const key of ['error', 'notice']) { $(key).textContent = next[key]; $(key).hidden = !next[key]; }
  if (next.ack >= sequence) busy = false;
  $('private').hidden = !next.private; $('main').hidden = next.private;
  const list = $('list'); list.replaceChildren();
  for (const entry of next.entries) {
    const row = element('div', 'entry' + (entry.enabled ? '' : ' off')), what = element('div', 'what');
    const toggle = document.createElement('input'); toggle.type = 'checkbox'; toggle.checked = entry.enabled;
    toggle.setAttribute('aria-label', 'Run ' + entry.name);
    toggle.onchange = () => command('toggle', {id: entry.id, enabled: toggle.checked});
    const kinds = [entry.css.trim() && 'CSS', entry.js.trim() && 'JavaScript'].filter(Boolean).join(' + ');
    what.append(element('div', 'name', entry.name), element('div', 'sites', kinds + ' · ' + entry.sites.join(', ')));
    row.append(toggle, what, button('Edit', () => edit(entry)), button('Delete', () => command('delete', {id: entry.id})));
    list.append(row);
  }
  $('empty').hidden = next.entries.length > 0;
  return true;
}};
$('new').onclick = () => edit(null);
$('cancel').onclick = () => { $('editor').hidden = true; editing = null; };
$('save').onclick = () => {
  command('save', {id: editing, name: $('name').value, sites: $('sites').value, css: $('css').value, js: $('js').value, when: $('when').value, enabled: true});
  $('editor').hidden = true; editing = null;
};
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
