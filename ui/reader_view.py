"""Reader view: an article's text in a clean, quiet page over the tab (F9).

A script in Steed's own script world (the page cannot see or change it) picks
the element with the most paragraph text and returns the article as plain
text blocks. Steed never takes the page's HTML: the reader page shows the text
with strict content rules and no network, so nothing from the site runs there.
"""

import base64
import json
from pathlib import Path
from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ui.html_surface import HtmlSurface

BLOCK_TYPES = ("h1", "h2", "h3", "p", "li", "quote", "pre", "caption", "img")
MAX_IMAGES = 30
IMAGE_LIMIT = 2_000_000  # bytes per image
IMAGES_TOTAL = 12_000_000  # bytes per article
IMAGE_TIMEOUT_MS = 15000
# Only these formats, recognised by their first bytes (never SVG).
IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
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
    BLOCKQUOTE: 'quote', PRE: 'pre', FIGCAPTION: 'caption', IMG: 'img'};
  let images = 0;
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
    if (kind === 'img') {
      // Only images the page shows at a useful size, from the web.
      const box = node.getBoundingClientRect();
      const source = node.currentSrc || node.src || '';
      if (images < 30 && box.width >= 120 && box.height >= 60 && /^https?:/.test(source)) {
        blocks.push({type: 'img', text: (node.alt || '').trim().slice(0, 300) || ' ', src: source.slice(0, 2000)});
        images++;
      }
      continue;
    }
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


def image_type(data: bytes) -> str | None:
    """The image format from its first bytes; None for anything else."""
    for signature, kind in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return None


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
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        if block.get("type") == "img":
            source = QUrl(block.get("src") if isinstance(block.get("src"), str) else "")
            if source.scheme() in ("http", "https") and source.isValid():
                alt = block["text"].strip()[:300]
                blocks.append({"type": "img", "text": alt, "src": source.toString()})
        elif block.get("type") in BLOCK_TYPES and block["text"].strip():
            blocks.append({"type": block["type"], "text": block["text"][:MAX_TEXT]})
    if (
        sum(len(b["text"]) for b in blocks if b["type"] not in ("h2", "img"))
        < MIN_ARTICLE
    ):
        return None

    def short(key, limit):
        value = data.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""

    words = sum(len(b["text"].split()) for b in blocks if b["type"] != "img")
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

    def __init__(self, window, tab, content, url, notes_url=None):
        super().__init__(tab)
        self.window = window
        self.tab = tab
        # Highlights belong to the article's address (also when read offline).
        self.notes_url = (notes_url or url.toString()) if url is not None else ""
        from reader_notes import ReaderNotes

        manager = window.tab_manager
        self.notes = ReaderNotes(manager.profile_dir if manager else Path())
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
        self.images = {}  # block index -> data: URL
        self.image_bytes = 0
        self.network = None
        self.replies = []
        self.flush = QTimer(self, singleShot=True, interval=300)
        self.flush.timeout.connect(self.publish)
        self.setGeometry(tab.rect())
        self.show()
        self.raise_()
        self.publish()
        if self.window.config.get("reader_images", True):
            self.fetch_images()

    def fetch_images(self):
        """Download the article's images without cookies, within size limits."""
        if self.network is None:
            # Its own empty cookie jar: these requests never carry sign-ins.
            self.network = QNetworkAccessManager(self)
        for index, block in enumerate(self.content["blocks"]):
            if block["type"] != "img" or index in self.images:
                continue
            request = QNetworkRequest(QUrl(block["src"]))
            request.setTransferTimeout(IMAGE_TIMEOUT_MS)
            for attribute in (
                QNetworkRequest.Attribute.CookieLoadControlAttribute,
                QNetworkRequest.Attribute.CookieSaveControlAttribute,
            ):
                request.setAttribute(attribute, QNetworkRequest.LoadControl.Manual)
            reply = self.network.get(request)
            reply.downloadProgress.connect(
                lambda received, _total, reply=reply: (
                    received > IMAGE_LIMIT and reply.abort()
                )
            )
            reply.finished.connect(
                lambda reply=reply, index=index: self._image(reply, index)
            )
            self.replies.append(reply)

    def _image(self, reply, index):
        data = bytes(reply.read(IMAGE_LIMIT + 1) or b"")
        ok = reply.error() == reply.NetworkError.NoError
        if reply in self.replies:
            self.replies.remove(reply)
        reply.deleteLater()
        kind = image_type(data) if ok and len(data) <= IMAGE_LIMIT else None
        if self.disposed or kind is None or self.image_bytes + len(data) > IMAGES_TOTAL:
            return
        self.image_bytes += len(data)
        self.images[index] = f"data:{kind};base64," + base64.b64encode(data).decode()
        # Images arrive together: one update for each burst, not each image.
        self.flush.start()

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
        show = bool(self.window.config.get("reader_images", True))
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "article": self.content,
                "images": {str(i): url for i, url in self.images.items()}
                if show
                else {},
                "show_images": show,
                "theme": theme,
                "size": size,
                "dark": theme == "dark",
                "highlights": self.notes.highlights(self.notes_url),
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
        if action == "images":
            show = command.get("show") is True
            config.set("reader_images", show)
            if show:
                self.fetch_images()
        elif action == "theme" and command.get("theme") in THEMES:
            config.set("reader_theme", command["theme"])
        elif action == "highlight":
            try:
                block, start, end = (
                    int(command.get(k)) for k in ("block", "start", "end")
                )
                blocks = self.content.get("blocks", [])
                text = (
                    blocks[block]["text"][start:end] if 0 <= block < len(blocks) else ""
                )
                if not text.strip():
                    raise ValueError
                self.notes.add(self.notes_url, block, start, end, text)
            except (TypeError, ValueError, KeyError, IndexError):
                pass  # A selection that is not text of one block: nothing to mark.
        elif action == "note":
            self.notes.note(
                self.notes_url, str(command.get("id")), command.get("text", "")
            )
        elif action == "unhighlight":
            self.notes.remove(self.notes_url, str(command.get("id")))
        elif action == "size":
            size = self.options()[1] + (1 if command.get("step") == 1 else -1)
            if size in FONT_SIZES:
                config.set("reader_font_size", size)
        self.publish()

    def close_reader(self):
        if self.disposed:
            return
        self.disposed = True
        self.flush.stop()
        for reply in list(self.replies):
            if not sip.isdeleted(reply):
                reply.abort()
        self.replies.clear()
        self.surface.dispose()
        self.hide()
        self.deleteLater()
        self.closed.emit()
