"""Mounted as steed_worker inside the isolated runtime; contains no credentials."""

import base64
import hashlib
import json
import sys

_sequence = 0


def call(command, **arguments):
    """Request one typed task operation from the browser's permission broker."""
    global _sequence
    _sequence += 1
    request = {"id": _sequence, "command": command, "arguments": arguments}
    sys.stdout.write(json.dumps(request, allow_nan=False) + "\n")
    sys.stdout.flush()
    response = json.loads(sys.stdin.readline())
    if response.get("id") != _sequence:
        raise RuntimeError("worker_response_mismatch")
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


def store_file(name, data, media_type="application/octet-stream"):
    """Store arbitrary bytes in the approved task workspace, then seal them."""
    metadata = call(
        "workspace.begin",
        name=name,
        media_type=media_type,
        byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    if metadata["status"] == "sealed":
        return metadata
    for offset in range(0, len(data), 32768):
        call(
            "workspace.write",
            file_id=metadata["file_id"],
            offset=offset,
            data_base64=base64.b64encode(data[offset : offset + 32768]).decode("ascii"),
        )
    return call("workspace.seal", file_id=metadata["file_id"])


def read_file(file_id):
    """Read an owned file; never opens a host filesystem path."""
    result, offset = bytearray(), 0
    while True:
        chunk = call("workspace.read", file_id=file_id, offset=offset)
        data = base64.b64decode(chunk["data_base64"], validate=True)
        result.extend(data)
        if len(result) > 8 * 1024 * 1024:
            raise RuntimeError("worker_file_too_large")
        if chunk["eof"]:
            return bytes(result)
        if not data:
            raise RuntimeError("worker_file_read_stalled")
        offset += len(data)
