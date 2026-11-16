"""Real namespace/seccomp checks against deliberately hostile generated code."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from services.local_worker import run_worker, worker_command

# The real home folder, read before tests patch Path.home.
REAL_HOME = Path(os.path.expanduser("~"))


class LocalWorkerTests(unittest.TestCase):
    def execute(self, source, *, timeout=3, cancel=None):
        self.messages = []

        def request(message):
            self.messages.append(message)
            return {"result": True}

        return run_worker(
            {"main.py": source.encode()},
            "main.py",
            cancel or threading.Event(),
            request,
            timeout=timeout,
        )

    def test_network_processes_host_data_and_environment_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            canary = Path(directory) / "private-profile-secret"
            canary.write_text("SYNTHETIC_SECRET")
            with patch.dict(os.environ, {"STEED_SECRET": "SYNTHETIC_SECRET"}):
                result = self.execute(
                    f"""
import os, socket, subprocess
from pathlib import Path
from steed_worker import call
assert 'STEED_SECRET' not in os.environ
assert not Path({str(canary)!r}).exists()
assert not Path({str(REAL_HOME / ".custom_browser")!r}).exists()
assert not Path('/run/user').exists()
assert not Path('/proc/1/root/home').exists()
for attempt in [
    lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM),
    lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM),
    lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM),
    os.fork,
    lambda: subprocess.run(['/usr/bin/true']),
    lambda: Path('/package/main.py').write_text('changed'),
]:
    try:
        attempt()
    except OSError:
        pass
    else:
        raise AssertionError('forbidden operation worked')
Path('/work/ok').write_bytes(b'local scratch')
assert Path('/tmp/ok').read_bytes() == b'local scratch'
call('workspace.list')
"""
                )
        self.assertEqual(result.state, "succeeded", result)
        self.assertEqual(len(self.messages), 1)

    def test_irreversible_resource_and_syscall_limits(self):
        result = self.execute(
            """
import resource, ctypes, errno
from pathlib import Path
from steed_worker import call
limits = [
    (resource.RLIMIT_AS, 512 * 1024 * 1024),
    (resource.RLIMIT_CPU, 10), (resource.RLIMIT_FSIZE, 8 * 1024 * 1024),
    (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_NPROC, 0),
    (resource.RLIMIT_CORE, 0),
]
for kind, maximum in limits:
    assert resource.getrlimit(kind) == (maximum, maximum)
    try:
        resource.setrlimit(kind, (maximum + 1, maximum + 1))
    except (ValueError, OSError):
        pass
    else:
        raise AssertionError('hard limit raised')
status = Path('/proc/self/status').read_text()
assert 'Seccomp:\t2' in status and 'NoNewPrivs:\t1' in status
assert 'CapEff:\t0000000000000000' in status
libc = ctypes.CDLL(None, use_errno=True)
assert libc.unshare(0x10000000) == -1 and ctypes.get_errno() == errno.EPERM
call('workspace.list')
"""
        )
        self.assertEqual(result.state, "succeeded", result)

    def test_memory_exhaustion_is_bounded(self):
        result = self.execute(
            """
from steed_worker import call
try:
    large = bytearray(600 * 1024 * 1024)
except MemoryError:
    call('workspace.list')
else:
    raise AssertionError('memory limit bypassed')
"""
        )
        self.assertEqual(result.state, "succeeded", result)

    def test_cpu_budget_terminates_busy_loop(self):
        result = self.execute(
            "import resource; resource.setrlimit(resource.RLIMIT_CPU, (1, 1))\nwhile True: pass",
            # A second of CPU can take many wall seconds on a busy machine; the
            # wall limit only has to be longer so the CPU budget acts first.
            timeout=30,
        )
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.reason, "worker_script_failed")

    def test_scratch_and_package_directories_are_removed_on_completion(self):
        created = []
        original = tempfile.TemporaryDirectory

        def directory(*args, **kwargs):
            result = original(*args, **kwargs)
            created.append(result.name)
            return result

        with patch("services.local_worker.tempfile.TemporaryDirectory", directory):
            self.assertEqual(self.execute("pass").state, "succeeded")
        self.assertEqual(len(created), 1)
        self.assertFalse(Path(created[0]).exists())

    def test_timeout_and_cancellation_kill_the_process(self):
        result = self.execute("import time; time.sleep(10)", timeout=0.4)
        self.assertEqual((result.state, result.reason), ("failed", "worker_timeout"))
        cancel = threading.Event()
        cancel.set()
        result = self.execute("import time; time.sleep(10)", cancel=cancel)
        self.assertEqual(result.state, "cancelled")

    def test_malformed_duplicate_oversized_and_incomplete_protocol(self):
        for source in (
            "print('not a broker message')",
            "print('x' * 70000)",
            "import sys; sys.stdout.write('unfinished')",
            'print(\'{"id":1,"id":2,"command":"workspace.list","arguments":{}}\')',
            'print(\'{"id":2,"command":"workspace.list","arguments":{}}\')',
        ):
            with self.subTest(source=source):
                result = self.execute(source)
                self.assertEqual(result.state, "failed", result)
                self.assertEqual(self.messages, [])

    def test_stderr_is_bounded_and_never_returns_user_content(self):
        result = self.execute("import sys; sys.stderr.write('PRIVATE' * 12000)")
        self.assertEqual(result.reason, "worker_output_limit")
        self.assertNotIn("PRIVATE", repr(result))

    def test_setup_failure_never_falls_back_to_host_execution(self):
        with patch("services.local_worker.worker_command", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.execute("raise AssertionError('must never execute')")
        with patch("services.local_worker.Path.is_file", return_value=False):
            with self.assertRaises(RuntimeError):
                worker_command(Path("/package"), Path("/sdk"), "main.py")

    def test_bootstrap_failure_never_imports_generated_source(self):
        from services import worker_bootstrap

        with (
            patch.object(worker_bootstrap, "restrict", side_effect=RuntimeError),
            patch.object(worker_bootstrap.runpy, "run_path") as execute,
            patch.object(worker_bootstrap.sys, "stdout"),
        ):
            self.assertEqual(worker_bootstrap.main(), 70)
            execute.assert_not_called()
