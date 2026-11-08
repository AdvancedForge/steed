"""A per-profile reading list: pages saved to read later, with their article text.

When reader view finds an article, its text (not images) is kept so the page
can be read offline, or after it changes. Stored in <profile>/reading_list.json;
incognito keeps none.
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from utils import load_json, save_json

MAX_ENTRIES = 500
MAX_ARTICLE_CHARS = 400_000


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _article(value):
    """Keep only a well-formed reader article (text blocks), or None."""
    if not isinstance(value, dict) or not isinstance(value.get("blocks"), list):
        return None
    blocks, chars = [], 0
    for block in value["blocks"]:
        if (
            isinstance(block, dict)
            and isinstance(block.get("type"), str)
            and isinstance(block.get("text"), str)
            and block["type"] != "img"
        ):
            chars += len(block["text"])
            if chars > MAX_ARTICLE_CHARS:
                break
            blocks.append({"type": block["type"], "text": block["text"]})
    if not blocks:
        return None
    article = {
        key: str(value.get(key, ""))[:300] for key in ("title", "byline", "site")
    }
    article["minutes"] = (
        value.get("minutes") if isinstance(value.get("minutes"), int) else 1
    )
    article["blocks"] = blocks
    return article


def _entry(item):
    if not (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("url"), str)
        and item["url"].startswith(("http://", "https://"))
    ):
        return None
    return {
        "id": item["id"],
        "url": item["url"][:4096],
        "title": str(item.get("title") or item["url"])[:300],
        "added": str(item.get("added", ""))[:40],
        "modified": str(item.get("modified") or item.get("added", ""))[:40],
        "read": item.get("read") is True,
        "article": _article(item.get("article")),
    }


class ReadingList:
    def __init__(self, profile_dir: Path):
        self.private = str(profile_dir) == "."
        self.path = Path(profile_dir) / "reading_list.json"

    def entries(self) -> list[dict]:
        """Unread first, then newest first."""
        if self.private:
            return []
        raw = load_json(self.path, [])
        found = [_entry(item) for item in raw if isinstance(raw, list)]
        found = [entry for entry in found if entry is not None]
        found.sort(key=lambda e: e["added"], reverse=True)
        return sorted(found, key=lambda e: e["read"])  # stable: unread, newest

    def _write(self, entries):
        save_json(self.path, entries[:MAX_ENTRIES])

    def add(self, url, title, article=None) -> dict:
        if self.private:
            raise ValueError("Incognito windows keep no reading list.")
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only web pages can be saved for later.")
        entries = self.entries()
        existing = next((e for e in entries if e["url"] == url), None)
        now = _now()
        if existing is not None:
            # Saving again refreshes it and marks it unread.
            existing.update(title=title or existing["title"], read=False, modified=now)
            if _article(article) is not None:
                existing["article"] = _article(article)
            entry = existing
        else:
            if len(entries) >= MAX_ENTRIES:
                raise ValueError(f"Remove some pages first (limit {MAX_ENTRIES}).")
            entry = _entry(
                {
                    "id": uuid4().hex,
                    "url": url,
                    "title": title,
                    "added": now,
                    "modified": now,
                    "read": False,
                    "article": article,
                }
            )
            entries.insert(0, entry)
        self._write(entries)
        return entry

    def set_read(self, entry_id, read=True) -> None:
        entries = self.entries()
        for entry in entries:
            if entry["id"] == entry_id:
                entry["read"] = bool(read)
                entry["modified"] = _now()
        self._write(entries)

    def remove(self, entry_id) -> None:
        self._write([e for e in self.entries() if e["id"] != entry_id])

    def replace_all(self, entries) -> None:
        """Used by sync: entries already merged from several computers."""
        found = [_entry(item) for item in entries]
        self._write([e for e in found if e is not None])
