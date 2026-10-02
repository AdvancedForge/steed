"""Bounded, network-isolated stdlib Python worker with a typed pipe broker."""

import json
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from services.agent_tools import REQUEST_LIMIT
from services.task_workspace import unique_object, workspace_name

MAX_WORKERS = 2
WALL_SECONDS = 20
MAX_RPC = 512
MAX_STDERR = 65536


@dataclass(frozen=True)
class WorkerOutcome:
    state: str
    reason: str = ""
    rpc_count: int = 0


class WorkerStopped(RuntimeError):
    pass


def worker_command(package, sdk, entrypoint):
    """No environment, home, credentials, session bus, host temp or socket mounts."""
    workspace_name(entrypoint)
    if not entrypoint.endswith(".py"):
        raise ValueError("The local worker currently supports Python entrypoints")
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    bwrap = Path("/usr/bin/bwrap")
    if (
        not bwrap.is_file()
        or bwrap.stat().st_uid != 0
        or bwrap.stat().st_mode & 0o022
        or not str(executable).startswith("/usr/")
        or executable.stat().st_uid != 0
        or executable.stat().st_mode & 0o022
    ):
        raise RuntimeError("local_worker_unavailable")
    args = [
        str(bwrap),
        "--unshare-all",
        "--unshare-user",
        "--unshare-net",
        "--unshare-pid",
        "--disable-userns",
        "--assert-userns-disabled",
        "--cap-drop",
        "ALL",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv",
        "HOME",
        "/work",
        "--setenv",
        "PATH",
        "/usr/bin",
        "--ro-bind",
        "/usr",
        "/usr",
    ]
    for name in ("lib", "lib64", "bin"):
        path = Path("/") / name
        if path.is_symlink():
            args += ["--symlink", os.readlink(path), str(path)]
        elif path.exists():
            args += ["--ro-bind", str(path), str(path)]
    cache = Path("/etc/ld.so.cache")
    if cache.exists():
        args += ["--ro-bind", str(cache), str(cache)]
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--size",
        str(32 * 1024 * 1024),
        "--tmpfs",
        "/work",
        "--symlink",
        "work",
        "/tmp",
        "--ro-bind",
        str(package),
        "/package",
        "--ro-bind",
        str(sdk),
        "/sdk",
        "--chdir",
        "/work",
        "--",
        str(executable),
        "-I",
        "-S",
        "-u",
        "/sdk/bootstrap.py",
        entrypoint,
    ]
    return args


