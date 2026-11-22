const $ = id => document.getElementById(id);
let state = null, bridge = null, sequence = 0, context = null;
function command(action) {
  if (!bridge || !state) return;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action}));
}
function stat(value, label) {
  const box = document.createElement('div'), strong = document.createElement('strong'), span = document.createElement('span');
  box.className = 'stat'; strong.textContent = value; span.textContent = label; box.append(strong, span); return box;
}
const CODECS = {
  built: ['Video codecs built in', ''],
  pypi: ['Without H.264/AAC', 'This is the PyPI engine, which cannot play H.264 video or AAC audio, so many MP4 videos and GIFs do not play. Run .venv/bin/python -m browser.build_codecs check to add them.'],
  replaced: ['Codec build replaced', 'A Python package update put back the engine without H.264/AAC. Run .venv/bin/python -m browser.build_codecs install (or build, for a new version), then restart Steed.'],
};
const DRM = {
  chrome: ['Available', ''],
  firefox: ['Available', ''],
  '': ['Not available', 'Netflix, Spotify, Disney+ and other protected video need Google\'s Widevine module, which Steed cannot include. Install Google Chrome, Chromium or Firefox and play one protected video there once; Steed then uses the same module after a restart.'],
};
function engine(data) {
  const [label, advice] = CODECS[data.codecs] || CODECS.pypi;
  const [drm, drmAdvice] = DRM[data.drm] || DRM[''];
  $('engine').replaceChildren(stat(data.qt, 'Qt WebEngine'), stat(data.chromium, 'Chromium features'),
    stat(data.security, 'Chromium security fixes'), stat(label, 'Video codecs'), stat(drm, 'Protected streaming'));
  const notes = [advice, drmAdvice].filter(Boolean).join(' ');
  $('codecs').textContent = notes; $('codecs').hidden = !notes;
}
function features(list) {
  const target = $('features'); target.replaceChildren();
  if (!list) { $('summary').textContent = state.checking ? 'Checking…' : 'The check did not finish.'; return; }
  const supported = list.filter(f => f.supported).length;
  const newer = list.filter(f => !f.supported && f.kind === 'newer').length;
  $('summary').textContent = `${supported} of ${list.length} supported. ${newer} missing until a newer Qt WebEngine; ${list.length - supported - newer} missing for other reasons.`;
  const groups = new Map();
  for (const feature of list) { if (!groups.has(feature.group)) groups.set(feature.group, []); groups.get(feature.group).push(feature); }
  for (const [name, items] of groups) {
    const group = document.createElement('div'), heading = document.createElement('h3');
    group.className = 'group'; heading.textContent = name; group.append(heading);
    for (const feature of items) {
      const row = document.createElement('div'), mark = document.createElement('span'), text = document.createElement('div');
      const title = document.createElement('strong'), use = document.createElement('p');
      row.className = 'feature ' + (feature.supported ? 'yes' : 'no');
      mark.className = 'mark'; mark.textContent = feature.supported ? '✓' : '✗';
      mark.setAttribute('aria-label', feature.supported ? 'Supported' : 'Missing');
      title.textContent = feature.name; use.textContent = feature.use; text.append(title, use);
      if (!feature.supported) { const why = document.createElement('p'); why.className = 'why'; why.textContent = feature.reason; text.append(why); }
      row.append(mark, text); group.append(row);
    }
    target.append(group);
  }
}
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  engine(next.engine); features(next.features);
  $('check').disabled = next.checking;
  const u = next.updates || {};
  const current = u.steed_checked === false
    ? 'The engine is up to date. This copy of Steed is not a git checkout, so its own updates are not checked.'
    : 'Steed and its engine are up to date.';
  $('updates').textContent = u.notice || (u.error ? u.error : u.checked ? current : u.enabled ? 'Checked once a day.' : 'Update checks are off (Settings → General).');
  $('update-how').hidden = !u.summary; $('update-how-text').textContent = u.summary || '';
  return true;
}};
$('check').onclick = () => command('check');
$('check-updates').onclick = () => command('updates');
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
