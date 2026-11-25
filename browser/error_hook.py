"""One error never takes Steed down.

PyQt6 aborts the whole process when Python code called from Qt (an event
filter, a paint, a slot) raises, unless sys.excepthook has been replaced.
Steed replaces it: the error is printed and kept in <config>/errors.log, and
Steed carries on. The log holds tracebacks (file names, lines and the error's
message), never page content; it is capped and stays on this computer.
"""

import sys
import time
import traceback
from pathlib import Path

MAX_BYTES = 256 * 1024


def log_path(config_dir) -> Path:
    return Path(config_dir) / "errors.log"


def record(config_dir, kind, value, trace):
    text = "".join(traceback.format_exception(kind, value, trace))
    sys.stderr.write(text)
    try:
        path = log_path(config_dir)
        old = path.read_bytes() if path.exists() else b""
        entry = f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}".encode(
            "utf-8", "replace"
        )
        # Keep the newest errors, within the cap.
        kept = (old + entry)[-MAX_BYTES:]
        path.write_bytes(kept)
        path.chmod(0o600)
    except OSError:
        pass


def install(config_dir):
    """Report errors from Qt callbacks instead of aborting."""

    def hook(kind, value, trace):
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, trace)
            return
        record(config_dir, kind, value, trace)

    sys.excepthook = hook
    return hook
