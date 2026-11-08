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
function button(label, action) { const b = element('button', '', label); b.type = 'button'; b.onclick = action; return b; }
function openEditor(login) {
  editing = login ? login.id : null;
  $('editor').hidden = false;
  $('editor-title').textContent = login ? 'Edit login' : 'Add login';
  for (const name of ['origin', 'username', 'title', 'notes']) $('f-' + name).value = login ? (login[name] || '') : '';
  $('f-password').value = ''; $('f-totp').value = '';
  $('f-password').placeholder = login ? 'Leave empty to keep it' : 'Password';
  $('f-totp').placeholder = login && login.totp ? 'Saved — enter a new one to replace it' : 'otpauth://… or base32';
  $('f-origin').focus();
}
function list() {
  const query = $('search').value.toLowerCase().split(/\s+/).filter(Boolean);
  const target = $('list'); target.replaceChildren();
  const shown = state.logins.filter(login => query.every(word =>
    (login.origin + ' ' + login.username + ' ' + login.title).toLowerCase().includes(word)));
  for (const login of shown) {
    const row = element('div', 'login'); row.setAttribute('role', 'listitem');
    const text = element('div');
    text.append(element('div', 'site', login.title || login.site), element('div', 'meta', login.username || '(no username)'),
      element('div', 'meta', login.origin));
    const flags = [];
    if (login.breached) flags.push(`Found in breaches (${login.breached.toLocaleString()} times)`);
    if (login.reused) flags.push(`Reused on ${login.reused} logins`);
    if (login.weak) flags.push('Weak');
    if (flags.length) text.append(element('div', 'flag', flags.join(' · ')));
    if (login.password !== null && login.password !== undefined) text.append(element('div', 'secret', login.password));
    const actions = element('div', 'actions');
    actions.append(
      button(login.password ? 'Hide' : 'Show', () => command(login.password ? 'hide' : 'reveal', {id: login.id})),
      button('Copy username', () => command('copy', {id: login.id, field: 'username'})),
      button('Copy password', () => command('copy', {id: login.id, field: 'password'})));
    if (login.totp) actions.append(button('Copy code', () => command('copy', {id: login.id, field: 'code'})));
    actions.append(button('Edit', () => openEditor(login)), button('Delete', () => command('delete', {id: login.id})));
    row.append(text, actions); target.append(row);
  }
  $('empty').hidden = state.logins.length > 0;
}
function passkeys() {
  const target = $('passkeys'); target.replaceChildren();
  if (!state.passkeys.length) target.append(element('p', 'hint', 'None yet.'));
  for (const passkey of state.passkeys) {
    const row = element('div', 'login'), text = element('div'), actions = element('div', 'actions');
    text.append(element('div', 'site', passkey.site), element('div', 'meta', passkey.account || '(no account name)'),
      element('div', 'meta', 'Created ' + passkey.created));
    actions.append(button('Delete', () => command('delete_passkey', {id: passkey.id})));
    row.append(text, actions); target.append(row);
  }
}
function checkup() {
  const found = state.checkup, line = $('checkup');
  line.hidden = !found;
  if (found) line.textContent = `${found.breached} breached · ${found.reused} reused · ${found.weak} weak. ${found.status}`;
}
function settings() {
  const target = $('settings'); target.replaceChildren();
  for (const item of state.settings) {
    const label = element('label', 'check'), box = element('input');
    box.type = 'checkbox'; box.checked = item.value;
    box.onchange = () => command('setting', {key: item.key, value: box.checked});
    label.append(box, document.createTextNode(item.label)); target.append(label);
  }
  const never = $('never'); never.replaceChildren();
  if (!state.never.length) never.append(element('p', 'hint', 'None.'));
  for (const origin of state.never) {
    const row = element('div', 'row'); row.append(element('span', 'grow', origin), button('Allow again', () => command('never_remove', {origin})));
    never.append(row);
  }
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  for (const key of ['error', 'notice']) { $(key).textContent = next[key]; $(key).hidden = !next[key]; }
  if (next.ack >= sequence) busy = false;
  $('private').hidden = next.state !== 'private';
  $('locked').hidden = next.state !== 'locked';
  $('unlocked').hidden = next.state !== 'unlocked';
  $('scope').textContent = next.state === 'private' ? 'Incognito' : `Saved logins for the ${next.profile} profile, encrypted on this computer.`;
  if (next.state === 'unlocked') { list(); passkeys(); checkup(); settings(); }
  if (next.generated && !$('editor').hidden) {
    $('f-password').value = next.generated;
    $('f-password').type = 'text';
  }
  return true;
}};
$('search').oninput = () => state && list();
$('add').onclick = () => openEditor(null);
$('cancel').onclick = () => { $('editor').hidden = true; editing = null; };
$('generate').onclick = () => command('generate');
$('show-new').onclick = () => { const f = $('f-password'); f.type = f.type === 'password' ? 'text' : 'password'; };
$('import').onclick = () => command('import');
$('check-local').onclick = () => command('checkup', {online: false});
$('check-online').onclick = () => command('checkup', {online: true});
$('export').onclick = () => command('export');
$('editor').onsubmit = event => {
  event.preventDefault();
  const fields = {};
  for (const name of ['origin', 'username', 'password', 'title', 'totp', 'notes']) fields[name] = $('f-' + name).value;
  if (editing && !fields.totp) delete fields.totp;
  command('save', {id: editing, ...fields});
  $('editor').hidden = true; $('f-password').type = 'password'; editing = null;
};
$('unlock-form').onsubmit = event => { event.preventDefault(); command('unlock', {password: $('master').value}); $('master').value = ''; };
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
