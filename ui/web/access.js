const $ = id => document.getElementById(id);
let state = null, bridge = null, sequence = 0, context = null, busy = false, editing = false;
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
function area(data) {
  const section = element('section'); section.setAttribute('aria-labelledby', 'area-' + data.id);
  const heading = element('h2', '', data.title); heading.id = 'area-' + data.id;
  const axis = element('div', 'axis'); axis.setAttribute('role', 'radiogroup'); axis.setAttribute('aria-label', data.title + ' preset');
  for (const preset of state.presets) {
    const button = element('button', '', preset.label);
    button.setAttribute('role', 'radio'); button.setAttribute('aria-checked', String(data.preset === preset.id));
    button.onclick = () => command('preset', {area: data.id, preset: preset.id});
    axis.append(button);
  }
  const ends = element('p', 'axis-ends'); ends.append(element('span', '', 'Most private'), element('span', '', 'Fewest prompts'));
  const custom = element('p', 'hint', 'Custom: these settings differ from every preset.'); custom.hidden = data.preset !== 'custom';
  section.append(heading, element('p', 'area-text', data.text), axis, ends, custom);
  for (const capability of data.capabilities) {
    const row = element('div', 'option'), text = element('div'), label = element('label', '', capability.label), select = element('select');
    label.htmlFor = 'cap-' + data.id + '-' + capability.id; select.id = label.htmlFor;
    for (const policy of state.policies) {
      const option = element('option', '', policy.label); option.value = policy.id;
      option.selected = policy.id === capability.policy; select.append(option);
    }
    select.addEventListener('focus', () => { editing = true; });
    select.addEventListener('blur', () => { editing = false; });
    select.addEventListener('change', () => { editing = false; command('set', {area: data.id, capability: capability.id, policy: select.value}); });
    text.append(label, element('p', '', capability.help)); row.append(text, select); section.append(row);
  }
  return section;
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  for (const key of ['error', 'notice']) { $(key).textContent = next[key]; $(key).hidden = !next[key]; }
  $('yolo').hidden = !next.yolo;
  if (next.ack >= sequence) busy = false;
  if (!editing) $('areas').replaceChildren(...next.areas.map(area));
  return true;
}};
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
