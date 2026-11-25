"""Search what you've read: the text of pages you visited, on this computer.

Off until Settings → General → "Remember what I read" is on. Then each web
page you finish loading (a normal profile; never incognito, never a page with
a password field, never a site you excluded) has its text kept in
<profile>/read_pages.sqlite, a full-text index of up to MAX_PAGES pages, the
newest kept. History → Search What You've Read… (Ctrl+Shift+F) finds pages by
any words in them, and can hand the matches to your agent. Clearing browsing
history clears this too.
"""

import re
import sqlite3
import time
from pathlib import Path

MAX_PAGES = 5000
MAX_TEXT = 60000
SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
  url UNINDEXED, title, text, visited UNINDEXED, tokenize='unicode61'
);
"""
# Steed's own script world reads this: the text, and whether it asks for a password.
READ = r"""
(() => {
  const password = [...document.querySelectorAll("input[type=password]")]
    .some((f) => f.offsetParent !== null);
  const text = document.body ? document.body.innerText : "";
  return JSON.stringify({password, text: text.slice(0, MAX_TEXT)});
})()
""".replace("MAX_TEXT", str(MAX_TEXT))


def excluded(host, sites):
    host = (host or "").lower().removeprefix("www.")
    return any(host == s or host.endswith("." + s) for s in sites)


def query_of(text, *, any_word=False):
    """Words as an FTS5 query: every word must appear (prefix match on the
    last), or with any_word, any of them (each a prefix)."""
    words = re.findall(r"\w+", text or "", re.UNICODE)[:12]
    if not words:
        return None
    quoted = ['"' + w.replace('"', "") + '"' for w in words]
    if any_word:
        return " OR ".join(word + "*" for word in quoted)
    quoted[-1] += "*"
    return " ".join(quoted)


class PageMemory:
    def __init__(self, path: Path | None):
        self.path = path
        self._db = None

    @property
    def db(self):
        if self._db is None and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path))
            self._db.executescript(SCHEMA)
        return self._db

    def remember(self, url, title, text, when=None):
        if self.db is None or not text or not text.strip():
            return
        text = " ".join(text.split())[:MAX_TEXT]
        with self.db:
            self.db.execute("DELETE FROM pages WHERE url = ?", (url,))
            self.db.execute(
                "INSERT INTO pages (url, title, text, visited) VALUES (?, ?, ?, ?)",
                (url, (title or url)[:300], text, when or time.time()),
            )
            count = self.db.execute("SELECT count(*) FROM pages").fetchone()[0]
            if count > MAX_PAGES:
                self.db.execute(
                    "DELETE FROM pages WHERE rowid IN (SELECT rowid FROM pages "
                    "ORDER BY CAST(visited AS REAL) LIMIT ?)",
                    (count - MAX_PAGES,),
                )

    def search(self, text, limit=30, *, any_word=False):
        """[{url, title, snippet, visited}], best matches first."""
        query = query_of(text, any_word=any_word)
        if self.db is None or query is None:
            return []
        try:
            rows = self.db.execute(
                "SELECT url, title, snippet(pages, 2, '«', '»', '…', 18), visited, "
                "text FROM pages WHERE pages MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {"url": u, "title": t, "snippet": s, "visited": float(v), "text": x}
            for u, t, s, v, x in rows
        ]

    def count(self):
        return (
            self.db.execute("SELECT count(*) FROM pages").fetchone()[0]
            if self.db
            else 0
        )

    def forget(self, since=0.0):
        """Pages read at or after since (0: all), as history forgets them."""
        if self.db is None:
            return
        with self.db:
            if since <= 0:
                self.db.execute("DELETE FROM pages")
            else:
                self.db.execute(
                    "DELETE FROM pages WHERE CAST(visited AS REAL) >= ?", (since,)
                )

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
