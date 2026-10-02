"""Owned binary/source files; staging itself grants no execution authority."""

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

from services.connector_policy import connector_permissions

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_WORKSPACE_BYTES = 32 * 1024 * 1024
MAX_FILES = 50
CHUNK_BYTES = 32768


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest keys are not allowed")
        result[key] = value
    return result


WORKSPACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_files (
    file_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    expected_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('building','sealed','deleted')),
    data BLOB NOT NULL,
    source TEXT NOT NULL DEFAULT 'generated' CHECK(source IN ('generated','user_shared')),
    UNIQUE(task_id,name)
);
CREATE INDEX IF NOT EXISTS workspace_by_task ON workspace_files(task_id,file_id);
CREATE TABLE IF NOT EXISTS workspace_extensions (
    extension_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    manifest_id TEXT NOT NULL REFERENCES workspace_files(file_id),
    package_sha256 TEXT NOT NULL,
    files TEXT NOT NULL,
    UNIQUE(task_id,manifest_id)
);
"""


@dataclass(frozen=True)
class WorkspaceFile:
    file_id: str
    task_id: str
    name: str
    media_type: str
    expected_bytes: int
    sha256: str
    status: str
    byte_count: int
    source: str


def workspace_name(name):
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 200
        or not name.isprintable()
        or "\\" in name
        or name.startswith("/")
        or str(PurePosixPath(name)) != name
        or any(part in {".", ".."} or len(part) > 128 for part in name.split("/"))
    ):
        raise ValueError(
            "Provide a normalized relative workspace name without traversal"
        )
    return name


class TaskWorkspace:
    def __init__(self, store):
        self.store = store

    def get(self, task_id, file_id):
        self.store.get(task_id)
        row = self.store._db.execute(
            "SELECT file_id,task_id,name,media_type,expected_bytes,sha256,status,source,length(data) AS byte_count FROM workspace_files WHERE task_id=? AND file_id=?",
            (task_id, file_id),
        ).fetchone()
        if row is None:
            raise KeyError("Workspace file does not belong to this task")
        return WorkspaceFile(**dict(row))

    def list(self, task_id):
        self.store.get(task_id)
        return [
            self.get(task_id, row[0])
            for row in self.store._db.execute(
                "SELECT file_id FROM workspace_files WHERE task_id=? ORDER BY name",
                (task_id,),
            )
        ]

    def begin(self, task_id, name, media_type, byte_count, sha256):
        self.store.get(task_id)
        workspace_name(name)
        if (
            not isinstance(media_type, str)
            or len(media_type) > 100
            or not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type)
            or type(byte_count) is not int
            or not 0 <= byte_count <= MAX_FILE_BYTES
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", sha256)
        ):
            raise ValueError(
                "Declare a media type, size up to 8 MiB and lowercase SHA-256"
            )
        previous = self.store._db.execute(
            "SELECT file_id FROM workspace_files WHERE task_id=? AND name=?",
            (task_id, name),
        ).fetchone()
        if previous:
            result = self.get(task_id, previous[0])
            if (result.media_type, result.expected_bytes, result.sha256) != (
                media_type,
                byte_count,
                sha256,
            ) or result.status == "deleted":
                raise ValueError(
                    "This workspace name is bound to different or deleted content"
                )
            return result
        count, total = self.store._db.execute(
            "SELECT count(*),coalesce(sum(CASE WHEN status!='deleted' THEN expected_bytes ELSE 0 END),0) FROM workspace_files WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if count >= MAX_FILES or total + byte_count > MAX_WORKSPACE_BYTES:
            raise ValueError("Workspace limit reached: 50 files or 32 MiB per task")
        file_id = uuid4().hex
        with self.store._db:
            self.store._db.execute(
                "INSERT INTO workspace_files(file_id,task_id,name,media_type,expected_bytes,sha256,status,data) VALUES (?,?,?,?,?,?,'building',?)",
                (file_id, task_id, name, media_type, byte_count, sha256, b""),
            )
            self.store._append(
                task_id,
                "workspace.file_created",
                {"file_id": file_id, "byte_count": byte_count, "sha256": sha256},
            )
        return self.get(task_id, file_id)

    def write(self, task_id, file_id, offset, data_base64):
        item = self.get(task_id, file_id)
        if (
            type(offset) is not int
            or not 0 <= offset <= item.expected_bytes
            or not isinstance(data_base64, str)
            or len(data_base64) > 43692
        ):
            raise ValueError("Provide a bounded chunk and a valid byte offset")
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Chunk must be canonical base64") from None
        if (
            not data
            or len(data) > CHUNK_BYTES
            or base64.b64encode(data).decode("ascii") != data_base64
        ):
            raise ValueError("Write 1–32768 bytes of canonical base64 per chunk")
        if item.status != "building":
            raise ValueError("Only building files accept chunks")
        if offset < item.byte_count and offset + len(data) <= item.byte_count:
            old = self.store._db.execute(
                "SELECT substr(data,?,?) FROM workspace_files WHERE file_id=?",
                (offset + 1, len(data), file_id),
            ).fetchone()[0]
            if old == data:
                return item
        if offset != item.byte_count or offset + len(data) > item.expected_bytes:
            raise ValueError("Chunk overlaps, skips or exceeds the declared file")
        with self.store._db:
            self.store._db.execute(
                "UPDATE workspace_files SET data=CAST(data || ? AS BLOB) WHERE file_id=?",
                (data, file_id),
            )
            self.store._append(
                task_id,
                "workspace.chunk_written",
                {"file_id": file_id, "offset": offset, "byte_count": len(data)},
            )
        return self.get(task_id, file_id)

    def bytes(self, task_id, file_id):
        item = self.get(task_id, file_id)
        if item.status == "deleted":
            raise ValueError("Workspace file was deleted by the user")
        data = self.store._db.execute(
            "SELECT data FROM workspace_files WHERE file_id=?", (file_id,)
        ).fetchone()[0]
        if item.status == "sealed" and (
            len(data) != item.expected_bytes
            or hashlib.sha256(data).hexdigest() != item.sha256
        ):
            raise ValueError("Sealed workspace file integrity check failed")
        return data

    def seal(self, task_id, file_id):
        item = self.get(task_id, file_id)
        data = self.bytes(task_id, file_id)
        if (
            len(data) != item.expected_bytes
            or hashlib.sha256(data).hexdigest() != item.sha256
        ):
            raise ValueError(
                "File size or hash does not match its immutable declaration"
            )
        if item.status == "sealed":
            return item
        with self.store._db:
            self.store._db.execute(
                "UPDATE workspace_files SET status='sealed' WHERE file_id=?", (file_id,)
            )
            self.store._append(
                task_id,
                "workspace.file_sealed",
                {"file_id": file_id, "sha256": item.sha256},
            )
        return self.get(task_id, file_id)

    def read(self, task_id, file_id, offset=0, limit=CHUNK_BYTES):
        item = self.get(task_id, file_id)
        if (
            type(offset) is not int
            or not 0 <= offset <= item.byte_count
            or type(limit) is not int
            or not 1 <= limit <= CHUNK_BYTES
        ):
            raise ValueError("Read a valid byte range of at most 32768 bytes")
        data = self.bytes(task_id, file_id)[offset : offset + limit]
        self.store.record_event(
            task_id,
            "workspace.file_read",
            {"file_id": file_id, "offset": offset, "byte_count": len(data)},
        )
        return {
            "file_id": file_id,
            "offset": offset,
            "data_base64": base64.b64encode(data).decode("ascii"),
            "eof": offset + len(data) == item.byte_count,
        }

    def delete(self, task_id, file_id):
        item = self.get(task_id, file_id)
        if item.status == "deleted":
            return
        with self.store._db:
            self.store._db.execute(
                "UPDATE workspace_files SET status='deleted',data=? WHERE file_id=?",
                (b"", file_id),
            )
            self.store._append(task_id, "workspace.file_deleted", {"file_id": file_id})

    def stage_extension(self, task_id, manifest_id):
        """Validate untrusted declarations and pin bytes; grant/install nothing."""
        manifest = self.get(task_id, manifest_id)
        if manifest.status != "sealed" or manifest.expected_bytes > 16384:
            raise ValueError("Seal a manifest of at most 16 KiB before staging")
        try:
            data = json.loads(
                self.bytes(task_id, manifest_id).decode("utf-8"),
                object_pairs_hook=unique_object,
            )
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("Manifest must be UTF-8 JSON") from None
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "name", "entrypoint", "files", "capabilities"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["name"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", data["name"])
            or not isinstance(data["files"], list)
            or not 1 <= len(data["files"]) <= 20
            or not all(isinstance(f, str) for f in data["files"])
            or len(set(data["files"])) != len(data["files"])
            or data["entrypoint"] not in data["files"]
        ):
            raise ValueError("Invalid version-1 extension manifest")
        capabilities = data["capabilities"]
        if not isinstance(capabilities, list) or len(capabilities) > 20:
            raise ValueError("Declare at most 20 requested capabilities")
        connector_permissions(capabilities)
        files = {item.name: item for item in self.list(task_id)}
        selected = []
        for name in sorted(data["files"]):
            workspace_name(name)
            item = files.get(name)
            if item is None or item.status != "sealed" or item.file_id == manifest_id:
                raise ValueError(
                    "Every package source must be a distinct owned sealed file"
                )
            payload = self.bytes(task_id, item.file_id)
            if hashlib.sha256(payload).hexdigest() != item.sha256:
                raise ValueError("Package source integrity check failed")
            selected.append(
                {"file_id": item.file_id, "name": item.name, "sha256": item.sha256}
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {"manifest": manifest.sha256, "files": selected}, sort_keys=True
            ).encode()
        ).hexdigest()
        previous = self.store._db.execute(
            "SELECT extension_id,package_sha256 FROM workspace_extensions WHERE task_id=? AND manifest_id=?",
            (task_id, manifest_id),
        ).fetchone()
        if previous:
            if previous[1] != fingerprint:
                raise ValueError("Staged package identity changed")
            extension_id = previous[0]
        else:
            extension_id = uuid4().hex
            with self.store._db:
                self.store._db.execute(
                    "INSERT INTO workspace_extensions VALUES (?,?,?,?,?)",
                    (
                        extension_id,
                        task_id,
                        manifest_id,
                        fingerprint,
                        json.dumps(selected),
                    ),
                )
                self.store._append(
                    task_id,
                    "workspace.extension_staged",
                    {
                        "extension_id": extension_id,
                        "package_sha256": fingerprint,
                        "file_count": len(selected),
                    },
                )
        return {
            "extension_id": extension_id,
            "name": data["name"],
            "package_sha256": fingerprint,
            "status": "staged",
            "executable": False,
            "requested_capabilities": capabilities,
        }

    def extensions(self, task_id):
        """Trusted UI metadata only; staging never activates a package."""
        self.store.get(task_id)
        result = []
        for row in self.store._db.execute(
            "SELECT * FROM workspace_extensions WHERE task_id=? ORDER BY extension_id",
            (task_id,),
        ):
            files = json.loads(row["files"])
            manifest = self.get(task_id, row["manifest_id"])
            available = manifest.status == "sealed" and all(
                self.get(task_id, f["file_id"]).status == "sealed" for f in files
            )
            item = {
                "extension_id": row["extension_id"],
                "package_sha256": row["package_sha256"],
                "status": "staged" if available else "unavailable",
                "executable": False,
            }
            if available:
                data = json.loads(self.bytes(task_id, manifest.file_id))
                item["name"] = data["name"]
                item["requested_capabilities"] = data["capabilities"]
            result.append(item)
        return result

    def package(self, task_id, extension_id):
        """Revalidate the manifest and every pinned source before execution."""
        self.store.get(task_id)
        row = self.store._db.execute(
            "SELECT * FROM workspace_extensions WHERE task_id=? AND extension_id=?",
            (task_id, extension_id),
        ).fetchone()
        if row is None:
            raise KeyError("Package does not belong to this task")
        checked = self.stage_extension(task_id, row["manifest_id"])
        if checked["package_sha256"] != row["package_sha256"]:
            raise ValueError("Package identity changed")
        manifest = json.loads(self.bytes(task_id, row["manifest_id"]))
        selected = json.loads(row["files"])
        return {
            **checked,
            "manifest": manifest,
            "file_ids": [row["manifest_id"], *[f["file_id"] for f in selected]],
            "files": {f["name"]: self.bytes(task_id, f["file_id"]) for f in selected},
        }

    def package_file_ids(self, task_id, extension_id):
        self.store.get(task_id)
        row = self.store._db.execute(
            "SELECT manifest_id,files FROM workspace_extensions WHERE task_id=? AND extension_id=?",
            (task_id, extension_id),
        ).fetchone()
        if row is None:
            raise KeyError("Package does not belong to this task")
        return [row[0], *[item["file_id"] for item in json.loads(row[1])]]