def run_worker(files, entrypoint, cancelled, request, *, timeout=WALL_SECONDS):
    """No Qt/SQLite calls here; request bridges to the owner-thread authority."""
    deadline, count, ready = time.monotonic() + timeout, 0, False
    with tempfile.TemporaryDirectory(prefix="steed-worker-") as directory:
        root = Path(directory)
        package, sdk = root / "package", root / "sdk"
        package.mkdir(mode=0o700)
        sdk.mkdir(mode=0o700)
        for name, data in files.items():
            target = package / workspace_name(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o400)
        source = Path(__file__).parent
        shutil.copyfile(source / "worker_sdk.py", sdk / "steed_worker.py")
        shutil.copyfile(source / "worker_bootstrap.py", sdk / "bootstrap.py")
        process = subprocess.Popen(
            worker_command(package, sdk, entrypoint),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            env={},
        )
        reason, buffer, stderr_bytes = "", bytearray(), 0
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    if cancelled.is_set():
                        raise WorkerStopped("cancelled")
                    if time.monotonic() >= deadline:
                        raise WorkerStopped("worker_timeout")
                    for key, _ in selector.select(0.05):
                        data = os.read(key.fd, 8192)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        if key.data == "stderr":
                            stderr_bytes += len(data)
                            if stderr_bytes > MAX_STDERR:
                                raise WorkerStopped("worker_output_limit")
                            continue
                        buffer.extend(data)
                        while b"\n" in buffer:
                            line, _, tail = buffer.partition(b"\n")
                            buffer = bytearray(tail)
                            if len(line) > REQUEST_LIMIT:
                                raise WorkerStopped("worker_protocol_limit")
                            try:
                                message = json.loads(
                                    line, object_pairs_hook=unique_object
                                )
                            except (ValueError, UnicodeError, RecursionError):
                                raise WorkerStopped("worker_protocol_invalid") from None
                            if not ready:
                                if message != {"kind": "ready", "protocol": 1}:
                                    raise WorkerStopped("sandbox_setup_failed")
                                ready = True
                                continue
                            if (
                                not isinstance(message, dict)
                                or set(message) != {"id", "command", "arguments"}
                                or type(message["id"]) is not int
                                or not 1 <= message["id"] <= MAX_RPC
                            ):
                                raise WorkerStopped("worker_protocol_invalid")
                            count += 1
                            if message["id"] != count:
                                raise WorkerStopped("worker_protocol_invalid")
                            if count > MAX_RPC:
                                raise WorkerStopped("worker_rpc_limit")
                            response = request(message)
                            if cancelled.is_set():
                                raise WorkerStopped("cancelled")
                            raw = (
                                json.dumps(
                                    {"id": message["id"], **response}, allow_nan=False
                                ).encode()
                                + b"\n"
                            )
                            if len(raw) > 1024 * 1024:
                                raise WorkerStopped("worker_response_limit")
                            # Responses fit the existing bounded broker transport. Blocking
                            # writes to a malicious non-reader must also honor the deadline.
                            os.set_blocking(process.stdin.fileno(), False)
                            position = 0
                            while position < len(raw):
                                if cancelled.is_set() or time.monotonic() >= deadline:
                                    raise WorkerStopped("worker_timeout")
                                try:
                                    position += os.write(
                                        process.stdin.fileno(), raw[position:]
                                    )
                                except BlockingIOError:
                                    time.sleep(0.005)
                        if len(buffer) > REQUEST_LIMIT:
                            raise WorkerStopped("worker_protocol_limit")
                if buffer or not ready:
                    reason = (
                        "worker_protocol_invalid" if ready else "sandbox_setup_failed"
                    )
                code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
                return WorkerOutcome(
                    "failed" if code or reason else "succeeded",
                    reason or ("worker_script_failed" if code else ""),
                    count,
                )
        except WorkerStopped as error:
            return WorkerOutcome(
                "cancelled" if cancelled.is_set() else "failed", str(error), count
            )
        except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
            return WorkerOutcome("failed", "worker_transport_failed", count)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


class LocalWorkerJob(QObject):
    requested = pyqtSignal(object, object)

    def __init__(self, files, entrypoint, callback, finished, parent):
        super().__init__(parent)
        self.cancelled = threading.Event()
        self.result = Future()
        self.requested.connect(callback, Qt.ConnectionType.QueuedConnection)
        self.finished = finished
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.files, self.entrypoint = files, entrypoint
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.deadline = time.monotonic() + WALL_SECONDS
        self.timer.start()
        self.thread.start()

    def rpc(self, message):
        if self.cancelled.is_set():
            raise WorkerStopped("cancelled")
        future = Future()
        self.requested.emit(message, future)
        while not self.cancelled.is_set() and time.monotonic() < self.deadline:
            try:
                return future.result(timeout=0.05)
            except TimeoutError:
                pass
        raise WorkerStopped(
            "cancelled" if self.cancelled.is_set() else "worker_timeout"
        )

    def run(self):
        try:
            result = run_worker(self.files, self.entrypoint, self.cancelled, self.rpc)
        except Exception:
            result = WorkerOutcome("failed", "local_worker_unavailable")
        self.files = {}
        self.result.set_result(result)

    def poll(self):
        if self.result.done():
            self.timer.stop()
            self.finished(self.result.result())

    def close(self):
        self.cancelled.set()
        self.timer.stop()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
