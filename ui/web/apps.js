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
function edit(app) {
  editing = app ? app.id : null;
  $('editor').hidden = false;
  $('editor-title').textContent = app ? 'Edit app' : 'New app';
  $('name').value = app ? app.name : 'My app';
  $('html').value = app ? app.html : state.starter;
  $('name').focus();
}
function save(open) {
  command('save', {id: editing, name: $('name').value, html: $('html').value, open});
  $('editor').hidden = true; editing = null;
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  for (const key of ['error', 'notice']) { $(key).textContent = next[key]; $(key).hidden = !next[key]; }
  if (next.ack >= sequence) busy = false;
  $('private').hidden = !next.private; $('main').hidden = next.private;
  const list = $('list'); list.replaceChildren();
  for (const app of next.apps) {
    const row = element('div', 'app');
    row.append(element('span', 'name', app.name), button('Open in panel', () => command('open', {id: app.id})),
      button('Edit', () => edit(app)), button('Delete', () => command('delete', {id: app.id})));
    list.append(row);
  }
  $('empty').hidden = next.apps.length > 0;
  return true;
}};
$('new').onclick = () => edit(null);
$('import').onclick = () => command('import');
$('cancel').onclick = () => { $('editor').hidden = true; editing = null; };
$('save').onclick = () => save(false);
$('save-open').onclick = () => save(true);
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
