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
  busy = true; $('save').disabled = true; $('save-layout').disabled = true;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, ...extra}));
}
let layoutDirty = false, layoutShown = null;
function field(id, label, input) {
  const wrap = document.createElement('div'), heading = document.createElement('label');
  heading.htmlFor = id; heading.textContent = label; input.id = id;
  input.addEventListener('input', () => { layoutDirty = true; widthState(); });
  wrap.append(heading, input); return wrap;
}
function select(id, label, options, value) {
  const node = document.createElement('select');
  for (const [optionValue, text] of options) {
    const option = document.createElement('option'); option.value = optionValue; option.textContent = text; node.append(option);
  }
  node.value = value; return field(id, label, node);
}
function widthState() { const width = $('tab-vertical_width'); if (width) width.disabled = !['left', 'right'].includes($('tab-position').value); }
function layoutFields(layout) {
  const grid = $('tab-fields'), flags = $('tab-flags'), values = layout.values;
  grid.replaceChildren(); flags.replaceChildren();
  for (const [key, options] of Object.entries(layout.choices))
    grid.append(select('tab-' + key, layout.labels[key], options, values.tabs[key]));
  const width = document.createElement('input');
  width.type = 'number'; width.min = layout.width[0]; width.max = layout.width[1]; width.value = values.tabs.vertical_width;
  grid.append(field('tab-vertical_width', layout.labels.vertical_width + ' (px)', width));
  grid.append(select('layout-density', 'Control spacing', layout.densities.map(d => [d, d[0].toUpperCase() + d.slice(1)]), values.density));
  grid.append(select('layout-text_size', 'Browser text', layout.text_sizes.map(d => [d, d[0].toUpperCase() + d.slice(1)]), values.text_size));
  for (const key of layout.flags) {
    const wrap = document.createElement('label'), box = document.createElement('input');
    wrap.className = 'check'; box.type = 'checkbox'; box.id = 'tab-' + key; box.checked = values.tabs[key];
    box.addEventListener('input', () => { layoutDirty = true; });
    wrap.append(box, document.createTextNode(layout.labels[key])); flags.append(wrap);
  }
  widthState();
  layoutDirty = false; layoutShown = JSON.stringify(values);
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; fill(next.values); $('all_profiles').checked = false; }
  guide(); document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  // Show newly saved values unless the user is in the middle of editing them.
  if (!layoutDirty && JSON.stringify(next.layout.values) !== layoutShown) layoutFields(next.layout);
  $('profile').textContent = 'Editing: ' + next.profile;
  for (const key of ['error', 'notice']) { $(key).textContent = next[key]; $(key).hidden = !next[key]; }
  $('conflict').hidden = !next.external_changes;
  $('all_profiles').disabled = next.private;
  if (next.ack >= sequence) busy = false;
  $('save').disabled = busy; $('save-layout').disabled = busy;
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
$('reload-layout').onclick = () => { layoutDirty = false; layoutShown = null; layoutFields(state.layout); };
$('save-layout').onclick = () => {
  const layout = state.layout, tabs = {};
  for (const key of Object.keys(layout.choices)) tabs[key] = $('tab-' + key).value;
  for (const key of layout.flags) tabs[key] = $('tab-' + key).checked;
  tabs.vertical_width = Number($('tab-vertical_width').value);
  layoutDirty = false; layoutShown = null;
  command('save_layout', {values: {tabs, density: $('layout-density').value, text_size: $('layout-text_size').value}});
};
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
