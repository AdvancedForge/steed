"""Claude Code's saved sessions, so a chat can continue in Steed's sidebar.

Claude Code keeps each session as a JSON-lines file under
~/.claude/projects/<folder>/<session id>.jsonl. Steed reads only what it shows
in the picker: the session's title (Claude Code's own, else its first prompt),
folder and last change. Files can be very large, so only their first and last
parts are read. Continuing runs `claude --resume <id> --fork-session` in that
folder: the conversation goes on as a copy, so a session still open in a
terminal is not written to twice.
"""

import json
import re
from pathlib import Path

SESSION_ID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 128 * 1024
TITLE_LIMIT = 120
MAX_SESSIONS = 40
# Folders of Steed's own (private, unsaved) chats, never worth continuing.
STEED_FOLDER = re.compile(r"^-tmp-steed-")


def projects_root():
    return Path.home() / ".claude" / "projects"


def _records(data):
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue  # A partial first or last line of a slice.
        if isinstance(value, dict):
            yield value


def _prompt_text(message):
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return ""


def _clean(text):
    text = " ".join(str(text).split())
    # Commands and system notes are not a title.
    if not text or text.startswith(("<", "/", "Caveat:")):
        return ""
    return text if len(text) <= TITLE_LIMIT else text[: TITLE_LIMIT - 1] + "…"


def read_session(path):
    """{"id", "title", "cwd", "updated"} for one session file, or None."""
    path = Path(path)
    if not SESSION_ID.fullmatch(path.stem):
        return None
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES)
            if size > HEAD_BYTES:
                handle.seek(max(HEAD_BYTES, size - TAIL_BYTES))
                tail = handle.read(TAIL_BYTES)
            else:
                tail = b""
        updated = path.stat().st_mtime
    except OSError:
        return None
    cwd, prompt, title = "", "", ""
    for record in _records(head.decode("utf-8", "replace")):
        if not cwd and isinstance(record.get("cwd"), str):
            cwd = record["cwd"]
        if (
            not prompt
            and record.get("type") == "user"
            and not record.get("isSidechain")
            and not record.get("isMeta")
        ):
            prompt = _clean(_prompt_text(record.get("message")))
        if record.get("type") == "ai-title":
            title = _clean(record.get("aiTitle", "")) or title
    for record in _records(tail.decode("utf-8", "replace")):
        if record.get("type") == "ai-title":
            title = _clean(record.get("aiTitle", "")) or title
    if not cwd or not Path(cwd).is_absolute():
        return None
    return {
        "id": path.stem,
        "title": title or prompt or "Untitled session",
        "cwd": cwd,
        "updated": updated,
    }


def list_sessions(root=None, limit=MAX_SESSIONS):
    """Recent sessions, newest first (Steed's own private chats left out)."""
    root = Path(root) if root is not None else projects_root()
    try:
        folders = [f for f in root.iterdir() if f.is_dir()]
    except OSError:
        return []
    files = []
    for folder in folders:
        if STEED_FOLDER.match(folder.name):
            continue
        try:
            files += [
                (p.stat().st_mtime, p)
                for p in folder.glob("*.jsonl")
                if SESSION_ID.fullmatch(p.stem)
            ]
        except OSError:
            continue
    sessions = []
    for _, path in sorted(files, reverse=True):
        found = read_session(path)
        if found is not None:
            sessions.append(found)
        if len(sessions) >= limit:
            break
    return sessions


def find_session(session_id, root=None):
    """The session with this id, checked against the files on disk."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return None
    root = Path(root) if root is not None else projects_root()
    try:
        for folder in root.iterdir():
            path = folder / f"{session_id}.jsonl"
            if path.is_file():
                found = read_session(path)
                if found is not None and Path(found["cwd"]).is_dir():
                    return found
    except OSError:
        return None
    return None
