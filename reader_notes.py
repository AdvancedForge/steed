"""Highlights and notes made in reader view, per article address.

Each highlight is a span of one block of the article (by its index and
character offsets), its text, and an optional note. Kept in
<profile>/reader_notes.json; incognito keeps them only while reading.
"""

from pathlib import Path
from uuid import uuid4

from utils import load_json, save_json

MAX_ARTICLES = 1000
MAX_HIGHLIGHTS = 500
MAX_NOTE = 4000


def _highlight(item):
    if not (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and all(type(item.get(k)) is int for k in ("block", "start", "end"))
        and 0 <= item["start"] < item["end"]
    ):
        return None
    return {
        "id": item["id"][:40],
        "block": item["block"],
        "start": item["start"],
        "end": item["end"],
        "text": str(item.get("text", ""))[:2000],
        "note": str(item.get("note", ""))[:MAX_NOTE],
    }


class ReaderNotes:
    def __init__(self, profile_dir):
        self.private = str(profile_dir) == "."
        self.path = Path(profile_dir) / "reader_notes.json"
        self.memory = {}  # Incognito: only while Steed runs.

    def _all(self) -> dict:
        if self.private:
            return self.memory
        raw = load_json(self.path, {})
        return raw if isinstance(raw, dict) else {}

    def _save(self, everything):
        if self.private:
            self.memory = everything
            return
        if len(everything) > MAX_ARTICLES:
            everything = dict(list(everything.items())[-MAX_ARTICLES:])
        save_json(self.path, everything)

    def highlights(self, url) -> list[dict]:
        found = self._all().get(str(url), [])
        items = [
            h for h in map(_highlight, found if isinstance(found, list) else []) if h
        ]
        return sorted(items, key=lambda h: (h["block"], h["start"]))

    def _put(self, url, items):
        everything = self._all()
        if items:
            everything[str(url)] = items
        else:
            everything.pop(str(url), None)
        self._save(everything)

    def add(self, url, block, start, end, text) -> dict:
        items = self.highlights(url)
        if len(items) >= MAX_HIGHLIGHTS:
            raise ValueError("Too many highlights in this article.")
        made = _highlight(
            {
                "id": uuid4().hex,
                "block": block,
                "start": start,
                "end": end,
                "text": text,
            }
        )
        if made is None:
            raise ValueError("Select some text within one paragraph.")
        # A highlight overlapping another replaces it.
        items = [
            h
            for h in items
            if h["block"] != block or h["end"] <= start or h["start"] >= end
        ]
        self._put(url, [*items, made])
        return made

    def note(self, url, highlight_id, text):
        items = self.highlights(url)
        for item in items:
            if item["id"] == highlight_id:
                item["note"] = str(text)[:MAX_NOTE]
        self._put(url, items)

    def remove(self, url, highlight_id):
        self._put(url, [h for h in self.highlights(url) if h["id"] != highlight_id])
