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
  let list = null, titled = false;
  const plain = text => String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
  for (const [index, block] of article.blocks.entries()) {
    // The article's own first heading repeats the title shown above it.
    if (!titled && /^h[1-3]$/.test(block.type) && plain(block.text) === plain(article.title)) {
      titled = true;
      continue;
    }
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
    node.dataset.block = String(index);
    node.textContent = block.text;
  }
}
// Highlights: spans of one block's text, drawn as marks over the plain text.
let marked = '';
function paintHighlights(highlights) {
  const key = JSON.stringify(highlights);
  if (key === marked) return;
  marked = key;
  for (const node of document.querySelectorAll('[data-block]')) {
    const index = Number(node.dataset.block), text = state.article.blocks[index].text;
    const spans = highlights.filter(h => h.block === index).sort((a, b) => a.start - b.start);
    node.replaceChildren();
    let at = 0;
    for (const span of spans) {
      if (span.start < at) continue;
      node.append(document.createTextNode(text.slice(at, span.start)));
      const mark = document.createElement('mark');
      mark.textContent = text.slice(span.start, span.end);
      mark.dataset.id = span.id;
      if (span.note) { mark.title = span.note; mark.classList.add('noted'); }
      mark.onclick = () => openNote(span);
      node.append(mark);
      at = span.end;
    }
    node.append(document.createTextNode(text.slice(at)));
  }
}
// Where a selection sits in its block's plain text, or null.
function selected() {
  const selection = document.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const block = node => (node.nodeType === 1 ? node : node.parentElement)?.closest('[data-block]');
  const start = block(range.startContainer), end = block(range.endContainer);
  if (!start || start !== end) return null;
  const before = document.createRange();
  before.selectNodeContents(start);
  before.setEnd(range.startContainer, range.startOffset);
  const offset = before.toString().length, length = range.toString().length;
  return length ? {block: Number(start.dataset.block), start: offset, end: offset + length} : null;
}
let noting = null;
function openNote(span) {
  noting = span;
  $('note-text').value = span.note || '';
  $('note-editor').hidden = false;
  $('note-text').focus();
}
function closeNote() { noting = null; $('note-editor').hidden = true; }
document.addEventListener('selectionchange', () => { $('highlight').disabled = !selected(); });
$('highlight').onclick = () => {
  const span = selected();
  if (span) { command('highlight', span); document.getSelection().removeAllRanges(); }
};
$('note-editor').onsubmit = event => {
  event.preventDefault();
  if (noting) command('note', {id: noting.id, text: $('note-text').value});
  closeNote();
};
$('unhighlight').onclick = () => { if (noting) command('unhighlight', {id: noting.id}); closeNote(); };
$('note-close').onclick = closeNote;
let shown = null;
window.steedUI = {render(next) {
  state = next;
  if (context !== next.context) { context = next.context; sequence = 0; }
  document.documentElement.dataset.theme = next.dark ? 'dark' : 'light';
  document.documentElement.dataset.reader = next.theme;
  // Photo theme: colours from the New Tab photo (checked hex), a blur of it.
  const root = document.documentElement.style, palette = next.theme === 'photo' ? next.palette || {} : {};
  for (const key of ['bg', 'ink', 'muted', 'rule', 'accent']) {
    const value = palette[key];
    if (typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value)) root.setProperty('--reader-' + key, value);
    else root.removeProperty('--reader-' + key);
  }
  const backdrop = next.theme === 'photo' && /^data:image\/jpeg;base64,[A-Za-z0-9+\/=]+$/.test(next.backdrop || '') ? next.backdrop : '';
  if (backdrop) root.setProperty('--reader-photo', `url("${backdrop}")`); else root.removeProperty('--reader-photo');
  document.documentElement.classList.toggle('reader-photo', !!backdrop);
  document.documentElement.style.setProperty('--reader-size', next.size + 'px');
  for (const button of document.querySelectorAll('[data-theme]')) button.setAttribute('aria-checked', String(button.dataset.theme === next.theme));
  // One article per reader page; settings changes keep the reading position.
  if (shown !== next.context) { shown = next.context; marked = ''; render(next.article); }
  paintHighlights(next.highlights || []);
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
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  event.preventDefault();
  if (!$('note-editor').hidden) closeNote(); else command('close');
});
new QWebChannel(qt.webChannelTransport, channel => { bridge = channel.objects.interface; bridge.ready(STEED_GENERATION); });
