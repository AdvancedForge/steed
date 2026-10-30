"""Persistent user-owned folders, revisioned files and recoverable removal."""

import base64
import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import PurePosixPath
from uuid import uuid4

FILE_BYTES = 64 * 1024 * 1024
LIBRARY_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 32768
# Content search reads text files up to this size and returns short snippets.
CONTENT_SEARCH_BYTES = 4 * 1024 * 1024
SNIPPET_CHARS = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entries (
 folder TEXT NOT NULL REFERENCES folders(id), path TEXT NOT NULL, kind TEXT NOT NULL,
 revision INTEGER NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0, trash_id TEXT NOT NULL DEFAULT '',
 modified REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(folder,path)
);
CREATE TABLE IF NOT EXISTS versions (
 folder TEXT NOT NULL, path TEXT NOT NULL, revision INTEGER NOT NULL,
 kind TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(folder,path,revision)
);
CREATE TABLE IF NOT EXISTS writes (
 id TEXT PRIMARY KEY, task TEXT NOT NULL, request TEXT NOT NULL,
 folder TEXT NOT NULL REFERENCES folders(id), path TEXT NOT NULL,
 size INTEGER NOT NULL, sha256 TEXT NOT NULL, expected INTEGER NOT NULL,
 received INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'building',
 revision INTEGER NOT NULL DEFAULT 0, UNIQUE(task,request)
);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT NOT NULL REFERENCES writes(id), offset INTEGER NOT NULL, data BLOB NOT NULL,
 PRIMARY KEY(id,offset)
);
CREATE TABLE IF NOT EXISTS activity (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL,
 folder TEXT NOT NULL, kind TEXT NOT NULL, reference TEXT NOT NULL
);
PRAGMA user_version=2;
"""


def relative_path(path, *, root=False):
    if root and path == "":
        return path
    if (
        not isinstance(path, str)
        or not 1 <= len(path) <= 512
        or not path.isprintable()
        or "\\" in path
        or path.startswith("/")
        or str(PurePosixPath(path)) != path
        or any(p in (".", "..") or len(p) > 128 for p in path.split("/"))
        or len(path.split("/")) > 32
    ):
        raise ValueError("Use a normalized relative library path, without traversal")
    return path


class FileLibrary:
    def __init__(self, path, *, access_changed=None):
        self._access_changed = access_changed
        self._owner = threading.get_ident()
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Library storage must be a regular file")
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        if self.db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
            self.db.close()
            raise ValueError("Unsupported library database")
        self.db.executescript(SCHEMA)
        # Version 2 records when each entry last changed; older entries read as 0.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(entries)")}
        if "modified" not in columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE entries ADD COLUMN modified REAL NOT NULL DEFAULT 0"
                )
        # Never resume an unfinished mutation for an interrupted task on restart.
        with self.db:
            self.db.execute(
                "UPDATE writes SET state='cancelled' WHERE state='building'"
            )
            self.db.execute(
                "DELETE FROM chunks WHERE id IN (SELECT id FROM writes WHERE state='cancelled')"
            )

    def check(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Library operations require the owning thread")

    def close(self):
        self.check()
        self.db.close()

    def folders(self, *, enabled_only=False):
        self.check()
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id,name,enabled FROM folders "
                + ("WHERE enabled=1 " if enabled_only else "")
                + "ORDER BY name,id"
            )
        ]

    def create_folder(self, name):
        self.check()
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 128
            or not name.isprintable()
        ):
            raise ValueError(
                "Give the folder a name of at most 128 printable characters"
            )
        if len(self.folders()) >= 50:
            raise ValueError("The library supports up to 50 shared folders")
        folder_id = uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO folders(id,name) VALUES (?,?)", (folder_id, name.strip())
            )
        return folder_id

    def enable(self, folder_id, enabled):
        self.check()
        self.folder(folder_id)
        if type(enabled) is not bool:
            raise ValueError("Choose whether to enable folder access")
        with self.db:
            self.db.execute(
                "UPDATE folders SET enabled=? WHERE id=?", (int(enabled), folder_id)
            )
            if not enabled:
                self.db.execute(
                    "DELETE FROM chunks WHERE id IN (SELECT id FROM writes WHERE folder=? AND state='building')",
                    (folder_id,),
                )
                self.db.execute(
                    "UPDATE writes SET state='cancelled' WHERE folder=? AND state='building'",
                    (folder_id,),
                )

        if self._access_changed is not None:
            self._access_changed()

    def folder(self, folder_id, *, agent=False):
        self.check()
        row = self.db.execute(
            "SELECT * FROM folders WHERE id=?", (folder_id,)
        ).fetchone()
        if row is None or (agent and not row["enabled"]):
            raise ValueError(
                "Library folder is unavailable or agent access is disabled"
            )
        return dict(row)

    def row(self, folder_id, path):
        return self.db.execute(
            "SELECT * FROM entries WHERE folder=? AND path=?",
            (folder_id, relative_path(path)),
        ).fetchone()

    @staticmethod
    def metadata(row):
        return {
            k: row[k] for k in ("path", "kind", "revision", "sha256", "deleted")
        } | {"byte_count": len(row["data"])}

    def list(self, folder_id, path="", after="", limit=200, *, trash=False):
        self.folder(folder_id)
        relative_path(path, root=True)
        relative_path(after, root=True)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("List between 1 and 200 entries")
        prefix = path + "/" if path else ""
        # Range comparisons avoid SQL LIKE wildcard interpretation of filenames.
        rows = self.db.execute(
            "SELECT path,kind,revision,sha256,deleted,length(data) AS byte_count FROM entries WHERE folder=? AND deleted=? AND path>? ORDER BY path",
            (folder_id, int(trash), after),
        )
        items = []
        for row in rows:
            if row["path"].startswith(prefix) and (
                trash or "/" not in row["path"][len(prefix) :]
            ):
                items.append(dict(row))
                if len(items) > limit:
                    break
        return {
            "entries": items[:limit],
            "cursor": items[min(len(items), limit) - 1]["path"] if items else after,
            "has_more": len(items) > limit,
        }

    @staticmethod
    def _search_cursor(cursor):
        """Decode an opaque search cursor into its (folder name, folder id, path)."""
        if cursor in ("", None):
            return None
        if not isinstance(cursor, str) or len(cursor) > 1024:
            raise ValueError("Give the after cursor returned by a previous search")
        try:
            decoded = json.loads(cursor)
        except ValueError:
            raise ValueError(
                "Give the after cursor returned by a previous search"
            ) from None
        if (
            not isinstance(decoded, list)
            or len(decoded) != 3
            or not all(isinstance(part, str) and part for part in decoded)
            or len(decoded[0]) > 128
            or len(decoded[1]) > 64
        ):
            raise ValueError("Give the after cursor returned by a previous search")
        relative_path(decoded[2], root=True)
        return decoded

    @staticmethod
    def _search_query(query):
        """Validate a discovery query. Punctuation stays literal, never a pattern."""
        if not isinstance(query, str) or not query.isprintable() or len(query) > 512:
            raise ValueError("Search text must be at most 512 printable characters")
        return query

    def search(
        self,
        query="",
        after="",
        limit=200,
        *,
        folder_id=None,
        content=False,
        order="path",
    ):
        """Find entry metadata across enabled roots.

        This is the agent-facing discovery surface, so only enabled roots are
        ever considered. Path order is by folder name then path so paging is
        deterministic and groups each root together, and each row carries its
        folder id and name so identical file names in different roots stay
        distinguishable. ``instr`` gives literal substring matching, so ``%`` and
        ``_`` in a query are never wildcards. With ``content``, text files up to
        CONTENT_SEARCH_BYTES also match on their contents (ASCII case-insensitive)
        and return a short snippet. ``order="recent"`` returns the most recently
        changed entries first, without paging.
        """
        self.check()
        query = self._search_query(query)
        position = self._search_cursor(after)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("List between 1 and 200 entries")
        if order not in ("path", "recent"):
            raise ValueError("Order by path or recent")
        if type(content) is not bool:
            raise ValueError("content must be true or false")
        if content and not query:
            raise ValueError("Give search text to look for inside files")
        if order == "recent" and position is not None:
            raise ValueError("Recent results are not paged; raise the limit instead")
        if folder_id is not None:
            # Re-checked on every call so a root disabled since discovery hides.
            self.folder(folder_id, agent=True)
        # Text files only: a NUL byte near the start marks binary content.
        text_match = (
            " OR (? AND e.kind='file' AND length(e.data)<=?"
            " AND instr(substr(e.data,1,1024),X'00')=0"
            " AND instr(lower(CAST(e.data AS TEXT)),lower(?))>0)"
        )
        ordering = (
            " ORDER BY e.modified DESC, f.name, e.folder, e.path"
            if order == "recent"
            else " ORDER BY f.name, e.folder, e.path"
        )
        # Fetch one extra match for paging; do not materialize thousands of rows.
        rows = self.db.execute(
            "SELECT e.folder AS folder_id, f.name AS folder, e.path, e.kind,"
            " e.revision, e.sha256, length(e.data) AS byte_count,"
            " CAST(e.modified AS INTEGER) AS modified,"
            " instr(e.path, ?)>0 AS path_match"
            " FROM entries e JOIN folders f ON f.id=e.folder"
            " WHERE e.deleted=0 AND f.enabled=1"
            " AND (? IS NULL OR e.folder=?)"
            " AND (instr(e.path, ?)>0" + text_match + ")"
            " AND (? IS NULL OR (f.name, e.folder, e.path) > (?, ?, ?))"
            + ordering
            + " LIMIT ?",
            (
                query,
                folder_id,
                folder_id,
                query,
                int(content),
                CONTENT_SEARCH_BYTES,
                query,
                position[0] if position else None,
                position[0] if position else None,
                position[1] if position else None,
                position[2] if position else None,
                limit + 1,
            ),
        ).fetchall()
        entries = []
        for row in rows[:limit]:
            entry = dict(row)
            path_match = entry.pop("path_match")
            if content:
                entry["match"] = "path" if path_match else "content"
                if not path_match:
                    entry["snippet"] = self._snippet(
                        entry["folder_id"], entry["path"], query
                    )
            entries.append(entry)
        cursor = after
        if entries and order == "path":
            cursor = json.dumps(
                [entries[-1]["folder"], entries[-1]["folder_id"], entries[-1]["path"]],
                ensure_ascii=False,
            )
        return {
            "entries": entries,
            "cursor": cursor,
            "has_more": len(rows) > limit,
            "truncated": len(rows) > limit,
        }

    def _snippet(self, folder_id, path, query):
        """Up to SNIPPET_CHARS of text around the first match, whitespace folded."""
        data = self.db.execute(
            "SELECT data FROM entries WHERE folder=? AND path=?", (folder_id, path)
        ).fetchone()[0]
        text = bytes(data).decode("utf-8", errors="replace")
        at = text.lower().find(query.lower())
        if at < 0:
            return ""
        start = max(0, at - SNIPPET_CHARS // 2)
        piece = " ".join(text[start : start + SNIPPET_CHARS * 2].split())
        return ("…" if start else "") + piece[:SNIPPET_CHARS]

    def capacity(self, incoming=0):
        used = self.db.execute(
            "SELECT (SELECT coalesce(sum(length(data)),0) FROM entries)+(SELECT coalesce(sum(length(data)),0) FROM versions)+(SELECT coalesce(sum(size),0) FROM writes WHERE state='building')"
        ).fetchone()[0]
        count = self.db.execute(
            "SELECT (SELECT count(*) FROM entries)+(SELECT count(*) FROM versions)+(SELECT count(*) FROM activity)"
        ).fetchone()[0]
        if used + incoming > LIBRARY_BYTES or count >= 50000:
            raise ValueError(
                "Library capacity reached (512 MiB including recovery copies)"
            )

    def archive(self, row):
        if row is not None and not row["deleted"]:
            self.db.execute(
                "INSERT OR IGNORE INTO versions VALUES (?,?,?,?,?,?)",
                (
                    row["folder"],
                    row["path"],
                    row["revision"],
                    row["kind"],
                    row["data"],
                    row["sha256"],
                ),
            )

    def parents(self, folder_id, path):
        parts = path.split("/")
        for n in range(1, len(parts)):
            parent = "/".join(parts[:n])
            row = self.row(folder_id, parent)
            if row and not row["deleted"]:
                if row["kind"] != "directory":
                    raise ValueError("A parent path is a file")
            else:
                self.put(folder_id, parent, "directory", b"", 0)

    def touch_parents(self, folder_id, path):
        for parent in PurePosixPath(path).parents:
            if str(parent) != ".":
                self.db.execute(
                    "UPDATE entries SET revision=revision+1 WHERE folder=? AND path=? AND deleted=0",
                    (folder_id, str(parent)),
                )

    def put(self, folder_id, path, kind, data, expected):
        relative_path(path)
        self.capacity()
        if type(expected) is not int or expected < 0:
            raise ValueError("Provide a nonnegative expected revision")
        row = self.row(folder_id, path)
        current = row["revision"] if row and not row["deleted"] else 0
        if current != expected or (row and not row["deleted"] and row["kind"] != kind):
            raise ValueError("File changed; list the folder again before writing")
        self.parents(folder_id, path)
        self.archive(row)
        revision = row["revision"] + 1 if row else 1
        digest = hashlib.sha256(data).hexdigest() if kind == "file" else ""
        self.db.execute(
            "INSERT OR REPLACE INTO entries"
            " (folder,path,kind,revision,data,sha256,deleted,trash_id,modified)"
            " VALUES (?,?,?,?,?,?,0,'',?)",
            (folder_id, path, kind, revision, data, digest, time.time()),
        )
        self.touch_parents(folder_id, path)
        return self.metadata(self.row(folder_id, path))

    def mkdir(self, folder_id, path):
        self.folder(folder_id)
        with self.db:
            self.capacity()
            row = self.row(folder_id, path)
            if row and not row["deleted"] and row["kind"] == "directory":
                return self.metadata(row)
            return self.put(folder_id, path, "directory", b"", 0)

    def read(self, folder_id, path, revision, offset=0, limit=CHUNK_BYTES):
        self.folder(folder_id)
        row = self.db.execute(
            "SELECT path,revision,kind,sha256,deleted,length(data) AS byte_count FROM entries WHERE folder=? AND path=?",
            (folder_id, relative_path(path)),
        ).fetchone()
        if (
            row is None
            or row["deleted"]
            or row["kind"] != "file"
            or type(revision) is not int
            or row["revision"] != revision
        ):
            raise ValueError("File is unavailable or changed; list it again")
        if (
            type(offset) is not int
            or not 0 <= offset <= row["byte_count"]
            or type(limit) is not int
            or not 1 <= limit <= CHUNK_BYTES
        ):
            raise ValueError("Read a valid byte range of at most 32768 bytes")
        data = self.db.execute(
            "SELECT substr(data,?,?) FROM entries WHERE folder=? AND path=?",
            (offset + 1, limit, folder_id, path),
        ).fetchone()[0]
        return {
            "path": path,
            "revision": revision,
            "sha256": row["sha256"],
            "offset": offset,
            "data_base64": base64.b64encode(data).decode(),
            "eof": offset + len(data) == row["byte_count"],
        }

    def begin(
        self, task, folder_id, path, request_id, byte_count, sha256, expected_revision=0
    ):
        self.folder(folder_id)
        relative_path(path)
        if (
            not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 64
            or not request_id.isprintable()
        ):
            raise ValueError("Provide a stable write request ID")
        if (
            type(byte_count) is not int
            or not 0 <= byte_count <= FILE_BYTES
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(c not in "0123456789abcdef" for c in sha256)
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            raise ValueError(
                "Declare a file up to 64 MiB, SHA-256 and expected revision"
            )
        old = self.db.execute(
            "SELECT * FROM writes WHERE task=? AND request=?", (task, request_id)
        ).fetchone()
        if old:
            if (
                old["folder"],
                old["path"],
                old["size"],
                old["sha256"],
                old["expected"],
            ) != (folder_id, path, byte_count, sha256, expected_revision):
                raise ValueError(
                    "Write request ID is already bound to different content"
                )
            return dict(old)
        with self.db:
            self.capacity(byte_count)
            if (
                self.db.execute(
                    "SELECT count(*) FROM writes WHERE state='building'"
                ).fetchone()[0]
                >= 32
            ):
                raise ValueError("Too many unfinished library writes")
            write_id = uuid4().hex
            self.db.execute(
                "INSERT INTO writes(id,task,request,folder,path,size,sha256,expected) VALUES (?,?,?,?,?,?,?,?)",
                (
                    write_id,
                    task,
                    request_id,
                    folder_id,
                    path,
                    byte_count,
                    sha256,
                    expected_revision,
                ),
            )
        return self.write_info(task, write_id)

    def write_info(self, task, write_id):
        self.check()
        row = self.db.execute(
            "SELECT * FROM writes WHERE task=? AND id=?", (task, write_id)
        ).fetchone()
        if row is None:
            raise ValueError("Write belongs to another task or is unavailable")
        return dict(row)

    def cancel_task(self, task):
        self.check()
        with self.db:
            self.db.execute(
                "DELETE FROM chunks WHERE id IN (SELECT id FROM writes WHERE task=? AND state='building')",
                (task,),
            )
            self.db.execute(
                "UPDATE writes SET state='cancelled' WHERE task=? AND state='building'",
                (task,),
            )

    def chunk(self, task, write_id, offset, data_base64):
        item = self.write_info(task, write_id)
        if item["state"] != "building":
            raise ValueError("This write is no longer building")
        if (
            type(offset) is not int
            or not isinstance(data_base64, str)
            or len(data_base64) > 43692
        ):
            raise ValueError("Provide a valid chunk")
        data = base64.b64decode(data_base64, validate=True)
        if (
            not 1 <= len(data) <= CHUNK_BYTES
            or base64.b64encode(data).decode() != data_base64
        ):
            raise ValueError("Use canonical base64 chunks up to 32768 bytes")
        old = self.db.execute(
            "SELECT data FROM chunks WHERE id=? AND offset=?", (write_id, offset)
        ).fetchone()
        if old is not None and old[0] == data:
            return item
        if offset != item["received"] or offset + len(data) > item["size"]:
            raise ValueError("Chunk skips, overlaps or exceeds the declaration")
        with self.db:
            self.db.execute(
                "INSERT INTO chunks VALUES (?,?,?)", (write_id, offset, data)
            )
            self.db.execute(
                "UPDATE writes SET received=received+? WHERE id=?",
                (len(data), write_id),
            )
        return self.write_info(task, write_id)

    def commit(self, task, write_id):
        item = self.write_info(task, write_id)
        if item["state"] == "committed":
            return item
        if item["state"] != "building" or item["received"] != item["size"]:
            raise ValueError("Write is cancelled or incomplete")
        data = b"".join(
            row[0]
            for row in self.db.execute(
                "SELECT data FROM chunks WHERE id=? ORDER BY offset", (write_id,)
            )
        )
        if (
            len(data) != item["size"]
            or hashlib.sha256(data).hexdigest() != item["sha256"]
        ):
            raise ValueError("Library write length/hash mismatch")
        with self.db:
            # Reservation includes the new bytes; archived old bytes replace the
            # existing entry, so commit cannot exceed the reserved content budget.
            result = self.put(
                item["folder"], item["path"], "file", data, item["expected"]
            )
            self.db.execute(
                "UPDATE writes SET state='committed',revision=? WHERE id=?",
                (result["revision"], write_id),
            )
            self.db.execute("DELETE FROM chunks WHERE id=?", (write_id,))
            self.db.execute(
                "INSERT INTO activity(task,folder,kind,reference) VALUES (?,?,'write',?)",
                (task, item["folder"], write_id),
            )
        return self.write_info(task, write_id)

    def remove(self, folder_id, path, expected_revision, *, task="user"):
        self.folder(folder_id)
        row = self.row(folder_id, path)
        if (
            row is None
            or row["deleted"]
            or type(expected_revision) is not int
            or row["revision"] != expected_revision
        ):
            raise ValueError("Entry changed or is already removed")
        with self.db:
            self.capacity()
            trash_id = uuid4().hex
            rows = self.db.execute(
                "SELECT * FROM entries WHERE folder=? AND deleted=0 ORDER BY path",
                (folder_id,),
            ).fetchall()
            removed = 0
            for item in rows:
                if item["path"] == path or item["path"].startswith(path + "/"):
                    self.archive(item)
                    self.db.execute(
                        "UPDATE entries SET revision=revision+1,data=?,deleted=1,trash_id=? WHERE folder=? AND path=?",
                        (b"", trash_id, folder_id, item["path"]),
                    )
                    removed += 1
            self.touch_parents(folder_id, path)
            self.db.execute(
                "INSERT INTO activity(task,folder,kind,reference) VALUES (?,?,'remove',?)",
                (task, folder_id, trash_id),
            )
        return {"removed": removed, "recoverable": True}

    def restore(self, folder_id, path):
        self.folder(folder_id)
        selected = self.row(folder_id, path)
        if selected is None or not selected["deleted"]:
            raise ValueError("Choose an entry in Trash")
        rows = self.db.execute(
            "SELECT * FROM entries WHERE folder=? AND trash_id=? AND deleted=1 ORDER BY path",
            (folder_id, selected["trash_id"]),
        ).fetchall()
        with self.db:
            recovery = []
            for item in rows:
                if item["path"] != path and not item["path"].startswith(path + "/"):
                    continue
                old = self.db.execute(
                    "SELECT * FROM versions WHERE folder=? AND path=? ORDER BY revision DESC LIMIT 1",
                    (folder_id, item["path"]),
                ).fetchone()
                if old is None:
                    raise ValueError("Recovery copy is unavailable")
                if (
                    old["kind"] == "file"
                    and hashlib.sha256(old["data"]).hexdigest() != old["sha256"]
                ):
                    raise ValueError("Recovery copy failed its integrity check")
                recovery.append((item, old))
            self.capacity(sum(len(old["data"]) for _, old in recovery))
            for item, old in recovery:
                self.parents(folder_id, item["path"])
                self.db.execute(
                    "UPDATE entries SET revision=revision+1,data=?,sha256=?,deleted=0,trash_id='',modified=? WHERE folder=? AND path=?",
                    (old["data"], old["sha256"], time.time(), folder_id, item["path"]),
                )
                self.touch_parents(folder_id, item["path"])
        return self.metadata(self.row(folder_id, path))

    def import_files(self, folder_id, files):
        self.folder(folder_id)
        if not files or len(files) > 500:
            raise ValueError("Import between 1 and 500 files at a time")
        with self.db:
            self.capacity(sum(len(data) for _, data in files if data is not None))
            for path, data in files:
                if data is None:
                    row = self.row(folder_id, path)
                    if row is None or row["deleted"]:
                        self.put(folder_id, path, "directory", b"", 0)
                    elif row["kind"] != "directory":
                        raise ValueError("A directory name is already used by a file")
                    continue
                if not isinstance(data, bytes) or len(data) > FILE_BYTES:
                    raise ValueError("Each library file must be at most 64 MiB")
                self.put(folder_id, path, "file", data, 0)
        return {"added": len(files)}

    def available_path(self, folder_id, path):
        """``path``, or ``name (2).ext`` and so on if an entry already uses it."""
        self.folder(folder_id)
        relative_path(path)
        pure = PurePosixPath(path)
        for number in range(1, 1000):
            candidate = (
                path
                if number == 1
                else str(pure.with_name(f"{pure.stem} ({number}){pure.suffix}"))
            )
            row = self.row(folder_id, candidate)
            if row is None or row["deleted"]:
                return candidate
        raise ValueError("Too many files with this name; rename it first")

    def clear_recovery(self, folder_id):
        self.folder(folder_id)
        with self.db:
            self.db.execute("DELETE FROM versions WHERE folder=?", (folder_id,))
            self.db.execute(
                "DELETE FROM entries WHERE folder=? AND deleted=1", (folder_id,)
            )

    def history(self, folder_id, path):
        self.folder(folder_id)
        relative_path(path)
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT revision,kind,sha256,length(data) AS byte_count FROM versions WHERE folder=? AND path=? ORDER BY revision DESC LIMIT 100",
                (folder_id, path),
            )
        ]

    def restore_version(self, folder_id, path, revision, expected_revision):
        self.folder(folder_id)
        old = self.db.execute(
            "SELECT * FROM versions WHERE folder=? AND path=? AND revision=?",
            (folder_id, relative_path(path), revision),
        ).fetchone()
        if old is None or old["kind"] != "file":
            raise ValueError("Choose a saved file version")
        if hashlib.sha256(old["data"]).hexdigest() != old["sha256"]:
            raise ValueError("Saved version failed its integrity check")
        with self.db:
            self.capacity(len(old["data"]))
            return self.put(folder_id, path, "file", old["data"], expected_revision)


def read_import(paths, prefix=""):
    """Copy picker selections using descriptor-relative, non-following traversal."""
    files, total = [], 0

    def append(name, data):
        nonlocal total
        name = relative_path(prefix + name)
        total += len(data) if data is not None else 0
        if (
            (data is not None and len(data) > FILE_BYTES)
            or total > LIBRARY_BYTES
            or len(files) >= 500
        ):
            raise ValueError("Import exceeds the library file/size limits")
        files.append((name, data))

    def read_fd(fd, name):
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Import only regular files and directories")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(FILE_BYTES + 1)
        append(name, data)

    def walk(fd, name):
        append(name, None)
        with os.scandir(fd) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
        for entry in entries:
            if entry.is_symlink():
                raise ValueError("Folders containing symlinks are not imported")
            child = os.open(
                entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            try:
                child_name = name + "/" + entry.name
                if stat.S_ISDIR(os.fstat(child).st_mode):
                    walk(child, child_name)
                else:
                    read_fd(child, child_name)
            finally:
                os.close(child)

    for path in paths:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                walk(fd, path.name)
            else:
                read_fd(fd, path.name)
        finally:
            os.close(fd)
    return files
