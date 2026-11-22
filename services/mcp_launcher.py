"""A launcher for outside agents that keeps working when Steed moves.

Agent Setup gives MCP clients (Claude Desktop, Cursor, Codex…) a command to
start Steed's adapter. Pointing them straight at this checkout's Python and
agent_mcp.py would break the day the folder moves or the environment is
rebuilt. Instead they run ``steed-mcp`` in Steed's config folder, a two-line
script Steed rewrites at every start with wherever it lives now.
"""

import os
import shlex
import sys
from pathlib import Path

NAME = "steed-mcp"


def text(executable, repository):
    adapter = Path(repository) / "agent_mcp.py"
    return (
        "#!/bin/sh\n"
        "# Written by Steed at each start: where Steed's adapter lives now.\n"
        f'exec {shlex.quote(str(executable))} {shlex.quote(str(adapter))} "$@"\n'
    )


def write(config_dir, *, executable=None, repository=None):
    """The launcher's path, (re)written if it changed; never reads secrets."""
    repository = repository or Path(__file__).resolve().parents[1]
    path = Path(config_dir) / NAME
    wanted = text(executable or sys.executable, repository).encode()
    try:
        current = path.read_bytes() if path.is_file() else None
    except OSError:
        current = None
    if current != wanted:
        temporary = path.with_name(NAME + ".new")
        temporary.write_bytes(wanted)
        os.chmod(temporary, 0o700)
        os.replace(temporary, path)
    return path
