"""Cloud agents and chatbots with connectors: a private, expiring pairing link.

claude.ai, ChatGPT and cloud agents cannot reach Steed on this computer. A
pairing link lets them: Steed starts its MCP server's bridge end on a local
port (agent_mcp.py --transport bridge) and a tunnel that forwards a public
https address to it (Tailscale Funnel, a Cloudflare quick tunnel, or an
address you set up yourself). Nothing listens on your network; the tunnel
connects out. The link holds a long random secret in its path, because
connector settings in chat apps cannot add headers.

Whatever comes through the link is a cloud agent: Steed asks you about every
request it makes, whatever your access settings, saved approvals or
auto-approval say. The link ends after EXPIRY_HOURS, when you stop it, when
agent access ends, or when Steed closes.
"""

import json
import os
import re
import shutil
import signal
import stat
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

EXPIRY_HOURS = 8
START_SECONDS = 45
METHODS = {
    "tailscale": "Tailscale Funnel",
    "cloudflare": "Cloudflare quick tunnel",
    "own": "An address I set up myself",
}
PUBLIC_URL = {
    "tailscale": re.compile(r"https://[A-Za-z0-9.-]+\.ts\.net\b"),
    "cloudflare": re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com\b"),
}


def available_methods():
    """[(method, label)] that can work on this computer."""
    found = []
    if shutil.which("tailscale"):
        found.append(("tailscale", METHODS["tailscale"]))
    if shutil.which("cloudflared"):
        found.append(("cloudflare", METHODS["cloudflare"]))
    found.append(("own", METHODS["own"]))
    return found


def clean_base(url):
    """Your own public address: https, a host and nothing secret in it."""
    parts = urlsplit(url.strip()) if isinstance(url, str) else None
    if (
        parts is None
        or parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or len(url) > 512
    ):
        raise ValueError("Give the public https address that forwards to Steed")
    return url.strip().rstrip("/")


def read_endpoint(path):
    """The bridge's private endpoint file: owned by you, 0600, no symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError("Unsafe bridge endpoint file")
        value = json.loads(handle.read(4096))
    port, path = value.get("port"), value.get("path")
    if (
        not isinstance(port, int)
        or not 0 < port < 65536
        or not isinstance(path, str)
        or not re.fullmatch(r"/[A-Za-z0-9_-]{40,64}/mcp", path)
    ):
        raise ValueError("Invalid bridge endpoint")
    return port, path


def _process(parent):
    process = QProcess(parent)
    parameters = QProcess.UnixProcessParameters()
    parameters.flags = QProcess.UnixProcessFlag(
        QProcess.UnixProcessFlag.CreateNewSession.value
        | QProcess.UnixProcessFlag.ResetSignalHandlers.value
    )
    process.setUnixProcessParameters(parameters)
    return process


def _end(process):
    if process is None or process.state() == QProcess.ProcessState.NotRunning:
        return
    pid = process.processId()
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    if not process.waitForFinished(1500):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.kill()
        process.waitForFinished(500)


class AgentBridge(QObject):
    changed = pyqtSignal()

    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.state = "off"  # off, starting, on, failed
        self.method = None
        self.link = ""
        self.port = None
        self.message = ""
        self.expires = None
        self.server = self.tunnel = None
        self.folder = None
        self.base = None
        self.output = ""
        self.expiry = QTimer(self, singleShot=True)
        self.expiry.timeout.connect(lambda: self.stop("The pairing link expired."))
        self.startup = QTimer(self, singleShot=True)
        self.startup.timeout.connect(
            lambda: self._fail("The tunnel did not give an address in time.")
        )

    # ---------- Starting ----------

    def start(self, method, base=None):
        if self.state in ("starting", "on"):
            raise ValueError("A pairing link is already open. Stop it first.")
        if method not in dict(available_methods()):
            raise ValueError("That way of reaching Steed is not available here.")
        self.base = clean_base(base) if method == "own" else None
        self.registry.set_agent_access(True)
        gateway = self.registry.agent_gateway
        self.folder = tempfile.TemporaryDirectory(prefix="steed-bridge-")
        endpoint = Path(self.folder.name) / "bridge.json"
        self.method, self.state, self.link, self.message = method, "starting", "", ""
        self.output = ""
        self.server = _process(self)
        self.server.setProgram(sys.executable)
        self.server.setArguments(
            [
                str(Path(__file__).resolve().parents[1] / "agent_mcp.py"),
                "--transport",
                "bridge",
                "--discovery",
                str(gateway.discovery),
                "--http-discovery",
                str(endpoint),
            ]
        )
        self.server.readyReadStandardError.connect(lambda: self._server_said(endpoint))
        self.server.finished.connect(
            lambda *_: self._fail("Steed's side of the link stopped.")
        )
        self.startup.start(START_SECONDS * 1000)
        self.server.start()
        self.changed.emit()

    def _server_said(self, endpoint):
        text = bytes(self.server.readAllStandardError()).decode("utf-8", "replace")
        if "Steed bridge ready" not in text or self.state != "starting":
            return
        try:
            self.port, path = read_endpoint(endpoint)
        except (OSError, ValueError):
            self._fail("Steed's side of the link could not start.")
            return
        self.path = path
        if self.method == "own":
            self._ready(self.base)
            return
        self.tunnel = _process(self)
        self.tunnel.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if self.method == "tailscale":
            self.tunnel.setProgram("tailscale")
            self.tunnel.setArguments(["funnel", str(self.port)])
        else:
            self.tunnel.setProgram("cloudflared")
            self.tunnel.setArguments(
                [
                    "tunnel",
                    "--no-autoupdate",
                    "--url",
                    f"http://127.0.0.1:{self.port}",
                ]
            )
        self.tunnel.readyReadStandardOutput.connect(self._tunnel_said)
        self.tunnel.finished.connect(lambda *_: self._tunnel_ended())
        self.tunnel.start()

    def _tunnel_said(self):
        text = bytes(self.tunnel.readAllStandardOutput()).decode("utf-8", "replace")
        self.output = (self.output + text)[-4000:]
        found = PUBLIC_URL[self.method].search(self.output)
        if found and self.state == "starting":
            self._ready(found.group(0))

    def _tunnel_ended(self):
        if self.state == "starting":
            hint = ""
            if self.method == "tailscale" and "funnel" in self.output.lower():
                hint = " Turn on Funnel for this computer in Tailscale first."
            self._fail("The tunnel stopped before it gave an address." + hint)
        elif self.state == "on":
            self._fail("The tunnel stopped; the link no longer works.")

    def _ready(self, base):
        self.startup.stop()
        self.link = base.rstrip("/") + self.path
        self.state = "on"
        self.expires = time.time() + EXPIRY_HOURS * 3600
        self.expiry.start(EXPIRY_HOURS * 3600 * 1000)
        self.changed.emit()

    # ---------- Ending ----------

    def _fail(self, message):
        if self.state in ("starting", "on"):
            self._shutdown()
            self.state, self.message = "failed", message
            self.changed.emit()

    def stop(self, message=""):
        if self.state in ("starting", "on"):
            self._shutdown()
        self.state, self.message = "off", message
        self.changed.emit()

    def _shutdown(self):
        self.startup.stop()
        self.expiry.stop()
        for process in (self.tunnel, self.server):
            if process is not None:
                try:
                    process.finished.disconnect()
                except TypeError:
                    pass
                _end(process)
        self.tunnel = self.server = None
        self.link, self.expires, self.port = "", None, None
        if self.folder is not None:
            self.folder.cleanup()
            self.folder = None
