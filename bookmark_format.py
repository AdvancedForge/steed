"""Bounded, inert Netscape bookmark HTML import/export and folder labels."""

from html import escape
from html.parser import HTMLParser

MAX_BOOKMARK_HTML_BYTES = 8 * 1024 * 1024
MAX_BOOKMARKS = 10000


def bookmark_name(value, *, optional=False):
    if not isinstance(value, str):
        raise ValueError("Use a printable bookmark or folder name")
    value = " ".join(value.split())
    if (
        (not optional and not value)
        or len(value) > 200
        or any(not c.isprintable() for c in value)
    ):
        raise ValueError("Use a printable name of at most 200 characters")
    return value


class BookmarkParser(HTMLParser):
    def __init__(self, validate_url):
        super().__init__(convert_charrefs=True)
        self.validate_url = validate_url
        self.folders, self.pending_folder = [], None
        self.heading = self.anchor = None
        self.entries, self.skipped, self.anchors = [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self.heading = ""
        elif tag == "dl":
            if len(self.folders) >= 16:
                raise ValueError("Bookmark folders are nested too deeply")
            self.folders.append(self.pending_folder or "")
            self.pending_folder = None
        elif tag == "a":
            self.anchors += 1
            if self.anchors > MAX_BOOKMARKS * 4:
                raise ValueError("Too many entries in the bookmark file")
            addresses = [v for k, v in attrs if k == "href"]
            self.anchor = (addresses[0] if len(addresses) == 1 else None, "")

    def handle_data(self, data):
        if self.heading is not None:
            self.heading = (self.heading + data)[:201]
        if self.anchor is not None:
            url, title = self.anchor
            self.anchor = url, (title + data)[:201]

    def handle_endtag(self, tag):
        if tag == "h3" and self.heading is not None:
            try:
                self.pending_folder = bookmark_name(self.heading, optional=True)
            except ValueError:
                self.pending_folder = ""
            self.heading = None
        elif tag == "dl":
            if self.folders:
                self.folders.pop()
            self.pending_folder = None
        elif tag == "a" and self.anchor is not None:
            url, title = self.anchor
            self.anchor = None
            try:
                url = self.validate_url(url)
                title = bookmark_name(title.strip() or url)
                folder = bookmark_name(
                    " / ".join(f for f in self.folders if f), optional=True
                )
            except ValueError:
                self.skipped += 1
                return
            self.entries.append({"url": url, "title": title, "folder": folder})
            if len(self.entries) > MAX_BOOKMARKS:
                raise ValueError("The bookmark file exceeds the 2,000-entry limit")


def parse_bookmarks(content, validate_url):
    if (
        not isinstance(content, bytes)
        or not 0 < len(content) <= MAX_BOOKMARK_HTML_BYTES
    ):
        raise ValueError("Choose a bookmark HTML file of at most 8 MiB")
    try:
        source = content.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("Choose a UTF-8 bookmark HTML file") from None
    parser = BookmarkParser(validate_url)
    parser.feed(source)
    parser.close()
    if not parser.anchors:
        raise ValueError("No bookmark links found in this HTML file")
    return parser.entries, parser.skipped


def export_bookmarks(items):
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Steed Bookmarks</TITLE>",
        "<H1>Steed Bookmarks</H1>",
        "<DL><p>",
    ]
    groups = {}
    for item in items:
        groups.setdefault(item.get("folder", ""), []).append(item)
    for folder, bookmarks in groups.items():
        if folder:
            lines.extend([f"<DT><H3>{escape(folder)}</H3>", "<DL><p>"])
        lines.extend(
            f'<DT><A HREF="{escape(b["url"], quote=True)}">{escape(b["title"])}</A>'
            for b in bookmarks
        )
        if folder:
            lines.append("</DL><p>")
    lines.append("</DL><p>")
    content = ("\n".join(lines) + "\n").encode()
    if len(content) > MAX_BOOKMARK_HTML_BYTES:
        raise ValueError("This export exceeds the 8 MiB limit; export fewer bookmarks")
    return content
