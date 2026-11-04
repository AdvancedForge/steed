"""Reader view: an article's text in a clean, quiet page over the tab (F9).

A script in Steed's own script world (the page cannot see or change it) picks
the element with the most paragraph text and returns the article as plain
text blocks. Steed never takes the page's HTML: the reader page shows the text
with strict content rules and no network, so nothing from the site runs there.
"""

import json
from secrets import token_hex

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ui.html_surface import HtmlSurface

BLOCK_TYPES = ("h1", "h2", "h3", "p", "li", "quote", "pre", "caption")
MAX_BLOCKS = 600
MAX_TEXT = 20000
MIN_ARTICLE = 400  # characters; less is not an article
THEMES = ("light", "sepia", "dark")
FONT_SIZES = range(14, 31)
EXTRACT = r"""(() => {
  const MAX_BLOCKS = 600, MAX_CHARS = 250000;
  const noise = /comment|footer|footnote|sidebar|sponsor|\bad[s-]|advert|promo|related|share|social|\bnav|menu|cookie|banner|subscribe|newsletter|popup|modal/i;
  const content = /article|content|main|post|entry|story|body/i;
  const text = e => (e.innerText || '').trim();
  const linkShare = e => {
    let links = 0;
    e.querySelectorAll('a').forEach(a => { links += text(a).length; });
    return links / (text(e).length || 1);
  };
  const scores = new Map();
  document.querySelectorAll('p, pre, blockquote, li').forEach(p => {
    const length = text(p).length;
    if (length < 30) return;
    let node = p.parentElement;
    for (let depth = 0; node && depth < 3; depth++, node = node.parentElement) {
      scores.set(node, (scores.get(node) || 0) + (1 + Math.min(length / 100, 3)) / (depth + 1));
    }
  });
  let best = null, top = 0;
  for (const [node, raw] of scores) {
    const name = (typeof node.className === 'string' ? node.className : '') + ' ' + node.id;
    let score = raw * (1 - linkShare(node));
    if (noise.test(name) && !content.test(name)) score *= 0.3;
    if (node.tagName === 'ARTICLE' || node.tagName === 'MAIN') score *= 1.3;
    if (score > top) { top = score; best = node; }
  }
  if (!best) return JSON.stringify({ok: false});
  const kinds = {H1: 'h2', H2: 'h2', H3: 'h3', H4: 'h3', H5: 'h3', H6: 'h3', P: 'p', LI: 'li',
    BLOCKQUOTE: 'quote', PRE: 'pre', FIGCAPTION: 'caption'};
  const selector = Object.keys(kinds).join(',');
  const blocks = []; let chars = 0;
  for (const node of best.querySelectorAll(selector)) {
    if (blocks.length >= MAX_BLOCKS || chars >= MAX_CHARS) break;
    // Text inside a list item or quote belongs to that block.
    const outer = node.parentElement && node.parentElement.closest(selector);
    if (outer && best.contains(outer)) continue;
    let hidden = false;
    for (let up = node; up && up !== best; up = up.parentElement) {
      const name = (typeof up.className === 'string' ? up.className : '') + ' ' + up.id;
      if (up.hidden || up.getAttribute('aria-hidden') === 'true' || (noise.test(name) && !content.test(name))) { hidden = true; break; }
    }
    if (hidden || !node.getClientRects().length) continue;
    const kind = kinds[node.tagName];
    let value = kind === 'pre' ? node.innerText.replace(/\s+$/, '') : text(node).replace(/\s+/g, ' ');
    if (!value || (kind !== 'pre' && kind[0] !== 'h' && value.length < 40 && linkShare(node) > 0.5)) continue;
    blocks.push({type: kind, text: value.slice(0, 20000)});
    chars += value.length;
  }
  const meta = name => {
    const tag = document.querySelector(`meta[property="${name}"], meta[name="${name}"]`);
    return tag ? (tag.getAttribute('content') || '').trim() : '';
  };
  const bylineNode = document.querySelector('[rel="author"], .byline, .author, [itemprop="author"]');
  return JSON.stringify({
    ok: true,
    title: meta('og:title') || document.title || '',
    byline: meta('author') || (bylineNode ? text(bylineNode).replace(/\s+/g, ' ').slice(0, 200) : ''),
    site: meta('og:site_name') || location.hostname,
    blocks,
  });
})()"""


def article(raw) -> dict | None:
    """Check the page's answer; None when it is not a readable article."""
    try:
        data = (
            json.loads(raw) if isinstance(raw, str) and len(raw) < 4_000_000 else None
        )
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    blocks = []
    for block in data.get("blocks", [])[:MAX_BLOCKS]:
        if (
            isinstance(block, dict)
            and block.get("type") in BLOCK_TYPES
            and isinstance(block.get("text"), str)
            and block["text"].strip()
        ):
            blocks.append({"type": block["type"], "text": block["text"][:MAX_TEXT]})
    if sum(len(b["text"]) for b in blocks if b["type"] != "h2") < MIN_ARTICLE:
        return None

    def short(key, limit):
        value = data.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""

    words = sum(len(b["text"].split()) for b in blocks)
    return {
        "title": short("title", 300),
        "byline": short("byline", 200),
        "site": short("site", 200),
        "minutes": max(1, round(words / 230)),
        "blocks": blocks,
    }


class ReaderPanel(QWidget):
    """The reader page, laid over its tab until closed or the tab navigates."""

    closed = pyqtSignal()

    def __init__(self, window, tab, content, url):
        super().__init__(tab)
        self.window = window
        self.tab = tab
        self.url = url
        self.content = content
        self.context = token_hex(16)
        self.sequence = 0
        self.disposed = False
        self.surface = HtmlSurface("reader", self, label="reader")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        self.setGeometry(tab.rect())
        self.show()
        self.raise_()
        self.publish()

    def options(self):
        config = self.window.config
        theme = config.get("reader_theme", "light")
        size = config.get("reader_font_size", 19)
        return (
            theme if theme in THEMES else "light",
            size if type(size) is int and size in FONT_SIZES else 19,
        )

    def publish(self):
        if self.disposed:
            return
        theme, size = self.options()
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "article": self.content,
                "theme": theme,
                "size": size,
                "dark": theme == "dark",
            },
            force=True,
        )

    def command(self, command):
        if self.disposed or command.get("context") != self.context:
            return
        sequence = command.get("sequence")
        if type(sequence) is not int or sequence <= self.sequence:
            return
        self.sequence = sequence
        action = command.get("action")
        config = self.window.config
        if action == "close":
            self.close_reader()
            return
        if action == "theme" and command.get("theme") in THEMES:
            config.set("reader_theme", command["theme"])
        elif action == "size":
            size = self.options()[1] + (1 if command.get("step") == 1 else -1)
            if size in FONT_SIZES:
                config.set("reader_font_size", size)
        self.publish()

    def close_reader(self):
        if self.disposed:
            return
        self.disposed = True
        self.surface.dispose()
        self.hide()
        self.deleteLater()
        self.closed.emit()
