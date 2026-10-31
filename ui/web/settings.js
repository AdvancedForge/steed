const $ = id => document.getElementById(id);
let bridge = null, state = null, context = null, sequence = 0, version = 0, busy = null;
let profilesKey = null, renameTarget = null, lastIntent = null, reviewId = null;
const fields = [...document.querySelectorAll('[data-pref]')];
function show(id, value) { $(id).hidden = !value; }
function text(id, value) { $(id).textContent = value || ''; }
function values() { return Object.fromEntries(fields.map(f => [f.dataset.pref, f.type === 'checkbox' ? f.checked : f.value])); }
function command(action, data = {}, blocking = false) {
  if (!bridge || !state || (blocking && busy !== null)) return;
  const next = ++sequence;
  if (blocking) { busy = next; lock(); }
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: next, action, ...data}));
}
function lock() {
  for (const button of document.querySelectorAll('button[data-action]')) button.disabled = busy !== null || button.dataset.unavailable === 'true';
  for (const field of fields) field.disabled = busy !== null || (field.id === 'default_profile' && !state?.profiles_available);
}
function renderProfiles(profiles) {
  const key = JSON.stringify(profiles);
  if (key === profilesKey) return;
  profilesKey = key;
  const list = $('profile-list');
  list.replaceChildren();
  for (const profile of profiles) {
    const row = document.createElement('article'); row.className = 'profile-row'; row.dataset.profile = profile.id;
    const header = document.createElement('div'); header.className = 'profile-header';
    const name = document.createElement('strong'); name.textContent = profile.label; header.append(name);
    for (const [label, on, extra] of [['Current', profile.current, 'current'], ['Startup', profile.default, ''], ['Session only', profile.private, '']]) {
      if (!on) continue; const badge = document.createElement('span'); badge.className = 'badge ' + extra; badge.textContent = label; header.append(badge);
    }
    const help = document.createElement('p'); help.className = 'help'; help.textContent = profile.reason || 'Separate accounts, cookies and saved tabs.';
    const buttons = document.createElement('div'); buttons.className = 'inline-actions';
    for (const [label, action, allowed] of [['Switch', 'switch', !profile.current], ['New window', 'open_window', true], ['Rename', 'rename_form', profile.can_mutate], ['Archive', 'archive', profile.can_mutate]]) {
      const button = document.createElement('button'); button.textContent = label; button.dataset.action = action; button.dataset.profile = profile.id; button.dataset.unavailable = String(!allowed); button.disabled = !allowed; buttons.append(button);
    }
    row.append(header, help, buttons); list.append(row);
  }
  const selected = $('default_profile').value;
  $('default_profile').replaceChildren(...profiles.filter(p => !p.private).map(p => new Option(p.label, p.name)));
  if (!profiles.length) $('default_profile').append(new Option(state.preferences.default_profile, state.preferences.default_profile));
  $('default_profile').value = selected;
  if (renameTarget && !profiles.some(p => p.id === renameTarget && p.can_mutate)) { renameTarget = null; show('rename-form', false); }
}
window.steedUI = {render(next) {
  const switched = context !== next.context;
  state = next; context = next.context; sequence = Math.max(sequence, next.ack);
  if (switched || next.ack >= busy) busy = null;
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  for (const panel of document.querySelectorAll('[data-panel]')) panel.hidden = panel.dataset.panel !== next.section;
  for (const button of document.querySelectorAll('[data-section]')) {
    if (button.dataset.section === next.section) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  }
  renderProfiles(next.profiles);
  if (switched || next.draft_version >= version) {
    for (const field of fields) {
      const value = next.preferences[field.dataset.pref];
      if (field.type === 'checkbox') field.checked = value;
      else if (field.value !== value) field.value = value;
    }
    version = next.draft_version;
  }
  show('error', !!next.error); text('error', next.error);
  show('notice', !!next.notice); text('notice', next.notice);
  show('external', next.external_changes); show('private', next.private);
  text('effective-ua', next.effective_ua || 'The engine default is used when no override is set.');
  text('ua-warning', next.ua_warning); $('ua-warning').hidden = !next.ua_warning;
  text('save-state', next.dirty ? 'You have unsaved preferences' : 'Preferences are up to date');
  text('profile-intent', {rename: 'Choose an inactive profile below, then select Rename.', archive: 'Choose an inactive profile below, then select Archive.', open_window: 'Choose New window beside a profile to browse it separately.', switch: 'Switch here or use the Profile quick-switch shortcuts.'}[next.intent] || '');
  for (const b of document.querySelectorAll('[data-destination]')) b.dataset.unavailable = String(!next.window_links);
  $('create-profile').dataset.unavailable = String(!next.profiles_available);
  $('customize-newtab').dataset.unavailable = String(!next.window_links || next.private);
  if (switched) { renameTarget = null; show('rename-form', false); }
  show('review-overlay', !!next.review);
  document.querySelector('.settings-shell').inert = !!next.review;
  if (next.review) {
    const rename = next.review.kind === 'rename';
    text('review-title', rename ? `Rename “${next.review.source}”?` : `Archive “${next.review.source}”?`);
    text('review-description', rename ? `The new name will be “${next.review.name}”. This keeps the profile's browsing data and existing identity.` : 'Its folder will move to deleted_profiles in the browser data directory and can be recovered. Other profiles are kept. This action happens immediately.');
    text('confirm-review', rename ? 'Rename profile' : 'Archive profile');
    if (reviewId !== next.review.id) $('cancel-review').focus();
  }
  reviewId = next.review?.id || null;
  if (lastIntent !== next.intent_revision) {
    lastIntent = next.intent_revision;
    if (next.intent === 'create') $('new-profile-name').focus();
  }
  lock();
  return true;
}};
for (const field of fields) field.addEventListener(field.type === 'checkbox' || field.tagName === 'SELECT' ? 'change' : 'input', () => {
  command('edit', {key: field.dataset.pref, value: field.type === 'checkbox' ? field.checked : field.value, version: ++version});
});
document.addEventListener('click', event => {
  const nav = event.target.closest('button[data-section]');
  if (nav) { command('navigate', {section: nav.dataset.section}); return; }
  const button = event.target.closest('button[data-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.action, profile = button.dataset.profile;
  if (action === 'rename_form') { renameTarget = profile; $('rename-name').value = state.profiles.find(p => p.id === profile).name; show('rename-form', true); $('rename-name').focus(); }
  else if (action === 'cancel_rename') { renameTarget = null; show('rename-form', false); }
  else if (action === 'review_rename') command('rename', {profile: renameTarget, name: $('rename-name').value}, true);
  else if (action === 'save') command('save', {values: values(), version: ++version}, true);
  else if (action === 'create') command('create', {name: $('new-profile-name').value}, true);
  else if (action === 'confirm') command('confirm', {review: state.review.id}, true);
  else if (['switch', 'open_window', 'archive'].includes(action)) command(action, {profile}, true);
  else if (action === 'open') command('open', {destination: button.dataset.destination}, true);
  else command(action, {}, true);
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && state?.review) { event.preventDefault(); command('cancel_review', {}, true); }
});
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
