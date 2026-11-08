"""Reading-list articles as an e-book (EPUB 3, with an EPUB 2 contents file).

Each article kept for offline reading becomes a chapter: its title, site and
byline, then its text blocks (headings, paragraphs, lists, quotes, code).
"""

import time
import uuid
import zipfile
from html import escape

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
STYLE = """body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1 { font-size: 1.5em; } .site { color: #666; font-size: .85em; text-transform: uppercase; }
blockquote { margin-left: 1em; font-style: italic; } pre { white-space: pre-wrap; font-size: .85em; }"""


def _chapter(entry) -> str:
    article = entry["article"]
    parts = [
        f'<p class="site">{escape(article.get("site") or "")}</p>',
        f"<h1>{escape(article.get('title') or entry['title'])}</h1>",
    ]
    if article.get("byline"):
        parts.append(f"<p><em>{escape(article['byline'])}</em></p>")
    listing = False
    for block in article["blocks"]:
        kind, text = block["type"], escape(block["text"])
        if kind != "li" and listing:
            parts.append("</ul>")
            listing = False
        if kind == "li":
            if not listing:
                parts.append("<ul>")
                listing = True
            parts.append(f"<li>{text}</li>")
        elif kind in ("h2", "h3", "h4"):
            parts.append(f"<{kind}>{text}</{kind}>")
        elif kind == "quote":
            parts.append(f"<blockquote><p>{text}</p></blockquote>")
        elif kind == "pre":
            parts.append(f"<pre>{text}</pre>")
        else:
            parts.append(f"<p>{text}</p>")
    if listing:
        parts.append("</ul>")
    parts.append(f'<p class="site">{escape(entry["url"])}</p>')
    title = escape(article.get("title") or entry["title"])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f'<head><title>{title}</title><link rel="stylesheet" href="style.css"/></head>'
        f"<body>{''.join(parts)}</body></html>"
    )


def write(entries, path, title="Reading list") -> int:
    """Write the articles of entries (with "article") to path; returns how many."""
    chapters = [e for e in entries if e.get("article")]
    if not chapters:
        raise ValueError("No saved articles to put in a book.")
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    names = [f"chapter-{i + 1}.xhtml" for i in range(len(chapters))]
    titles = [escape(e["article"].get("title") or e["title"]) for e in chapters]
    manifest = "".join(
        f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, name in enumerate(names)
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(names)))
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="id">{book_id}</dc:identifier>'
        f"<dc:title>{escape(title)}</dc:title><dc:language>en</dc:language>"
        f'<meta property="dcterms:modified">{modified}</meta></metadata>'
        '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="style" href="style.css" media-type="text/css"/>'
        f'{manifest}</manifest><spine toc="ncx">{spine}</spine></package>'
    )
    nav = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>{escape(title)}</title></head><body>"
        '<nav epub:type="toc"><h1>Contents</h1><ol>'
        + "".join(
            f'<li><a href="{n}">{t}</a></li>'
            for n, t in zip(names, titles, strict=True)
        )
        + "</ol></nav></body></html>"
    )
    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{book_id}"/></head>'
        f"<docTitle><text>{escape(title)}</text></docTitle><navMap>"
        + "".join(
            f'<navPoint id="p{i}" playOrder="{i + 1}"><navLabel><text>{t}</text>'
            f'</navLabel><content src="{n}"/></navPoint>'
            for i, (n, t) in enumerate(zip(names, titles, strict=True))
        )
        + "</navMap></ncx>"
    )
    with zipfile.ZipFile(path, "w") as book:
        # The mimetype comes first and uncompressed, as readers expect.
        book.writestr(
            "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        book.writestr("META-INF/container.xml", CONTAINER, zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/nav.xhtml", nav, zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/toc.ncx", ncx, zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/style.css", STYLE, zipfile.ZIP_DEFLATED)
        for name, entry in zip(names, chapters, strict=True):
            book.writestr(f"OEBPS/{name}", _chapter(entry), zipfile.ZIP_DEFLATED)
    return len(chapters)
