"""Linux-only diagnostic Chromium process with anonymous DevTools pipes.

This helper owns a temporary profile. It never attaches to an existing browser
or exposes an HTTP debugging listener. It is not an application backend.
"""

import fcntl
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def launch():
    # Remap in a fresh, single-threaded Python process rather than a preexec_fn
    # in the threaded fixture process. Duplicate above 4 to avoid collisions.
    source_fds = (int(sys.argv[2]), int(sys.argv[3]))
    incoming = fcntl.fcntl(source_fds[0], fcntl.F_DUPFD_CLOEXEC, 5)
    outgoing = fcntl.fcntl(source_fds[1], fcntl.F_DUPFD_CLOEXEC, 5)
    os.dup2(incoming, 3, inheritable=True)
    os.dup2(outgoing, 4, inheritable=True)
    os.close(incoming)
    os.close(outgoing)
    for fd in source_fds:
        if fd not in (3, 4):
            os.close(fd)
    os.execv(sys.argv[4], sys.argv[4:])


class ChromiumPipe:
    def __init__(self, binary="chromium"):
        executable = shutil.which(binary)
        if executable is None:
            raise RuntimeError("Install Chromium or provide --chromium PATH")
        self.directory = tempfile.TemporaryDirectory(prefix="steed-native-engine-")
        self.log = open(Path(self.directory.name) / "chromium.log", "wb")
        incoming, self.write_fd = os.pipe()
        self.read_fd, outgoing = os.pipe()
        self.buffer = b""
        self.sequence = 0
        self.events = []
        self.process = None
        self.closed = False
        args = [
            executable,
            "--headless",
            "--remote-debugging-pipe",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--metrics-recording-only",
            "--disable-features=MediaRouter,OptimizationHints",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
            f"--user-data-dir={self.directory.name}/profile",
            "about:blank",
        ]
        try:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--launch",
                    str(incoming),
                    str(outgoing),
                    *args,
                ],
                stdin=subprocess.DEVNULL,
                stdout=self.log,
                stderr=self.log,
                pass_fds=(incoming, outgoing),
                start_new_session=True,
            )
        except Exception:
            self.close()
            raise
        finally:
            os.close(incoming)
            os.close(outgoing)

    def _receive(self, deadline):
        while b"\0" not in self.buffer:
            remaining = deadline - time.monotonic()
            if (
                remaining <= 0
                or not select.select([self.read_fd], [], [], remaining)[0]
            ):
                raise TimeoutError("Chromium protocol response timed out")
            chunk = os.read(self.read_fd, 65536)
            if not chunk:
                raise RuntimeError("Chromium protocol pipe closed")
            self.buffer += chunk
        value, self.buffer = self.buffer.split(b"\0", 1)
        return json.loads(value)

    def command(self, method, params=None, *, session=None, timeout=10):
        self.sequence += 1
        request = {"id": self.sequence, "method": method, "params": params or {}}
        if session is not None:
            request["sessionId"] = session
        payload = json.dumps(request).encode() + b"\0"
        while payload:
            payload = payload[os.write(self.write_fd, payload) :]
        deadline = time.monotonic() + timeout
        while True:
            response = self._receive(deadline)
            if response.get("id") == self.sequence:
                if "error" in response:
                    raise RuntimeError(f"Chromium command failed: {response['error']}")
                return response.get("result", {})
            if "method" in response:
                self.events.append(response)
                self.events = self.events[-512:]

    def page(self):
        target = self.command("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self.command(
            "Target.attachToTarget", {"targetId": target, "flatten": True}
        )["sessionId"]
        return target, session

    def evaluate(self, session, expression):
        result = self.command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session=session,
        )
        if "exceptionDetails" in result:
            raise RuntimeError("Fixture JavaScript raised an exception")
        return result["result"].get("value")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.command("Browser.close", timeout=1)
                self.process.wait(timeout=5)
            except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
                if self.process.poll() is None:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        for fd in (self.read_fd, self.write_fd):
            os.close(fd)
        self.log.close()
        self.directory.cleanup()


if __name__ == "__main__" and sys.argv[1:2] == ["--launch"]:
    launch()
