const $ = id => document.getElementById(id);
let state = null, bridge = null, sequence = 0, context = null;
function command(action, extra = {}) {
  if (!bridge || !state) return;
  bridge.dispatch(JSON.stringify({generation: STEED_GENERATION, context, sequence: ++sequence, action, ...extra}));
}
function render(article) {
  $('site').textContent = article.site;
  $('title').textContent = article.title;
  $('meta').textContent = [article.byline, article.minutes + ' min read'].filter(Boolean).join(' · ');
  const body = $('body'); body.replaceChildren();
  let list = null;
  for (const block of article.blocks) {
    if (block.type !== 'li') list = null;
    let node;
    if (block.type === 'li') {
      if (!list) { list = document.createElement('ul'); body.append(list); }
      node = document.createElement('li'); list.append(node);
    } else {
      node = document.createElement({quote: 'blockquote', caption: 'p'}[block.type] || block.type);
      if (block.type === 'caption') node.className = 'caption';
      body.append(node);
    }
    node.textContent = block.text;
  }
}
let shown = null;
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  document.documentElement.dataset.reader = next.theme;
  document.documentElement.style.setProperty('--reader-size', next.size + 'px');
  for (const button of document.querySelectorAll('[data-theme]')) button.setAttribute('aria-checked', String(button.dataset.theme === next.theme));
  // One article per reader page; settings changes keep the reading position.
  if (shown !== next.context) { shown = next.context; render(next.article); }
  return true;
}};
$('smaller').onclick = () => command('size', {step: -1});
$('larger').onclick = () => command('size', {step: 1});
$('close').onclick = () => command('close');
for (const button of document.querySelectorAll('[data-theme]')) button.onclick = () => command('theme', {theme: button.dataset.theme});
document.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); command('close'); } });
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
