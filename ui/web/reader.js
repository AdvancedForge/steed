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
  for (const [index, block] of article.blocks.entries()) {
    if (block.type !== 'li') list = null;
    let node;
    if (block.type === 'img') {
      const figure = document.createElement('figure'), image = document.createElement('img');
      figure.className = 'image'; figure.hidden = true;
      image.alt = block.text.trim(); image.dataset.index = String(index);
      figure.append(image); body.append(figure);
      continue;
    }
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
  // Images arrive after the text; each is set once, as an inline image.
  for (const image of document.querySelectorAll('img[data-index]')) {
    const source = next.images[image.dataset.index];
    if (source && !image.src) { image.src = source; image.parentElement.hidden = false; }
    if (!next.show_images) image.parentElement.hidden = true;
    else if (image.src) image.parentElement.hidden = false;
  }
  $('images').setAttribute('aria-pressed', String(next.show_images));
  return true;
}};
$('smaller').onclick = () => command('size', {step: -1});
$('larger').onclick = () => command('size', {step: 1});
$('close').onclick = () => command('close');
$('images').onclick = () => command('images', {show: !state.show_images});
for (const button of document.querySelectorAll('[data-theme]')) button.onclick = () => command('theme', {theme: button.dataset.theme});
document.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); command('close'); } });
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
