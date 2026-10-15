const $ = id => document.getElementById(id);
let state = null, bridge = null, sequence = 0, context = null, busy = false;
function fill(values) {
  for (const [key, value] of Object.entries(values)) {
    const node = $(key); if (typeof value === 'boolean') node.checked = value; else node.value = value;
  }
}
function command(action, extra = {}) {
  if (!bridge || !state || busy) return;
  busy = true; $('save').disabled = true;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, ...extra}));
}
window.steedUI = {render(next) {
  if (context !== next.context) { context = next.context; sequence = 0; fill(next.values); $('all_profiles').checked = false; }
  state = next; document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
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
  for (const [key, value] of Object.entries(state.defaults)) values[key] = typeof value === 'boolean' ? $(key).checked : typeof value === 'number' ? Number($(key).value) : $(key).value;
  command('save', {values, all_profiles: $('all_profiles').checked});
};
$('reset').onclick = () => fill(state.defaults);
$('reload').onclick = () => command('reload');
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
