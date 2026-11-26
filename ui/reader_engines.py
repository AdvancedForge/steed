"""Reader engines: which extractor finds a page's article (Settings → General →
Reading: reader_engine), and what agents get when they read a page
(agent_page_text).

- "defuddle": Defuddle, the extractor of Obsidian's web clipper (the default).
- "readability": Mozilla Readability, Firefox's reader mode.
- "steed": Steed's own small heuristic (reader_view.EXTRACT).

Defuddle and Readability are bundled in browser/data (see its README) and run
only in Steed's script world. Their article HTML becomes the same text blocks
as Steed's own extractor, so the reader page stays as strict as ever; if an
engine fails or finds no article, Steed's own extractor has its turn.

In a test on 18 live pages (news, encyclopedia, docs, blogs, forums), Steed's
heuristic picked the wrong part of 5 (a photo caption on the BBC, Wikipedia's
references, an epigraph); Defuddle found the article on all of them.
"""

from functools import lru_cache
from pathlib import Path

from ui.reader_view import EXTRACT

READER_ENGINES = ("defuddle", "readability", "steed")
AGENT_PAGE_TEXT = ("article", "page")
DATA = Path(__file__).resolve().parents[1] / "browser" / "data"
MIN_ARTICLE = 400

TO_BLOCKS = r"""
const toBlocks = html => {
  const doc = new DOMParser().parseFromString(html || '', 'text/html');
  const kinds = {H1: 'h2', H2: 'h2', H3: 'h3', H4: 'h3', H5: 'h3', H6: 'h3', P: 'p', LI: 'li',
    BLOCKQUOTE: 'quote', PRE: 'pre', FIGCAPTION: 'caption', IMG: 'img'};
  const selector = Object.keys(kinds).join(',');
  const blocks = []; let chars = 0, images = 0;
  for (const node of doc.body.querySelectorAll(selector)) {
    if (blocks.length >= 600 || chars >= 250000) break;
    // Text inside a list item or quote belongs to that block.
    const outer = node.parentElement && node.parentElement.closest(selector);
    if (outer) continue;
    const kind = kinds[node.tagName];
    if (kind === 'img') {
      let source = '';
      try { source = new URL(node.getAttribute('src') || '', location.href).href; } catch (e) {}
      if (images < 30 && /^https?:/.test(source)) {
        blocks.push({type: 'img', text: (node.getAttribute('alt') || '').trim().slice(0, 300) || ' ',
          src: source.slice(0, 2000)});
        images++;
      }
      continue;
    }
    const value = kind === 'pre' ? node.textContent.replace(/\s+$/, '')
      : node.textContent.replace(/\s+/g, ' ').trim();
    if (!value) continue;
    blocks.push({type: kind, text: value.slice(0, 20000)});
    chars += value.length;
  }
  if (!enough(blocks)) {
    // Text laid out with <br>s instead of paragraphs (older sites): each
    // line break ends a paragraph.
    const loose = new DOMParser().parseFromString(
      (html || '').replace(/<br\s*\/?>/gi, '\n'), 'text/html');
    const paragraphs = (loose.body.textContent || '').split(/\n+/)
      .map(t => t.replace(/\s+/g, ' ').trim()).filter(Boolean);
    const text = paragraphs.map(t => ({type: 'p', text: t.slice(0, 20000)}));
    if (enough(text)) return text.slice(0, 600);
  }
  return blocks;
};
const enough = blocks => blocks.filter(b => b.type !== 'img' && b.type[0] !== 'h')
  .reduce((n, b) => n + b.text.length, 0) >= 400;
"""

READABILITY_CALL = """;
const parsed = new Readability(document.cloneNode(true)).parse();
const found = parsed && {title: parsed.title, byline: parsed.byline, site: parsed.siteName,
  html: parsed.content, markdown: parsed.textContent};
"""
DEFUDDLE_CALL = """;
const parsed = new Defuddle(document).parse();
const found = parsed && {title: parsed.title, byline: parsed.author, site: parsed.site,
  html: parsed.content,
  markdown: WANT_MARKDOWN ? new Defuddle(document, {markdown: true}).parse().content : ''};
"""


@lru_cache(maxsize=4)
def library(name):
    return (DATA / name).read_text(encoding="utf-8")


def engine_of(config):
    engine = config.get("reader_engine", "defuddle") if config else "defuddle"
    return engine if engine in READER_ENGINES else "defuddle"


def _engine_call(engine, *, markdown=False):
    """JS that leaves `found` = {title, byline, site, html, markdown} or null."""
    if engine == "readability":
        return library("readability.js") + READABILITY_CALL
    return library("defuddle.js") + DEFUDDLE_CALL.replace(
        "WANT_MARKDOWN", "true" if markdown else "false"
    )


def extract_script(config=None):
    """What Reader View, Read Aloud and the reading list run on a page; its
    answer has the same shape as reader_view.EXTRACT's."""
    engine = engine_of(config)
    if engine == "steed":
        return EXTRACT
    return (
        "(() => { try {"
        + _engine_call(engine)
        + TO_BLOCKS
        + """
  if (found) {
    const blocks = toBlocks(found.html);
    if (enough(blocks)) return JSON.stringify({ok: true,
      title: found.title || document.title || '', byline: (found.byline || '').slice(0, 200),
      site: found.site || location.hostname, blocks});
  }
} catch (e) {}
return """
        + EXTRACT
        + "; })()"
    )


WHOLE_PAGE = "(document.body ? document.body.innerText : '')"
# Headings and lists stay; link addresses go (the words stay), so the article,
# not its links, fills what an agent is sent.
CLEAN_MARKDOWN = r"""
  const text = found && (found.markdown || '')
    .replace(/!\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)/g, (m, alt) => alt ? `[image: ${alt}]` : '')
    .replace(/\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)/g, '$1')
    .replace(/\n{3,}/g, '\n\n').trim();
"""


def page_text_script(config=None):
    """A page's text as an agent gets it: the article (Markdown with Defuddle,
    plain text with Readability), or the whole page when it has no article or
    agent_page_text is "page" or the engine is Steed's own."""
    engine = engine_of(config)
    wanted = config.get("agent_page_text", "article") if config else "article"
    if wanted != "article" or engine == "steed":
        return WHOLE_PAGE
    return (
        "(() => { try {"
        + _engine_call(engine, markdown=True)
        + """
"""
        + CLEAN_MARKDOWN
        + """
  if (text && text.length >= """
        + str(MIN_ARTICLE)
        + """) return text;
} catch (e) {}
return """
        + WHOLE_PAGE
        + "; })()"
    )
