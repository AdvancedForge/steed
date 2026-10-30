const $ = id => document.getElementById(id);
let state = null, bridge = null, sequence = 0, context = null, busy = false, editing = false;
function command(action, extra = {}) {
  if (!bridge || !state || busy) return;
  busy = true;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, ...extra}));
}
function stat(value, label) {
  const box = document.createElement('div'), strong = document.createElement('strong'), span = document.createElement('span');
  box.className = 'stat'; strong.textContent = value; span.textContent = label; box.append(strong, span); return box;
}
function usage(data) {
  const target = $('usage'); target.replaceChildren();
  if (!data || !data.available) { target.append(stat('—', 'Memory figures need Linux /proc')); return; }
  const renderers = data.processes.renderer || {count: 0, mb: 0};
  target.append(stat(data.total_mb + ' MB', 'Steed in total'),
    stat((data.processes.browser || {mb: 0}).mb + ' MB', 'Browser core'),
    stat(renderers.count + ' · ' + renderers.mb + ' MB', 'Page and interface renderers'),
    stat(data.tabs.active + ' / ' + data.tabs.frozen + ' / ' + data.tabs.unloaded, 'Tabs active / frozen / unloaded'),
    stat(data.inspectors.awake + ' of ' + data.inspectors.total, 'Cookie inspectors awake'));
}
function label(option, value) {
  const choice = option.choices.find(c => JSON.stringify(c.value) === JSON.stringify(value));
  return choice ? choice.label : String(value);
}
function options() {
  if (editing) return;
  const target = $('options'); target.replaceChildren();
  for (const option of state.options) {
    const row = document.createElement('div'), text = document.createElement('div'), heading = document.createElement('label');
    const help = document.createElement('p'), presets = document.createElement('p'), select = document.createElement('select');
    row.className = 'option'; heading.htmlFor = 'opt-' + option.key; heading.textContent = option.label; help.textContent = option.help;
    presets.className = 'presets';
    presets.textContent = state.modes.map(m => m.label + ': ' + label(option, option.presets[m.id])).join(' · ');
    select.id = 'opt-' + option.key;
    option.choices.forEach((choice, index) => {
      const item = document.createElement('option'); item.value = String(index); item.textContent = choice.label;
      if (JSON.stringify(choice.value) === JSON.stringify(option.value)) item.selected = true;
      select.append(item);
    });
    select.addEventListener('focus', () => { editing = true; });
    select.addEventListener('blur', () => { editing = false; });
    select.addEventListener('change', () => { editing = false; command('set', {key: option.key, value: option.choices[Number(select.value)].value}); });
    text.append(heading, help, presets); row.append(text, select); target.append(row);
  }
}
function modes() {
  const target = $('modes'); target.replaceChildren();
  for (const mode of state.modes) {
    const button = document.createElement('button');
    button.textContent = mode.label; button.setAttribute('role', 'radio');
    button.setAttribute('aria-checked', String(state.mode === mode.id));
    button.onclick = () => command('mode', {mode: mode.id});
    target.append(button);
  }
  $('custom').hidden = state.mode !== 'custom';
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  for (const key of ['error', 'notice']) { $(key).textContent = next[key]; $(key).hidden = !next[key]; }
  if (next.ack >= sequence) busy = false;
  modes(); usage(next.usage); options();
  $('free').disabled = busy;
  return true;
}};
$('free').onclick = () => command('free');
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
