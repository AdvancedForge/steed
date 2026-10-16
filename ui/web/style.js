const $ = id => document.getElementById(id);
let state = null, bridge = null, sequence = 0, context = null, busy = false;
let colors = {};
function paletteFields() {
  const base = {...state.palettes[$('theme').value === 'system' ? (state.dark ? 'dark' : 'light') : $('theme').value]};
  if ($('accent').value !== state.defaults.accent) { base.accent = $('accent').value; base['accent-hover'] = $('accent').value; }
  const grid = $('palette-fields'); grid.replaceChildren();
  for (const [token, label] of Object.entries(state.guide.colors)) {
    const key = token.slice(2), wrap = document.createElement('div'), heading = document.createElement('label'), input = document.createElement('input'), code = document.createElement('code');
    heading.htmlFor = 'color-' + key; heading.textContent = label;
    input.id = heading.htmlFor; input.type = 'color'; input.value = colors[key] || base[key];
    input.oninput = () => { colors[key] = input.value; };
    code.textContent = token; wrap.append(heading, input, code); grid.append(wrap);
  }
}
function guide() {
  const target = $('css-guide'); target.replaceChildren();
  const table = document.createElement('table');
  for (const [name, description] of Object.entries({...state.guide.colors, ...state.guide.tokens, ...state.guide.selectors})) {
    const row = document.createElement('tr'), key = document.createElement('td'), value = document.createElement('td');
    key.textContent = name; value.textContent = description; row.append(key, value); table.append(row);
  }
  const note = document.createElement('p'); note.className = 'hint'; note.textContent = state.guide.precedence;
  target.append(table, note);
}
function fill(values) {
  colors = {...values.colors};
  for (const [key, value] of Object.entries(values)) {
    if (key === 'colors') continue;
    const node = $(key); if (typeof value === 'boolean') node.checked = value; else node.value = value;
  }
  if (state) paletteFields();
}
function command(action, extra = {}) {
  if (!bridge || !state || busy) return;
  busy = true; $('save').disabled = true;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, ...extra}));
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; fill(next.values); $('all_profiles').checked = false; }
  guide(); document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  $('profile').textContent = 'Editing: ' + next.profile;
  for (const key of ['error', 'notice']) { $(key).textContent = next[key]; $(key).hidden = !next[key]; }
  $('conflict').hidden = !next.external_changes;
  $('all_profiles').disabled = next.private;
  if (next.ack >= sequence) busy = false;
  $('save').disabled = busy;
  return true;
}};
$('save').onclick = () => {
  const values = {};
  for (const [key, value] of Object.entries(state.defaults)) values[key] = key === 'colors' ? {...colors} : typeof value === 'boolean' ? $(key).checked : typeof value === 'number' ? Number($(key).value) : $(key).value;
  command('save', {values, all_profiles: $('all_profiles').checked});
};
$('reset').onclick = () => fill(state.defaults);
$('theme').onchange = paletteFields;
$('reset-palette').onclick = () => { colors = {}; $('accent').value = state.defaults.accent; paletteFields(); };
$('reload').onclick = () => command('reload');
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
